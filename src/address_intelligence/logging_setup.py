"""Concise standard-library logging configuration."""

import logging

from address_intelligence.config import Settings


def configure_logging(settings: Settings) -> None:
    """Configure root logging from validated settings."""
    logging.basicConfig(
        level=settings.log_level,
        format="%(levelname)s %(name)s: %(message)s",
    )
