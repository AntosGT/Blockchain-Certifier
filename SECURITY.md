# Security policy

## Reporting a vulnerability

If you find a security issue in this software, please report it privately
instead of opening a public issue: email the maintainer listed in
`CITATION.cff`. Include steps to reproduce and the affected version.
Please allow a reasonable time to respond before any public disclosure.

## Scope

This repository ships the PRIMA server, its database migrations, the web
portal, the MCP server and the independent verifier. It does not ship:

- Production deployment configuration (reverse proxy, process manager,
  TLS termination) — that is specific to each deployment and is your
  responsibility to secure.
- The notification workflow (email delivery on registration and
  certification) — see `CHANGELOG.md`.
- Any secret, API key or credential. `.env.example` lists every setting
  with no values; generate your own secrets for `PROOF_STORAGE_SIGNING_SECRET`,
  `PROOF_SESSION_SECRET` and `PROOF_INTERNAL_API_TOKEN`.

## Operational guidance

- In `production` and `staging` the application refuses to start with
  default or missing secrets, or without Turnstile and the form guard
  enabled. `production` additionally refuses a bypass payment mode;
  `staging` allows it, for internal testing against a real deployment —
  see the `_production_guards` check in `src/core/config.py`.
- The server does not keep the client's original files by default
  (`PROOF_STORE_ORIGINALS=false`): only the fingerprint is computed and
  stored. Enabling it is meant for local development only.
- Rate limits (`PROOF_SUBMIT_RATE_PER_HOUR`, `PROOF_MCP_RATE_PER_HOUR`,
  and related settings) are enforced in the database, not only in memory;
  review them before exposing the service publicly.
