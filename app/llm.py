from __future__ import annotations

import json
import re
import time

from openai import OpenAI

from .config import settings
from .logging_config import get_logger

logger = get_logger("llm")

# ---------------------------------------------------------------------------
# Outreach copy versioning
# ---------------------------------------------------------------------------
#
# ``OUTREACH_COPY_VERSION`` identifies the outreach-generation policy that
# produced a draft.  When the prompt strategy changes materially (as in V2),
# existing drafts stamped with a prior version (or NULL for pre-versioning
# drafts) are marked stale by the migration so the operator is offered
# regeneration under the new policy.
#
# This constant is the single source of truth — pipeline and regeneration
# both import it rather than duplicating string literals.
# ---------------------------------------------------------------------------

OUTREACH_COPY_VERSION = "v2"

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


def _canonical_project_text(value: str) -> str:
    """Canonicalize text for robust project-name matching.

    Strategy (deterministic, no fuzzy/semantic matching):
      - lowercase
      - replace non-alphanumeric separators with spaces
      - collapse repeated whitespace
      - trim
    """
    if not value:
        return ""
    lowered = value.lower()
    # Replace any run of non-alphanumeric characters with a single space.
    replaced = re.sub(r"[^a-z0-9]+", " ", lowered)
    return re.sub(r"\s+", " ", replaced).strip()


# Pre-computed canonical forms of forbidden names for robust matching.
_FORBIDDEN_CANONICAL = {
    _canonical_project_text(name): name for name in _FORBIDDEN_NAMES
}


def _client() -> OpenAI | None:
    if not settings.openai_api_key:
        return None
    return OpenAI(api_key=settings.openai_api_key)


# R3-5/R4/R5/R6: Token sets for sender-proof detection.
# First-person sender pronouns (canonical form).
_SENDER_PRONOUNS = frozenset({"i", "we"})
# Proof/action verbs (canonical form) that indicate the company name is
# being used as the sender's own work.
_SENDER_PROOF_VERBS = frozenset({
    "built", "developed", "created", "made", "shipped",
    "designed", "wrote", "deployed",
})
# Auxiliaries/modifiers that may appear between the pronoun and the verb.
_SENDER_AUXILIARIES = frozenset({
    "have", "ve", "recently", "personally", "also", "previously",
    "actually", "just", "now", "already", "had", "been",
})
# Bridge words that may appear between the proof verb and the company name.
# These are very small determiner/preposition tokens that don't break the
# structural tie between the verb and the company name.
_SENDER_BRIDGE_WORDS = frozenset({"the", "a", "an", "our", "my", "this", "that"})
# Maximum token window to examine before a company-name occurrence.
_SENDER_PROOF_WINDOW = 8


def _has_sender_proof_context(haystack: str, canonical_company: str) -> bool:
    """R4/R5/R6: Check if any occurrence of the company name is directly
    preceded by a sender-proof pattern.

    R6 fix: The proof verb must be structurally tied to the company-name
    occurrence — it must be the last non-bridge token before the company
    name (or separated only by bridge words like ``the`` / ``a``).  This
    prevents false positives where the verb appears earlier in the window
    but acts on a different object (e.g. ``I've built agentic systems,
    and Aegis Labs' work...``).

    The anchored pattern is:
      pronoun → (auxiliaries)* → verb → (bridge words)* → COMPANY

    Examples detected (rejected):
      ``i built {company}``
      ``i recently built {company}``
      ``i have built {company}``
      ``i ve recently built {company}``
      ``we built {company}``
      ``i personally developed {company}``
      ``i built the {company} platform``

    Examples NOT detected (allowed):
      ``i ve built agentic systems and {company}' work...``
      ``i built integrations for {company}``
      ``we developed ai workflows relevant to {company}``

    Deterministic — no LLM/semantic classifier.  Token boundaries are
    preserved.
    """
    company_needle = canonical_company
    idx = 0
    while True:
        pos = haystack.find(company_needle, idx)
        if pos == -1:
            return False
        # Verify token-boundary match (preceded by space or start).
        if pos > 0 and haystack[pos - 1] != " ":
            idx = pos + len(company_needle)
            continue
        # Verify token boundary after the company name.
        after_pos = pos + len(company_needle)
        if after_pos < len(haystack) and haystack[after_pos] not in (" ", ""):
            idx = pos + len(company_needle)
            continue

        # Extract the token sequence immediately before the company name.
        before = haystack[:pos].strip()
        if before:
            tokens = before.split()
            window = tokens[-_SENDER_PROOF_WINDOW:] if len(tokens) > _SENDER_PROOF_WINDOW else tokens
            if _window_has_sender_proof(window):
                return True

        idx = pos + len(company_needle)
    return False


