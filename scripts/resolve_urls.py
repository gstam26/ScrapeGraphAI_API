"""CLI: resolve company names to official URLs.

Usage (from repo root):
    python scripts/resolve_urls.py input.csv
    python scripts/resolve_urls.py input.csv -o resolved_urls.csv
    python scripts/resolve_urls.py input.csv --fetch           # opt-in homepage fetch
    python scripts/resolve_urls.py input.csv --limit 10 --embeddings

Input CSV columns : company, booth, description, categories
Output CSV columns: company, resolved_url, confidence,
                    candidate_alternatives, needs_review, notes

This is a standalone tool. It reuses the project's authorised Firecrawl access
and the acquire-layer fetcher, but does not touch the extraction pipeline.
Search runs exclusively through Firecrawl's server-side search endpoint —
there is no direct-internet fallback.
"""

import argparse
import sys
from pathlib import Path

# Lives in scripts/; make repo-root imports (src.*, models, config) resolvable.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.resolve.resolver import resolve_csv


def _build_cfg(backend: str):
    """Build a minimal acquire Config for optional homepage fetching, reading
    project defaults from config.py. Returns None if config import fails."""
    try:
        from models import Config
        from config import ACQUIRE_TOOL, CACHE_DIR, REQUEST_HEADERS

        return Config(
            acquire_tool=backend or ACQUIRE_TOOL,
            cache_dir=CACHE_DIR,
            request_headers=REQUEST_HEADERS,
        )
    except Exception as e:  # pragma: no cover - defensive
        print(f"  (warning: could not build fetch config: {e}; running search-only)")
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Resolve company names to official URLs.")
    parser.add_argument("input", help="Path to input CSV (company,booth,description,categories)")
    parser.add_argument("-o", "--output", default="resolved_urls.csv", help="Output CSV path")
    parser.add_argument("--limit", type=int, default=8, help="Search results per company")
    parser.add_argument(
        "--backend", default="", help="Override acquire fetch backend (firecrawl/local/requests/playwright)"
    )
    parser.add_argument(
        "--fetch", action="store_true", help="Fetch and score homepages (slower; search-only is the default)"
    )
    parser.add_argument(
        "--embeddings", action="store_true",
        help="Enable the optional Ollama embedding boost (Sagentia-VPN-only; ignored if unreachable)",
    )
    args = parser.parse_args()

    cfg = _build_cfg(args.backend) if args.fetch else None

    print(f"Resolving companies from {args.input} -> {args.output}")
    results = resolve_csv(
        args.input,
        args.output,
        cfg=cfg,
        limit=args.limit,
        fetch_homepages=args.fetch,
        use_embeddings=args.embeddings,
    )

    reviewed = sum(1 for r in results if r.needs_review)
    print(
        f"Done. {len(results)} companies; "
        f"{len(results) - reviewed} confident, {reviewed} need review. "
        f"Written to {args.output}"
    )


if __name__ == "__main__":
    main()
