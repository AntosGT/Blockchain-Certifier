import os
import json
import hashlib
from typing import Dict, Any, List, Tuple
from src.core.hasher import compute_file_sha256
from src.core.manifest import canonicalize_json
from src.core.ots import inspect_ots, verify_bitcoin_attestation

def verify_proof_bundle(proof_dir: str, original_files_dir: str = None, verify_bitcoin: bool = False) -> Dict[str, Any]:
    """
    Independent verification engine:
    Validates the integrity of the manifest, its SHA-256 hash, the canonical form
    and the cryptographic match of each registered file.
    """
    manifest_path = os.path.join(proof_dir, "manifest.json")
    hash_path = os.path.join(proof_dir, "manifest.sha256")
    ots_path = os.path.join(proof_dir, "manifest.ots")

    report = {
        "verified": False,
        "manifest_found": os.path.exists(manifest_path),
        "hash_file_found": os.path.exists(hash_path),
        "ots_found": os.path.exists(ots_path),
        "canonical_match": False,
        "sha256_match": False,
        "ots_valid": False,
        "ots_digest_match": False,
        "ots_status": None,
        "bitcoin_block_height": None,
        "bitcoin_verified": False,
        "files_verification": [],
        "errors": []
    }

    if not report["manifest_found"]:
        report["errors"].append("The manifest.json file is missing")
        return report

    # 1. Read the bytes of manifest.json
    with open(manifest_path, "rb") as f:
        raw_manifest_bytes = f.read()

    try:
        manifest_dict = json.loads(raw_manifest_bytes.decode("utf-8"))
    except Exception as e:
        report["errors"].append(f"Error al parsear manifest.json: {e}")
        return report

    # 2. Check the deterministic RFC 8785 canonicalization
    recreated_canonical_bytes = canonicalize_json(manifest_dict)
    if raw_manifest_bytes == recreated_canonical_bytes:
        report["canonical_match"] = True
    else:
        report["errors"].append("WARNING: manifest.json does not match the RFC 8785 canonical form byte for byte.")

    # 3. Compute the manifest SHA-256
    calculated_manifest_hash = hashlib.sha256(raw_manifest_bytes).hexdigest()
    report["calculated_manifest_hash"] = calculated_manifest_hash

    # 4. Check against manifest.sha256 if present
    if report["hash_file_found"]:
        with open(hash_path, "r", encoding="utf-8") as f:
            expected_hash_line = f.read().strip()
        expected_hash = expected_hash_line.split()[0] if expected_hash_line else ""
        
        if calculated_manifest_hash.lower() == expected_hash.lower():
            report["sha256_match"] = True
        else:
            report["errors"].append(f"Discrepancia en manifest.sha256: Esperado {expected_hash}, obtenido {calculated_manifest_hash}")

    # 4b. Check that the .ots is a valid file, seals THIS manifest, and its status in Bitcoin
    if report["ots_found"]:
        with open(ots_path, "rb") as f:
            ots_bytes = f.read()
        info = inspect_ots(ots_bytes)
        report["ots_valid"] = info["valid"]
        report["ots_status"] = info["status"]
        report["bitcoin_block_height"] = info["bitcoin_block_height"]
        if not info["valid"]:
            report["errors"].append(f"manifest.ots no es un archivo OpenTimestamps válido: {info['error']}")
        elif info["digest_hex"] == calculated_manifest_hash.lower():
            report["ots_digest_match"] = True
        else:
            report["errors"].append("manifest.ots seals a different hash than manifest.json.")

        if verify_bitcoin and report["ots_valid"] and info["status"] == "bitcoin":
            try:
                report["bitcoin_verified"] = verify_bitcoin_attestation(ots_bytes)["verified"]
            except Exception as e:
                report["errors"].append(f"No se pudo verificar el anclaje en Bitcoin: {e}")

    # 5. Check the original files if a directory was given
    if original_files_dir and os.path.exists(original_files_dir):
        files_list = manifest_dict.get("files", [])
        for file_info in files_list:
            fname = file_info.get("filename")
            expected_file_hash = file_info.get("sha256")
            target_path = os.path.join(original_files_dir, fname)

            file_report = {
                "filename": fname,
                "expected_sha256": expected_file_hash,
                "found": os.path.exists(target_path),
                "match": False
            }

            if file_report["found"]:
                actual_hash, _ = compute_file_sha256(target_path)
                file_report["actual_sha256"] = actual_hash
                if actual_hash.lower() == expected_file_hash.lower():
                    file_report["match"] = True
                else:
                    report["errors"].append(f"El archivo '{fname}' ha sido modificado o alterado.")
            else:
                report["errors"].append(f"Archivo original no encontrado en el directorio: {fname}")

            report["files_verification"].append(file_report)

    # 6. Evaluate the overall result
    if report["manifest_found"] and report["ots_found"] and report["ots_valid"] and report["ots_digest_match"] and (not report["errors"]):
        report["verified"] = True

    return report
