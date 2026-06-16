"""
Azure OpenAI connectivity and extraction diagnostic.

Runs two checks:
  1. Basic connectivity — sends a trivial prompt and prints the response.
  2. Mock extraction — runs _extract_with_azure() on a small synthetic page
     with one entity and one column to verify the full JSON-parsing path.

Usage:
    python diagnostics/azure_test.py
    python diagnostics/azure_test.py --skip-extract

Requires:
    AZURE_API_KEY in .env
"""

import argparse
import os
import sys
import time

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from dotenv import load_dotenv
load_dotenv()

from config import AZURE_API_KEY, AZURE_ENDPOINT, AZURE_DEPLOYMENT


def check_connectivity() -> bool:
    from openai import OpenAI

    print(f"  endpoint   : {AZURE_ENDPOINT}")
    print(f"  deployment : {AZURE_DEPLOYMENT}")
    print(f"  api_key    : {'set' if AZURE_API_KEY else 'MISSING'}\n")

    if not AZURE_API_KEY:
        print("  ERROR: AZURE_API_KEY is not set in .env")
        return False

    print("  [1/2] Basic connectivity test...")
    t0 = time.time()
    try:
        client = OpenAI(base_url=AZURE_ENDPOINT, api_key=AZURE_API_KEY)
        completion = client.chat.completions.create(
            model=AZURE_DEPLOYMENT,
            messages=[{"role": "user", "content": "What is the capital of France? Reply in one word."}],
            timeout=30,
        )
        elapsed = time.time() - t0
        answer = completion.choices[0].message.content or ""
        print(f"  OK ({elapsed:.2f}s) — response: {answer.strip()}\n")
        return True
    except Exception as e:
        elapsed = time.time() - t0
        print(f"  FAILED ({elapsed:.2f}s): {e}\n")
        return False


def check_extraction() -> bool:
    from models import ColumnSpec, PageDoc
    from src.extract import _extract_with_azure

    print("  [2/2] Mock extraction test...")

    page = PageDoc(
        url="https://example.com/test",
        text=(
            "Acme Corp was founded in 2005 and is headquartered in London. "
            "The company employs approximately 1,200 people worldwide."
        ),
        html=None,
        from_cache=False,
        depth=0,
        crawl_score=0.0,
        fetch_time_ms=0,
        backend="test",
        render_fallback=False,
        gate_passed=True,
        gate_reason="test",
    )
    columns = [ColumnSpec(name="Headquarters", instruction="Where is the company headquartered?")]
    entities = ["Acme Corp"]

    t0 = time.time()
    try:
        data, timing = _extract_with_azure(page, columns, entities)
        elapsed = time.time() - t0
    except Exception as e:
        elapsed = time.time() - t0
        print(f"  FAILED ({elapsed:.2f}s): {e}\n")
        return False

    if not data:
        print(f"  WARNING ({elapsed:.2f}s): extraction returned empty dict — check prompt/response above\n")
        return False

    print(f"  OK ({elapsed:.2f}s)")
    print(f"  raw output: {data}\n")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Azure OpenAI diagnostic")
    parser.add_argument("--skip-extract", action="store_true", help="Only run connectivity check")
    args = parser.parse_args()

    print(f"\n{'='*60}")
    print("  AZURE DIAGNOSTIC")
    print(f"{'='*60}\n")

    ok = check_connectivity()
    if not ok:
        sys.exit(1)

    if not args.skip_extract:
        ok = check_extraction()
        if not ok:
            sys.exit(1)

    print("  All checks passed.")


if __name__ == "__main__":
    main()
