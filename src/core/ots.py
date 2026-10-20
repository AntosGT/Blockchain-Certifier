import os
import json
import urllib.request
from dataclasses import dataclass
from typing import Callable, Dict, Any, Optional

from opentimestamps.calendar import RemoteCalendar, CommitmentNotFoundError
from opentimestamps.core.notary import PendingAttestation, BitcoinBlockHeaderAttestation
from opentimestamps.core.op import OpAppend, OpSHA256
from opentimestamps.core.serialize import BytesDeserializationContext, BytesSerializationContext
from opentimestamps.core.timestamp import DetachedTimestampFile, Timestamp
from bitcoin.core import CBlockHeader

USER_AGENT = "PROOF-MVP/1.0 (Prima-Certified)"

# Public calendars the hash is sent to.
CALENDAR_URLS = [
    "https://a.pool.opentimestamps.org",
    "https://b.pool.opentimestamps.org",
    "https://a.pool.eternitywall.com",
    "https://ots.btc.catallaxy.com",
]

# Upgrade allowlist: the pools forward to these calendars, and those are the URIs left in the pending
# proof (same as the official client's list). Any other URI is ignored.
UPGRADE_WHITELIST = frozenset({
    "https://alice.btc.calendar.opentimestamps.org",
    "https://bob.btc.calendar.opentimestamps.org",
    "https://finney.calendar.eternitywall.com",
    "https://btc.calendar.catallaxy.com",
    "https://ots.btc.catallaxy.com",
    "https://a.pool.opentimestamps.org",
    "https://b.pool.opentimestamps.org",
    "https://a.pool.eternitywall.com",
})

# Block explorers used only to read the 80-byte header of a block and compare it with the proof.
HEADER_EXPLORERS = [
    "https://blockstream.info/api",
    "https://mempool.space/api",
]

def _serialize(detached: DetachedTimestampFile) -> bytes:
    ctx = BytesSerializationContext()
    detached.serialize(ctx)
    return ctx.getbytes()

def _deserialize(ots_bytes: bytes) -> DetachedTimestampFile:
    return DetachedTimestampFile.deserialize(BytesDeserializationContext(ots_bytes))

def submit_hash_to_opentimestamps(sha256_hex: str, min_calendars: int = 1, timeout: int = 10) -> bytes:
    """
    Timestamps the SHA-256 hash on the public calendars and returns a complete, valid `.ots` file
    (header + digest + pending proof), verifiable with the official `ots` client.
    """
    digest = bytes.fromhex(sha256_hex)
    if len(digest) != 32:
        raise ValueError("The SHA-256 digest must be exactly 32 bytes (64 hex characters).")

    file_timestamp = DetachedTimestampFile(OpSHA256(), Timestamp(digest))
    # Like the official client: the hash is sent with a random nonce, so the calendar never sees the real digest.
    nonce_stamp = file_timestamp.timestamp.ops.add(OpAppend(os.urandom(16)))
    merkle_tip = nonce_stamp.ops.add(OpSHA256())

    merged, last_error = 0, None
    for url in CALENDAR_URLS:
        try:
            calendar_stamp = RemoteCalendar(url, user_agent=USER_AGENT).submit(merkle_tip.msg, timeout=timeout)
            merkle_tip.merge(calendar_stamp)
            merged += 1
        except Exception as e:
            last_error = e
    if merged < min_calendars:
        raise RuntimeError(f"Error al enviar el hash a los calendarios de OpenTimestamps ({merged} de {min_calendars} requeridos): {last_error}")
    return _serialize(file_timestamp)

@dataclass
class UpgradeResult:
    ots_bytes: bytes
    changed: bool
    bitcoin_block_height: Optional[int]

def _bitcoin_heights(timestamp: Timestamp):
    return sorted(a.height for _, a in timestamp.all_attestations() if isinstance(a, BitcoinBlockHeaderAttestation))

def _pending_substamps(stamp: Timestamp):
    if stamp.attestations:
        if any(isinstance(a, PendingAttestation) for a in stamp.attestations):
            yield stamp
    for sub in stamp.ops.values():
        yield from _pending_substamps(sub)

