"""Synwevo Docs' part of the licence statement and the Corresponding Source offer.

The offer belongs to the whole program, so its machinery — the release list, the archive, the
download limit, the /api/about and /api/source-code router, the licence and third-party tables —
lives in ``synwevo_core.source_offer``, where the account service, the portal and Synwevo Message
serve it too without importing this application. What only a Synwevo Docs process knows is which
PDF engine it has selected (``engine.pdf_engine``, which can also be switched at run time), so its
/api/about reports that engine rather than the one the settings would select.
"""
from __future__ import annotations

from synwevo_core import source_offer
from synwevo_core.source_offer import pymupdf_installed   # re-exported: capabilities reports it

__all__ = ["about", "pymupdf_installed", "router"]


def about() -> dict:
    """``synwevo_core.source_offer.about`` with the engine this process has selected."""
    from .engine import pdf_engine
    return source_offer.about(pdf_engine=pdf_engine.ENGINE)


def router():
    """The shared /api/about and /api/source-code router, answering /api/about with ``about``."""
    return source_offer.router(about)
