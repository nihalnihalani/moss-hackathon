"""Tests for :mod:`crossexam_backend.tracing` (no-op by default, env-gated)."""

from __future__ import annotations

from crossexam_backend.config import Settings
from crossexam_backend.tracing import NoOpTracer, get_tracer, tracing_status


def _settings(**kw: object) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


def test_default_tracer_is_noop() -> None:
    """With no obs config the tracer is a zero-overhead no-op (off in test)."""
    tracer = get_tracer(_settings())
    assert isinstance(tracer, NoOpTracer)
    assert tracer.enabled is False


def test_noop_span_accepts_attributes_without_error() -> None:
    """The no-op span swallows set_attribute / set_attributes calls."""
    tracer = get_tracer(_settings())
    with tracer.span("retrieve", query="hi", top_k=3) as span:
        span.set_attribute("latency_ms", 1.2)
        span.set_attributes({"faithfulness.score": 0.9})


def test_tracing_status_off_without_keys() -> None:
    """tracing_status is OFF when obs is disabled or keys/extra are missing."""
    assert tracing_status(_settings()) == "OFF"
    assert tracing_status(_settings(obs_enabled=True)) == "OFF"


def test_tracing_status_off_when_enabled_but_no_keys() -> None:
    """Even with obs_enabled, missing Langfuse keys keep tracing OFF."""
    s = _settings(obs_enabled=True, langfuse_public_key="pk")
    assert s.has_langfuse_credentials is False
    assert tracing_status(s) == "OFF"
