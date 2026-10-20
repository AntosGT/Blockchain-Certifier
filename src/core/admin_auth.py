import hashlib
import hmac
import os

# Standard library scrypt: each password has its own salt and the cost is stored in the hash itself.
_N, _R, _P, _DKLEN = 2 ** 14, 8, 1, 32
MIN_PASSWORD_LENGTH = 12

def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${digest.hex()}"

def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, digest_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        candidate = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt_hex),
                                   n=int(n), r=int(r), p=int(p), dklen=len(bytes.fromhex(digest_hex)))
        return hmac.compare_digest(candidate, bytes.fromhex(digest_hex))
    except Exception:
        return False

# Dummy hash: if the user does not exist it is verified against this one anyway, so the response time does not reveal which emails are admins.
DUMMY_HASH = hash_password("relleno-para-igualar-tiempos")
