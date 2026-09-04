"""Tests for contact hygiene — rejected emails, tiered selection, role detection,
mailto extraction, provenance, dedupe and crawl-page prioritization."""
from __future__ import annotations

from app.contacts import discover_contact
from app.scrape import _select_research_pages, score_path


DOMAIN = "company.com"
HOME_URL = "https://company.com"
CONTACT_URL = "https://company.com/contact"
ABOUT_URL = "https://company.com/about"
SERVICES_URL = "https://company.com/services"
WORK_URL = "https://company.com/work"


def _make_text(emails: list[str], domain: str = DOMAIN, roles: str = "") -> str:
    """Build site text containing the given emails."""
    email_str = " ".join(f"Contact us at {e}" for e in emails)
    return f"Welcome to our company. {roles} {email_str} We build AI solutions."


def test_media_email_rejected():
    text = _make_text(["media@company.com"])
    result = discover_contact(text, [], DOMAIN)
    assert result["contact_email"] == ""
    assert result["contact_quality"] == "none"


def test_press_email_rejected():
    text = _make_text(["press@company.com"])
    result = discover_contact(text, [], DOMAIN)
    assert result["contact_email"] == ""


def test_privacy_email_rejected():
    text = _make_text(["privacy@company.com"])
    result = discover_contact(text, [], DOMAIN)
    assert result["contact_email"] == ""


def test_careers_email_rejected():
    text = _make_text(["careers@company.com"])
    result = discover_contact(text, [], DOMAIN)
    assert result["contact_email"] == ""


def test_noreply_email_rejected():
    text = _make_text(["noreply@company.com"])
    result = discover_contact(text, [], DOMAIN)
    assert result["contact_email"] == ""


def test_support_email_rejected():
    text = _make_text(["support@company.com"])
    result = discover_contact(text, [], DOMAIN)
    assert result["contact_email"] == ""


def test_founder_preferred_over_hello():
    text = _make_text(["hello@company.com", "founder@company.com"])
    result = discover_contact(text, [], DOMAIN)
    assert result["contact_email"] == "founder@company.com"
    assert result["contact_quality"] == "high"


def test_partnerships_preferred_over_info():
    text = _make_text(["info@company.com", "partnerships@company.com"])
    result = discover_contact(text, [], DOMAIN)
    assert result["contact_email"] == "partnerships@company.com"
    assert result["contact_quality"] == "high"


def test_hello_accepted_as_fallback():
    text = _make_text(["hello@company.com"])
    result = discover_contact(text, [], DOMAIN)
    assert result["contact_email"] == "hello@company.com"
    assert result["contact_quality"] == "medium"


def test_contact_accepted_as_fallback():
    text = _make_text(["contact@company.com"])
    result = discover_contact(text, [], DOMAIN)
    assert result["contact_email"] == "contact@company.com"
    assert result["contact_quality"] == "medium"


def test_other_domain_email_rejected():
    text = _make_text(["founder@other-domain.com"])
    result = discover_contact(text, [], DOMAIN)
    assert result["contact_email"] == ""


def test_no_email_returns_empty():
    text = "Welcome to our company. We build AI solutions."
    result = discover_contact(text, [], DOMAIN)
    assert result["contact_email"] == ""
    assert result["contact_quality"] == "none"


def test_all_rejected_returns_empty():
    text = _make_text(["media@company.com", "press@company.com", "privacy@company.com"])
    result = discover_contact(text, [], DOMAIN)
    assert result["contact_email"] == ""
    assert result["contact_quality"] == "none"


def test_role_detected_from_text():
    text = _make_text(["hello@company.com"], roles="Our founder and CEO leads the team.")
    result = discover_contact(text, [], DOMAIN)
    assert result["contact_role"] != ""
    assert "Founder" in result["contact_role"] or "Ceo" in result["contact_role"].title()


def test_no_role_defaults_to_empty():
    text = _make_text(["hello@company.com"])
    result = discover_contact(text, [], DOMAIN)
    assert result["contact_role"] == ""


