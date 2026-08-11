"""
security.py
-----------
Handles key derivation via PBKDF2 (Argon2 / PBKDF2-HMAC-SHA256) and PIN hashing.
"""

import os
import hashlib
import binascii
import argon2

SALT_FILE = "sera.salt"
PBKDF2_ITERATIONS = 480_000

def generate_and_save_salt(salt_path: str):
    salt = os.urandom(16)
    with open(salt_path, "wb") as f:
        f.write(salt)

def load_salt(salt_path: str) -> bytes:
    with open(salt_path, "rb") as f:
        return f.read()

def derive_key_hex(master_password: str, salt: bytes) -> str:
    key = hashlib.pbkdf2_hmac(
        'sha256',
        master_password.encode('utf-8'),
        salt,
        PBKDF2_ITERATIONS,
        dklen=32
    )
    return binascii.hexlify(key).decode('utf-8')

def hash_admin_pin(pin: str) -> str:
    ph = argon2.PasswordHasher()
    return ph.hash(pin)

def verify_admin_pin(pin: str, hashed: str) -> bool:
    ph = argon2.PasswordHasher()
    try:
        return ph.verify(hashed, pin)
    except Exception:
        return False