# Audio Spaces & Calibration — Build Spec v1.1

**Version 1.1 — 2026-06-15**
**Status:** Draft for Claude Code. The inspect-and-report gates in §9 must clear before implementation begins.
**Changelog:**
- v1.1 — Space **membership now derives from an HA label** (one grouping source shared with the dashboard / scene / service-targeting scheme). Calibration + runtime state stay integration-owned, keyed `(space, zone)`, and tolerate membership changes. Explicit zone overrides supported so Phase 1 isn't blocked before the label scheme exists. (§2, §4, §9, §0)
- v1.0 — initial spec: Spaces model, zone-driver abstraction, level interpolation, calibration tool, space-aware card.

> **Provisional naming.** "Space" is the working term for a *calibrated group of zones* (chosen to avoid colliding with Home Assistant's built-in "area"). If Mark picks another term (candidates: Soundscape, Listening Space), it's a cosmetic rename — keep it a single constant/string so it's one find-replace. The Space name should **match its HA label name** (see §4).

---

## 0. Orientation — read before doing anything

Two repos are in play:

- **`hass-binary-moip`** (integration, Python) — owns the Space model, zone drivers, calibration storage, interpolation, and services. **This is the bulk of the work.**
- **`hass-binary-moip-card`** (Lovelace card, currently v2.2.0) — two frontend surfaces: the **space-aware playback card** (evolve the existing card, do NOT rebuild) and the **calibration tool** (new view; may be a separate card in the same repo).

Read first, before designing anything:
- The integration's current zone / source / routing model — how zones, MoIP outputs, and the controller relate *today*.
- The v2.2 card's source picker + master/per-zone volume code. Its master-delta-preserves-trims logic is the seed the interpolation generalizes — reuse it.

**Reuse, don't rebuild.** The playback card already has source picking, now-playing, master + per-zone volume. Spaces layer on top.

**Dependency.** Space membership is sourced from an HA **label** scheme (§4), which assumes the audio zones are area-assigned and the areas labeled. That scheme should be at least *designed* before wiring the resolver; until it exists, explicit zone lists (§4) keep Phase 1 unblocked.

---

## 1. What we're building (one paragraph)

A **Space** is a named, calibrated group of audio zones that turn on together, balanced, at a chosen level. Its membership is the same grouping used by the dashboard and scene targeting (sourced from one HA label). The integration owns the per-zone calibration and the math that turns a single "level" into correct per-zone volumes, and exposes services so the card, automations, or scenes can drive them. A **calibration tool** (mobile, walk-around) lets Mark play pink noise or a music sample to each zone and save its Background / Listening / Party values. The **playback card** lets the family turn Spaces on/off at a preset level, fine-tune the master (zones rebalance by interpolation), and drop into per-zone toggles when needed. Multiple Spaces run at once at different levels — inside at Listening, outside at Party.

---

## 2. Architecture decisions (LOCKED)

1. **The Space model, calibration data, drivers, and interpolation live in the integration** — single source of truth for the audio-specific data, runtime-editable, automatable. The card is a thin view.
2. **A zone is a target with a driver.** *Routing* is always MoIP; *power/input/volume* is per-zone — either the MoIP controller or a downstream `media_player` (e.g., the theater Denon).
3. **Level → per-zone volume is per-zone, per-anchor interpolation in native units.** No cross-device unit conversion.
4. **Two frontend surfaces over one model:** calibration tool (authoring) and space-aware card (control).
5. **Space membership is sourced from an HA label**, resolved via the area/entity/label registries → audio-zone entities. One grouping concept shared with the dashboard and scene/service targeting; the Space name matches the label name. Calibration + runtime state stay integration-owned (labels can't hold them). Explicit zone overrides remain available (§4).

---

## 3. Zone driver abstraction

Each zone has:

- **`routing`** — the MoIP source/output assignment that gets a stream to this zone. Always MoIP. Unchanged from today.
- **`driver`** — how power / input / volume are realized:
  - **`moip`** — set the zone's volume on the controller (current behavior). Power = existing on/off (route / mute).
  - **`device`** — delegate to a `media_player`:
    - `on`: `turn_on` → `select_source(input)` → set volume
    - `off`: **smart** — only power down if the integration powered it on and nothing else is using the device; otherwise stop sending / restore the prior input (see §12)
    - `set_level`: `volume_set(value)`
    - config: `entity_id`, `input`/source name, optional separate `volume_entity`, min/max bounds

The Space/interpolation layer never knows which driver a zone uses — it computes a per-zone target *value* and calls `driver.set_level(value)`. Each zone carries its own native units (MoIP step vs `media_player` 0–1 vs dB).

---

## 4. Space model + storage

A **Space**:
- `id`, `name` (name matches the HA label)
- **membership** — sourced from an HA **label** (`label_id` / label name). The integration resolves it through the HA registries: label → areas (and/or directly labeled entities) → the audio-zone entities in scope. Optional explicit overrides:
  - `zones_add`: zone ids to include regardless of label
  - `zones_exclude`: zone ids to drop
  - resolved membership = labeled zones + adds − excludes
  - (a zone MAY land in more than one Space — overlap policy §12)
- `levels`: three anchors, each holding **per-zone native values**:
  - `background: { zone_id: value, ... }`
  - `listening: { zone_id: value, ... }`
  - `party: { zone_id: value, ... }`
  - anchors may be partially defined (graceful degradation, §5)
- `master_positions` (optional; global defaults): master-scale positions of the three anchors — default **20 / 50 / 85**
- runtime state: `active`, `current_master` (0–100), `current_source`, `current_level` (nearest preset or "custom")

**Calibration is stored keyed by `(space_id, zone_id)`, independent of current membership.** Consequences to handle:
- A zone newly resolved into the space with **no stored anchors** is *uncalibrated* — flag it (so the calibration tool surfaces it) and apply a safe default until it's calibrated. Never blast an uncalibrated zone to full.
- A zone dropped from the label **retains its stored anchors** (inactive), so re-adding it later restores calibration rather than losing it.

Persist in integration storage (config-entry options or a `Store` helper), runtime-editable by the calibration services, surviving restart.

---

## 5. Levels + interpolation (precise)

- Master scale: **0–100** (abstract "area level position").
- Anchor positions `P_bg < P_list < P_party` (defaults 20 / 50 / 85).
- Per zone, native anchor values `V_bg, V_list, V_party` (whichever are defined).
- For master `M`, zone target:
  - `M ≤ P_bg` → `V_bg`
  - `P_bg < M ≤ P_list` → `lerp(V_bg, V_list, (M−P_bg)/(P_list−P_bg))`
  - `P_list < M ≤ P_party` → `lerp(V_list, V_party, (M−P_list)/(P_party−P_list))`
  - `M > P_party` → `V_party`
  - clamp result to the zone's device min/max
- A **preset button** sets `M` to that anchor's position.
- **Graceful degradation:** 1 anchor → constant value at all M. 2 anchors → linear between them, clamp outside. 3 → piecewise as above. A zone missing an anchor the others have interpolates from its nearest defined anchors. An *uncalibrated* zone (no anchors) holds the safe default (§4).
- A **manual per-zone nudge** in normal use is a temporary override; resets on the next preset tap or master move. Saved anchors change *only* in the calibration tool.

---

## 6. Integration services (the API)

**Playback**
- `binary_moip.space_activate(space, level | master, source)` — route source to the space's zones, set each by interpolation, mark active
- `binary_moip.space_deactivate(space)`
- `binary_moip.space_set_master(space, position)` — re-interpolate all zones live
- `binary_moip.space_set_level(space, level)` — jump master to a preset
- `binary_moip.zone_set(space, zone, on | off | nudge)` — per-zone override within a space

**Calibration**
- `binary_moip.calibration_play(zone, type=pink|sample, sample_uri?)` — play reference to one zone (route + working level)
- `binary_moip.calibration_set_anchor(space, zone, level, value)`
- `binary_moip.calibration_clear_anchor(space, zone, level)`

Names are a proposal; adjust to integration conventions. These are what both frontend surfaces call.

---

## 7. Frontend surface A — Calibration tool

Mobile, walk-around dashboard/card:
- Pick a Space, pick the zone you're standing in.
- Play **pink noise** or a **music sample** to that zone (toggle). Band-limit the pink noise if generated; otherwise use a provided file.
- A level slider in the zone's native scale; nudge until it matches the reference (by ear / SPL meter).
- Save as **Background / Listening / Party** for this zone.
- Shows the zone's saved anchors; surfaces any *uncalibrated* zones (newly label-added) that still need anchors. Any can be re-done.
- Writes via the calibration services. No card-config editing.

---

## 8. Frontend surface B — Space-aware playback card (evolve v2.2)

- **Primary: by Space.** List Spaces; tap → on/off, three preset buttons (Background / Listening / Party), master slider (drives interpolation live), current source.
- **Expand a Space → its zones** for on/off + nudge (the existing per-zone row becomes the fine-tune layer).
- **Source** still chosen per stream as in v2.2 (Music Assistant / Spotify Connect siblings). A session = Space + Source + Level.
- **Multi-space / multi-level** concurrently. Same source → two spaces at different levels = one stream (per-zone volume differs). Different sources = the two streams.
- Reuse the v2.2 source picker + now-playing wholesale.

---

## 9. Probe / inspect gates — DO FIRST, report back before coding

1. **MoIP controller volume units** (10.4.1.50): dB or steps? Range? Sets the `moip` driver's value scale and stored anchor units.
2. **Denon `media_player` entity**: actual `volume_level` scale (0–1 vs dB attribute), `source_list` values, power behavior. Confirms the `device` driver mapping for the theater zone.
3. **Current integration zone model**: how zones / outputs / routing are represented now, so Spaces + drivers *extend* rather than fork it.
4. **Label resolution + area assignment:** confirm the audio-zone entities are assigned to HA areas, and how to resolve `label → areas → audio-zone entities` via the area / entity / label registries (or labels applied directly to the zone entities). If the label scheme isn't set up yet, build the resolver and test Phase 1 with explicit `zones_add` lists, then switch to label-sourced membership.
5. **Report the model back** (zone schema, membership-resolution approach, service signatures, storage shape) for an explicit lock before implementing.

---

## 10. Build order (phased)

- **Phase 1 — backend core:** zone-driver abstraction (`moip` + `device`), Space model + storage, membership resolution (label-sourced, with explicit-list fallback), interpolation, `space_activate/deactivate/set_master/set_level`. Test against the existing MoIP zones + the Denon theater zone. One Space, three anchors.
- **Phase 2 — calibration tool:** calibration services + the walk-around card. Calibrate real Spaces; surface uncalibrated (newly label-added) zones.
- **Phase 3 — space-aware card:** evolve v2.2 — Space list, presets, master, expand-to-zones, multi-space.

Ship each phase rough and test on the real system before polishing — the frontend deploy loop is heavy, so prove behavior early.

---

## 11. Tests

- Interpolation logic: 1 / 2 / 3-anchor cases, clamping outside range, missing-anchor fallback, uncalibrated-zone default, preset-jump positions. Mirror the card's 18-test discipline.
- Membership resolution: label → zones, with `zones_add` / `zones_exclude`; membership change keeps calibration keyed `(space, zone)` (added zone = uncalibrated; removed zone = anchors retained/inactive).
- Driver dispatch: `moip` vs `device` `set_level` routing.
- Space activate/deactivate with overlapping zones (per §12).

---

## 12. Open questions / TBD

- **Overlap policy:** zone in two active Spaces at different levels. Proposed: last-activation-wins; deactivating one returns the zone to any other active Space containing it, else off. Confirm.
- **Shared Denon off-logic:** don't power the AVR down if the theater TV is using it. Proposed: integration tracks whether *it* powered the AVR; off only powers down if it did and no other consumer remains. Confirm.
- **Uncalibrated-zone default:** what's the "safe default" level for a newly label-added zone before calibration (e.g., the Background anchor of a sibling zone, or a fixed low value)?
- **Master display:** 0–100, a rough %, or nothing numeric?
- **Space name** (cosmetic; matches label).
- **Anchor scope:** all three levels per zone up front, or Phase 1 with one level then add? Graceful degradation supports either.

---

## 13. Out of scope (this spec)

- Floor/area *card filtering* (the separate v2.3 idea — different feature).
- Video MoIP (separate workstream — see `moip_video_spec_v1_0.md`).
- Per-account Spotify browsing.
- Defining the label scheme itself (lives in the home's area/label setup, not this integration).
