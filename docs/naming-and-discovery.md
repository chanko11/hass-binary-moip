# Naming & Discovery Model

How the integration discovers topology from the MoIP REST API (v1.3.0) and
where the names come from. Derived from analysis of `docs/openapi.json` and a
real-system dump (`docs/moip-full.json`).

## TL;DR

- **Zones come from `group_rx`** (logical), **never from `audio_rx`** (hardware).
- **Sources come from `group_tx`** (logical).
- Each `group_rx` → one Home Assistant `media_player` entity.
- The integration discovers *everything* from the API; Home Assistant (via the
  config/options flow) is the friendly-name and enable/disable layer.

## Name layers

The controller stores several independent, user-editable names. They are edited
in OvrC or the MoIP controller's own web UI.

| Layer | API field | Editable | Example | Role |
|-------|-----------|:--------:|---------|------|
| Unit | `unit.settings.name` | ✅ | `Main 6 Zone Amp` | parent device name |
| **Zone** | `group_rx.settings.name` | ✅ | `Master Bedroom` | **HA media_player name** |
| **Source** | `group_tx.settings.name` | ✅ | `TX-D46A9128261A-1` (default) | source-list label |
| Audio out | `audio_rx.label` | ❌ | `Audio Output 5` | fixed hardware label — avoid |
| Audio in | `audio_tx.label` | ❌ | `Digital Input` | fixed hardware label |

Many integrations mistakenly name zones from `audio_rx.label` ("Audio Output
1"…). We must read `group_rx.settings.name`.

## Discovery graph

```
unit ──┬─ associations.group.rx[] → group_rx ──┬─ settings.name          ZONE NAME
       │                                        ├─ associations.audio_rx  → audio_rx  (volume / mute / state)
       │                                        └─ associations.paired_tx → group_tx  CURRENT SOURCE
       └─ associations.group.tx[] → group_tx ──┬─ settings.name          SOURCE NAME
                                                └─ associations.audio_tx  → audio_tx
```

### Endpoints used
- `GET /api/v1/moip/unit` → unit IDs; `GET /api/v1/moip/unit/{id}` → name, model, mac, state
- `GET /api/v1/moip/group_rx` → zone IDs; `GET /api/v1/moip/group_rx/{id}` → zone detail
- `GET /api/v1/moip/group_tx` → source IDs; `GET /api/v1/moip/group_tx/{id}` → source detail
- `GET /api/v1/moip/audio_rx/{id}` → volume / mute / format for a zone

### Commands
- **Select source:** `PUT /api/v1/moip/group_rx/{id}` with `associations.paired_tx = <group_tx id>` (null = unpaired/off)
- **Volume:** `PUT /api/v1/moip/audio_rx/{id}` with `settings.volume`
- **Mute:** `PUT /api/v1/moip/audio_rx/{id}` with `settings.mute` (see below)

## State / volume / mute semantics

- **State** enum: `unconnected`, `stopped`, `detecting`, `streaming`,
  `unsupported`, `upgrading`, `unknown`. Map `streaming`→playing,
  `stopped`→idle/off, `unconnected`→unavailable.
- **Volume** is a float within `audio_rx.settings.supported_volume.range`
  `[min, max]` (inclusive), capped by `maxvolume`. HA wants `0.0–1.0`, so scale.
- **Mute** is an `AudioMuteList` — a *list of output ports* to mute, **not a
  bool**. Empty list = unmuted; to mute, send the list of supported output
  ports (`settings.supported_output`).

## Real-system notes (this controller)

11 units, 21 `group_rx`, 21 `group_tx`. Things a naive client gets wrong:

1. **21 zones, not 20.** Zones `RX-D46A9127F24A` / `RX-D46A9127F232` are
   standalone A/V receivers whose `group_rx.settings.name` was never set, so
   they still show default hardware-derived names.
2. **A zone is literally named `Skip`** — a deliberate "don't use" marker.
3. **All sources still have default names** (`TX-…`). Don't rely on
   `group_tx.settings.name` being meaningful; synthesize a label from
   `parent unit + audio_tx.label + input type`.
4. **The RYFF streamer exposes 4 sources** all named `TX-000FFFA11BEB`
   ("Audio Input"). Identical names → disambiguate by `group_tx` id/index.

## Eventing (websocket)

Real-time updates come from the change-event websocket; polling (every
`FALLBACK_SCAN_INTERVAL`, 5 min) is only a fallback for a dropped socket.

- **Endpoint:** `wss://{host}:{port}/api/v1/moip/change`
- **Auth:** the controller can't read an `Authorization` header here, so the JWT
  is passed via the WS subprotocol as `Bearer.{token}`.
- **Messages:** `{"changes": [{"url": "/api/v1/moip/audio_rx/1023", "kind": "added|removed|modified|ping"}]}`.
- The coordinator requests a (debounced, ~2s) refresh on any non-`ping`
  `/moip/` change, coalescing bursts (e.g. a volume ramp) into ≤1 full
  rediscovery. Reconnects with exponential backoff (2s→60s).
- A raw-socket fallback exists at `/api/v1/moip/raw_change` (line-based
  `MOD /api/v1/moip/unit/1022`); not used by this integration.

## Config / options flow behavior

- Discover and list **all** zones and sources in the integration's config UI.
- Per zone and per source, the user can **enable/disable** and set a **friendly
  label override** (stored in config entry options).
- Only **enabled** items appear in normal HA pickers; everything stays visible
  and editable in the integration configuration.

## Gotcha: dump encoding

`docs/moip-full.json` (PowerShell dump) is **UTF-8 with BOM** — load with
`encoding="utf-8-sig"`. It is a *flattened* view (PascalCase keys like
`ZoneName`), not the raw API response. The live REST API returns the
snake_case schema described above.
