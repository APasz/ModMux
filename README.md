# ModMux

[![PyPI](https://img.shields.io/pypi/v/modmux.svg?style=for-the-badge)](https://pypi.org/project/modmux/)
[![Python](https://img.shields.io/badge/python-3.13%2B-blue.svg?style=for-the-badge)](https://pypi.org/project/modmux/)
[![License](https://img.shields.io/github/license/APasz/ModMux.svg?style=for-the-badge)](LICENSE)
[![CI](https://img.shields.io/github/actions/workflow/status/APasz/ModMux/ci.yml?style=for-the-badge)](https://github.com/APasz/ModMux/actions/workflows/ci.yml)

Unified async client for multiple game mod platforms. ModMux normalises provider responses into shared Pydantic models and ships a small CLI for quick lookups.

## Features
- Async HTTP client with rate limiting and retry handling
- Normalised `Mod` metadata model across providers
- Pluggable provider registry
- URL parsing helpers for provider links
- CLI for fetching a single mod and printing JSON

## Supported providers
- Modrinth
- CurseForge
- Nexus Mods
- mod.io
- Steam Workshop
- Factorio Mod Portal (Wube)

## Requirements
- Python 3.13+
- Dependencies: `pydantic`, `httpx`, `aiolimiter`

## Install
```bash
python -m pip install modmux
```

### Credentials and environment variables
- `--token` accepts API tokens/keys.
- `--user` supplies a user id for providers that need user-scoped base URLs (mod.io).
- Environment variables:
  - `MODMUX_TOKEN` (fallback for all providers)
  - `MODMUX_<PROVIDER>_TOKEN` (provider-specific, e.g. `MODMUX_MODRINTH_TOKEN`)
  - `MODMUX_<PROVIDER>_USER` (provider-specific user id, e.g. `MODMUX_MODIO_USER`)

## Library usage
Two supported patterns are available: an async context manager or a manually
closed client.

```python
import asyncio

from modmux import ModID, Muxer, Provider

mod_id = ModID(provider=Provider.MODRINTH, id="fabric-api")


async def run() -> None:
    async with Muxer(creds={Provider.MODRINTH: {"token": "token"}}) as mux:
        mod = await mux.get_mod(Provider.MODRINTH, mod_id)
    print(mod.name)


asyncio.run(run())
```

```python
import asyncio

from modmux import ModID, Muxer, Provider

mod_id = ModID(provider=Provider.MODRINTH, id="fabric-api")


async def run() -> None:
    mux = Muxer()
    try:
        mod = await mux.get_mod(Provider.MODRINTH, mod_id)
    finally:
        await mux.aclose()
    print(mod.name)


asyncio.run(run())
```

The `modmux_client(...)` helper remains available as a convenience wrapper
around `Muxer` for existing code.

## URL parsing
Parse provider URLs into `ModID` instances, or fetch directly from a URL.

```python
import asyncio

from modmux import Muxer, parse_url


async def run_mod_id() -> None:
    async with Muxer() as mux:
        mod = await mux.get_mod_from_url("https://modrinth.com/mod/fabric-api")
    print(mod.name)

async def run_from_url() -> None:
    mod_id = parse_url("https://modrinth.com/mod/fabric-api")
    if mod_id:
        async with Muxer() as mux:
            mod = await mux.get_mod(mod_id.provider, mod_id)
        print(mod.name)

asyncio.run(run())
```

Some providers require extra context (game ids or credentials); use
`get_mod_from_url(..., game="...")` when the URL cannot supply it.

## CLI usage
The CLI expects a provider name and provider-specific mod id. Provider names are case-insensitive.

```bash
modmux MODRINTH fabric-api --pretty
modmux CURSEFORGE 238222 --pretty
modmux NEXUSMODS 12345 --game transportfever2
modmux MODIO some-mod --game 4321 --user 12345 --token <api-key>
```

Or without installing a script:

```bash
python -m modmux MODRINTH fabric-api --pretty
```

Output is a JSON serialisation of the `Mod` model.

## Provider notes
- CurseForge: slug lookups require `ModID.game` (game id).
- Nexus Mods: requires `ModID.game` (game domain, e.g. `skyrim`).
- mod.io: requires `ModID.game` plus a user id (use `--user` or `MODMUX_MODIO_USER`).
- Steam: uses a Workshop published file id; `ModID.game` is optional.
- Wube: uses the Factorio mod name slug.
