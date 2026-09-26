"""Tests for the config-driven model/mode picker plumbing (browser-free)."""
from free_llm_coder.drivers.generic import GenericDriver


def _driver(selectors: dict) -> GenericDriver:
    return GenericDriver(
        {"name": "x", "selectors": selectors}, "/tmp/fct_picker", headless=True,
    )


def test_supports_picker_reflects_config():
    d = _driver({"model_menu": "#m", "mode_menu": ".t"})
    assert d.supports_picker("model") is True
    assert d.supports_picker("mode") is True

    d2 = _driver({})
    assert d2.supports_picker("model") is False
    assert d2.supports_picker("mode") is False


def test_unconfigured_picker_is_inert_without_browser():
    # No menu selector: must return empty/None without touching a page.
    d = _driver({})
    assert d.list_models() == []
    assert d.select_model("anything") is None
    assert d.list_modes() == []
    assert d.select_mode("anything") is None


def test_item_selector_falls_back_to_role_default():
    d = _driver({"model_menu": "#m"})
    menu, items = d._picker_selectors("model")
    assert menu == "#m"
    assert "[role='option']" in items and "[role='menuitem']" in items

    d2 = _driver({"model_menu": "#m", "model_item": ".my-item"})
    assert d2._picker_selectors("model") == ("#m", ".my-item")
