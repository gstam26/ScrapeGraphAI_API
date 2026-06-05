import os
from dotenv import load_dotenv

load_dotenv()

# ============================================================
# API
# ============================================================

API_KEY = os.getenv("SGAI_API_KEY")

if not API_KEY:
    raise ValueError("Missing SGAI_API_KEY in .env")

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

# Total pages allowed per entity
CRAWL_MAX_PAGES = 2

# Ignore links below this relevance score
CRAWL_MIN_SCORE = 0.12

# Maximum candidate links extracted from a page
CRAWL_MAX_LINKS_PER_PAGE = 30

# Generic fallback terms used by the crawl planner
CRAWL_FALLBACK_TERMS = [
    "about",
    "company",
    "overview",
    "story",
    "mission",
    "products",
    "services",
]

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