from __future__ import annotations

from dataclasses import dataclass

from .commercial_fit import score_commercial_fit


@dataclass
class Score:
    value: int
    reasons: list[str]


def score_agency(text: str) -> Score:
    """Score a crawled site for outreach target quality.

    Delegates to :func:`app.commercial_fit.score_commercial_fit` which combines
    technical relevance and commercial delivery fit into a single score that
    represents "outreach target quality" — not just AI keyword density.
    """
    fit = score_commercial_fit(text)
    return Score(value=fit.score, reasons=fit.reasons)
