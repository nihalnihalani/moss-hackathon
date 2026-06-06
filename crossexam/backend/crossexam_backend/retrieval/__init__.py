"""Retrieval subpackage: pluggable semantic/keyword indexes for CrossExam."""

from __future__ import annotations

from crossexam_backend.retrieval.base import RetrievalIndex
from crossexam_backend.retrieval.factory import get_index
from crossexam_backend.retrieval.mock_index import MockIndex
from crossexam_backend.retrieval.moss_client import MossIndex

__all__ = ["RetrievalIndex", "MockIndex", "MossIndex", "get_index"]
