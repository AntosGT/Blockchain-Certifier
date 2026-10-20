# PRIMA — prueba de existencia con sellos de tiempo en Bitcoin

![archivo -> hash -> Bitcoin -> certificado](docs/banner.png)

[English](README.md)

PRIMA genera evidencia digital verificable: un manifiesto canónico, un
sello de tiempo anclado en la cadena de bloques de Bitcoin (vía
OpenTimestamps) y un paquete de certificado que se puede verificar de
forma independiente, años después, sin esta plataforma ni ningún
tercero. Es evidencia de existencia, integridad y anterioridad — **no**
es un registro de propiedad intelectual y no prueba autoría.

Desarrollado siguiendo los principios FAIR (FAIR4RS): un JSON Schema
público del manifiesto, un verificador independiente, metadatos JSON-LD
(schema.org), una descripción OpenAPI estática de la API pública y un
servidor Model Context Protocol (MCP) para que agentes LLM (Claude, o
cualquier cliente compatible con MCP) puedan registrar y seguir evidencia
en nombre de una persona.

Hay una instancia pública en <https://certified.fragola.agency>; este
repositorio es el software detrás de ella, para que puedas correr tu
propia instancia, verificar un certificado de forma independiente, o
inspeccionar exactamente cómo se produce la evidencia.

## Estructura del repositorio

- `src/` — servidor: portal web, API JSON, worker y modelos de base de datos.
- `migrations/` — esquema de Postgres (se aplica con `python -m src.migrate`).
- `frontend/` — frontend estático servido delante de la API.
- `fragola_mcp/` — servidor MCP (herramientas `prima_*`), skill de
  Claude, esquemas públicos (JSON Schema del manifiesto, OpenAPI) y el
  verificador independiente.
- `tests/` — suite de tests (`pytest`).
- `docs/` — arquitectura y guía de instalación.

## Inicio rápido

```bash
cp .env.example .env
docker compose -f docker/docker-compose.dev.yml up -d
pip install -r requirements.txt
python -m src.migrate
python -m src.api
```

Ver [`docs/installation.md`](docs/installation.md) para la guía completa,
incluyendo el worker, el servidor MCP, la configuración de producción y
la suite de tests.

## Verificar un certificado

Sin depender de PRIMA ni de instalar el resto del repositorio, solo la
carpeta `fragola_mcp/verify/`:

```bash
pip install -r fragola_mcp/requirements.txt opentimestamps-client
python -m fragola_mcp.verify.prima_verify ruta/al/certificado.zip [archivo_original ...] --online
```

`--online` comprueba el encabezado real del bloque de Bitcoin en vez de
confiar en lo que dice la prueba. Sin esa opción, la verificación sigue
comprobando el manifiesto, su hash y la consistencia interna del sello,
pero no la cadena de bloques en sí. Ver
[`docs/architecture.md`](docs/architecture.md) para cómo funciona todo el
proceso de evidencia.

## Licencia

Apache License 2.0 — ver [`LICENSE`](LICENSE). El nombre y el logo de
Fragola son marcas registradas y no están cubiertos por esta licencia —
ver [`NOTICE`](NOTICE).

## Cita

```
Gagliano, Antonio (2026). PRIMA: proof of existence with Bitcoin timestamps (v1.0.0). Zenodo. https://doi.org/10.5281/zenodo.23512705
```

Ver [`CITATION.cff`](CITATION.cff). Autor: FAIR Certified researcher
Antonio Gagliano — <https://fragola.agency>.

## Palabras clave

proof of existence, Bitcoin timestamping, OpenTimestamps, digital
evidence, data provenance, FAIR principles, FAIR4RS, research software,
open science, MCP server, Claude.
