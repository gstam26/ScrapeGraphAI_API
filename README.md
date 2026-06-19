# Entity Extraction Pipeline

A modular pipeline for extracting structured information about named entities from websites and exporting the results to an Excel workbook with provenance and verification metadata.

## Pipeline Overview

```text
Input workbook
  entities + urls + questions + optional config
        |
        v
Acquire
  Fetch seed URLs and optionally crawl relevant same-domain pages.
        |
        v
Filter
  Route acquired pages to the requested extraction questions.
        |
        v
Extract
  Ask ScrapeGraphAI for answers about the relevant entities on each page.
        |
        v
Verify
  Check supporting quotes against acquired page text with RapidFuzz.
        |
        v
Aggregate
  Group by entity and question, then select the best evidence.
        |
        v
Excel output
  Matrix + Provenance + optional diagnostic sheets
```

## Features

- Entity-first extraction: output rows are real entity names, not URL strings.
- URL scoping: each URL can apply to one entity, multiple entities, or all entities.
- Per-question instructions: each question can include prompt instructions in the workbook.
- Per-run config overrides: selected `config.py` settings can be overridden from the workbook.
- Backward compatibility: workbooks without an `entities` sheet treat each URL as its own entity.
- Pluggable acquisition tools: `requests`, ScrapeGraphAI, Firecrawl, or Playwright.
- Persistent disk cache: pages are cached by `sha256(url)` to reduce repeat fetches.
- Guided crawling: optional depth-based crawl that scores candidate links against the questions.
- Quote-level provenance: every extracted claim can include a supporting source quote.
- Automated verification: quote matches are scored before output.

## Installation

```bash
pip install -r requirements.txt
```

Create a `.env` file in the project root:

```env
AZURE_API_KEY=your_azure_api_key
```

Set `EXTRACT_TOOL` to the backend you want to use. Supported extraction backends are
`sgai`, `llmapi`, `azure`, and `claude`; the project default is `azure`.

Claude is available as an optional extractor for small runs or spot checks:

```env
CLAUDE_API_KEY=your_claude_api_key
CLAUDE_MODEL=claude-haiku-4-5-20251001
EXTRACT_TOOL=claude
```

It calls Anthropic's HTTPS API directly with the existing `httpx` dependency, so the
Anthropic Python SDK is not required. Large extraction runs can hit Anthropic rate
limits unless worker counts and chunking are tuned down.

## Usage

Prepare an input workbook using the format below, or start from:

```text
samples/test_smoke.xlsx
```

Run the pipeline:

```bash
python main.py
```

The terminal prompts only ask for:

1. Input Excel file path.
2. Output Excel filename.

Everything else comes from the workbook. Results are written to `outputs/<filename>.xlsx`.

Example run:

```text
Path to input Excel file: samples/test_smoke.xlsx
Output Excel filename: test_output.xlsx
```

## Input Workbook Format

The new input format uses four sheets: `entities`, `urls`, `questions`, and optional `config`.

### entities sheet

One column: `entity`.

Each row is one entity name. These names become the row labels in the output Matrix sheet.

| entity |
|--------|
| Oatly |
| Ripple |
| Califia |
| Silk |
| Elmhurst |

### urls sheet

Three columns: `url`, `depth`, and `entities`.

| url | depth | entities |
|-----|-------|----------|
| https://www.oatly.com/oatly-who/sustainability-plan/sustainability-report | 1 | Oatly |
| https://ripplefoods.com/pages/our-story | 1 | Ripple |
| https://www.mintel.com/food-and-drink/plant-based-milk | 0 | |

Rules:

- `url` is required.
- `depth` is optional and defaults to `0` when blank.
- `depth` must be an integer: `0`, `1`, or `2`.
- `entities` is optional.
- If `entities` is blank, the URL is relevant to all entities from the `entities` sheet.
- If `entities` is present, use comma-separated entity names, for example `Oatly, Ripple`.
- Entity names in this column must match names from the `entities` sheet.

### questions sheet

Two columns: `question` and optional `instructions`.

| question | instructions |
|----------|--------------|
| What sustainability claims does the brand make? | return as a list, one claim per item |
| What is the carbon footprint of their products? | include specific numbers and units where stated |
| Do they have any sustainability certifications? | return the certification name and issuing body |

Rules:

- `question` is required.
- `instructions` is optional.
- If the `instructions` column is absent, all instructions are treated as blank.
- Non-blank instructions are appended to the extraction prompt with the old colon syntax, equivalent to `Question: instructions`.

### config sheet

Optional. Two columns: `setting` and `value`.

| setting | value |
|---------|-------|
| CRAWL_MAX_PAGES | 15 |
| DEFAULT_DEPTH | 1 |

Supported settings:

| Setting | Type | Description |
|---------|------|-------------|
| `ACQUIRE_TOOL` | string | Fetcher backend for this run. |
| `EXTRACT_TOOL` | string | Extraction backend for this run. |
| `CRAWL_MIN_SCORE` | float | Minimum crawl link relevance score. |
| `CRAWL_MAX_PAGES` | integer | Maximum pages fetched per seed URL crawl. |
| `DEFAULT_DEPTH` | integer | Default depth used when a URL entry does not provide one in legacy contexts. |

