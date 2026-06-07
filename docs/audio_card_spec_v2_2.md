# Audio Card v2 — Build Spec (`hass-binary-moip-card`)

**Version:** 2.2
**Date:** June 6, 2026
**Status:** Active — this is Claude Code's build target (structure confirmed via mockup)
**Companion to:** `homelab_knowledge_base_v1_9.md`, `binary_moip` integration v0.3.0, `audio_card_spec_v1_0.md` (shipped v1)
**Relationship to v1:** Supersedes v1 as the primary audio card. Same `custom:binary-moip-card` type; replaces v1 on the dashboard.

## Change log
- **2.2  2026-06-06** — Picker mechanism settled after a source-level investigation. Provider-first navigation is **not exposed to Home Assistant** (`browse_media` gives only MA's merged library; MA's per-provider Browse lives only in MA's own server API, which the card can't safely use — LAN-only, insecure-ws/mixed-content, version-fragile). **Decision:** present the merged library as a **"Music Assistant"** source with **"Spotify Connect"** as a sibling source; runtime stays HA-native (`browse_media` + `play_media`). Per-account/enumerated sources deferred (see Future). Structure confirmed via mockup.
- **2.1  2026-06-06** — Provider-first navigation (assumed reachable via MA's Browse tree — later found not exposed to HA).
- **2.0  2026-06-05** — Streaming-as-parent redesign.

## Architecture finding (why it's shaped this way)
- HA's MA integration `browse_media` is **library-only** (7 categories → items addressed by `uri`, e.g. `library://playlist/17`); there's no provider code path. Available services: `search`, `get_library`, `get_queue`, `play_media` — no `browse`/`get_providers`. `get_library` items carry **no provider field**, so per-provider grouping can't even be synthesized.
- MA's per-provider Browse (the "Mark's Spotify / Pandora / Apple Music" folders) exists **only in MA's own server API** (`ws://10.4.1.x:8095`). Driving the card off that means LAN-only access, mixed-content blocking from the https dashboard, and breakage across MA versions — unacceptable for a family card used remotely.
- So the card stays **HA-native** (`browse_media` + `play_media`) and presents source-first at the level HA supports: **"Music Assistant"** (the library) as one source, with siblings beside it — Spotify Connect now, enumerated per-account sources later.

## The model (unchanged)
- **Input** (parent) owns **zones + volume**. Streams carry swappable sources; physical inputs are fixed.
- On a **stream**, a **source** is what's feeding it. Sources are **siblings**: "Music Assistant" (the MA library) and "Spotify Connect" (cast). Physical inputs (Record player, Apple TV) are their own inputs with a fixed source.
- **Item** — drill into a source's content. **Track** — now-playing only.
- **Zones belong to the input** — swapping source or item never moves them.
- Two streams = the cap. Reach: MoIP-only (Sonos latent).

## Structure / functional requirements
- **Rail (inputs):** Streaming 1, Streaming 2, Record player, Apple TV. Stream tile: headline = current source (Music Assistant / Spotify Connect / Idle), subtitle = input name, active dot. Physical tile: device name + "line-in".
- **Selected stream:**
  - **Source row** — current source label + subtitle (the selected item name for MA / "cast from your phone" for Connect / "tap Change source" if idle) + **Change source** button.
  - **Change source → source picker (siblings):**
    - **Music Assistant** → drill via `browse_media` into the library categories (Playlists, Radio; optionally Artists/Albums/Tracks) → pick item → `music_assistant.play_media(ma_player, item.uri)`.
    - **Spotify Connect** → no items; shows "cast from your Spotify app to {stream}."
  - **Now-playing** — track + artist + transport. The only place the track appears.
  - **Master volume + zone rows + add zones** *(reused from v1)*.
- **Physical input:** fixed-source label + "no transport" note; zones; no picker.
- **Live from `hass`; runtime is `browse_media` + `play_media` only** — HA-native, works remotely, stable across MA versions.

## Entities & mapping
Per stream: the **binary_moip source** (routing/transport) + the **backing `ma_player`** (browse + play target). Play via `music_assistant.play_media(ma_player, uri)`.

## Config
- `inputs[ { entity, name, kind: stream|physical, ma_player (stream only) } ]`.
- `sources` (the stream picker) — for now fixed: **Music Assistant** (`browse_media` library) + **Spotify Connect** (cast). Structure the list so more sources can be added later as siblings with no refactor.
- `zone_groups` — label → MoIP zone entity_ids for Add-zones.
- Round every displayed %.

## Reused from v1 (do not rebuild)
Volume math, zone rows (mute/slider/%/remove via `volume_set`/`volume_mute`/`unjoin`), one-tap Add-zones join, now-playing/transport binding, `ha-card` theming, LitElement + TS + esbuild single-bundle tooling, logic/render split + tests.

## Acceptance
- Rail = inputs; tap selects. On a stream, **Change source** shows **Music Assistant** + **Spotify Connect** as siblings.
- Music Assistant drills into Playlists/Radio; picking an item fires `play_media` on that stream's `ma_player`; the track shows **only** in now-playing; zones unchanged across the swap.
- Spotify Connect shows the cast instruction (no browse).
- Physical inputs: fixed source + no transport; MoIP zones; no Sonos.
- Single bundle; HA-native runtime (`browse_media` + `play_media`); tests for the browse→play builder + reused math.
- **HOLD the release** until visually verified on a real dashboard via SSH; then replace v1.

## Future / next version
- **Search within the Music Assistant source** — use the `search` service (HA-native) for a search box → results → play. Clean and remote-safe; the natural next control upgrade.
- **Fuller library browse** — more categories, pagination.
- **Per-account / enumerated sources** (Mark's Spotify, Geralyn's Spotify, Pandora as distinct siblings) via a **server-side** enumeration of MA's API into a static source manifest the card reads (the "C" approach) — provider-first without runtime MA-API access. Slots in beside "Music Assistant."
- **Optional deep-link to the in-HA Music Assistant panel** as an advanced escape hatch (cleaner than linking the raw MA server, which is LAN-only). It's a context switch, so keep it secondary.
- Sonos / multi-system, room-first card, logical players, video "activities" (unchanged).
