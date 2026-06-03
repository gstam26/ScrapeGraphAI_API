from typing import Any

from scrapegraph_py import ScrapeGraphAI, JsonFormatConfig, FetchConfig

from config import API_KEY, FETCH_WAIT_MS
from models import PageDoc, ColumnSpec, ExtractedCell


def _build_prompt(columns: list[ColumnSpec]) -> str:
    fields = ""

    for col in columns:
        if col.instruction:
            fields += f'- "{col.name}": {col.instruction}\n'
        else:
            fields += f'- "{col.name}"\n'

    return f"""
Extract sustainability-related information from the webpage.

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
- Only use information present on the page.
- If the answer is not found, use null.
- If multiple answers are requested, return a JSON array.
- The quote must be copied from the page text where possible.
- Return only JSON.
"""


def extract_cells(page: PageDoc, columns: list[ColumnSpec]) -> list[ExtractedCell]:
    prompt = _build_prompt(columns)

    cells = []

    try:
        sgai = ScrapeGraphAI(api_key=API_KEY)

        result = sgai.scrape(
            page.url,
            formats=[JsonFormatConfig(prompt=prompt)],
            fetch_config=FetchConfig(mode="js", wait=FETCH_WAIT_MS),
        )

        sgai.close()

        data = result.data.results.get("json", {}).get("data", {})

    except Exception as e:
        print(f"    ✗ Extraction failed for {page.url}: {e}")
        data = {}

    for col in columns:
        raw = data.get(col.name)

        value: Any = None
        quote: str | None = None

        if isinstance(raw, dict):
            value = raw.get("value")
            quote = raw.get("quote")

        elif isinstance(raw, list):
            value = []
            quotes = []

            for item in raw:
                if isinstance(item, dict):
                    value.append(item.get("value"))
                    if item.get("quote"):
                        quotes.append(item.get("quote"))
                else:
                    value.append(item)

            quote = " ".join(quotes) if quotes else None

        else:
            value = raw

        cells.append(
            ExtractedCell(
                url=page.url,
                column=col.name,
                value=value,
                quote=quote,
            )
        )

    return cells