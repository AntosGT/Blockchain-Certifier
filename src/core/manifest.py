import json
import hashlib
from datetime import datetime, timezone
from typing import List, Dict, Any, Tuple

def format_iso8601_utc(dt: datetime = None) -> str:
    """Formats a datetime as strict ISO-8601 UTC with milliseconds and the Z suffix."""
    if dt is None:
        dt = datetime.now(timezone.utc)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

def canonicalize_json(data: Dict[str, Any]) -> bytes:
    """
    Deterministic JSON canonicalization following RFC 8785 (JCS):
    - UTF-8 without BOM.
    - Strict alphabetical ordering of Unicode keys (sort_keys=True).
    - No extra whitespace (separators=(',', ':')).
    """
    json_str = json.dumps(
        data,
        sort_keys=True,
        separators=(',', ':'),
        ensure_ascii=False
    )
    return json_str.encode('utf-8')

def build_manifest_dict(
    evidence_id: str,
    title: str,
    category: str,
    owner_name: str,
    owner_email: str,
    owner_country: str,
    files_metadata: List[Dict[str, Any]],
    declaration_text: str = "Declaro que presento este contenido para generar evidencia tecnica verificable de su existencia e integridad.",
    declaration_version: str = "1.0",
    created_at_iso: str = None
) -> Dict[str, Any]:
    """Builds the full manifest structure before canonicalizing it."""
    if created_at_iso is None:
        created_at_iso = format_iso8601_utc()

    manifest = {
        "schema_version": "1.0",
        "system_version": "PROOF-MVP-1.0",
        "evidence_id": evidence_id,
        "created_at": created_at_iso,
        "title": title,
        "category": category,
        "hash_algorithm": "SHA-256",
        "owner": {
            "name": owner_name,
            "email": owner_email,
            "country": owner_country
        },
        "declaration": {
            "version": declaration_version,
            "text": declaration_text
        },
        "files": sorted(files_metadata, key=lambda x: x["filename"])
    }
    return manifest

def generate_manifest_bundle(
    evidence_id: str,
    title: str,
    category: str,
    owner_name: str,
    owner_email: str,
    owner_country: str,
    files_metadata: List[Dict[str, Any]],
    declaration_text: str = None,
    declaration_version: str = None
) -> Tuple[Dict[str, Any], bytes, str]:
    """
    Generates the manifest dictionary, the RFC 8785 canonical bytes
    and the corresponding hexadecimal SHA-256 hash.
    """
    declaration_args = {}
    if declaration_text is not None:
        declaration_args["declaration_text"] = declaration_text
    if declaration_version is not None:
        declaration_args["declaration_version"] = declaration_version

    manifest_dict = build_manifest_dict(
        evidence_id=evidence_id,
        title=title,
        category=category,
        owner_name=owner_name,
        owner_email=owner_email,
        owner_country=owner_country,
        files_metadata=files_metadata,
        **declaration_args
    )
    
    canonical_bytes = canonicalize_json(manifest_dict)
    manifest_hash = hashlib.sha256(canonical_bytes).hexdigest()
    
    return manifest_dict, canonical_bytes, manifest_hash