def test_contact_name_always_empty():
    text = _make_text(["founder@company.com"])
    result = discover_contact(text, [], DOMAIN)
    assert result["contact_name"] == ""


# ---------------------------------------------------------------------------
# Contact discovery hardening: mailto, provenance, dedupe, crawl priority
# ---------------------------------------------------------------------------


def test_email_in_visible_text_is_found():
    text = "Reach us at hello@company.com for project work."
    result = discover_contact(text, [], DOMAIN)
    assert result["contact_email"] == "hello@company.com"
    assert result["contact_quality"] == "medium"


def test_email_only_in_mailto_is_found():
    # No email in visible text; only present as a mailto href.
    text = "Welcome to our company. We build AI solutions."
    mailtos = [("mailto:hello@company.com", CONTACT_URL)]
    result = discover_contact(text, [], DOMAIN, mailtos)
    assert result["contact_email"] == "hello@company.com"


def test_mailto_with_query_parameters_is_normalized():
    text = "Welcome to our company."
    mailtos = [("mailto:hello@company.com?subject=Project", CONTACT_URL)]
    result = discover_contact(text, [], DOMAIN, mailtos)
    assert result["contact_email"] == "hello@company.com"


def test_mailto_uppercase_is_normalized_to_lowercase():
    text = "Welcome to our company."
    mailtos = [("mailto:Hello@Company.com", CONTACT_URL)]
    result = discover_contact(text, [], DOMAIN, mailtos)
    assert result["contact_email"] == "hello@company.com"


def test_duplicate_email_is_deduplicated():
    # Same email in visible text AND a mailto href AND on a crawled page.
    # When home_text is provided, the combined `text` is NOT scanned for
    # provenance, so the contact page URL wins (R1-1).
    home_text = "Welcome to Company."  # no email on homepage
    text = "Contact us at hello@company.com."  # combined text contains it
    pages = [(CONTACT_URL, "Email us at hello@company.com anytime.")]
    mailtos = [("mailto:hello@company.com", CONTACT_URL)]
    result = discover_contact(
        text, pages, DOMAIN, mailtos,
        home_text=home_text, home_url=HOME_URL,
    )
    assert result["contact_email"] == "hello@company.com"
    # Provenance is the contact page URL, NOT the homepage, because the
    # homepage text did not contain the email.
    assert result["contact_source"] == CONTACT_URL


def test_third_party_domain_email_is_rejected():
    text = "We use vendor@thirdparty.com for billing."
    mailtos = [("mailto:someone@external.com", CONTACT_URL)]
    result = discover_contact(text, [], DOMAIN, mailtos)
    assert result["contact_email"] == ""


def test_rejected_local_parts_remain_rejected_via_mailto():
    text = "Welcome."
    mailtos = [
        ("mailto:support@company.com", CONTACT_URL),
        ("mailto:privacy@company.com", CONTACT_URL),
        ("mailto:noreply@company.com", CONTACT_URL),
        ("mailto:careers@company.com", CONTACT_URL),
    ]
    result = discover_contact(text, [], DOMAIN, mailtos)
    assert result["contact_email"] == ""
    assert result["contact_quality"] == "none"


def test_contact_source_url_is_preserved():
    # Email only exists on the crawled contact page (not the homepage text).
    text = "Welcome to our company. We build AI solutions."
    pages = [(CONTACT_URL, "Reach us at hello@company.com for project work.")]
    result = discover_contact(text, pages, DOMAIN)
    assert result["contact_email"] == "hello@company.com"
    assert result["contact_source"] == CONTACT_URL


def test_higher_quality_email_beats_generic_info():
    text = "Email info@company.com for general queries."
    pages = [(ABOUT_URL, "Founder email: founder@company.com")]
    result = discover_contact(text, pages, DOMAIN)
    assert result["contact_email"] == "founder@company.com"
    assert result["contact_quality"] == "high"


def test_generic_valid_company_email_used_when_nothing_better():
    text = "Welcome to our company."
    pages = [(CONTACT_URL, "Email us at info@company.com.")]
    result = discover_contact(text, pages, DOMAIN)
    assert result["contact_email"] == "info@company.com"
    assert result["contact_quality"] == "medium"


