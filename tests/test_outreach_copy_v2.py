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
