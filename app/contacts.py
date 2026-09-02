from __future__ import annotations

import re

EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
ROLE_WORDS = ["founder", "co-founder", "ceo", "cto", "head of engineering", "technical director", "head of delivery", "coo"]


def discover_contact(text: str, pages: list[tuple[str, str]], domain: str) -> dict:
    emails = []
    for source, chunk in [("website", text)] + pages:
        for email in EMAIL_RE.findall(chunk):
            e = email.lower()
            if e.endswith("@" + domain) and e not in {x[0] for x in emails}:
                emails.append((e, source))

    preferred = None
    for email, source in emails:
        local = email.split("@")[0]
        if local not in {"info", "hello", "contact", "support", "sales", "admin"}:
            preferred = (email, source)
            break
    if not preferred and emails:
        preferred = emails[0]

    lowered = text.lower()
    role = next((r.title() for r in ROLE_WORDS if r in lowered), "Founder / CTO")
    return {
        "contact_email": preferred[0] if preferred else "",
        "contact_source": preferred[1] if preferred else "",
        "contact_name": "",
        "contact_role": role,
    }
