from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Score:
    value: int
    reasons: list[str]


POSITIVE = [
    (25, ["ai development", "artificial intelligence", "generative ai", "llm"] , "sells AI/LLM work"),
    (20, ["automation", "ai agent", "agents", "agentic"], "sells automation/agent work"),
    (15, ["custom software", "product development", "product studio", "software development"], "custom software/product delivery"),
    (10, ["rag", "retrieval augmented", "vector database"], "RAG/retrieval capability"),
    (10, ["api", "backend", "fastapi", "python"], "backend/API engineering"),
    (10, ["hire", "hiring", "careers", "open position", "join our team"], "possible capacity/hiring signal"),
    (5, ["case study", "client", "portfolio"], "shows client delivery"),
]

NEGATIVE = [
    (-25, ["branding agency", "creative agency", "graphic design", "logo design"], "primarily creative/branding"),
    (-20, ["fortune 500", "10,000 employees", "global consulting giant"], "likely too large"),
    (-15, ["no-code only", "webflow only"], "limited engineering fit"),
]


def score_agency(text: str) -> Score:
    t = text.lower()
    score = 0
    reasons = []
    for points, terms, reason in POSITIVE + NEGATIVE:
        if any(term in t for term in terms):
            score += points
            reasons.append(f"{points:+d} {reason}")
    return Score(max(0, min(100, score)), reasons)
