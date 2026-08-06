"""Runtime configuration for the entity-extraction pipeline.

Every tunable lives here, grouped by pipeline stage (Acquire → Filter →
Extract → Verify → Aggregate → Group → Summarize), so behaviour changes are
config edits, not code edits. Secrets and machine-local mode choices come
from the environment via .env (python-dotenv); a handful of flags are
env-overridable so per-machine settings never live as uncommitted edits to
this file. The input workbook's config sheet can override a further subset
per run (default < env < workbook — see pipeline._build_config).

Section index (in file order, matching the `# =====` banners below):

    API                             LLM / vendor credentials and endpoints
    TOOLS                           which backend serves each pipeline stage
    PATHS                           cache and output directories
    ACQUISITION                     fetch-level fallback policy
    SELF-HOSTED FETCH POLITENESS    robots.txt, per-domain delay, render budget
    LOCAL-FETCH QUALITY GATE        pass/fail rule for statically fetched pages
    GUIDED CRAWLING                 depth/page budgets, link scoring, path policy
    FILTER                          question-to-page routing thresholds
    VERIFICATION                    quote-matching thresholds
    EXTRACTION                      chunking, determinism, per-page cost caps
    PIPELINE CONCURRENCY            worker pools and global LLM call ceiling
    GROUPING                        deterministic themes sheet
    LLM SUMMARY LAYER               AI Summary sheet (off by default)
    AGGREGATION                     cross-source merge behaviour
    AGGREGATION DIAGNOSTICS         thresholds for the aggregation diagnostics sheet
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ============================================================
# API
# ============================================================

API_KEY = os.getenv("SGAI_API_KEY")

AZURE_API_KEY = os.getenv("AZURE_API_KEY")
# The real endpoint is deployment-specific and comes from .env (AZURE_ENDPOINT);
# the default below is a placeholder and will not resolve as-is.
AZURE_ENDPOINT = os.getenv("AZURE_ENDPOINT", "https://<your-resource>.openai.azure.com/openai/v1")
AZURE_DEPLOYMENT = os.getenv("AZURE_DEPLOYMENT", "gpt-4.1-mini")

CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")

# ============================================================
# TOOLS
# ============================================================

# Deployment default (Firecrawl credits cover only ~74 of 178 companies in a
# full run).
# Static-first hybrid: httpx + Trafilatura, escalating
# to the pooled browser only on quality-gate failure; politeness (robots.txt,
# per-domain delay, honest UA) on both paths. Alternatives: "firecrawl" |
# "playwright_pooled" | "sgai" | "playwright" | "requests" | "local".
FETCH_BACKEND = "playwright_pooled_hybrid"

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

# When True, a thin-content response from Firecrawl triggers one Playwright
# re-render attempt. Set to False for backend-comparison runs where you want
# each backend to stand alone without borrowing another.
THIN_CONTENT_FALLBACK = True

# ============================================================
# SELF-HOSTED FETCH POLITENESS (playwright_pooled + playwright_pooled_hybrid)
# Firecrawl fetches from ITS infrastructure; the pooled backends fetch from
# THIS machine's IP. Our egress IPs have been blocked by target sites before,
# so politeness is mandatory for the self-hosted path, not tunable-to-zero in
# production runs.
# The hybrid's static httpx path goes through the same robots.txt check and
# per-domain delay as the browser path.
# ============================================================

# Minimum seconds between requests to the same domain (all threads combined).
CRAWL_POLITE_DELAY_S = 2.0

# Respect robots.txt (disallowed pages are skipped with an explicit reason).
CRAWL_RESPECT_ROBOTS = True

# --- Headless-render wait budget (pooled render + hybrid render escalation) ---
# Navigation stops at domcontentloaded, then a flat paint settle. A networkidle
# wait was trialled to recover content on JS-heavy pages but was
# DISCONFIRMED on the site that motivated it: the gate-failing pages there are
# genuine link-grid category pages (link_density 0.70+), not
# hydration-starved SPAs — rendering
# longer recovered no prose and added ~5 s/page. Tunable here without a code
# change; re-add a bounded networkidle wait only with evidence of a real
# timing-bound site.
RENDER_GOTO_TIMEOUT_MS = 15000   # navigation (page.goto) ceiling
RENDER_SETTLE_MS = 2000          # paint settle after load

# The hybrid's static httpx probe is only a fast-path check before escalating to
# the browser, so it uses a shorter timeout than the full request_timeout: a
# slow/hanging static response should escalate to render quickly, not burn the
# full 30 s (observed on one slow site: a 52 s fetch = 30 s dead static wait
# + render; with a 12 s cap the same page succeeds on static in 7 s).
HYBRID_STATIC_TIMEOUT_S = 12

# ============================================================
# LOCAL-FETCH QUALITY GATE
# Explicit pass/fail rule applied after httpx + Trafilatura extraction.
# A page that fails triggers a Playwright re-render (one attempt).
# Motivation: silently returning nav/footer junk instead of real content
# is the exact ScrapeGraphAI failure mode documented in earlier evaluation.
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

# Full-page rescue: when Trafilatura's extraction fails the gate
# but the page's FULL visible text is between QUALITY_MIN_CHARS and this cap,
# ship the full text instead of the husk. Short link-grid "info card" pages
# (locations / contact / leadership / about) carry their facts in link labels
# and address blocks that Trafilatura strips — observed on a smoke run: one
# site's /locations page extracted 837 husk chars -> 0 items while the HQ
# address sat in the DOM. Above the cap, a Trafilatura-failed page is a real
# link farm (the gate's original prey) and stays failed.
FULL_PAGE_RESCUE_MAX_CHARS = 12_000

REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 entity-extraction-pipeline"
}

# ============================================================
# GUIDED CRAWLING
# ============================================================

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

# Frontier discipline for the guided crawler.
# "bfs" (default, all validation to date): explore in depth waves — every
#   chosen depth-1 link before any depth-2 link. When the page budget binds
#   before the frontier exhausts, BFS spends the whole budget on shallow
#   breadth BY CONSTRUCTION (measured on a 5-entity client run: 3/5 entities pinned
#   at budget with depth-3+ links discovered but never dequeued, while
#   depth-2 pages carried 2.4x the extracted claims of depth-1).
# "best_first": pop the highest-scoring queued link regardless of depth —
#   the budget flows to the most answer-relevant pages and depth becomes an
#   outcome rather than a knob (max_depth remains a discovery cutoff).
#   Amplifies the link scorer in both directions: weak queries (questions
#   without instructions) give fuzzy priorities; instruction-aware queries
#   should target well. A/B before adopting anywhere validated.
# Env-overridable so comparison runs don't need config edits.
CRAWL_STRATEGY = os.getenv("CRAWL_STRATEGY", "bfs")  # "bfs" | "best_first"

# Collapse locale/language variants of the same page during link discovery
# (e.g. /fr.html, /de.html, /ko_kr.html, /de/de) so the crawl budget is not
# spent re-fetching translated copies of pages already visited. Generic
# pattern-based rule, no site list. Set False for before/after comparison runs.
CRAWL_LOCALE_DEDUP = True

# --- Link-discovery starvation fixes (diagnosed on two starved seeds) ---

# Crawl scope: "host" keeps the historical behaviour (candidate must share the
# seed's exact host, www-stripped). "site" widens to the registered domain, so
# a hub homepage that delegates content to its own subdomains stays crawlable
# (example.com -> engineering.example.com / asia.example.com; 46 of one seed's
# 56 homepage links were own-subdomain and ALL were discarded under "host" —
# 2 pages fetched, 9 GT misses). Off by default pending validation: widening
# scope changes crawl behaviour, so the locked benchmark and a full client run
# must be re-validated before this becomes the default.
CRAWL_SCOPE = os.getenv("CRAWL_SCOPE", "host")  # "host" | "site"

# Render-for-discovery: the hybrid fetcher renders only when the static fetch
# fails the quality gate — so a seed whose TEXT is fine but whose nav menu is
# JS-injected (one seed printed its whole menu from a header .js file) passes
# the gate and the crawl never sees the menu (3 pages fetched, 8 GT misses).
# When enabled, a statically-fetched SEED page whose link discovery yields
# fewer than CRAWL_DISCOVERY_MIN_LINKS candidates is rendered once and
# discovery re-runs on the rendered DOM (union). Depth-0 only: one render per
# starved seed, politeness handled by the pooled renderer's per-domain gate.
CRAWL_RENDER_FOR_DISCOVERY = os.getenv("CRAWL_RENDER_FOR_DISCOVERY", "false").lower() == "true"
CRAWL_DISCOVERY_MIN_LINKS = 3

# --- Boilerplate paths: classify and CAP, do not block ----------------------
#
# The original plan was a crawl-time blocklist for legal/compliance paths, on the
# stated assumption of "near-zero recall risk". THAT ASSUMPTION IS REFUTED.
# On the very run that motivated the work, one entity's HQ answer — a verified
# city/country, available from no other page the crawl reached — came out of
# that site's /company/en/privacypolicy page. Legal pages carry the registered
# company address BY LAW (GDPR Art. 13 requires the controller's identity and
# contact details; a German Impressum is nothing but that address). For HQ,
# legal-entity and registered-address questions they are often the single most
# reliable page on a site. Blocking them would have deleted a correct answer.
#
# The real cost problem is narrower and is about SIZE, not category: the worst
# observed privacy policy is 234K chars = 30 chunks = 30 Azure extract calls of
# pure legalese. So boilerplate paths are CLASSIFIED here and used two ways:
#   - EXTRACT_MAX_CHUNKS_BOILERPLATE caps their extraction cost (below), which
#     captures that 30-call saving while keeping the identity block — which sits
#     early by legal convention (the HQ address above was at char 3,439 of 4,193).
#   - they are NOT dropped from the crawl.
# Only genuine infrastructure is blocked outright (BLOCKED_PATH_PATTERNS).
#
# This does NOT reopen the earlier rejection of a URL blocklist. That
# rejection was about TOPICAL paths (/products/ is boilerplate for a
# consumer-brand site and core disclosure for a pharma company) — domain
# knowledge that does not generalise, correctly left to the embedding
# page-type signal. Topical
# segments must never be added to either list here.
#
# Kept as data, not logic (the INFORMATIONAL_REF/TRANSACTIONAL_REF precedent).
# Patterns are fullmatched against individual path SEGMENTS with any page
# extension stripped, so /privacy-first-manufacturing and
# /news/cookie-factory-opens are untouched while /privacypolicy and
# /en/cookie-policy.html classify.
BOILERPLATE_PATH_PATTERNS = (
    r"privacy([-_]?(policy|policies|notice|notices|statement))?",
    r"cookies?([-_]?(policy|policies|notice|settings|preferences|consent))?",
    r"terms([-_]?(of[-_]?(use|service|sale|business|purchase)"
    r"|and[-_]?conditions|conditions))?",
    r"tos",
    r"legal([-_]?(notice|notices|information|disclaimer|mentions))?",
    r"disclaimer",
    r"imprint",
    r"impressum",
    r"mentions[-_]?legales",
    r"agb",
    r"datenschutz(erklaerung|erklarung|erklärung)?",
    r"data[-_]?protection",
    r"gdpr",
    r"ccpa",
    r"accessibility([-_]?statement)?",
)

# Outright-blocked at link discovery: infrastructure endpoints and templating
# artefacts that are not pages at all. Deliberately NOT here: modern-slavery
# and code-of-conduct statements (they name supply-chain and manufacturing
# COUNTRIES — a live GT question), sitemaps (link-rich, aid discovery),
# careers/news/investors (low-yield but legitimate company facts).
CRAWL_BLOCK_INFRA_PATHS = os.getenv(
    "CRAWL_BLOCK_INFRA_PATHS", "false").lower() == "true"

BLOCKED_PATH_PATTERNS = (
    r"cdn-cgi",                 # Cloudflare endpoints (email-protection, /l/…)
    r"wp-json",                 # WordPress REST API
    r"xmlrpc",
    r"\{\{.*\}\}",              # unrendered template placeholders
)

# Boilerplate pages are capped to this many extract chunks. 1 keeps the legally
# mandated identity block (which convention puts early) and drops the
# processing/rights/retention legalese behind it: 30 calls -> 1 on the worst
# observed policy page.
# Trade-off recorded: a fact sitting late in a very long legal page is lost.
EXTRACT_MAX_CHUNKS_BOILERPLATE = 1

# --- Single-answer display discipline ---------------------------------------
#
# A single-answer question gets ONE lead answer in the Matrix. The flags-on
# 69-run showed the pattern at scale: fed entities answer more and every
# co-displayed candidate on a single-answer cell ("Hong Kong; Dongguan,
# China" for one HQ) reads as a competing claim. The Matrix leads with the
# best-attested candidate (verified first, then by how many independent
# evidence items back it) and points at Provenance for the rest:
# "[+2 other candidate(s) — see Provenance]". The conflict flag and orange
# fill REMAIN — the consultant still sees that sources disagreed; the
# deliverable just answers the question first. Render-time only: Provenance,
# Grouped Themes and the evidence trail keep every candidate.
#
# Default on: an A/B test on real renders (gt42, matrix+prose) showed
# P 0.492->0.572, F1 0.545->0.577, hallucination 0.508->0.428, at R -0.029
# (24 lead-pick errors, the known residual class). Display-incoherence cells
# ("Yes" + "Not disclosed" stacked) suppressed-null count 187->88. Set to
# "false" to reproduce the previous multi-candidate renders.
MATRIX_SINGLE_BEST = os.getenv("MATRIX_SINGLE_BEST", "true").lower() == "true"

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

# Ollama embedding endpoint (internal server — only resolves on the corporate
# network/VPN; override with OLLAMA_HOST in .env)
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
# word discriminative probe; name-only queries barely discriminate on a full
# client run
# (score-vs-answered AUC 0.64 on a validation run). Applies to
# both the Filter (score_page_columns) and the crawler's baseline embed link
# scorer. Exists as a flag purely for before/after A-B comparison: set False
# to restore the old name-only queries.
QUERY_INCLUDES_INSTRUCTION = True

# Minimum cosine similarity between page text and a question for that column
# to be included in extraction.
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
# EXTRACTION
# ============================================================

# Timeout for extractor SGAI calls (seconds). Lower to fail fast for unreliable endpoints.
EXTRACT_TIMEOUT = 120
EXTRACT_CHUNK_SIZE = 8000
EXTRACT_CHUNK_OVERLAP = 200

# Extraction determinism: the Azure extractor previously sent no
# temperature/seed and ran at the API default (temperature 1.0) — identical
# input produced 5 vs 10 items across two client smoke runs
# (same services page, byte-identical text). The summary layer has run
# temperature=0 + seed since birth; extraction now matches it. Same
# reduced-nondeterminism caveat as SUMMARY_SEED: a best-effort determinism
# hint, not a guarantee (Azure fingerprint changes still vary output).
EXTRACT_TEMPERATURE = 0.0
EXTRACT_SEED = 42
EXTRACT_MAX_WORKERS = 8
EXTRACT_PAGE_WORKERS = 4

# Bound extraction cost on pathological pages. One site's /company/news page is
# a 735 KB news archive -> 95 chunks -> 95 LLM calls -> 654 claims in one cell
# (validation run). 40 chunks (~312 KB) covers every normal page — the largest
# benchmark report (~113 KB) is 15 chunks, so the locked
# benchmark is unaffected. Truncation is printed, never silent; archive pages
# list newest items first, so the kept prefix is the "recent" part anyway.
EXTRACT_MAX_CHUNKS_PER_PAGE = 40

# ============================================================
# PIPELINE CONCURRENCY
# ============================================================

# URL specs processed concurrently (one spec per entity in typical
# multi-entity workbooks, so each worker crawls a different domain —
# per-domain request rate is unchanged and politeness is preserved by
# construction).
# Ceilings to respect when raising: Firecrawl plan concurrency and the
# Power Automate LLMAPI throughput.
PIPELINE_ENTITY_WORKERS = 4

# Global cap on concurrent extractor LLM calls across all entities, pages and
# chunks. Without it, worst case is PIPELINE_ENTITY_WORKERS x
# EXTRACT_PAGE_WORKERS x EXTRACT_MAX_WORKERS (4 x 4 x 8 = 128) simultaneous
# calls — the LLMAPI proxy already returned a 502 under single-entity load.
EXTRACT_MAX_CONCURRENT_CALLS = 16

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

# Per-cell mean-centering before clustering (anisotropy correction).
# Calibration on real validation claims showed RAW nomic cosines
# compress into one giant cluster at any threshold <= 0.70 (all claims from
# one company share a dominant company/domain component); centering removes
# that shared component so only what distinguishes claims within the cell
# drives similarity. Deterministic. Set False only for raw-vs-centered
# comparison via diagnostics/group_calibration.py.
GROUP_CENTER_VECTORS = True

# Centroid-cosine threshold for joining an existing cluster. Applies in the
# CENTERED space (GROUP_CENTER_VECTORS=True). Calibrated on the
# five biggest validation cells (65-862 claims): 0.15 puts every cell in the
# scannable 6-19 theme range (the largest cell, 862 news items -> 19 themes; the
# provisional 0.30 fragmented it into 93). 0.10 is the tighter alternative
# (5-12 themes) if themes read as over-split.
GROUP_SIMILARITY = 0.15

# ============================================================
# LLM SUMMARY LAYER (AI Summary sheet)
# ============================================================

# Synthesized prose per grouped cell:
# Azure GPT-4.1-mini over the grouped-theme structure,
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
# non-determinism at source (seed honoured on this deployment — a probe
# produced identical outputs + a stable system_fingerprint). Best-effort per
# OpenAI docs, so each call's fingerprint is recorded in the Summary Log.
SUMMARY_SEED = 42

# Max member claims listed per theme in the prompt. Truncation is principled:
# drop members WITHIN a theme (marked "+N more"), never whole themes.
SUMMARY_MAX_CLAIMS_PER_THEME = 15

# Compact analyst format: cited lines, "topic: synthesis [cites]". Cap on
# items listed per line — themes with more end the list with a visible
# "(more in Provenance)" marker, nothing dropped silently.
SUMMARY_MAX_ITEMS_PER_LINE = 8

# Hard ceiling on lines per cell. "One line per theme" alone imposes no
# cell-level cap — some Description cells have 11 themes, so the "compact"
# summary became an 11-line wall. Three lines covers the
# top-3 themes the Tier-1 coverage gate checks; omitted themes are marked
# "(more in Provenance)".
SUMMARY_MAX_LINES_PER_CELL = 3

# Cells whose citable values are ALL no longer than this render
# deterministically with no LLM call (src/summarize.py deterministic_answer,
# generalized from the original single-tag route): bare yes/no/true/
# false claims collapse to one cited verdict ("Yes [C0046, C0089]"), every
# other short value renders verbatim ("MIL-STD 810 testing [C0037]"),
# '; '-joined and capped at SUMMARY_MAX_ITEMS_PER_LINE. Compact by
# construction, zero faithfulness risk; the LLM only sees cells with
# prose-length claims, where synthesis earns its risk.
SUMMARY_TAG_MAX_CHARS = 80

# Per-call timeout (seconds). The OpenAI SDK adds its own retries on
# connection errors / 408 / 429 / 5xx (default max_retries=2), so no
# hand-rolled retry here.
SUMMARY_TIMEOUT = 60

# Concurrent summarizer calls. Size against the deployment's TPM/RPM quota;
# extraction runs share the deployment.
SUMMARY_MAX_CONCURRENT_CALLS = 4

# ============================================================
# AGGREGATION
# ============================================================

# Question/column names whose values are comma-separated LISTS of items rather
# than competing answers to one question (e.g. certifications, product types,
# markets served). For a column named here, src.aggregate splits every
# surviving value on commas and merges the items into ONE canonical
# comma-joined string, so partial lists contributed by different source pages
# become a single union instead of several rival bullets. This catches subset
# strings that the fuzzy near-duplicate pass cannot collapse ("a, b" vs
# "a, c, b, d, e" scores ~50, well below aggregate._DEDUP_RATIO).
#
# EMPTY BY DEFAULT — the union pass is inert unless a task opts in, because the
# right column names are task-specific. Set it per task (exact column names as
# they appear in the input workbook's questions sheet), e.g.
#   UNION_LIST_COLUMNS = frozenset({"Certifications", "Markets served"})
UNION_LIST_COLUMNS: frozenset[str] = frozenset()

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
