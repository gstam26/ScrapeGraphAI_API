from typing import Any
import json
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

try:
    from scrapegraph_py import JsonFormatConfig, ScrapeGraphAI
except ImportError:
    JsonFormatConfig = None
    ScrapeGraphAI = None

from config import API_KEY, EXTRACT_TIMEOUT, EXTRACT_TOOL
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
        snippet = page_text[:7000]
        return base + "\nPage content (truncated):\n'''\n" + snippet + "\n'''\n"
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
    prompt = _build_prompt(columns, entities, page_text[:7000] if page_text else None)

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
    prompt = _build_prompt(columns, entities, page_text[:7000] if page_text else None)

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

        with ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(llm.call, prompt)
            try:
                raw = future.result(timeout=EXTRACT_TIMEOUT)
            except FuturesTimeoutError:
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


def _parse_field_value(raw: Any) -> tuple[Any, list[SourceQuote]]:
    evidence = []

    if raw is None:
        return None, evidence

    if isinstance(raw, dict):
        value = raw.get("value")
        quote = raw.get("quote")
        if value not in (None, "", []):
            evidence.append(SourceQuote(value=value, quote=quote))
        return value, evidence

    if isinstance(raw, list):
        values = []
        for item in raw:
            if isinstance(item, dict):
                value = item.get("value")
                quote = item.get("quote")
                if value not in (None, "", []):
                    values.append(value)
                    evidence.append(SourceQuote(value=value, quote=quote))
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


def extract_cells(
    page: PageDoc,
    columns: list[ColumnSpec],
    entities: list[str],
    cfg: Config | None = None,
    diag: dict | None = None,
) -> list[ExtractedCell]:
    """Extract cells from a page using the configured extractor."""
    cells = []
    runtime_cfg = cfg or Config(extract_tool=EXTRACT_TOOL)

    if runtime_cfg.extract_tool == "sgai":
        data, timing = _extract_with_sgai(page, columns, entities)
    elif runtime_cfg.extract_tool == "llmapi":
        data, timing = _extract_with_llmapi(page, columns, entities)
    else:
        print(f"      X Unknown EXTRACT_TOOL: {runtime_cfg.extract_tool}")
        return cells

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
            value, evidence = _parse_field_value(raw)

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
