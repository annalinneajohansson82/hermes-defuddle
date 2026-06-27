"""Defuddle content extraction — user plugin.

Extract-only provider that shells out to the locally-installed `defuddle` CLI
to fetch article content from URLs. Produces clean markdown with metadata,
no API key required.

Register via ``plugins.enabled`` in config.yaml.
"""

from __future__ import annotations

from .provider import DefuddleWebExtractProvider


def register(ctx) -> None:
    """Register the Defuddle provider with the plugin context."""
    ctx.register_web_search_provider(DefuddleWebExtractProvider())
