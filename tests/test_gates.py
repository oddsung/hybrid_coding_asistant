"""Tests for login-redirect and consent-gate detection (browser-free)."""
from types import SimpleNamespace

from free_llm_coder.drivers.generic import GenericDriver


def _driver_at(url: str) -> GenericDriver:
    d = GenericDriver({"name": "x", "selectors": {}}, "/tmp/fct_gate", headless=True)
    d.page = SimpleNamespace(url=url)
    return d


def test_login_urls_detected():
    assert _driver_at("https://chat.deepseek.com/sign_in").login_required()
    assert _driver_at("https://accounts.google.com/v3/signin").login_required()
    assert not _driver_at("https://chat.deepseek.com/").login_required()


def test_consent_gate_urls_detected():
    assert _driver_at("https://grok.com/tos-gate").manual_gate_required()
    assert not _driver_at("https://grok.com/").manual_gate_required()
    # A login page is not a consent gate (different guidance to the user).
    assert not _driver_at("https://chat.deepseek.com/sign_in").manual_gate_required()


def test_detection_survives_broken_page():
    d = GenericDriver({"name": "x", "selectors": {}}, "/tmp/fct_gate", headless=True)
    d.page = None  # page gone -> attribute errors must not escape
    assert d.login_required() is False
    assert d.manual_gate_required() is False
