# Defuddle — Hermes Web Extract Plugin

Extract-only content provider for [Hermes Agent](https://hermes-agent.nousresearch.com).
Shells out to the locally-installed [`defuddle`](https://www.npmjs.com/package/defuddle) CLI
to fetch article content from URLs. Clean markdown, rich metadata, no API key.

## Features

- **Free & local** — no API key, no external service, no credits
- **Clean markdown** — strips nav, sidebars, ads, and other boilerplate
- **Rich metadata** — title, description, domain, language, word count, author, publish date
- **No truncation** — returns full page content (unlike cloud providers that cap at ~5K chars)
- **Parallel extraction** — multiple URLs fetched concurrently via asyncio

## Requirements

- `defuddle` CLI installed and on `$PATH`:

  ```sh
  npm i -g defuddle
  ```

## Installation

Copy the plugin directory to `~/.hermes/plugins/web/defuddle/`, then enable it:

```sh
hermes config set web.extract_backend defuddle
```

Make sure `web-defuddle` is in `plugins.enabled` in `~/.hermes/config.yaml`:

```yaml
plugins:
  enabled:
    - web-defuddle
```

Restart Hermes to activate.

## Usage

The provider is used automatically by the `web_extract` tool. No manual invocation needed.

Pair with a search provider (e.g. SearXNG) for a fully free search + extract stack:

```yaml
web:
  search_backend: searxng
  extract_backend: defuddle
```

## Files

| File | Purpose |
|---|---|
| `plugin.yaml` | Plugin manifest (name, version, provides) |
| `__init__.py` | Entry point — `register()` hook |
| `provider.py` | `DefuddleWebExtractProvider` — async subprocess wrapper |

## License

MIT
