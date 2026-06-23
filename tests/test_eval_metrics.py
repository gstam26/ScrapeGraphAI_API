from diagnostics.eval_lib.aligner import AIOnly, CellAlignment, GTAlignment, PairScore
from diagnostics.eval_lib.gt_reader import GTClaim, GroundTruth, normalise_entity
from diagnostics.eval_lib.metrics import _cell_metrics, ai_only_audit_rows
from diagnostics.eval_lib.pipeline_reader import AIClaim


def _gt(entity: str, claim: str, claim_id: str = "GT-1", is_null: bool = False) -> GTClaim:
    return GTClaim(
        entity=entity,
        entity_norm=normalise_entity(entity),
        question="ParentCompany",
        claim_id=claim_id,
        quote_id=None,
        claim=claim,
        verbatim_quote="",
        source_url="https://example.test",
        is_null=is_null,
        is_list=False,
    )


def _ai(entity: str, value: str, verified: bool = False, is_null: bool = False) -> AIClaim:
    return AIClaim(
        entity=entity,
        entity_norm=normalise_entity(entity),
        question="Parent company",
        value=value,
        quote="",
        source_url="https://example.test",
        verified=verified,
        match_type="none",
        verification_score=None,
        semantic_score=None,
        confidence_score=None,
        source_depth=0,
        is_null=is_null,
    )


def test_unmatched_null_sentinel_is_neutral_when_real_answer_matched():
    gt = _gt("DREAM", "SunOpta")
    real_ai = _ai("DREAM", "SunOpta", verified=True)
    null_ai = _ai("DREAM", "None (not disclosed on site)", is_null=True)
    cell = CellAlignment(
        entity="DREAM",
        entity_norm=normalise_entity("DREAM"),
        gt_question="ParentCompany",
        pipeline_question="Parent company",
        is_list=False,
        alignments=[
            GTAlignment(
                gt_claim=gt,
                verdict="auto_match",
                ai_claim=real_ai,
                score=PairScore(1.0, 0.0, 1.0, "containment"),
            )
        ],
        ai_only=[AIOnly(ai_claim=null_ai)],
    )

    metrics = _cell_metrics(cell, excluded_for_entity=[])

    assert metrics.hallucinations == 0
    assert metrics.possible_gt_gap == 0
    assert metrics.dynamic_neutral == 1
    assert metrics.precision_strict == 1.0
    assert metrics.precision_distinct == 1.0


def test_unmatched_null_sentinel_still_penalised_when_no_real_answer_matched():
    gt = _gt("DREAM", "SunOpta")
    null_ai = _ai("DREAM", "None (not disclosed on site)", is_null=True)
    cell = CellAlignment(
        entity="DREAM",
        entity_norm=normalise_entity("DREAM"),
        gt_question="ParentCompany",
        pipeline_question="Parent company",
        is_list=False,
        alignments=[GTAlignment(gt_claim=gt, verdict="auto_miss")],
        ai_only=[AIOnly(ai_claim=null_ai)],
    )

    metrics = _cell_metrics(cell, excluded_for_entity=[])

    assert metrics.hallucinations == 1
    assert metrics.dynamic_neutral == 0
    assert metrics.precision_strict == 0.0


def test_ai_only_audit_reports_null_sentinel_as_neutral_hypothesis():
    gt = _gt("DREAM", "SunOpta")
    real_ai = _ai("DREAM", "SunOpta", verified=True)
    null_ai = _ai("DREAM", "Not disclosed")
    cell = CellAlignment(
        entity="DREAM",
        entity_norm=normalise_entity("DREAM"),
        gt_question="ParentCompany",
        pipeline_question="Parent company",
        is_list=False,
        alignments=[
            GTAlignment(
                gt_claim=gt,
                verdict="auto_match",
                ai_claim=real_ai,
                score=PairScore(1.0, 0.0, 1.0, "containment"),
            )
        ],
        ai_only=[AIOnly(ai_claim=null_ai)],
    )

    rows = ai_only_audit_rows(
        result=type("Result", (), {"cells": [cell]})(),
        gt=GroundTruth(active_claims=[gt], excluded=[]),
    )

    assert len(rows) == 1
    assert rows[0].category == "excluded/null_neutral"
    assert "null-like AI answer" in rows[0].reason
