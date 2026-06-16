from typing import Any
import hashlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed

try:
    from scrapegraph_py import JsonFormatConfig, ScrapeGraphAI
except ImportError:
    JsonFormatConfig = None
    ScrapeGraphAI = None

from config import (
    API_KEY,
    AZURE_API_KEY,
    AZURE_DEPLOYMENT,
    AZURE_ENDPOINT,
    EXTRACT_CHUNK_OVERLAP,
    EXTRACT_CHUNK_SIZE,
    EXTRACT_CACHE_DIR,
    EXTRACT_MAX_WORKERS,
    EXTRACT_TIMEOUT,
    EXTRACT_TOOL,
)
from models import ColumnSpec, Config, ExtractedCell, PageDoc, SourceQuote


def _build_prompt(
    columns: list[ColumnSpec],
    entities: list[str],
    page_text: str | None = None,
) -> str:
    entity_fields = "".join(f'- "{entity}"\n' for entity in entities)
    question_fields = ""
    for col in columns:
        if col.instruction:
            question_fields += f'- "{col.name}": {col.instruction}\n'
        else:
            question_fields += f'- "{col.name}"\n'

    base = f"""
Extract answers to these questions about these specific entities from this page.

Specific entities:
{entity_fields}

Return a JSON object with exactly these top-level keys, one per specific entity:
{entity_fields}

For each entity key, return an object with exactly these question keys:
{question_fields}

For each question key, return an object with this structure:
{{
  "value": the extracted answer, or null if not found,
  "quote": a short exact quote from the page supporting the answer, or null if not found
}}

Rules:
- Extract only answers about the specific entities listed above.
- Use exactly the requested entity names and question names.
- Do not invent information.
- Only use information present in the content provided.
- If the answer is not found, use null for both value and quote.
- If multiple answers exist, return a JSON array of objects, one per answer.
- Each quote must be copied exactly from the page content where possible.
- Return only JSON, no other text.
"""
    if page_text:
        return base + "\nPage content:\n'''\n" + page_text + "\n'''\n"
    return base


