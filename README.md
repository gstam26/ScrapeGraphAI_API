# Web Extraction Pipeline

A modular pipeline for extracting structured information from websites and exporting the results to a structured Excel matrix with provenance and verification metadata.

The system is designed around a layered architecture:

Acquire → Filter → Extract → Verify → Excel Output

This structure allows individual components to be improved or replaced independently while maintaining a stable end-to-end workflow.

---

## Features

- Reads URLs from an Excel spreadsheet
- User-defined extraction schema via column prompts
- Automated website acquisition and caching
- AI-based information extraction using ScrapeGraphAI
- Quote-based provenance collection
- Automated verification using fuzzy matching
- Excel output with:
  - Matrix sheet (final results)
  - Provenance sheet (supporting evidence and verification)

---

## Architecture

### Acquire

Downloads and processes website content into a standard document representation.

Current implementation:

- requests
- BeautifulSoup
- Local text cache

Output:

```python
PageDoc(
    url="...",
    text="..."
)
```

### Filter

Receives acquired content and prepares it for extraction.

Current implementation:

- Pass-through (no filtering)

Future work:

- Navigation removal
- Boilerplate removal
- Content relevance filtering

### Extract

Uses ScrapeGraphAI to extract requested fields from each webpage.

Example fields:

```text
Brand name
Parent company
Type of milk
Claims
```

Each extraction returns:

```json
{
  "value": "...",
  "quote": "..."
}
```

where the quote serves as supporting evidence.

### Verify

Checks whether supporting quotes actually appear in the acquired page text.

Current implementation:

- RapidFuzz partial matching

Outputs:

- Verification score
- Verified / not verified flag

### Excel Output

Two worksheets are produced.

#### Matrix

Contains the final extracted values.

| URL | Brand Name | Claims |
|------|------|------|
| ... | ... | ... |

#### Provenance

Contains cell-level metadata.

| URL | Column | Value | Quote | Verified | Score |
|------|------|------|------|------|------|
| ... | ... | ... | ... | True | 100 |

---

## Installation

Install dependencies:

```bash
pip install -r requirements.txt
```

Create a `.env` file:

```env
SGAI_API_KEY=your_api_key_here
```

---

## Usage

Prepare an Excel file containing a URL column:

| URL |
|------|
| https://www.ripplefoods.com/our-story/ |
| https://www.oatly.com/en-us/oatly-who/sustainability-plan |

Run:

```bash
python main.py
```

You will be prompted for:

1. Input Excel file
2. Extraction columns
3. Output filename

Example:

```text
Column 1: Brand name
Column 2: Claims: return only concrete claims
Column 3: done
```

---

## Project Structure

```text
├── main.py
├── config.py
├── models.py
├── acquire.py
├── filter.py
├── extract.py
├── verify.py
├── pipeline.py
├── io_excel.py
├── requirements.txt
├── README.md
├── samples/
├── cache/
├── outputs/
└── .env
```

---

## Output

Generated files are written to:

```text
outputs/
```

Each workbook contains two worksheets:

```text
Matrix
Provenance
```

### Matrix Sheet

Contains the final extracted matrix.

### Provenance Sheet

Contains:

- Extracted value
- Supporting quote
- Verification status
- Verification score

---

## Development Notes

### Cache

The acquisition layer caches downloaded page text locally to:

- Reduce repeated website requests
- Speed up testing
- Support debugging

Cache contents are intended for local development and should not be committed to version control.

### Verification

Verification currently uses RapidFuzz partial matching to determine whether supporting quotes are present in the acquired page text.

This provides a lightweight automated validation layer while more advanced verification approaches are explored.

---

## Current Status

Week 1 implementation:

- Layered pipeline architecture
- Local acquisition cache
- AI extraction using ScrapeGraphAI
- Provenance tracking
- Automated verification
- Excel matrix generation

Planned future work:

- Multi-page crawling
- Content filtering
- Alternative acquisition methods
- Evaluation datasets
- Automated benchmarking
- Cost and latency instrumentation
- Improved verification methods