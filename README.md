# PRIMA — proof of existence with Bitcoin timestamps

![file -> hash -> Bitcoin -> certificate](docs/banner.png)

[Español](README.es.md)

PRIMA generates verifiable digital evidence: a canonical manifest, a
timestamp proof anchored in the Bitcoin blockchain (via OpenTimestamps),
and a certificate package you can verify independently, years later,
without this platform or any third party. It is evidence of existence,
integrity and anteriority — **not** an intellectual property registry,
and it does not prove authorship.

Developed following FAIR principles (FAIR4RS): a public JSON Schema for
the manifest, an independent verifier, JSON-LD (schema.org) metadata, a
static OpenAPI description of the public API, and a Model Context
Protocol (MCP) server so LLM agents (Claude, or any MCP-compatible client)
can register and track evidence on someone's behalf.

A running instance is public at <https://certified.fragola.agency>; this
repository is the software behind it, so you can run your own, verify a
certificate independently, or inspect exactly how the evidence is produced.

## Repository layout

- `src/` — server: web portal, JSON API, worker and database models.
- `migrations/` — Postgres schema (applied by `python -m src.migrate`).
- `frontend/` — static frontend served in front of the API.
- `fragola_mcp/` — MCP server (`prima_*` tools), Claude skill, public
  schemas (manifest JSON Schema, OpenAPI) and the independent verifier.
- `tests/` — test suite (`pytest`).
- `docs/` — architecture and installation guide.

## Quick start

```bash
cp .env.example .env
docker compose -f docker/docker-compose.dev.yml up -d
pip install -r requirements.txt
python -m src.migrate
python -m src.api
```

See [`docs/installation.md`](docs/installation.md) for the full guide,
including the worker, the MCP server, production settings and the test
suite.

## Verify a certificate

No dependency on PRIMA or this repository being installed beyond the
`fragola_mcp/verify/` folder:

```bash
pip install -r fragola_mcp/requirements.txt opentimestamps-client
python -m fragola_mcp.verify.prima_verify path/to/certificate.zip [original_file ...] --online
```

`--online` checks the Bitcoin block header directly instead of trusting
the proof's own claim. Without it, verification still checks the manifest,
its hash and the timestamp proof's internal consistency, but not the
blockchain itself. See [`docs/architecture.md`](docs/architecture.md) for
how the whole evidence pipeline works.

## License

Apache License 2.0 — see [`LICENSE`](LICENSE). The Fragola name and logo
are trademarks and are not covered by this license — see
[`NOTICE`](NOTICE).

## Citation

```
Gagliano, Antonio (2026). PRIMA: proof of existence with Bitcoin timestamps (v1.0.0). Zenodo. https://doi.org/10.5281/zenodo.23512705
```

See [`CITATION.cff`](CITATION.cff). Author: FAIR Certified researcher
Antonio Gagliano — <https://fragola.agency>.

## Keywords

proof of existence, Bitcoin timestamping, OpenTimestamps, digital
evidence, data provenance, FAIR principles, FAIR4RS, research software,
open science, MCP server, Claude.
