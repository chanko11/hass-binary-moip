# Home Label Scheme — Area → Space Labels v1.1

**Version 1.1 — 2026-06-15**
**Purpose:** One canonical grouping ("spaces") shared by the audio **Listening Spaces** (membership resolution) and the **kitchen-kiosk dashboard** (tabs + auto-populating cards). Seven labels, applied to **areas**.
**Changelog:**
- v1.1 — Locked to actual HA assignments (from the label readout). Confirmed labels are applied at the **area level** (no entity/device labels). Flagged the Theater gap and minor placements.
- v1.0 — initial draft (inferred from audio zone groupings).

---

## The map (as applied in HA)

| Label | Areas |
|---|---|
| `Common` | Kitchen · Parlor · Dining Room · Music Room · Half Bath |
| `Master Suite` | Master Bedroom · Master Bathroom · Office |
| `Entertainment` | Game Room · Game Room Bathroom · Bar |
| `Pool` | Pool · Outdoor Kitchen · Outdoor Seating · Game Room Porch |
| `Gym` | Gym · Pool House Bathroom |
| `Casita Common` | Casita Kitchen · Sewing Room · Casita Living Room |
| `Shop` | Shop · Casita Garage |

Labels are on **areas only** — the correct altitude. Audio membership resolves `label → areas → audio zones in those areas`, so a room with no speaker (the bathrooms, etc.) adds nothing to the Listening Space while still appearing as a room on the dashboard tab. One scheme, both jobs.

---

## Open / confirm

- **Theater — not in any label.** The theater/Denon zone is part of the audio system; as applied it won't belong to any Listening Space. Decide: leave it standalone (controlled on its own), or add the **Theater** area to `Entertainment` so it joins whole-home / sports-party audio.
- **Game Room Porch → `Pool`** (outdoor) rather than `Entertainment` — sensible (it's outside); confirm intended.
- **Bathrooms / porches** (Game Room Bathroom, Half Bath, Pool House Bathroom) are included for dashboard completeness; no audio impact unless they have speakers.

---

## Dependency (next)

For **label-sourced audio membership** to reach the zones, each MoIP zone `media_player` must be **assigned to its area** (so `label → area → zone` resolves). Verify with:

```jinja
{% for s in states.media_player if s.attributes.moip_role == 'zone' %}
{{ s.name }} → {{ area_name(s.entity_id) or '— UNASSIGNED —' }}
{%- endfor %}
```

Any `UNASSIGNED` zone won't be picked up by its area's label until assigned.

---

## What hangs off this

- **Audio Listening Spaces:** membership becomes `label → areas → audio zones` (the deferred label-sourced membership). Relabel a room or add a labeled device → re-resolves on reload.
- **Dashboard:** tabs and cards filter by these labels and auto-populate — add a device to a labeled area and it appears without editing card config.
- **Naming:** the audio Listening Space names and the dashboard tab names reuse these exact seven labels, so one word means the same rooms everywhere.
