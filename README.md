# hass-binary-moip

Home Assistant custom integration for the Snap One Binary MoIP whole-home audio system.

Uses the Binary MoIP REST API v1.3.0 ([API docs](https://help.snapone.com/moip-ig/Content/Binary%20MoIP%20Topics/API%20v1.3.0.html)).

## Status

**Early development.** Not ready for general use.

## Why this exists

The existing community integration ([gjbadros/hass-binarymoip](https://github.com/gjbadros/hass-binarymoip)) is a proof of concept and doesn't cover the full API. This integration is being built fresh, walking `group_rx` for proper zone naming and supporting volume, source selection, mute, and (eventually) serial passthrough for TV control.

## Hardware tested against

- Snap One Core3 controller
- B-900-MOIP-A-TX (audio transmitter)
- B-900-MOIP-4K-TX (A/V transmitter)
- B-900-MOIP-4K-RX (A/V receiver)
- EA-MOIP-AMP-6D-50 (3-zone amp)
- EA-MOIP-AMP-12D-100 (6-zone amp)