from __future__ import annotations

import json
import time

from openai import OpenAI

from .config import settings
from .logging_config import get_logger

logger = get_logger("llm")

# ---------------------------------------------------------------------------
# Portfolio metadata
# ---------------------------------------------------------------------------
#
# ``PORTFOLIO`` remains the internal research-time portfolio used by
# ``analyze_agency()`` to select a primary relevance signal.  It is NOT
# directly used for outreach copy.
#
# ``OUTREACH_PROOF`` is the outreach-safe proof bank.  Each entry carries:
#   - description: capability description (may be supplied as context)
#   - public_url:  verified public destination, or "" when none exists
#   - nameable:    whether the project name may appear in cold outreach
#
# A project with ``nameable=False`` must never be named in generated
# outreach copy.  Its capability description may still inform the email.
# ----------------------------------------------------------------------------

PORTFOLIO = {
    "WingerX": "AI automation and business orchestration platform with agents, workflows, integrations, CRM and search intelligence.",
    "GradeWise": "AI interview mastery product with diagnostics, evidence-driven mastery, adaptive preparation and production backend architecture.",
    "Aegis": "Autonomous AI code review and repository hygiene agent using deterministic scanning, LLM remediation, validation and human approval.",
    "Forge Crew": "Local-first multi-agent software engineering orchestrator coordinating specialist agents, tools, validation and approval workflows.",
}

OUTREACH_PROOF = {
    "WingerX": {
        "description": "AI automation and business orchestration platform with agents, workflows, integrations, CRM and search intelligence.",
        "public_url": "https://wingerx.com/",
        "nameable": True,
    },
    "GradeWise": {
        "description": "AI interview mastery product with diagnostics, evidence-driven mastery, adaptive preparation and production backend architecture.",
        "public_url": "https://gradewise.quest/",
        "nameable": True,
    },
    "Aegis": {
        "description": "Autonomous AI code review and repository hygiene agent using deterministic scanning, LLM remediation, validation and human approval.",
        "public_url": "",
        "nameable": False,
    },
    "Forge Crew": {
        "description": "Local-first multi-agent software engineering orchestrator coordinating specialist agents, tools, validation and approval workflows.",
        "public_url": "",
        "nameable": False,
    },
}

# Projects that must never appear by name in cold outreach copy.
_FORBIDDEN_NAMES = [
    name for name, meta in OUTREACH_PROOF.items() if not meta["nameable"]
]


def _client() -> OpenAI | None:
    if not settings.openai_api_key:
        return None
    return OpenAI(api_key=settings.openai_api_key)


def _contains_forbidden_project(text: str) -> str | None:
    """Return the forbidden project name found in *text*, or ``None``.

    Case-sensitive exact-name match.  We intentionally do not use substring
    fuzzing to avoid false positives on common words.
    """
    for name in _FORBIDDEN_NAMES:
        if name in text:
            return name
    return None


def _capability_fallback_subject(company: str) -> str:
    """Deterministic, non-salesy subject line."""
    return "Extra AI delivery capacity"


def _capability_fallback_body(
    company: str, fit_reason: str, outreach_angle: str
) -> str:
    """Deterministic capability-led fallback body.

    Never names a non-public project.  Positions the sender as senior AI
    engineering capacity, not as a software vendor.
    """
    angle = outreach_angle.rstrip(".").strip()
    return (
        f"{company}'s work looks close to the kind of AI projects I build day to day. "
        f"I work across agentic systems, RAG, automation and production AI backends, "
        f"and can plug in as senior overflow or white-label engineering capacity when "
        f"a team gets stretched. Do you ever bring in external AI engineers for client "
        f"work when delivery capacity gets tight?"
    )


