"""ASGI entry point for the Digisac webhook API."""

from src.api.routes import app

__all__ = ["app"]
