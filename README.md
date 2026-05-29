# Plant-Based Milk Sustainability Scraper

A CLI tool that scrapes sustainability information from brand websites and exports the results to a formatted Excel file. Built with [ScrapeGraphAI](https://scrapegraph-ai.readthedocs.io/).

---

## How it works

1. You provide an Excel file with a column of URLs
2. You define what to extract by typing column headers at runtime
3. The tool scrapes each URL using AI and writes results to a new Excel file

Column headers support an optional instruction after a colon, which is injected directly into the AI prompt:

```
Type of milk: return only the base ingredient, lowercase, e.g. "pea", "oat, soy"
Sustainability claims: return as a list of concrete, measurable environmental claims only
```

---

## Setup

**1. Clone the repo and install dependencies**
```bash
pip install -r requirements.txt
```

**2. Add your API key**

Create a `.env` file in the root folder:
```
SGAI_API_KEY=your_key_here
```
Get your key at [scrapegraphai.com](https://scrapegraphai.com).

---

## Usage

**Prepare your input Excel file**

The file needs a column named `URL` or `Link`, one URL per row:

| URL |
|-----|
| https://www.ripplefoods.com/our-story/ |
| https://www.oatly.com/en-us/oatly-who/sustainability-plan |

**Run the tool**
```bash
python main.py
```

You will be prompted to:
1. Enter the path to your input Excel file
2. Define your column headers (with optional instructions after a `:`)
3. Enter the output filename

**Example session**
```
=== Web Scraper ===

Path to input Excel file: urls.xlsx
✓ Loaded 3 URL(s) from 'urls.xlsx'

Column 1: Parent company: return the name of the parent company that owns this brand
Column 2: Brand name(s): return as a list of brand names
Column 3: Type of milk: return only the base ingredient, lowercase, e.g. "pea", "oat, soy"
Column 4: Sustainability claims: return as a list of concrete, measurable environmental claims only. Exclude vague marketing language, nutrition facts, and product attributes.
Column 5: done

Output Excel filename: results.xlsx

Scraping 3 URL(s)...

  Scraping: https://www.ripplefoods.com/our-story/
  Scraping: https://www.oatly.com/en-us/oatly-who/sustainability-plan
  Scraping: https://silk.com/about-us/sustainability/

✓ Results saved to 'results.xlsx' — completed in 28s
```

---

## Project structure

```
├── main.py          # Entry point — handles user inputs and orchestration
├── scraper.py       # Builds the AI prompt and scrapes each URL
├── output.py        # Writes and formats the Excel output
├── config.py        # Loads API key and settings from .env
├── requirements.txt
├── .gitignore
├── .env             # Not committed — add your own
└── outputs/         # All Excel results saved here automatically
```

---

## Tips

- **Empty results** mean the page doesn't contain the information, not a bug
- **Vague claims** (e.g. "dairy free", "organic") can be filtered out by being more specific in your column instruction
- **Speed** is roughly 3–8 seconds per URL; 20 URLs takes 1–3 minutes
- If a brand's main sustainability page returns little, try a different subpage (e.g. `/about`, `/our-story`, `/impact`)

---

## Requirements

- Python 3.10+
- A ScrapeGraphAI API key