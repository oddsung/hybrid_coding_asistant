"""Configuration schema for free-llm-coder.

Single source of truth for default service definitions, plus validation and
light migration of the user's ``config.yaml``. Keeping selectors and limit
keywords here (and ultimately in the user's config) means a site change can be
fixed by editing config instead of code.
"""
from copy import deepcopy
from typing import List

REQUIRED_SELECTOR_KEYS = ("input_area", "submit_button", "response_container")

# Lowercased text fragments that commonly signal a usage/rate limit. Matched
# case-insensitively against page text. A few localized variants are included
# because these chat UIs are translated per region.
COMMON_LIMIT_KEYWORDS = [
    "reached your limit",
    "you've reached",
    "you have reached",
    "rate limit",
    "usage limit",
    "limit reached",
    "upgrade to continue",
    "too many requests",
    "사용량",
    "한도",
    "请求过于频繁",
]

# Text fragments that signal a human-verification challenge (slider captcha,
# "verify you are human", ...). The tool never tries to defeat these; it
# detects them so it can fail fast in headless mode (rotate to another
# service) or tell the user to complete the check in the visible window.
COMMON_CHALLENGE_KEYWORDS = [
    "drag the slider",
    "슬라이더를 드래그",
    "밀어서 확인",
    "인증을 완료해",
    "安全验证",
    "verify you are human",
    "i'm not a robot",
    "unusual activity",
    "captcha",
]

DEFAULT_BROWSER = {"headless": False}

DEFAULT_CONTEXT = {
    "max_files": 25,
    "max_chars": 60000,
    "ignore_patterns": ["*.pyc", "__pycache__", ".git", "node_modules", "venv", ".idea", ".vscode"],
}

# Known drivers and their default config. ``limit_indicators`` carries optional
# CSS selectors and extra text keywords used by BaseDriver.is_limit_reached.
#
# A service may set ``driver`` to pick its driver class explicitly; without it
# the service ``name`` is looked up in DRIVER_REGISTRY and unknown names fall
# back to the config-only GenericDriver. This is how a user can add ANY chat
# site from config alone: give it a name, URL, selectors and (optionally)
# ``selectors.generating_indicator`` -- no code required.
DEFAULT_SERVICES = {
    "chatgpt": {
        "name": "chatgpt",
        "url": "https://chatgpt.com",
        "priority": 1,
        "selectors": {
            "input_area": "#prompt-textarea",
            "submit_button": "button[data-testid='send-button']",
            "response_container": ".markdown",
            "error_message": ".text-red-500",
            # No model_menu: the Free-tier ChatGPT UI has no model picker
            # (only a "Think" toggle). Plus accounts can enable /models by
            # adding e.g.:
            #   model_menu: "button[data-testid='model-switcher-dropdown-button']"
            # after verifying it with `flc doctor -s chatgpt`.
        },
        "limit_indicators": {
            "selectors": [],
            "keywords": ["you've reached our limit", "message limit", "get plus"],
        },
    },
    "gemini": {
        "name": "gemini",
        "url": "https://gemini.google.com",
        "priority": 2,
        "selectors": {
            "input_area": "div[contenteditable='true']",
            # aria-labels are localized (Korean UI says something else), so
            # prefer the stable class and keep the label as an alternative.
            "submit_button": "button.send-button, button[aria-label='Send message']",
            "response_container": "model-response",
            "error_message": None,
            # Model picker in the composer ("Flash-Lite ⌄"); verified live.
            "model_menu": "button.input-area-switch",
            "model_item": "[role='menuitem']",
        },
        "limit_indicators": {
            "selectors": [],
            "keywords": ["capacity"],
        },
    },
    "qwen": {
        "name": "qwen",
        "url": "https://chat.qwen.ai",
        "priority": 3,
        "selectors": {
            # 2026-09 UI: the input lost its #chat-input id; keep the old
            # selector as a comma-alternative for older deployments.
            "input_area": "textarea.message-input-textarea, #chat-input",
            "submit_button": ".send-button",
            "response_container": ".qwen-chat-message-assistant",
            "error_message": None,
            # Model picker (top bar) and composer mode/tool menu; verified live.
            "model_menu": "[aria-label='Select Model']",
            "model_item": "[role='option']",
            "mode_menu": ".mode-select-open",
            "mode_item": "[role='menuitem']",
        },
        # Qwen's cookie-consent banner intercepts clicks page-wide.
        "dismiss_selectors": ["text=필수 쿠키만 사용"],
        "limit_indicators": {
            "selectors": [],
            "keywords": [],
        },
    },
    "grok": {
        "name": "grok",
        "url": "https://grok.com",
        "priority": 4,
        "selectors": {
            "input_area": "textarea",
            "submit_button": "button.group.flex.flex-col.justify-center.rounded-full",
            "response_container": 'div[data-testid="messageGroup"], div.message-content',
            "error_message": None,
        },
        "limit_indicators": {
            "selectors": [],
            "keywords": ["try again later"],
        },
    },
    "deepseek": {
        "name": "deepseek",
        "url": "https://chat.deepseek.com",
        "priority": 5,
        "selectors": {
            "input_area": "textarea",
            "submit_button": 'div[role="button"]:has(path)',
            "response_container": ".ds-markdown",
            "error_message": None,
        },
        "limit_indicators": {
            "selectors": [],
            "keywords": ["server is busy", "服务器繁忙"],
        },
    },
    # GLM (Zhipu AI) international chat. Uses the config-only GenericDriver;
    # verify/adjust selectors with `flc doctor -s glm` after logging in.
    "glm": {
        "name": "glm",
        "url": "https://chat.z.ai",
        "priority": 6,
        "driver": "generic",
        "selectors": {
            "input_area": "#chat-input",
            "submit_button": "#send-message-button",
            "response_container": ".chat-assistant",
            "generating_indicator": "#stop-response-button",
            "error_message": None,
        },
        "limit_indicators": {
            "selectors": [],
            "keywords": ["daily limit", "usage cap"],
        },
    },
    # Kimi (Moonshot AI). Uses the config-only GenericDriver; verify/adjust
    # selectors with `flc doctor -s kimi` after logging in.
    "kimi": {
        "name": "kimi",
        "url": "https://www.kimi.ai",
        "priority": 7,
        "driver": "generic",
        "selectors": {
            "input_area": ".chat-input-editor",
            "submit_button": ".send-button",
            "response_container": ".segment-assistant .markdown",
            "generating_indicator": ".stop-button",
            "error_message": None,
        },
        "limit_indicators": {
            "selectors": [],
            "keywords": ["达到上限", "次数已用完"],
        },
    },
}


