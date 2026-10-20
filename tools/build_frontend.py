"""
Builds the static prima-certified frontend in frontend/dist/ to serve from a static host.
Both landings come from the Jinja templates (index.html, index_en.html), with the dynamic data as markers filled in by
frontend/static/portal.js; the form, the status page and the 404 come from frontend/src/. It also copies the static files (CSS, fonts,
images, scripts). It includes no secret: the public Turnstile key is passed through the environment at build time and is public by design.

Usage, from the prima-certified folder:
  python tools/build_frontend.py [--site-key CLAVE_PUBLICA_DE_TURNSTILE]
Without a key, the landings come out without the Turnstile challenge (useful for local tests).
"""
import argparse
import shutil
import sys
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.core.i18n import tr  # noqa: E402

TEMPLATES = ROOT / "src" / "api" / "templates"
SOURCES = ROOT / "frontend" / "src"
STATIC_FROM = [ROOT / "src" / "api" / "static", ROOT / "frontend" / "static"]
DIST = ROOT / "frontend" / "dist"

def cfg(name: str, default: str) -> Markup:
    return Markup(f'<span data-cfg="{name}">{default}</span>')

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site-key", default="")
    args = parser.parse_args()

    env = Environment(loader=FileSystemLoader([str(TEMPLATES), str(SOURCES)]), autoescape=select_autoescape(["html"]))
    env.globals["tr"] = tr

    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir(parents=True)

    landing = dict(values={}, error=None, sent_to=None, turnstile_site_key=args.site_key, form_token="pendiente",
                   max_files=cfg("max_files", "5"), link_hours=cfg("link_hours", "24"), payment_mode="bypass", price_usd=0.0)
    pages = {
        "index.html": ("index_en.html", dict(landing, lang="en")),
        "es.html": ("index.html", dict(landing, lang="es")),
        "formulario.html": ("formulario.src.html", dict(lang="en")),
        "estado.html": ("estado.src.html", dict(lang="en")),
        "404.html": ("404.src.html", dict(lang="en")),
    }
    for output, (template, context) in pages.items():
        (DIST / output).write_text(env.get_template(template).render(**context), encoding="utf-8", newline="\n")

    static = DIST / "static"
    for source in STATIC_FROM:
        # .md files are internal notes (context.md): they are not published
        shutil.copytree(source, static, dirs_exist_ok=True, ignore=shutil.ignore_patterns("*.md"))
    shutil.copy(DIST / "static" / "favicon.ico", DIST / "favicon.ico")
    print("ok:", ", ".join(sorted(p.name for p in DIST.glob("*.html"))), "| archivos:", sum(1 for _ in DIST.rglob("*") if _.is_file()))

if __name__ == "__main__":
    main()
