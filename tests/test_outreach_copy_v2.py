"""Tests for outreach copy quality V2: capability-led proof selection.

These tests verify the new outreach-safe proof metadata, the rewritten
prompt, the private-project-name guard, the deterministic fallback, and
that existing draft-freshness / optimistic-concurrency / approval /
Gmail safety behavior is preserved.

No real OpenAI, Gmail, Serper, or network calls are made.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app import llm as llm_mod
from app import pipeline as pipeline_mod
from app.config import settings


@pytest.fixture(autouse=True)
def restore_openai_key():
    """Restore openai_api_key after each test (frozen dataclass)."""
    original = settings.openai_api_key
    yield
    object.__setattr__(settings, "openai_api_key", original)


def _set_openai_key(value: str):
    object.__setattr__(settings, "openai_api_key", value)


# ---------------------------------------------------------------------------
# Metadata structure
# ---------------------------------------------------------------------------

def test_forge_crew_is_non_nameable_without_public_url():
    """Forge Crew is configured as non-nameable with no public destination."""
    meta = llm_mod.OUTREACH_PROOF["Forge Crew"]
    assert meta["nameable"] is False
    assert meta["public_url"] == ""


def test_aegis_is_non_nameable_without_public_url():
    """Aegis is also non-nameable (no public destination configured)."""
    meta = llm_mod.OUTREACH_PROOF["Aegis"]
    assert meta["nameable"] is False
    assert meta["public_url"] == ""


def test_wingerx_is_nameable_with_public_url():
    meta = llm_mod.OUTREACH_PROOF["WingerX"]
    assert meta["nameable"] is True
    assert meta["public_url"] == "https://wingerx.com/"


def test_gradewise_is_nameable_with_public_url():
    meta = llm_mod.OUTREACH_PROOF["GradeWise"]
    assert meta["nameable"] is True
    assert meta["public_url"] == "https://gradewise.quest/"


def test_forbidden_names_are_exactly_non_nameable_projects():
    """_FORBIDDEN_NAMES must be exactly the non-nameable projects."""
    expected = sorted(
        name for name, meta in llm_mod.OUTREACH_PROOF.items()
        if not meta["nameable"]
    )
    assert sorted(llm_mod._FORBIDDEN_NAMES) == expected
    assert "Forge Crew" in llm_mod._FORBIDDEN_NAMES
    assert "Aegis" in llm_mod._FORBIDDEN_NAMES
    assert "WingerX" not in llm_mod._FORBIDDEN_NAMES
    assert "GradeWise" not in llm_mod._FORBIDDEN_NAMES


# ---------------------------------------------------------------------------
# Non-nameable capability may still be supplied as context
# ---------------------------------------------------------------------------

def test_non_nameable_capability_still_supplied_as_context():
    """The primary internal proof's capability description is supplied to the
    prompt even when the project is non-nameable."""
    prompt = llm_mod._build_outreach_prompt(
        "TestCo", "fit reason", "Forge Crew", "angle"
    )
    # The capability description for Forge Crew must appear.
    assert llm_mod.OUTREACH_PROOF["Forge Crew"]["description"] in prompt
    # But the prompt must explicitly say the project is NOT required by name.
    assert "NOT required to mention this project by name" in prompt


# ---------------------------------------------------------------------------
# Prompt content
# ---------------------------------------------------------------------------

def test_prompt_says_selected_project_is_optional():
    prompt = llm_mod._build_outreach_prompt(
        "TestCo", "fit", "WingerX", "angle"
    )
    assert "internal relevance signal" in prompt.lower()
    assert "NOT required to mention this project by name" in prompt


def test_prompt_contains_full_outreach_safe_proof_bank():
    prompt = llm_mod._build_outreach_prompt(
        "TestCo", "fit", "WingerX", "angle"
    )
    # Nameable projects must appear in the proof bank section.
    assert "WingerX" in prompt
    assert "GradeWise" in prompt
    # Their public URLs must be present.
    assert "https://wingerx.com/" in prompt
    assert "https://gradewise.quest/" in prompt


def test_prompt_allows_zero_named_projects():
    """The prompt must explicitly state proof is optional (zero allowed)."""
    prompt = llm_mod._build_outreach_prompt(
        "TestCo", "fit", "WingerX", "angle"
    )
    assert "Proof is optional" in prompt


def test_prompt_allows_at_most_two_named_public_proofs():
    prompt = llm_mod._build_outreach_prompt(
        "TestCo", "fit", "WingerX", "angle"
    )
    assert "at most two named projects" in prompt.lower()


def test_prompt_forbids_non_nameable_projects():
    prompt = llm_mod._build_outreach_prompt(
        "TestCo", "fit", "WingerX", "angle"
    )
    assert "Forbidden names" in prompt
    assert "Forge Crew" in prompt
    assert "Aegis" in prompt


def test_prompt_forbids_architecture_phrases():
    prompt = llm_mod._build_outreach_prompt(
        "TestCo", "fit", "WingerX", "angle"
    )
    assert "local-first multi-agent orchestrator" in prompt
    assert "deterministic scanning" in prompt


def test_prompt_includes_sender_capabilities():
    prompt = llm_mod._build_outreach_prompt(
        "TestCo", "fit", "WingerX", "angle"
    )
    assert "agentic systems" in prompt.lower()
    assert "RAG" in prompt
    assert "automation" in prompt
    assert "production AI backends" in prompt


def test_prompt_word_count_guideline():
    prompt = llm_mod._build_outreach_prompt(
        "TestCo", "fit", "WingerX", "angle"
    )
    assert "60 to 95 words" in prompt
    assert "3 or 4 short sentences" in prompt


# ---------------------------------------------------------------------------
# Deterministic fallback
# ---------------------------------------------------------------------------

def test_deterministic_fallback_does_not_name_forge_crew(tmp_path, monkeypatch):
    """When OpenAI is not configured, the fallback must not name Forge Crew."""
    _set_openai_key("")
    subject, body = llm_mod.draft_outreach(
        "LaunchPad Lab", "fit", "Forge Crew", "AI agents and client delivery"
    )
    assert "Forge Crew" not in subject
    assert "Forge Crew" not in body
    assert "Aegis" not in subject
    assert "Aegis" not in body


def test_deterministic_fallback_is_capability_led():
    """The fallback positions the sender as senior engineering capacity."""
    body = llm_mod._capability_fallback_body(
        "TestCo", "fit", "angle"
    )
    assert "senior" in body.lower()
    assert "capacity" in body.lower()
    # No project names at all.
    for name in llm_mod.OUTREACH_PROOF:
        assert name not in body


def test_deterministic_fallback_subject_is_not_salesy():
    subject = llm_mod._capability_fallback_subject("TestCo")
    assert "White-label" not in subject
    assert "Partnership" not in subject
    assert subject == "Extra AI delivery capacity"


def test_deterministic_fallback_has_easy_question():
    body = llm_mod._capability_fallback_body("TestCo", "fit", "angle")
    assert "?" in body
    assert "Do you ever" in body


# ---------------------------------------------------------------------------
# Private-project-name guard with retry/fallback
# ---------------------------------------------------------------------------

def _mock_response(text: str):
    """Create a mock OpenAI response object."""
    resp = MagicMock()
    resp.output_text = text
    return resp


def test_generated_response_with_forge_crew_triggers_retry(monkeypatch):
    """If the first response names Forge Crew, a retry must be attempted."""
    _set_openai_key("test-key")

    first = _mock_response(json.dumps({
        "subject": "Forge Crew for your team",
        "body": "Forge Crew is a great orchestrator for your agency.",
    }))
    second = _mock_response(json.dumps({
        "subject": "Extra AI delivery capacity",
        "body": "I work across agentic systems and can plug in when capacity is tight.",
    }))

    client = MagicMock()
    client.responses.create.side_effect = [first, second]
    monkeypatch.setattr(llm_mod, "_client", lambda: client)

    subject, body = llm_mod.draft_outreach("TestCo", "fit", "Forge Crew", "angle")
    assert "Forge Crew" not in subject
    assert "Forge Crew" not in body
    assert client.responses.create.call_count == 2


def test_successful_retry_without_forge_crew_is_accepted(monkeypatch):
    """A retry that removes the forbidden name is accepted as output."""
    _set_openai_key("test-key")

    first = _mock_response(json.dumps({
        "subject": "Aegis review for you",
        "body": "Aegis can help your team with code review.",
    }))
    second = _mock_response(json.dumps({
        "subject": "Overflow AI engineering",
        "body": "I build agentic systems and can help when delivery is tight.",
    }))

    client = MagicMock()
    client.responses.create.side_effect = [first, second]
    monkeypatch.setattr(llm_mod, "_client", lambda: client)

    subject, body = llm_mod.draft_outreach("TestCo", "fit", "Aegis", "angle")
    assert "Aegis" not in subject
    assert "Aegis" not in body
    assert "agentic" in body.lower()


def test_two_failed_attempts_use_safe_fallback(monkeypatch):
    """If both attempts contain forbidden names, use the safe fallback."""
    _set_openai_key("test-key")

    first = _mock_response(json.dumps({
        "subject": "Forge Crew for you",
        "body": "Forge Crew orchestrator is great.",
    }))
    second = _mock_response(json.dumps({
        "subject": "Aegis review",
        "body": "Aegis code review agent.",
    }))

    client = MagicMock()
    client.responses.create.side_effect = [first, second]
    monkeypatch.setattr(llm_mod, "_client", lambda: client)

    subject, body = llm_mod.draft_outreach("TestCo", "fit", "Forge Crew", "angle")
    # Fallback used — no forbidden names.
    assert "Forge Crew" not in subject
    assert "Aegis" not in subject
    assert "Forge Crew" not in body
    assert "Aegis" not in body
    # Fallback subject.
    assert subject == "Extra AI delivery capacity"
    # Fallback body content.
    assert "senior" in body.lower()
    assert client.responses.create.call_count == 2


def test_forbidden_name_in_subject_triggers_retry(monkeypatch):
    """Forbidden name in subject only (not body) still triggers retry."""
    _set_openai_key("test-key")

    first = _mock_response(json.dumps({
        "subject": "Forge Crew support",
        "body": "I build agentic systems and can help.",
    }))
    second = _mock_response(json.dumps({
        "subject": "Extra AI delivery capacity",
        "body": "I build agentic systems and can help when delivery is tight.",
    }))

    client = MagicMock()
    client.responses.create.side_effect = [first, second]
    monkeypatch.setattr(llm_mod, "_client", lambda: client)

    subject, body = llm_mod.draft_outreach("TestCo", "fit", "Forge Crew", "angle")
    assert "Forge Crew" not in subject
    assert client.responses.create.call_count == 2


# ---------------------------------------------------------------------------
# Public proof may be named
# ---------------------------------------------------------------------------

def test_public_proof_may_be_named(monkeypatch):
    """When the model names a nameable project, it is accepted."""
    _set_openai_key("test-key")

    resp = _mock_response(json.dumps({
        "subject": "Extra AI delivery capacity",
        "body": "I work across agents, RAG, automation and AI backends, with "
                "WingerX being one public example of that work. Do you ever use "
                "external senior AI engineering capacity?",
    }))

    client = MagicMock()
    client.responses.create.return_value = resp
    monkeypatch.setattr(llm_mod, "_client", lambda: client)

    subject, body = llm_mod.draft_outreach("TestCo", "fit", "WingerX", "angle")
    assert "WingerX" in body  # nameable, so allowed
    assert client.responses.create.call_count == 1  # no retry needed


def test_two_public_proofs_may_be_named(monkeypatch):
    """The model may name up to two public projects."""
    _set_openai_key("test-key")

    resp = _mock_response(json.dumps({
        "subject": "Overflow AI engineering",
        "body": "My recent work includes WingerX on AI automation and GradeWise "
                "on production AI product/backend delivery. Do you ever bring in "
                "external senior AI engineers?",
    }))

    client = MagicMock()
    client.responses.create.return_value = resp
    monkeypatch.setattr(llm_mod, "_client", lambda: client)

    subject, body = llm_mod.draft_outreach("TestCo", "fit", "WingerX", "angle")
    assert "WingerX" in body
    assert "GradeWise" in body
    assert client.responses.create.call_count == 1  # no retry


# ---------------------------------------------------------------------------
# Existing JSON parsing still works
# ---------------------------------------------------------------------------

def test_strict_json_parsing_still_works(monkeypatch):
    """The existing JSON parsing + recovery path still functions."""
    _set_openai_key("test-key")

    # Response with surrounding whitespace (no code fence).
    raw = '  {"subject": "Extra capacity", "body": "I can help with AI delivery."}  '
    resp = _mock_response(raw)

    client = MagicMock()
    client.responses.create.return_value = resp
    monkeypatch.setattr(llm_mod, "_client", lambda: client)

    subject, body = llm_mod.draft_outreach("TestCo", "fit", "WingerX", "angle")
    assert subject == "Extra capacity"
    assert body == "I can help with AI delivery."


def test_json_recovery_from_surrounding_text(monkeypatch):
    """JSON embedded in surrounding text is still recovered."""
    _set_openai_key("test-key")

    raw = 'Here is the email:\n{"subject": "Overflow AI", "body": "I can help."}\nDone.'
    resp = _mock_response(raw)

    client = MagicMock()
    client.responses.create.return_value = resp
    monkeypatch.setattr(llm_mod, "_client", lambda: client)

    subject, body = llm_mod.draft_outreach("TestCo", "fit", "WingerX", "angle")
    assert subject == "Overflow AI"
    assert body == "I can help."


# ---------------------------------------------------------------------------
# Draft freshness + optimistic concurrency + approval/Gmail safety preserved
# ---------------------------------------------------------------------------

def test_draft_regeneration_still_clears_draft_stale(tmp_path, monkeypatch):
    """Regeneration must still clear draft_stale after successful generation."""
    from app.db import init_db, upsert_lead, get_lead
    from app.pipeline import regenerate_draft

    # Use the existing test infrastructure from test_run_history.
    import tests.test_run_history as trh
    trh._live_mode(tmp_path)
    init_db()
    lead_id = upsert_lead(trh._stale_lead(status="drafted"))
    monkeypatch.setattr(pipeline_mod, "draft_outreach", lambda *a: ("Fresh subject", "Fresh body"))

    result = regenerate_draft(lead_id)
    assert result["regenerated"] is True
    assert result["draft_stale"] is False

    row = get_lead(lead_id)
    assert bool(row["draft_stale"]) is False
    assert row["subject"] == "Fresh subject"


def test_optimistic_concurrency_conflict_still_returns_409(tmp_path, monkeypatch):
    """The R2 optimistic concurrency guard must still fire on concurrent change."""
    from app.db import init_db, upsert_lead, get_lead, update_lead
    from app.pipeline import regenerate_draft, RegenerationBlocked
    import tests.test_run_history as trh

    trh._live_mode(tmp_path)
    init_db()
    lead_id = upsert_lead(trh._stale_lead(status="drafted"))

    def mutate():
        update_lead(lead_id, status="do_not_contact")

    def fake_draft(*a):
        mutate()
        return ("Concurrent subject", "Concurrent body")

    monkeypatch.setattr(pipeline_mod, "draft_outreach", fake_draft)

    with pytest.raises(RegenerationBlocked) as exc_info:
        regenerate_draft(lead_id)
    assert exc_info.value.status_code == 409

    row = get_lead(lead_id)
    assert row["status"] == "do_not_contact"
    assert bool(row["draft_stale"]) is True


def test_stale_approval_still_blocked(tmp_path):
    """Stale drafts cannot be approved (R1-9)."""
    from app.db import init_db, upsert_lead
    from fastapi.testclient import TestClient
    from app import api as api_mod
    import tests.test_run_history as trh

    trh._live_mode(tmp_path)
    init_db()
    lead_id = upsert_lead(trh._stale_lead(status="drafted"))

    client = TestClient(api_mod.app)
    resp = client.post(f"/api/leads/{lead_id}/approve")
    assert resp.status_code == 409


def test_stale_gmail_creation_still_blocked(tmp_path):
    """Stale drafts cannot create Gmail drafts (R1-10)."""
    from app.db import init_db, upsert_lead
    from fastapi.testclient import TestClient
    from app import api as api_mod
    import tests.test_run_history as trh

    trh._live_mode(tmp_path)
    init_db()
    lead_id = upsert_lead(trh._stale_lead(status="approved"))

    client = TestClient(api_mod.app)
    resp = client.post(f"/api/leads/{lead_id}/gmail-draft")
    assert resp.status_code == 409


def test_demo_mode_blocks_regeneration(tmp_path):
    """Demo mode must block regeneration before any LLM work (R1-12)."""
    from app.db import init_db, upsert_lead
    from fastapi.testclient import TestClient
    from app import api as api_mod
    from app.config import settings as cfg_settings
    import tests.test_run_history as trh

    trh._live_mode(tmp_path)
    object.__setattr__(cfg_settings, "pactsignal_demo_mode", True)
    init_db()
    lead_id = upsert_lead(trh._stale_lead(status="drafted"))

    client = TestClient(api_mod.app)
    resp = client.post(f"/api/leads/{lead_id}/regenerate-draft")
    assert resp.status_code == 403
    object.__setattr__(cfg_settings, "pactsignal_demo_mode", False)


# ---------------------------------------------------------------------------
# No automatic sending
# ---------------------------------------------------------------------------

def test_no_automatic_send_behavior_introduced():
    """draft_outreach must not call any Gmail/send logic."""
    import inspect
    source = inspect.getsource(llm_mod.draft_outreach)
    assert "create_draft" not in source
    assert "send" not in source.lower()
    assert "gmail" not in source.lower()


# ---------------------------------------------------------------------------
# R1-1 / R1-2: Case-insensitive normalized private project detection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("variant", [
    "Forge Crew",
    "forge crew",
    "FORGE CREW",
    "Forge-Crew",
    "Forge   Crew",
    "Forge.Crew",
    "Forge_Crew",
])
def test_forge_crew_variants_detected(variant):
    """All case/separator variants of Forge Crew must be detected."""
    result = llm_mod._contains_forbidden_project(variant)
    assert result is not None
    assert result == "Forge Crew"


@pytest.mark.parametrize("variant", [
    "Aegis",
    "aegis",
    "AEGIS",
    "Aegis-Review",
    "aegis code review",
])
def test_aegis_variants_detected(variant):
    """All case/separator variants of Aegis must be detected."""
    result = llm_mod._contains_forbidden_project(variant)
    assert result is not None
    assert result == "Aegis"


def test_forbidden_variant_in_subject_triggers_retry(monkeypatch):
    """A forbidden variant in the subject triggers the corrective retry."""
    _set_openai_key("test-key")

    first = _mock_response(json.dumps({
        "subject": "forge crew support for your team",
        "body": "I build agentic systems and can help.",
    }))
    second = _mock_response(json.dumps({
        "subject": "Extra AI delivery capacity",
        "body": "I build agentic systems and can help when delivery is tight.",
    }))

    client = MagicMock()
    client.responses.create.side_effect = [first, second]
    monkeypatch.setattr(llm_mod, "_client", lambda: client)

    subject, body = llm_mod.draft_outreach("TestCo", "fit", "Forge Crew", "angle")
    assert "forge crew" not in subject.lower()
    assert client.responses.create.call_count == 2


def test_forbidden_variant_in_body_triggers_retry(monkeypatch):
    """A forbidden variant in the body triggers the corrective retry."""
    _set_openai_key("test-key")

    first = _mock_response(json.dumps({
        "subject": "Extra AI delivery capacity",
        "body": "FORGE CREW is a great orchestrator for your agency.",
    }))
    second = _mock_response(json.dumps({
        "subject": "Extra AI delivery capacity",
        "body": "I build agentic systems and can help when delivery is tight.",
    }))

    client = MagicMock()
    client.responses.create.side_effect = [first, second]
    monkeypatch.setattr(llm_mod, "_client", lambda: client)

    subject, body = llm_mod.draft_outreach("TestCo", "fit", "Forge Crew", "angle")
    assert "forge crew" not in body.lower()
    assert client.responses.create.call_count == 2


def test_successful_corrected_retry_accepted(monkeypatch):
    """A retry that removes all forbidden variants is accepted."""
    _set_openai_key("test-key")

    first = _mock_response(json.dumps({
        "subject": "AEGIS review for you",
        "body": "aegis can help your team with code review.",
    }))
    second = _mock_response(json.dumps({
        "subject": "Overflow AI engineering",
        "body": "I build agentic systems and can help when delivery is tight.",
    }))

    client = MagicMock()
    client.responses.create.side_effect = [first, second]
    monkeypatch.setattr(llm_mod, "_client", lambda: client)

    subject, body = llm_mod.draft_outreach("TestCo", "fit", "Aegis", "angle")
    assert "aegis" not in subject.lower()
    assert "aegis" not in body.lower()
    assert client.responses.create.call_count == 2


def test_second_unsafe_response_uses_fallback(monkeypatch):
    """If the retry still contains a forbidden variant, use safe fallback."""
    _set_openai_key("test-key")

    first = _mock_response(json.dumps({
        "subject": "Forge-Crew for you",
        "body": "Forge Crew orchestrator is great.",
    }))
    second = _mock_response(json.dumps({
        "subject": "AEGIS review",
        "body": "aegis code review agent.",
    }))

    client = MagicMock()
    client.responses.create.side_effect = [first, second]
    monkeypatch.setattr(llm_mod, "_client", lambda: client)

    subject, body = llm_mod.draft_outreach("TestCo", "fit", "Forge Crew", "angle")
    assert "forge crew" not in subject.lower()
    assert "aegis" not in subject.lower()
    assert "forge crew" not in body.lower()
    assert "aegis" not in body.lower()
    assert subject == "Extra AI delivery capacity"


def test_corrective_retry_provider_exception_uses_fallback(monkeypatch):
    """R1-10: If the corrective retry raises, use the safe fallback."""
    _set_openai_key("test-key")

    first = _mock_response(json.dumps({
        "subject": "Forge Crew support",
        "body": "Forge Crew is a great orchestrator.",
    }))

    client = MagicMock()
    client.responses.create.side_effect = [first, RuntimeError("provider timeout")]
    monkeypatch.setattr(llm_mod, "_client", lambda: client)

    subject, body = llm_mod.draft_outreach("TestCo", "fit", "Forge Crew", "angle")
    # Fallback used — no forbidden names.
    assert "forge crew" not in subject.lower()
    assert "forge crew" not in body.lower()
    assert subject == "Extra AI delivery capacity"
    assert "senior" in body.lower()


def test_fallback_contains_no_private_project_names():
    """The deterministic fallback must never contain private project names."""
    body = llm_mod._capability_fallback_body("TestCo", "fit", "angle")
    for name in llm_mod._FORBIDDEN_NAMES:
        canonical_name = llm_mod._canonical_project_text(name)
        canonical_body = llm_mod._canonical_project_text(body)
        assert canonical_name not in canonical_body


# ---------------------------------------------------------------------------
# R1-3 / R1-4 / R1-13: Draft copy versioning
# ---------------------------------------------------------------------------

def test_outreach_copy_version_constant():
    """OUTREACH_COPY_VERSION is defined and stable."""
    assert llm_mod.OUTREACH_COPY_VERSION == "v2"


def test_migration_adds_draft_copy_version(tmp_path):
    """The migration adds the draft_copy_version column."""
    import sqlite3
    from app.db import init_db, SCHEMA
    from app.config import settings as cfg

    db_path = tmp_path / "test.db"
    object.__setattr__(cfg, "db_path", db_path)

    # Create a DB without draft_copy_version by creating with old schema.
    db = sqlite3.connect(str(db_path))
    db.executescript("""
    CREATE TABLE leads (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company TEXT NOT NULL,
        domain TEXT NOT NULL UNIQUE,
        website TEXT NOT NULL,
        source_query TEXT,
        source_url TEXT,
        summary TEXT,
        services TEXT,
        team_hint TEXT,
        score INTEGER DEFAULT 0,
        score_reasons TEXT,
        fit_reason TEXT,
        proof_project TEXT,
        outreach_angle TEXT,
        contact_name TEXT,
        contact_role TEXT,
        contact_email TEXT,
        contact_source TEXT,
        contact_quality TEXT,
        subject TEXT,
        draft TEXT,
        status TEXT NOT NULL DEFAULT 'discovered',
        gmail_draft_id TEXT,
        draft_stale INTEGER DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        last_contact_at TEXT,
        followup_due_at TEXT
    );
    """)
    db.close()

    # Run migration.
    init_db()

    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row
    cols = {row["name"] for row in db.execute("PRAGMA table_info(leads)").fetchall()}
    db.close()
    assert "draft_copy_version" in cols


def _create_pre_v2_db(tmp_path, status, draft="Old draft body", subject="Old subject"):
    """Create a DB with a pre-V2 lead (no draft_copy_version column)."""
    import sqlite3
    from app.config import settings as cfg

    db_path = tmp_path / "test.db"
    object.__setattr__(cfg, "db_path", db_path)

    db = sqlite3.connect(str(db_path))
    db.executescript("""
    CREATE TABLE leads (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company TEXT NOT NULL,
        domain TEXT NOT NULL UNIQUE,
        website TEXT NOT NULL,
        source_query TEXT,
        source_url TEXT,
        summary TEXT,
        services TEXT,
        team_hint TEXT,
        score INTEGER DEFAULT 0,
        score_reasons TEXT,
        fit_reason TEXT,
        proof_project TEXT,
        outreach_angle TEXT,
        contact_name TEXT,
        contact_role TEXT,
        contact_email TEXT,
        contact_source TEXT,
        contact_quality TEXT,
        subject TEXT,
        draft TEXT,
        status TEXT NOT NULL DEFAULT 'discovered',
        gmail_draft_id TEXT,
        draft_stale INTEGER DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        last_contact_at TEXT,
        followup_due_at TEXT
    );
    """)
    db.execute(
        "INSERT INTO leads (company, domain, website, fit_reason, proof_project, "
        "outreach_angle, subject, draft, status, draft_stale, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("TestCo", "test.example.com", "https://test.example.com", "fit", "WingerX",
         "angle", subject, draft, status, 0, "2024-01-01T00:00:00Z", "2024-01-01T00:00:00Z")
    )
    db.commit()
    db.close()
    return db_path


def test_pre_v2_drafted_lead_becomes_stale_after_migration(tmp_path):
    """R1-4: Pre-V2 drafted lead with draft becomes stale after migration."""
    from app.db import init_db, get_lead
    from app.config import settings as cfg

    _create_pre_v2_db(tmp_path, "drafted")
    init_db()

    lead = get_lead(1)
    assert bool(lead["draft_stale"]) is True
    assert lead["draft_copy_version"] is None  # legacy


def test_pre_v2_rejected_lead_becomes_stale_after_migration(tmp_path):
    """R1-4: Pre-V2 rejected lead with draft becomes stale after migration."""
    from app.db import init_db, get_lead

    _create_pre_v2_db(tmp_path, "rejected")
    init_db()

    lead = get_lead(1)
    assert bool(lead["draft_stale"]) is True


def test_pre_v2_approved_lead_becomes_stale_after_migration(tmp_path):
    """R1-4: Pre-V2 approved lead with draft becomes stale after migration."""
    from app.db import init_db, get_lead

    _create_pre_v2_db(tmp_path, "approved")
    init_db()

    lead = get_lead(1)
    assert bool(lead["draft_stale"]) is True


def test_gmail_drafted_lead_not_migration_staled(tmp_path):
    """R1-4: gmail_drafted lead is NOT staled by the V2 migration."""
    from app.db import init_db, get_lead

    _create_pre_v2_db(tmp_path, "gmail_drafted")
    init_db()

    lead = get_lead(1)
    assert bool(lead["draft_stale"]) is False


def test_sent_lead_not_migration_staled(tmp_path):
    """R1-4: sent lead is NOT staled by the V2 migration."""
    from app.db import init_db, get_lead

    _create_pre_v2_db(tmp_path, "sent")
    init_db()

    lead = get_lead(1)
    assert bool(lead["draft_stale"]) is False


def test_do_not_contact_lead_not_migration_staled(tmp_path):
    """R1-4: do_not_contact lead is NOT staled by the V2 migration."""
    from app.db import init_db, get_lead

    _create_pre_v2_db(tmp_path, "do_not_contact")
    init_db()

    lead = get_lead(1)
    assert bool(lead["draft_stale"]) is False


def test_migration_is_idempotent(tmp_path):
    """R1-4: Running init_db() again does not re-stale V2-regenerated drafts."""
    from app.db import init_db, get_lead, update_lead
    from app.llm import OUTREACH_COPY_VERSION

    _create_pre_v2_db(tmp_path, "drafted")
    init_db()

    # Simulate V2 regeneration: clear stale, stamp version.
    update_lead(1, draft_stale=0, draft_copy_version=OUTREACH_COPY_VERSION)
    lead = get_lead(1)
    assert bool(lead["draft_stale"]) is False
    assert lead["draft_copy_version"] == OUTREACH_COPY_VERSION

    # Run init_db() again — must NOT re-stale.
    init_db()
    lead = get_lead(1)
    assert bool(lead["draft_stale"]) is False
    assert lead["draft_copy_version"] == OUTREACH_COPY_VERSION


def test_v2_regenerated_draft_remains_fresh_after_init_db(tmp_path):
    """R1-4: A V2-regenerated draft remains fresh after a later init_db()."""
    from app.db import init_db, get_lead, update_lead
    from app.llm import OUTREACH_COPY_VERSION

    _create_pre_v2_db(tmp_path, "drafted")
    init_db()

    update_lead(1, draft_stale=0, draft_copy_version=OUTREACH_COPY_VERSION,
                subject="V2 subject", draft="V2 body", status="drafted")
    init_db()  # re-run

    lead = get_lead(1)
    assert bool(lead["draft_stale"]) is False
    assert lead["draft_copy_version"] == OUTREACH_COPY_VERSION


# ---------------------------------------------------------------------------
# R1-5 / R1-6: Version stamping in pipeline and regeneration
# ---------------------------------------------------------------------------

def test_normal_pipeline_stamps_current_copy_version(tmp_path, monkeypatch):
    """R2-5/R2-6: Normal run_pipeline() generation stamps draft_copy_version=v2.

    This exercises the actual normal processing path (crawl -> analyze ->
    draft_outreach -> update_lead), NOT regenerate_draft().  All external
    boundaries (Serper, scraping, OpenAI, Gmail) are mocked.
    """
    from app.db import init_db, get_lead_by_domain
    from app.config import settings as cfg
    from app.pipeline import run as run_pipeline

    db_path = tmp_path / "test_pipeline_v2.db"
    object.__setattr__(cfg, "db_path", db_path)
    init_db()

    # Mock Serper search.
    from app.search import SearchHit
    hits = [
        SearchHit(
            title="testco.ai - AI Development Agency",
            url="https://testco.ai",
            snippet="We build AI agents and custom software for clients.",
            query="q",
        )
    ]
    monkeypatch.setattr(pipeline_mod, "search_serper", lambda query, num=10: hits)

    # Mock website crawl with strong agency text (long enough to pass filters).
    strong_text = (
        "We are an AI development agency providing custom software and AI development services "
        "for clients. We build AI agents, workflow automation, RAG systems, APIs and backend products. "
        "See our case studies and client projects. Our delivery team helps companies with "
        "AI implementation and system integration. We are a technology partner and development partner "
        "offering engineering services and implementation services. "
        "We deliver production AI systems for clients across multiple industries. "
        "Our team specializes in LLM development, retrieval augmented generation, "
        "machine learning, data engineering, and end-to-end AI product engineering. "
        "Contact us at hello@testco.ai"
    )
    monkeypatch.setattr(
        pipeline_mod, "crawl_company",
        lambda url: {
            "root": "https://testco.ai",
            "domain": "testco.ai",
            "title": "TestCo",
            "text": strong_text,
            "pages": [],
        },
    )

    # Mock analyze_agency.
    monkeypatch.setattr(
        pipeline_mod, "analyze_agency",
        lambda company, website, text: {
            "summary": "AI agency",
            "services": "ai",
            "fit_reason": "fit",
            "proof_project": "WingerX",
            "outreach_angle": "AI agents and client delivery",
        },
    )

    # Mock draft_outreach.
    monkeypatch.setattr(
        pipeline_mod, "draft_outreach",
        lambda company, fit, proof, angle: ("Pipeline subject", "Pipeline body"),
    )

    result = run_pipeline(limit=1)
    assert result["processed"] == 1
    assert result["drafted"] == 1

    lead = get_lead_by_domain("testco.ai")
    assert lead is not None
    assert lead["status"] == "drafted"
    assert bool(lead["draft_stale"]) is False
    assert lead["draft_copy_version"] == llm_mod.OUTREACH_COPY_VERSION
    assert lead["subject"] == "Pipeline subject"
    assert lead["draft"] == "Pipeline body"


def test_normal_pipeline_sets_draft_stale_false(tmp_path, monkeypatch):
    """R2-10: Normal pipeline sets draft_stale=0 (fresh) after generation."""
    from app.db import init_db, get_lead_by_domain
    from app.config import settings as cfg
    from app.pipeline import run as run_pipeline

    db_path = tmp_path / "test_stale_false.db"
    object.__setattr__(cfg, "db_path", db_path)
    init_db()

    from app.search import SearchHit
    hits = [
        SearchHit(
            title="staleco.ai - AI Development Agency",
            url="https://staleco.ai",
            snippet="We build AI agents and custom software for clients.",
            query="q",
        )
    ]
    monkeypatch.setattr(pipeline_mod, "search_serper", lambda query, num=10: hits)
    strong_text = (
        "We are an AI development agency providing custom software and AI development services "
        "for clients. We build AI agents, workflow automation, RAG systems, APIs and backend products. "
        "See our case studies and client projects. Our delivery team helps companies with "
        "AI implementation and system integration. We are a technology partner and development partner "
        "offering engineering services and implementation services. "
        "We deliver production AI systems for clients across multiple industries. "
        "Our team specializes in LLM development, retrieval augmented generation, "
        "machine learning, data engineering, and end-to-end AI product engineering. "
        "Contact us at hello@staleco.ai"
    )
    monkeypatch.setattr(
        pipeline_mod, "crawl_company",
        lambda url: {
            "root": "https://staleco.ai",
            "domain": "staleco.ai",
            "title": "StaleCo",
            "text": strong_text,
            "pages": [],
        },
    )
    monkeypatch.setattr(
        pipeline_mod, "analyze_agency",
        lambda company, website, text: {
            "summary": "AI agency",
            "services": "ai",
            "fit_reason": "fit",
            "proof_project": "WingerX",
            "outreach_angle": "angle",
        },
    )
    monkeypatch.setattr(
        pipeline_mod, "draft_outreach",
        lambda company, fit, proof, angle: ("Subject", "Body"),
    )

    run_pipeline(limit=1)
    lead = get_lead_by_domain("staleco.ai")
    assert bool(lead["draft_stale"]) is False


def test_explicit_regeneration_stamps_current_copy_version(tmp_path, monkeypatch):
    """R1-6: Explicit regeneration stamps draft_copy_version = v2."""
    from app.db import init_db, upsert_lead, get_lead
    import tests.test_run_history as trh

    trh._live_mode(tmp_path)
    init_db()
    lead_id = upsert_lead(trh._stale_lead(status="rejected"))
    monkeypatch.setattr(pipeline_mod, "draft_outreach", lambda *a: ("Fresh subject", "Fresh body"))

    pipeline_mod.regenerate_draft(lead_id)

    lead = get_lead(lead_id)
    assert lead["draft_copy_version"] == llm_mod.OUTREACH_COPY_VERSION
    assert lead["status"] == "drafted"
    assert bool(lead["draft_stale"]) is False


def test_optimistic_concurrency_with_version_remains_correct(tmp_path, monkeypatch):
    """R1-7: Optimistic concurrency still works with draft_copy_version in snapshot."""
    from app.db import init_db, upsert_lead, get_lead, update_lead
    from app.pipeline import regenerate_draft, RegenerationBlocked
    import tests.test_run_history as trh

    trh._live_mode(tmp_path)
    init_db()
    lead_id = upsert_lead(trh._stale_lead(status="drafted"))

    def mutate():
        update_lead(lead_id, status="do_not_contact")

    def fake_draft(*a):
        mutate()
        return ("Concurrent subject", "Concurrent body")

    monkeypatch.setattr(pipeline_mod, "draft_outreach", fake_draft)

    with pytest.raises(RegenerationBlocked) as exc_info:
        regenerate_draft(lead_id)
    assert exc_info.value.status_code == 409

    lead = get_lead(lead_id)
    assert lead["status"] == "do_not_contact"
    assert bool(lead["draft_stale"]) is True


# ---------------------------------------------------------------------------
# R1-15: Fallback quality tests
# ---------------------------------------------------------------------------

def test_fallback_uses_company_name():
    body = llm_mod._capability_fallback_body("LaunchPad Lab", "fit", "AI agents")
    assert "LaunchPad Lab" in body


def test_fallback_uses_usable_outreach_angle():
    body = llm_mod._capability_fallback_body("TestCo", "fit", "AI agents and client delivery")
    assert "AI agents and client delivery" in body


def test_fallback_remains_concise():
    body = llm_mod._capability_fallback_body("TestCo", "fit", "AI agents")
    word_count = len(body.split())
    assert word_count <= 80


def test_fallback_contains_easy_reply_question():
    body = llm_mod._capability_fallback_body("TestCo", "fit", "AI agents")
    assert "?" in body
    assert "Do you ever" in body


def test_fallback_contains_no_forge_crew_variant():
    body = llm_mod._capability_fallback_body("TestCo", "fit", "AI agents")
    canonical_body = llm_mod._canonical_project_text(body)
    assert "forge crew" not in canonical_body


def test_fallback_contains_no_aegis_variant():
    body = llm_mod._capability_fallback_body("TestCo", "fit", "AI agents")
    canonical_body = llm_mod._canonical_project_text(body)
    assert "aegis" not in canonical_body


def test_empty_angle_yields_safe_generic_fallback():
    """When the angle is empty, the fallback uses a generic safe sentence."""
    body = llm_mod._capability_fallback_body("TestCo", "fit", "")
    assert "TestCo" in body
    assert "senior" in body.lower()
    # No angle-specific text since angle was empty.
    assert "around" not in body.lower()


def test_fallback_long_angle_is_truncated():
    """A very long angle is truncated to keep the email concise."""
    long_angle = " ".join(["word"] * 50)
    body = llm_mod._capability_fallback_body("TestCo", "fit", long_angle)
    # The angle should be truncated — not all 50 words present.
    assert body.count("word") < 50


# ---------------------------------------------------------------------------
# R2-1/R2-2/R2-3: Unsafe fallback angle rejection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("angle", [
    "Forge Crew delivery",
    "forge crew delivery",
    "FORGE CREW delivery",
    "Forge-Crew delivery",
    "Forge   Crew   delivery",
])
def test_forge_crew_in_fallback_angle_is_rejected(angle):
    """R2-1: Forge Crew in any variant in the angle must be rejected."""
    body = llm_mod._capability_fallback_body("TestCo", "fit", angle)
    canonical_body = llm_mod._canonical_project_text(body)
    assert "forge crew" not in canonical_body
    # Generic hook used (no "around" since angle was discarded).
    assert "around" not in body.lower()


@pytest.mark.parametrize("angle", [
    "Aegis code review",
    "aegis code review",
    "AEGIS code review",
])
def test_aegis_in_fallback_angle_is_rejected(angle):
    """R2-1: Aegis in any variant in the angle must be rejected."""
    body = llm_mod._capability_fallback_body("TestCo", "fit", angle)
    canonical_body = llm_mod._canonical_project_text(body)
    assert "aegis" not in canonical_body
    assert "around" not in body.lower()


def test_safe_angle_is_still_used_normally():
    """R2-3: A safe angle (no forbidden names) is still used in the fallback."""
    body = llm_mod._capability_fallback_body("TestCo", "fit", "AI agents and client delivery")
    assert "AI agents and client delivery" in body
    assert "around" in body.lower()


def test_retry_exception_with_unsafe_angle_still_safe(monkeypatch):
    """R2-3: Retry exception + unsafe angle -> safe fallback with no private names."""
    _set_openai_key("test-key")

    first = _mock_response(json.dumps({
        "subject": "Forge Crew support",
        "body": "Forge Crew is a great orchestrator.",
    }))

    client = MagicMock()
    client.responses.create.side_effect = [first, RuntimeError("provider timeout")]
    monkeypatch.setattr(llm_mod, "_client", lambda: client)

    subject, body = llm_mod.draft_outreach(
        "TestCo", "fit", "Forge Crew", "Forge Crew multi-agent delivery"
    )
    assert "forge crew" not in subject.lower()
    assert "forge crew" not in body.lower()
    assert "aegis" not in body.lower()
    assert subject == "Extra AI delivery capacity"


def test_second_unsafe_response_with_unsafe_angle_still_safe(monkeypatch):
    """R2-3: Second unsafe LLM response + unsafe angle -> safe fallback."""
    _set_openai_key("test-key")

    first = _mock_response(json.dumps({
        "subject": "Forge Crew for you",
        "body": "Forge Crew orchestrator is great.",
    }))
    second = _mock_response(json.dumps({
        "subject": "AEGIS review",
        "body": "aegis code review agent.",
    }))

    client = MagicMock()
    client.responses.create.side_effect = [first, second]
    monkeypatch.setattr(llm_mod, "_client", lambda: client)

    subject, body = llm_mod.draft_outreach(
        "TestCo", "fit", "Forge Crew", "Forge Crew multi-agent delivery"
    )
    assert "forge crew" not in subject.lower()
    assert "forge crew" not in body.lower()
    assert "aegis" not in subject.lower()
    assert "aegis" not in body.lower()
    assert subject == "Extra AI delivery capacity"


# ---------------------------------------------------------------------------
# R2-4: Exact token-sequence matching (no false positives)
# ---------------------------------------------------------------------------

def test_aegisian_does_not_falsely_match_aegis():
    """R2-4: 'Aegisian' must NOT be identified as the private project 'Aegis'."""
    result = llm_mod._contains_forbidden_project("Aegisian systems")
    assert result is None


def test_aegis_as_standalone_word_still_matches():
    """R2-4: 'Aegis' as a standalone word must still be detected."""
    result = llm_mod._contains_forbidden_project("Aegis code review")
    assert result == "Aegis"


def test_forge_crew_normalized_variants_still_match():
    """R2-4: Existing Forge Crew normalized variants still match."""
    for variant in ["Forge Crew", "forge crew", "FORGE CREW", "Forge-Crew", "Forge   Crew"]:
        result = llm_mod._contains_forbidden_project(variant)
        assert result == "Forge Crew", f"Failed for variant: {variant}"


def test_forge_crew_as_part_of_larger_word_does_not_false_match():
    """R2-4: 'ForgeCrews' (no separator) should not match 'Forge Crew'."""
    # After canonicalization, "forgecrews" has no space, so "forge crew"
    # (with space) should not be found as a token sequence.
    result = llm_mod._contains_forbidden_project("ForgeCrews system")
    # "forgecrews" canonicalizes to "forgecrews" — no space between forge and crew,
    # so "forge crew" (with space) won't match.
    assert result is None
