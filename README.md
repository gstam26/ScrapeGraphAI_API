# Entity Extraction Pipeline

A modular pipeline for extracting structured information from websites and exporting results to an Excel workbook with full provenance and verification metadata.

---

## Pipeline Overview

```
URLs + Column Schema
        │
        ▼
  ┌───────────┐
  │  Acquire  │  Fetch pages (requests / sgai / firecrawl / playwright)
  │           │  Disk-cached by sha256(url)
  └─────┬─────┘
        │  list[FetchedPage]
        ▼
  ┌───────────┐
  │  Filter   │  Route pages → relevant columns
  │           │  (MVP: all columns, all pages)
  └─────┬─────┘
        │  list[RoutedPage]
        ▼
  ┌───────────┐
  │  Extract  │  ScrapeGraphAI JSON extraction
  │           │  Returns value + supporting quote per field
  └─────┬─────┘
        │  list[ExtractedCell]
        ▼
  ┌───────────┐
  │  Verify   │  RapidFuzz partial match
  │           │  Checks quotes exist in acquired text
  └─────┬─────┘
        │  list[ExtractedCell] (with verification scores)
        ▼
  ┌───────────┐
  │ Aggregate │  Best-cell selection per column
  │           │  Prefers: value > evidence-only, verified > unverified
  └─────┬─────┘
        │  PipelineResult
        ▼
  Excel Output  (Matrix sheet + Provenance sheet)
```

---

## Features

- **Pluggable fetchers** — swap between `requests`, ScrapeGraphAI, Firecrawl, or Playwright via a single config field
- **Persistent disk cache** — pages are cached by `sha256(url)`, so re-runs are instant and API costs are zero on repeat calls
- **Guided crawling** — optional multi-page crawl that scores internal links against your extraction schema before following them (disabled by default)
- **Quote-level provenance** — every extracted value is paired with an exact quote from the source page
- **Automated verification** — fuzzy-match score confirms quotes actually appear in the fetched text
- **Excel output** — Matrix sheet for final values, Provenance sheet for full audit trail

---

## Installation

```bash
pip install -r requirements.txt
```

Create a `.env` file in the project root:

```env
SGAI_API_KEY=your_scrapegraphai_api_key
```

---

## Usage

Prepare an input Excel file with a `URL` column:

| URL |
|-----|
| https://www.ripplefoods.com/our-story/ |
| https://www.oatly.com/sustainability |

Run the pipeline:

```bash
python main.py
```

You will be prompted for:

1. **Input Excel path** — the file containing your URLs
2. **Extraction columns** — names and optional instructions
3. **Output filename** — written to `outputs/`

**Column input examples:**

```
Column 1: Brand name
Column 2: Parent company
Column 3: Type of milk: return only the base word, e.g. oat, pea, almond
Column 4: Sustainability claims: return as a list, one item per claim
Column 5: done
```

---

## Configuration

All runtime settings live in `config.py`. Key options:

| Setting | Default | Description |
|---------|---------|-------------|
| `ACQUIRE_TOOL` | `"requests"` | Fetcher backend (`requests` / `sgai` / `firecrawl` / `playwright`) |
| `CRAWL_ENABLED` | `False` | Enable multi-page guided crawling |
| `CRAWL_MAX_DEPTH` | `1` | Max link hops from seed URL |
| `CRAWL_MAX_PAGES` | `2` | Max pages fetched per entity |
| `CRAWL_MIN_SCORE` | `0.12` | Min relevance score for a link to be followed |
| `VERIFY_THRESHOLD` | `70` | Minimum fuzzy-match score to mark a quote verified |
| `EXTRACT_TIMEOUT` | `30` | Seconds before an extraction call is abandoned |

The `Config` model (in `models.py`) exposes these same settings as a typed object for programmatic use:

```python
from models import Config
from acquire import acquire

cfg = Config(acquire_tool="sgai", fetch_wait_ms=5000)
pages = acquire(["https://example.com"], cfg)
```

---

## Output

Results are written to `outputs/`. Each workbook contains two sheets.

### Matrix sheet

One row per entity, one column per requested field:

| URL | Brand name | Type of milk | Claims |
|-----|-----------|--------------|--------|
| https://... | Oatly | oat | ["carbon label", ...] |

### Provenance sheet

One row per evidence item, with full audit metadata:

| Entity URL | Source URL | Column | Value | Quote | Verified | Score |
|------------|------------|--------|-------|-------|----------|-------|
| https://... | https://.../sustainability | Claims | carbon label | "we carbon label every product" | True | 97.0 |

---

## Fetcher Backends

| Tool | Requires | Best for |
|------|----------|----------|
| `requests` | nothing extra | Fast, static HTML pages |
| `sgai` | `SGAI_API_KEY` | JS-rendered pages, AI-native markdown |
| `firecrawl` | `firecrawl-py` + key | Crawl-optimised markdown extraction |
| `playwright` | `playwright` package | Full browser rendering, complex SPAs |

Switch backends by setting `acquire_tool` in `Config` or `ACQUIRE_TOOL` in `config.py`. All backends share the same disk cache, so switching tools does not re-fetch cached URLs.

---

## Guided Crawling

When `CRAWL_ENABLED = True`, the pipeline replaces the single-page fetch with a scored BFS crawl:

1. Start from the seed URL
2. Extract all same-domain links
3. Score each link using URL path + anchor text vs. your column schema
4. Follow only links above `CRAWL_MIN_SCORE`, up to `CRAWL_MAX_PAGES`
5. Skip noisy URLs (login, cart, cookie policy, etc.)

This means the crawler selects pages most likely to contain the data you asked for, rather than crawling blindly.

---

## Project Structure

```
├── main.py              # CLI entry point
├── pipeline.py          # Orchestrates all stages end-to-end
├── config.py            # All tunable settings
├── models.py            # Pydantic data models (FetchedPage, Config, …)
│
├── acquire.py           # Stage 1 — fetch + cache pages
├── filter.py            # Stage 2 — route pages to relevant columns
├── extract.py           # Stage 3 — AI extraction via ScrapeGraphAI
├── verify.py            # Stage 4 — quote verification via RapidFuzz
├── aggregate.py         # Stage 5 — best-cell selection per column
│
├── crawler.py           # Guided multi-page BFS crawler
├── crawl_planner.py     # Derives crawl intent from column schema
├── link_scorer.py       # Relevance scorer for candidate links
│
├── io_excel.py          # Excel reader/writer
│
├── test_smoke.py        # Unit smoke tests (no network)
├── test_acquire_smoke.py# Acquire layer integration tests (requires network)
│
├── requirements.txt
├── .env                 # SGAI_API_KEY (not committed)
├── cache/               # sha256-keyed page cache (not committed)
└── outputs/             # Generated Excel files
```

---

## Running Tests

Unit tests (no network, no API key needed):

```bash
python test_smoke.py
```

Acquire layer smoke tests (live network, 3 URLs):

```bash
python test_acquire_smoke.py
```

---

## Roadmap

- [ ] Content filtering (remove nav, footers, cookie banners)
- [ ] Embedding-based column routing in the Filter layer
- [ ] Firecrawl and Playwright fetcher validation
- [ ] Evaluation dataset + automated benchmarking
- [ ] Cost and latency tracking per run
- [ ] Stage 7: configurable crawl depth via `Config`