def _window_has_sender_proof(tokens: list[str]) -> bool:
    """R6: Check if a token window ends with an anchored sender-proof pattern.

    The pattern must be anchored to the END of the token sequence
    (i.e. immediately before the company name):

      pronoun → (auxiliaries)* → verb → (bridge words)*

    The verb must be the last non-bridge token in the window.  Bridge
    words (``the``, ``a``, ``an``, ``our``, ``my``, ``this``, ``that``)
    may appear between the verb and the company name.  No other tokens
    may appear after the verb except bridge words.

    This prevents false positives where the verb acts on a different
    object earlier in the sentence (e.g. ``I built integrations for
    {company}`` — ``for`` is not a bridge word, so the pattern breaks).
    """
    n = len(tokens)
    if n < 2:
        return False

    # Step 1: Strip trailing bridge words to find the last non-bridge token.
    # That token must be a proof verb.
    end = n - 1
    while end >= 0 and tokens[end] in _SENDER_BRIDGE_WORDS:
        end -= 1
    if end < 1:
        return False
    if tokens[end] not in _SENDER_PROOF_VERBS:
        return False

    # Step 2: Scan backward from the verb for a pronoun, allowing only
    # auxiliaries/modifiers between the pronoun and the verb.
    for j in range(end - 1, -1, -1):
        if tokens[j] in _SENDER_PRONOUNS:
            return True
        if tokens[j] not in _SENDER_AUXILIARIES:
            break  # Non-auxiliary token breaks the pattern.

    return False


def _contains_forbidden_project(text: str, *, company: str = "") -> str | None:
    """Return the original forbidden project name found in *text*, or ``None``.

    R1-1: Matching is case-insensitive and separator-normalized so that
    variants such as ``forge crew``, ``FORGE CREW``, ``Forge-Crew``, and
    ``Forge   Crew`` are all detected.  No fuzzy/semantic matching is used.

    R2-4: Matching uses exact token sequences, not arbitrary substring
    matching.  This prevents false positives such as ``Aegisian`` matching
    ``Aegis``.  Both the haystack and needle are padded with spaces so
    that only whole-token-sequence boundaries match.

    R3-5/R3-6/R4: When *company* is supplied, a forbidden project-name
    occurrence that is part of the prospect's company name is tolerated
    ONLY when it is a legitimate recipient identity reference (e.g.
    ``Aegis Labs' AI delivery work...``).  It is NOT tolerated when the
    full company name appears in a sender-proof context (e.g.
    ``I built Aegis Labs as a code-review system``) or when the forbidden
    name appears standalone outside the company name (e.g.
    ``I built Aegis as a code-review agent``).

    The algorithm is deterministic:
      1. Check if the forbidden name appears standalone in the text.
      2. Check if the company name (containing the forbidden name) appears
         in the text.
      3. If the forbidden name appears standalone:
         a. If no company context, or the forbidden name is not part of
            the company name, reject.
         b. If the company name is supplied and contains the forbidden
            name as a token subset, remove company-name occurrences and
            check if standalone occurrences remain → if so, reject.
      4. If the forbidden name does NOT appear standalone but the company
         name does (and the forbidden name is a token subset of the
         company name), check if any company-name occurrence is in a
         sender-proof context → if so, reject.
      5. Otherwise, tolerate (recipient identity reference).
    """
    if not text:
        return None
    canonical_text = _canonical_project_text(text)
    haystack = f" {canonical_text} "

    canonical_company = _canonical_project_text(company) if company else ""

    for canonical_name, original_name in _FORBIDDEN_CANONICAL.items():
        forbidden_needle = f" {canonical_name} "
        company_needle = f" {canonical_company} " if canonical_company else ""
        # Is the forbidden name a token-subset of the company name?
        is_subset = (
            canonical_company
            and forbidden_needle in f" {canonical_company} "
        )

        # Step 1: Does the forbidden name appear standalone?
        forbidden_standalone = forbidden_needle in haystack

        # Step 2: Does the company name appear in the text?
        company_present = bool(company_needle) and company_needle in haystack

        if not forbidden_standalone and not (is_subset and company_present):
            # Neither standalone forbidden name nor company name present.
            continue

        if forbidden_standalone:
            # Forbidden name appears standalone somewhere.
            if not canonical_company or not is_subset:
                # No company context or forbidden name is not part of
                # company name → always reject.
                return original_name
            # Forbidden name is a token subset of company name.
            # Remove full company-name occurrences and check if standalone
            # occurrences remain outside the company name.
            remaining = haystack.replace(company_needle, " ")
            if forbidden_needle in remaining:
                return original_name
            # All standalone occurrences were part of the full company name.
            # Fall through to sender-proof context check below.

        # Step 4: Company name present, forbidden name is a token subset.
        # Check if any company-name occurrence is in a sender-proof context.
        if is_subset and company_present:
            if _has_sender_proof_context(haystack, canonical_company):
                return original_name

        # All occurrences are legitimate recipient identity references.
        # Tolerate and continue checking other forbidden names.
        continue

    return None


