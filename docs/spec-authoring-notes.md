# Spec & prompt authoring notes

Lessons from building the audio card (v1 → v2.2) for Claude Code. The goal: skip
the detours we hit, and keep the practices that worked. Applies to any spec that
touches an external system (the MoIP controller, Music Assistant, Spotify, HA's
own browse/services).

## The one rule: probe the platform before you spec against it

Most wasted effort came from specifying a design around an **unverified
assumption** about what a platform exposes.

- **v2.1** assumed Music Assistant's per-provider "Browse" folders were reachable
  via HA's `browse_media`. They aren't — HA's MA integration browse is
  **library-only**; the provider tree lives only in MA's own server API. The spec
  even flagged "confirm the `media_content_id` shapes," yet still defined a
  `content_nav.providers[{ match: … }]` schema around data that didn't exist.
  Result: a whole version (v2.1) was thrown away.
- **v2.2** did it right: it opened with an **"Architecture finding (why it's
  shaped this way)"** section — the result of actually probing `browse_media` and
  the MA services — and shaped the design to reality.

**Before writing a build spec, run the 10-minute probe** and put the findings in
the spec:
- What does `browse_media` / the relevant service actually return? (ids, shapes,
  whether the thing you want is even a level in the tree)
- What attributes does the live entity expose? (dump them — that's how we found
  `source: Spotify Connect` / `app_id: spotify_connect--…` for Connect detection)
- Does the data you want to key config on actually exist?

If you can't probe it yet, the spec should say **"feasibility unconfirmed — gate
the build on an inspection step"**, not assume.

## Separate "desired UX" from "confirmed-feasible"

Several asks were great UX but unbacked by available data, and that only surfaced
at runtime:
- provider-first navigation (not in `browse_media`),
- the casting client's name, e.g. "Geralyn's iPhone" (not exposed — only an
  `app_id` instance hash),
- "source row shows the source, never the track" (sounded trivial; the hard part
  was *detecting the current source* — MA playback vs Spotify Connect look nearly
  identical and needed a live cast to find the distinguishing attribute).

Mark such items clearly (e.g. "TBD — confirm from live state") instead of writing
them as solved.

## Don't assert platform/service behavior you haven't verified

The spec said "`radio_mode` for station-like items." Backwards: MA **rejects**
`radio_mode` for an actual Radio item (`Dynamic tracks not supported for Radio
MediaItem`). A wrong assertion about a service parameter becomes a runtime error,
not a review comment. Hedge or verify these.

## Spec structure that worked (copy v2.2)

- **Change log with rationale** (what changed and *why*).
- **Architecture finding** section when an external system constrains the design.
- **"Reused from v1 — do not rebuild"** — crisp scope control every round.
- **Explicit Acceptance** criteria.
- **Out of scope / future** list.

## Prompt patterns that worked — keep doing these

- **"Inspect, then report before coding."** The single most valuable instruction
  — it created the gate that caught the `browse_media` gap before any picker code
  was written. Add it whenever a premise might be wrong.
- **Locking decisions explicitly** ("Decisions are locked: …") removed ambiguity.
- **Decisive pivots under a wrong premise.** When provider-first proved
  impossible, the next prompt didn't say "fix it" — it gave the exact alternative
  ("build the HA-native mechanics, presented provider-first, library nested under
  a Music Assistant sibling"). Concrete recovery, no round-trip.
- **HOLD-the-release + verify-on-a-real-dashboard-first.** Nothing shipped unseen.

## Deployment / iteration realities

- **Frontend cache bites.** Overwriting the bundle in place does **not** refresh
  the HA frontend/service-worker cache — the resource **URL** must change (bump a
  query param) or you'll keep seeing the old card. HACS version bumps do this
  automatically; manual SSH deploys don't.
- **Ship rough to a real dashboard early.** The best improvements (sub-card that
  swaps to the picker, the picked-item breadcrumb, Connect labeling, per-stream
  turn-off) came from *using it*, not from the spec. Treat the spec as a frame;
  iterate on hardware.
- **Batch UI changes** per round — each tweak is edit → build → deploy →
  cache-bust → reload.

## TL;DR

v2.1 over-specified on an unverified assumption; v2.2 nailed the loop:
**investigate → write down the finding → spec to reality.** Open every
external-system spec with a capability probe, split "desired" from
"confirmed-feasible," and gate the build on an inspection step when unsure.
