"""Preflight / doctor for the CrossExam backend.

Run with::

    python -m crossexam_backend.doctor

It inspects the resolved configuration and prints a status table of every
subsystem the worker needs — without making ANY network calls. For each item it
reports one of:

  - ``READY``   the real integration is configured and its deps import.
  - ``MISSING`` a credential or dependency is absent (the worker would degrade
                or fail to start that leg).
  - ``MOCK``    the deterministic in-memory/offline path will be used instead.

This turns an on-site bring-up into "read the table, fill the gaps" rather than
discovering breakage at runtime. It is exit-code aware: ``0`` when nothing is
outright broken (all rows READY or MOCK), ``1`` when a row is MISSING in a way
that would stop a live session the current config asks for.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import importlib.util
import logging
from dataclasses import dataclass
from enum import Enum

from crossexam_backend.agent import LIVEKIT_AVAILABLE
from crossexam_backend.config import Settings, get_settings
from crossexam_backend.tracing import tracing_status

logger = logging.getLogger(__name__)


class Status(str, Enum):
    """Status for a single preflight check.

    ``OFF`` marks an optional feature that is intentionally disabled (e.g.
    tracing without the [obs] extra/keys, or proactive surfacing toggled off);
    unlike ``MISSING`` it never fails the run.
    """

    READY = "READY"
    MISSING = "MISSING"
    MOCK = "MOCK"
    OFF = "OFF"


@dataclass(frozen=True)
class Check:
    """A single preflight row.

    Attributes:
        name: Short subsystem name (e.g. ``"Moss retrieval"``).
        status: One of :class:`Status`.
        detail: Human-readable explanation / next action.
    """

    name: str
    status: Status
    detail: str


def _module_available(module_name: str) -> bool:
    """Return ``True`` if ``module_name`` can be imported, without importing it.

    Uses :func:`importlib.util.find_spec` so no top-level side effects run and
    no network calls are made.
    """
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, ValueError, ModuleNotFoundError):
        return False


def _moss_import_available() -> str | None:
    """Return the first importable Moss module name, or ``None``."""
    # Mirror moss_client._MOSS_IMPORT_CANDIDATES without importing the SDK.
    for name in ("moss", "inferedge_moss"):
        if _module_available(name):
            return name
    return None


def _moss_client_cls(module_name: str) -> type | None:
    """Import ``module_name`` and return its ``MossClient``/``Client`` class.

    Returns ``None`` when the module fails to import or exposes neither class,
    so the probe can report a clear reason instead of raising.
    """
    try:
        module = importlib.import_module(module_name)
    except Exception:  # noqa: BLE001 - probe must never crash the doctor
        logger.debug("doctor.moss_probe SDK import failed", exc_info=True)
        return None
    return getattr(module, "MossClient", None) or getattr(module, "Client", None)


async def _probe_moss_index_async(
    settings: Settings, module_name: str
) -> tuple[bool, str]:
    """Async core of the live probe — reports the index's REAL readiness.

    Uses the real ``inferedge_moss`` async lifecycle:

    * ``await client.list_indexes()`` to confirm the project is reachable and the
      configured index exists,
    * ``await client.get_index(name)`` to read ``IndexInfo.status`` and
      ``doc_count``, and
    * ``await client.load_index(name)`` to confirm the index actually loads.

    Returns ``(ok, detail)`` where ``ok`` is ``True`` only when the index exists,
    reports a Ready status and loads. Every failure is caught and surfaced in the
    detail string; the probe is non-fatal and never raises.
    """
    client_cls = _moss_client_cls(module_name)
    if client_cls is None:
        return False, (
            f"SDK {module_name!r} import failed or exposes neither MossClient "
            "nor Client."
        )

    name = settings.moss_index_name
    client = client_cls(settings.moss_project_id, settings.moss_project_key)
    try:
        # 1) list_indexes -> reachability + existence.
        try:
            indexes = await client.list_indexes()
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            logger.debug("doctor.moss_probe list_indexes failed", exc_info=True)
            return False, (
                f"index={name!r} NOT reachable via {module_name!r}: "
                f"list_indexes failed: {exc}"
            )
        existing = {getattr(i, "name", None) for i in (indexes or [])}
        if name not in existing:
            available = sorted(n for n in existing if n)
            return False, (
                f"index={name!r} not found via {module_name!r}; "
                f"available={available}."
            )

        # 2) get_index -> status + doc_count.
        info = await client.get_index(name)
        status = getattr(info, "status", None)
        status_str = getattr(status, "value", None) or getattr(
            status, "name", None
        ) or str(status)
        doc_count = getattr(info, "doc_count", None)

        # 3) load_index -> confirm it loads.
        load_status = await client.load_index(name)

        ready = str(status_str).lower() in {"ready", "completed"}
        detail = (
            f"index={name!r} status={status_str} docs={doc_count} "
            f"(load_index -> {load_status!r}) via {module_name!r}."
        )
        return ready, detail
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            try:
                closed = close()
                if hasattr(closed, "__await__"):
                    await closed
            except Exception:  # noqa: BLE001 - close failure is non-fatal
                logger.debug("doctor.moss_probe client.close failed", exc_info=True)


def _probe_moss_index(settings: Settings, module_name: str) -> tuple[bool, str]:
    """Live-probe Moss readiness via the real async lifecycle (CLI-only).

    This is the ONLY check that makes network calls, so it runs ONLY under
    ``--probe``/``--live``. It drives :func:`_probe_moss_index_async` through
    :func:`asyncio.run`, reporting the configured index's real status and
    doc_count (e.g. ``index='x' status=Ready docs=42``).

    CLI-only (uses ``asyncio.run``): do NOT call from a coroutine or from any
    context that already has a running event loop — it will raise
    ``RuntimeError: This event loop is already running``.

    Returns:
        ``(ok, detail)`` — ``ok`` is ``True`` only when the index exists, is
        Ready and loads. Every failure is caught and reported; the probe is
        non-fatal and never raises.
    """
    try:
        return asyncio.run(_probe_moss_index_async(settings, module_name))
    except Exception as exc:  # noqa: BLE001 - probe failure is reported, not raised
        logger.debug("doctor.moss_probe failed", exc_info=True)
        return False, (
            f"index={settings.moss_index_name!r} probe failed via "
            f"{module_name!r}: {exc}"
        )


def _check_retrieval(settings: Settings, *, probe: bool = False) -> Check:
    """Check the retrieval backend (Moss vs MockIndex).

    When ``probe`` is ``True`` and the live path is configured, this makes a
    LIVE network call to confirm the index is reachable (see
    :func:`_probe_moss_index`). Otherwise it only verifies creds + SDK import and
    reports the index existence as UNVERIFIED.
    """
    if settings.use_mocks:
        return Check(
            "Moss retrieval",
            Status.MOCK,
            "USE_MOCKS resolves true -> deterministic MockIndex (fixture).",
        )
    if not settings.has_moss_credentials:
        return Check(
            "Moss retrieval",
            Status.MISSING,
            "USE_MOCKS=false but MOSS_PROJECT_ID/MOSS_PROJECT_KEY not set.",
        )
    moss_module = _moss_import_available()
    if moss_module is None:
        return Check(
            "Moss retrieval",
            Status.MISSING,
            "Credentials set but Moss SDK not installed "
            "(pip install '.[moss]').",
        )
    if probe:
        ok, detail = _probe_moss_index(settings, moss_module)
        return Check("Moss retrieval", Status.READY if ok else Status.MISSING, detail)
    return Check(
        "Moss retrieval",
        Status.READY,
        f"Credentials set; SDK importable as {moss_module!r}; "
        f"index={settings.moss_index_name!r} configured (index existence "
        "unverified — re-run with --probe to confirm reachability).",
    )


def _check_livekit(settings: Settings) -> Check:
    """Check the LiveKit Agents runtime + credentials."""
    if not LIVEKIT_AVAILABLE:
        return Check(
            "LiveKit runtime",
            Status.MOCK,
            "livekit-agents not installed -> worker prints a notice and exits.",
        )
    if not settings.has_livekit_credentials:
        return Check(
            "LiveKit runtime",
            Status.MISSING,
            "livekit-agents installed but LIVEKIT_URL/API_KEY/API_SECRET incomplete.",
        )
    return Check(
        "LiveKit runtime",
        Status.READY,
        "livekit-agents installed and all LiveKit credentials present.",
    )


# (env var name, settings attribute holding the key, livekit plugin module).
_PROVIDERS: tuple[tuple[str, str, str, str], ...] = (
    ("STT", "stt_provider", "deepgram_api_key", "livekit.plugins.deepgram"),
    ("LLM", "llm_provider", "openai_api_key", "livekit.plugins.openai"),
    ("TTS", "tts_provider", "cartesia_api_key", "livekit.plugins.cartesia"),
)


def _check_provider(
    settings: Settings, leg: str, provider_attr: str, key_attr: str, plugin: str
) -> Check:
    """Check one STT/LLM/TTS provider leg (key + plugin import)."""
    provider = getattr(settings, provider_attr)
    name = f"{leg} provider ({provider})"
    # In mock mode the voice pipeline is never started.
    if settings.use_mocks or not LIVEKIT_AVAILABLE:
        return Check(name, Status.MOCK, "Voice pipeline not started in mock/offline mode.")
    has_key = bool(getattr(settings, key_attr))
    has_plugin = _module_available(plugin)
    if has_key and has_plugin:
        return Check(name, Status.READY, f"Key set and {plugin} importable.")
    missing = []
    if not has_key:
        missing.append(f"{key_attr.upper()} not set")
    if not has_plugin:
        missing.append(f"{plugin} not installed ([voice] extra)")
    return Check(name, Status.MISSING, "; ".join(missing) + ".")


def _check_api_service() -> Check:
    """Check the FastAPI HTTP service deps (``crossexam-api``).

    The service needs ``fastapi`` (+ ``uvicorn``/``python-multipart``) to serve
    at all, plus ``pdfplumber`` for the PDF upload path — all from the ``[api]``
    extra. This is independent of keys: it reports whether the service *can run*.
    """
    has_fastapi = _module_available("fastapi")
    has_pdfplumber = _module_available("pdfplumber")
    if has_fastapi and has_pdfplumber:
        return Check(
            "HTTP API (fastapi)",
            Status.READY,
            "fastapi + pdfplumber importable; `crossexam-api` can serve /config, "
            "/healthz, /token and /documents.",
        )
    missing = []
    if not has_fastapi:
        missing.append("fastapi not installed")
    if not has_pdfplumber:
        missing.append("pdfplumber not installed (PDF upload path)")
    return Check(
        "HTTP API (fastapi)",
        Status.MISSING,
        "; ".join(missing) + " — pip install '.[api,voice]'.",
    )


def _check_token_minting(settings: Settings) -> Check:
    """Check the ``POST /token`` leg: ``livekit-api`` import + LiveKit creds.

    Token minting needs the ``livekit-api`` package (the ``[api]`` extra) AND the
    LiveKit credentials. Without creds the endpoint returns a clear 503, so we
    surface that as MISSING when the real path is requested. In mock mode (no
    LiveKit creds and ``use_mocks`` resolved true) tokens are not needed, so the
    row is MOCK rather than MISSING — the API still serves /config and /documents.
    """
    has_lib = _module_available("livekit.api")
    has_creds = settings.has_livekit_credentials
    if settings.use_mocks and not has_creds:
        return Check(
            "Token minting (/token)",
            Status.MOCK,
            "Mock mode: no LiveKit creds -> POST /token returns 503; frontend stays mock.",
        )
    if has_lib and has_creds:
        return Check(
            "Token minting (/token)",
            Status.READY,
            "livekit-api importable and LIVEKIT_URL/API_KEY/API_SECRET present.",
        )
    missing = []
    if not has_lib:
        missing.append("livekit-api not installed ([api] extra)")
    if not has_creds:
        missing.append("LiveKit credentials incomplete (POST /token -> 503)")
    return Check("Token minting (/token)", Status.MISSING, "; ".join(missing) + ".")


def _check_silero() -> Check:
    """Check the Silero VAD plugin (no key needed; downloads weights lazily)."""
    if not LIVEKIT_AVAILABLE:
        return Check("VAD (silero)", Status.MOCK, "Voice pipeline not started offline.")
    if _module_available("livekit.plugins.silero"):
        return Check("VAD (silero)", Status.READY, "livekit-plugins-silero importable.")
    return Check(
        "VAD (silero)",
        Status.MISSING,
        "livekit-plugins-silero not installed ([voice] extra).",
    )


def _check_tracing(settings: Settings) -> Check:
    """Check observability tracing readiness (OpenTelemetry -> Langfuse).

    READY when OBS_ENABLED, the [obs] extra is importable AND Langfuse keys are
    present. Otherwise OFF — a zero-overhead no-op tracer is used and nothing is
    exported. Never MISSING: tracing is always optional.
    """
    if tracing_status(settings) == "READY":
        return Check(
            "Tracing (Langfuse)",
            Status.READY,
            "OBS_ENABLED, opentelemetry-sdk importable and Langfuse keys present; "
            "retrieve/publish spans export to Langfuse.",
        )
    reasons = []
    if not settings.obs_enabled:
        reasons.append("OBS_ENABLED is false")
    if not _module_available("opentelemetry.sdk"):
        reasons.append("opentelemetry-sdk not installed ([obs] extra)")
    if not settings.has_langfuse_credentials:
        reasons.append("LANGFUSE_PUBLIC_KEY/SECRET_KEY not set")
    return Check(
        "Tracing (Langfuse)",
        Status.OFF,
        ("No-op tracer — " + "; ".join(reasons) + ".") if reasons else "No-op tracer.",
    )


def _check_proactive(settings: Settings) -> Check:
    """Check proactive / ambient citation surfacing readiness (config toggle)."""
    if settings.proactive_enabled:
        return Check(
            "Proactive surfacing",
            Status.READY,
            "PROACTIVE_ENABLED: claims (not questions) confidently supported by "
            f"the document are surfaced unprompted (faithfulness>="
            f"{settings.faithfulness_threshold:.2f}).",
        )
    return Check(
        "Proactive surfacing",
        Status.OFF,
        "PROACTIVE_ENABLED is false; citations are only published on request.",
    )


def _check_multihop(settings: Settings) -> Check:
    """Check multi-hop / contradiction routing readiness (config toggle).

    The path is pure and dependency-free (deterministic decomposer + RRF fusion +
    contradiction detector), so readiness is purely whether the toggle is on.
    """
    if settings.multihop_enabled:
        return Check(
            "Multi-hop routing",
            Status.READY,
            "MULTIHOP_ENABLED: contradiction/multi-hop questions decompose into "
            f"hops, retrieve per sub-query (per_hop_k={settings.multihop_per_hop_k}) "
            "across documents, fuse and flag contradictions.",
        )
    return Check(
        "Multi-hop routing",
        Status.OFF,
        "MULTIHOP_ENABLED is false; every turn uses the single-query path.",
    )


def _check_memory() -> Check:
    """Check conversation-memory readiness (feat 5 dedupe/recall).

    The default store is an in-process dict — always available, no deps, no
    network — so this is READY whenever the package imports.
    """
    if _module_available("crossexam_backend.memory"):
        return Check(
            "Conversation memory",
            Status.READY,
            "In-process ConversationMemory active: a citation surfaced once per "
            "session is recalled ('as we saw on page N') instead of re-snapped.",
        )
    return Check(
        "Conversation memory",
        Status.MISSING,
        "crossexam_backend.memory not importable.",
    )


def run_checks(settings: Settings, *, probe: bool = False) -> list[Check]:
    """Run every preflight check against ``settings``.

    No network calls are made unless ``probe`` is ``True``, in which case the
    Moss retrieval check makes a single live ``load_index`` call to confirm the
    configured index is reachable.
    """
    checks = [_check_retrieval(settings, probe=probe), _check_livekit(settings)]
    for leg, provider_attr, key_attr, plugin in _PROVIDERS:
        checks.append(_check_provider(settings, leg, provider_attr, key_attr, plugin))
    checks.append(_check_silero())
    # HTTP API service readiness (deps + token-minting leg).
    checks.append(_check_api_service())
    checks.append(_check_token_minting(settings))
    # Technical-depth feature readiness (never fails the run).
    checks.append(_check_tracing(settings))
    checks.append(_check_proactive(settings))
    # Depth-v2 feature readiness (multi-hop routing + conversation memory).
    checks.append(_check_multihop(settings))
    checks.append(_check_memory())
    return checks


def overall_mode(checks: list[Check]) -> str:
    """Summarise the run: ``MOCK`` if any mock path is active, else ``LIVE``."""
    if any(c.status is Status.MOCK for c in checks):
        return "MOCK"
    return "LIVE"


def format_table(checks: list[Check]) -> str:
    """Render the checks as a fixed-width text table."""
    name_w = max((len(c.name) for c in checks), default=12)
    status_w = max(len(s.value) for s in Status)
    header = f"{'SUBSYSTEM'.ljust(name_w)}  {'STATUS'.ljust(status_w)}  DETAIL"
    sep = "-" * len(header)
    rows = [
        f"{c.name.ljust(name_w)}  {c.status.value.ljust(status_w)}  {c.detail}"
        for c in checks
    ]
    return "\n".join([header, sep, *rows])


def main() -> int:
    """Print the preflight table. Returns ``1`` if anything is MISSING, else ``0``.

    Accepts ``--probe`` / ``--live`` to additionally make a single live Moss
    ``load_index`` call confirming the configured index is reachable. Without it,
    no network calls are made and the Moss row reports index existence as
    unverified.
    """
    parser = argparse.ArgumentParser(
        prog="crossexam-doctor",
        description="CrossExam backend preflight (config + dependency table).",
    )
    parser.add_argument(
        "--probe",
        "--live",
        dest="probe",
        action="store_true",
        help="Make a live Moss load_index call to confirm the index is reachable.",
    )
    # parse_known_args (not parse_args) so the doctor stays callable in contexts
    # where sys.argv carries unrelated flags (e.g. under pytest's `-q`).
    args, _unknown = parser.parse_known_args()

    settings = get_settings()
    checks = run_checks(settings, probe=args.probe)
    mode = overall_mode(checks)

    print("CrossExam backend preflight")
    print(
        f"resolved mode: {mode}  (use_mocks={settings.use_mocks}, "
        f"probe={args.probe})"
    )
    print()
    print(format_table(checks))
    print()

    missing = [c for c in checks if c.status is Status.MISSING]
    if missing:
        print(f"{len(missing)} subsystem(s) MISSING — see DETAIL above.")
        return 1
    print("All subsystems READY or MOCK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
