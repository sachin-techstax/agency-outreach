from app.scoring import score_agency


def test_strong_ai_agency_scores_high():
    text = "Generative AI development agency. We build AI agents, workflow automation, custom software, RAG systems, APIs and backend products. See our client case studies."
    assert score_agency(text).value >= 70


def test_branding_agency_penalized():
    text = "Branding agency focused on logo design and graphic design."
    assert score_agency(text).value < 30
