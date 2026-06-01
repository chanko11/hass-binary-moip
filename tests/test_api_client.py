"""Tests for BinaryMoIPClient behavior: auth, request retry, commands, discovery.

Driven by the FakeSession in conftest.py — no real controller or network.
"""

from __future__ import annotations

import pytest
from aiohttp import ClientConnectionError, ClientError, ClientResponseError

from conftest import FakeResponse, api


# --- authenticate -----------------------------------------------------------


async def test_authenticate_stores_token_and_expiry(make_client, monkeypatch):
    monkeypatch.setattr(api.time, "monotonic", lambda: 1000.0)
    session_post = lambda **kw: FakeResponse(
        json_data={"accessToken": "tok-123", "tokenType": "Bearer", "expiresIn": 3600}
    )
    from conftest import FakeSession

    session = FakeSession(post_handler=session_post)
    client = make_client(session)

    await client.authenticate()

    assert client._access_token == "tok-123"
    # expiry = now + expiresIn - margin(30)
    assert client._token_expires_at == 1000.0 + 3600 - 30
    # credentials posted to the login path
    assert session.post_calls[0]["json"] == {"username": "admin", "password": "secret"}
    assert session.post_calls[0]["url"].endswith("/api/v1/base/auth/login")


@pytest.mark.parametrize("status", [400, 401, 403])
async def test_authenticate_rejected_raises_auth_error(make_client, status):
    from conftest import FakeSession

    session = FakeSession(post_handler=lambda **kw: FakeResponse(status=status))
    client = make_client(session)
    with pytest.raises(api.BinaryMoIPAuthError):
        await client.authenticate()


async def test_authenticate_missing_token_raises_auth_error(make_client):
    from conftest import FakeSession

    session = FakeSession(post_handler=lambda **kw: FakeResponse(json_data={"tokenType": "Bearer"}))
    client = make_client(session)
    with pytest.raises(api.BinaryMoIPAuthError):
        await client.authenticate()


async def test_authenticate_connection_error_is_wrapped(make_client):
    from conftest import FakeSession

    def boom(**kw):
        raise ClientConnectionError("no route to host")

    client = make_client(FakeSession(post_handler=boom))
    with pytest.raises(api.BinaryMoIPConnectionError):
        await client.authenticate()


async def test_authenticate_5xx_raises_via_raise_for_status(make_client):
    from conftest import FakeSession

    session = FakeSession(post_handler=lambda **kw: FakeResponse(status=500))
    client = make_client(session)
    # 500 is not in the auth-reject set, so raise_for_status fires -> wrapped as
    # a connection error (ClientResponseError is a ClientError subclass).
    with pytest.raises(api.BinaryMoIPConnectionError):
        await client.authenticate()


# --- _ensure_token / _request -----------------------------------------------


def _auth_post():
    return lambda **kw: FakeResponse(json_data={"accessToken": "tok", "expiresIn": 3600})


async def test_request_authenticates_then_succeeds(make_client):
    from conftest import FakeSession

    session = FakeSession(
        post_handler=_auth_post(),
        request_handler=lambda **kw: FakeResponse(json_data={"ok": True}),
    )
    client = make_client(session)
    result = await client._request("GET", "/api/v1/moip/unit")
    assert result == {"ok": True}
    # Authenticated lazily before the request, and sent the bearer header.
    assert len(session.post_calls) == 1
    assert session.request_calls[0]["headers"]["Authorization"] == "Bearer tok"


async def test_request_204_returns_none(make_client):
    from conftest import FakeSession

    session = FakeSession(
        post_handler=_auth_post(),
        request_handler=lambda **kw: FakeResponse(status=204),
    )
    client = make_client(session)
    assert await client._request("PUT", "/x", json={"a": 1}) is None


async def test_request_reauths_once_on_401_then_retries(make_client):
    from conftest import FakeSession

    state = {"n": 0}

    def request_handler(**kw):
        state["n"] += 1
        if state["n"] == 1:
            return FakeResponse(status=401)  # token rejected
        return FakeResponse(json_data={"retried": True})

    session = FakeSession(post_handler=_auth_post(), request_handler=request_handler)
    client = make_client(session)

    result = await client._request("GET", "/api/v1/moip/unit")
    assert result == {"retried": True}
    assert state["n"] == 2                  # one retry
    assert len(session.post_calls) == 2     # initial auth + re-auth on 401


async def test_request_401_twice_raises_auth_error(make_client):
    from conftest import FakeSession

    session = FakeSession(
        post_handler=_auth_post(),
        request_handler=lambda **kw: FakeResponse(status=401),
    )
    client = make_client(session)
    with pytest.raises(api.BinaryMoIPAuthError):
        await client._request("GET", "/api/v1/moip/unit")


