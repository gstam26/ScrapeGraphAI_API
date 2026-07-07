import os
from dotenv import load_dotenv

load_dotenv()

# ============================================================
# API
# ============================================================

API_KEY = os.getenv("SGAI_API_KEY")

AZURE_API_KEY = os.getenv("AZURE_API_KEY")
AZURE_ENDPOINT = os.getenv("AZURE_ENDPOINT", "https://thebeastgpu.openai.azure.com/openai/v1")
AZURE_DEPLOYMENT = os.getenv("AZURE_DEPLOYMENT", "gpt-4.1-mini")

CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")

# ============================================================
# TOOLS
# ============================================================

# Dev default.  Change to "firecrawl" (or "sgai"/"playwright"/"requests") for
# deployment — Firecrawl is the deployment-default candidate.
FETCH_BACKEND = "firecrawl"

ACQUIRE_TOOL = FETCH_BACKEND
EXTRACT_TOOL = os.getenv("EXTRACT_TOOL", "azure")
VERIFY_TOOL = "rapidfuzz"

# ============================================================
# PATHS
# ============================================================

CACHE_DIR = "cache"
EXTRACT_CACHE_DIR = "cache/extract/"
OUTPUT_DIR = "outputs"

# ============================================================
# ACQUISITION
# ============================================================

FETCH_WAIT_MS = 3000

# When True, a thin-content response from Firecrawl triggers one Playwright
# re-render attempt. Set to False for backend-comparison runs where you want
# each backend to stand alone without borrowing another.
THIN_CONTENT_FALLBACK = True

# ============================================================
# SELF-HOSTED FETCH POLITENESS (playwright_pooled backend)
# Firecrawl fetches from ITS infrastructure; the pooled-Playwright backend
# fetches from THIS machine's IP. Sagentia has had IPs blocked before, so
# politeness is mandatory for the self-hosted path, not tunable-to-zero
# in production runs.
# ============================================================

# Minimum seconds between requests to the same domain (all threads combined).
CRAWL_POLITE_DELAY_S = 2.0

# Respect robots.txt (disallowed pages are skipped with an explicit reason).
CRAWL_RESPECT_ROBOTS = True

# ============================================================
# LOCAL-FETCH QUALITY GATE
# Explicit pass/fail rule applied after httpx + Trafilatura extraction.
# A page that fails triggers a Playwright re-render (one attempt).
# Motivation: silently returning nav/footer junk instead of real content
# is the exact ScrapeGraphAI failure mode documented in Table 4.1.
# These constants make every failure visible rather than silent.
# ============================================================

# Minimum characters that Trafilatura must extract for a page to pass.
# Nav/footer-only pages rarely produce more than a few sentences once stripped.
QUALITY_MIN_CHARS = 200

# Maximum fraction of body text that may be anchor-link text.
# Above this threshold the page is likely a navigation listing or link directory.
QUALITY_MAX_LINK_DENSITY = 0.60

# Minimum fraction of full-page plain text that must survive Trafilatura filtering.
# Very low retention means almost all content was classified as boilerplate and stripped.
QUALITY_MIN_CONTENT_RATIO = 0.10

REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 entity-extraction-pipeline"
}

# ============================================================
# GUIDED CRAWLING
# ============================================================

CRAWL_ENABLED = False

# How many link hops away from the seed URL
CRAWL_MAX_DEPTH = 1

# Default depth used when a URL has no explicit depth outside the Excel urls sheet.
DEFAULT_DEPTH = 0

# Total pages allowed per entity
CRAWL_MAX_PAGES = 15

# Ignore links below this relevance score.
# BM25 scores are per-batch relative (0-1); 0.12 = must reach 12% of best link.
# Embed scores are absolute cosine; 0.50 blocks noise (~0.41-0.52) while keeping
# borderline-relevant links (~0.54-0.56). Raise toward 0.55 for higher precision.
CRAWL_MIN_SCORE = 0.12        # used by BM25 scorer
CRAWL_MIN_SCORE_EMBED = 0.50  # used by Ollama embedding scorer