def _capability_fallback_subject(company: str) -> str:
    """Deterministic, non-salesy subject line."""
    return "Extra AI delivery capacity"


def _sanitize_angle(angle: str, max_words: int = 12, *, company: str = "") -> str:
    """Sanitize and truncate an outreach angle for use in fallback copy.

    Keeps it short, grounded, and free of fabricated claims.  Strips
    trailing punctuation and limits to ``max_words`` words.

    R2-1/R2-2: If the angle contains a non-nameable project name (in any
    supported variant), the entire angle is discarded and ``""`` is
    returned.  This prevents the deterministic fallback from reintroducing
    a private project name through the angle.  We do not attempt clever
    word deletion — if the angle is unsafe, we discard it wholesale.

    R3-5/R3-9: When *company* is supplied, the prospect's own company name
    is tolerated (it is the recipient's identity, not sender proof).  An
    angle that mentions the prospect's company name is still usable as
    long as it does not introduce a forbidden project name as sender
    proof outside the company identity.
    """
    if not angle:
        return ""
    cleaned = angle.strip().rstrip(".").strip()
    if not cleaned:
        return ""
    words = cleaned.split()
    if len(words) > max_words:
        words = words[:max_words]
    result = " ".join(words)
    # R2-1: Reject angles containing forbidden project names.
    # R3-5: Tolerate the prospect's own company name.
    if _contains_forbidden_project(result, company=company):
        return ""
    return result


def _capability_fallback_body(
    company: str, fit_reason: str, outreach_angle: str
) -> str:
    """Deterministic capability-led fallback body.

    R1-9: Uses the supplied grounded outreach angle when available to make
    the email prospect-specific.  Falls back to a generic safe sentence when
    the angle is empty or unusable.

    Never names a non-public project.  Positions the sender as senior AI
    engineering capacity, not as a software vendor.
    """
    angle = _sanitize_angle(outreach_angle, company=company)
    if angle:
        hook = f"{company}'s work around {angle} looks close to the kind of AI delivery I handle."
    else:
        hook = f"{company}'s work looks close to the kind of AI projects I build day to day."
    return (
        f"{hook} "
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

PUBLIC PROOF URLS
- URLs are supplied for credibility/context only.
- Do not automatically include raw URLs in the email.
- The operator can inspect public proof before approval.
- Naming a public project does not require including its URL.
- Prefer concise conversational copy over link dumping.
- Do not include raw portfolio URLs in normal cold outreach.

SUBJECT
- Prefer 3 to 7 words.
- Natural and understated.
- No clickbait.
- No fake familiarity.
- No excessive capitalization.
- Do not use generic subjects such as "Partnership opportunity".
- Do not use formulaic subjects such as "White-label AI engineering for {{Company}}".
- Do not automatically put the prospect company name in the subject.
- Prefer simple directions such as: Extra AI delivery capacity, Overflow AI engineering, AI delivery support, Extra capacity for AI projects.
- Do not mention a non-public/private project in the subject.
- Controlled variation is acceptable — do not rigidly force one subject template.

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
16. A private project name may coincidentally overlap with the prospect's company name. You may use the supplied prospect company name to refer to the recipient, but never present a private/non-public project as sender proof.

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
    # R3-5/R3-6: Pass the prospect company name so that a forbidden-name
    # occurrence that is part of the prospect's own identity is tolerated.
    # ------------------------------------------------------------------
    forbidden_in_subject = _contains_forbidden_project(subject, company=company)
    forbidden_in_body = _contains_forbidden_project(body, company=company)
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
        # R1-10: If the corrective retry itself fails (provider exception,
        # network error, etc.), fall back to the safe deterministic copy
        # rather than failing the entire lead.  The first output was already
        # rejected as unsafe, so we must NOT persist it.  Log the retry
        # failure without exposing secret-bearing exception content.
        try:
            retry_resp = client.responses.create(model=settings.openai_model, input=retry_prompt)
        except Exception as exc:
            logger.warning(
                "Outreach corrective retry failed for %s (%s); "
                "using capability-led fallback",
                company, type(exc).__name__,
            )
            subject = _capability_fallback_subject(company)
            body = _capability_fallback_body(company, fit_reason, outreach_angle)
            return subject, body
        retry_raw = retry_resp.output_text.strip()
        subject, body = _parse_outreach_json(retry_raw, company)
        logger.info("Outreach retry generated for %s", company)

        # If the retry STILL contains a forbidden name, use the safe fallback.
        if _contains_forbidden_project(subject, company=company) or _contains_forbidden_project(body, company=company):
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
