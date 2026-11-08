"""Shared application logging, configured by Django's LOGGING setting."""

import logging


def get_logger(name: str = "app") -> logging.Logger:
    """Return a logger under the configured app namespace.

    Pass __name__ to identify the module in log output. Repeated calls reuse
    the same logger without adding handlers.
    """
    if name != "app" and not name.startswith("app."):
        name = f"app.{name}"
    return logging.getLogger(name)


logger = get_logger()
