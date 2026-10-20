#!/usr/bin/env python3
"""Independent verifier for PRIMA evidence packages.

Checks a certificate package (the ZIP received by email, or its unzipped folder) without contacting PRIMA:

1. The evidence manifest is in canonical form (sorted keys, no spaces, UTF-8).
2. The SHA-256 of the manifest matches the evidence hash file.
3. The timestamp proof seals that same hash and, if present, its Bitcoin block attestation.
   With --online, the attestation is checked against the real block header from public block explorers.
4. Each original file given matches the SHA-256 registered in the manifest.

Steps 1, 2 and 4 use only the Python standard library. Step 3 needs the open-source package
`opentimestamps-client` (pip install opentimestamps-client); without it, the proof can be checked with its `ots verify`.

Usage:
    python prima_verify.py <package.zip | folder> [original files...] [--online] [--json]

Exit code 0 means verified.
"""
import argparse
import hashlib
import json
import sys
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

# Package layouts, newest first: (manifest, hash file, timestamp proof)
LAYOUTS = [
    ("proof/evidence.json", "proof/evidence.sha256", "proof/proof.ots"),
    ("prueba/evidencia.json", "prueba/evidencia.sha256", "prueba/prueba.ots"),
    ("manifest.json", "manifest.sha256", "manifest.ots"),
]
HEADER_EXPLORERS = ["https://blockstream.info/api", "https://mempool.space/api"]
USER_AGENT = "prima-verify/1.0"


def canonical_json(data: Any) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_package(source: Path) -> Dict[str, Optional[bytes]]:
    """Reads manifest, hash file and proof from a ZIP or a folder. Also returns originals included in the ZIP."""
    if source.is_file() and zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as z:
            names = set(z.namelist())
            get = lambda n: z.read(n) if n in names else None
            for manifest, hash_file, ots in LAYOUTS:
                if manifest in names:
                    inside = {n.split("/", 1)[1]: z.read(n) for n in names
                              if n.startswith(("originals/", "originales/")) and not n.endswith("/")}
                    return {"manifest": get(manifest), "hash": get(hash_file), "ots": get(ots), "inside": inside}
    elif source.is_dir():
        for manifest, hash_file, ots in LAYOUTS:
            if (source / manifest).is_file():
                get = lambda n: (source / n).read_bytes() if (source / n).is_file() else None
                return {"manifest": get(manifest), "hash": get(hash_file), "ots": get(ots), "inside": {}}
    raise ValueError(f"No PRIMA evidence manifest found in {source}.")


def fetch_block_header(height: int, timeout: int = 15) -> bytes:
    last_error: Optional[Exception] = None
    for base in HEADER_EXPLORERS:
        try:
            req = lambda url: urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req(f"{base}/block-height/{height}"), timeout=timeout) as r:
                block_hash = r.read().decode().strip()
            with urllib.request.urlopen(req(f"{base}/block/{block_hash}/header"), timeout=timeout) as r:
                return bytes.fromhex(r.read().decode().strip())
        except Exception as e:
            last_error = e
    raise RuntimeError(f"Could not get the header of block {height}: {last_error}")


def check_timestamp(ots_bytes: bytes, manifest_hash: str, online: bool, header_fetcher=fetch_block_header) -> Dict[str, Any]:
    result: Dict[str, Any] = {"checked": False, "seals_manifest": False, "bitcoin_block": None, "bitcoin_verified": None,
                              "error": None}
    try:
        from opentimestamps.core.notary import BitcoinBlockHeaderAttestation
        from opentimestamps.core.serialize import BytesDeserializationContext
        from opentimestamps.core.timestamp import DetachedTimestampFile
    except ImportError:
        result["error"] = "Install opentimestamps-client to check the timestamp proof (pip install opentimestamps-client)."
        return result
    try:
        detached = DetachedTimestampFile.deserialize(BytesDeserializationContext(ots_bytes))
    except Exception as e:
        result["error"] = f"The timestamp proof is not valid: {type(e).__name__}: {e}"
        return result
    result["checked"] = True
    result["seals_manifest"] = detached.file_digest.hex() == manifest_hash
    attestations = sorted(((a.height, msg, a) for msg, a in detached.timestamp.all_attestations()
                           if isinstance(a, BitcoinBlockHeaderAttestation)), key=lambda t: t[0])
    if not attestations:
        return result
    height, msg, att = attestations[0]
    result["bitcoin_block"] = height
    if online:
        try:
            from bitcoin.core import CBlockHeader
            att.verify_against_blockheader(msg, CBlockHeader.deserialize(header_fetcher(height)))
            result["bitcoin_verified"] = True
        except Exception as e:
            result["bitcoin_verified"] = False
            result["error"] = f"Bitcoin check failed: {type(e).__name__}: {e}"
    return result


