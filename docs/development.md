# Development

## Credential handling

Credentials for the MoIP controller are **never hardcoded**. There are two
distinct modes, depending on how the code is running.

### Production (normal HA install) — HA config entry

When the integration is installed in Home Assistant, the user adds it via
**Settings → Devices & Services → Add Integration → Binary MoIP** and enters
host/credentials in the config-flow UI. Home Assistant stores these
**encrypted in its own config store** (`.storage/core.config_entries`).

At runtime the integration reads them from the config entry — see
`__init__.py::async_setup_entry`, which pulls `entry.data[CONF_*]` and passes
them to `BinaryMoIPClient`. No `.env`, no files, no environment variables are
involved in production.

### Development / test (outside the HA UI) — `.env`

For iterating on the API client without going through the HA UI (e.g.
`scripts/dev_discover.py`, integration tests against the live controller), we
read credentials from a project-root `.env` file using
[`python-dotenv`](https://pypi.org/project/python-dotenv/).

`.env` is **gitignored** (verified) and must never be committed. The committed
`.env.example` documents the variable names.

#### Setup

```bash
pip install -r requirements-dev.txt
cp .env.example .env            # then edit .env with real values
python scripts/dev_discover.py  # authenticates + dumps discovery
```

#### Variables

| Variable | Required | Default | Notes |
|----------|:--------:|---------|-------|
| `MOIP_HOST` | ✅ | — | Controller IP, e.g. `10.4.1.50` |
| `MOIP_USERNAME` | ✅ | — | Same account as OvrC / MoIP web UI |
| `MOIP_PASSWORD` | ✅ | — | — |
| `MOIP_PORT` | ❌ | `443` | HTTPS port |
| `MOIP_VERIFY_SSL` | ❌ | `false` | Controllers use self-signed certs |

> The dev `.env` is a convenience for local testing only. It is **not** a
> second source of truth — production always uses the HA config entry.

## Auth model (for reference)

`POST /api/v1/base/auth/login` with `{username, password}` returns
`{accessToken, tokenType: "Bearer", expiresIn: 3600}`. There is **no refresh
token** — when the access token expires, the client re-logs-in with the stored
credentials. This is why the username/password must be retained (HA config
entry in prod, `.env` in dev), not just a token.

## Dev Home Assistant

See [`ha-dev/README.md`](../ha-dev/README.md) for the Docker-based dev HA
instance (starts at http://localhost:8123, mounts this repo's
`custom_components/` so HA loads the integration).