# Maximum candidate links kept per page. Applied AFTER relevance scoring
# (top-N by score), not in DOM order — a footer About/locations link past the
# 30th anchor is no longer silently dropped before the scorer sees it.
CRAWL_MAX_LINKS_PER_PAGE = 30

# Collapse locale/language variants of the same page during link discovery
# (e.g. /fr.html, /de.html, /ko_kr.html, /de/de) so the crawl budget is not
# spent re-fetching translated copies of pages already visited. Generic
# pattern-based rule, no site list. Set False for before/after comparison runs.
CRAWL_LOCALE_DEDUP = True

# --- Relevance scorer ---

# Baseline preserves the current production scorer. Experimental is opt-in for
# diagnostics/evaluation and should not be promoted without evidence.
CRAWL_SCORER = "baseline"  # "baseline" | "experimental"

# Which scorer backend to use behind the _SCORERS dispatch.
# NOTE: confirm this name matches the key your _SCORERS dispatch reads.
SCORER_TOOL = "ollama"   # or "openai" if embeddings move off-network later

# Experimental crawl scorer weights. These are generic structural penalties,
# not brand/product/domain-specific rules.
EXPERIMENTAL_TITLE_WEIGHT = 0.25
EXPERIMENTAL_QUESTION_WEIGHT = 1.0
EXPERIMENTAL_INSTRUCTION_WEIGHT = 0.45
EXPERIMENTAL_STRUCTURAL_PENALTY_WEIGHT = 0.35
EXPERIMENTAL_MIN_SCORE_FLOOR = 0.0
EXPERIMENTAL_BOILERPLATE_TERMS = {
    "skip", "navigation", "menu", "search", "account", "login", "sign",
    "subscribe", "newsletter", "cookie", "privacy", "terms", "accessibility",
    "image", "logo", "icon", "button", "share", "close", "open",
}
EXPERIMENTAL_NAVIGATION_TERMS = {
    "shop", "cart", "checkout", "store", "locator", "recipe", "recipes",
    "collection", "collections", "category", "categories", "product",
    "products", "browse", "filter", "sort",
}

# Ollama embedding endpoint (internal server — only resolves on Sagentia network/VPN)
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://10.99.96.1:11434")
OLLAMA_EMBED_MODEL = "nomic-embed-text"
OLLAMA_TIMEOUT = 60       # generous: covers cold-start model load
OLLAMA_KEEP_ALIVE = "10m" # keep model resident between bursts of link scoring

# nomic-embed-text task prefixes for asymmetric retrieval (query vs document)
OLLAMA_QUERY_PREFIX = "search_query: "
OLLAMA_DOC_PREFIX = "search_document: "

# Reference texts for page-type scoring in score_links_embed().
# A link's type_score = cosine(link, INFORMATIONAL_REF) - cosine(link, TRANSACTIONAL_REF).
# PAGE_TYPE_ALPHA controls how much the type signal adjusts the topic score.
INFORMATIONAL_REF = "about us company information sustainability research reports press news corporate responsibility mission values environment science"
TRANSACTIONAL_REF = "shop buy products add to cart checkout account order collection store purchase browse"
PAGE_TYPE_ALPHA = 0.4

# ============================================================
# FILTER
# ============================================================

# "threshold" (default): embedding cosine + keyword gate as normal.
# "passthrough": every question is routed to every page regardless of score.
#   Scores are still computed and written to the Filter Log so the data
#   remains available for analysis — the mode only suppresses the routing
#   decision. Use this for extraction evaluations over hand-picked GT URLs
#   where every page is known-relevant and filtering can only lose recall.
# Env-overridable (set FILTER_MODE=passthrough in .env) so machine-local mode
# choices don't live as uncommitted config.py edits that block every git pull.
FILTER_MODE = os.getenv("FILTER_MODE", "threshold")

