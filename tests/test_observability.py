"""Tests for the logging configuration and search-layer observability."""
from __future__ import annotations

import logging
import time
from unittest.mock import patch, MagicMock

import httpx
import pytest

from app.logging_config import configure_logging, get_logger
from app.search import SearchHit, search_serper
from app.config import settings


def test_configure_logging_sets_level():
    configure_logging(level="DEBUG")
    root = logging.getLogger()
    console_handler = next(h for h in root.handlers if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler))
    assert console_handler.level == logging.DEBUG


def test_configure_logging_default_info():
    configure_logging(level="INFO")
    root = logging.getLogger()
    console_handler = next(h for h in root.handlers if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler))
    assert console_handler.level == logging.INFO


def test_get_logger_returns_named_logger():
    log = get_logger("test_module")
    assert log.name == "test_module"


def test_search_logs_query_and_results(caplog):
    object.__setattr__(settings, "serper_api_key", "fake-key")
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "organic": [
            {"title": "AI Co", "link": "https://ai.co", "snippet": "s"},
            {"title": "ML Co", "link": "https://ml.co", "snippet": "s"},
        ]
    }
    fake_response.raise_for_status = MagicMock()

    with patch("app.search.httpx.post", return_value=fake_response):
        with caplog.at_level(logging.INFO, logger="search"):
            hits = search_serper("test query", num=10)

    assert len(hits) == 2
    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "test query" in messages
    assert "2 results returned" in messages


def test_search_logs_auth_error(caplog):
    object.__setattr__(settings, "serper_api_key", "fake-key")
    fake_response = MagicMock()
    fake_response.status_code = 401
    fake_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "401", request=MagicMock(), response=fake_response
    )

    with patch("app.search.httpx.post", return_value=fake_response):
        with caplog.at_level(logging.ERROR, logger="search"):
            with pytest.raises(httpx.HTTPStatusError):
                search_serper("q")

    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "401" in messages


def test_search_does_not_log_api_key(caplog):
    secret = "do-not-leak-this-key"
    object.__setattr__(settings, "serper_api_key", secret)
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {"organic": []}
    fake_response.raise_for_status = MagicMock()

    with patch("app.search.httpx.post", return_value=fake_response):
        with caplog.at_level(logging.DEBUG, logger="search"):
            search_serper("q")

    full_text = " ".join(r.getMessage() for r in caplog.records)
    assert secret not in full_text
