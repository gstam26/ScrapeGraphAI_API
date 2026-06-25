"""
Unit tests for quote_id group-credit logic in aligner._match_cell.

The invariant under test: every member of a quote_id group receives a verdict
that derives from its OWN combined_score, not from the group representative's
score.  The group mechanism allows one AI claim to credit multiple GT rows; it
must not share scores between GT rows.
"""

from __future__ import annotations

import sys
import os
from unittest.mock import patch

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from diagnostics.eval_lib.aligner import (
    AUTO_MATCH_THRESHOLD,
    AUTO_MISS_THRESHOLD,
    PairScore,
    _match_cell,
)
from diagnostics.eval_lib.gt_reader import GTClaim
from diagnostics.eval_lib.pipeline_reader import AIClaim


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _gt(claim_id: str, claim: str, quote_id: str | None = None) -> GTClaim:
    return GTClaim(
        entity="TestBrand",
        entity_norm="testbrand",
        question="Sustainability",
        claim_id=claim_id,
        quote_id=quote_id,
        claim=claim,
        verbatim_quote="",
        source_url="",
        is_null=False,
        is_list=True,
    )


def _ai(value: str) -> AIClaim:
    return AIClaim(
        entity="TestBrand",
        entity_norm="testbrand",
        question="sustainability_claims",
        value=value,
        quote="",
        source_url="",
        verified=False,
        match_type="none",
        verification_score=None,
        semantic_score=None,
        confidence_score=None,
        source_depth=0,
        is_null=False,
    )


def _score(combined: float) -> PairScore:
    return PairScore(
        claim_cosine=combined,
        quote_overlap=0.0,
        combined_score=combined,
        method="cosine+noquote",
    )


# ---------------------------------------------------------------------------
# Core test
# ---------------------------------------------------------------------------

class TestGroupCredit:
    """
    Two GT claims share a quote_id (merged group). One AI claim is a strong
    match for the carrier but a weak match for the passenger.  After the fix,
    each member must receive the verdict that its OWN score warrants.
    """

    # Scores deliberately chosen to cross different band boundaries:
    #   carrier   0.90  → auto_match  (>= AUTO_MATCH_THRESHOLD 0.82)
    #   passenger 0.45  → auto_miss   (<  AUTO_MISS_THRESHOLD  0.60)
    CARRIER_SCORE = 0.90
    PASSENGER_SCORE = 0.45

    def _run(self):
        carrier = _gt(
            "TEST-C01",
            "Water conservation and sustainable watershed partnerships drive reduction "
            "in freshwater usage across all manufacturing sites globally.",
            quote_id="TEST-Q1",
        )
        passenger = _gt(
            "TEST-C02",
            "Packaging materials use recycled post-consumer content to minimise virgin "
            "plastic production and reduce landfill waste streams.",
            quote_id="TEST-Q1",
        )
        ai_claim = _ai(
            "Water conservation partnerships have significantly reduced freshwater "
            "consumption at all production facilities worldwide."
        )

        slot = {"gt": [carrier, passenger], "ai": [ai_claim]}

        scores = {
            "TEST-C01": _score(self.CARRIER_SCORE),
            "TEST-C02": _score(self.PASSENGER_SCORE),
        }

        def mock_pair_score(gt_claim, ai_claim, emb):
            return scores[gt_claim.claim_id]

        with patch("diagnostics.eval_lib.aligner._pair_score", side_effect=mock_pair_score):
            alignments, ai_only = _match_cell(slot, emb={}, is_list=True)

        return {a.gt_claim.claim_id: a for a in alignments}

    def test_carrier_auto_match(self):
        """Carrier (own score 0.90) must be auto_match."""
        by_id = self._run()
        assert "TEST-C01" in by_id, "Carrier alignment missing"
        assert by_id["TEST-C01"].verdict == "auto_match", (
            f"Carrier expected auto_match, got {by_id['TEST-C01'].verdict!r}. "
            f"Score was {self.CARRIER_SCORE} >= AUTO_MATCH_THRESHOLD {AUTO_MATCH_THRESHOLD}."
        )

    def test_passenger_auto_miss(self):
        """Passenger (own score 0.45) must be auto_miss, not promoted to auto_match."""
        by_id = self._run()
        assert "TEST-C02" in by_id, "Passenger alignment missing"
        assert by_id["TEST-C02"].verdict == "auto_miss", (
            f"Passenger expected auto_miss, got {by_id['TEST-C02'].verdict!r}. "
            f"Score was {self.PASSENGER_SCORE} < AUTO_MISS_THRESHOLD {AUTO_MISS_THRESHOLD}. "
            "This indicates the passenger is inheriting the carrier's verdict."
        )

    def test_both_members_assigned_same_ai_claim(self):
        """Both members must reference the same AI claim — group sharing is preserved."""
        by_id = self._run()
        assert by_id["TEST-C01"].ai_claim is by_id["TEST-C02"].ai_claim, (
            "Group members should reference the same AI claim object."
        )

    def test_passenger_has_own_score_in_alignment(self):
        """The score recorded for the passenger must be its own, not the carrier's."""
        by_id = self._run()
        recorded = by_id["TEST-C02"].score.combined_score
        assert recorded == self.PASSENGER_SCORE, (
            f"Passenger's recorded combined_score should be {self.PASSENGER_SCORE}, "
            f"got {recorded}. The carrier's score ({self.CARRIER_SCORE}) must not leak."
        )


