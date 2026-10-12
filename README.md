# PRIMA · Blockchain certifier

PRIMA le da a una persona una **prueba de existencia** de sus archivos: un certificado con sello de tiempo respaldado por la red de Bitcoin. Sirve como evidencia verificable de que un contenido existía, tal cual está, en una fecha determinada. No prueba autoría ni es un registro de propiedad intelectual.

Este repositorio es el **conector para Claude**: un servidor MCP (`fragola`) y una skill (`prima`) que permiten decirle a Claude "certificame esto" y que el archivo quede registrado en PRIMA (https://certified.fragola.agency).

- El archivo **nunca sale de tu computadora**. Claude calcula su huella SHA-256 localmente y al servidor solo viajan el nombre, el tamaño y la huella.
- Una copia exacta del original queda en `Certificaciones/<proyecto>/`, dentro de la carpeta desde la que abrís Claude.
- El certificado (PDF y paquete de verificación) llega por correo cuando Bitcoin confirma el sello. Puede demorar de una a cinco horas.
- Guardá el archivo **exactamente como lo registraste**: para verificarlo hace falta ese mismo archivo.
- Servicio gratuito: hasta 50 MB por archivo, 50 MB por pedido y 25 MB para video (con 480p alcanza).

---

## Instalación con Claude (para pasarle el link de este repo)

> **Instrucciones para Claude.** La persona te pasó el link de este repositorio para que lo instales. Seguí estos pasos en orden, mostrale cada comando antes de ejecutarlo y pedile confirmación cuando cambies su configuración.

### 1. Datos que necesitás de la persona

Pedile, en un solo mensaje:

- su **correo** (recibirá el enlace de confirmación y el certificado),
- su **nombre completo** (figura en el certificado),
- su **país**, como código de dos letras (por ejemplo `AR`, `ES`, `US`).

### 2. Requisitos

- Python 3.10 o superior (`python --version`). Si falta, indicale cómo instalarlo y detenete.
- `ffmpeg` solo si va a registrar videos de más de 25 MB (para reducirlos a 480p).

### 3. Descargar el repositorio

Clonalo en una carpeta estable de su usuario, por ejemplo `~/blockchain-certifier`:

```bash
git clone https://github.com/AntosGT/blockchain-certifier.git ~/blockchain-certifier
python -m pip install -r ~/blockchain-certifier/fragola_mcp/requirements.txt
```

### 4. Registrar el servidor MCP

**Claude Code** (alcance de usuario, para que funcione en cualquier carpeta). Reemplazá los valores de la persona y la ruta del clon:

```bash
claude mcp add -s user fragola \
  -e PRIMA_EMAIL="correo@ejemplo.com" \
  -e PRIMA_NAME="Nombre Apellido" \
  -e PRIMA_COUNTRY="AR" \
  -e PYTHONPATH="/ruta/al/clon/blockchain-certifier" \
  -- python -m fragola_mcp.server
```

**Claude Desktop**: agregá esto en `mcpServers` de su `claude_desktop_config.json`:

```json
"fragola": {
  "command": "python",
  "args": ["-m", "fragola_mcp.server"],
  "env": {
    "PRIMA_EMAIL": "correo@ejemplo.com",
    "PRIMA_NAME": "Nombre Apellido",
    "PRIMA_COUNTRY": "AR",
    "PYTHONPATH": "/ruta/al/clon/blockchain-certifier"
  }
}
```

Variables opcionales: `PRIMA_FOLDER` (carpeta donde se guardan las copias; por defecto `Certificaciones` dentro del directorio de trabajo), `PRIMA_LANG` (`es` o `en`), `PRIMA_API_URL` y `FRAGOLA_MCP_HOME` (dónde se guarda la clave de conexión; por defecto `~/.fragola_mcp`).

### 5. Instalar la skill

Copiá `skill/SKILL.md` a `~/.claude/skills/prima/SKILL.md` (Claude Code). La skill te explica cómo usar las herramientas `prima_*`, cómo hablarle a la persona y qué no hacer.

### 6. Reiniciar y conectar

Pedile a la persona que **abra una sesión nueva de Claude** (los servidores MCP se cargan al iniciar). En esa sesión, la primera vez:

1. Llamá `prima_conectar`: le llega un correo con un enlace de confirmación.
2. Que pegue el enlace en el chat y llamá `prima_confirmar_conexion`. Se hace una sola vez.

Después ya puede decir "certificame este archivo": le mostrás la declaración completa, espera su "sí" explícito y recién ahí se registra.

---

## Herramientas

| Herramienta | Qué hace |
|---|---|
| `prima_conectar` / `prima_confirmar_conexion` | Conexión única por correo |
| `prima_ver_declaracion` | Muestra la declaración que hay que aceptar en cada registro |
| `prima_registrar_archivo` | Registra un archivo (huella local, copia exacta en `Certificaciones/<proyecto>/`) |
| `prima_procesar_pendientes` | Registra lo que haya en la carpeta y todavía no esté registrado |
| `prima_estado_registros` | Estado de los registros de un proyecto |
| `prima_reducir_video` | Reduce un video a 480p con ffmpeg para cumplir el límite |

## Verificar un certificado

El paquete ZIP que llega por correo trae el certificado, la prueba de Bitcoin y una guía para verificar sin depender de la plataforma. La skill ayuda a la persona a seguir esos pasos.

---

## English summary

PRIMA gives you **proof of existence** for your files: a certificate with a timestamp anchored in the Bitcoin network. It proves a file existed, exactly as it is, at a given time. It does not prove authorship.

This repository is the **Claude connector**: an MCP server (`fragola`) and a skill (`prima`). Give Claude the link to this repo and it will install them for you (see the steps above; the instructions are written for Claude). Your files never leave your computer: only the file name, size and SHA-256 hash are sent. The certificate arrives by email once Bitcoin confirms the timestamp (usually one to five hours).