def test_mailto_provenance_uses_page_url():
    text = "Welcome to our company."
    mailtos = [("mailto:hello@company.com", CONTACT_URL)]
    result = discover_contact(text, [], DOMAIN, mailtos)
    assert result["contact_email"] == "hello@company.com"
    assert result["contact_source"] == CONTACT_URL


# ---------------------------------------------------------------------------
# Crawl-page prioritization (deterministic path scoring)
# ---------------------------------------------------------------------------


def test_score_path_contact_variants_rank_highest():
    assert score_path("/contact") == 100
    assert score_path("/contact-us") == 100
    assert score_path("/contactus") == 100
    assert score_path("/get-in-touch") == 100
    assert score_path("/talk-to-us") == 100
    assert score_path("/connect") == 100


def test_score_path_service_below_contact():
    assert score_path("/services") < score_path("/contact")
    assert score_path("/solutions") < score_path("/contact-us")


def test_score_path_about_below_contact():
    assert score_path("/about") < score_path("/contact")
    assert score_path("/team") < score_path("/contact")


def test_score_path_ignored_fragments_return_negative():
    assert score_path("/blog/post-1") < 0
    assert score_path("/legal/privacy") < 0
    assert score_path("/jobs") < 0
    assert score_path("/news/article") < 0


def test_contact_page_ranked_above_ordinary_service_pages():
    root = "https://company.com"
    hrefs = [
        "/services/web-development",
        "/services/ai-agents",
        "/solutions/automation",
        "/work/case-studies",
        "/about",
        "/contact",
        "/blog/ai-trends",
        "/team",
    ]
    selected = _select_research_pages(root, DOMAIN, hrefs)
    # The contact page must be selected and must come first.
    assert selected[0] == "https://company.com/contact"
    assert "https://company.com/contact" in selected
    # Irrelevant blog page must never be selected.
    assert "https://company.com/blog/ai-trends" not in selected


def test_contact_page_still_crawled_when_many_internal_links_exist():
    root = "https://company.com"
    # Many service/work/about pages that would otherwise fill the cap.
    hrefs = [f"/services/service-{i}" for i in range(20)]
    hrefs += [f"/work/case-{i}" for i in range(10)]
    hrefs += [f"/about/team-{i}" for i in range(5)]
    hrefs.append("/contact")
    selected = _select_research_pages(root, DOMAIN, hrefs)
    assert "https://company.com/contact" in selected
    assert selected[0] == "https://company.com/contact"


def test_select_research_pages_dedupes_normalized_urls():
    root = "https://company.com"
    hrefs = [
        "/contact",
        "/contact/",
        "/contact#section",
    ]
    selected = _select_research_pages(root, DOMAIN, hrefs)
    # All three collapse to one normalized contact URL.
    assert selected.count("https://company.com/contact") == 1


def test_select_research_pages_dedupes_query_variants():
    # R1-2/R2-1: tracking query variants of the same content page must not
    # consume separate bounded crawl slots.  The query-free URL is preferred
    # when it appears among the links.
    root = "https://company.com"
    hrefs = [
        "/contact",
        "/contact/",
        "/contact#team",
        "/contact?utm_source=nav",
        "/contact?utm_source=footer",
        "/contact?utm_source=nav&utm_campaign=launch",
    ]
    selected = _select_research_pages(root, DOMAIN, hrefs)
    # Only one contact research target should remain after normalization.
    assert len(selected) == 1
    # The query-free /contact appeared first, so that's the fetch URL.
    assert selected[0] == "https://company.com/contact"


def test_select_research_pages_preserves_meaningful_query_in_fetch_url():
    # R2-1 Case A: a meaningful query parameter must be preserved in the
    # fetched URL.  Only tracking params are stripped for dedup.
    root = "https://company.com"
    hrefs = ["/contact?form=agency"]
    selected = _select_research_pages(root, DOMAIN, hrefs)
    assert len(selected) == 1
    assert selected[0] == "https://company.com/contact?form=agency"


