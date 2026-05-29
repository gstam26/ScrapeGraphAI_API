from scrapegraph_py import ScrapeGraphAI, JsonFormatConfig, FetchConfig


def parse_columns(raw_columns: list[str]) -> tuple[list[str], dict[str, str]]:
    """
    Split each entry on the first ':'.
    Returns:
      - headers: clean column names (before the colon)
      - hints: dict of {header: instruction} for entries that had a colon
    """
    headers = []
    hints = {}
    for col in raw_columns:
        if ":" in col:
            header, hint = col.split(":", 1)
            header, hint = header.strip(), hint.strip()
        else:
            header, hint = col.strip(), None
        headers.append(header)
        if hint:
            hints[header] = hint
    return headers, hints


def build_prompt(headers: list[str], hints: dict[str, str]) -> str:
    fields = ""
    for h in headers:
        if h in hints:
            fields += f'- "{h}" ({hints[h]})\n'
        else:
            fields += f'- "{h}"\n'

    return f"""
Extract the following information from this page and return it as a JSON object.
Use exactly these keys (match the casing and spacing), following any instructions in parentheses:
{fields}
Rules:
- Only return factual information found on the page.
- If a field is not found, return null for that key.
- Do not invent or infer information.
- Return a single flat JSON object, not a list.
- For any field whose instruction says to return multiple items or a list, return a JSON array of strings.
"""


def scrape_url(url: str, raw_columns: list[str], api_key: str, wait_ms: int) -> dict:
    headers, hints = parse_columns(raw_columns)
    prompt = build_prompt(headers, hints)
    row = {"URL": url}

    try:
        sgai = ScrapeGraphAI(api_key=api_key)
        result = sgai.scrape(
            url,
            formats=[JsonFormatConfig(prompt=prompt)],
            fetch_config=FetchConfig(mode="js", wait=wait_ms)
        )
        sgai.close()

        data = result.data.results.get("json", {}).get("data", {})
        for header in headers:
            row[header] = data.get(header)

    except Exception as e:
        print(f"    ✗ Error scraping {url}: {e}")
        for header in headers:
            row[header] = None

    return row, headers
