import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from fragola_mcp.verify.prima_verify import main, verify
from src.core.ots import inspect_ots

ROOT = Path(__file__).resolve().parents[1]
SPIKE = ROOT / "spike_output" / "proof"
# This demo is a real OpenTimestamps proof submitted from scratch with non-personal data
# (no name/email tied to anyone), so it can ship with the public repo. It starts "pending"
# and self-upgrades to a Bitcoin attestation once a block confirms it; the tests that need
# a confirmed block are skipped until then (see `prima_procesar_pendientes` / `ots_worker.py`
# for how the real system performs that upgrade).
_ots_bytes = (SPIKE / "manifest.ots").read_bytes() if (SPIKE / "manifest.ots").exists() else b""
_BITCOIN_CONFIRMED = bool(_ots_bytes) and inspect_ots(_ots_bytes)["status"] == "bitcoin"


class TestPrimaVerify(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def make_zip(self, manifest=None, layout=("proof/evidence.json", "proof/evidence.sha256", "proof/proof.ots"), originals=None):
        path = self.tmp / "certificate.zip"
        with zipfile.ZipFile(path, "w") as z:
            z.writestr(layout[0], manifest if manifest is not None else (SPIKE / "manifest.json").read_bytes())
            z.writestr(layout[1], (SPIKE / "manifest.sha256").read_bytes())
            z.writestr(layout[2], (SPIKE / "manifest.ots").read_bytes())
            for name, data in (originals or {}).items():
                z.writestr(f"originals/{name}", data)
        return path

    def test_the_demo_proof_is_structurally_valid(self):
        r = verify(SPIKE, [])
        self.assertTrue(r["canonical"])
        self.assertTrue(r["hash_file_match"])
        self.assertTrue(r["timestamp"]["seals_manifest"])
        self.assertEqual(r["evidence_id"], "EV-20261020-PUBLICDEMO")

    @unittest.skipUnless(_BITCOIN_CONFIRMED, "demo proof still pending a Bitcoin confirmation (run `ots upgrade` on manifest.ots)")
    def test_the_demo_is_verified_against_the_real_bitcoin_block(self):
        r = verify(SPIKE, [], online=True)
        self.assertTrue(r["verified"], r["problems"])

    def test_the_demo_original_is_still_the_registered_file(self):
        original = ROOT / "spike_output" / "original" / "demo.txt"
        r = verify(SPIKE, [original])
        self.assertTrue(r["files"][0]["match"], "el original del demo fue modificado: no se edita, es la evidencia")
        self.assertTrue(r["canonical"] and r["hash_file_match"])

    def test_every_package_layout_is_read(self):
        for layout in (("proof/evidence.json", "proof/evidence.sha256", "proof/proof.ots"),
                       ("prueba/evidencia.json", "prueba/evidencia.sha256", "prueba/prueba.ots")):
            r = verify(self.make_zip(layout=layout), [])
            self.assertTrue(r["canonical"] and r["hash_file_match"] and r["timestamp"]["seals_manifest"], (layout, r["problems"]))

    def test_a_modified_manifest_is_not_verified(self):
        manifest = json.loads((SPIKE / "manifest.json").read_text(encoding="utf-8"))
        manifest["title"] = "Otro título"
        raw = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        r = verify(self.make_zip(manifest=raw), [])
        self.assertFalse(r["verified"])
        self.assertFalse(r["hash_file_match"])

    def test_a_reformatted_manifest_is_not_canonical(self):
        manifest = json.loads((SPIKE / "manifest.json").read_text(encoding="utf-8"))
        r = verify(self.make_zip(manifest=json.dumps(manifest, indent=2).encode("utf-8")), [])
        self.assertFalse(r["canonical"])
        self.assertFalse(r["verified"])

    def test_originals_are_matched_by_fingerprint(self):
        other = self.tmp / "otro.md"
        other.write_bytes(b"no registrado")
        r = verify(SPIKE, [other])
        self.assertEqual(r["files"][0]["match"], False)
        self.assertFalse(r["verified"])
        r = verify(self.make_zip(originals={"x.md": b"no registrado"}), [])
        self.assertFalse(r["files"][0]["match"])

    def test_a_fake_proof_is_not_verified(self):
        path = self.tmp / "fake.zip"
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("proof/evidence.json", (SPIKE / "manifest.json").read_bytes())
            z.writestr("proof/evidence.sha256", (SPIKE / "manifest.sha256").read_bytes())
            z.writestr("proof/proof.ots", b"OTS-ANCHORED")
        self.assertFalse(verify(path, [])["verified"])

    def test_the_command_line_exit_code_and_json_output(self):
        expected = 0 if _BITCOIN_CONFIRMED else 1
        self.assertEqual(main([str(SPIKE), "--json"]), expected)
        self.assertEqual(main([str(self.tmp)]), 2)


if __name__ == "__main__":
    unittest.main()
