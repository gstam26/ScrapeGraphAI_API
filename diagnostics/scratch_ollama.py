"""
scratch_ollama.py — standalone diagnostic for the Ollama nomic-embed-text endpoint.

NOT part of the pipeline. Run it directly to confirm the embedding server works
BEFORE wiring nomic-embed-text behind the _SCORERS dispatch.

Usage:
    1. Edit OLLAMA_HOST below to point at your server.
    2. python scratch_ollama.py

Each check narrows the failure: reachability -> client -> vector shape ->
similarity behaviour -> prefix effect. If a check fails, the ones below it
are skipped, so the first failure line tells you where the problem is.
"""

import sys
import json
import math
import urllib.request
import urllib.error

# ---------------------------------------------------------------------------
# EDIT THIS: your server address (the one you were given).
OLLAMA_HOST = "http://10.99.96.1:11434"
MODEL = "nomic-embed-text"
TIMEOUT = 60          # generous: first call loads the model into memory
EXPECTED_DIM = 768    # nomic-embed-text returns 768-dim vectors
# ---------------------------------------------------------------------------


def _post_embedding(text, host=OLLAMA_HOST, timeout=TIMEOUT):
    """Call /api/embeddings via stdlib only (no deps needed to diagnose)."""
    url = f"{host.rstrip('/')}/api/embeddings"
    payload = json.dumps({"model": MODEL, "prompt": text}).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return body["embedding"]


def _cos(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def check_1_reachable():
    print("\n[1] Reachability + cold-start ------------------------------------")
    print(f"    host: {OLLAMA_HOST}")
    try:
        import time
        t0 = time.time()
        _ = _post_embedding("hello")
        t1 = time.time()
        _ = _post_embedding("hello again")  # second call: model already loaded
        t2 = time.time()
        print(f"    OK. first call {t1 - t0:.1f}s (incl. model load), "
              f"second {t2 - t1:.1f}s")
        if (t1 - t0) > 10 and (t2 - t1) < (t1 - t0) / 2:
            print("    note: slow first call is the model loading — expected. "
                  "Set keep_alive if calls are bursty.")
        return True
    except urllib.error.URLError as e:
        print(f"    FAIL (network/host): {e}")
        print("    -> server unreachable or wrong host. This is NOT your code.")
        print("       Try from a shell:")
        print(f'       curl {OLLAMA_HOST}/api/embeddings '
              f'-d \'{{"model":"{MODEL}","prompt":"hi"}}\'')
        return False
    except Exception as e:
        print(f"    FAIL: {type(e).__name__}: {e}")
        return False


def check_2_vector_shape():
    print("\n[2] Vector shape -------------------------------------------------")
    try:
        v = _post_embedding("search_document: oat milk has a low carbon footprint")
        ok = isinstance(v, list) and all(isinstance(x, (int, float)) for x in v[:5])
        print(f"    length: {len(v)} (expected {EXPECTED_DIM})")
        print(f"    first 3: {v[:3]}")
        if len(v) != EXPECTED_DIM:
            print(f"    WARN: dim {len(v)} != {EXPECTED_DIM}. Check the model name "
                  f"is exactly '{MODEL}'.")
        return ok
    except Exception as e:
        print(f"    FAIL: {type(e).__name__}: {e}")
        return False


def check_3_similarity():
    print("\n[3] Similarity behaviour (with prefixes) -------------------------")
    try:
        q = _post_embedding("search_query: what is the carbon footprint of this product?")
        hit = _post_embedding("search_document: our oat milk emits 0.4 kg CO2 per litre")
        miss = _post_embedding("search_document: visit our store locator and newsletter signup")
        s_hit, s_miss = _cos(q, hit), _cos(q, miss)
        print(f"    relevant doc   : {s_hit:.4f}")
        print(f"    irrelevant doc : {s_miss:.4f}")
        print(f"    gap            : {s_hit - s_miss:+.4f}")
        if s_hit > s_miss:
            print("    OK. relevant scores higher than irrelevant.")
            return True
        print("    FAIL: irrelevant scored >= relevant. Likely a prefix or "
              "wiring problem — see check [4].")
        return False
    except Exception as e:
        print(f"    FAIL: {type(e).__name__}: {e}")
        return False


def check_4_prefix_effect():
    print("\n[4] Do the prefixes actually help? -------------------------------")
    try:
        # With prefixes
        q_p = _post_embedding("search_query: carbon footprint of the product")
        hit_p = _post_embedding("search_document: our oat milk emits 0.4 kg CO2 per litre")
        miss_p = _post_embedding("search_document: store locator and newsletter signup")
        gap_with = _cos(q_p, hit_p) - _cos(q_p, miss_p)

        # Without prefixes
        q_n = _post_embedding("carbon footprint of the product")
        hit_n = _post_embedding("our oat milk emits 0.4 kg CO2 per litre")
        miss_n = _post_embedding("store locator and newsletter signup")
        gap_without = _cos(q_n, hit_n) - _cos(q_n, miss_n)

        print(f"    relevant-minus-irrelevant gap WITH prefixes   : {gap_with:+.4f}")
        print(f"    relevant-minus-irrelevant gap WITHOUT prefixes: {gap_without:+.4f}")
        if gap_with >= gap_without:
            print("    OK. prefixes widen (or hold) the gap — keep them. "
                  "Record this as a design decision in the report.")
        else:
            print("    NOTE: prefixes didn't help on this toy example. Re-test on "
                  "real question/page pairs before drawing a conclusion.")
        return True
    except Exception as e:
        print(f"    FAIL: {type(e).__name__}: {e}")
        return False


def main():
    print("=" * 66)
    print("Ollama nomic-embed-text diagnostic")
    print("=" * 66)

    if "your-server" in OLLAMA_HOST:
        print("\n!! Edit OLLAMA_HOST at the top of this file first.\n")
        sys.exit(1)

    if not check_1_reachable():
        sys.exit(1)
    if not check_2_vector_shape():
        sys.exit(1)
    sim_ok = check_3_similarity()
    check_4_prefix_effect()

    print("\n" + "=" * 66)
    if sim_ok:
        print("PASS — endpoint works and ranks relevant content higher.")
        print("Next: wire nomic-embed-text behind your _SCORERS dispatch and run")
        print("on real subpage links. The 'store locator' miss above is a preview")
        print("of the crawl scorer correctly rejecting nav links.")
    else:
        print("CHECK [3] FAILED — fix similarity before wiring into the pipeline.")
    print("=" * 66)


if __name__ == "__main__":
    main()
