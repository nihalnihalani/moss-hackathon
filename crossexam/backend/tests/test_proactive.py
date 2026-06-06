"""Tests for :mod:`crossexam_backend.proactive` (claim detection + gating)."""

from __future__ import annotations

from crossexam_backend.models import BBox, Chunk, Citation, RetrievalResult
from crossexam_backend.proactive import (
    ClaimDetector,
    SurfaceThresholds,
    should_surface,
)
from crossexam_backend.verify import METHOD_LEXICAL, FaithfulnessVerdict


def _result(score: float) -> RetrievalResult:
    """A result with a single citation at the given relevance ``score``."""
    chunk = Chunk(
        id="c1",
        text="The defendant signed the contract on March 3rd.",
        page=1,
        bbox=BBox(page=1, x0=0.0, y0=0.0, x1=10.0, y1=10.0),
        confidence=0.9,
    )
    return RetrievalResult(
        query="q", citations=[Citation(chunk=chunk, score=score)], latency_ms=1.0
    )


def _verdict(supported: bool, score: float) -> FaithfulnessVerdict:
    return FaithfulnessVerdict(supported=supported, score=score, method=METHOD_LEXICAL)


# -- ClaimDetector: question vs claim --------------------------------------- #
def test_question_with_mark_is_question() -> None:
    """A trailing '?' marks a question, never a claim."""
    det = ClaimDetector()
    assert det.is_question("Where were you on the 14th?") is True
    assert det.is_claim("Where were you on the 14th?") is False


def test_question_without_mark_detected_by_leader() -> None:
    """A leading auxiliary/interrogative marks a question without a '?'."""
    det = ClaimDetector()
    assert det.is_question("did you sign the contract") is True
    assert det.is_claim("did you sign the contract") is False


def test_declarative_with_number_is_claim() -> None:
    """A declarative carrying a number/ordinal is a checkable claim."""
    det = ClaimDetector()
    assert det.is_question("The contract was signed on March 3rd.") is False
    assert det.is_claim("The contract was signed on March 3rd.") is True


def test_declarative_with_negation_is_claim() -> None:
    """A declarative with a negation is a checkable claim."""
    det = ClaimDetector()
    assert det.is_claim("I never entered the warehouse that night.") is True


def test_declarative_with_entity_is_claim() -> None:
    """A declarative naming an entity is a checkable claim."""
    det = ClaimDetector()
    assert det.is_claim("The deal closed with Acme Corporation.") is True


def test_short_filler_not_a_claim() -> None:
    """Short filler utterances are not claims."""
    det = ClaimDetector()
    assert det.is_claim("yes") is False
    assert det.is_claim("okay sure") is False


def test_plain_declarative_without_signal_not_a_claim() -> None:
    """A declarative with no entity/number/negation lacks a checkable signal."""
    det = ClaimDetector()
    assert det.is_question("we walked over there") is False
    assert det.is_claim("we walked over there") is False


# -- should_surface: confidence gating -------------------------------------- #
def test_should_surface_passes_when_both_clear() -> None:
    """Surfacing is allowed when retrieval and faithfulness both clear gates."""
    assert should_surface(_result(0.8), _verdict(True, 0.8)) is True


def test_should_surface_blocked_by_low_retrieval() -> None:
    """A weak retrieval score blocks surfacing even if faithfulness is strong."""
    assert should_surface(_result(0.2), _verdict(True, 0.9)) is False


def test_should_surface_blocked_by_unsupported_faithfulness() -> None:
    """An unsupported faithfulness verdict blocks surfacing."""
    assert should_surface(_result(0.9), _verdict(False, 0.9)) is False


def test_should_surface_blocked_by_low_faithfulness_score() -> None:
    """A weak faithfulness score blocks surfacing even if retrieval is strong."""
    assert should_surface(_result(0.9), _verdict(True, 0.3)) is False


def test_should_surface_no_citations_blocked() -> None:
    """No citations means nothing to surface."""
    empty = RetrievalResult(query="q", citations=[], latency_ms=1.0)
    assert should_surface(empty, _verdict(True, 1.0)) is False


def test_should_surface_custom_thresholds() -> None:
    """Custom thresholds tighten or loosen the surfacing gate."""
    thr = SurfaceThresholds(retrieval_score=0.95, faithfulness_score=0.95)
    assert should_surface(_result(0.9), _verdict(True, 0.99), thr) is False
    assert should_surface(_result(0.96), _verdict(True, 0.96), thr) is True