# When True (default), semantic-routing queries embed the column NAME plus its
# INSTRUCTION ("R&D location. In which country does the company conduct its
# R&D? ...") instead of the 2-3 word name alone. The instruction is a 30-50
# word discriminative probe; name-only queries barely discriminate on ADLM
# (score-vs-answered AUC 0.64 on the 2026-07-02 validation run). Applies to
# both the Filter (score_page_columns) and the crawler's baseline embed link
# scorer. Exists as a flag purely for before/after A-B comparison: set False
# to restore the old name-only queries.
QUERY_INCLUDES_INSTRUCTION = True

# Minimum cosine similarity between page text and a question for that column
# to be included in extraction. 0.35 keeps clearly irrelevant pages out while
# being lenient enough not to drop borderline-relevant content.
FILTER_THRESHOLD = 0.55

# Page text is split into ~this many characters per chunk (on paragraph
# boundaries where possible) before embedding; a column's page score is the
# max cosine across chunks.
FILTER_CHUNK_SIZE = 1000

# ============================================================
# VERIFICATION
# ============================================================

VERIFY_THRESHOLD = 70
VERIFY_THRESHOLD_SOFT = 68
VERIFY_LONG_QUOTE_MIN = 100

# ============================================================
# FUTURE EVALUATION SETTINGS
# ============================================================

ENABLE_COST_TRACKING = False
ENABLE_LATENCY_TRACKING = False

# Timeout for extractor SGAI calls (seconds). Lower to fail fast for unreliable endpoints.
EXTRACT_TIMEOUT = 120
EXTRACT_CHUNK_SIZE = 8000
EXTRACT_CHUNK_OVERLAP = 200
EXTRACT_MAX_WORKERS = 8
EXTRACT_PAGE_WORKERS = 4

# Bound extraction cost on pathological pages. HORIBA's /usa/company/news is a
# 735 KB news archive -> 95 chunks -> 95 LLM calls -> 654 claims in one cell
# (2026-07-02 validation). 40 chunks (~312 KB) covers every normal page — the
# plant-milk maximum (Oatly report, 113 KB) is 15 chunks, so the locked
# benchmark is unaffected. Truncation is printed, never silent; archive pages
# list newest items first, so the kept prefix is the "recent" part anyway.
EXTRACT_MAX_CHUNKS_PER_PAGE = 40

# ============================================================
# PIPELINE CONCURRENCY
# ============================================================

# URL specs processed concurrently (one spec per entity in ADLM-style
# workbooks, so each worker crawls a different domain — per-domain request
# rate is unchanged and politeness is preserved by construction).
# Ceilings to respect when raising: Firecrawl plan concurrency and the
# Power Automate LLMAPI throughput.
PIPELINE_ENTITY_WORKERS = 4

# Global cap on concurrent extractor LLM calls across all entities, pages and
# chunks. Without it, worst case is PIPELINE_ENTITY_WORKERS x
# EXTRACT_PAGE_WORKERS x EXTRACT_MAX_WORKERS (4 x 4 x 8 = 128) simultaneous
# calls — the LLMAPI proxy already returned a 502 under single-entity load.
EXTRACT_MAX_CONCURRENT_CALLS = 16

ENABLE_PROVENANCE = True

DIAGNOSTICS = True  # True = all 7 sheets; False = Summary, Matrix, Provenance only

# Cap on bullet items rendered in one Matrix cell. Every item is still in
# Provenance; overflow is marked "[+N more items — see Provenance]" so nothing
# is hidden silently. Excel's hard cell limit (32,767 chars) is additionally
# enforced with an explicit truncation marker in io_excel.
MATRIX_MAX_DISPLAY_ITEMS = 50

# ============================================================
# GROUPING (deterministic themes sheet)
# ============================================================