def analyze_agency(company: str, website: str, text: str) -> dict:
    client = _client()
    if client is None:
        logger.info("OPENAI_API_KEY not configured. Using deterministic fallback.")
        return {
            "summary": text[:700],
            "services": "",
            "fit_reason": "Potential AI/automation delivery partner based on website keywords.",
            "proof_project": "WingerX",
            "outreach_angle": "Offer overflow AI engineering capacity for client delivery.",
        }

    logger.info("Analyzing agency %s with model %s", company, settings.openai_model)
    prompt = f"""
You are qualifying a software/AI agency as a potential white-label delivery partner for a senior AI engineer.
Use ONLY the supplied website text. Do not invent facts.

Company: {company}
Website: {website}
Portfolio proof available:
{json.dumps(PORTFOLIO, indent=2)}

Website text:
{text[:18000]}

Return strict JSON with keys:
summary (max 80 words), services (comma-separated), fit_reason (1 sentence),
proof_project (exactly one of WingerX, GradeWise, Aegis, Forge Crew),
outreach_angle (one concrete sentence tied to something actually supported by the website text).
"""
    start = time.perf_counter()
    try:
        resp = client.responses.create(model=settings.openai_model, input=prompt)
    except Exception as exc:
        logger.error("LLM analysis failed for %s: %s", company, exc)
        raise
    raw = resp.output_text.strip()
    logger.debug("LLM analysis response length: %d chars", len(raw))
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("LLM returned invalid JSON for %s, attempting recovery", company)
        start_idx, end_idx = raw.find("{"), raw.rfind("}")
        if start_idx >= 0 and end_idx > start_idx:
            result = json.loads(raw[start_idx:end_idx+1])
            logger.debug("JSON recovery succeeded for %s", company)
        else:
            logger.error("LLM JSON recovery failed for %s", company)
            raise
    elapsed = time.perf_counter() - start
    logger.info("Selected proof project: %s", result.get("proof_project", "WingerX"))
    logger.info("Agency analysis completed in %.1fs", elapsed)
    return result


def _build_outreach_prompt(
    company: str,
    fit_reason: str,
    proof_project: str,
    outreach_angle: str,
    *,
    corrective: str | None = None,
) -> str:
    """Build the capability-led outreach drafting prompt.

    ``proof_project`` is an internal relevance hint, NOT a hard requirement
    to name that project in the email.
    """
    primary_meta = OUTREACH_PROOF.get(proof_project, OUTREACH_PROOF["WingerX"])

    # Outreach-safe proof bank: only nameable projects with their public URLs.
    nameable_bank = {
        name: {
            "description": meta["description"],
            "public_url": meta["public_url"],
        }
        for name, meta in OUTREACH_PROOF.items()
        if meta["nameable"]
    }

    forbidden_list = ", ".join(_FORBIDDEN_NAMES)

    corrective_block = ""
    if corrective:
        corrective_block = f"\n\nCORRECTIVE INSTRUCTION:\n{corrective}\n"

    return f"""
You write concise cold outreach from a senior AI engineer to an agency
decision-maker.

The goal is to start a conversation about overflow or white-label AI
engineering capacity.

The recipient should feel that the sender:
- actually looked at their company
- understands the kind of work they deliver
- has credible adjacent experience
- could plug into delivery when capacity gets tight

Do not sound like a salesperson.
Do not sound like an AI-generated portfolio description.
Use only the supplied facts.

PROSPECT
Company: {company}
Why it fits: {fit_reason}
Specific angle: {outreach_angle}

SENDER CAPABILITIES
Senior AI engineer with experience across:
- AI agents and agentic systems
- automation
- RAG / retrieval systems
- FastAPI and production AI backends
- computer vision / ML where relevant
- AI product engineering

PRIMARY INTERNAL PROOF
{proof_project}: {primary_meta["description"]}

This is an internal relevance signal.
You are NOT required to mention this project by name.

OUTREACH-SAFE PROOF BANK
{json.dumps(nameable_bank, indent=2)}

RULES
1. Lead with something specific about the prospect.
2. Position the sender as senior engineering capacity, not as a software vendor.
3. Explain what he can help deliver in plain language.
4. Proof is optional.
5. Prefer capability evidence over an obscure project name.
6. Only name projects where nameable=true (listed in the proof bank above).
7. Never name a private/non-public project. Forbidden names: {forbidden_list}
8. Use at most two named projects.
9. Do not explain project architecture unless it directly helps the prospect.
10. Avoid phrases such as: local-first multi-agent orchestrator, deterministic scanning, production-grade, cutting-edge, revolutionary, leverage synergies.
11. Avoid listing every technical skill.
12. Avoid generic compliments.
13. Avoid claiming knowledge that is not in the prospect research.
14. Do not say "I noticed" or "caught my eye" in every email.
15. Make the final question easy to answer.

STYLE
- natural
- direct
- technically credible
- understated
- peer-to-peer
- 60 to 95 words
- 3 or 4 short sentences
- no greeting
- no signature

Return strict JSON: {{"subject":"...","body":"..."}}
{corrective_block}
"""


def _parse_outreach_json(raw: str, company: str) -> tuple[str, str]:
    """Parse the LLM outreach response as strict JSON with recovery."""
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("LLM outreach returned invalid JSON for %s, attempting recovery", company)
        obj = json.loads(raw[raw.find("{"):raw.rfind("}")+1])
        logger.debug("JSON recovery succeeded for outreach %s", company)
    return obj["subject"], obj["body"]


