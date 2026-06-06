"""Env-gated OpenTelemetry tracing exported to Langfuse.

CrossExam's hot path (retrieve -> verify -> publish) is wrapped in spans so a
live session can be inspected in Langfuse. Tracing is *never required*: it only
activates when the ``[obs]`` extra is installed (opentelemetry-sdk + langfuse)
AND the Langfuse keys are present in the environment. Otherwise a zero-overhead
no-op tracer is returned, so mock/test/offline runs never touch OTel or the
network.

Usage::

    tracer = get_tracer(settings)
    with tracer.span("retrieve", query=text, top_k=5) as span:
        result = await index.query(text, top_k=5)
        span.set_attribute("latency_ms", result.latency_ms)

The :class:`Span` returned by ``span()`` is a thin uniform wrapper so callers
need not branch on whether real OTel is active.
"""

from __future__ import annotations

import contextlib
import importlib.util
import logging
from collections.abc import Iterator
from typing import Any

from crossexam_backend.config import Settings

logger = logging.getLogger(__name__)


def _otel_available() -> bool:
    """Return ``True`` when the OpenTelemetry SDK is importable (no import)."""
    try:
        return importlib.util.find_spec("opentelemetry.sdk") is not None
    except (ImportError, ValueError, ModuleNotFoundError):
        return False


class _NoOpSpan:
    """A span that records nothing. Returned by the no-op tracer."""

    def set_attribute(self, key: str, value: Any) -> None:  # noqa: ANN401
        """Discard the attribute (no-op)."""

    def set_attributes(self, attributes: dict[str, Any]) -> None:
        """Discard the attributes (no-op)."""


class _OTelSpan:
    """Uniform wrapper over a real OpenTelemetry span."""

    def __init__(self, span: Any) -> None:  # noqa: ANN401 - OTel Span optional
        """Wrap the underlying OTel span handle."""
        self._span = span

    def set_attribute(self, key: str, value: Any) -> None:  # noqa: ANN401
        """Set a single attribute on the underlying span (None-safe)."""
        if value is None:
            return
        with contextlib.suppress(Exception):
            self._span.set_attribute(key, value)

    def set_attributes(self, attributes: dict[str, Any]) -> None:
        """Set several attributes, skipping ``None`` values."""
        for key, value in attributes.items():
            self.set_attribute(key, value)


class NoOpTracer:
    """Zero-overhead tracer used when observability is off (the default)."""

    enabled = False

    @contextlib.contextmanager
    def span(self, name: str, **attributes: Any) -> Iterator[_NoOpSpan]:  # noqa: ANN401
        """Yield a no-op span; never touches OTel or the network."""
        del name, attributes
        yield _NoOpSpan()


class OTelTracer:
    """OpenTelemetry-backed tracer (only built when obs is fully configured)."""

    enabled = True

    def __init__(self, tracer: Any) -> None:  # noqa: ANN401 - OTel Tracer optional
        """Wrap an underlying OTel tracer."""
        self._tracer = tracer

    @contextlib.contextmanager
    def span(self, name: str, **attributes: Any) -> Iterator[_OTelSpan]:  # noqa: ANN401
        """Start a span named ``name``, applying ``attributes`` up front."""
        with self._tracer.start_as_current_span(name) as raw:
            wrapped = _OTelSpan(raw)
            wrapped.set_attributes(attributes)
            yield wrapped


def tracing_status(settings: Settings) -> str:
    """Report tracing readiness for the doctor: ``READY`` or ``OFF``.

    READY means the ``[obs]`` extra is importable AND Langfuse keys are present,
    so a live session would export spans. Otherwise OFF (no-op tracer).
    """
    if settings.obs_enabled and _otel_available() and settings.has_langfuse_credentials:
        return "READY"
    return "OFF"


def get_tracer(settings: Settings) -> NoOpTracer | OTelTracer:
    """Return a real OTel tracer when fully configured, else a no-op tracer.

    Activation requires all of: ``settings.obs_enabled``, the OpenTelemetry SDK
    installed (``[obs]`` extra), and Langfuse credentials present. Any failure
    to construct the real pipeline falls back to the no-op tracer — tracing must
    never break the turn.
    """
    if not settings.obs_enabled:
        return NoOpTracer()
    if not _otel_available() or not settings.has_langfuse_credentials:
        return NoOpTracer()
    try:  # pragma: no cover - exercised only with the [obs] extra installed
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import (
            BatchSpanProcessor,
            SpanExporter,
        )

        exporter = _build_langfuse_exporter(settings, SpanExporter)
        if exporter is None:
            return NoOpTracer()
        provider = TracerProvider()
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        return OTelTracer(trace.get_tracer("crossexam"))
    except Exception:  # noqa: BLE001 - tracing is best-effort, never fatal
        logger.exception("tracing.get_tracer failed; falling back to no-op")
        return NoOpTracer()


def _build_langfuse_exporter(
    settings: Settings, span_exporter_cls: type
) -> Any | None:  # noqa: ANN401 - OTel exporter type is optional
    """Build the OTLP exporter pointed at Langfuse, or ``None`` on failure.

    Langfuse ingests OpenTelemetry over OTLP/HTTP with basic-auth built from the
    public/secret key pair. Imported lazily so the [obs] extra is only required
    when tracing is actually on.
    """
    try:  # pragma: no cover - needs the [obs] extra installed
        import base64

        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )

        auth = base64.b64encode(
            f"{settings.langfuse_public_key}:{settings.langfuse_secret_key}".encode()
        ).decode()
        endpoint = settings.langfuse_host.rstrip("/") + "/api/public/otel/v1/traces"
        exporter = OTLPSpanExporter(
            endpoint=endpoint,
            headers={"Authorization": f"Basic {auth}"},
        )
        assert isinstance(exporter, span_exporter_cls)  # noqa: S101
        return exporter
    except Exception:  # noqa: BLE001 - exporter is best-effort
        logger.exception("tracing exporter build failed")
        return None
