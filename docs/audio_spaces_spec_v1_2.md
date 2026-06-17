# Audio Listening Spaces & Calibration — Build Spec v1.2

**Version 1.2 — 2026-06-15**
**Status:** Reconciled to as-built (Phase-1 backend on branch `feature/audio-spaces-phase1`, unmerged). Sections marked *(built)* / *(remaining)* reflect current code.
**Changelog:**
- v1.2 — **Reconciled to as-built.** Locked term "Listening Space." Generalized the single MoIP routing assumption into **two independent per-zone axes (routing + control)**. MoIP volume confirmed **linear native float** (dB reserved for balance), not steps/dB. **Overlap locked** = disallowed, steal-no-return. **Storage split**: structure in config-entry options, calibration anchors in a dedicated Store. Membership as-built is **explicit (options flow)**; label-sourcing documented as the deferred enhancement. Added platform/entity facts (§9) and build status (§10).
- v1.1 — Space membership intended to derive from an HA label (one grouping source); calibration/state integration-owned; explicit overrides as fallback.
- v1.0 — initial spec.

> **Term (locked).** "Listening Space" is the display term; code identifiers use `space`/`spaces`.

---

## 0. Orientation

Two repos:
- **`hass-binary-moip`** (integration, Python) — owns the model, drivers, calibration, interpolation, services. Phase-1 backend lives on branch `feature/audio-spaces-phase1`. **Bulk of the work.**
- **`hass-binary-moip-card`** (Lovelace card, v2.2.0) — two frontend surfaces (calibration tool + space-aware playback card). **Not started.**

**Reuse, don't rebuild.** The playback card already has source picking, now-playing, master + per-zone volume. Spaces layer on top.

**Deferred dependency.** Label-sourced membership (§4) assumes the home's area→label scheme exists. It doesn't yet, so membership is authored explicitly for now; the resolver lands when the label scheme is designed.

---

## 1. What we're building

A **Listening Space** = a named, calibrated group of zones that turn on together, balanced, at a chosen level. The integration owns the model, per-zone calibration, and the math that turns one **master position (0–100)** into correct per-zone native volumes, and exposes services so a card/automation can drive it. A **calibration tool** (mobile, walk-around) plays a reference to each zone so its Background/Listening/Party values can be saved. The **playback card** turns Spaces on/off at a preset, fine-tunes the master (zones rebalance by interpolation), and drops into per-zone toggles. Multiple Spaces run concurrently at different levels.

---

## 2. Architecture decisions (LOCKED)