def upgrade_ots(ots_bytes: bytes, timeout: int = 10) -> UpgradeResult:
    """Asks the calendars whether there is a Bitcoin anchor yet and completes the proof. Does not fail if there is none yet."""
    detached = _deserialize(ots_bytes)
    changed = False
    for sub in list(_pending_substamps(detached.timestamp)):
        for att in [a for a in sub.attestations if isinstance(a, PendingAttestation)]:
            if att.uri not in UPGRADE_WHITELIST:
                continue  # URI outside the allowlist: ignored
            try:
                upgraded = RemoteCalendar(att.uri, user_agent=USER_AGENT).get_timestamp(sub.msg, timeout=timeout)
            except (CommitmentNotFoundError, OSError):
                continue
            if _bitcoin_heights(upgraded):
                sub.merge(upgraded)
                changed = True
    heights = _bitcoin_heights(detached.timestamp)
    return UpgradeResult(
        ots_bytes=_serialize(detached) if changed else ots_bytes,
        changed=changed,
        bitcoin_block_height=heights[0] if heights else None,
    )

def inspect_ots(ots_bytes: bytes) -> Dict[str, Any]:
    """Validates the format of an .ots file and summarizes what it seals and its status (no network)."""
    try:
        detached = _deserialize(ots_bytes)
    except Exception as e:
        return {"valid": False, "error": f"{type(e).__name__}: {e}", "digest_hex": None, "status": None, "bitcoin_block_height": None}
    heights = _bitcoin_heights(detached.timestamp)
    return {
        "valid": True,
        "error": None,
        "digest_hex": detached.file_digest.hex(),
        "status": "bitcoin" if heights else "pending",
        "bitcoin_block_height": heights[0] if heights else None,
    }

def bitcoin_height_of(ots_bytes: bytes) -> Optional[int]:
    heights = _bitcoin_heights(_deserialize(ots_bytes).timestamp)
    return heights[0] if heights else None

def fetch_block_header(height: int, timeout: int = 15) -> bytes:
    """Downloads the 80-byte header of block `height` from a public explorer (with fallback)."""
    last_error = None
    for base in HEADER_EXPLORERS:
        try:
            with urllib.request.urlopen(urllib.request.Request(f"{base}/block-height/{height}", headers={"User-Agent": USER_AGENT}), timeout=timeout) as r:
                block_hash = r.read().decode().strip()
            with urllib.request.urlopen(urllib.request.Request(f"{base}/block/{block_hash}/header", headers={"User-Agent": USER_AGENT}), timeout=timeout) as r:
                return bytes.fromhex(r.read().decode().strip())
        except Exception as e:
            last_error = e
    raise RuntimeError(f"No se pudo obtener el header del bloque {height}: {last_error}")

def verify_bitcoin_attestation(ots_bytes: bytes, header_fetcher: Callable[[int], bytes] = fetch_block_header) -> Dict[str, Any]:
    """
    Verifies the proof against the real block header: the attested digest must be the block's Merkle root.
    Returns {"verified": bool, "height": int|None, "block_time": int|None}. Raises if the block cannot be queried.
    """
    detached = _deserialize(ots_bytes)
    for msg, att in detached.timestamp.all_attestations():
        if isinstance(att, BitcoinBlockHeaderAttestation):
            header = CBlockHeader.deserialize(header_fetcher(att.height))
            block_time = att.verify_against_blockheader(msg, header)  # raises VerificationError if it does not match
            return {"verified": True, "height": att.height, "block_time": block_time}
    return {"verified": False, "height": None, "block_time": None}

def save_proof_files(
    output_dir: str,
    evidence_id: str,
    manifest_dict: Dict[str, Any],
    canonical_bytes: bytes,
    manifest_hash: str,
    ots_bytes: bytes
) -> Dict[str, str]:
    """
    Saves the proof package files in the given directory:
    - manifest.json
    - manifest.sha256
    - manifest.ots
    """
    os.makedirs(output_dir, exist_ok=True)

    manifest_path = os.path.join(output_dir, "manifest.json")
    hash_path = os.path.join(output_dir, "manifest.sha256")
    ots_path = os.path.join(output_dir, "manifest.ots")

    with open(manifest_path, "wb") as f:
        f.write(canonical_bytes)

    with open(hash_path, "w", encoding="utf-8") as f:
        f.write(f"{manifest_hash}  manifest.json\n")

    with open(ots_path, "wb") as f:
        f.write(ots_bytes)

    return {
        "manifest_json": manifest_path,
        "manifest_sha256": hash_path,
        "manifest_ots": ots_path
    }
