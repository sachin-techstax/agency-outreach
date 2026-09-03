from app.scoring import score_agency


def test_strong_ai_agency_scores_high():
    text = (
        "We are an AI development agency providing custom software and AI development services "
        "for clients. We build AI agents, workflow automation, RAG systems, and LLM applications. "
        "See our case studies and client projects. Our delivery team helps companies with "
        "AI implementation and system integration. We are a technology partner and "
        "development partner offering engineering services. We also do machine learning "
        "and data engineering with Python and FastAPI."
    )
    assert score_agency(text).value >= 70


def test_branding_agency_penalized():
    text = "Branding agency focused on logo design and graphic design."
    assert score_agency(text).value < 30


def test_saas_platform_scores_low():
    text = (
        "AI agent platform for building conversational AI. Start free today. "
        "Pricing plans for every team. Our SaaS platform offers self-service "
        "deployment. Subscribe to our developer platform today. API documentation "
        "and developer tools available."
    )
    assert score_agency(text).value < 70
