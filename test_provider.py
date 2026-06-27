"""Tests for the Defuddle web extract plugin.

Uses unittest.mock to avoid needing the real `defuddle` binary.
Run with: python -m pytest test_provider.py -v
Or:     python -m unittest test_provider.py -v
"""

from __future__ import annotations

import asyncio
import json
import os
import unittest
from unittest.mock import MagicMock, patch

from provider import DefuddleWebExtractProvider


def _make_proc(stdout_bytes: bytes, stderr_bytes: bytes, returncode: int):
    """Build a mock asyncio subprocess result.

    Uses a real async closure for communicate() instead of AsyncMock to avoid
    'coroutine never awaited' RuntimeWarnings from mock coroutine GC.
    """
    proc = MagicMock()
    proc.returncode = returncode

    async def _communicate():
        return (stdout_bytes, stderr_bytes)

    proc.communicate = _communicate
    return proc


class TestProviderProperties(unittest.IsolatedAsyncioTestCase):
    """Test static properties and capability flags."""

    def setUp(self):
        self.provider = DefuddleWebExtractProvider()

    def test_name(self):
        assert self.provider.name == "defuddle"

    def test_display_name(self):
        assert self.provider.display_name == "Defuddle"

    def test_supports_search_false(self):
        assert self.provider.supports_search() is False

    def test_supports_extract_true(self):
        assert self.provider.supports_extract() is True

    def test_is_available_true(self):
        with patch("provider.shutil.which", return_value="/usr/bin/defuddle"):
            assert self.provider.is_available() is True

    def test_is_available_false(self):
        with patch("provider.shutil.which", return_value=None):
            assert self.provider.is_available() is False

    def test_get_setup_schema(self):
        schema = self.provider.get_setup_schema()
        assert schema["name"] == "Defuddle"
        assert schema["badge"] == "free · local"
        assert schema["env_vars"] == []
        assert "defuddle" in schema["tag"].lower()


