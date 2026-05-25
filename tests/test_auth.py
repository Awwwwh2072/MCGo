"""Tests for mcgo.auth – ServerAuth and ClientAuth challenge-response state machines."""

import base64

import pytest

from mcgo.auth import AuthError, ClientAuth, ServerAuth
from mcgo.crypto import generate_rsa_keypair, load_private_key, load_public_key


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_key(path, pem_bytes):
    """Write PEM bytes to a file."""
    from pathlib import Path
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(pem_bytes)


def _write_clients_toml(path, client_id, pubkey_path):
    """Write a clients.toml registry."""
    from pathlib import Path
    # Use forward slashes to avoid TOML escape issues on Windows
    safe_path = pubkey_path.replace("\\", "/")
    content = f"""[client.{client_id}]
public_key_path = "{safe_path}"
"""
    Path(path).write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# ServerAuth
# ---------------------------------------------------------------------------

class TestServerAuth:
    @pytest.fixture
    def keypair(self):
        return generate_rsa_keypair()

    @pytest.fixture
    def clients_file(self, tmp_path, keypair):
        priv_pem, pub_pem = keypair
        keys_dir = tmp_path / "keys"
        keys_dir.mkdir()
        pub_path = keys_dir / "client_public.pem"
        pub_path.write_bytes(pub_pem)
        reg_path = tmp_path / "clients.toml"
        _write_clients_toml(str(reg_path), "testclient", str(pub_path))
        return str(reg_path)

    def test_init_loads_registered_clients(self, clients_file):
        auth = ServerAuth(clients_file)
        assert auth.is_registered("testclient") is True

    def test_is_registered_unknown(self, clients_file):
        auth = ServerAuth(clients_file)
        assert auth.is_registered("unknown") is False

    def test_start_challenge_known_client(self, clients_file):
        auth = ServerAuth(clients_file)
        challenge_b64 = auth.start_challenge("testclient")
        raw = base64.b64decode(challenge_b64)
        assert len(raw) == 32

    def test_start_challenge_unknown_client_raises(self, clients_file):
        auth = ServerAuth(clients_file)
        with pytest.raises(AuthError, match="Unknown client"):
            auth.start_challenge("unknown")

    def test_verify_response_valid(self, clients_file, keypair):
        priv_pem, _pub_pem = keypair
        priv = load_private_key_from_pem(priv_pem)
        auth = ServerAuth(clients_file)
        challenge_b64 = auth.start_challenge("testclient")
        challenge = base64.b64decode(challenge_b64)

        from mcgo.crypto import sign_challenge
        sig = sign_challenge(priv, challenge)
        sig_b64 = base64.b64encode(sig).decode("ascii")

        assert auth.verify_response("testclient", sig_b64) is True

    def test_verify_response_invalid_signature(self, clients_file):
        auth = ServerAuth(clients_file)
        auth.start_challenge("testclient")
        assert auth.verify_response("testclient", "!!!bad!!!") is False

    def test_verify_response_non_base64(self, clients_file):
        auth = ServerAuth(clients_file)
        auth.start_challenge("testclient")
        assert auth.verify_response("testclient", "!!!not-base64!!!") is False

    def test_verify_response_no_challenge(self, clients_file):
        auth = ServerAuth(clients_file)
        assert auth.verify_response("testclient", "AAAA") is False

    def test_is_authenticated_after_success(self, clients_file, keypair):
        priv_pem, _pub_pem = keypair
        priv = load_private_key_from_pem(priv_pem)
        auth = ServerAuth(clients_file)
        challenge_b64 = auth.start_challenge("testclient")
        challenge = base64.b64decode(challenge_b64)

        from mcgo.crypto import sign_challenge
        sig = sign_challenge(priv, challenge)
        sig_b64 = base64.b64encode(sig).decode("ascii")

        auth.verify_response("testclient", sig_b64)
        assert auth.is_authenticated("testclient") is True

    def test_is_authenticated_before_auth(self, clients_file):
        auth = ServerAuth(clients_file)
        auth.start_challenge("testclient")
        assert auth.is_authenticated("testclient") is False

    def test_is_authenticated_unknown_client(self, clients_file):
        auth = ServerAuth(clients_file)
        assert auth.is_authenticated("unknown") is False

    def test_cleanup_timeouts(self, clients_file, monkeypatch):
        import time as _time
        auth = ServerAuth(clients_file, challenge_timeout=5)

        # Set initial time to a known base
        base = 1_000_000.0
        monkeypatch.setattr(_time, "time", lambda: base)

        auth.start_challenge("testclient")

        # Move time forward past timeout
        monkeypatch.setattr(_time, "time", lambda: base + 10)

        auth.cleanup_timeouts()
        # Challenge is cleared after timeout, and client is registered
        assert auth.is_authenticated("testclient") is True

    def test_cleanup_timeouts_not_expired(self, clients_file, monkeypatch):
        import time as _time
        base = 1_000_000.0
        monkeypatch.setattr(_time, "time", lambda: base)
        auth = ServerAuth(clients_file, challenge_timeout=30)
        auth.start_challenge("testclient")

        # Move forward only 10 seconds
        monkeypatch.setattr(_time, "time", lambda: base + 10)
        auth.cleanup_timeouts()
        # Challenge still pending
        assert auth.is_authenticated("testclient") is False

    def test_remove_challenge(self, clients_file):
        auth = ServerAuth(clients_file)
        auth.start_challenge("testclient")
        auth.remove_challenge("testclient")
        # After manual removal, is_authenticated returns True (registered + no pending challenge)
        assert auth.is_authenticated("testclient") is True

    def test_remove_challenge_nonexistent(self, clients_file):
        auth = ServerAuth(clients_file)
        auth.remove_challenge("nonexistent")  # no error


