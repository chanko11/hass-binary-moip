# Tests

Run everything (with coverage, configured in `pytest.ini`):

```bash
pip install -r requirements-dev.txt
python3 -m pytest
```

Current coverage is ~99% of the integration package.

## Two groups, one command

### `api.py` — no Home Assistant needed

`test_api_parsing.py` and `test_api_client.py` exercise the REST client and its
pure parsing/normalization helpers. `api.py` depends only on `aiohttp` + stdlib,
so `conftest.py` loads it by file path (bypassing the package `__init__`, which
would import `homeassistant`) and drives it with a `FakeSession` that records
calls and returns canned responses.

Covered: `_opt_int`, `_parse_unit/_zone/_source`, `_apply_audio_rx/_audio_tx`
(incl. the firmware `settings.source`-as-list quirk), volume scaling/clamping,
mute port semantics, source routing/unpair, JWT auth + expiry, the 401
re-auth-and-retry path, full topology discovery (with and without backing
hardware), and websocket subprotocol auth.

### HA-backed modules — `pytest-homeassistant-custom-component`

`test_config_flow.py`, `test_coordinator.py`, `test_media_player.py`, and
`test_init.py` import the modules that load `homeassistant`, using phcc's
`hass` / `enable_custom_integrations` fixtures and `MockConfigEntry`.

- **config flow** — user setup (success + invalid_auth / cannot_connect /
  unknown / duplicate-abort) and the options flow (zones + sources steps, plus
  the `_unique_displays` / `_build_options_schema` / `_parse_options` helpers).
- **coordinator** — `_async_update_data` (success + auth/generic → UpdateFailed),
  the `_ws_consume` change filter, and the `_ws_listen` connect/reconnect loop.
- **media_player** — `_source_label` / `_build_source_maps` disambiguation, all
  entity properties (state, volume scaling, mute, source/source_list,
  availability, device_info), and the service calls.
- **init** — end-to-end entry setup → entity creation, unload, and the
  options-change reload listener, with the client fully mocked.

phcc pins an exact Home Assistant version; track it to the version running in
`ha-dev` (`requirements-dev.txt`). Tests are validated on HA 2026.6.0b0 /
Python 3.14.
