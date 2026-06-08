import os
from dotenv import load_dotenv

load_dotenv()

# ============================================================
# API
# ============================================================

API_KEY = os.getenv("SGAI_API_KEY")

# ============================================================
# TOOLS
# ============================================================

ACQUIRE_TOOL = "firecrawl"
EXTRACT_TOOL = "sgai"
VERIFY_TOOL = "rapidfuzz"

# ============================================================
# PATHS
# ============================================================

CACHE_DIR = "cache"
OUTPUT_DIR = "outputs"

# ============================================================
# ACQUISITION
# ============================================================

FETCH_WAIT_MS = 3000

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
CRAWL_MAX_PAGES = 2

# Ignore links below this relevance score
CRAWL_MIN_SCORE = 0.12

# Maximum candidate links extracted from a page
CRAWL_MAX_LINKS_PER_PAGE = 30

# --- Relevance scorer ---

# Which scorer backend to use behind the _SCORERS dispatch.
# NOTE: confirm this name matches the key your _SCORERS dispatch reads.
SCORER_TOOL = "ollama"   # or "openai" if embeddings move off-network later

# Ollama embedding endpoint (internal server — only resolves on Sagentia network/VPN)
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://10.99.96.1:11434")
OLLAMA_EMBED_MODEL = "nomic-embed-text"
OLLAMA_TIMEOUT = 60       # generous: covers cold-start model load
OLLAMA_KEEP_ALIVE = "10m" # keep model resident between bursts of link scoring

# nomic-embed-text task prefixes for asymmetric retrieval (query vs document)
OLLAMA_QUERY_PREFIX = "search_query: "
OLLAMA_DOC_PREFIX = "search_document: "

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

ENABLE_PROVENANCE = True

DIAGNOSTICS = True  # True = all 7 sheets; False = Summary, Matrix, Provenance only