def _extract_with_sgai(
    page: PageDoc,
    columns: list[ColumnSpec],
    entities: list[str],
) -> tuple[dict[str, Any], dict]:
    """
    Extract fields using ScrapeGraphAI.

    Returns (data_dict, timing_info) where timing_info has keys:
      extraction_time_ms, timed_out, retry_count
    """
    page_text = page.text if getattr(page, "text", None) else None
    prompt = _build_prompt(columns, entities, page_text)

    if ScrapeGraphAI is None or JsonFormatConfig is None:
        raise RuntimeError("scrapegraph-py is required when EXTRACT_TOOL='sgai'")
    if not API_KEY:
        raise RuntimeError("Missing SGAI_API_KEY in .env")

    print(f"      -> SGAI extracting: {page.url}")
    t0 = time.time()

    max_attempts = 2
    retry_wait_s = 5
    timed_out = False
    attempts_made = 0

    def make_timing():
        return {
            "extraction_time_ms": int((time.time() - t0) * 1000),
            "timed_out": timed_out,
            "retry_count": max(attempts_made - 1, 0),
        }

    result = None
    try:
        for attempt in range(1, max_attempts + 1):
            attempts_made = attempt
            sgai = ScrapeGraphAI(api_key=API_KEY)

            def do_scrape():
                content_candidates = [
                    ("content", page.text),
                    ("html", page.html),
                    ("text", page.text),
                ]
                for name, content in content_candidates:
                    if not content:
                        continue
                    try:
                        return sgai.scrape(**{name: content}, formats=[JsonFormatConfig(prompt=prompt)])
                    except TypeError:
                        continue
                return sgai.scrape(page.url, formats=[JsonFormatConfig(prompt=prompt)])

            with ThreadPoolExecutor(max_workers=1) as ex:
                future = ex.submit(do_scrape)
                try:
                    result = future.result(timeout=EXTRACT_TIMEOUT)
                except FuturesTimeoutError:
                    timed_out = True
                    duration = time.time() - t0
                    try:
                        sgai.close()
                    except Exception:
                        pass
                    if attempt < max_attempts:
                        print(
                            f"      ! SGAI timed out after {EXTRACT_TIMEOUT}s "
                            f"(attempt {attempt}/{max_attempts}); retrying in {retry_wait_s}s..."
                        )
                        time.sleep(retry_wait_s)
                        continue
                    print(f"      ! SGAI timed out on final attempt after {duration:.2f}s")
                    return {}, make_timing()
                except Exception as e:
                    duration = time.time() - t0
                    print(f"      X Extraction error during call after {duration:.2f}s: {e}")
                    try:
                        sgai.close()
                    except Exception:
                        pass
                    return {}, make_timing()

            duration = time.time() - t0
            print(f"      -> SGAI call completed in {duration:.2f}s (attempt {attempt}/{max_attempts})")
            try:
                sgai.close()
            except Exception:
                pass
            break

        if result is None:
            print("      ! Result is None")
            return {}, make_timing()

        data_obj = getattr(result, "data", None)
        if data_obj is None:
            print("      ! result.data is None")
            return {}, make_timing()

        results = getattr(data_obj, "results", None)
        if not results:
            print("      ! result.data.results is empty or falsy")
            return {}, make_timing()

        json_data = {}
        if isinstance(results, dict):
            json_entry = results.get("json") or results.get("json_format") or results.get("json_result")
            if json_entry and isinstance(json_entry, dict):
                json_data = json_entry.get("data", {})

        if not json_data and isinstance(results, dict):
            json_data = results

        if not json_data:
            keys = list(results.keys()) if isinstance(results, dict) else type(results)
            print(f"      ! Extraction returned no usable JSON data (keys: {keys})")
            return {}, make_timing()

        if not isinstance(json_data, dict):
            print(f"      ! Parsed json_data is not a dict: {type(json_data)}")
            return {}, make_timing()

        return json_data, make_timing()

    except Exception as e:
        duration = time.time() - t0
        print(f"      X Extraction error after {duration:.2f}s: {e}")
        return {}, make_timing()


