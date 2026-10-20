import hashlib
import os
import shutil
import tempfile
import unittest
import unittest.mock
from bitcoin.core import CBlockHeader
from opentimestamps.core.notary import BitcoinBlockHeaderAttestation, PendingAttestation
from opentimestamps.core.op import OpSHA256
from opentimestamps.core.timestamp import DetachedTimestampFile, Timestamp
from src.core.manifest import generate_manifest_bundle
from src.core.ots import inspect_ots, save_proof_files, verify_bitcoin_attestation, _serialize
from src.core.verifier import verify_proof_bundle

def build_ots(digest: bytes, attestation) -> bytes:
    ts = Timestamp(digest)
    ts.attestations.add(attestation)
    return _serialize(DetachedTimestampFile(OpSHA256(), ts))

class TestVerifier(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.original_dir = os.path.join(self.tmp, "original")
        os.makedirs(self.original_dir)
        content = b"contenido original del proyecto"
        with open(os.path.join(self.original_dir, "doc.txt"), "wb") as f:
            f.write(content)
        files = [{"filename": "doc.txt", "mime": "text/plain", "sha256": hashlib.sha256(content).hexdigest(),
                  "size": len(content), "salt_hex": "e" * 64}]
        self.manifest, self.canonical, self.manifest_hash = generate_manifest_bundle(
            "EV-20261004-AAAA0001", "Doc", "otro", "Owner", "owner@example.com", "AR", files)
        self.proof_dir = os.path.join(self.tmp, "proof")

    def _save(self, ots_bytes):
        save_proof_files(self.proof_dir, "EV-1", self.manifest, self.canonical, self.manifest_hash, ots_bytes)

    def test_valid_pending_proof_is_verified(self):
        self._save(build_ots(bytes.fromhex(self.manifest_hash), PendingAttestation("https://alice.btc.calendar.opentimestamps.org")))
        report = verify_proof_bundle(self.proof_dir, self.original_dir)
        self.assertTrue(report["verified"], report["errors"])
        self.assertEqual((report["ots_valid"], report["ots_digest_match"], report["ots_status"]), (True, True, "pending"))

    def test_fragment_that_is_not_a_real_ots_file_is_rejected(self):
        """Regression: the original spike stored the raw calendar fragment and took it as valid."""
        self._save(bytes.fromhex("f0089ae2374809f1cc9e08f0107d2b498058260229a40910790b5196c008f020"))
        report = verify_proof_bundle(self.proof_dir, self.original_dir)
        self.assertFalse(report["verified"])
        self.assertFalse(report["ots_valid"])

    def test_ots_sealing_a_different_hash_is_rejected(self):
        self._save(build_ots(b"\x01" * 32, PendingAttestation("https://alice.btc.calendar.opentimestamps.org")))
        report = verify_proof_bundle(self.proof_dir, self.original_dir)
        self.assertFalse(report["verified"])
        self.assertFalse(report["ots_digest_match"])

    def test_modified_original_file_is_detected(self):
        self._save(build_ots(bytes.fromhex(self.manifest_hash), PendingAttestation("https://alice.btc.calendar.opentimestamps.org")))
        with open(os.path.join(self.original_dir, "doc.txt"), "wb") as f:
            f.write(b"contenido alterado")
        self.assertFalse(verify_proof_bundle(self.proof_dir, self.original_dir)["verified"])

    def test_bitcoin_attestation_checked_against_block_header(self):
        header = CBlockHeader(nVersion=1, hashPrevBlock=b"\x00" * 32, hashMerkleRoot=bytes.fromhex(self.manifest_hash),
                              nTime=1_700_000_000, nBits=0x1d00ffff, nNonce=7)
        ots = build_ots(bytes.fromhex(self.manifest_hash), BitcoinBlockHeaderAttestation(123))
        info = inspect_ots(ots)
        self.assertEqual((info["status"], info["bitcoin_block_height"]), ("bitcoin", 123))
        result = verify_bitcoin_attestation(ots, header_fetcher=lambda h: header.serialize())
        self.assertEqual((result["verified"], result["height"], result["block_time"]), (True, 123, 1_700_000_000))

        self._save(ots)
        with unittest.mock.patch("src.core.verifier.verify_bitcoin_attestation",
                                 lambda b: verify_bitcoin_attestation(b, header_fetcher=lambda h: header.serialize())):
            report = verify_proof_bundle(self.proof_dir, self.original_dir, verify_bitcoin=True)
        self.assertTrue(report["bitcoin_verified"])
        self.assertTrue(report["verified"], report["errors"])

if __name__ == "__main__":
    unittest.main()
