"""LLM web-service drivers.

``DRIVER_REGISTRY`` maps a driver key (used as ``driver:`` in config, or as
the service ``name`` itself) to its driver class. Resolution order for a
service config entry (see :func:`resolve_driver_class`):

1. explicit ``driver:`` key in the service config,
2. the service ``name`` looked up in the registry,
3. fallback to :class:`GenericDriver`, which is driven purely by config
   selectors -- so brand-new services can be added without touching code.
"""
from .base import BaseDriver
from .chatgpt import ChatGPTDriver
from .gemini import GeminiDriver
from .qwen import QwenDriver
from .grok import GrokDriver
from .deepseek import DeepSeekDriver
from .generic import GenericDriver

DRIVER_REGISTRY: dict[str, type[BaseDriver]] = {
    "chatgpt": ChatGPTDriver,
    "gemini": GeminiDriver,
    "qwen": QwenDriver,
    "grok": GrokDriver,
    "deepseek": DeepSeekDriver,
    "generic": GenericDriver,
}


def resolve_driver_class(service_cfg: dict) -> type[BaseDriver]:
    """Pick the driver class for a service config entry.

    An explicit ``driver:`` key wins; otherwise the service name is looked up;
    unknown names get the config-only GenericDriver. Raises ValueError only
    when an explicit ``driver:`` names something that does not exist (a typo
    the user should hear about rather than silently getting generic behavior).
    """
    explicit = service_cfg.get("driver")
    if explicit:
        cls = DRIVER_REGISTRY.get(explicit)
        if cls is None:
            raise ValueError(
                f"Unknown driver '{explicit}' for service "
                f"'{service_cfg.get('name', '?')}'. "
                f"Known drivers: {', '.join(sorted(DRIVER_REGISTRY))}"
            )
        return cls
    return DRIVER_REGISTRY.get(service_cfg.get("name", ""), GenericDriver)


__all__ = [
    "BaseDriver",
    "ChatGPTDriver",
    "GeminiDriver",
    "QwenDriver",
    "GrokDriver",
    "DeepSeekDriver",
    "GenericDriver",
    "DRIVER_REGISTRY",
    "resolve_driver_class",
]
