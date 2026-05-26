"""Tests for config validation and migration."""
from copy import deepcopy

from free_llm_coder.config_schema import (
    DEFAULT_SERVICES,
    build_default_config,
    ensure_services,
    validate_config,
)


def test_default_config_validates():
    assert validate_config(build_default_config()) == []


def test_validate_reports_missing_required_keys():
    cfg = {"browser": {}, "services": [{"name": "x", "priority": "high"}]}
    errors = validate_config(cfg)
    assert any("priority" in e for e in errors)
    assert any("selectors" in e for e in errors)


def test_validate_detects_duplicate_priority():
    cfg = {
        "browser": {},
        "services": [
            deepcopy(DEFAULT_SERVICES["chatgpt"]),
            dict(DEFAULT_SERVICES["gemini"], priority=DEFAULT_SERVICES["chatgpt"]["priority"]),
        ],
    }
    errors = validate_config(cfg)
    assert any("duplicate priority" in e for e in errors)


def test_validate_rejects_non_positive_context_values():
    cfg = build_default_config()
    cfg["context"]["max_files"] = 0
    errors = validate_config(cfg)
    assert any("max_files" in e for e in errors)


def test_ensure_services_injects_missing_and_is_idempotent():
    cfg = {"browser": {}, "services": [deepcopy(DEFAULT_SERVICES["chatgpt"])]}
    assert ensure_services(cfg) is True
    assert {s["name"] for s in cfg["services"]} == set(DEFAULT_SERVICES)
    # Second call: nothing to add.
    assert ensure_services(cfg) is False


def test_ensure_services_does_not_overwrite_user_customizations():
    cfg = build_default_config()
    cfg["services"][0]["selectors"]["input_area"] = "#custom"
    ensure_services(cfg)
    assert cfg["services"][0]["selectors"]["input_area"] == "#custom"
