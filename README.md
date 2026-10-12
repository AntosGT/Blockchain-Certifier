# PRIMA · Blockchain certifier

PRIMA gives you **proof of existence** for your files: a certificate with a timestamp anchored in the Bitcoin network. It is verifiable evidence that a file existed, exactly as it is, at a given moment. It does not prove authorship and it is not an intellectual property registry.

This repository is the **Claude connector**: an MCP server (`fragola`) and a skill (`prima`) that let you tell Claude "certify this" and have the file registered in PRIMA (https://certified.fragola.agency).

- Your file **never leaves your computer**. Claude computes its SHA-256 hash locally; only the file name, size and hash are sent to the server.
- An exact copy of the original is kept in `Certificaciones/<project>/`, inside the folder where you open Claude.
- The certificate (PDF and verification package) arrives by email once Bitcoin confirms the timestamp. This usually takes one to five hours.
- Keep the file **exactly as you registered it**: verifying it later requires that same file.
- Free service limits: 50 MB per file, 50 MB per request, and 25 MB for video (480p is enough).

---

## Install with Claude (give Claude the link to this repo)

> **Instructions for Claude.** The person gave you the link to this repository so you install it. Follow these steps in order, show them each command before running it, and ask for confirmation when you change their configuration. Talk to them in their own language.

### 1. What you need from the person

Ask for these in a single message:

- their **email** (it receives the confirmation link and the certificate),
- their **full name** (it appears on the certificate),
- their **country** as a two-letter code (for example `AR`, `ES`, `US`).

### 2. Requirements

- Python 3.10 or newer (`python --version`). If it is missing, tell them how to install it and stop.
- `ffmpeg`, only if they will register videos larger than 25 MB (to reduce them to 480p).

### 3. Download the repository

Clone it into a stable folder in their user directory, for example `~/blockchain-certifier`:

```bash
git clone https://github.com/AntosGT/Blockchain-Certifier.git ~/blockchain-certifier
python -m pip install -r ~/blockchain-certifier/fragola_mcp/requirements.txt
```

### 4. Register the MCP server

**Claude Code** (user scope, so it works in any folder). Replace the person's values and the path to the clone:

```bash
claude mcp add -s user fragola \
  -e PRIMA_EMAIL="email@example.com" \
  -e PRIMA_NAME="First Last" \
  -e PRIMA_COUNTRY="AR" \
  -e PYTHONPATH="/path/to/blockchain-certifier" \
  -- python -m fragola_mcp.server
```

**Claude Desktop**: add this under `mcpServers` in their `claude_desktop_config.json`:

```json
"fragola": {
  "command": "python",
  "args": ["-m", "fragola_mcp.server"],
  "env": {
    "PRIMA_EMAIL": "email@example.com",
    "PRIMA_NAME": "First Last",
    "PRIMA_COUNTRY": "AR",
    "PYTHONPATH": "/path/to/blockchain-certifier"
  }
}
```

Optional variables: `PRIMA_FOLDER` (where the copies are kept; default `Certificaciones` inside the working directory), `PRIMA_LANG` (`en` or `es`; default `en`), `PRIMA_API_URL`, and `FRAGOLA_MCP_HOME` (where the connection key is stored; default `~/.fragola_mcp`).

### 5. Install the skill

Copy `skill/SKILL.md` to `~/.claude/skills/prima/SKILL.md` (Claude Code). The skill explains how to use the `prima_*` tools, how to talk to the person and what not to do.

### 6. Restart and connect

Ask the person to **open a new Claude session** (MCP servers load at startup). In that session, the first time:

1. Call `prima_conectar`: they receive an email with a confirmation link.
2. They paste the link into the chat and you call `prima_confirmar_conexion`. This is done once.

After that they can simply say "certify this file": you show the full declaration, wait for their explicit "yes", and only then register.

---

## Tools

| Tool | What it does |
|---|---|
| `prima_conectar` / `prima_confirmar_conexion` | One-time connection by email |
| `prima_ver_declaracion` | Shows the declaration that must be accepted on every registration |
| `prima_registrar_archivo` | Registers a file (local hash, exact copy in `Certificaciones/<project>/`) |
| `prima_procesar_pendientes` | Registers whatever is in the folder and is not registered yet |
| `prima_estado_registros` | Status of a project's registrations |
| `prima_reducir_video` | Reduces a video to 480p with ffmpeg to meet the limit |

## Verifying a certificate

The ZIP package that arrives by email contains the certificate, the Bitcoin proof and a guide to verify without depending on the platform. The skill helps the person follow those steps.

---

## Resumen en español

PRIMA te da una **prueba de existencia** de tus archivos: un certificado con sello de tiempo anclado en la red de Bitcoin. Prueba que un archivo existía, tal cual está, en un momento dado; no prueba autoría.

Este repositorio es el **conector para Claude** (servidor MCP `fragola` y skill `prima`). Pasale el link de este repo a tu Claude y lo instala: las instrucciones de arriba están escritas para que Claude las siga. Tus archivos no salen de tu computadora: al servidor solo viajan el nombre, el tamaño y la huella SHA-256. El certificado llega por correo cuando Bitcoin confirma el sello (normalmente de una a cinco horas). Guardá el archivo exactamente como lo registraste, porque para verificarlo hace falta ese mismo archivo.
