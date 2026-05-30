#!/usr/bin/env python3
"""Local dev harness: authenticate to the MoIP controller and dump discovery.

DEV ONLY. Reads credentials from a project-root `.env` (see `.env.example`),
never from hardcoded values. This runs the API client OUTSIDE Home Assistant so
we can iterate on auth/discovery against the real controller without the HA UI.

In production, the integration gets credentials from the HA config entry, not
from `.env` — see docs/development.md.

Usage:
    pip install -r requirements-dev.txt
    cp .env.example .env   # then edit .env with real values
    python scripts/dev_discover.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Make the custom_components package importable when run from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "custom_components"))


def _load_env() -> dict[str, object]:
    """Load and validate MoIP_* credentials from the project-root .env."""
    try:
        from dotenv import load_dotenv
    except ModuleNotFoundError:
        sys.exit("python-dotenv not installed. Run: pip install -r requirements-dev.txt")

    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        sys.exit(f"No .env found at {env_path}. Copy .env.example to .env and fill it in.")
    load_dotenv(env_path)

    missing = [k for k in ("MOIP_HOST", "MOIP_USERNAME", "MOIP_PASSWORD") if not os.getenv(k)]
    if missing:
        sys.exit(f"Missing required vars in {env_path}: {', '.join(missing)}")

    return {
        "host": os.environ["MOIP_HOST"],
        "port": int(os.getenv("MOIP_PORT", "443")),
        "username": os.environ["MOIP_USERNAME"],
        "password": os.environ["MOIP_PASSWORD"],
        "verify_ssl": os.getenv("MOIP_VERIFY_SSL", "false").lower() in ("1", "true", "yes"),
    }


async def _main() -> None:
    from aiohttp import ClientSession

    from binary_moip.api import BinaryMoIPClient  # noqa: PLC0415

    cfg = _load_env()
    print(f"Connecting to MoIP controller at {cfg['host']}:{cfg['port']} "
          f"(verify_ssl={cfg['verify_ssl']}) as {cfg['username']}...")

    async with ClientSession() as session:
        client = BinaryMoIPClient(
            session,
            cfg["host"],
            port=cfg["port"],
            username=cfg["username"],
            password=cfg["password"],
            verify_ssl=cfg["verify_ssl"],
        )
        try:
            await client.authenticate()
            topology = await client.async_discover()
        except NotImplementedError:
            print("\nClient auth/discovery not implemented yet — credential "
                  "plumbing is in place and ready. Implement BinaryMoIPClient "
                  "next, then re-run this script.")
            return

        print(f"\nUnits:   {len(topology.units)}")
        print(f"Zones:   {len(topology.zones)}")
        print(f"Sources: {len(topology.sources)}")
        for zone in topology.zones.values():
            print(f"  zone {zone.group_id}: {zone.name}")


if __name__ == "__main__":
    asyncio.run(_main())