def verify(source: Path, originals: List[Path], online: bool = False, header_fetcher=fetch_block_header) -> Dict[str, Any]:
    report: Dict[str, Any] = {"verified": False, "evidence_id": None, "manifest_sha256": None, "canonical": False,
                              "hash_file_match": False, "timestamp": None, "files": [], "problems": []}
    pkg = read_package(source)
    raw = pkg["manifest"]
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except Exception as e:
        report["problems"].append(f"The manifest is not valid JSON: {e}")
        return report
    report["evidence_id"] = manifest.get("evidence_id")
    report["canonical"] = canonical_json(manifest) == raw
    if not report["canonical"]:
        report["problems"].append("The manifest is not in canonical form: it was modified after sealing.")
    manifest_hash = sha256_bytes(raw)
    report["manifest_sha256"] = manifest_hash
    if pkg["hash"] is None:
        report["problems"].append("The evidence hash file is missing.")
    else:
        expected = pkg["hash"].decode("utf-8").split()[0].lower() if pkg["hash"].strip() else ""
        report["hash_file_match"] = expected == manifest_hash
        if not report["hash_file_match"]:
            report["problems"].append("The manifest SHA-256 does not match the evidence hash file.")
    if pkg["ots"] is None:
        report["problems"].append("The timestamp proof is missing.")
    else:
        ts = check_timestamp(pkg["ots"], manifest_hash, online, header_fetcher)
        report["timestamp"] = ts
        if ts["error"]:
            report["problems"].append(ts["error"])
        if ts["checked"] and not ts["seals_manifest"]:
            report["problems"].append("The timestamp proof seals a different hash than this manifest.")
        if ts["checked"] and ts["bitcoin_block"] is None:
            report["problems"].append("The timestamp proof has no Bitcoin attestation yet.")

    registered = {f["sha256"]: f["filename"] for f in manifest.get("files", [])}
    candidates = [(p.name, sha256_file(p)) for p in originals] + [(n, sha256_bytes(b)) for n, b in pkg["inside"].items()]
    for name, digest in candidates:
        match = registered.get(digest)
        report["files"].append({"file": name, "sha256": digest, "registered_as": match, "match": match is not None})
        if match is None:
            report["problems"].append(f"{name} does not match any file registered in this evidence.")

    ts = report["timestamp"] or {}
    report["verified"] = (report["canonical"] and report["hash_file_match"] and ts.get("seals_manifest") is True
                          and ts.get("bitcoin_block") is not None and ts.get("bitcoin_verified") is not False
                          and not report["problems"])
    return report


def print_report(r: Dict[str, Any]) -> None:
    yes = lambda b: "yes" if b else "no"
    ts = r["timestamp"] or {}
    print(f"Evidence: {r['evidence_id']}")
    print(f"Manifest SHA-256: {r['manifest_sha256']}")
    print(f"Manifest unchanged since sealing: {yes(r['canonical'])}")
    print(f"Matches the evidence hash file: {yes(r['hash_file_match'])}")
    print(f"Timestamp proof seals this manifest: {yes(ts.get('seals_manifest'))}")
    print(f"Bitcoin block: {ts.get('bitcoin_block') or 'none'}"
          + ("" if ts.get("bitcoin_verified") is None else f" (checked against the real block: {yes(ts['bitcoin_verified'])})"))
    for f in r["files"]:
        print(f"File {f['file']}: " + (f"matches {f['registered_as']}" if f["match"] else "DOES NOT MATCH"))
    for p in r["problems"]:
        print(f"Problem: {p}")
    print("RESULT: VERIFIED" if r["verified"] else "RESULT: NOT VERIFIED")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Verify a PRIMA evidence package without contacting PRIMA.")
    parser.add_argument("package", help="Certificate ZIP or its unzipped folder.")
    parser.add_argument("originals", nargs="*", help="Original files to check against the evidence.")
    parser.add_argument("--online", action="store_true", help="Check the Bitcoin attestation against the real block header.")
    parser.add_argument("--json", action="store_true", help="Print the report as JSON.")
    args = parser.parse_args(argv)
    try:
        report = verify(Path(args.package), [Path(p) for p in args.originals], online=args.online)
    except (ValueError, OSError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print_report(report)
    return 0 if report["verified"] else 1


if __name__ == "__main__":
    sys.exit(main())
