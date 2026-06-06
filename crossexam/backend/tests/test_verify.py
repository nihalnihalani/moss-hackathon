"""Tests for the inference-time faithfulness verifier."""

from __future__ import annotations

from crossexam_backend.verify import (
    METHOD_LEXICAL,
    METHOD_LLM,
    FaithfulnessVerdict,
    LLMVerifier,
    first_supported,
    verify_faithfulness,
)

# The canonical page-12 admission chunk from the demo fixture.
CHUNK_P12 = (
    "Q. Where were you on the night of the 14th? A. I was at the Harbor Street "
    "warehouse from approximately 9:00 p.m. until well past midnight, conducting "
    "the inventory count with Mr. Reyes."
)


def test_grounded_answer_is_supported() -> None:
    """An answer that reuses the chunk's content tokens is supported."""
    answer = (
        "The witness was at the Harbor Street warehouse until past midnight "
        "on the night of the 14th."
    )
    verdict = verify_faithfulness(answer, CHUNK_P12)
    assert isinstance(verdict, FaithfulnessVerdict)
    assert verdict.supported is True
    assert verdict.score >= 0.5
    assert verdict.method == METHOD_LEXICAL


def test_unrelated_answer_is_not_supported() -> None:
    """An answer about a different subject is not supported by the chunk."""
    answer = (
        "The contract's indemnification clause requires the supplier to cover "
        "all defective goods losses."
    )
    verdict = verify_faithfulness(answer, CHUNK_P12)
    assert verdict.supported is False
    assert verdict.score < 0.5


def test_empty_answer_is_vacuously_supported() -> None:
    """An answer with no content claim has nothing to contradict."""
    verdict = verify_faithfulness("", CHUNK_P12)
    assert verdict.supported is True
    assert verdict.score == 1.0


def test_empty_chunk_supports_nothing() -> None:
    """A non-trivial answer cannot be supported by an empty chunk."""
    verdict = verify_faithfulness("The witness was at the warehouse.", "")
    assert verdict.supported is False
    assert verdict.score == 0.0


def test_threshold_is_tunable() -> None:
    """Raising the threshold can flip a borderline answer to unsupported."""
    answer = "The witness was at the warehouse."  # partial overlap with chunk
    lenient = verify_faithfulness(answer, CHUNK_P12, threshold=0.1)
    strict = verify_faithfulness(answer, CHUNK_P12, threshold=0.99)
    assert lenient.supported is True
    assert strict.supported is False
    # Same underlying score, different gate.
    assert lenient.score == strict.score


def test_score_is_in_unit_interval() -> None:
    """The support score is always within [0, 1]."""
    verdict = verify_faithfulness("warehouse warehouse warehouse", CHUNK_P12)
    assert 0.0 <= verdict.score <= 1.0


def test_llm_verifier_falls_back_to_lexical_without_scorer() -> None:
    """With no entailment scorer wired, LLMVerifier uses the lexical proxy."""
    verifier = LLMVerifier()
    assert verifier.available is False
    verdict = verifier.verify("The witness was at the warehouse.", CHUNK_P12)
    assert verdict.method == METHOD_LEXICAL


def test_llm_verifier_uses_injected_scorer() -> None:
    """A wired entailment scorer drives the verdict and is tagged as LLM."""

    def always_entails(answer: str, cited_text: str) -> float:
        return 0.92

    verifier = LLMVerifier(always_entails, threshold=0.5)
    assert verifier.available is True
    verdict = verifier.verify("anything at all", CHUNK_P12)
    assert verdict.method == METHOD_LLM
    assert verdict.supported is True
    assert verdict.score == 0.92


def test_llm_verifier_clamps_and_thresholds() -> None:
    """Out-of-range entailment probabilities are clamped into [0, 1]."""
    verifier = LLMVerifier(lambda a, c: 1.5, threshold=0.5)
    verdict = verifier.verify("x", "y")
    assert verdict.score == 1.0


def test_llm_verifier_recovers_from_scorer_error() -> None:
    """A scorer that raises degrades to the lexical fallback, never crashes."""

    def boom(answer: str, cited_text: str) -> float:
        raise RuntimeError("model unavailable")

    verifier = LLMVerifier(boom)
    verdict = verifier.verify("The witness was at the warehouse.", CHUNK_P12)
    assert verdict.method == METHOD_LEXICAL


def test_first_supported_returns_first_grounded_chunk() -> None:
    """first_supported skips unsupported chunks and returns the grounded one."""
    answer = "The witness was at the Harbor Street warehouse past midnight."
    chunks = ["A. Certainly.", "Q. State your name.", CHUNK_P12]
    verdict = first_supported(answer, chunks)
    assert verdict is not None
    assert verdict.supported is True


def test_first_supported_returns_none_when_unsupported() -> None:
    """first_supported returns None when no chunk supports the answer."""
    answer = "The contract indemnification clause covers defective goods."
    chunks = ["A. Certainly.", "Q. State your name for the record."]
    assert first_supported(answer, chunks) is None
