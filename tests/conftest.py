"""Fakes and guards copied from liminal's tests/conftest.py: tests never read the user's real keys
or limits, and never send a request (a FakeSession answers, with the bytes of a zip here)."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def no_saved_keys(monkeypatch):
    """Tests never read the user's saved keys (their user environment variables)."""
    from liminal_lib import config

    monkeypatch.setattr(config, "saved_user_keys", lambda: {})


@pytest.fixture(autouse=True)
def no_found_limits(monkeypatch):
    """Nor the limits kept from the user's last capability scans (in their database)."""
    from liminal_lib.sources import registry

    monkeypatch.setattr(registry, "found_limits", lambda name, settings: [])


class FakeResponse:
    def __init__(self, status: int = 200, content: bytes = b"", headers: dict | None = None, text: str = ""):
        self.status_code = status
        self.headers = headers or {}
        self.content = content
        self.text = text or content.decode("latin-1", "replace")

    def json(self) -> dict:
        raise ValueError("not JSON")


class FakeSession:
    """Returns queued responses and records every request; an exception queued is raised."""

    def __init__(self, responses: list):
        self.responses = list(responses)
        self.calls: list[tuple[str, dict | None, dict]] = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, params, headers))
        if not self.responses:
            raise AssertionError(f"no answer queued for {url}")
        answer = self.responses.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer
