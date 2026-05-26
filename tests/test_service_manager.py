"""Tests for service prioritization and the circuit breaker.

ServiceManager is constructed without touching browsers; only get_active_driver
spawns playwright, which these tests never invoke.
"""
import time

from free_llm_coder.config_schema import build_default_config
from free_llm_coder.drivers import DRIVER_REGISTRY
from free_llm_coder.manager.service_manager import (
    ServiceManager,
    FAILURE_THRESHOLD,
    COOLDOWN_SECONDS,
)


def _cfg():
    cfg = build_default_config()
    cfg["browser"]["user_data_dir"] = "/tmp/fct_ud"
    return cfg


def test_registry_covers_default_services():
    assert set(DRIVER_REGISTRY) == set(s["name"] for s in build_default_config()["services"])


def test_priority_sort_and_preferred_moves_to_front():
    cfg = _cfg()
    sm = ServiceManager(cfg)
    assert [s["name"] for s in sm.services_config] == ["chatgpt", "gemini", "qwen", "grok", "deepseek"]

    sm2 = ServiceManager(cfg, preferred_service="grok")
    assert sm2.services_config[0]["name"] == "grok"


def test_reset_rotation_picks_first_available_skipping_open():
    sm = ServiceManager(_cfg())
    sm.reset_rotation()
    assert sm.active_service_index == 0
    first = sm.services_config[0]["name"]

    for _ in range(FAILURE_THRESHOLD):
        sm.record_failure(first)
    assert sm.breakers[first]["state"] == "open"

    sm.reset_rotation()
    assert sm.active_service_index == 1


def test_cooldown_transitions_to_half_open_and_back_to_closed():
    sm = ServiceManager(_cfg())
    name = sm.services_config[0]["name"]
    for _ in range(FAILURE_THRESHOLD):
        sm.record_failure(name)

    # Simulate the cooldown having elapsed.
    sm.breakers[name]["opened_at"] = time.time() - COOLDOWN_SECONDS - 1
    sm.reset_rotation()
    assert sm.breakers[name]["state"] == "half-open"
    assert sm.active_service_index == 0

    sm.record_success(name)
    assert sm.breakers[name]["state"] == "closed"
    assert sm.breakers[name]["failures"] == 0


def test_half_open_failure_reopens_immediately():
    sm = ServiceManager(_cfg())
    name = sm.services_config[0]["name"]
    sm.breakers[name]["state"] = "half-open"
    sm.breakers[name]["failures"] = 0

    sm.record_failure(name)
    assert sm.breakers[name]["state"] == "open"


def test_rotate_service_records_failure_and_advances():
    sm = ServiceManager(_cfg())
    sm.reset_rotation()
    first = sm.services_config[0]["name"]
    sm.rotate_service()
    assert sm.breakers[first]["failures"] == 1
    assert sm.active_service_index == 1


def test_is_exhausted_when_all_breakers_open():
    sm = ServiceManager(_cfg())
    for name in list(sm.breakers):
        for _ in range(FAILURE_THRESHOLD):
            sm.record_failure(name)
    sm.reset_rotation()
    assert sm.is_exhausted()