async def test_request_http_error_wrapped_as_binary_moip_error(make_client):
    from conftest import FakeSession

    session = FakeSession(
        post_handler=_auth_post(),
        request_handler=lambda **kw: FakeResponse(status=500),
    )
    client = make_client(session)
    with pytest.raises(api.BinaryMoIPError) as exc:
        await client._request("GET", "/api/v1/moip/unit")
    # Not the connection/auth subclasses — a generic API error.
    assert not isinstance(exc.value, (api.BinaryMoIPConnectionError, api.BinaryMoIPAuthError))


async def test_request_connection_error_wrapped(make_client):
    from conftest import FakeSession

    def boom(**kw):
        raise ClientError("connection reset")

    session = FakeSession(post_handler=_auth_post(), request_handler=boom)
    client = make_client(session)
    with pytest.raises(api.BinaryMoIPConnectionError):
        await client._request("GET", "/api/v1/moip/unit")


async def test_request_skips_reauth_when_token_still_valid(make_client, monkeypatch):
    from conftest import FakeSession

    monkeypatch.setattr(api.time, "monotonic", lambda: 0.0)
    session = FakeSession(
        post_handler=_auth_post(),
        request_handler=lambda **kw: FakeResponse(json_data={}),
    )
    client = make_client(session)
    await client._request("GET", "/a")
    await client._request("GET", "/b")
    assert len(session.post_calls) == 1  # authenticated only once


# --- commands: volume / mute / source ---------------------------------------


def _capture_session():
    """A session whose request_handler records the last PUT payload."""
    from conftest import FakeSession

    sent: list[dict] = []

    def handler(**kw):
        sent.append(kw)
        return FakeResponse(status=204)

    return FakeSession(post_handler=_auth_post(), request_handler=handler), sent


@pytest.mark.parametrize(
    ("volume", "rng", "max_volume", "expected"),
    [
        (0.5, (0.0, 100.0), None, 50.0),
        (0.5, (10.0, 90.0), None, 50.0),     # scales into a non-zero range
        (0.0, (10.0, 90.0), None, 10.0),
        (1.0, (10.0, 90.0), None, 90.0),
        (1.5, (0.0, 100.0), None, 100.0),    # clamped above 1.0
        (-0.5, (0.0, 100.0), None, 0.0),     # clamped below 0.0
        (0.5, (0.0, 100.0), 30.0, 30.0),     # capped by max_volume
    ],
)
async def test_set_volume_scales_and_clamps(make_client, volume, rng, max_volume, expected):
    session, sent = _capture_session()
    client = make_client(session)
    zone = api.MoIPZone(
        group_id=1, name="Z", audio_rx_id=21, volume_range=rng, max_volume=max_volume
    )
    await client.async_set_volume(zone, volume)
    assert sent[0]["json"] == {"settings": {"volume": expected}}
    assert sent[0]["url"].endswith("/api/v1/moip/audio_rx/21")


async def test_set_volume_without_audio_rx_raises(make_client):
    session, sent = _capture_session()
    client = make_client(session)
    zone = api.MoIPZone(group_id=1, name="Z", audio_rx_id=None)
    with pytest.raises(api.BinaryMoIPError):
        await client.async_set_volume(zone, 0.5)
    assert sent == []  # no request issued


async def test_set_mute_uses_supported_output_ports(make_client):
    session, sent = _capture_session()
    client = make_client(session)
    zone = api.MoIPZone(group_id=1, name="Z", audio_rx_id=21, mute_ports=["lineout", "speaker"])
    await client.async_set_mute(zone, True)
    assert sent[0]["json"] == {"settings": {"mute": ["lineout", "speaker"]}}


async def test_unmute_sends_empty_list(make_client):
    session, sent = _capture_session()
    client = make_client(session)
    zone = api.MoIPZone(group_id=1, name="Z", audio_rx_id=21, mute_ports=["lineout"])
    await client.async_set_mute(zone, False)
    assert sent[0]["json"] == {"settings": {"mute": []}}


async def test_set_mute_without_audio_rx_raises(make_client):
    session, sent = _capture_session()
    client = make_client(session)
    with pytest.raises(api.BinaryMoIPError):
        await client.async_set_mute(api.MoIPZone(group_id=1, name="Z"), True)
    assert sent == []


async def test_select_source_routes_paired_tx(make_client):
    session, sent = _capture_session()
    client = make_client(session)
    await client.async_select_source(11, 41)
    assert sent[0]["json"] == {"associations": {"paired_tx": 41}}
    assert sent[0]["url"].endswith("/api/v1/moip/group_rx/11")


async def test_select_source_none_unpairs(make_client):
    session, sent = _capture_session()
    client = make_client(session)
    await client.async_select_source(11, None)
    assert sent[0]["json"] == {"associations": {"paired_tx": None}}


# --- async_discover ---------------------------------------------------------


