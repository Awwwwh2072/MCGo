"""Cryptographic utilities: RSA key management, AES-256-GCM encryption, zlib compression."""

from __future__ import annotations

import base64
import os
import zlib
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class CryptoError(Exception):
    """Raised when a cryptographic operation fails."""


# --- RSA key management ---

def generate_rsa_keypair() -> tuple[bytes, bytes]:
    """Generate a 2048-bit RSA key pair.
    Returns (private_pem, public_pem) as bytes.
    """
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


def save_private_key(path: str, pem: bytes) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(pem)


def save_public_key(path: str, pem: bytes) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(pem)


def load_private_key(path: str) -> rsa.RSAPrivateKey:
    """Load an RSA private key from a PEM file."""
    p = Path(path)
    if not p.exists():
        raise CryptoError(f"Private key file not found: {path}")
    pem = p.read_bytes()
    key = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise CryptoError(f"Key is not an RSA private key: {path}")
    return key


def load_public_key(path: str) -> rsa.RSAPublicKey:
    """Load an RSA public key from a PEM file."""
    p = Path(path)
    if not p.exists():
        raise CryptoError(f"Public key file not found: {path}")
    pem = p.read_bytes()
    key = serialization.load_pem_public_key(pem)
    if not isinstance(key, rsa.RSAPublicKey):
        raise CryptoError(f"Key is not an RSA public key: {path}")
    return key


# --- RSA signing / verification ---

def sign_challenge(private_key: rsa.RSAPrivateKey, challenge: bytes) -> bytes:
    """Sign a challenge with the private key (PKCS1v15, SHA-256)."""
    try:
        return private_key.sign(challenge, padding.PKCS1v15(), hashes.SHA256())
    except Exception as e:
        raise CryptoError(f"Signing failed: {e}")


def verify_challenge(public_key: rsa.RSAPublicKey, challenge: bytes, signature: bytes) -> bool:
    """Verify a challenge signature."""
    try:
        public_key.verify(signature, challenge, padding.PKCS1v15(), hashes.SHA256())
        return True
    except Exception:
        return False


def public_key_fingerprint(public_key: rsa.RSAPublicKey) -> str:
    """SHA-256 fingerprint of the DER-encoded public key."""
    der = public_key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    digest = hashes.Hash(hashes.SHA256())
    digest.update(der)
    return digest.finalize().hex()


# --- AES-256-GCM symmetric encryption ---

_NONCE_LENGTH = 12  # 96 bits – standard for GCM


def generate_encryption_key() -> str:
    """Generate a random 32-byte AES-256 key and return as base64 string."""
    return base64.b64encode(os.urandom(32)).decode("ascii")


def encrypt_data(key_bytes: bytes, data: bytes, aad: bytes = b"") -> bytes:
    """Encrypt data with AES-256-GCM.
    Returns: nonce (12 bytes) + ciphertext (includes 16-byte tag).
    """
    nonce = os.urandom(_NONCE_LENGTH)
    aesgcm = AESGCM(key_bytes)
    ct = aesgcm.encrypt(nonce, data, aad)
    return nonce + ct


def decrypt_data(key_bytes: bytes, encrypted: bytes, aad: bytes = b"") -> bytes:
    """Decrypt data encrypted with encrypt_data().
    Expects: nonce (12 bytes) + ciphertext (includes 16-byte tag).
    """
    if len(encrypted) < _NONCE_LENGTH + 16:
        raise CryptoError("Encrypted data too short")
    nonce = encrypted[:_NONCE_LENGTH]
    ct = encrypted[_NONCE_LENGTH:]
    aesgcm = AESGCM(key_bytes)
    try:
        return aesgcm.decrypt(nonce, ct, aad)
    except Exception as e:
        raise CryptoError(f"Decryption failed: {e}")


# --- Compression ---

def compress_data(data: bytes) -> bytes:
    """Compress data with zlib (level 6)."""
    return zlib.compress(data, level=6)


def decompress_data(data: bytes) -> bytes:
    """Decompress zlib-compressed data."""
    try:
        return zlib.decompress(data)
    except zlib.error as e:
        raise CryptoError(f"Decompression failed: {e}")