# ---------------------------------------------------------------------------
# ClientAuth
# ---------------------------------------------------------------------------

class TestClientAuth:
    @pytest.fixture
    def keypair(self):
        return generate_rsa_keypair()

    @pytest.fixture
    def private_key_path(self, tmp_path, keypair):
        priv_pem, _pub_pem = keypair
        p = tmp_path / "client_private.pem"
        p.write_bytes(priv_pem)
        return str(p)

    def test_handle_challenge_valid(self, private_key_path):
        auth = ClientAuth(private_key_path)
        challenge = base64.b64encode(b"12345678901234567890123456789012").decode("ascii")
        result = auth.handle_challenge({"challenge": challenge})
        assert result == b"12345678901234567890123456789012"

    def test_handle_challenge_missing_key(self, private_key_path):
        auth = ClientAuth(private_key_path)
        result = auth.handle_challenge({"foo": "bar"})
        assert result == b""

    def test_handle_challenge_invalid_base64(self, private_key_path):
        auth = ClientAuth(private_key_path)
        result = auth.handle_challenge({"challenge": "!!!bad!!!"})
        assert result is None

    def test_build_response(self, private_key_path):
        auth = ClientAuth(private_key_path)
        auth.handle_challenge({"challenge": base64.b64encode(b"x" * 32).decode("ascii")})
        response = auth.build_response()
        assert "signature" in response
        raw_sig = base64.b64decode(response["signature"])
        assert len(raw_sig) == 256  # RSA-2048 signature

    def test_build_response_without_challenge_raises(self, private_key_path):
        auth = ClientAuth(private_key_path)
        with pytest.raises(AuthError, match="No pending challenge"):
            auth.build_response()

    def test_build_response_clears_pending(self, private_key_path):
        auth = ClientAuth(private_key_path)
        auth.handle_challenge({"challenge": base64.b64encode(b"y" * 32).decode("ascii")})
        auth.build_response()
        # Second call should raise since challenge was cleared
        with pytest.raises(AuthError, match="No pending challenge"):
            auth.build_response()


# ---------------------------------------------------------------------------
# Full challenge-response roundtrip
# ---------------------------------------------------------------------------

class TestFullAuthRoundtrip:
    def test_full_roundtrip(self, tmp_path):
        """Generate keypair, register client, complete full auth flow."""
        priv_pem, pub_pem = generate_rsa_keypair()

        # Write keys and registry
        keys_dir = tmp_path / "keys"
        keys_dir.mkdir()
        pub_path = keys_dir / "client_public.pem"
        pub_path.write_bytes(pub_pem)
        priv_path = keys_dir / "client_private.pem"
        priv_path.write_bytes(priv_pem)

        reg_path = tmp_path / "clients.toml"
        _write_clients_toml(str(reg_path), "myclient", str(pub_path))

        # Server-side
        server_auth = ServerAuth(str(reg_path))
        assert server_auth.is_registered("myclient")

        # Client-side
        client_auth = ClientAuth(str(priv_path))

        # Server sends challenge
        challenge_b64 = server_auth.start_challenge("myclient")

        # Client receives challenge and responds
        challenge = client_auth.handle_challenge({"challenge": challenge_b64})
        assert challenge is not None
        response = client_auth.build_response()

        # Server verifies
        assert server_auth.verify_response("myclient", response["signature"]) is True
        assert server_auth.is_authenticated("myclient") is True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_private_key_from_pem(pem: bytes):
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = load_pem_private_key(pem, password=None)
    assert isinstance(key, rsa.RSAPrivateKey)
    return key
