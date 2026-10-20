import hashlib
import os
from typing import Tuple

def compute_file_sha256(file_path: str) -> Tuple[str, int]:
    """
    Computes the SHA-256 hash of a file by streaming it in blocks
    and returns the hexadecimal hash string together with the size in bytes.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"El archivo especificado no existe: {file_path}")

    sha256_hash = hashlib.sha256()
    total_size = 0

    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha256_hash.update(chunk)
            total_size += len(chunk)

    return sha256_hash.hexdigest(), total_size

def generate_salt_hex(length_bytes: int = 32) -> str:
    """Generates a cryptographically secure random salt in hex format."""
    return os.urandom(length_bytes).hex()
