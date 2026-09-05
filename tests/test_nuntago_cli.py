from __future__ import annotations

import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from app import cli as cli_mod
from app.config import settings
from app.db import upsert_lead

runner = CliRunner()


@pytest.fixture(autouse=True)
def restore_settings():
    original_db = settings.db_path
    original_demo = settings.nuntago_demo_mode
    original_serper = settings.serper_api_key
    original_openai = settings.openai_api_key
    original_demo_env = os.environ.get("NUNTAGO_DEMO_MODE")
    original_nuntago_demo_env = os.environ.get("NUNTAGO_DEMO_MODE")
    yield
    object.__setattr__(settings, "db_path", original_db)
    object.__setattr__(settings, "nuntago_demo_mode", original_demo)
    object.__setattr__(settings, "serper_api_key", original_serper)
    object.__setattr__(settings, "openai_api_key", original_openai)
    if original_demo_env is None:
        os.environ.pop("NUNTAGO_DEMO_MODE", None)
    else:
        os.environ["NUNTAGO_DEMO_MODE"] = original_demo_env
    if original_nuntago_demo_env is None:
        os.environ.pop("NUNTAGO_DEMO_MODE", None)
    else:
        os.environ["NUNTAGO_DEMO_MODE"] = original_nuntago_demo_env


def test_help_is_branded_nuntago():
    result = runner.invoke(cli_mod.app, ["--help"])

    assert result.exit_code == 0
    assert "Nuntago" in result.stdout
    assert "Agency Outreach" not in result.stdout


def test_version_command():
    result = runner.invoke(cli_mod.app, ["version"])

    assert result.exit_code == 0
    assert "Nuntago 0.1.0" in result.stdout


def test_status_initializes_fresh_database(tmp_path: Path):
    object.__setattr__(settings, "db_path", tmp_path / "status.db")

    result = runner.invoke(cli_mod.app, ["status"])

    assert result.exit_code == 0
    assert "Nuntago" in result.stdout
    assert "Total leads" in result.stdout
    assert (tmp_path / "status.db").exists()


def test_status_counts_suppressed_and_retryable(tmp_path: Path):
    object.__setattr__(settings, "db_path", tmp_path / "counts.db")
    upsert_lead({
        "company": "Drafted Co",
        "domain": "drafted.example",
        "website": "https://drafted.example",
        "score": 81,
        "status": "drafted",
    })
    upsert_lead({
        "company": "Fresh Co",
        "domain": "fresh.example",
        "website": "https://fresh.example",
        "score": 74,
        "status": "qualified",
    })

    result = runner.invoke(cli_mod.app, ["status"])

    assert result.exit_code == 0
    assert "Fresh / retryable" in result.stdout
    assert "Drafted" in result.stdout


def test_doctor_never_prints_secret_values(tmp_path: Path):
    object.__setattr__(settings, "db_path", tmp_path / "doctor.db")
    object.__setattr__(settings, "serper_api_key", "super-secret-serper-value")
    object.__setattr__(settings, "openai_api_key", "super-secret-openai-value")

    result = runner.invoke(cli_mod.app, ["doctor"])

    assert result.exit_code == 0
    assert "super-secret-serper-value" not in result.stdout
    assert "super-secret-openai-value" not in result.stdout
    assert "Nuntago doctor" in result.stdout


def test_doctor_strict_fails_without_serper(tmp_path: Path):
    object.__setattr__(settings, "db_path", tmp_path / "doctor-strict.db")
    object.__setattr__(settings, "serper_api_key", "")

    result = runner.invoke(cli_mod.app, ["doctor", "--strict"])

    assert result.exit_code == 1


def test_demo_mode_blocks_gmail_cli_action(tmp_path: Path):
    object.__setattr__(settings, "db_path", tmp_path / "demo.db")
    object.__setattr__(settings, "nuntago_demo_mode", True)

    result = runner.invoke(cli_mod.app, ["gmail-drafts"])

    assert result.exit_code != 0
    assert "disabled in Nuntago demo mode" in result.stdout


def test_serve_demo_sets_read_only_mode(monkeypatch):
    calls = {}

    def fake_run(app, **kwargs):
        calls["app"] = app
        calls.update(kwargs)

    import uvicorn

    monkeypatch.setattr(uvicorn, "run", fake_run)
    object.__setattr__(settings, "nuntago_demo_mode", False)

    result = runner.invoke(
        cli_mod.app,
        ["serve", "--demo", "--host", "127.0.0.1", "--port", "9090"],
    )

    assert result.exit_code == 0
    assert settings.nuntago_demo_mode is True
    assert os.environ["NUNTAGO_DEMO_MODE"] == "true"
    assert os.environ["NUNTAGO_DEMO_MODE"] == "true"
    assert calls["app"] == "app.api:app"
    assert calls["host"] == "127.0.0.1"
    assert calls["port"] == 9090
    assert "Demo safety is active" in result.stdout


@pytest.mark.parametrize(
    "command",
    [
        ["run", "--limit", "1"],
        ["discover", "--limit", "1"],
        ["approve", "1"],
        ["reject", "1"],
        ["do-not-contact", "1"],
        ["allow-contact", "1"],
        ["mark-sent", "1"],
        ["followup-draft", "1"],
    ],
)
def test_demo_mode_blocks_risky_cli_commands(command):
    object.__setattr__(settings, "nuntago_demo_mode", True)

    result = runner.invoke(cli_mod.app, command)

    assert result.exit_code != 0
    assert "disabled in Nuntago demo mode" in result.stdout
