import json
import os
import unittest
import zipfile

from src.core.manifest import build_manifest_dict

try:
    import jsonschema
except ImportError:
    jsonschema = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_PATH = os.path.join(ROOT, "fragola_mcp", "schema", "prima-manifest-1.0.schema.json")


def _load_schema():
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        return json.load(f)


def _sample_manifest():
    return build_manifest_dict(
        evidence_id="PRIMA-20261018-ABCDEF12",
        title="Prueba",
        category="guion",
        owner_name="Test",
        owner_email="test@example.com",
        owner_country="AR",
        files_metadata=[{
            "filename": "a.txt", "mime": "text/plain", "sha256": "0" * 64, "size": 3,
            "salt_hex": "1" * 64, "storage_reference": "",
        }],
    )


@unittest.skipIf(jsonschema is None, "jsonschema no instalado")
class TestManifestSchema(unittest.TestCase):
    def setUp(self):
        self.schema = _load_schema()
        jsonschema.Draft202012Validator.check_schema(self.schema)
        self.validator = jsonschema.Draft202012Validator(self.schema)

    def test_manifest_generado(self):
        self.validator.validate(_sample_manifest())

    def test_manifest_del_spike(self):
        with open(os.path.join(ROOT, "spike_output", "proof", "manifest.json"), encoding="utf-8") as f:
            self.validator.validate(json.load(f))

    def test_manifest_del_paquete_de_ejemplo(self):
        with zipfile.ZipFile(os.path.join(ROOT, "ejemplos", "paquete_ejemplo.zip")) as z:
            name = next(n for n in z.namelist() if n.endswith(".json"))
            self.validator.validate(json.loads(z.read(name).decode("utf-8")))

    def test_rechaza_huella_invalida(self):
        m = _sample_manifest()
        m["files"][0]["sha256"] = "XYZ"
        with self.assertRaises(jsonschema.ValidationError):
            self.validator.validate(m)

    def test_rechaza_campo_desconocido(self):
        m = _sample_manifest()
        m["extra"] = 1
        with self.assertRaises(jsonschema.ValidationError):
            self.validator.validate(m)

    def test_esquema_sigue_al_codigo(self):
        self.assertEqual(set(self.schema["properties"]), set(_sample_manifest()))
        self.assertEqual(self.schema["properties"]["schema_version"]["const"], _sample_manifest()["schema_version"])


if __name__ == "__main__":
    unittest.main()
