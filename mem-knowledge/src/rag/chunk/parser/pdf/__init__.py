"""PDF parser adapters supported by the knowledge worker."""

from .plain import PlainPdfParser
from .textln import TextLnPdfParser

__all__ = ["PlainPdfParser", "TextLnPdfParser"]
