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

import importlib.util
from dataclasses import dataclass
from enum import Enum

from crossexam_backend.agent import LIVEKIT_AVAILABLE
from crossexam_backend.config import Settings, get_settings


class Status(str, Enum):
    """Tri-state status for a single preflight check."""

    READY = "READY"
    MISSING = "MISSING"
    MOCK = "MOCK"


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
    for name in ("inferedge_moss", "moss"):
        if _module_available(name):
            return name
    return None


def _check_retrieval(settings: Settings) -> Check:
    """Check the retrieval backend (Moss vs MockIndex)."""
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
            "(pip install inferedge-moss).",
        )
    return Check(
        "Moss retrieval",
        Status.READY,
        f"Credentials set; SDK importable as {moss_module!r}; "
        f"index={settings.moss_index_name!r}.",
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


def run_checks(settings: Settings) -> list[Check]:
    """Run every preflight check against ``settings`` (no network calls)."""
    checks = [_check_retrieval(settings), _check_livekit(settings)]
    for leg, provider_attr, key_attr, plugin in _PROVIDERS:
        checks.append(_check_provider(settings, leg, provider_attr, key_attr, plugin))
    checks.append(_check_silero())
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
    """Print the preflight table. Returns ``1`` if anything is MISSING, else ``0``."""
    settings = get_settings()
    checks = run_checks(settings)
    mode = overall_mode(checks)

    print("CrossExam backend preflight")
    print(f"resolved mode: {mode}  (use_mocks={settings.use_mocks})")
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
