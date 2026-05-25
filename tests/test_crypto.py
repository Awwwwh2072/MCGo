"""Tests for mcgo.crypto – RSA, AES-256-GCM, zlib compression."""

import base64

import pytest

from mcgo.crypto import (
    CryptoError,
    compress_data,
    decompress_data,
    decrypt_data,
    encrypt_data,
    generate_encryption_key,
    generate_rsa_keypair,
    load_private_key,
    load_public_key,
    public_key_fingerprint,
    save_private_key,
    save_public_key,
    sign_challenge,
    verify_challenge,
)


# ---------------------------------------------------------------------------
# RSA key management
# ---------------------------------------------------------------------------

class TestGenerateRSAKeypair:
    def test_returns_two_bytes(self):
        priv, pub = generate_rsa_keypair()
        assert isinstance(priv, bytes)
        assert isinstance(pub, bytes)

    def test_private_key_is_pem(self):
        priv, _ = generate_rsa_keypair()
        assert priv.startswith(b"-----BEGIN PRIVATE KEY-----")

    def test_public_key_is_pem(self):
        _, pub = generate_rsa_keypair()
        assert pub.startswith(b"-----BEGIN PUBLIC KEY-----")

    def test_valid_keys_are_loadable(self):
        priv_pem, pub_pem = generate_rsa_keypair()
        from cryptography.hazmat.primitives.serialization import (
            load_pem_private_key,
            load_pem_public_key,
        )
        priv = load_pem_private_key(priv_pem, password=None)
        pub = load_pem_public_key(pub_pem)
        assert priv is not None
        assert pub is not None


class TestSaveAndLoadKey:
    def test_save_and_load_private_key(self, tmp_path, rsa_private_key_bytes):
        p = str(tmp_path / "private.pem")
        save_private_key(p, rsa_private_key_bytes)
        key = load_private_key(p)
        from cryptography.hazmat.primitives.asymmetric import rsa
        assert isinstance(key, rsa.RSAPrivateKey)

    def test_load_private_key_missing_file(self):
        with pytest.raises(CryptoError, match="not found"):
            load_private_key("/nonexistent/path/key.pem")

    def test_load_private_key_wrong_type(self, tmp_path):
        p = tmp_path / "bad.pem"
        p.write_text("garbage data not a key")
        with pytest.raises((CryptoError, ValueError)):
            load_private_key(str(p))

    def test_save_and_load_public_key(self, tmp_path, rsa_public_key_bytes):
        p = str(tmp_path / "public.pem")
        save_public_key(p, rsa_public_key_bytes)
        key = load_public_key(p)
        from cryptography.hazmat.primitives.asymmetric import rsa
        assert isinstance(key, rsa.RSAPublicKey)

    def test_load_public_key_missing_file(self):
        with pytest.raises(CryptoError, match="not found"):
            load_public_key("/nonexistent/path/key.pem")

    def test_save_creates_parent_dir(self, tmp_path, rsa_private_key_bytes):
        p = str(tmp_path / "sub" / "nested" / "key.pem")
        save_private_key(p, rsa_private_key_bytes)
        assert load_private_key(p) is not None


# ---------------------------------------------------------------------------
# Signing and verification
# ---------------------------------------------------------------------------

class TestSignVerify:
    def test_sign_and_verify_roundtrip(self, rsa_keypair):
        priv_pem, pub_pem = rsa_keypair
        priv = load_private_key_from_bytes(priv_pem)
        pub = load_public_key_from_bytes(pub_pem)
        challenge = b"hello-challenge-32-bytes!!----"
        sig = sign_challenge(priv, challenge)
        assert verify_challenge(pub, challenge, sig) is True

    def test_verify_wrong_key(self, rsa_keypair):
        priv_pem, _ = rsa_keypair
        priv = load_private_key_from_bytes(priv_pem)
        other_priv, other_pub = generate_rsa_keypair()
        other_pub_key = load_public_key_from_bytes(other_pub)
        challenge = b"hello-challenge-32-bytes!!----"
        sig = sign_challenge(priv, challenge)
        assert verify_challenge(other_pub_key, challenge, sig) is False

    def test_verify_tampered_signature(self, rsa_keypair):
        priv_pem, pub_pem = rsa_keypair
        priv = load_private_key_from_bytes(priv_pem)
        pub = load_public_key_from_bytes(pub_pem)
        challenge = b"hello-challenge-32-bytes!!----"
        sig = sign_challenge(priv, challenge)
        tampered = sig[:-1] + bytes([sig[-1] ^ 0xFF])
        assert verify_challenge(pub, challenge, tampered) is False

    def test_verify_tampered_challenge(self, rsa_keypair):
        priv_pem, pub_pem = rsa_keypair
        priv = load_private_key_from_bytes(priv_pem)
        pub = load_public_key_from_bytes(pub_pem)
        challenge = b"hello-challenge-32-bytes!!----"
        sig = sign_challenge(priv, challenge)
        assert verify_challenge(pub, b"tampered-challenge!!!!!!!!!!!!!!", sig) is False

    def test_public_key_fingerprint_is_64_char_hex(self, rsa_public_key_bytes):
        pub = load_public_key_from_bytes(rsa_public_key_bytes)
        fp = public_key_fingerprint(pub)
        assert len(fp) == 64
        assert all(c in "0123456789abcdef" for c in fp)

    def test_public_key_fingerprint_deterministic(self, rsa_public_key_bytes):
        pub = load_public_key_from_bytes(rsa_public_key_bytes)
        assert public_key_fingerprint(pub) == public_key_fingerprint(pub)


