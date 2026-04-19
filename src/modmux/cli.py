"""CLI entrypoint for ModMux."""

import argparse
import asyncio
import json
import logging
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from ._log import get_logger
from .client import Muxer
from .models import Mod, ModID, Provider, ProviderCreds
from .modmux_errors import BatchResponseError
from .utils.urls import parse_url

log = get_logger("cli")
_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]


def _parse_provider(value: str) -> Provider:
    cleaned = value.strip().upper()
    try:
        return Provider[cleaned]
    except KeyError as exc:
        raise argparse.ArgumentTypeError(f"Unknown provider: {value!r}") from exc


def _build_creds(
    providers: Sequence[Provider],
    *,
    token: str | None,
    user: str | None,
) -> dict[Provider, ProviderCreds | dict[str, str] | None] | None:
    unique_providers: list[Provider] = []
    for provider in providers:
        if provider not in unique_providers:
            unique_providers.append(provider)

    creds: dict[Provider, ProviderCreds | dict[str, str] | None] = {}
    for provider in unique_providers:
        resolved_token = token or os.getenv(f"MODMUX_{provider.value}_TOKEN") or os.getenv("MODMUX_TOKEN")
        resolved_user = user or os.getenv(f"MODMUX_{provider.value}_USER")
        payload: dict[str, str] = {}
        if resolved_token:
            payload["token"] = resolved_token
        if resolved_user:
            payload["user"] = resolved_user
        if payload:
            creds[provider] = payload

    return creds or None


def _resolve_dotenv_path(
    path: str | os.PathLike[str] | None = None,
    *,
    anchors: Sequence[str | os.PathLike[str]] = (),
) -> Path | None:
    if path is not None:
        env_path = Path(path).expanduser()
        if env_path.is_file():
            return env_path
        return None

    searched: set[Path] = set()
    for anchor in (Path.cwd(), _REPO_ROOT, *anchors):
        anchor_path = Path(anchor).expanduser().resolve()
        directory = anchor_path if anchor_path.is_dir() else anchor_path.parent
        if directory in searched:
            continue
        searched.add(directory)
        env_path = directory / ".env"
        if env_path.is_file():
            return env_path
    return None


def _load_dotenv(
    path: str | os.PathLike[str] | None = None,
    *,
    anchors: Sequence[str | os.PathLike[str]] = (),
) -> None:
    env_path = _resolve_dotenv_path(path, anchors=anchors)
    if env_path is None:
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        name = key.strip()
        if not name or name in os.environ:
            continue
        cleaned_value = value.strip()
        if len(cleaned_value) >= 2 and cleaned_value[0] == cleaned_value[-1] and cleaned_value[0] in {"'", '"'}:
            cleaned_value = cleaned_value[1:-1]
        os.environ[name] = cleaned_value


def _load_urls(path: str) -> list[str]:
    urls: list[str] = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            cleaned = line.strip()
            if not cleaned or cleaned.startswith("#"):
                continue
            urls.append(cleaned)
    return urls


async def _get_mods_from_urls(
    cli: Muxer,
    urls: Sequence[str],
    *,
    game: str | None = None,
) -> list[Mod]:
    indexed_ids: list[tuple[int, ModID]] = []
    for index, url in enumerate(urls):
        mod_id = parse_url(url)
        if mod_id is None:
            raise ValueError(f"Unsupported mod URL: {url!r}")
        if game is not None and mod_id.game is None:
            mod_id = ModID(provider=mod_id.provider, id=mod_id.id, game=game)
        indexed_ids.append((index, mod_id))

    grouped: dict[Provider, list[tuple[int, ModID]]] = {}
    provider_order: list[Provider] = []
    for index, mod_id in indexed_ids:
        provider = mod_id.provider
        if provider not in grouped:
            grouped[provider] = []
            provider_order.append(provider)
        grouped[provider].append((index, mod_id))

    results: dict[int, Mod] = {}
    for provider in provider_order:
        entries = grouped[provider]
        mods = await cli.get_mods(provider, [mod_id for _, mod_id in entries])
        if len(mods) != len(entries):
            raise BatchResponseError(
                f"{provider}: expected {len(entries)} mods from bulk lookup, received {len(mods)}"
            )
        for (index, _), mod in zip(entries, mods, strict=False):
            results[index] = mod

    return [results[index] for index in range(len(indexed_ids))]


async def _run(argv: list[str] | None = None) -> int:
    """Fetch one or more mods and print a JSON summary.

    Args;
        argv: Optional CLI arguments for testing.

    Returns;
        Exit status code.
    """
    parser = argparse.ArgumentParser(description="Fetch one or more mods by provider and ID, or from a URL file.")
    parser.add_argument("provider", nargs="?", type=_parse_provider)
    parser.add_argument("mod_ids", nargs="*")
    parser.add_argument("--from-urls", help="Read mod URLs from a file, one per line.")
    parser.add_argument("--game", help="Game domain name for providers that require it (e.g. Nexus).")
    parser.add_argument("--token", help="API token/key. Falls back to MODMUX_TOKEN or MODMUX_<PROVIDER>_TOKEN.")
    parser.add_argument("--user", help="User id for providers that use user-scoped base URLs (e.g. mod.io).")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging.")
    args = parser.parse_args(argv)

    if args.from_urls:
        if args.provider is not None or args.mod_ids:
            parser.error("--from-urls cannot be combined with provider or mod ids.")
    elif args.provider is None or not args.mod_ids:
        parser.error("provider and at least one mod_id are required unless --from-urls is used.")

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)
    log.info("main")
    dotenv_anchors: list[str | os.PathLike[str]] = []
    if args.from_urls:
        dotenv_anchors.append(args.from_urls)
    _load_dotenv(anchors=dotenv_anchors)

    urls: list[str] = []
    mod_ids: list[ModID] = []
    provider: Provider | None = None

    if args.from_urls:
        urls = _load_urls(args.from_urls)
        indexed_mod_ids: list[ModID] = []
        for url in urls:
            mod_id = parse_url(url)
            if mod_id is None:
                raise ValueError(f"Unsupported mod URL: {url!r}")
            if args.game is not None and mod_id.game is None:
                mod_id = ModID(provider=mod_id.provider, id=mod_id.id, game=args.game)
            indexed_mod_ids.append(mod_id)
        creds = _build_creds([mod_id.provider for mod_id in indexed_mod_ids], token=args.token, user=args.user)
    else:
        assert args.provider is not None
        single_provider: Provider = args.provider
        provider = single_provider
        creds = _build_creds([provider], token=args.token, user=args.user)
        mod_ids = [ModID(provider=provider, id=mod_id, game=args.game) for mod_id in args.mod_ids]

    async with Muxer(creds=creds) as cli:
        if args.from_urls:
            mods = await _get_mods_from_urls(cli, urls, game=args.game)
        elif len(mod_ids) == 1:
            assert provider is not None
            mod = await cli.get_mod(provider, mod_ids[0])
            print(mod.model_dump_json(indent=4 if args.pretty else None))
            return 0
        else:
            assert provider is not None
            mods = await cli.get_mods(provider, mod_ids)
    print(json.dumps([mod.model_dump(mode="json") for mod in mods], indent=4 if args.pretty else None))
    return 0


def main() -> None:
    """Entry point for the modmux CLI."""
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
