import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = ROOT / "fragola_mcp" / "schema" / "prima-api.openapi.json"
ROUTE_RE = re.compile(r'@app\.(get|post|put|patch|delete)\("([^"]+)"')


def code_routes():
    """Routes declared in the server code, without starting the app."""
    routes = set()
    for path in (ROOT / "src" / "api").glob("*.py"):
        for method, route in ROUTE_RE.findall(path.read_text(encoding="utf-8")):
            routes.add((method, route))
    return routes


class TestOpenApiSpec(unittest.TestCase):
    def setUp(self):
        self.spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
        self.documented = {(m, p) for p, ops in self.spec["paths"].items() for m in ops}

    def test_every_documented_route_exists_in_the_code(self):
        self.assertEqual(self.documented - code_routes(), set())

    def test_no_internal_admin_or_browser_routes_are_documented(self):
        for _, path in self.documented:
            self.assertFalse(path.startswith(("/admin", "/internal")), path)
        # the web portal routes depend on Turnstile and the form: they are not for third parties
        for browser_only in ("/api/registro", "/api/formulario", "/api/config", "/api/i18n"):
            self.assertNotIn(browser_only, self.spec["paths"])

    def test_the_mcp_entry_is_fully_documented(self):
        mcp = {r for r in code_routes() if r[1].startswith("/api/mcp/")}
        self.assertTrue(mcp)
        self.assertEqual(mcp - self.documented, set())

    def test_references_resolve(self):
        raw = SPEC_PATH.read_text(encoding="utf-8")
        for ref in re.findall(r'"\$ref":\s*"#/([^"]+)"', raw):
            node = self.spec
            for part in ref.split("/"):
                self.assertIn(part, node, ref)
                node = node[part]

    def test_the_spec_is_valid_openapi_when_the_validator_is_installed(self):
        try:
            from openapi_spec_validator import validate
        except ImportError:
            self.skipTest("openapi-spec-validator no instalado")
        validate(self.spec)


if __name__ == "__main__":
    unittest.main()