def draft_outreach(company: str, fit_reason: str, proof_project: str, outreach_angle: str) -> tuple[str, str]:
    """Generate a cold outreach subject/body pair.

    The signature is unchanged from the original implementation so callers
    (pipeline, regeneration, tests) continue to work without modification.

    Philosophy (V2):
      - capability-led positioning, not a portfolio pitch
      - ``proof_project`` is an internal relevance hint; the model is NOT
        required to name it
      - only ``nameable=True`` projects may appear by name
      - at most two named projects
      - private/non-public project names are forbidden and guarded
        deterministically with one retry, then a safe fallback
    """
    client = _client()
    if client is None:
        logger.info("OPENAI_API_KEY not configured. Using deterministic fallback for outreach.")
        subject = _capability_fallback_subject(company)
        body = _capability_fallback_body(company, fit_reason, outreach_angle)
        return subject, body

    logger.info("Generating outreach draft for %s", company)
    prompt = _build_outreach_prompt(company, fit_reason, proof_project, outreach_angle)

    start = time.perf_counter()
    try:
        resp = client.responses.create(model=settings.openai_model, input=prompt)
    except Exception as exc:
        logger.error("LLM outreach generation failed for %s: %s", company, exc)
        raise
    raw = resp.output_text.strip()
    logger.debug("LLM outreach response length: %d chars", len(raw))
    subject, body = _parse_outreach_json(raw, company)
    elapsed = time.perf_counter() - start
    logger.info("Outreach draft generated in %.1fs", elapsed)

    # ------------------------------------------------------------------
    # Deterministic guard against private/non-public project names.
    # ------------------------------------------------------------------
    forbidden_in_subject = _contains_forbidden_project(subject)
    forbidden_in_body = _contains_forbidden_project(body)
    if forbidden_in_subject or forbidden_in_body:
        bad = forbidden_in_subject or forbidden_in_body
        logger.warning(
            "Outreach draft for %s contained forbidden project name '%s'; "
            "retrying with corrective instruction",
            company, bad,
        )
        corrective = (
            f"The previous draft named a non-public/internal project ('{bad}'). "
            "Rewrite it using the project's capability as evidence without naming it. "
            "Do not mention '{bad}' or any other non-public project by name."
        )
        retry_prompt = _build_outreach_prompt(
            company, fit_reason, proof_project, outreach_angle,
            corrective=corrective,
        )
        try:
            retry_resp = client.responses.create(model=settings.openai_model, input=retry_prompt)
        except Exception as exc:
            logger.error("LLM outreach retry failed for %s: %s", company, exc)
            raise
        retry_raw = retry_resp.output_text.strip()
        subject, body = _parse_outreach_json(retry_raw, company)
        logger.info("Outreach retry generated for %s", company)

        # If the retry STILL contains a forbidden name, use the safe fallback.
        if _contains_forbidden_project(subject) or _contains_forbidden_project(body):
            logger.warning(
                "Outreach retry for %s still contained a forbidden project name; "
                "using capability-led fallback",
                company,
            )
            subject = _capability_fallback_subject(company)
            body = _capability_fallback_body(company, fit_reason, outreach_angle)

    return subject, body


def draft_followup(company: str, prior_body: str) -> tuple[str, str]:
    client = _client()
    if client is None:
        logger.info("OPENAI_API_KEY not configured. Using deterministic fallback for follow-up.")
        return (
            f"Re: Extra AI delivery capacity",
            "Just resurfacing this in case extra AI delivery capacity is useful for your team. Would it help if I sent one or two relevant builds?",
        )
    logger.info("Generating follow-up draft for %s", company)
    prompt = f"""
Write a 2-sentence follow-up to this cold outreach. No greeting, no guilt, no 'just checking in', no corporate fluff.
Make it useful and easy to reply to. Under 45 words.
Agency: {company}
Original email: {prior_body}
Return strict JSON: {{"subject":"...","body":"..."}}
"""
    start = time.perf_counter()
    try:
        resp = client.responses.create(model=settings.openai_model, input=prompt)
    except Exception as exc:
        logger.error("LLM follow-up generation failed for %s: %s", company, exc)
        raise
    raw = resp.output_text.strip()
    logger.debug("LLM follow-up response length: %d chars", len(raw))
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("LLM follow-up returned invalid JSON for %s, attempting recovery", company)
        obj = json.loads(raw[raw.find("{"):raw.rfind("}")+1])
        logger.debug("JSON recovery succeeded for follow-up %s", company)
    elapsed = time.perf_counter() - start
    logger.info("Follow-up draft generated in %.1fs", elapsed)
    return obj["subject"], obj["body"]
