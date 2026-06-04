import os
import hashlib
import requests
from bs4 import BeautifulSoup

from config import CACHE_DIR, REQUEST_HEADERS
from models import PageDoc


def _cache_path(url: str) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return os.path.join(CACHE_DIR, f"{key}.txt")


def acquire_page(url: str) -> PageDoc:
    cache_file = _cache_path(url)

    if os.path.exists(cache_file):
        with open(cache_file, "r", encoding="utf-8") as f:
            text = f.read()

        return PageDoc(
            url=url,
            text=text,
            html=None,
            from_cache=True,
        )

    response = requests.get(
        url,
        timeout=30,
        headers=REQUEST_HEADERS,
    )
    response.raise_for_status()

    html = response.text
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    text = soup.get_text(separator="\n")
    text = "\n".join(line.strip() for line in text.splitlines() if line.strip())

    with open(cache_file, "w", encoding="utf-8") as f:
        f.write(text)

    return PageDoc(
        url=url,
        text=text,
        html=html,
        from_cache=False,
    )