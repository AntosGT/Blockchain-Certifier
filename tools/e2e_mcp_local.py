"""LOCAL end-to-end test of the MCP: real server (HTTP) + real MCP program (stdio), with ffmpeg. Its own temporary database
(a schema dropped at the end), port 8077 and temporary folders. Requires the development database (docker/docker-compose.dev.yml)
and ffmpeg in the PATH. Usage: python tools/e2e_mcp_local.py"""
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
from pathlib import Path

import psycopg
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
PORT = 8077
BASE = f"http://127.0.0.1:{PORT}"
SCHEMA = "t_e2e_" + uuid.uuid4().hex[:8]
DSN = "host=127.0.0.1 port=55432 dbname=prima_dev user=prima password=prima_dev"
TMP = Path(tempfile.mkdtemp(prefix="prima_e2e_"))
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
results = []

def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("OK   " if ok else "FALLA"), name, ("- " + str(detail)) if detail else "")

def q(sql, args=()):
    with psycopg.connect(DSN, autocommit=True, options=f"-csearch_path={SCHEMA}") as conn:
        return conn.execute(sql, args).fetchall()

def wait_health():
    for _ in range(60):
        try:
            with urllib.request.urlopen(BASE + "/salud", timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(1)
    return False

async def call(session, tool, **args):
    res = await session.call_tool(tool, args)
    text = res.content[0].text if res.content else "{}"
    try:
        return json.loads(text)
    except Exception:
        return {"raw": text}

async def flow():
    home, folder = TMP / "home", TMP / "Certificaciones"
    env = dict(os.environ, PRIMA_API_URL=BASE, PRIMA_EMAIL="e2e@example.com", PRIMA_NAME="Prueba E2E", PRIMA_COUNTRY="AR",
               PRIMA_FOLDER=str(folder), FRAGOLA_MCP_HOME=str(home), PRIMA_MAX_VIDEO_MB="1", PRIMA_LANG="es")
    params = StdioServerParameters(command=sys.executable, args=["-m", "fragola_mcp.server"], cwd=str(ROOT), env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()
            names = sorted(t.name for t in (await s.list_tools()).tools)
            check("el MCP real lista sus 7 herramientas prima_*", len(names) == 7 and all(n.startswith("prima_") for n in names), names)

            r = await call(s, "prima_registrar_archivo", proyecto="P", ruta="x", declaracion_aceptada=True)
            check("sin conectarse no deja registrar y pide conectarse", r.get("ok") is False and "prima_conectar" in json.dumps(r, ensure_ascii=False).lower(), r)

            r = await call(s, "prima_conectar")
            check("prima_conectar envia el correo", r.get("ok") and r.get("sent_to") == "e2e@example.com", r)
            row = q("SELECT payload FROM notifications WHERE kind='cliente.confirmacion_email'")[0][0]
            check("el aviso lleva purpose=mcp", row.get("purpose") == "mcp", row)
            link = row["form_url"]

            r = await call(s, "prima_confirmar_conexion", enlace=link)
            check("prima_confirmar_conexion guarda la clave sin mostrarla", r.get("ok") and "prima_" not in json.dumps(r), r)
            key_file = home / "prima.json"
            check("la clave quedo guardada en la PC", key_file.exists())
            check("en la base solo esta el hash de la clave", q("SELECT count(*) FROM mcp_keys")[0][0] == 1)

            doc = TMP / "propuesta.txt"
            doc.write_text("contenido de la propuesta para el cliente\n", encoding="utf-8")
            r = await call(s, "prima_registrar_archivo", proyecto="Proyecto Uno", ruta=str(doc), declaracion_aceptada=True)
            check("registrar sin pedir la declaracion se rechaza", r.get("ok") is False and "declaraci" in json.dumps(r, ensure_ascii=False).lower(), r)

            r = await call(s, "prima_ver_declaracion")
            check("prima_ver_declaracion devuelve el texto y no el comprobante", r.get("ok") and r.get("text") and "receipt" not in r, list(r))
            r = await call(s, "prima_registrar_archivo", proyecto="Proyecto Uno", ruta=str(doc), declaracion_aceptada=True)
            check("registrar un documento funciona", r.get("ok") and r.get("status") == "PROCESSED", r)
            check("el original queda en Certificaciones/<proyecto>", (folder / "Proyecto Uno" / "propuesta.txt").read_bytes() == doc.read_bytes())
            files = q("SELECT filename, storage_reference, size FROM evidence_files")
            check("el servidor guardo la huella y NO el archivo", files == [("propuesta.txt", "", doc.stat().st_size)], files)
            stored = [p for p in (TMP / "storage").rglob("*") if p.is_file() and "certificates" not in p.parts]
            check("no hay archivos de usuarios en el almacenamiento del servidor", stored == [], [str(p) for p in stored])
            req = q("SELECT source, project, email FROM evidence_requests")[0]
            check("origen mcp, proyecto y email de la clave", req == ("mcp", "Proyecto Uno", "e2e@example.com"), req)

            r = await call(s, "prima_registrar_archivo", proyecto="Proyecto Uno", ruta=str(doc), declaracion_aceptada=True)
            check("el mismo archivo no se registra dos veces", r.get("already_registered") and q("SELECT count(*) FROM evidence_requests")[0][0] == 1, r)

            # Real video with ffmpeg
            clip = TMP / "video_hd.mp4"
            subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30", "-f", "lavfi", "-i", "sine=frequency=440",
                            "-t", "10", "-c:v", "libx264", "-crf", "14", "-pix_fmt", "yuv420p", "-c:a", "aac", str(clip)],
                           capture_output=True, creationflags=NOWIN, check=True)
            check("video de prueba mas pesado que el limite", clip.stat().st_size > 1024 * 1024, f"{clip.stat().st_size} bytes")
            r = await call(s, "prima_registrar_archivo", proyecto="Proyecto Uno", ruta=str(clip), declaracion_aceptada=True)
            check("video pesado: 'Excediste el limite' y como reducirlo", r.get("ok") is False and "Limit exceeded" in r.get("message", "") and "prima_reducir_video" in r.get("message", ""), r)
            r = await call(s, "prima_reducir_video", ruta=str(folder / "Proyecto Uno" / "video_hd.mp4"))
            reduced = Path(r.get("archivo", "")) if r.get("ok") else None
            check("prima_reducir_video con ffmpeg real", reduced and reduced.exists() and reduced.stat().st_size <= 1024 * 1024, r)
            if reduced:
                height = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=height", "-of", "csv=p=0", str(reduced)],
                                        capture_output=True, text=True, creationflags=NOWIN).stdout.strip()
                check("el video reducido es de 480p", height == "480", height)
                r = await call(s, "prima_registrar_archivo", proyecto="Proyecto Uno", ruta=str(reduced), declaracion_aceptada=True)
                check("el video reducido se registra", r.get("ok") and r.get("status") == "PROCESSED", r)

            r = await call(s, "prima_procesar_pendientes", declaracion_aceptada=True)
            check("la cola registra el video original grande? no: lo salta con aviso", r.get("ok") and any("video_hd.mp4" in x.get("filename", "") for x in r.get("skipped", [])), r)

            r = await call(s, "prima_estado_registros", proyecto="Proyecto Uno")
            regs = r.get("registros", [])
            check("prima_estado_registros lista los registros", r.get("ok") and len(regs) == 2 and all(x.get("evidence_id") for x in regs), regs)
            check("el estado no muestra tecnologia interna en evidence_status", all("OpenTimestamps" not in str(x.get("evidence_status")) for x in regs))

