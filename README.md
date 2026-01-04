# ModMux

Unified async client for multiple game mod platforms. ModMux normalises provider responses into shared Pydantic models and ships a small CLI for quick lookups.

## Features
- Async HTTP client with rate limiting and retry handling
- Normalised `Mod` metadata model across providers
- Pluggable provider registry
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
From the repo:

```bash
python -m pip install -e .
```

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

### Credentials and environment variables
- `--token` accepts API tokens/keys.
- `--user` supplies a user id for providers that need user-scoped base URLs (mod.io).
- Environment variables:
  - `MODMUX_TOKEN` (fallback for all providers)
  - `MODMUX_<PROVIDER>_TOKEN` (provider-specific, e.g. `MODMUX_MODRINTH_TOKEN`)
  - `MODMUX_<PROVIDER>_USER` (provider-specific user id, e.g. `MODMUX_MODIO_USER`)

## Library usage
```python
import asyncio

from modmux import ModID, Provider, modmux_client


async def run() -> None:
    mod_id = ModID(provider=Provider.MODRINTH, id="fabric-api")
    async with modmux_client(creds={Provider.MODRINTH: {"token": "token"}}) as mux:
        mod = await mux.get_mod(Provider.MODRINTH, mod_id)
    print(mod.name)


asyncio.run(run())
```

## Provider notes
- CurseForge: slug lookups require `ModID.game` (game id).
- Nexus Mods: requires `ModID.game` (game domain, e.g. `skyrim`).
- mod.io: requires `ModID.game` plus a user id (use `--user` or `MODMUX_MODIO_USER`).
- Steam: uses a Workshop published file id; `ModID.game` is optional.
- Wube: uses the Factorio mod name slug.