# ---------------------------------------------------------------------------
# Manual-band passenger (score in [0.60, 0.82)) → manual, not auto_match
# ---------------------------------------------------------------------------

class TestManualBandPassenger:
    """A passenger whose score falls in the manual band must get 'manual'."""

    CARRIER_SCORE = 0.91
    PASSENGER_SCORE = 0.70   # in [AUTO_MISS_THRESHOLD, AUTO_MATCH_THRESHOLD)

    def _run(self):
        carrier = _gt("MB-C01", "A" * 80, quote_id="MB-Q1")
        passenger = _gt("MB-C02", "B" * 80, quote_id="MB-Q1")
        slot = {"gt": [carrier, passenger], "ai": [_ai("C" * 80)]}

        scores = {
            "MB-C01": _score(self.CARRIER_SCORE),
            "MB-C02": _score(self.PASSENGER_SCORE),
        }

        def mock_pair_score(gt_claim, ai_claim, emb):
            return scores[gt_claim.claim_id]

        with patch("diagnostics.eval_lib.aligner._pair_score", side_effect=mock_pair_score):
            alignments, _ = _match_cell(slot, emb={}, is_list=True)

        return {a.gt_claim.claim_id: a for a in alignments}

    def test_manual_band_passenger_gets_manual(self):
        by_id = self._run()
        assert by_id["MB-C02"].verdict == "manual", (
            f"Manual-band passenger expected 'manual', got {by_id['MB-C02'].verdict!r}."
        )

    def test_carrier_still_auto_match(self):
        by_id = self._run()
        assert by_id["MB-C01"].verdict == "auto_match"


# ---------------------------------------------------------------------------
# Singleton (no group) — unchanged behaviour
# ---------------------------------------------------------------------------

class TestSingleton:
    """Non-merged GT claims must behave identically to before the change."""

    def _run(self, score: float):
        gt = _gt("SG-C01", "Standalone claim about renewable energy procurement.", quote_id=None)
        slot = {"gt": [gt], "ai": [_ai("Renewable energy sourcing claim.")]}

        def mock_pair_score(g, a, emb):
            return _score(score)

        with patch("diagnostics.eval_lib.aligner._pair_score", side_effect=mock_pair_score):
            alignments, _ = _match_cell(slot, emb={}, is_list=True)

        return alignments[0]

    def test_singleton_above_threshold_auto_match(self):
        assert self._run(0.88).verdict == "auto_match"

    def test_singleton_in_manual_band(self):
        assert self._run(0.71).verdict == "manual"

    def test_singleton_below_miss_threshold_auto_miss(self):
        assert self._run(0.50).verdict == "auto_miss"
