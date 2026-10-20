# Changelog

All notable changes to this project are documented in this file.

## [1.0.0] - 2026-10-19

First public release, developed following FAIR principles (FAIR4RS).

### Added
- Core evidence engine: canonical manifest, SHA-256 fingerprints, OpenTimestamps proof anchored in the Bitcoin blockchain, PDF certificate with a verification package (ZIP).
- Web portal (FastAPI, server-rendered) with email verification before the registration form, Turnstile bot protection and an admin dashboard.
- Model Context Protocol (MCP) server (`fragola_mcp`) with a `prima_*` tool set and a Claude skill, so an LLM agent can register and track evidence on behalf of a person.
- Public JSON Schema (draft 2020-12) for the evidence manifest.
- Independent verifier (`prima_verify.py`): a standalone script, with no dependency on the PRIMA service, that checks a certificate package (manifest, Bitcoin timestamp, original files) against the real Bitcoin blockchain.
- JSON-LD (schema.org) metadata generated locally by the MCP for each registered project.
- Static OpenAPI 3.1 description of the public API surface.
- Optional `version` and ORCID fields in the MCP registration tool, kept local to the person's machine.

### Notes
- The `Prima · Avisos` n8n workflow that sends confirmation and certificate emails is not included in this release; self-hosted deployments need to build their own notification flow. See `SECURITY.md` and the installation guide for what a self-hosted deployment is responsible for.
