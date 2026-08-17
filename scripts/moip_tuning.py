#!/usr/bin/env python3
"""Declarative MoIP per-zone audio tuning: EQ (parametric biquads) + loudness + mono.

Why this exists: none of the DSP tuning below is stored by Home Assistant or the
integration — it lives ONLY on the MoIP controller, and the controller config has
already been wiped once. This tool is the source of truth + one-command recovery.

Usage (creds are read from env, or fetched from HA — see _load_creds):
    MOIP_HOST=10.4.1.50 MOIP_USER=binary MOIP_PASS=... python3 scripts/moip_tuning.py --status
    python3 scripts/moip_tuning.py --dry-run     # show what WOULD change (default)
    python3 scripts/moip_tuning.py --apply       # push the tuning

Mechanism (Binary MoIP REST API v1.3.0, on the backing audio_rx of each zone):
  - loudness: PUT /audio_rx/{id}            {"settings": {"loudness": "default"|"none"}}
  - mono:     PUT /audio_rx/{id}            {"settings": {"mono": true|false}}
  - EQ band:  PUT /audio_rx/{id}/main_bq/{i} {"type","freq","q","gain"}   (i = 0..11)
    (NOTE: settings.biquad is NOT writable — must use the main_bq/{i} sub-resource.)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import ssl
import subprocess
import sys

from aiohttp import ClientSession, TCPConnector

# --- Tuning definition ------------------------------------------------------

# Parametric curves per speaker model. Each band -> one main_bq slot (0..11).
# type: peaking | lowshelf | highshelf | notch | bandpass | highpass | lowpass
# Empty list = flat (no EQ), but loudness/mono from GLOBAL still apply.
SPEAKER_CURVES: dict[str, list[dict]] = {
    # Episode CORE 3 6" family (in-ceiling, DVC, all-weather in-ceiling) — same
    # 6.5" driver, so one "house curve": warmth + low punch + a touch of air.
    # Validated by ear 2026-08 on the Main-amp zones.
    "ES-CORE-36-IC": [
        {"type": "lowshelf", "freq": 110, "q": 0.707, "gain": 4.0},
        {"type": "peaking", "freq": 60, "q": 0.9, "gain": 3.0},
        {"type": "highshelf", "freq": 10000, "q": 0.707, "gain": 2.0},
    ],
    # DVC and all-weather in-ceiling share the CORE 3 6" tuning until tuned apart.
    "ES-CORE-36-IC-DVCS": "ES-CORE-36-IC",
    "ES-CORE-36-AWIC": "ES-CORE-36-IC",
    # Unknown-brand 6" in-ceiling (Game Room, pre-existing) — reuse the CORE 3
    # curve as a generic 6" in-ceiling start; tune by ear.
    "GENERIC-6IC": "ES-CORE-36-IC",
    # Episode ES-500-AW-8 outdoor surface-mount (8" poly, titanium dome, 60Hz-20k,
    # 2kHz xover). Outdoor = no room gain -> more bass than CORE3; punch centered
    # above the 60Hz rolloff; a modest high shelf for distance clarity (titanium is
    # already bright, so restrained). Starting point — tune by ear.
    "ES-500-AW": [
        {"type": "lowshelf", "freq": 120, "q": 0.707, "gain": 5.0},
        {"type": "peaking", "freq": 80, "q": 0.9, "gain": 3.0},
        {"type": "highshelf", "freq": 8000, "q": 0.707, "gain": 2.0},
    ],
    None: [],  # unknown speaker -> flat, but mono + loudness still applied
}

# Applied to every zone listed below, regardless of model.
GLOBAL = {"loudness": "default", "mono": True}

# Zone map keyed by group_rx id, with the expected name (to catch a post-wipe
# re-map) and the speaker model (-> curve). gids on the reference system as of
# 2026-08; the name check warns if a gid now points at a different zone.
ZONE_MODELS: dict[int, dict] = {
    1024: {"name": "Master Bedroom", "model": "ES-CORE-36-IC"},
    1026: {"name": "Master Bathroom", "model": "ES-CORE-36-AWIC"},
    1028: {"name": "Dining Room", "model": "ES-CORE-36-IC"},
    1030: {"name": "Music Room", "model": "ES-CORE-36-IC-DVCS"},
    1032: {"name": "Kitchen", "model": "ES-CORE-36-IC"},
    1034: {"name": "Parlor", "model": "ES-CORE-36-IC-DVCS"},
    # Outdoor surface-mounts (larger, 8") — different speaker; flat until tuned by ear.
    1060: {"name": "Pool (Kitchen Side)", "model": "ES-500-AW"},
    1102: {"name": "Pool", "model": "ES-500-AW"},
    # All-weather CORE 3 in-ceiling (covered outdoor) — share the CORE 3 curve.
    1062: {"name": "Outdoor Seating", "model": "ES-CORE-36-AWIC"},
    1064: {"name": "Outdoor Kitchen 1", "model": "ES-CORE-36-AWIC"},
    1066: {"name": "Outdoor Kitchen 2", "model": "ES-CORE-36-AWIC"},
    1083: {"name": "Casita Back Porch", "model": "ES-CORE-36-AWIC"},
    # Basic CORE 3 in-ceiling.
    1068: {"name": "Garage", "model": "ES-CORE-36-IC"},
    1070: {"name": "Shop", "model": "ES-CORE-36-IC"},
    1079: {"name": "Casita Kitchen", "model": "ES-CORE-36-IC"},
    1081: {"name": "Sewing Room", "model": "ES-CORE-36-IC"},
    1098: {"name": "Workout", "model": "ES-CORE-36-IC"},
    # Unconfirmed / non-speaker outputs.
    1100: {"name": "Skip", "model": None, "skip": True},        # unused output — leave alone
    1117: {"name": "Pool Table", "model": "GENERIC-6IC"},       # pre-existing unknown 6" in-ceiling
    1145: {"name": "Outdoor TVs", "model": None, "skip": True}, # TV audio — leave stereo (RX, 0 EQ bands)
}


def _curve(model: str | None) -> list[dict]:
    c = SPEAKER_CURVES.get(model, [])
    return SPEAKER_CURVES.get(c, []) if isinstance(c, str) else c  # resolve aliases


# --- Controller client ------------------------------------------------------

def _load_creds() -> dict:
    """Creds from env, else fetched from the HA config entry over ssh."""
    if os.environ.get("MOIP_PASS"):
        return {
            "host": os.environ.get("MOIP_HOST", "10.4.1.50"),
            "port": int(os.environ.get("MOIP_PORT", "443")),
            "u": os.environ["MOIP_USER"],
            "p": os.environ["MOIP_PASS"],
        }
    ha = os.environ.get("HA_HOST", "root@10.4.1.10")
    raw = subprocess.check_output(["ssh", ha, "cat /config/.storage/core.config_entries"])
    e = next(x for x in json.loads(raw)["data"]["entries"] if x["domain"] == "binary_moip")["data"]
    return {"host": e["host"], "port": e.get("port", 443), "u": e["username"], "p": e["password"]}


class Moip:
    def __init__(self, cf: dict, session: ClientSession):
        self.base = f"https://{cf['host']}:{cf['port']}"
        self.cf, self.s, self.tok = cf, session, None

    async def login(self):
        async with self.s.post(f"{self.base}/api/v1/base/auth/login",
                               json={"username": self.cf["u"], "password": self.cf["p"]}) as r:
            self.tok = (await r.json())["accessToken"]

    @property
    def _h(self):
        return {"Authorization": f"Bearer {self.tok}"}

    async def get(self, p):
        async with self.s.get(f"{self.base}{p}", headers=self._h) as r:
            return (r.status, await r.json()) if r.status == 200 else (r.status, None)

    async def put(self, p, body):
        async with self.s.put(f"{self.base}{p}", headers=self._h, json=body) as r:
            return r.status

    async def audio_rx_id(self, gid: int) -> int | None:
        st, grx = await self.get(f"/api/v1/moip/group_rx/{gid}")
        return (grx.get("associations", {}).get("audio_rx")) if grx else None


async def run(mode: str):
    cf = _load_creds()
    ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
    async with ClientSession(connector=TCPConnector(ssl=ctx)) as session:
        m = Moip(cf, session)
        await m.login()
        changes = 0
        for gid, spec in ZONE_MODELS.items():
            name, model = spec["name"], spec["model"]
            if spec.get("skip"):
                print(f"  [{gid}] {name:20} skipped (per config — left untouched)")
                continue
            arx = await m.audio_rx_id(gid)
            if not arx:
                print(f"  [{gid}] {name:20} OFFLINE/absent — skipped")
                continue
            st, rx = await m.get(f"/api/v1/moip/audio_rx/{arx}")
            se = (rx or {}).get("settings") or {}
            # name-drift check (post-wipe re-map guard)
            live_name = (await m.get(f"/api/v1/moip/group_rx/{gid}"))[1]
            live_name = ((live_name or {}).get("settings") or {}).get("name")
            warn = "" if (live_name and name.lower() in live_name.lower()) else f"  !! name is {live_name!r}"
            curve = _curve(model)
            supports_eq = (se.get("supported_biquad_count") or 0) >= len(curve)

            if mode == "status":
                bands = []
                for i in range(len(curve) or 0):
                    _, b = await m.get(f"/api/v1/moip/audio_rx/{arx}/main_bq/{i}")
                    if b:
                        bands.append(f"{b['gain']:+.0f}@{b['freq']}")
                print(f"  [{gid}] {name:20} loud={se.get('loudness'):8} mono={str(se.get('mono')):5} "
                      f"model={model or '?':20} EQ[{' '.join(bands)}]{warn}")
                continue

            plan = [f"loudness={GLOBAL['loudness']}", f"mono={GLOBAL['mono']}"]
            if curve and supports_eq:
                plan.append("EQ[" + " ".join(f"{b['gain']:+.0f}@{b['freq']}" for b in curve) + "]")
            elif curve and not supports_eq:
                plan.append(f"EQ-SKIP(only {se.get('supported_biquad_count')} bands)")
            print(f"  [{gid}] {name:20} {model or '?':20} -> {', '.join(plan)}{warn}")

            if mode == "apply":
                await m.put(f"/api/v1/moip/audio_rx/{arx}",
                            {"settings": {"loudness": GLOBAL["loudness"], "mono": GLOBAL["mono"]}})
                if curve and supports_eq:
                    for i, band in enumerate(curve):
                        await m.put(f"/api/v1/moip/audio_rx/{arx}/main_bq/{i}", band)
                changes += 1
        if mode == "apply":
            print(f"\nApplied to {changes} online zone(s).")


def main():
    ap = argparse.ArgumentParser(description="Apply/inspect MoIP per-zone EQ + loudness + mono.")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--status", action="store_true", help="read + print current per-zone tuning")
    g.add_argument("--apply", action="store_true", help="push the tuning (default is dry-run)")
    args = ap.parse_args()
    mode = "status" if args.status else "apply" if args.apply else "dry-run"
    print(f"MoIP tuning — mode: {mode}\n")
    asyncio.run(run(mode))
    if mode == "dry-run":
        print("\n(dry-run — re-run with --apply to push)")


if __name__ == "__main__":
    main()
