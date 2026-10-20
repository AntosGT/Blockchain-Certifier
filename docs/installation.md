# Installation

This guide covers running the PRIMA server yourself. It does not cover any
specific hosting provider — adapt it to your own infrastructure.

## Requirements

- Python 3.12+ (the Docker image uses 3.12; development was done on 3.13)
- PostgreSQL 15+
- `ffmpeg` on PATH (video reduction tool)
- Chromium, for PDF certificate rendering (installed automatically by
  Playwright in the Docker image; run `playwright install --with-deps chromium`
  if you install without Docker)

## Local development (Docker Compose for Postgres only)

```bash
cp .env.example .env            # fill in secrets for your own setup
docker compose -f docker/docker-compose.dev.yml up -d   # Postgres
pip install -r requirements.txt
python -m src.migrate           # applies migrations/
python -m src.api               # web portal + JSON API on PROOF_PORT (default 8050)
python -m src.worker            # background worker: OpenTimestamps, certificates, notifications
```

Emails in development are written to `PROOF_OUTBOX_DIR` as `.eml` files —
nothing leaves your machine with the default `.env.example` settings. In
production and staging, notifications are delivered by an external consumer
(n8n, in this deployment) through the internal API (`PROOF_NOTIFICATION_DELIVERY=external`).

## Running the full test suite

```bash
pip install -r requirements.txt   # includes httpx and pdfplumber, test-only
python -m pytest
```

Stop any running `python -m src.worker` process before running the suite:
it shares a lock with the tests and they fail intermittently otherwise.

## Production / staging

- Set `PROOF_ENV=staging` or `PROOF_ENV=production`. Both modes refuse to
  start with default secrets, a bypass payment mode (production only),
  or without Turnstile and the form guard enabled. See
  `_production_guards` in `src/core/config.py` for the exact checks.
- Run migrations with a database user that owns the tables, separate from
  the user the application uses day to day: `python -m src.migrate`.
- `PROOF_NOTIFICATION_DELIVERY=external` expects something consuming the
  internal notifications API to actually send emails (confirmation link,
  certificate delivery, failure alerts). This project does not ship that
  workflow — see `CHANGELOG.md`. Build your own consumer against
  `src/api/internal.py`.
- Put a reverse proxy (TLS, your own domain) in front of `src.api`. Set
  `PROOF_PROXY_IP_HEADER` and `PROOF_TRUSTED_PROXY_IPS` so client IPs used
  for rate limiting come from the real visitor, not your proxy.
- The Docker image (`Dockerfile`) builds the API; run `python -m src.worker`
  as a separate process or container with the same `.env`.

## MCP server (fragola_mcp)

A separate, optional component: a Model Context Protocol server exposing
`prima_*` tools to an LLM agent (Claude, or any MCP-compatible client), so
the agent can register and track evidence on behalf of a person against a
running PRIMA server (yours, or the public one at
`https://certified.fragola.agency`).

```bash
pip install -r fragola_mcp/requirements.txt
PRIMA_API_URL=https://your-prima-instance python -m fragola_mcp.server
```

See `fragola_mcp/prima/skill/SKILL.md` for the agent-facing instructions and
`fragola_mcp/schema/prima-api.openapi.json` for the exact API surface the
MCP entry talks to.

## Verifying a certificate independently

`fragola_mcp/verify/prima_verify.py` has no dependency on a running PRIMA
server. See the main `README.md`, section "Verify a certificate", for usage.
