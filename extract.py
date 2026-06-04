import json
from typing import Any
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

from scrapegraph_py import ScrapeGraphAI, JsonFormatConfig

from config import API_KEY, EXTRACT_TOOL, EXTRACT_TIMEOUT
from models import PageDoc, ColumnSpec, ExtractedCell, SourceQuote


def _build_prompt(columns: list[ColumnSpec], page_text: str | None = None) -> str:
    """Build extraction prompt from column specs and optional page content.

    The page_text is truncated before insertion to avoid overly large prompts.
    """
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
        # Truncate conservatively to avoid huge prompts; include a clear separator.
        # Keep the final prompt safely below typical SDK limits (10k chars).
        # Reserve room for the base prompt by truncating to 7000 chars.
        snippet = page_text[:7000]
        return base + "\nPage content (truncated):\n'''\n" + snippet + "\n'''\n"

    return base


def _extract_with_sgai(page: PageDoc, columns: list[ColumnSpec]) -> dict[str, Any]:
    """
    Extract fields using ScrapeGraphAI.

    Attempts to pass pre-fetched page content to the SDK to avoid re-fetching.
    Falls back to URL scraping if content-mode isn't supported by the SDK.

    Runs the SDK call inside a thread with a timeout (EXTRACT_TIMEOUT) so slow
    network/SDK fetches do not block the pipeline for long.
    """
    # Include a truncated slice of the acquired page text in the prompt so the extractor
    # can use pre-fetched content instead of attempting to re-fetch the URL.
    page_text = page.text if getattr(page, "text", None) else None
    prompt = _build_prompt(columns, page_text[:7000] if page_text else None)

    print(f"      → SGAI extracting: {page.url}")
    t0 = time.time()

    _MAX_ATTEMPTS = 2
    _RETRY_WAIT_S = 5

    result = None
    try:
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            sgai = ScrapeGraphAI(api_key=API_KEY)

            # Define a scraping function that tries content-first signatures
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
                    return {}
                except Exception as e:
                    duration = time.time() - t0
                    print(f"      ✗ Extraction error during call after {duration:.2f}s: {e}")
                    try:
                        sgai.close()
                    except Exception:
                        pass
                    return {}

            # Successful call — close client and exit retry loop
            duration = time.time() - t0
            print(f"      → SGAI call completed in {duration:.2f}s (attempt {attempt}/{_MAX_ATTEMPTS})")
            try:
                sgai.close()
            except Exception:
                pass
            break

        # Diagnostics
        if result is None:
            print("      ⚠ Result is None")
            return {}

        data_obj = getattr(result, "data", None)
        if data_obj is None:
            print("      ⚠ result.data is None")
            return {}

        results = getattr(data_obj, "results", None)
        if not results:
            print("      ⚠ result.data.results is empty or falsy")
            return {}

        # Prefer structured JSON result under key 'json'
        json_data = {}
        if isinstance(results, dict):
            json_entry = results.get("json") or results.get("json_format") or results.get("json_result")
            if json_entry and isinstance(json_entry, dict):
                json_data = json_entry.get("data", {})

        # Fallback: if results already looks like a mapping of fields
        if not json_data and isinstance(results, dict):
            # Attempt to use results directly if it contains field names
            json_data = results

        if not json_data:
            print(f"      ⚠ Extraction returned no usable JSON data (keys: {list(results.keys()) if isinstance(results, dict) else type(results)})")
            return {}

        # Final sanity check
        if not isinstance(json_data, dict):
            print(f"      ⚠ Parsed json_data is not a dict: {type(json_data)}")
            return {}

        return json_data

    except Exception as e:
        duration = time.time() - t0
        print(f"      ✗ Extraction error after {duration:.2f}s: {e}")
        return {}


def _parse_field_value(raw: Any) -> tuple[Any, list[SourceQuote]]:
    """
    Parse a field value and create evidence items.
    
    Returns:
        (aggregated_value, evidence_items)
    
    For scalar: value is the scalar, evidence has one item
    For list: value is the list, evidence has one item per list element
    """
    evidence = []

    if raw is None:
        return None, evidence

    # Dict response: value + quote format
    if isinstance(raw, dict):
        value = raw.get("value")
        quote = raw.get("quote")

        if value not in (None, "", []):
            evidence.append(SourceQuote(value=value, quote=quote))

        return value, evidence

    # List of dict responses: one evidence per item
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
                # Plain scalar in list
                if item not in (None, "", []):
                    values.append(item)
                    evidence.append(SourceQuote(value=item, quote=None))

        return values if values else None, evidence

    # Plain scalar value
    else:
        if raw not in (None, "", []):
            evidence.append(SourceQuote(value=raw, quote=None))

        return raw, evidence


def extract_cells(page: PageDoc, columns: list[ColumnSpec]) -> list[ExtractedCell]:
    """
    Extract cells from a page using configured extractor.
    
    Creates evidence items:
    - For scalar values: one item
    - For list values: one item per element
    
    This uses the pre-fetched page content (from acquire.py) to avoid
    re-fetching and bypassing the cache.
    """
    cells = []

    # For now, only SGAI is supported. Can add Claude dispatch later.
    if EXTRACT_TOOL != "sgai":
        print(f"      ✗ Unknown EXTRACT_TOOL: {EXTRACT_TOOL}")
        return cells

    data = _extract_with_sgai(page, columns)

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
                first_val = str(value)[:40]
                print(f"      • {col.name}: {first_val} ({len(evidence)} evidence)")

        cells.append(cell)

    return cells