def test_select_research_pages_prefers_query_free_url():
    # R2-1 Case B: when both /contact and /contact?utm_source=nav appear,
    # the clean /contact URL is preferred as the fetch URL.
    root = "https://company.com"
    hrefs = [
        "/contact?utm_source=nav",
        "/contact",
        "/contact?utm_source=footer",
    ]
    selected = _select_research_pages(root, DOMAIN, hrefs)
    assert len(selected) == 1
    # The query-free URL is preferred even though it appeared second.
    assert selected[0] == "https://company.com/contact"


def test_select_research_pages_preserves_meaningful_query_with_tracking():
    # R2-1 Case C: tracking param is ignored for dedupe, but meaningful
    # form=agency must remain in the fetched URL.
    root = "https://company.com"
    hrefs = ["/contact?form=agency&utm_source=nav"]
    selected = _select_research_pages(root, DOMAIN, hrefs)
    assert len(selected) == 1
    # The fetch URL preserves ALL query params (including tracking), but
    # the dedupe key strips tracking.  Since only one variant exists, the
    # fetch URL is the original with all params.
    assert "form=agency" in selected[0]


def test_select_research_pages_dedupes_fragment_and_slash_variants():
    # R2-1 Case D: fragments and trailing-slash variants dedupe safely.
    root = "https://company.com"
    hrefs = [
        "/contact",
        "/contact/",
        "/contact#section",
        "/contact/#team",
    ]
    selected = _select_research_pages(root, DOMAIN, hrefs)
    assert len(selected) == 1
    assert selected[0] == "https://company.com/contact"


def test_select_research_pages_meaningful_query_not_deduped():
    # Two different meaningful query params should NOT dedupe — they may
    # be different pages.
    root = "https://company.com"
    hrefs = [
        "/contact?form=agency",
        "/contact?form=partner",
    ]
    selected = _select_research_pages(root, DOMAIN, hrefs)
    # Both should remain as separate targets (different meaningful params).
    assert len(selected) == 2


def test_select_research_pages_ignores_other_domains():
    root = "https://company.com"
    hrefs = [
        "/contact",
        "https://other.com/contact",
        "https://blog.company.com/contact",  # subdomain is different domain
    ]
    selected = _select_research_pages(root, DOMAIN, hrefs)
    assert selected == ["https://company.com/contact"]


def test_select_research_pages_is_bounded():
    root = "https://company.com"
    hrefs = [f"/services/service-{i}" for i in range(50)]
    selected = _select_research_pages(root, DOMAIN, hrefs)
    from app.scrape import MAX_EXTRA_PAGES
    assert len(selected) <= MAX_EXTRA_PAGES


# ---------------------------------------------------------------------------
# R1-9: Integration tests across the scraper/contact contract.
# These model the REAL crawl_company() output shape (home_text separate from
# combined text) and assert provenance is the actual page URL, not a synthetic
# "website" label and not the combined text.
# ---------------------------------------------------------------------------

def test_integration_homepage_links_to_contact_page_visible_email():
    # Case A: homepage links to many service pages plus /contact.
    # Contact page visible text contains hello@example.com.
    # Expected: contact page fetched, email discovered, source = contact URL.
    home_text = "Welcome to Example. We build AI solutions for agencies."
    contact_text = "Reach us at hello@example.com for project inquiries."
    combined = home_text + "\n" + contact_text  # combined text also has it
    pages = [("https://example.com/contact", contact_text)]
    mailtos = []
    result = discover_contact(
        combined, pages, "example.com", mailtos,
        home_text=home_text, home_url="https://example.com",
    )
    assert result["contact_email"] == "hello@example.com"
    assert result["contact_source"] == "https://example.com/contact"


def test_integration_contact_page_only_mailto():
    # Case B: contact page contains only a mailto link (no visible email).
    home_text = "Welcome to Example."
    contact_text = "Email us"  # no visible email
    combined = home_text + "\n" + contact_text
    pages = [("https://example.com/contact", contact_text)]
    mailtos = [("mailto:hello@example.com?subject=Project", "https://example.com/contact")]
    result = discover_contact(
        combined, pages, "example.com", mailtos,
        home_text=home_text, home_url="https://example.com",
    )
    assert result["contact_email"] == "hello@example.com"
    assert result["contact_source"] == "https://example.com/contact"


