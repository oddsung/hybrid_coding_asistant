"""Logging configuration for free-llm-coder.

A dedicated module so every entry point sets up logging the same way.
Console output is concise; the file handler always captures DEBUG so that
selector-breakage and limit-detection issues can be reverse-engineered after
the fact -- crucial for a tool whose targets (third-party chat UIs) change
without warning.
"""
import logging
from pathlib import Path

from rich.logging import RichHandler

LOGGER_NAME = "free_llm_coder"


def setup_logging(log_dir: Path, verbose: bool = False) -> logging.Logger:
    """Configure the project's root logger and return it.

    Safe to call repeatedly; re-configures by clearing previous handlers.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "free-llm-coder.log"

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    logger.propagate = False

    console_handler = RichHandler(
        rich_tracebacks=True,
        show_time=False,
        show_path=False,
        markup=False,
    )
    console_handler.setLevel(logging.DEBUG if verbose else logging.WARNING)
    logger.addHandler(console_handler)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"
    ))
    file_handler.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)

    return logger


def get_logger(name: str) -> logging.Logger:
    """Return a logger nested under the project's root logger."""
    return logging.getLogger(f"{LOGGER_NAME}.{name}")
