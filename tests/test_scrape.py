from __future__ import annotations

from app import scrape


def test_clean_text_preserves_mailto_email_when_anchor_text_hides_address():
    html = """
    <html>
      <head><title>Example Agency</title></head>
      <body>
        <a href="mailto:contact@example.com?subject=Hello">Email us</a>
      </body>
    </html>
    """

    title, text, links = scrape.clean_text(html)

    assert title == "Example Agency"
    assert "contact@example.com" in text
    assert "mailto:contact@example.com?subject=Hello" in links


def test_crawl_company_prioritizes_contact_page_before_other_matching_links(monkeypatch):
    homepage = """
    <html><head><title>Example</title></head><body>
      <a href="/services/one">Services one</a>
      <a href="/services/two">Services two</a>
      <a href="/work/one">Work one</a>
      <a href="/case-study/one">Case study</a>
      <a href="/contact">Contact</a>
      <a href="/about">About</a>
    </body></html>
    """

    fetched: list[str] = []

    def fake_fetch_page(url: str):
        fetched.append(url)
        if url == "https://example.com":
            return scrape.clean_text(homepage)
        if url.endswith("/contact"):
            return "", "Reach us at contact@example.com", []
        return "", f"Content for {url} " + ("x" * 250), []

    monkeypatch.setattr(scrape, "fetch_page", fake_fetch_page)

    result = scrape.crawl_company("https://example.com/some-result")

    assert fetched[0] == "https://example.com"
    assert fetched[1] == "https://example.com/contact"
    assert any(url.endswith("/contact") for url, _ in result["pages"])
    assert "contact@example.com" in result["text"]
