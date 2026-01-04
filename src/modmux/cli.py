"""CLI entrypoint for ModMux."""

import argparse
import asyncio
import logging
import os

from ._log import get_logger
from .client import Muxer
from .models import ModID, Provider, ProviderCreds

log = get_logger("cli")


def _parse_provider(value: str) -> Provider:
    cleaned = value.strip().upper()
    try:
        return Provider[cleaned]
    except KeyError as exc:
        raise argparse.ArgumentTypeError(f"Unknown provider: {value!r}") from exc


async def _run(argv: list[str] | None = None) -> int:
    """Fetch a single mod and print a JSON summary.

    Args;
        argv: Optional CLI arguments for testing.

    Returns;
        Exit status code.
    """
    parser = argparse.ArgumentParser(description="Fetch a single mod by provider and ID.")
    parser.add_argument("provider", type=_parse_provider)
    parser.add_argument("mod_id")
    parser.add_argument("--game", help="Game domain name for providers that require it (e.g. Nexus).")
    parser.add_argument("--token", help="API token/key. Falls back to MODMUX_TOKEN or MODMUX_<PROVIDER>_TOKEN.")
    parser.add_argument("--user", help="User id for providers that use user-scoped base URLs (e.g. mod.io).")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)
    log.info("main")

    token = args.token or os.getenv(f"MODMUX_{args.provider.value}_TOKEN") or os.getenv("MODMUX_TOKEN")
    user = args.user or os.getenv(f"MODMUX_{args.provider.value}_USER")
    creds_payload: dict[str, str] = {}
    if token:
        creds_payload["token"] = token
    if user:
        creds_payload["user"] = user
    creds: dict[Provider, ProviderCreds | dict[str, str] | None] | None = (
        {args.provider: creds_payload} if creds_payload else None
    )
    mod_id = ModID(provider=args.provider, id=args.mod_id, game=args.game)
    async with Muxer(creds=creds) as cli:
        mod = await cli.get_mod(args.provider, mod_id)

    print(mod.model_dump_json(indent=2 if args.pretty else None))
    return 0


def main() -> None:
    """Entry point for the modmux CLI."""
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
