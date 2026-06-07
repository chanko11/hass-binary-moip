# Audio Card v2 — Build Spec (`hass-binary-moip-card`)

**Version:** 2.1
**Date:** June 6, 2026
**Status:** Active spec → drives the next Claude Code pass
**Companion to:** `homelab_knowledge_base_v1_9.md`, `binary_moip` integration v0.3.0, `audio_card_spec_v1_0.md` (shipped v1)
**Relationship to v1:** Supersedes v1 as the primary audio card. v2 keeps v1's zone / volume / transport machinery and adds the **streaming-as-parent** model with a **provider-first content browser**. Built as the next major version of the same card (`custom:binary-moip-card`), replacing v1 on the dashboard.

## Change log
- **2.1  2026-06-06** — **Provider-first navigation.** The content picker is now **provider → item → track**, mirroring Music Assistant's own browse tree, instead of a flat `media_id` preset list. Source row shows the **provider**, not the track (fixes the v2.0 triple-title). Resolves the v2.0 open decisions (see below). Adds the MA music-provider prerequisite.
- **2.0  2026-06-05** — Streaming-as-parent redesign: inputs (streams + physical line-ins) own zones + volume; content swappable on streams, fixed on physical inputs; first content picker + MA `play_media` mapping.

---

## Resolved decisions (were open in v2.0)
1. **Content selection** — *not* a curated `media_id` list. The picker walks **Music Assistant's browse tree** (provider → its content). Curated presets are dropped.
2. **Card identity** — evolve this card into v2 and **replace** v1 on the dashboard (same repo, same `custom:binary-moip-card` type, major version bump).
3. **Spotify Connect** — **kept** as a source, but as a *cast-only* entry (no browse): it shows the cast instruction; the phone drives it.

## The model

Three levels hang under each input:

- **Input** (parent) — owns **zones + volume**. The MoIP matrix routes the input to zones; it doesn't care what's on it. Streams (Streaming 1 / 2) carry swappable content; physical inputs (Record player, Apple TV) carry fixed content.
- **Source / provider** — what's feeding the stream: "Mark's Spotify," "Geralyn's Spotify," "Pandora," "Internet Radio," "Spotify Connect." **This is what the source row shows.**
- **Item** — the thing picked *within* a provider: Spotify → a playlist, Pandora → a station, Radio → a channel. Reached by drilling into the provider.
- **Track** — the now-playing line + transport controls. **The only place the track title/artist appears.**

Key properties (unchanged from v2.0):
- **Zones belong to the input, not the content** — swapping source/provider/item never moves the zones (routing lives on the `binary_moip` source; content is a play action on the backing MA player).
- **Two streams = the cap** — at most two independent streaming sessions; both streams are first-class so the limit is visible.
- **Reach (latent)** — streams are digital (could reach other systems like Sonos later); physical inputs are analog/MoIP-only. v2 is MoIP-only; the structure reserves a clean slot for a future per-system dimension.

## Entities & the Music Assistant mapping

Per **stream** input the card uses two entities:
- **binary_moip source** (e.g. `media_player.ha_streaming_1`) — routing (`group_members` / `join` / `unjoin`) + now-playing/transport (proxied), as v1.
- **backing MA player** (e.g. `media_player.streaming_1`) — the target for browse + play.