def _extract_with_llmapi(
    page: PageDoc,
    columns: list[ColumnSpec],
    entities: list[str],
) -> tuple[dict[str, Any], dict]:
    """Extract fields using the internal LLMAPI HTTP endpoint."""
    from src.llmapi import LLMAPI

    page_text = page.text if getattr(page, "text", None) else None
    prompt = _build_prompt(columns, entities, page_text)

    print(f"      -> LLMAPI extracting: {page.url}")
    t0 = time.time()
    timed_out = False

    def make_timing():
        return {
            "extraction_time_ms": int((time.time() - t0) * 1000),
            "timed_out": timed_out,
            "retry_count": 0,
        }

    try:
        llm = LLMAPI()
        try:
            raw = llm.call(prompt, timeout=EXTRACT_TIMEOUT)
        except TimeoutError:
            timed_out = True
            duration = time.time() - t0
            print(f"      ! LLMAPI timed out after {duration:.2f}s")
            return {}, make_timing()
        except Exception as e:
            duration = time.time() - t0
            print(f"      X LLMAPI call error after {duration:.2f}s: {e}")
            return {}, make_timing()

        duration = time.time() - t0
        print(f"      -> LLMAPI call completed in {duration:.2f}s")

        text = raw.strip()
        if text.startswith("```"):
            text = text.split("```", 2)[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.rsplit("```", 1)[0].strip()

        json_data = json.loads(text)
        if not isinstance(json_data, dict):
            print(f"      ! LLMAPI response is not a dict: {type(json_data)}")
            return {}, make_timing()

        return json_data, make_timing()

    except Exception as e:
        duration = time.time() - t0
        print(f"      X LLMAPI extraction error after {duration:.2f}s: {e}")
        return {}, make_timing()


def _extract_with_azure(
    page: PageDoc,
    columns: list[ColumnSpec],
    entities: list[str],
) -> tuple[dict[str, Any], dict]:
    """Extract fields using Azure OpenAI via the OpenAI Python SDK."""
    from openai import OpenAI

    if not AZURE_API_KEY:
        raise RuntimeError("Missing AZURE_API_KEY in .env")
    if not AZURE_ENDPOINT:
        raise RuntimeError("Missing AZURE_ENDPOINT in .env")
    if not AZURE_DEPLOYMENT:
        raise RuntimeError("Missing AZURE_DEPLOYMENT in .env")

    page_text = page.text if getattr(page, "text", None) else None
    prompt = _build_prompt(columns, entities, page_text)

    print(f"      -> Azure extracting: {page.url}")
    t0 = time.time()
    timed_out = False

    def make_timing():
        return {
            "extraction_time_ms": int((time.time() - t0) * 1000),
            "timed_out": timed_out,
            "retry_count": 0,
        }

    try:
        client = OpenAI(base_url=AZURE_ENDPOINT, api_key=AZURE_API_KEY)
        try:
            completion = client.chat.completions.create(
                model=AZURE_DEPLOYMENT,
                messages=[{"role": "user", "content": prompt}],
                timeout=EXTRACT_TIMEOUT,
            )
        except Exception as e:
            if "timeout" in str(e).lower() or "timed out" in str(e).lower():
                timed_out = True
                duration = time.time() - t0
                print(f"      ! Azure timed out after {duration:.2f}s")
                return {}, make_timing()
            duration = time.time() - t0
            print(f"      X Azure call error after {duration:.2f}s: {e}")
            return {}, make_timing()

        duration = time.time() - t0
        print(f"      -> Azure call completed in {duration:.2f}s")

        raw = completion.choices[0].message.content or ""
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("```", 2)[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.rsplit("```", 1)[0].strip()

        json_data = json.loads(text)
        if not isinstance(json_data, dict):
            print(f"      ! Azure response is not a dict: {type(json_data)}")
            return {}, make_timing()

        return json_data, make_timing()

    except Exception as e:
        duration = time.time() - t0
        print(f"      X Azure extraction error after {duration:.2f}s: {e}")
        return {}, make_timing()


def _normalise_quote(quote: Any) -> list[str | None]:
    """Return quote(s) as a flat list suitable for SourceQuote construction.

    - str or None  → [quote]          (single item, existing behaviour)
    - list         → one str per item  (LLM sometimes returns multiple quotes)
    - other type   → []               (warn and produce no evidence for this field)
    """
    if quote is None or isinstance(quote, str):
        return [quote]
    if isinstance(quote, list):
        strings = [q for q in quote if isinstance(q, str) and q]
        return strings if strings else [None]
    print(f"      ! Unexpected quote type {type(quote).__name__!r} — dropping quote")
    return []


def _parse_field_value(raw: Any) -> tuple[Any, list[SourceQuote]]:
    evidence = []

    if raw is None:
        return None, evidence

    if isinstance(raw, dict):
        value = raw.get("value")
        quote = raw.get("quote")
        if value not in (None, "", []):
            for q in _normalise_quote(quote):
                evidence.append(SourceQuote(value=value, quote=q))
        return value, evidence

    if isinstance(raw, list):
        values = []
        for item in raw:
            if isinstance(item, dict):
                value = item.get("value")
                quote = item.get("quote")
                if value not in (None, "", []):
                    values.append(value)
                    for q in _normalise_quote(quote):
                        evidence.append(SourceQuote(value=value, quote=q))
            else:
                if item not in (None, "", []):
                    values.append(item)
                    evidence.append(SourceQuote(value=item, quote=None))
        return values if values else None, evidence

    if raw not in (None, "", []):
        evidence.append(SourceQuote(value=raw, quote=None))
    return raw, evidence


def _get_case_insensitive(mapping: dict[str, Any], key: str) -> Any:
    if key in mapping:
        return mapping[key]
    key_lower = key.lower()
    for candidate_key, value in mapping.items():
        if str(candidate_key).lower() == key_lower:
            return value
    return None


def _entity_payload(data: dict[str, Any], entity: str, entities: list[str]) -> dict[str, Any]:
    payload = _get_case_insensitive(data, entity)
    if isinstance(payload, dict):
        return payload

    # Backward tolerance for single-entity outputs that return the old flat shape.
    if len(entities) == 1:
        return data

    return {}


def _chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    if not text:
        return []
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start = end - overlap
    return chunks


def _extract_cache_key(
    chunk_text: str,
    columns: list[ColumnSpec],
    entities: list[str],
    extract_tool: str,
) -> str:
    payload = {
        "chunk_text": chunk_text,
        "columns": sorted(c.name for c in columns),
        "entities": sorted(entities),
        "extract_tool": extract_tool,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _extract_cache_path(cache_key: str) -> str:
    os.makedirs(EXTRACT_CACHE_DIR, exist_ok=True)
    return os.path.join(EXTRACT_CACHE_DIR, f"{cache_key}.json")


def _read_extract_cache(cache_key: str) -> dict[str, Any] | None:
    path = _extract_cache_path(cache_key)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"      ! Extract cache read failed: {exc}")
        return None
    return data if isinstance(data, dict) else None


def _write_extract_cache(cache_key: str, data: dict[str, Any]) -> None:
    path = _extract_cache_path(cache_key)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except OSError as exc:
        print(f"      ! Extract cache write failed: {exc}")


def _merge_chunk_data(chunk_results: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge per-chunk extraction dicts. Non-null answers accumulate; nulls are dropped."""
    merged: dict[str, dict[str, list]] = {}

    def normalise_items(raw: Any) -> list[dict[str, Any]]:
        if raw is None:
            return []

        if isinstance(raw, dict):
            value = raw.get("value")
            if value in (None, "", []):
                return []
            if isinstance(value, list):
                items = []
                for item in value:
                    if isinstance(item, dict):
                        items.extend(normalise_items(item))
                    else:
                        items.extend(normalise_items({"value": item, "quote": raw.get("quote")}))
                return items
            return [{"value": value, "quote": raw.get("quote")}]

        if isinstance(raw, list):
            items = []
            for item in raw:
                items.extend(normalise_items(item))
            return items

        if raw in (None, "", []):
            return []
        return [{"value": raw, "quote": None}]

    def merge_item(items: list[dict[str, Any]], item: dict[str, Any]) -> None:
        value_key = str(item.get("value"))
        for existing in items:
            if str(existing.get("value")) == value_key:
                if existing.get("quote") in (None, "") and item.get("quote") not in (None, ""):
                    existing["quote"] = item.get("quote")
                return
        items.append(item)

    for chunk_data in chunk_results:
        if not chunk_data:
            continue
        for entity, entity_data in chunk_data.items():
            if not isinstance(entity_data, dict):
                continue
            merged.setdefault(entity, {})
            for question, raw in entity_data.items():
                merged[entity].setdefault(question, [])
                for item in normalise_items(raw):
                    merge_item(merged[entity][question], item)

    result: dict[str, Any] = {}
    for entity, questions in merged.items():
        result[entity] = {}
        for question, items in questions.items():
            if not items:
                result[entity][question] = None
            elif len(items) == 1:
                result[entity][question] = items[0]
            else:
                result[entity][question] = items

    return result


def extract_cells(
    page: PageDoc,
    columns: list[ColumnSpec],
    entities: list[str],
    cfg: Config | None = None,
    diag: dict | None = None,
    use_cache: bool = True,
) -> list[ExtractedCell]:
    """Extract cells from a page using the configured extractor."""
    cells = []
    if not columns:
        return cells

    runtime_cfg = cfg or Config(extract_tool=EXTRACT_TOOL)

    text = page.text or ""
    chunks = _chunk_text(text, EXTRACT_CHUNK_SIZE, EXTRACT_CHUNK_OVERLAP) or [""]

    chunk_results: list[dict[str, Any]] = [{} for _ in chunks]
    agg_timing: dict = {"extraction_time_ms": 0, "timed_out": False, "retry_count": 0}

    def extract_chunk(chunk: str) -> tuple[dict[str, Any], dict]:
        cache_key = _extract_cache_key(chunk, columns, entities, runtime_cfg.extract_tool)
        if use_cache:
            cached = _read_extract_cache(cache_key)
            if cached is not None:
                print(f"      -> Extract cache hit: {page.url}")
                return cached, {"extraction_time_ms": 0, "timed_out": False, "retry_count": 0}

        chunk_page = PageDoc(
            url=page.url, text=chunk, html=None,
            from_cache=page.from_cache, depth=page.depth,
            crawl_score=page.crawl_score, fetch_time_ms=page.fetch_time_ms,
            backend=page.backend, render_fallback=page.render_fallback,
            gate_passed=page.gate_passed, gate_reason=page.gate_reason,
        )
        if runtime_cfg.extract_tool == "sgai":
            chunk_data, timing = _extract_with_sgai(chunk_page, columns, entities)
        elif runtime_cfg.extract_tool == "llmapi":
            chunk_data, timing = _extract_with_llmapi(chunk_page, columns, entities)
        elif runtime_cfg.extract_tool == "azure":
            chunk_data, timing = _extract_with_azure(chunk_page, columns, entities)
        else:
            print(f"      X Unknown EXTRACT_TOOL: {runtime_cfg.extract_tool}")
            return {}, {"extraction_time_ms": 0, "timed_out": False, "retry_count": 0}
        if use_cache and chunk_data and not timing["timed_out"]:
            _write_extract_cache(cache_key, chunk_data)
        return chunk_data, timing

    if runtime_cfg.extract_tool not in {"sgai", "llmapi", "azure"}:
        print(f"      X Unknown EXTRACT_TOOL: {runtime_cfg.extract_tool}")
        return cells

    max_workers = min(EXTRACT_MAX_WORKERS, len(chunks))
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(extract_chunk, chunk): index for index, chunk in enumerate(chunks)}
        for future in as_completed(futures):
            index = futures[future]
            try:
                chunk_data, timing = future.result()
            except Exception as exc:
                print(f"      X Chunk extraction failed: {exc}")
                chunk_data = {}
                timing = {"extraction_time_ms": 0, "timed_out": False, "retry_count": 0}
            chunk_results[index] = chunk_data
            agg_timing["extraction_time_ms"] += timing["extraction_time_ms"]
            agg_timing["timed_out"] = agg_timing["timed_out"] or timing["timed_out"]
            agg_timing["retry_count"] += timing["retry_count"]

    data = _merge_chunk_data(chunk_results)
    timing = agg_timing

    for entity in entities:
        payload = _entity_payload(data, entity, entities)

        if diag is not None:
            items_extracted = sum(
                len(v) if isinstance(v, list) else (1 if v is not None else 0)
                for v in payload.values()
            ) if payload else 0
            diag.setdefault("extract_log", []).append({
                "entity": entity,
                "source_url": page.url,
                "question": "; ".join(c.name for c in columns),
                "extract_tool": runtime_cfg.extract_tool,
                "items_extracted": items_extracted,
                "extraction_time_ms": timing["extraction_time_ms"],
                "timed_out": timing["timed_out"],
                "retry_count": timing["retry_count"],
                "page_length_input": len(page.text) if page.text else 0,
                "raw_answer_preview": str(payload)[:300] if payload else "",
            })

        for col in columns:
            raw = _get_case_insensitive(payload, col.name)
            try:
                value, evidence = _parse_field_value(raw)
            except Exception as exc:
                print(f"      ! Parse error {entity}/{col.name}: {exc} — skipping field")
                continue

            cell = ExtractedCell(
                entity=entity,
                source_url=page.url,
                column=col.name,
                value=value,
                evidence=evidence,
            )

            if not evidence:
                print(f"      - {entity} / {col.name}: (no data extracted)")
            elif value is None:
                print(f"      - {entity} / {col.name}: (null value, {len(evidence)} evidence items)")
            elif isinstance(value, list):
                print(f"      - {entity} / {col.name}: [{len(value)} items] ({len(evidence)} evidence)")
            else:
                print(f"      - {entity} / {col.name}: {str(value)[:40]} ({len(evidence)} evidence)")

            cells.append(cell)

    return cells
