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

# ============================================================
# TOOLS
# ============================================================

# Dev default.  Change to "firecrawl" (or "sgai"/"playwright"/"requests") for
# deployment — Firecrawl is the deployment-default candidate.
FETCH_BACKEND = "firecrawl"

ACQUIRE_TOOL = FETCH_BACKEND
EXTRACT_TOOL = "azure"
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

# Maximum candidate links extracted from a page
CRAWL_MAX_LINKS_PER_PAGE = 30

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
FILTER_MODE = "threshold"

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

ENABLE_PROVENANCE = True

DIAGNOSTICS = True  # True = all 7 sheets; False = Summary, Matrix, Provenance only

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