**Provider-first picker (the new flow):**
1. Open **Change source** → the picker shows the **providers** available in Music Assistant (read from MA's browse via `browse_media` on the stream's MA player), **plus** an injected **Spotify Connect** entry.
2. Pick a *browse* provider → the card lists its content (`browse_media` again, drilling: playlists / stations / channels) until a playable item.
3. Pick an item → `music_assistant.play_media` on the stream's MA player with that item's URI (`enqueue: replace`; `radio_mode` for station-like items). It plays into the MoIP input → out to the already-routed zones (which don't move).
4. Pick **Spotify Connect** → no action; show "Cast from your Spotify app to {stream}." Now-playing populates once they cast.

> CC to confirm against the live MA browse tree how providers/items are identified (the `media_content_id` shapes), since the config `match` keys below depend on it.

## Functional requirements

1. **Input rail** — streams + physical inputs. Tile: input icon, **provider as headline** (the current source, e.g. "Mark's Spotify" / "Spotify Connect"), **input name as subtitle** ("Streaming 1"), active dot + state. Tap selects. *(The headline is the source, never the track — per the v2.0 fix.)*
2. **Selected input detail:**
   - **Source row** — the current **provider** (icon + label) + **Change source** control opening the provider-first picker. Physical inputs: fixed-source label + "live input — no transport" note.
   - **Now-playing** — artwork + **track title + artist** + transport (prev / play-pause / next). The single home for the track.
   - **Master volume** — "All zones" slider; rounded average; delta to all members; clamp/round. *[reused from v1]*
   - **Zone rows** — name, mute, slider, %, remove (unjoin). *[reused from v1]*
   - **Add zones** — grouped picker; one-tap join (takes a busy zone). *[reused from v1]*
3. **Content picker** — provider list → drill-in browse → play (above). Current provider highlighted.
4. **Live state** — read from `hass`; reflect routing, provider/now-playing, volumes immediately.
5. **Zones persist across any source/item change** — verify `group_members` is untouched by `play_media`.

## Config (Lovelace YAML)

- `inputs`: each `{ entity, name, kind: stream|physical, ma_player (stream only) }`.
- `content_nav` (optional — curates presentation of MA's browse):
  - `providers`: relabel / icon / order / filter the MA providers, e.g. `{ match: <ma provider id>, label: "Mark's Spotify", icon: mdi:spotify }`. If omitted, show MA's providers as-is.
  - `connect`: the cast-only entries to inject, e.g. `{ label: "Spotify Connect", icon: mdi:cast }`.
- `zone_groups` (optional) — label → MoIP zone entity_ids for Add-zones.
- Round every displayed %.

```yaml
type: custom:binary-moip-card
inputs:
  - { entity: media_player.ha_streaming_1, name: Streaming 1, kind: stream, ma_player: media_player.streaming_1 }
  - { entity: media_player.ha_streaming_2, name: Streaming 2, kind: stream, ma_player: media_player.streaming_2 }
  - { entity: media_player.record_player,  name: Record player, kind: physical }
  - { entity: media_player.game_room_apple_tv, name: Apple TV, kind: physical }
content_nav:
  providers:
    - { match: "<mark spotify provider id>",   label: "Mark's Spotify",   icon: mdi:spotify }
    - { match: "<geralyn spotify provider id>", label: "Geralyn's Spotify", icon: mdi:spotify }
    - { match: "<pandora provider id>",         label: "Pandora",          icon: mdi:radio }
    - { match: "<radio provider id>",           label: "Internet Radio",   icon: mdi:radio-tower }
  connect:
    - { label: "Spotify Connect", icon: mdi:cast }
zone_groups:
  Main House: [media_player.kitchen, media_player.parlor, ...]
  Outdoor:    [media_player.pool, media_player.outdoor_seating, ...]
  Casita:     [media_player.casita_kitchen, ...]
```

## Prerequisite (user-side, in Music Assistant — not a card task)

Browsing "Mark's / Geralyn's Spotify → playlists," "Pandora → stations," etc. requires **each account/provider added as an MA music provider** (per-account logins). This is the in-HA provider setup that was parked under the Spotify-app-only choice, and it's **separate** from the Spotify Connect plugin. Spotify therefore appears twice at the provider level: "Mark's Spotify" (browse) and "Spotify Connect" (cast) — the pull vs push split.

## Behaviors / edge cases

- **Two-stream cap / replace** — content is one-per-stream; picking a new item on a stream replaces what's there. Selecting the stream first makes the replacement explicit.
- **Idle stream** — empty source row ("Nothing playing — pick a source"); picker available; pre-routed zones play once content starts.
- **Same service twice** — Mark's Spotify on Streaming 1 and Geralyn's on Streaming 2 are independent.
- **Reach (v2)** — all zones MoIP; no Sonos group; keep grouping open for a future per-system dimension.

## Reused from v1 (do not rebuild)
Volume math, zone rows (mute/slider/%/remove via `volume_set`/`volume_mute`/`unjoin`), one-tap Add-zones join, now-playing/transport binding, `ha-card` theming, LitElement + TS + esbuild single-bundle tooling, logic/render split + tests.

## Constraints / Tech
- Generic/publishable: all entities + provider curation from config; no household specifics in code.
- Theme via HA card vars; light + dark.
- Browse via `browse_media`; play via `music_assistant.play_media`; routing via `join`/`unjoin`. Don't invent calls.
- Single JS bundle, HACS frontend; deploy via SSH to `/config/www` or HACS.
- Coverage for: the browse→play action builder (item URI → `play_media` call) + reused volume/join math.

## Acceptance
- Rail shows streams (provider as headline) + physical inputs; tap selects.
- **Change source** lists MA providers + the Connect entry; drilling into a provider lists its playlists/stations; picking one fires `music_assistant.play_media` on that stream's MA player and starts playing.
- Source row shows the **provider**; the track appears **only** in now-playing; zones unchanged across the swap.
- Spotify Connect entry shows the cast instruction (no browse).
- Physical input: fixed source + no transport; no Sonos; MoIP zones.
- Master + per-zone volume and add/remove behave as v1.
- Single bundle; tests for the browse→play builder + reused math.

## Out of scope / future
- Sonos / multi-system (the conductor/sync layer lives in MA, not this card).
- Room-first card (separate).
- Content-first shortcut entry + logical "players" (play to a zone-group that auto-grabs a free stream).
- Video "activities."