class TestExtractSuccess(unittest.IsolatedAsyncioTestCase):
    """Test the happy path — defuddle returns valid JSON."""

    async def test_extract_single_url(self):
        """Successful extraction with contentMarkdown and metadata."""
        provider = DefuddleWebExtractProvider()
        defuddle_json = json.dumps({
            "contentMarkdown": "# Hello World\n\nThis is article text.",
            "title": "Hello World",
            "description": "A greeting article",
            "domain": "example.com",
            "site": "example",
            "language": "en",
            "wordCount": 5,
            "author": "Jane Doe",
            "published": "2025-01-15",
            "content": "<p>Hello World</p>",
        }).encode()

        proc = _make_proc(defuddle_json, b"", 0)

        with patch("provider.shutil.which", return_value="/usr/bin/defuddle"), \
             patch("provider.asyncio.create_subprocess_exec", return_value=proc):
            results = await provider.extract(["https://example.com/article"])

        assert len(results) == 1
        r = results[0]
        assert r["url"] == "https://example.com/article"
        assert r["title"] == "Hello World"
        assert "# Hello World" in r["content"]
        assert "This is article text." in r["content"]
        assert r["raw_content"] == "<p>Hello World</p>"
        assert r["metadata"]["domain"] == "example.com"
        assert r["metadata"]["language"] == "en"
        assert r["metadata"]["word_count"] == 5
        assert r["metadata"]["author"] == "Jane Doe"
        assert r["metadata"]["published"] == "2025-01-15"
        assert "error" not in r

    async def test_extract_description_prefix(self):
        """When description differs from title, it's prepended as a blockquote."""
        provider = DefuddleWebExtractProvider()
        defuddle_json = json.dumps({
            "contentMarkdown": "Body text here.",
            "title": "Title",
            "description": "A unique description",
            "wordCount": 3,
        }).encode()

        proc = _make_proc(defuddle_json, b"", 0)

        with patch("provider.shutil.which", return_value="/usr/bin/defuddle"), \
             patch("provider.asyncio.create_subprocess_exec", return_value=proc):
            results = await provider.extract(["https://example.com/a"])

        r = results[0]
        assert r["content"].startswith("> A unique description\n\n")
        assert "Body text here." in r["content"]

    async def test_extract_description_same_as_title_no_prefix(self):
        """When description equals title, no blockquote prefix is added."""
        provider = DefuddleWebExtractProvider()
        defuddle_json = json.dumps({
            "contentMarkdown": "Body text.",
            "title": "Same Title",
            "description": "Same Title",
            "wordCount": 2,
        }).encode()

        proc = _make_proc(defuddle_json, b"", 0)

        with patch("provider.shutil.which", return_value="/usr/bin/defuddle"), \
             patch("provider.asyncio.create_subprocess_exec", return_value=proc):
            results = await provider.extract(["https://example.com/a"])

        r = results[0]
        assert not r["content"].startswith(">")
        assert r["content"] == "Body text."

    async def test_extract_fallback_to_content_key(self):
        """When contentMarkdown is missing, returns an error (no HTML in content).

        Per S2: `content` must hold markdown or be empty — never raw HTML.
        The HTML is preserved in `raw_content` and an error is returned so the
        caller knows defuddle didn't produce clean markdown.
        """
        provider = DefuddleWebExtractProvider()
        defuddle_json = json.dumps({
            "content": "<p>Fallback HTML content.</p>",
            "title": "Fallback Title",
            "wordCount": 3,
        }).encode()

        proc = _make_proc(defuddle_json, b"", 0)

        with patch("provider.shutil.which", return_value="/usr/bin/defuddle"), \
             patch("provider.asyncio.create_subprocess_exec", return_value=proc):
            results = await provider.extract(["https://example.com/a"])

        r = results[0]
        # content is empty, not HTML
        assert r["content"] == ""
        # raw_content preserves the HTML
        assert r["raw_content"] == "<p>Fallback HTML content.</p>"
        # title is still surfaced
        assert r["title"] == "Fallback Title"
        # an error explains the situation
        assert "markdown" in r["error"]

    async def test_extract_multiple_urls_parallel(self):
        """Multiple URLs are extracted and returned in order."""
        provider = DefuddleWebExtractProvider()
        json_a = json.dumps({"contentMarkdown": "Article A", "title": "A", "wordCount": 1}).encode()
        json_b = json.dumps({"contentMarkdown": "Article B", "title": "B", "wordCount": 1}).encode()

        proc_a = _make_proc(json_a, b"", 0)
        proc_b = _make_proc(json_b, b"", 0)

        with patch("provider.shutil.which", return_value="/usr/bin/defuddle"), \
             patch("provider.asyncio.create_subprocess_exec",
                   side_effect=[proc_a, proc_b]):
            results = await provider.extract([
                "https://example.com/a",
                "https://example.com/b",
            ])

        assert len(results) == 2
        assert results[0]["title"] == "A"
        assert results[1]["title"] == "B"
        assert results[0]["url"] == "https://example.com/a"
        assert results[1]["url"] == "https://example.com/b"

    async def test_extract_empty_content_markdown(self):
        """Empty contentMarkdown produces empty content, no crash."""
        provider = DefuddleWebExtractProvider()
        defuddle_json = json.dumps({
            "contentMarkdown": "",
            "title": "Empty",
            "wordCount": 0,
        }).encode()

        proc = _make_proc(defuddle_json, b"", 0)

        with patch("provider.shutil.which", return_value="/usr/bin/defuddle"), \
             patch("provider.asyncio.create_subprocess_exec", return_value=proc):
            results = await provider.extract(["https://example.com/empty"])

        r = results[0]
        assert r["content"] == ""
        assert r["title"] == "Empty"