# Cluster the claims inside each aggregated Matrix cell into themes and write
# them as a "Grouped Themes" sheet. Deterministic (embeddings + fixed-threshold
# greedy clustering, no LLM); the Matrix/Provenance chain is untouched. Fails
# soft: if Ollama is unreachable the sheet is simply absent, the run unaffected.
GROUPING_ENABLED = True

# Cells with fewer distinct values than this aren't worth clustering — they
# are emitted as a single "(all items)" group without any embedding call.
GROUP_MIN_ITEMS = 6

# Per-cell mean-centering before clustering (anisotropy correction). The
# 2026-07-03 calibration on real validation claims showed RAW nomic cosines
# compress into one giant cluster at any threshold <= 0.70 (all claims from
# one company share a dominant company/domain component); centering removes
# that shared component so only what distinguishes claims within the cell
# drives similarity. Deterministic. Set False only for raw-vs-centered
# comparison via diagnostics/group_calibration.py.
GROUP_CENTER_VECTORS = True

# Centroid-cosine threshold for joining an existing cluster. Applies in the
# CENTERED space (GROUP_CENTER_VECTORS=True). Calibrated 2026-07-03 on the
# five biggest validation cells (65-862 claims): 0.15 puts every cell in the
# scannable 6-19 theme range (HORIBA 862 news items -> 19 themes; the
# provisional 0.30 fragmented it into 93). 0.10 is the tighter alternative
# (5-12 themes) if themes read as over-split.
GROUP_SIMILARITY = 0.15

# ============================================================
# LLM SUMMARY LAYER (AI Summary sheet)
# ============================================================

# Synthesized prose per grouped cell (brain/proposals/llm-summary-layer.md,
# approved 2026-07-07): Azure GPT-4.1-mini over the grouped-theme structure,
# every sentence citing Provenance claim IDs. OFF by default — flips to True
# in a client-facing config only after the pre-registered faithfulness bar
# passes (judge >=0.90 on the corruption set, >=0.80 agreement with human
# labels, >=0.90 self-agreement). Fails soft: any Azure failure only skips
# the AI Summary sheet; every other sheet is byte-identical either way.
# Env-overridable (SUMMARY_ENABLED=true in .env) for the same reason as
# FILTER_MODE: machine-local mode choices must not live as uncommitted
# config.py edits that block every git pull.
SUMMARY_ENABLED = os.getenv("SUMMARY_ENABLED", "").strip().lower() in {"1", "true", "yes"}

# temperature=0 + this fixed seed on every summarizer/judge call reduces
# non-determinism at source (seed honoured on this deployment — probe
# 2026-07-07, identical outputs + stable system_fingerprint). Best-effort per
# OpenAI docs, so each call's fingerprint is recorded in the Summary Log.
SUMMARY_SEED = 42

# Max member claims listed per theme in the prompt. Truncation is principled:
# drop members WITHIN a theme (marked "+N more"), never whole themes.
SUMMARY_MAX_CLAIMS_PER_THEME = 15

# Per-call timeout (seconds). The OpenAI SDK adds its own retries on
# connection errors / 408 / 429 / 5xx (default max_retries=2), so no
# hand-rolled retry here.
SUMMARY_TIMEOUT = 60

# Concurrent summarizer calls. Size against the deployment's TPM/RPM quota
# (work-laptop checklist item 4); extraction runs share the deployment.
SUMMARY_MAX_CONCURRENT_CALLS = 4

# ============================================================
# AGGREGATION DIAGNOSTICS
# ============================================================

AGGREGATION_NEAR_DUPLICATE_THRESHOLD = 85
AGGREGATION_LOW_RELEVANCE_THRESHOLD = 35
# Generic diagnostic defaults only. Do not add domain, entity, product, or
# question-specific terms here; pass those per run through diagnostic config/CLI.
AGGREGATION_BOILERPLATE_TERMS = {
    "the", "and", "or", "of", "in", "to", "for", "with", "on", "at", "by",
    "a", "an", "is", "are", "be", "this", "that", "it", "its",
}
