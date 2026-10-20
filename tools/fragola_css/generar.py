"""
Regenerates src/api/static/fragola/fragola.css: the Tailwind classes of the Fragola Agency header and footer, compiled and scoped to
.fa-scope so the rest of the portal is untouched. It reads the HTML of the `_fa_header_*` and `_fa_footer_*` template partials (used by the landing and the form), so the
CSS always comes from what the templates use. Requires Node and network (downloads tailwindcss 3.4.17 with npx).

Usage, from the prima-certified folder:  python tools/fragola_css/generar.py [output_file]
Scale: every rem measure is multiplied by FACTOR (set to 0.8 on 2026-10-08) and the 11px text becomes 8.8px.
"""
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL = Path(__file__).resolve().parent
TEMPLATES = ROOT / "src" / "api" / "templates"
OUTPUT = ROOT / "src" / "api" / "static" / "fragola" / "fragola.css"
TAILWIND = "tailwindcss@3.4.17"
FACTOR = 0.8

BASE = (
    "/* Reglas base de Tailwind (preflight) acotadas al encabezado y pie de Fragola, para no afectar el resto del portal */\n"
    ".fa-scope{font-family:'IBM Plex Mono',monospace;color:#fff;line-height:1.5;-webkit-text-size-adjust:100%;tab-size:4}\n"
    ".fa-scope *,.fa-scope ::before,.fa-scope ::after{box-sizing:border-box;border-width:0;border-style:solid;border-color:#e5e7eb}\n"
    ".fa-scope a{color:inherit;text-decoration:inherit}\n"
    ".fa-scope h4,.fa-scope p,.fa-scope ul,.fa-scope li{margin:0;font-size:inherit;font-weight:inherit}\n"
    ".fa-scope ul{list-style:none;padding:0}\n"
    ".fa-scope img{display:block;vertical-align:middle;max-width:100%;height:auto}\n"
    ".fa-scope footer{padding:0;font-size:inherit;color:inherit}\n"
)


def partials() -> str:
    """HTML of the Fragola header and footer, in both languages (the `_fa_header_*` and `_fa_footer_*` partials)."""
    files = sorted(TEMPLATES.glob("_fa_header_*.html")) + sorted(TEMPLATES.glob("_fa_footer_*.html"))
    if len(files) != 4:
        sys.exit(f"Se esperaban 4 parciales de Fragola y hay {len(files)}")
    return "\n".join(f.read_text(encoding="utf-8") for f in files)


def main() -> None:
    npx = shutil.which("npx")
    if not npx:
        sys.exit("No se encontró npx (Node).")
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        (work / "markup.html").write_text(partials(), encoding="utf-8")
        shutil.copy(TOOL / "tailwind.config.js", work / "tailwind.config.js")
        shutil.copy(TOOL / "input.css", work / "input.css")
        subprocess.run([npx, "--yes", TAILWIND, "-c", "tailwind.config.js", "-i", "input.css", "-o", "out.css", "--minify"],
                       cwd=work, check=True)
        css = (work / "out.css").read_text(encoding="utf-8")
    css = re.sub(r"(-?\d*\.?\d+)rem", lambda m: ("%.4f" % (float(m.group(1)) * FACTOR)).rstrip("0").rstrip(".") + "rem", css)
    css = css.replace("11px", "8.8px")
    fonts = "".join(
        '@font-face{font-family:"IBM Plex Mono";font-style:normal;font-weight:%d;font-display:swap;'
        'src:url("/static/fonts/ibm-plex-mono-%d-latin.woff2") format("woff2")}\n' % (w, w) for w in (400, 500, 600, 700))
    header = "/* Generado por tools/fragola_css/generar.py desde las clases de Tailwind del encabezado y pie de fragola.agency (tailwindcss 3.4.17) */\n"
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else OUTPUT  # with an argument it writes there (to compare without overwriting the current one)
    target.write_text(header + fonts + BASE + css + "\n", encoding="utf-8", newline="\n")
    print("ok", target, len(css), "bytes de CSS compilado")


if __name__ == "__main__":
    main()
