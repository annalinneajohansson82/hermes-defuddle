"""Defuddle content extraction — plugin form.

Extract-only provider that shells out to the locally-installed ``defuddle``
CLI (https://www.npmjs.com/package/defuddle). No API key, no network service
— defuddle runs locally, fetches the URL, and returns clean article markdown.

``supports_search()`` returns False — pair with SearXNG or another search
provider via per-capability config::

    web:
      search_backend: "searxng"
      extract_backend: "defuddle"

Config keys this provider responds to::

    web:
      extract_backend: "defuddle"   # explicit per-capability
      backend: "defuddle"           # shared fallback (if search-only)

No env vars required. The ``defuddle`` binary must be on ``$PATH``.

The provider shells out via ``asyncio.create_subprocess_exec`` and parses
the ``--json`` output, which includes ``contentMarkdown``, ``title``,
``description``, ``domain``, ``wordCount``, etc.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from typing import Any, Dict, List

from agent.web_search_provider import WebSearchProvider

logger = logging.getLogger(__name__)

# Timeout for the defuddle CLI subprocess (seconds)
_DEFUDDLE_TIMEOUT = 60


class DefuddleWebExtractProvider(WebSearchProvider):
    """Extract page content via the locally-installed defuddle CLI."""

    @property
    def name(self) -> str:
        return "defuddle"

    @property
    def display_name(self) -> str:
        return "Defuddle"

    def is_available(self) -> bool:
        """Return True when the ``defuddle`` binary is on PATH."""
        return shutil.which("defuddle") is not None

    def supports_search(self) -> bool:
        return False

    def supports_extract(self) -> bool:
        return True

    async def extract(self, urls: List[str], **kwargs: Any) -> List[Dict[str, Any]]:
        """Extract content from URLs using defuddle parse --json.

        Runs defuddle once per URL in parallel via asyncio.
        """
        tasks = [self._extract_one(url) for url in urls]
        results = await asyncio.gather(*tasks)
        return list(results)

    async def _extract_one(self, url: str) -> Dict[str, Any]:
        """Extract a single URL via defuddle."""
        if not self.is_available():
            return {
                "url": url,
                "title": "",
                "content": "",
                "raw_content": "",
                "error": "defuddle binary not found on PATH",
            }

        try:
            proc = await asyncio.create_subprocess_exec(
                "defuddle", "parse", "--json", url,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=_DEFUDDLE_TIMEOUT
            )
        except asyncio.TimeoutError:
            return {
                "url": url,
                "title": "",
                "content": "",
                "raw_content": "",
                "error": f"defuddle timed out after {_DEFUDDLE_TIMEOUT}s",
            }
        except Exception as exc:
            logger.warning("defuddle subprocess error for %s: %s", url, exc)
            return {
                "url": url,
                "title": "",
                "content": "",
                "raw_content": "",
                "error": f"defuddle failed: {exc}",
            }

        if proc.returncode != 0:
            err_msg = stderr.decode("utf-8", errors="replace").strip()
            logger.warning("defuddle exit %d for %s: %s", proc.returncode, url, err_msg)
            return {
                "url": url,
                "title": "",
                "content": "",
                "raw_content": "",
                "error": f"defuddle exited {proc.returncode}: {err_msg}",
            }

        try:
            data = json.loads(stdout)
        except json.JSONDecodeError as exc:
            logger.warning("defuddle JSON parse error for %s: %s", url, exc)
            return {
                "url": url,
                "title": "",
                "content": "",
                "raw_content": "",
                "error": f"defuddle returned invalid JSON: {exc}",
            }

        content_md = data.get("contentMarkdown", "") or data.get("content", "")
        title = data.get("title", "") or ""
        description = data.get("description", "") or ""

        # Build metadata from defuddle's rich output
        metadata: Dict[str, Any] = {
            "domain": data.get("domain", ""),
            "site": data.get("site", ""),
            "language": data.get("language", ""),
            "word_count": data.get("wordCount", 0),
            "author": data.get("author", ""),
            "published": data.get("published", ""),
        }

        # content field gets the markdown; raw_content gets the HTML if available
        result: Dict[str, Any] = {
            "url": url,
            "title": title,
            "content": content_md,
            "raw_content": data.get("content", ""),  # HTML content
            "metadata": metadata,
        }

        # Include description as a prefix if it's different from title
        if description and description != title:
            result["content"] = f"> {description}\n\n{content_md}"

        logger.info(
            "defuddle extract %s: %d chars markdown, %d words",
            url,
            len(content_md),
            data.get("wordCount", 0),
        )

        return result

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "Defuddle",
            "badge": "free · local",
            "tag": "Local content extraction via defuddle CLI. No API key needed — install with npm i -g defuddle.",
            "env_vars": [],
        }
