# MoIP Video Distribution — Spec & Verification v1.0

**Version 1.0 — 2026-06-14**
**Status:** Verification & scoping. Card/scene design finalizes after the §3 gates clear.
**Changelog:** v1.0 — initial: confirmed topology, target workflows, verify list.

---

## 1. Confirmed topology

- **Apple TV (game room)** → **Denon** HDMI in
- **Denon out 1** → **98" TV** (direct, high-res — keep MoIP out of this path for theater)
- **Denon out 2** → **MoIP video TX** (game-room closet)
- **TX** → MoIP network → **video RX** → **two outdoor TVs**
- **TX de-embeds audio**, which is selectable as a source on the audio-only zones (already worked under Control4).

So a TX is **two things at once**: a video source (for RXs/TVs) and a de-embedded audio source (for the audio matrix). A video RX is a video zone. Video routing is source→zone, **no calibration** (unlike audio Spaces).

**98" sync option (party mode).** To watch the same game on the 98" *in sync* with the outdoor TVs, the 98" needs a feed carrying the **same MoIP delay** as the outdoor RXs. The 98" then becomes a **switchable input** on the Denon: direct Apple TV (theater, high-res) vs a delay-matched MoIP feed (party, synced). Mode = which Denon input is selected.

> The delay-matched feed should come from an **RX**, not the TX monitor-out — see §3b. Most TX monitor/loop-outs are near-instant pass-throughs of the input, not network-decoded copies, so they would *not* match the outdoor delay.

---

## 2. Target workflows (pending §3 verification)

- **Sports Party:** route the TX to all RXs (outdoor + a 98" RX if used) → switch the Denon to its synced/party input → select the TX's de-embedded audio as the source on the chosen audio Spaces at Party level. One source, every screen, sound everywhere.
- **Theater:** switch the Denon back to the direct Apple TV input; game-room audio off the Denon (keeps the 98" lip-synced).
- **Video card:** a simple source→zone router (pick source → pick screens). Likely dominated by the one-tap party flow given the use case.
- **Audio tie-in:** the TX de-embed is just another **source** in the audio Spaces system — a sibling to Music Assistant / Spotify Connect. No new audio plumbing if §3a #3 confirms it enumerates like the line-ins.

Both "Sports Party" and "Theater" reduce to a route + a Denon `select_source` + (for party) an audio source/level set — which is exactly the **device-driver** action already planned for the theater zone in the audio Spaces spec. The integration doesn't care whether the synced feed came from a monitor-out or an RX; it just flips the input.

---

## 3. Verify before designing cards

### 3a. Claude Code probes (controller API) — droppable into a CC session

1. In `hass-binary-moip`, find the existing controller API client and the **audio** routing calls to 10.4.1.50 — endpoint shape, how sources and zones are addressed.
2. Confirm **video** TX/RX routing rides the same controller/API: same REST shape, different object type? Get the actual endpoints and IDs for the current TX and the outdoor RX.
3. Confirm the TX's **de-embedded audio** enumerates as a selectable audio source the same way the line-ins do (so it slots into the audio card's source list with no new plumbing).
4. Report endpoints / object model back before any video code is written.

### 3b. Mark verifies (physical / gear)

1. **TX monitor-out latency:** is it a network-decoded copy (delay-matched to the RXs) or a near-instant loop-through of the input? Determines whether the 98" party feed comes from the monitor-out or a dedicated **RX**. (Default assumption: loop-through → use an RX.)
2. **Denon input map:** which physical input is the direct Apple TV vs the synced/party return feed — the `select_source` values the Theater/Party modes will flip between.
3. **Apple TV count:** one Apple TV + an HDMI splitter (one leg direct to Denon, one leg to TX) covers "same content everywhere." A second Apple TV is only needed for **different** content inside vs outside at the same time. Decide which.

---

## 4. Design decisions to settle after verification

- 98" party feed: RX vs (verified) monitor-out.
- One Apple TV + splitter vs two Apple TVs.
- Video card scope — mostly the party flow, or full source/zone routing UI?
- Sports Party scene sequence (video route + Denon input + audio source + level).
- Game-room lip-sync handling: 98" audio off the Denon (instant, matches 98" picture) while the rest of the property runs the de-embedded matrix audio.

---

## 5. Out of scope (this doc)

- Audio Spaces / calibration (see `audio_spaces_spec_v1_0.md`).
- Per-account Spotify browsing.
