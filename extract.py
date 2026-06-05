from typing import Any
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

from scrapegraph_py import ScrapeGraphAI, JsonFormatConfig

from config import API_KEY, EXTRACT_TOOL, EXTRACT_TIMEOUT
from models import PageDoc, ColumnSpec, ExtractedCell, SourceQuote


def _build_prompt(columns: list[ColumnSpec], page_text: str | None = None) -> str:
    fields = ""
    for col in columns:
        if col.instruction:
            fields += f'- "{col.name}": {col.instruction}\n'
        else:
            fields += f'- "{col.name}"\n'

    base = f"""
Extract information from the provided webpage content.

Return a JSON object with exactly these top-level keys:
{fields}

For each key, return an object with this structure:
{{
  "value": the extracted answer, or null if not found,
  "quote": a short exact quote from the page supporting the answer, or null if not found
}}

Rules:
- Use exactly the requested column names.
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


def _extract_with_sgai(page: PageDoc, columns: list[ColumnSpec]) -> tuple[dict[str, Any], dict]:
    """
    Extract fields using ScrapeGraphAI.

    Returns (data_dict, timing_info) where timing_info has keys:
      extraction_time_ms, timed_out, retry_count
    """
    page_text = page.text if getattr(page, "text", None) else None
    prompt = _build_prompt(columns, page_text[:7000] if page_text else None)

    print(f"      → SGAI extracting: {page.url}")
    t0 = time.time()

    _MAX_ATTEMPTS = 2
    _RETRY_WAIT_S = 5
    _timed_out = False
    _attempts_made = 0

    def _make_timing():
        return {
            "extraction_time_ms": int((time.time() - t0) * 1000),
            "timed_out": _timed_out,
            "retry_count": max(_attempts_made - 1, 0),
        }

    result = None
    try:
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            _attempts_made = attempt
            sgai = ScrapeGraphAI(api_key=API_KEY)

            def _do_scrape():
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
                future = ex.submit(_do_scrape)
                try:
                    result = future.result(timeout=EXTRACT_TIMEOUT)
                except FuturesTimeoutError:
                    _timed_out = True
                    duration = time.time() - t0
                    try:
                        sgai.close()
                    except Exception:
                        pass
                    if attempt < _MAX_ATTEMPTS:
                        print(f"      ⚠ SGAI timed out after {EXTRACT_TIMEOUT}s (attempt {attempt}/{_MAX_ATTEMPTS}) — retrying in {_RETRY_WAIT_S}s...")
                        time.sleep(_RETRY_WAIT_S)
                        continue
                    print(f"      ⚠ SGAI timed out on final attempt after {EXTRACT_TIMEOUT}s (elapsed {duration:.2f}s)")
                    return {}, _make_timing()
                except Exception as e:
                    duration = time.time() - t0
                    print(f"      ✗ Extraction error during call after {duration:.2f}s: {e}")
                    try:
                        sgai.close()
                    except Exception:
                        pass
                    return {}, _make_timing()

            duration = time.time() - t0
            print(f"      → SGAI call completed in {duration:.2f}s (attempt {attempt}/{_MAX_ATTEMPTS})")
            try:
                sgai.close()
            except Exception:
                pass
            break

        if result is None:
            print("      ⚠ Result is None")
            return {}, _make_timing()

        data_obj = getattr(result, "data", None)
        if data_obj is None:
            print("      ⚠ result.data is None")
            return {}, _make_timing()

        results = getattr(data_obj, "results", None)
        if not results:
            print("      ⚠ result.data.results is empty or falsy")
            return {}, _make_timing()

        json_data = {}
        if isinstance(results, dict):
            json_entry = results.get("json") or results.get("json_format") or results.get("json_result")
            if json_entry and isinstance(json_entry, dict):
                json_data = json_entry.get("data", {})

        if not json_data and isinstance(results, dict):
            json_data = results

        if not json_data:
            print(f"      ⚠ Extraction returned no usable JSON data (keys: {list(results.keys()) if isinstance(results, dict) else type(results)})")
            return {}, _make_timing()

        if not isinstance(json_data, dict):
            print(f"      ⚠ Parsed json_data is not a dict: {type(json_data)}")
            return {}, _make_timing()

        return json_data, _make_timing()

    except Exception as e:
        duration = time.time() - t0
        print(f"      ✗ Extraction error after {duration:.2f}s: {e}")
        return {}, _make_timing()


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

    elif isinstance(raw, list):
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

    else:
        if raw not in (None, "", []):
            evidence.append(SourceQuote(value=raw, quote=None))
        return raw, evidence


def extract_cells(
    page: PageDoc,
    columns: list[ColumnSpec],
    entity_url: str = "",
    diag: dict | None = None,
) -> list[ExtractedCell]:
    """
    Extract cells from a page using configured extractor.
    """
    cells = []

    if EXTRACT_TOOL != "sgai":
        print(f"      ✗ Unknown EXTRACT_TOOL: {EXTRACT_TOOL}")
        return cells

    data, timing = _extract_with_sgai(page, columns)

    if diag is not None:
        items_extracted = sum(
            len(v) if isinstance(v, list) else (1 if v is not None else 0)
            for v in data.values()
        ) if data else 0
        diag.setdefault("extract_log", []).append({
            "entity_url": entity_url,
            "source_url": page.url,
            "question": "; ".join(c.name for c in columns),
            "extract_tool": EXTRACT_TOOL,
            "items_extracted": items_extracted,
            "extraction_time_ms": timing["extraction_time_ms"],
            "timed_out": timing["timed_out"],
            "retry_count": timing["retry_count"],
            "page_length_input": len(page.text) if page.text else 0,
            "raw_answer_preview": str(data)[:300] if data else "",
        })

    for col in columns:
        raw = data.get(col.name)
        value, evidence = _parse_field_value(raw)

        cell = ExtractedCell(
            source_url=page.url,
            column=col.name,
            value=value,
            evidence=evidence,
        )

        if not evidence:
            print(f"      • {col.name}: (no data extracted)")
        elif value is None:
            print(f"      • {col.name}: (null value, {len(evidence)} evidence items)")
        else:
            if isinstance(value, list):
                print(f"      • {col.name}: [{len(value)} items] ({len(evidence)} evidence)")
            else:
                print(f"      • {col.name}: {str(value)[:40]} ({len(evidence)} evidence)")

        cells.append(cell)

    return cells
