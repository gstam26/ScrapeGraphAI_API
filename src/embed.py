import json
import urllib.request

from config import OLLAMA_HOST, OLLAMA_EMBED_MODEL, OLLAMA_KEEP_ALIVE, OLLAMA_TIMEOUT


def embed_batch(texts: list[str]) -> list[list[float]]:
    """Send a batch of texts to the Ollama embed endpoint and return vectors.

    Raises RuntimeError on a bad response and propagates urllib errors so
    callers can decide whether to retry or fall back.
    """
    if not texts:
        return []
    req = urllib.request.Request(
        f"{OLLAMA_HOST.rstrip('/')}/api/embed",
        data=json.dumps({
            "model": OLLAMA_EMBED_MODEL,
            "input": texts,
            "keep_alive": OLLAMA_KEEP_ALIVE,
        }).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    embs = body.get("embeddings")
    if not embs or len(embs) != len(texts):
        raise RuntimeError(
            f"embed returned {len(embs) if embs else 0} vectors for {len(texts)} inputs"
        )
    return embs
