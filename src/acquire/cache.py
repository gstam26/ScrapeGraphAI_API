"""Flat on-disk page cache keyed by sha256(url).

Stores extracted page text as one file per URL under cache_dir, so repeat
runs (and re-runs after a crash) skip the network entirely. Entry points:
read_cache / write_cache; cache_path_any exists for read-only diagnostics
that need to locate an entry regardless of its extension.
"""

import os
import hashlib


def cache_path(url: str, cache_dir: str = "cache", ext: str = ".txt") -> str:
    """Return the cache file path for url (sha256 key), creating cache_dir."""
    os.makedirs(cache_dir, exist_ok=True)
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return os.path.join(cache_dir, f"{key}{ext}")


def cache_path_any(url: str, cache_dir: str = "cache") -> str | None:
    """Try .md (Firecrawl) then .txt (requests). For read-only diagnostics."""
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    for ext in (".md", ".txt"):
        path = os.path.join(cache_dir, f"{key}{ext}")
        if os.path.exists(path):
            return path
    return None


def read_cache(url: str, cache_dir: str = "cache") -> str | None:
    """Return the cached text for url, or None on a cache miss."""
    path = cache_path(url, cache_dir)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return None


def write_cache(url: str, content: str, cache_dir: str = "cache") -> None:
    """Store content as the cached text for url."""
    with open(cache_path(url, cache_dir), "w", encoding="utf-8") as f:
        f.write(content)
