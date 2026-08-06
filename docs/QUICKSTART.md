# Quickstart — running the pipeline on your own companies

You give the tool a list of companies, their websites, and the questions you
want answered. It crawls each site, extracts answers **with verbatim quotes**,
independently verifies every quote against the source page, and writes an
Excel workbook you can open and read.

No answer is ever taken on the AI's word alone: if a quote can't be found on
the page it claims to come from, the answer is marked unverified.

## 1. Make your input workbook

Generate a blank template:

```
python scripts/build_input_template.py my_project.xlsx
```

Then fill in the three sheets (examples are pre-filled — replace them):

| sheet | what goes in it |
|---|---|
| **entities** | one company name per row (exactly as you want it in the output) |
| **urls** | the company's website, a crawl depth (1 is the recommended default), and which entity the URL belongs to |
| **questions** | one question per row, plus optional instructions (answer format, what counts as evidence) |

The **config** sheet is optional — sensible defaults apply, and the template's
config tab lists every supported setting with its default, allowed values, and
what it does. The ones most worth knowing:

| setting | default | meaning |
|---|---|---|
| `CRAWL_MAX_PAGES` | 15 | page budget per company |
| `CRAWL_SCOPE` | host | `site` also crawls the company's own subdomains |
| `CRAWL_RENDER_FOR_DISCOVERY` | false | `true` helps sites whose menus need JavaScript |

Tips that matter:
- **URLs**: full addresses (`https://www.acme.com`). If a site has moved or
  been acquired, give the current site — the tool reports what the site says.
- **Questions**: specific beats generic. "Does the company have ISO 13485
  certification?" outperforms "Quality?".
- **Instructions**: state the answer format you want ("Answer Yes, No, or
  Not disclosed") — the output will follow it.

## 2. Run

**Easiest — the web page:** double-click `run_ui.bat` (or run
`python -m webapp`). Your browser opens a local page: drag your workbook in,
press **Run pipeline**, watch the live log, download the results when done.
You can also download a blank input template from the page. Everything runs
on your machine — nothing is uploaded anywhere.

**Or the command line:**

```
python main.py --input my_project.xlsx --output my_results.xlsx
```

Expect roughly 30–60 seconds per company on a first run (much faster on
re-runs — pages and extractions are cached). The run ends with a summary:
pages fetched, LLM calls, cells answered, and **any sites that yielded
nothing** — an unreachable or bot-blocking site is reported as a finding, not
silently skipped.

## 3. Read the output (`outputs/my_results.xlsx`)

Read the tabs in this order:

| tab | what it is |
|---|---|
| **Summary / AI Summary** | one row per company — the consultant-facing view |
| **Matrix** | the raw answer grid (companies × questions) |
| **Provenance** | every claim with its verbatim quote, source URL, and verification status — the audit trail |
| remaining tabs | diagnostics (what was crawled, filtered, extracted) |

How to read an answer:
- **Conflicting values shown side-by-side** ("Hong Kong; Dongguan, China") —
  the sources disagree; the tool shows both rather than guessing.
- **"Not disclosed"** — the site was read and does not state the answer.
- **"No data found"** — nothing usable was retrieved for that cell.
- **(unverified)** — the quote could not be confirmed on the source page;
  treat with caution.

## Honest limits

- The tool reports **what the company's website says** — not ground truth.
  A site that hasn't announced its acquisition will look independent.
- Some sites block automated access entirely (shown in the run summary).
  Those companies need manual research; the block itself is worth noting.
- Marketing sites rarely state revenue, employee counts, or production
  volumes — expect "Not disclosed" on those; it means the tool didn't guess.