# ---------------------------------------------------------------------------
# AES-256-GCM encryption
# ---------------------------------------------------------------------------

class TestGenerateEncryptionKey:
    def test_format_is_base64_string(self):
        key = generate_encryption_key()
        assert isinstance(key, str)
        raw = base64.b64decode(key)
        assert len(raw) == 32

    def test_two_keys_are_different(self):
        assert generate_encryption_key() != generate_encryption_key()


class TestEncryptDecrypt:
    def test_roundtrip(self, encryption_key_bytes):
        data = b"Hello, McGo! This is test data."
        encrypted = encrypt_data(encryption_key_bytes, data)
        decrypted = decrypt_data(encryption_key_bytes, encrypted)
        assert decrypted == data

    def test_roundtrip_with_aad(self, encryption_key_bytes):
        data = b"important data"
        aad = b"file-id-12345"
        encrypted = encrypt_data(encryption_key_bytes, data, aad)
        decrypted = decrypt_data(encryption_key_bytes, encrypted, aad)
        assert decrypted == data

    def test_wrong_aad_fails(self, encryption_key_bytes):
        data = b"important data"
        encrypted = encrypt_data(encryption_key_bytes, data, b"correct-aad")
        with pytest.raises(CryptoError, match="Decryption failed"):
            decrypt_data(encryption_key_bytes, encrypted, b"wrong-aad")

    def test_wrong_key_fails(self, encryption_key_bytes):
        data = b"test data"
        encrypted = encrypt_data(encryption_key_bytes, data)
        wrong_key = bytes([b ^ 0xFF for b in encryption_key_bytes])
        with pytest.raises(CryptoError, match="Decryption failed"):
            decrypt_data(wrong_key, encrypted)

    def test_nonce_uniqueness(self, encryption_key_bytes):
        data = b"same data twice"
        e1 = encrypt_data(encryption_key_bytes, data)
        e2 = encrypt_data(encryption_key_bytes, data)
        # Nonces (first 12 bytes) should differ
        assert e1[:12] != e2[:12]

    def test_data_too_short(self, encryption_key_bytes):
        with pytest.raises(CryptoError, match="too short"):
            decrypt_data(encryption_key_bytes, b"short")

    def test_roundtrip_large_data(self, encryption_key_bytes):
        data = b"x" * 1_000_000
        encrypted = encrypt_data(encryption_key_bytes, data)
        assert decrypt_data(encryption_key_bytes, encrypted) == data

    def test_roundtrip_empty_data(self, encryption_key_bytes):
        encrypted = encrypt_data(encryption_key_bytes, b"")
        assert decrypt_data(encryption_key_bytes, encrypted) == b""


# ---------------------------------------------------------------------------
# Compression
# ---------------------------------------------------------------------------

class TestCompressDecompress:
    def test_roundtrip(self):
        data = b"Hello, compress me! " * 500
        compressed = compress_data(data)
        decompressed = decompress_data(compressed)
        assert decompressed == data

    def test_reduces_repetitive_data(self):
        data = b"A" * 10_000
        compressed = compress_data(data)
        assert len(compressed) < len(data)

    def test_decompress_invalid_data_raises(self):
        with pytest.raises(CryptoError, match="Decompression failed"):
            decompress_data(b"not valid zlib data!!")

    def test_roundtrip_empty_bytes(self):
        compressed = compress_data(b"")
        assert decompress_data(compressed) == b""

    def test_roundtrip_binary_data(self):
        data = bytes(range(256)) * 40
        compressed = compress_data(data)
        assert decompress_data(compressed) == data


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_private_key_from_bytes(pem: bytes):
    """Load private key from PEM bytes without file I/O."""
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = load_pem_private_key(pem, password=None)
    assert isinstance(key, rsa.RSAPrivateKey)
    return key


def load_public_key_from_bytes(pem: bytes):
    """Load public key from PEM bytes without file I/O."""
    from cryptography.hazmat.primitives.serialization import load_pem_public_key
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = load_pem_public_key(pem)
    assert isinstance(key, rsa.RSAPublicKey)
    return key