class TestExtractErrors(unittest.IsolatedAsyncioTestCase):
    """Test error paths — binary missing, timeout, non-zero exit, bad JSON."""

    async def test_binary_not_found(self):
        """When defuddle is not on PATH, returns error for each URL."""
        provider = DefuddleWebExtractProvider()

        with patch("provider.shutil.which", return_value=None):
            results = await provider.extract(["https://example.com/a"])

        r = results[0]
        assert r["url"] == "https://example.com/a"
        assert r["title"] == ""
        assert r["content"] == ""
        assert r["error"] == "defuddle binary not found on PATH"

    async def test_subprocess_timeout(self):
        """When the subprocess times out, returns a timeout error."""
        provider = DefuddleWebExtractProvider()

        # communicate is a non-async MagicMock: asyncio.wait_for is patched to
        # raise before the return value is ever awaited, so no coroutine is
        # created (avoids 'coroutine never awaited' RuntimeWarning).
        proc = MagicMock()
        proc.returncode = None
        proc.communicate = MagicMock(return_value=(b"", b""))

        with patch("provider.shutil.which", return_value="/usr/bin/defuddle"), \
             patch("provider.asyncio.create_subprocess_exec", return_value=proc), \
             patch("provider.asyncio.wait_for",
                   side_effect=asyncio.TimeoutError()):
            results = await provider.extract(["https://example.com/slow"])

        r = results[0]
        assert "timed out" in r["error"]
        assert r["content"] == ""

    async def test_subprocess_timeout_kills_proc(self):
        """M1: on timeout the subprocess is killed (no zombie leak)."""
        provider = DefuddleWebExtractProvider()

        proc = MagicMock()
        proc.returncode = None
        proc.kill = MagicMock()

        # wait() is awaited in the kill path — needs to be a real awaitable.
        async def _wait():
            return 0

        proc.wait = _wait
        # communicate is non-async (same reason as test_subprocess_timeout).
        proc.communicate = MagicMock(return_value=(b"", b""))

        with patch("provider.shutil.which", return_value="/usr/bin/defuddle"), \
             patch("provider.asyncio.create_subprocess_exec", return_value=proc), \
             patch("provider.asyncio.wait_for",
                   side_effect=asyncio.TimeoutError()):
            results = await provider.extract(["https://example.com/slow"])

        # proc.kill() must have been called to reap the zombie
        proc.kill.assert_called_once()

    async def test_subprocess_exception(self):
        """When create_subprocess_exec raises, returns a generic error."""
        provider = DefuddleWebExtractProvider()

        with patch("provider.shutil.which", return_value="/usr/bin/defuddle"), \
             patch("provider.asyncio.create_subprocess_exec",
                   side_effect=FileNotFoundError("no such file")):
            results = await provider.extract(["https://example.com/x"])

        r = results[0]
        assert "defuddle failed" in r["error"]
        assert "no such file" in r["error"]

    async def test_nonzero_exit(self):
        """Non-zero exit code produces an error with stderr."""
        provider = DefuddleWebExtractProvider()
        proc = _make_proc(b"", b"URL not found", 1)

        with patch("provider.shutil.which", return_value="/usr/bin/defuddle"), \
             patch("provider.asyncio.create_subprocess_exec", return_value=proc):
            results = await provider.extract(["https://example.com/404"])

        r = results[0]
        assert "exited 1" in r["error"]
        assert "URL not found" in r["error"]

    async def test_invalid_json(self):
        """When defuddle outputs non-JSON, returns a JSON parse error."""
        provider = DefuddleWebExtractProvider()
        proc = _make_proc(b"this is not json {{{", b"", 0)

        with patch("provider.shutil.which", return_value="/usr/bin/defuddle"), \
             patch("provider.asyncio.create_subprocess_exec", return_value=proc):
            results = await provider.extract(["https://example.com/bad"])

        r = results[0]
        assert "invalid JSON" in r["error"]

    async def test_empty_urls_list(self):
        """Empty URL list returns empty results list."""
        provider = DefuddleWebExtractProvider()
        results = await provider.extract([])
        assert results == []

    async def test_mixed_success_and_error(self):
        """One URL succeeds, one fails — both returned in order."""
        provider = DefuddleWebExtractProvider()

        good_json = json.dumps({
            "contentMarkdown": "Good content",
            "title": "Good",
            "wordCount": 2,
        }).encode()

        proc_good = _make_proc(good_json, b"", 0)
        proc_bad = _make_proc(b"", b"Connection refused", 2)

        with patch("provider.shutil.which", return_value="/usr/bin/defuddle"), \
             patch("provider.asyncio.create_subprocess_exec",
                   side_effect=[proc_good, proc_bad]):
            results = await provider.extract([
                "https://example.com/good",
                "https://example.com/bad",
            ])

        assert len(results) == 2
        assert results[0]["title"] == "Good"
        assert "error" not in results[0]
        assert "exited 2" in results[1]["error"]

    async def test_gather_isolates_base_exception(self):
        """M2: a BaseException in one task doesn't abort sibling extractions.

        return_exceptions=True lets asyncio.gather survive a CancelledError
        (BaseException) raised by one task while siblings still complete.
        """
        provider = DefuddleWebExtractProvider()

        good_json = json.dumps({
            "contentMarkdown": "Survived content",
            "title": "Survived",
            "wordCount": 2,
        }).encode()
        proc_good = _make_proc(good_json, b"", 0)

        # Patch _extract_one so the first URL raises a BaseException
        # (CancelledError is a BaseException in 3.8+) and the second goes
        # through the normal path. Using the real provider method for the
        # second URL exercises the gather isolation end-to-end.
        original_extract_one = provider._extract_one

        async def _raising_extract(url: str):
            if url == "https://example.com/boom":
                raise asyncio.CancelledError("simulated cancel")
            return await original_extract_one(url)

        with patch("provider.shutil.which", return_value="/usr/bin/defuddle"), \
             patch("provider.asyncio.create_subprocess_exec", return_value=proc_good), \
             patch.object(provider, "_extract_one", side_effect=_raising_extract):
            results = await provider.extract([
                "https://example.com/boom",
                "https://example.com/ok",
            ])

        assert len(results) == 2
        # The failed task is converted to an error dict (not re-raised)
        assert "error" in results[0]
        assert "extraction failed" in results[0]["error"]
        # The sibling succeeded despite the first task's BaseException
        assert results[1]["title"] == "Survived"
        assert "Survived content" in results[1]["content"]

    async def test_empty_stdout_guard(self):
        """S1: empty stdout with exit code 0 gives a clear 'empty output' error,
        not a confusing JSONDecodeError.
        """
        provider = DefuddleWebExtractProvider()
        proc = _make_proc(b"", b"", 0)

        with patch("provider.shutil.which", return_value="/usr/bin/defuddle"), \
             patch("provider.asyncio.create_subprocess_exec", return_value=proc):
            results = await provider.extract(["https://example.com/blank"])

        r = results[0]
        assert "empty output" in r["error"]
        assert r["content"] == ""

    async def test_multiline_description_blockquote(self):
        """S3: multi-line descriptions get '> ' prefix on every line."""
        provider = DefuddleWebExtractProvider()
        defuddle_json = json.dumps({
            "contentMarkdown": "Body text.",
            "title": "Title",
            "description": "Line one\nLine two\nLine three",
            "wordCount": 2,
        }).encode()

        proc = _make_proc(defuddle_json, b"", 0)

        with patch("provider.shutil.which", return_value="/usr/bin/defuddle"), \
             patch("provider.asyncio.create_subprocess_exec", return_value=proc):
            results = await provider.extract(["https://example.com/multi"])

        r = results[0]
        content = r["content"]
        # Every line of the description should be a blockquote line
        assert content.startswith("> Line one\n> Line two\n> Line three")
        # Body follows after the blank separator
        assert "\n\nBody text." in content


class TestRegister(unittest.TestCase):
    """Test the register() entry point."""

    def test_register_calls_provider_registration(self):
        """register() should call register_web_search_provider on the context."""
        import sys

        # Add the parent of the plugin dir to sys.path so `import defuddle`
        # resolves as a package (with relative imports working)
        pkg_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sys.path.insert(0, pkg_parent)
        try:
            import defuddle as plugin_init

            ctx = MagicMock()
            plugin_init.register(ctx)

            ctx.register_web_search_provider.assert_called_once()
            registered = ctx.register_web_search_provider.call_args[0][0]
            # Check by type name, not isinstance — the package import path
            # creates a different class object than the top-level import
            assert type(registered).__name__ == "DefuddleWebExtractProvider"
            assert registered.name == "defuddle"
        finally:
            sys.path.pop(0)
            sys.modules.pop("defuddle", None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
