from __future__ import annotations

import json

from openai import OpenAI

from .config import settings

PORTFOLIO = {
    "WingerX": "AI automation and business orchestration platform with agents, workflows, integrations, CRM and search intelligence.",
    "GradeWise": "AI interview mastery product with diagnostics, evidence-driven mastery, adaptive preparation and production backend architecture.",
    "Aegis": "Autonomous AI code review and repository hygiene agent using deterministic scanning, LLM remediation, validation and human approval.",
    "Forge Crew": "Local-first multi-agent software engineering orchestrator coordinating specialist agents, tools, validation and approval workflows.",
}


def _client() -> OpenAI | None:
    if not settings.openai_api_key:
        return None
    return OpenAI(api_key=settings.openai_api_key)


def analyze_agency(company: str, website: str, text: str) -> dict:
    client = _client()
    if client is None:
        return {
            "summary": text[:700],
            "services": "",
            "fit_reason": "Potential AI/automation delivery partner based on website keywords.",
            "proof_project": "WingerX",
            "outreach_angle": "Offer overflow AI engineering capacity for client delivery.",
        }

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
    resp = client.responses.create(model=settings.openai_model, input=prompt)
    raw = resp.output_text.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start >= 0 and end > start:
            return json.loads(raw[start:end+1])
        raise


def draft_outreach(company: str, fit_reason: str, proof_project: str, outreach_angle: str) -> tuple[str, str]:
    proof = PORTFOLIO.get(proof_project, PORTFOLIO["WingerX"])
    client = _client()
    if client is None:
        subject = f"AI delivery support for {company}"
        body = (
            f"Saw that {company} is working in AI/software delivery, and {outreach_angle.rstrip('.').lower()}. "
            f"I build production AI systems across agents, automation, RAG and FastAPI backends; {proof_project} is a relevant example: {proof} "
            "Do you ever bring in an external senior AI engineer when client delivery capacity gets tight?"
        )
        return subject, body

    prompt = f"""
Write a cold outreach email to an agency decision-maker.
Exactly 3 sentences. Direct, casual and highly professional.
No greeting. No corporate fluff. No claims not supported below.
Sentence 1: specific hook about the agency.
Sentence 2: proof and what I can deliver as overflow/white-label AI engineering capacity.
Sentence 3: one direct question that is easy to answer.
Keep the whole email under 95 words.

Agency: {company}
Why it fits: {fit_reason}
Specific angle: {outreach_angle}
Relevant proof: {proof_project} — {proof}
Portfolio URL: {settings.portfolio_url or '[portfolio URL]'}

Return strict JSON: {{"subject":"...","body":"..."}}
"""
    resp = client.responses.create(model=settings.openai_model, input=prompt)
    raw = resp.output_text.strip()
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        obj = json.loads(raw[raw.find("{"):raw.rfind("}")+1])
    return obj["subject"], obj["body"]


def draft_followup(company: str, prior_body: str) -> tuple[str, str]:
    client = _client()
    if client is None:
        return (
            f"Re: AI delivery support for {company}",
            "Just resurfacing this in case extra AI delivery capacity is useful for your team. Would it help if I sent one or two relevant builds?",
        )
    prompt = f"""
Write a 2-sentence follow-up to this cold outreach. No greeting, no guilt, no 'just checking in', no corporate fluff.
Make it useful and easy to reply to. Under 45 words.
Agency: {company}
Original email: {prior_body}
Return strict JSON: {{"subject":"...","body":"..."}}
"""
    resp = client.responses.create(model=settings.openai_model, input=prompt)
    raw = resp.output_text.strip()
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        obj = json.loads(raw[raw.find("{"):raw.rfind("}")+1])
    return obj["subject"], obj["body"]
