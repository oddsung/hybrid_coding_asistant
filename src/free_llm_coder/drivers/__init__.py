"""LLM web-service drivers.

``DRIVER_REGISTRY`` maps a service ``name`` (as used in ``config.yaml``) to its
driver class. To add a new service: create a driver module, import its class
here, and add one entry below -- no changes needed in ServiceManager.
"""
from .base import BaseDriver
from .chatgpt import ChatGPTDriver
from .gemini import GeminiDriver
from .qwen import QwenDriver
from .grok import GrokDriver
from .deepseek import DeepSeekDriver

DRIVER_REGISTRY: dict[str, type[BaseDriver]] = {
    "chatgpt": ChatGPTDriver,
    "gemini": GeminiDriver,
    "qwen": QwenDriver,
    "grok": GrokDriver,
    "deepseek": DeepSeekDriver,
}

__all__ = [
    "BaseDriver",
    "ChatGPTDriver",
    "GeminiDriver",
    "QwenDriver",
    "GrokDriver",
    "DeepSeekDriver",
    "DRIVER_REGISTRY",
]
