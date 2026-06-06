"""Tests for the in-process Okapi BM25 scorer."""

from __future__ import annotations

from crossexam_backend.retrieval.bm25 import BM25Okapi


def _corpus() -> list[list[str]]:
    return [
        ["the", "warehouse", "was", "dark", "at", "night"],
        ["the", "witness", "left", "the", "warehouse", "before", "eight"],
        ["the", "contract", "indemnification", "clause", "is", "void"],
    ]


def test_scores_have_one_entry_per_document() -> None:
    """get_scores returns a score for every corpus document, in order."""
    bm25 = BM25Okapi(_corpus())
    scores = bm25.get_scores(["warehouse"])
    assert len(scores) == 3


def test_document_with_query_term_outscores_one_without() -> None:
    """A document containing the query term scores above one that does not."""
    bm25 = BM25Okapi(_corpus())
    scores = bm25.get_scores(["indemnification"])
    # Only doc 2 contains 'indemnification'.
    assert scores[2] > scores[0]
    assert scores[2] > scores[1]
    assert scores[0] == 0.0
    assert scores[1] == 0.0


def test_more_query_terms_matched_scores_higher() -> None:
    """A document matching more distinct query terms ranks higher."""
    bm25 = BM25Okapi(_corpus())
    scores = bm25.get_scores(["witness", "warehouse"])
    # Doc 1 has both 'witness' and 'warehouse'; doc 0 has only 'warehouse'.
    assert scores[1] > scores[0] > 0.0


def test_idf_rewards_rarer_terms() -> None:
    """A term in fewer documents has a higher IDF than a more common term."""
    bm25 = BM25Okapi(_corpus())
    # 'warehouse' appears in 2 docs; 'indemnification' in 1 -> rarer, higher IDF.
    assert bm25.idf("indemnification") > bm25.idf("warehouse")


def test_idf_is_non_negative_for_common_terms() -> None:
    """A term present in (almost) every document keeps a non-negative IDF."""
    bm25 = BM25Okapi(_corpus())
    # 'the' appears in all three docs; classic BM25 IDF would go negative, but
    # the floor keeps it non-negative so it never penalises a document.
    assert bm25.idf("the") >= 0.0


def test_length_normalisation_prefers_shorter_relevant_doc() -> None:
    """With equal term frequency, the shorter document scores higher (b>0)."""
    corpus = [
        ["warehouse"],
        ["warehouse", "and", "many", "other", "unrelated", "filler", "words"],
    ]
    bm25 = BM25Okapi(corpus)
    scores = bm25.get_scores(["warehouse"])
    assert scores[0] > scores[1]


def test_out_of_vocabulary_query_scores_zero() -> None:
    """A query term absent from the corpus contributes nothing."""
    bm25 = BM25Okapi(_corpus())
    scores = bm25.get_scores(["nonexistentterm"])
    assert all(s == 0.0 for s in scores)


def test_empty_corpus_is_safe() -> None:
    """An empty corpus yields no scores rather than raising."""
    bm25 = BM25Okapi([])
    assert bm25.get_scores(["warehouse"]) == []