# Canonical paths -> canned JSON, modeling one amp with one zone (Kitchen) and
# one source (TX-1), each backed by audio_rx/audio_tx hardware.
_DISCOVERY = {
    "/api/v1/moip/unit": {"items": [3]},
    "/api/v1/moip/unit/3": {
        "id": 3,
        "settings": {"name": "Main Amp"},
        "status": {"model": "EA-MOIP-AMP-12D-100"},
        "associations": {"group": {"rx": [11], "tx": [41]}},
    },
    "/api/v1/moip/group_rx/11": {
        "id": 11,
        "settings": {"name": "Kitchen"},
        "associations": {"unit": 3, "audio_rx": 21, "paired_tx": 41},
        "status": {"state": "streaming"},
    },
    "/api/v1/moip/group_tx/41": {
        "id": 41,
        "settings": {"name": "TX-1"},
        "associations": {"unit": 3, "audio_tx": 51},
        "status": {"state": "streaming"},
    },
    "/api/v1/moip/audio_rx/21": {
        "settings": {
            "volume": 40,
            "maxvolume": 80,
            "supported_volume": {"range": [0, 100]},
            "supported_output": ["lineout"],
            "mute": [],
        },
        "status": {"state": "streaming"},
    },
    "/api/v1/moip/audio_tx/51": {
        "label": "Digital Input",
        "settings": {"source": ["toslink"]},
    },
}


async def test_async_discover_builds_full_topology(make_client):
    from conftest import FakeSession

    base = "https://ctrl.local:443"

    def request_handler(method, url, **kw):
        path = url[len(base):]
        return FakeResponse(json_data=_DISCOVERY[path])

    session = FakeSession(post_handler=_auth_post(), request_handler=request_handler)
    client = make_client(session)

    topo = await client.async_discover()

    assert set(topo.units) == {3}
    assert topo.units[3].model == "EA-MOIP-AMP-12D-100"

    assert set(topo.zones) == {11}
    zone = topo.zones[11]
    assert zone.name == "Kitchen"           # group_rx name, not hardware label
    assert zone.audio_rx_id == 21
    assert zone.paired_tx_id == 41
    assert zone.volume == 40 and zone.max_volume == 80
    assert zone.volume_range == (0.0, 100.0)
    assert zone.muted is False

    assert set(topo.sources) == {41}
    source = topo.sources[41]
    assert source.name == "TX-1"
    assert source.unit_name == "Main Amp"
    assert source.hw_label == "Digital Input"
    assert source.input_type == "toslink"   # normalized from ["toslink"]


def _discovery_session(routes):
    from conftest import FakeSession

    base = "https://ctrl.local:443"
    return FakeSession(
        post_handler=_auth_post(),
        request_handler=lambda method, url, **kw: FakeResponse(
            json_data=routes[url[len(base):]]
        ),
    )


async def test_async_get_zones_returns_list(make_client):
    client = make_client(_discovery_session(_DISCOVERY))
    zones = await client.async_get_zones()
    assert [z.name for z in zones] == ["Kitchen"]


async def test_async_get_units_returns_list(make_client):
    client = make_client(_discovery_session(_DISCOVERY))
    units = await client.async_get_units()
    assert [u.name for u in units] == ["Main Amp"]


async def test_async_get_sources_returns_list(make_client):
    client = make_client(_discovery_session(_DISCOVERY))
    sources = await client.async_get_sources()
    assert [s.name for s in sources] == ["TX-1"]


# Topology where the zone/source lack backing hardware ids, so discovery uses
# the _none() placeholder instead of fetching audio_rx/audio_tx.
_DISCOVERY_NO_HW = {
    "/api/v1/moip/unit": {"items": [3]},
    "/api/v1/moip/unit/3": {
        "id": 3,
        "settings": {"name": "Amp"},
        "associations": {"group": {"rx": [11], "tx": [41]}},
    },
    "/api/v1/moip/group_rx/11": {
        "id": 11,
        "settings": {"name": "Kitchen"},
        "associations": {},  # no audio_rx
    },
    "/api/v1/moip/group_tx/41": {
        "id": 41,
        "settings": {"name": "TX-1"},
        "associations": {},  # no audio_tx
    },
}


async def test_async_discover_handles_zones_without_backing_hardware(make_client):
    client = make_client(_discovery_session(_DISCOVERY_NO_HW))
    topo = await client.async_discover()
    zone = topo.zones[11]
    assert zone.audio_rx_id is None
    assert zone.volume is None          # nothing applied via _none placeholder
    assert topo.sources[41].input_type is None


# --- websocket --------------------------------------------------------------


async def test_ws_connect_smuggles_token_via_subprotocol(make_client):
    from conftest import FakeSession

    sentinel_ws = object()
    session = FakeSession(post_handler=_auth_post(), ws=sentinel_ws)
    client = make_client(session)

    ws = await client.async_ws_connect()
    assert ws is sentinel_ws
    call = session.ws_calls[0]
    assert call["url"] == "wss://ctrl.local:443/api/v1/moip/change"
    assert call["protocols"] == ("Bearer.tok",)   # JWT in the WS subprotocol
    assert call["heartbeat"] == 30