1. **Integration owns the model, calibration, and interpolation** — single source of truth for audio-specific data, runtime-editable, automatable. Card is a thin view.
2. **Two independent per-zone axes — routing and control** (§3). Not every speaker is MoIP-connected, so each axis is `moip` or `device` independently; both default `moip`.
3. **Level → per-zone volume = per-zone, per-anchor interpolation in native units** (§5). MoIP native units are a **linear float range**, not dB. No cross-device unit conversion.
4. **Two frontend surfaces over one model:** calibration tool (authoring) and space-aware card (control).
5. **Membership:** as-built is an **explicit zone list** authored in the options flow. Planned enhancement: source membership from an HA **label** (one grouping concept shared with dashboard/scene targeting) with explicit add/exclude overrides — deferred until the label scheme exists. Calibration + runtime state always stay integration-owned (labels can't hold them).
6. **Overlap disallowed:** a zone is driven by **≤1 active Space**. Activating a Space **steals** any overlapping zones from the previously-active Space (no auto-return).
7. **Storage split:** structural config (zone routing/driver, Space name/zones/positions) → config-entry **options** (options flow). Calibration **anchor values** → a dedicated **Store** (a calibration save must not reload the integration).

---

## 3. Two-axis zone model

Each zone carries two independent axes:

| Axis | Values | Default |
|---|---|---|
| **Routing** — how audio reaches the zone | `moip` (controller routes via `paired_tx`) · `device` (the zone's device sources its own content) | `moip` |
| **Control / driver** — power/input/volume | `moip` (controller volume, native float units) · `device` (any `media_player`: `turn_on` → `select_source` → `volume_set`, device-native volume, smart power-off) | `moip` |

- The interpolation engine is **driver-agnostic**: it computes a per-zone native value and calls `driver.set_level(value)`. Each zone carries its own native units/bounds.
- `DeviceDriver` smart power-off: only powers down a device **it** turned on (so a receiver already on for, say, the theater TV is left alone).
- A future non-`media_player` path is just a new `ZoneDriver` subclass.
- **Worked example — theater/Denon:** routing = `moip` (the MoIP RX `media_player.rx_d46a9127f232` "Denon AVR Input" physically feeds the AVR input) + control = `device` (`media_player.home_theater`, `device_class: receiver`, volume 0–1, `source_list` = AVR inputs).

---

## 4. Space model + storage

A **Space**:
- `id`, `name` (display "Listening Space …")
- **membership** — *as-built:* explicit `zones[]` (group ids), authored via the options flow (multi-select, with a soft notice of unassigned zones). *Planned:* `label` source + `zones_add` / `zones_exclude` overrides (deferred).
- `levels`: `{ level: { zone_id: native_value } }` for `background` / `listening` / `party`; anchors may be partial (graceful degradation, §5)
- `master_positions`: per-Space, default **20 / 50 / 85**

**Storage split (§2.7):** structure in config-entry options; anchors in a dedicated Store.

**Calibration is keyed `(space_id, zone_id)`, independent of current membership:**
- A zone newly in a Space with **no anchors** is *uncalibrated* — flag it (calibration tool surfaces it) and hold a safe default; never blast it to full.
- A zone removed from a Space **retains** its anchors (inactive), so re-adding restores them.

---

## 5. Levels + interpolation (precise)

- Master scale **0–100**. Anchor positions `P_bg < P_list < P_party` (default 20/50/85).
- Per zone, native anchor values `V_bg, V_list, V_party` (whichever defined).
- For master `M`: clamp below `P_bg`→`V_bg`; piecewise-linear `lerp` between bracketing anchors; clamp above `P_party`→`V_party`; clamp result to the zone's native min/max.
- A **preset button** sets `M` to that anchor's position.
- **Graceful degradation:** 1 anchor → constant; 2 → linear+clamp; 3 → piecewise. A zone missing an anchor others have interpolates from its nearest defined ones. Uncalibrated zone → safe default.
- A **manual per-zone nudge** in normal use is temporary; resets on the next preset tap or master move. Saved anchors change *only* via calibration.
- *(built — `interpolation.py`, 100% cov.)*

---

## 6. Integration services (the API)

**Playback** *(built)*
- `space_activate(space, level | master, source)` · `space_deactivate(space)` · `space_set_master(space, position)` · `space_set_level(space, level)`

**Per-zone override** *(remaining)*
- `zone_set(space, zone, on | off | nudge)`

**Calibration** *(set/clear built; play remaining)*
- `calibration_set_anchor(space, zone, level, value | snapshot-live-native)` · `calibration_clear_anchor(space, zone, level)`
- `calibration_play(space|zone, type=pink|sample, ref?)` — *remaining;* route + audition a reference across the zone(s) so the adjust→snapshot loop works.

**Volume** *(built)* — `async_set_volume_native` (native-units PUT, `api.py`).

---

## 7. Frontend surface A — Calibration tool *(remaining)*

Mobile, walk-around card:
- Pick a Space → pick a Level → set the reference zone's volume → walk to each zone, adjust live, **Save** (snapshots the anchor) → repeat per level; re-editable.
- Reference: a **music sample / the Space's current source** path first (so calibration isn't blocked on the pink-noise file), with a configured **pink-noise** Local-Media item (+ chosen MA player) as the preferred mode once uploaded.
- Surfaces uncalibrated (newly added) zones. Writes via the calibration services; no card-config editing.

---

## 8. Frontend surface B — Space-aware playback card (evolve v2.2) *(remaining)*

- **By Space:** list Spaces → on/off, three preset buttons, master slider (live interpolation), current source.
- **Expand a Space → its zones** for on/off + nudge (existing per-zone row = fine-tune layer; calls `zone_set`).
- **Source** per stream as in v2.2 (MA / Spotify Connect siblings). Session = Space + Source + Level.
- **Multi-space / multi-level** concurrent. Same source → two spaces at different levels = one stream; different sources = the two streams.

---

## 9. Platform facts (resolved) + remaining unknowns

**Resolved (probe):**
- **MoIP volume** = linear float per zone from `audio_rx.settings.supported_volume.range`, capped by `maxvolume` — not dB (dB reserved for balance). Integration captures `volume_range`/`max_volume` per zone; native-units setter added.
- **Existing model:** zones = `group_rx` media_players; sources = `group_tx`; routing via `paired_tx` (HA join/unjoin). Each entity exposes `moip_role: zone|source` (shipped in integration **v0.3.1**, on prod).
- **Theater/Denon** two-stage (see §3 worked example). `home_theater_2` = theater's MA player/source; `home_theater_heos` = HEOS surface.

**Remaining unknowns (Mark owes):**
- Which AVR input the MoIP "Denon AVR Input" RX feeds (CD / TV Audio / AUX / Phono…) — needed only to author the device-control theater zone.
- The pink-noise reference item (uploading to Local Media).

---

## 10. Build status & order

- **Phase 1 — backend core *(done, branch only, 179 tests / ~95% cov):*** `interpolation.py`, `drivers.py` (ZoneDriver ABC + Moip/Device + build_driver), `spaces.py` (Space + SpaceManager, overlap-steal, snapshot calibration), `services.py`/`services.yaml` (4 playback + 2 calibration), `api.py` native setter, `const.py`; options-flow Space create/rename/delete + zone assignment.
- **Remaining backend:** `calibration_play`; `zone_set`; per-zone **device/routing config options step** (entity picker → input dropdown from `source_list` → bounds — wires the Denon zone; engine already reads it from options).
- **Frontend (card repo, not started):** calibration tool; Phase-3 space-aware playback card.
- **Validation:** no live test yet; the device/Denon path powers on the AVR — run with Mark present. Branch unmerged/unreleased (Mark does releases).

---

## 11. Tests

- Interpolation: 1/2/3-anchor, clamp-outside, missing-anchor fallback, uncalibrated default, preset positions.
- Drivers: `moip` vs `device` dispatch; device smart power-off only for devices it powered on.
- Membership/overlap: activate steals overlapping zones; calibration stays keyed `(space, zone)` across membership change.
- *(Current: full suite 179 passing, ~95% cov; `interpolation.py` 100%.)*

---

## 12. Open questions / TBD

- **Pink-noise reference source** — leaning a Local-Media file selected via a Calibration options step; Mark uploading.
- **Denon AVR input** the MoIP RX feeds (for the device-control zone).
- **Master display format** — 0–100 vs % vs none (frontend).
- **Uncalibrated-zone safe default** — fixed low value vs a sibling's Background anchor.
- **Anchor scope** — all three levels up front vs one then add (graceful either way).

*(Resolved since v1.1: overlap policy → §2.6 locked; shared-receiver off-logic → §3 "only powers down what it turned on.")*

---

## 13. Out of scope (this spec)

- Defining the area/label scheme itself (home setup, not this integration).
- Floor/area *card filtering* (separate v2.3 idea).
- Video MoIP (separate workstream — `moip_video_spec_v1_0.md`).
- Per-account Spotify browsing.
