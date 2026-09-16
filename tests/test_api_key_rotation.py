"""
The auth decorator accepts two keys so a key can be rotated without an outage.

That second slot is the risky part: it is a live credential that is supposed to
be temporary. These tests pin the three things that make it safe — both keys
work while it is set, clearing it retires the old key immediately, and an unset
API_KEY rejects everything rather than degrading into "anything matches".
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import azure.functions as func

from shared.db import require_api_key


NEW = "new-key-aaaa"
OLD = "old-key-bbbb"


@require_api_key
def handler(req):
    return func.HttpResponse(body="ok", status_code=200)


def call(key):
    headers = {"X-API-Key": key} if key is not None else {}
    req = func.HttpRequest(
        method="GET", url="https://example.invalid/api/thing",
        headers=headers, body=None,
    )
    return handler(req).status_code


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("API_KEY_OLD", raising=False)


def test_only_the_current_key_is_accepted_when_no_rotation_is_in_flight(monkeypatch):
    monkeypatch.setenv("API_KEY", NEW)
    assert call(NEW) == 200
    assert call(OLD) == 401


def test_both_keys_work_mid_rotation(monkeypatch):
    # The overlap window: callers are being moved from OLD to NEW and neither
    # side should 401 while that is happening.
    monkeypatch.setenv("API_KEY", NEW)
    monkeypatch.setenv("API_KEY_OLD", OLD)
    assert call(NEW) == 200
    assert call(OLD) == 200


def test_clearing_the_old_slot_retires_that_key(monkeypatch):
    # This is what actually completes a rotation. If this ever passes with OLD
    # still returning 200, the retired key is still live.
    monkeypatch.setenv("API_KEY", NEW)
    monkeypatch.setenv("API_KEY_OLD", "")
    assert call(NEW) == 200
    assert call(OLD) == 401


def test_an_unset_api_key_rejects_everything(monkeypatch):
    # A misconfigured app must fail closed, not open.
    assert call(NEW) == 401
    assert call("") == 401
    assert call(None) == 401


def test_a_missing_header_is_rejected_even_mid_rotation(monkeypatch):
    monkeypatch.setenv("API_KEY", NEW)
    monkeypatch.setenv("API_KEY_OLD", OLD)
    assert call(None) == 401
    assert call("") == 401
