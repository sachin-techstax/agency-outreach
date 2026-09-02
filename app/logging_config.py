"""Central logging configuration for the agency outreach pipeline.

Uses the Python standard library ``logging`` module. Operational diagnostics
must go through :func:`get_logger` (or ``logging`` directly) rather than ad-hoc
``print`` statements so that output is consistent, timestamped, level-aware and
redirectable to a file when needed.

Configuration is read from environment variables:

- ``LOG_LEVEL``  - one of DEBUG/INFO/WARNING/ERROR (default: INFO)
- ``LOG_FILE``   - optional path to a rotating log file (default: empty/console only)

Call :func:`configure_logging` once at process start (the CLI does this). Other
modules should call :func:`get_logger` to obtain a named logger.
"""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Single shared formatter so console and file output look identical.
_FORMAT = "%(asctime)s | %(levelname)-5s | %(name)s | %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"
_CONFIGURED = False

# Module names that are allowed to emit through the root configuration. We keep
# this explicit so unrelated libraries do not flood the console.
_MANAGED_LOGGERS = (
    "pipeline",
    "search",
    "scrape",
    "scoring",
    "contacts",
    "llm",
    "db",
    "cli",
    "gmail",
)


def _level_from_env(value: str | None) -> int:
    name = (value or "INFO").strip().upper()
    return getattr(logging, name, logging.INFO)


def configure_logging(level: str | int | None = None, log_file: str | None = None) -> None:
    """Configure root logging for the process.

    Safe to call multiple times: subsequent calls reconfigure handlers cleanly.

    Parameters
    ----------
    level:
        Explicit level override. When ``None`` the ``LOG_LEVEL`` env var is
        used (default INFO). Accepts either a string name or an int level.
    log_file:
        Explicit file path override. When ``None`` the ``LOG_FILE`` env var is
        used; an empty value means console only.
    """
    global _CONFIGURED

    if isinstance(level, int):
        level_int = level
    elif isinstance(level, str):
        level_int = getattr(logging, level.upper(), logging.INFO)
    else:
        level_int = _level_from_env(os.getenv("LOG_LEVEL"))

    file_path = log_file if log_file is not None else os.getenv("LOG_FILE", "").strip()

    root = logging.getLogger()
    # Remove previous handlers that we installed so re-configuration (e.g.
    # --verbose) is clean. We deliberately leave foreign handlers (such as
    # pytest's LogCaptureHandler) in place so test fixtures keep working.
    for h in list(root.handlers):
        if getattr(h, "_agency_outreach_owned", False):
            root.removeHandler(h)
            try:
                h.close()
            except Exception:
                pass

    root.setLevel(logging.DEBUG)  # handlers filter; root lets everything through
    formatter = logging.Formatter(_FORMAT, datefmt=_DATEFMT)

    console = logging.StreamHandler()
    console.setLevel(level_int)
    console.setFormatter(formatter)
    console._agency_outreach_owned = True  # type: ignore[attr-defined]
    root.addHandler(console)

    if file_path:
        try:
            path = Path(file_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                str(path), maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
            )
            # File handler respects the configured LOG_LEVEL (R1-6). A separate
            # file-specific level can be added in the future if needed.
            file_handler.setLevel(level_int)
            file_handler.setFormatter(formatter)
            file_handler._agency_outreach_owned = True  # type: ignore[attr-defined]
            root.addHandler(file_handler)
        except Exception as exc:
            # File logging is optional; keep console logging active and emit a
            # WARNING so the operator knows file logging is unavailable. Do not
            # expose sensitive configuration beyond the requested path.
            logging.getLogger("logging_config").warning(
                "File logging could not be initialized for path %r: %s. "
                "Continuing with console logging only.",
                file_path,
                exc,
            )

    # Quiet noisy third-party loggers that are not part of our managed set.
    for noisy in ("httpx", "httpcore", "openai", "urllib3", "googleapiclient", "google_auth_httplib2"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # Some versions of the openai SDK emit under a renamed httpx logger.
    for extra in ("httpx2", "httpcore2"):
        logging.getLogger(extra).setLevel(logging.WARNING)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a named logger.

    The returned logger propagates to the root logger configured by
    :func:`configure_logging`. If logging has not been configured yet a
    sensible default (INFO to console) is installed so library-style callers
    still see output.
    """
    if not _CONFIGURED:
        configure_logging()
    return logging.getLogger(name)
