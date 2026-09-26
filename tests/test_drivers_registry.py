"""Tests for driver resolution (explicit key, name lookup, generic fallback)."""
import pytest

from free_llm_coder.drivers import (
    resolve_driver_class,
    ChatGPTDriver,
    GenericDriver,
)


def test_name_lookup_finds_dedicated_driver():
    assert resolve_driver_class({"name": "chatgpt"}) is ChatGPTDriver


def test_explicit_driver_key_wins_over_name():
    assert resolve_driver_class({"name": "chatgpt", "driver": "generic"}) is GenericDriver


def test_unknown_name_falls_back_to_generic():
    assert resolve_driver_class({"name": "some-new-site"}) is GenericDriver


def test_unknown_explicit_driver_raises():
    with pytest.raises(ValueError, match="Unknown driver"):
        resolve_driver_class({"name": "x", "driver": "no-such-driver"})