If the `config` sheet is absent, values from `config.py` are used.

### Backward Compatibility

If the workbook has no `entities` sheet, the reader falls back to the legacy behavior:

- The `urls` sheet, or the first sheet if `urls` is missing, is read for URLs.
- Each URL becomes its own entity.
- Each URL is extracted only for that URL-as-entity row.

This keeps older URL-only workbooks usable, but the new four-sheet format is preferred.

## Sample Workbook

The repository includes:

```text
samples/test_smoke.xlsx
```

It contains:

- Entities: `Oatly`, `Ripple`, `Califia`, `Silk`, `Elmhurst`.
- Five brand sustainability URLs, each scoped to its matching entity at depth `1`.
- One multi-entity URL, `https://www.mintel.com/food-and-drink/plant-based-milk`, scoped to all entities by leaving `entities` blank.
- Three sustainability questions with instructions.
- Config overrides: `CRAWL_MAX_PAGES = 15` and `DEFAULT_DEPTH = 1`.

## Configuration

Default runtime settings live in `config.py`.

| Setting | Default | Description |
|---------|---------|-------------|
| `ACQUIRE_TOOL` | `"firecrawl"` | Fetcher backend: `requests`, `sgai`, `firecrawl`, or `playwright`. |
| `EXTRACT_TOOL` | `"azure"` | Extractor backend: `sgai`, `llmapi`, `azure`, or `claude`. Can be set from `.env`; use `azure`/`llmapi` for full runs unless Claude quota is sufficient. |
| `VERIFY_TOOL` | `"rapidfuzz"` | Quote verification backend. |
| `CACHE_DIR` | `"cache"` | Directory for cached page text. |
| `OUTPUT_DIR` | `"outputs"` | Directory for generated Excel output. |
| `DEFAULT_DEPTH` | `0` | Default crawl depth outside explicit workbook URL depths. |
| `CRAWL_MAX_DEPTH` | `1` | Max link hops from a seed URL. |
| `CRAWL_MAX_PAGES` | `2` | Max pages fetched per crawl. |
| `CRAWL_MIN_SCORE` | `0.12` | Minimum relevance score for a link to be followed. |
| `VERIFY_THRESHOLD` | `70` | Minimum fuzzy-match score to mark a quote verified. |
| `EXTRACT_TIMEOUT` | `120` | Seconds before an extraction call times out. |
| `DIAGNOSTICS` | `True` | Include diagnostic sheets in output. |

Workbook `config` overrides apply only to that run and do not modify `config.py`.

## Output Workbook

Results are written to `outputs/`. By default, the workbook contains these sheets:

- `Summary`
- `Matrix`
- `Provenance`
- `Acquire Log`
- `Crawl Candidates`
- `Extract Log`
- `Verify Log`

If `DIAGNOSTICS = False`, only `Summary`, `Matrix`, and `Provenance` are written.

### Matrix sheet

One row per entity, one column per question. The first column header is `Entity`.

| Entity | What sustainability claims does the brand make? | What is the carbon footprint of their products? |
|--------|--------------------------------------------------|-------------------------------------------------|
| Oatly | - claim 1<br>- claim 2 | - 0.31 kg CO2e where stated |
| Ripple | No data found | - value from supporting evidence |

Cells with no data are highlighted. Unverified claims are marked in the cell text and highlighted.

### Provenance sheet

One row per evidence item, with the entity and source URL preserved.

| Entity | Source URL | Question | Claim | Verbatim Quote | Verified | Verification Score | Match Type | Source Page Depth |
|--------|------------|----------|-------|----------------|----------|--------------------|------------|-------------------|
| Oatly | https://... | What sustainability claims does the brand make? | claim text | supporting quote | TRUE | 97.0 | fuzzy | 1 |

The `Entity` column identifies which entity the claim is about. `Source URL` keeps URL traceability.

## Development Checks

Run unit smoke tests:

```bash
python test_smoke.py
```

These tests cover:

- Input parsing for the four-sheet workbook.
- Blank URL entities expanding to all entities.
- Specific URL entities staying scoped.
- Backward compatibility without an `entities` sheet.
- Entity-based aggregation.
- Matrix and Provenance output shape.
- `main.py` only prompting for input path and output filename.

Run acquire integration tests with live network access:

```bash
python test_acquire_smoke.py
```

## Project Structure

```text
.
+-- main.py
+-- pipeline.py
+-- config.py
+-- models.py
+-- io_excel.py
+-- aggregate.py
+-- extract.py
+-- filter.py
+-- verify.py
+-- src/
|   +-- acquire/
|       +-- __init__.py
|       +-- cache.py
|       +-- crawler.py
|       +-- fetcher.py
|       +-- link_scorer.py
|       +-- models.py
+-- samples/
|   +-- test_smoke.xlsx
+-- diagnostics/
+-- test_smoke.py
+-- test_acquire_smoke.py
+-- requirements.txt
+-- cache/
+-- outputs/
```

## Notes

- `cache/` and `outputs/` are generated directories.
- `samples/test_smoke.xlsx` is intentionally committed even though `*.xlsx` files are otherwise ignored.
- The input workbook is now the source of truth for entities, URLs, questions, and supported run-level config overrides.
