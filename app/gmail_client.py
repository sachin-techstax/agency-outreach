from __future__ import annotations

import base64
from email.message import EmailMessage

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from .config import settings

SCOPES = ["https://www.googleapis.com/auth/gmail.compose"]


def credentials() -> Credentials:
    creds = None
    if settings.gmail_token_file.exists():
        creds = Credentials.from_authorized_user_file(str(settings.gmail_token_file), SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    if not creds or not creds.valid:
        if not settings.gmail_client_secret.exists():
            raise RuntimeError(
                f"Missing {settings.gmail_client_secret}. Download an OAuth desktop client JSON from Google Cloud and place it here."
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(settings.gmail_client_secret), SCOPES)
        creds = flow.run_local_server(port=0)
        settings.gmail_token_file.write_text(creds.to_json())
    return creds


def create_draft(to: str, subject: str, body: str) -> str:
    if not to:
        raise ValueError("Contact email is empty")
    service = build("gmail", "v1", credentials=credentials())
    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    draft = service.users().drafts().create(userId="me", body={"message": {"raw": raw}}).execute()
    return draft["id"]
