"""
Cross-encoder pairwise scorer — EXPERIMENTAL alternative to embedding cosine.

A cross-encoder reads a (text_a, text_b) PAIR through one transformer and
outputs a single relevance logit; it never produces vectors. That makes it a
candidate replacement for cosine similarity exactly where the task is
pairwise scoring (filter routing: question vs chunk; eval matching: GT value
vs AI value) and structurally unusable where vectors are required
(group.py clustering needs centroids and mean-centering — keep Ollama there).

Two caveats, both deliberate and documented rather than hidden:

  * TASK MISMATCH: ms-marco models are trained on RELEVANCE ("is this passage
    relevant to this query?"), not EQUIVALENCE ("are these two answers the
    same fact?"). For the eval matcher this is an approximation — a
    paraphrase/STS or NLI cross-encoder is the technically right model; point
    CROSS_ENCODER_MODEL at one when available.
  * UNVALIDATED THRESHOLD: sigmoid(logit) shares the 0..1 range with cosine
    but NOT its distribution. CROSS_ENCODER_MIN starts at 0.50 as a
    placeholder; do not trust verdicts that hinge on it until the
    matcher_eval label-score leg has measured agreement with human labels
    (the same bar the summary judge had to pass).

Network policy: HuggingFace downloads are blocked on the Sagentia network.
Run this only where the model files already exist locally (set
CROSS_ENCODER_MODEL to the local path) or off-network.
"""
from __future__ import annotations

import math
import os

# HF id of the model George has locally; override with a filesystem path via
# env when the auto-resolved cache is not available (e.g. on-network).
CROSS_ENCODER_MODEL = os.getenv(
    "CROSS_ENCODER_MODEL", "cross-encoder/ms-marco-MiniLM-L6-v2"
)
CROSS_ENCODER_MIN = float(os.getenv("CROSS_ENCODER_MIN", "0.50"))


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


class CrossEncoderScorer:
    """Pairwise scorer with the interface generic_eval's matcher expects:
    .score(a, b) -> float in [0, 1], .min_score, .name.

    The model loads lazily on first use (sentence-transformers + torch are
    heavy and not installed everywhere); pass `model=` to inject a fake in
    tests. Scores are cached per (a, b) pair — alignment scores the same
    pair from several call sites.
    """

    def __init__(self, model=None, model_name: str = CROSS_ENCODER_MODEL,
                 min_score: float = CROSS_ENCODER_MIN):
        self._model = model
        self.model_name = model_name
        self.min_score = min_score
        self.name = f"cross-encoder [{model_name}] (EXPERIMENTAL — threshold unvalidated)"
        self._cache: dict[tuple[str, str], float] = {}

    def _ensure_model(self):
        if self._model is None:
            from sentence_transformers import CrossEncoder  # heavy import, on demand
            self._model = CrossEncoder(self.model_name)
        return self._model

    def score(self, a: str, b: str) -> float:
        return self.score_pairs([(a, b)])[0]

    def score_pairs(self, pairs: list[tuple[str, str]]) -> list[float]:
        """Batch-score; one model call for all cache misses."""
        missing = [p for p in pairs if p not in self._cache]
        if missing:
            model = self._ensure_model()
            logits = model.predict(missing)
            for p, logit in zip(missing, logits):
                self._cache[p] = _sigmoid(float(logit))
        return [self._cache[p] for p in pairs]