def build_default_config() -> dict:
    """Return a fresh default config dict (for first-time setup)."""
    return {
        "browser": deepcopy(DEFAULT_BROWSER),
        "services": [deepcopy(svc) for svc in DEFAULT_SERVICES.values()],
        "context": deepcopy(DEFAULT_CONTEXT),
    }


def ensure_services(config: dict) -> bool:
    """Inject any known driver missing from the user's config.

    Returns True if the config dict was modified so the caller can decide
    whether to persist it. Existing service entries (and any selectors the
    user customized) are left untouched.
    """
    services = config.setdefault("services", [])
    present = {s.get("name") for s in services if isinstance(s, dict)}
    modified = False
    for name, default in DEFAULT_SERVICES.items():
        if name not in present:
            services.append(deepcopy(default))
            modified = True
    return modified


def validate_config(config: dict) -> List[str]:
    """Return a list of human-readable problems. Empty list means the config is valid."""
    errors: List[str] = []
    if not isinstance(config, dict):
        return ["config root is not a mapping"]

    if not isinstance(config.get("browser"), dict):
        errors.append("missing or invalid 'browser' section")

    services = config.get("services")
    if not isinstance(services, list) or not services:
        errors.append("'services' must be a non-empty list")
        return errors

    seen_priorities = {}
    for i, svc in enumerate(services):
        where = f"services[{i}]"
        if not isinstance(svc, dict):
            errors.append(f"{where}: not a mapping")
            continue
        name = svc.get("name", f"<index {i}>")
        for key in ("name", "url", "priority", "selectors"):
            if key not in svc:
                errors.append(f"{where} ('{name}'): missing key '{key}'")

        driver = svc.get("driver")
        if driver is not None and not isinstance(driver, str):
            errors.append(f"{where} ('{name}'): 'driver' must be a string")

        svc_headless = svc.get("headless")
        if svc_headless is not None and not isinstance(svc_headless, bool):
            errors.append(f"{where} ('{name}'): 'headless' must be true or false")

        dismiss = svc.get("dismiss_selectors")
        if dismiss is not None and (
            not isinstance(dismiss, list)
            or not all(isinstance(s, str) for s in dismiss)
        ):
            errors.append(f"{where} ('{name}'): 'dismiss_selectors' must be a list of strings")

        prio = svc.get("priority")
        if prio is not None and not isinstance(prio, int):
            errors.append(f"{where} ('{name}'): 'priority' must be an integer")
        elif isinstance(prio, int):
            if prio in seen_priorities:
                errors.append(
                    f"service '{name}' has duplicate priority {prio} (also used by '{seen_priorities[prio]}')"
                )
            else:
                seen_priorities[prio] = name

        selectors = svc.get("selectors")
        if not isinstance(selectors, dict):
            errors.append(f"{where} ('{name}'): 'selectors' must be a mapping")
        else:
            for key in REQUIRED_SELECTOR_KEYS:
                if not selectors.get(key):
                    errors.append(f"{where} ('{name}'): selectors.{key} is missing or empty")

    ctx = config.get("context")
    if isinstance(ctx, dict):
        for key in ("max_files", "max_chars"):
            val = ctx.get(key)
            if val is not None and (not isinstance(val, int) or val <= 0):
                errors.append(f"context.{key} must be a positive integer")

    return errors