def test_integration_same_email_visible_and_mailto_and_combined():
    # Case C: same contact email exists in visible page text, mailto, and
    # combined research text. Provenance must be the actual contact page URL.
    home_text = "Welcome to Example."
    contact_text = "Email us at hello@example.com anytime."
    combined = home_text + "\n" + contact_text  # combined also contains it
    pages = [("https://example.com/contact", contact_text)]
    mailtos = [("mailto:hello@example.com", "https://example.com/contact")]
    result = discover_contact(
        combined, pages, "example.com", mailtos,
        home_text=home_text, home_url="https://example.com",
    )
    assert result["contact_email"] == "hello@example.com"
    assert result["contact_source"] == "https://example.com/contact"


def test_integration_homepage_email_provenance_is_home_url():
    # When the email is ONLY on the homepage, provenance should be the actual
    # homepage URL (https://example.com), not the string "website".
    home_text = "Welcome to Example. Contact us at hello@example.com."
    combined = home_text
    pages = []
    mailtos = []
    result = discover_contact(
        combined, pages, "example.com", mailtos,
        home_text=home_text, home_url="https://example.com",
    )
    assert result["contact_email"] == "hello@example.com"
    assert result["contact_source"] == "https://example.com"


def test_integration_query_variants_do_not_consume_multiple_slots():
    # R2-1 Case D: tracking-query duplicates must not consume multiple crawl
    # slots.  The query-free URL is preferred when present.
    root = "https://example.com"
    hrefs = [
        "/contact",
        "/contact?utm_source=nav",
        "/contact?utm_source=footer",
        "/contact?utm_campaign=launch",
        "/about",
        "/services",
    ]
    selected = _select_research_pages(root, "example.com", hrefs)
    # Only one /contact target should remain.
    contact_targets = [u for u in selected if "/contact" in u]
    assert len(contact_targets) == 1
    assert contact_targets[0] == "https://example.com/contact"


# ---------------------------------------------------------------------------
# R2-2: Role detection across combined research text
# ---------------------------------------------------------------------------

def test_role_detected_from_about_team_page():
    # Homepage has no role mention. About/team page mentions CTO.
    # Combined research text contains the about page.
    # Expected: contact_role detects CTO from the combined text.
    # At the same time, email provenance must remain the contact page URL.
    home_text = "Welcome to Company. We build AI solutions."
    about_text = "Our CTO leads engineering delivery and oversees all technical projects."
    contact_text = "Email us at hello@company.com for inquiries."
    combined = home_text + "\n" + about_text + "\n" + contact_text
    pages = [
        ("https://company.com/about", about_text),
        ("https://company.com/contact", contact_text),
    ]
    result = discover_contact(
        combined, pages, DOMAIN, [],
        home_text=home_text, home_url=HOME_URL,
    )
    # Role detected from the about page via combined text.
    assert result["contact_role"] == "Cto" or result["contact_role"].lower() == "cto"
    # Email provenance is still the contact page, not corrupted by role detection.
    assert result["contact_email"] == "hello@company.com"
    assert result["contact_source"] == "https://company.com/contact"


def test_role_detection_does_not_corrupt_email_provenance():
    # Role found on /team page, email found on /contact page.
    # Both are in combined text. Provenance must be contact page URL.
    home_text = "Welcome to Company."
    team_text = "Our founder and CEO started the company in 2018."
    contact_text = "Reach us at partnerships@company.com."
    combined = home_text + "\n" + team_text + "\n" + contact_text
    pages = [
        ("https://company.com/team", team_text),
        ("https://company.com/contact", contact_text),
    ]
    result = discover_contact(
        combined, pages, DOMAIN, [],
        home_text=home_text, home_url=HOME_URL,
    )
    # Role detected from team page.
    assert result["contact_role"] != ""
    # Email provenance is the contact page.
    assert result["contact_email"] == "partnerships@company.com"
    assert result["contact_source"] == "https://company.com/contact"
