"""Create the signed Chrome/Edge CRX and align native messaging with its ID."""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
import struct
import zipfile
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


ROOT = Path(__file__).resolve().parent.parent
EXTENSION_DIR = ROOT / "sera_extension"
NATIVE_MANIFEST = ROOT / "native_host" / "com.amanassociates.sera.json"
KEY_PATH = ROOT / "build_tools" / "sera_extension.pem"
OUTPUT_DIR = ROOT / "package_assets" / "extension"


def _varint(value: int) -> bytes:
    data = bytearray()
    while value > 127:
        data.append((value & 127) | 128)
        value >>= 7
    data.append(value)
    return bytes(data)


def _field(field_number: int, value: bytes) -> bytes:
    return _varint((field_number << 3) | 2) + _varint(len(value)) + value


def extension_id(public_key_der: bytes) -> str:
    # Chromium maps the first 16 bytes of SHA-256 to a-p, one nibble per letter.
    digest = hashlib.sha256(public_key_der).digest()[:16]
    return "".join(chr(ord("a") + nibble) for byte in digest for nibble in (byte >> 4, byte & 15))


def get_key() -> rsa.RSAPrivateKey:
    if KEY_PATH.exists():
        return serialization.load_pem_private_key(KEY_PATH.read_bytes(), password=None)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    KEY_PATH.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return key


def create_zip(destination: Path) -> None:
    excluded = {".git", "__pycache__", ".DS_Store"}
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in EXTENSION_DIR.rglob("*"):
            if path.is_file() and not any(part in excluded for part in path.parts):
                archive.write(path, path.relative_to(EXTENSION_DIR).as_posix())


def create_crx(zip_bytes: bytes, key: rsa.RSAPrivateKey, public_key_der: bytes) -> bytes:
    crx_id = hashlib.sha256(public_key_der).digest()[:16]
    signed_header_data = _field(1, crx_id)  # SignedData.crx_id
    signed_data = b"CRX3 SignedData\x00" + struct.pack("<I", len(signed_header_data)) + signed_header_data + zip_bytes
    signature = key.sign(signed_data, padding.PKCS1v15(), hashes.SHA256())
    proof = _field(1, public_key_der) + _field(2, signature)  # AsymmetricKeyProof
    header = _field(2, proof) + _field(10000, signed_header_data)  # CrxFileHeader
    return b"Cr24" + struct.pack("<I", 3) + struct.pack("<I", len(header)) + header + zip_bytes


def build() -> tuple[str, str]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    key = get_key()
    public_key_der = key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    ext_id = extension_id(public_key_der)

    manifest_path = EXTENSION_DIR / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["key"] = base64.b64encode(public_key_der).decode("ascii")
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    native = json.loads(NATIVE_MANIFEST.read_text(encoding="utf-8"))
    native["allowed_origins"] = [f"chrome-extension://{ext_id}/"]
    NATIVE_MANIFEST.write_text(json.dumps(native, indent=2) + "\n", encoding="utf-8")

    zip_path = OUTPUT_DIR / "ProjectSeraCompanion.zip"
    create_zip(zip_path)
    crx_path = OUTPUT_DIR / "ProjectSeraCompanion.crx"
    crx_path.write_bytes(create_crx(zip_path.read_bytes(), key, public_key_der))
    zip_path.unlink()

    (OUTPUT_DIR / "extension_id.txt").write_text(ext_id + "\n", encoding="ascii")
    (OUTPUT_DIR / "extension_version.txt").write_text(str(manifest["version"]) + "\n", encoding="ascii")
    print(f"Built {crx_path}")
    print(f"Extension ID: {ext_id}")
    return ext_id, manifest["version"]


if __name__ == "__main__":
    build()