def main():
    admin = psycopg.connect(DSN, autocommit=True)
    admin.execute(f"CREATE SCHEMA {SCHEMA}")
    env = dict(os.environ, PROOF_ENV="development", PROOF_HOST="127.0.0.1", PROOF_PORT=str(PORT), PROOF_STORAGE_ROOT=str(TMP / "storage"),
               PROOF_PUBLIC_BASE_URL=BASE, PROOF_MCP_ENABLED="true", PROOF_NOTIFICATION_WAKE_URL="",
               PROOF_DATABASE_URL=f"postgresql+psycopg://prima:prima_dev@127.0.0.1:55432/prima_dev?options=-csearch_path%3D{SCHEMA}")
    server = subprocess.Popen([sys.executable, "-m", "src.api"], cwd=str(ROOT), env=env, creationflags=NOWIN,
                              stdout=open(TMP / "server.log", "w"), stderr=subprocess.STDOUT)
    try:
        if not wait_health():
            print("El servidor no arranco; log:\n", (TMP / "server.log").read_text(errors="replace")[-2000:])
            return 2
        asyncio.run(flow())
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except Exception:
            server.kill()
        admin.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        admin.close()
        shutil.rmtree(TMP, ignore_errors=True)
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)} de {len(results)} comprobaciones OK")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
