"""Shared fixtures for McGo unit tests."""

import base64
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# Ensure src/ is on sys.path so ``from mcgo.xxx import ...`` works
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


# ---------------------------------------------------------------------------
# Session-scoped crypto fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def rsa_keypair():
    """Generate a single RSA-2048 key pair reused across all tests."""
    from mcgo.crypto import generate_rsa_keypair

    return generate_rsa_keypair()


@pytest.fixture(scope="session")
def rsa_private_key_bytes(rsa_keypair):
    """PEM bytes of the private key."""
    return rsa_keypair[0]


@pytest.fixture(scope="session")
def rsa_public_key_bytes(rsa_keypair):
    """PEM bytes of the public key."""
    return rsa_keypair[1]


@pytest.fixture(scope="session")
def encryption_key_str():
    """A valid base64-encoded 32-byte AES-256 key."""
    from mcgo.crypto import generate_encryption_key

    return generate_encryption_key()


@pytest.fixture(scope="session")
def encryption_key_bytes(encryption_key_str):
    """Raw 32-byte AES-256 key."""
    return base64.b64decode(encryption_key_str)


# ---------------------------------------------------------------------------
# Function-scoped filesystem fixtures (tmp_path)
# ---------------------------------------------------------------------------


def _write_toml(path, content):
    path.write_text(content, encoding="utf-8")
    return str(path)


@pytest.fixture
def temp_keys_dir(tmp_path, rsa_private_key_bytes, rsa_public_key_bytes):
    """Create a keys/ directory with server and client key files."""
    keys = tmp_path / "keys"
    keys.mkdir()
    (keys / "server_private.pem").write_bytes(rsa_private_key_bytes)
    (keys / "server_public.pem").write_bytes(rsa_public_key_bytes)
    (keys / "client_private.pem").write_bytes(rsa_private_key_bytes)
    (keys / "client_public.pem").write_bytes(rsa_public_key_bytes)
    return keys


@pytest.fixture
def server_config_path(tmp_path, temp_keys_dir, encryption_key_str):
    """Write a valid server TOML config and return its path."""
    content = f"""[server]
mqtt_host = "localhost"
mqtt_port = 1883
mqtt_tls = false
mqtt_username = ""
mqtt_password = ""
scan_directory = "{tmp_path.as_posix()}/files"
ignore_file = ".mcgoignore"
encryption_key = "{encryption_key_str}"

[auth]
server_private_key = "{temp_keys_dir.as_posix()}/server_private.pem"
clients_file = "{tmp_path.as_posix()}/clients.toml"
challenge_timeout_seconds = 30

[logging]
level = "DEBUG"
file = ""
"""
    return _write_toml(tmp_path / "server.toml", content)


@pytest.fixture
def client_config_path(tmp_path, temp_keys_dir, encryption_key_str):
    """Write a valid client TOML config and return its path."""
    content = f"""[client]
client_id = "testclient"
display_name = "Test Client"
mqtt_host = "localhost"
mqtt_port = 1883
mqtt_tls = false
mqtt_username = ""
mqtt_password = ""
sync_directory = "{tmp_path.as_posix()}/sync"
ignore_file = ".mcgoignore"
encryption_key = "{encryption_key_str}"

[auth]
client_private_key = "{temp_keys_dir.as_posix()}/client_private.pem"
server_public_key = "{temp_keys_dir.as_posix()}/server_public.pem"
auth_timeout_seconds = 30

[logging]
level = "DEBUG"
file = ""
"""
    return _write_toml(tmp_path / "client.toml", content)


@pytest.fixture
def clients_registry_path(tmp_path, temp_keys_dir):
    """Write a clients.toml that registers 'testclient'."""
    content = f"""[client.testclient]
public_key_path = "{temp_keys_dir.as_posix()}/client_public.pem"
"""
    return _write_toml(tmp_path / "clients.toml", content)


@pytest.fixture
def scan_dir(tmp_path):
    """Create and return the server scan directory."""
    d = tmp_path / "files"
    d.mkdir()
    return d


@pytest.fixture
def sync_dir(tmp_path):
    """Create and return the client sync directory."""
    d = tmp_path / "sync"
    d.mkdir()
    return d


# ---------------------------------------------------------------------------
# MQTT mock fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_mqtt():
    """Return a MagicMock configured to look like a paho MQTT client instance."""
    m = MagicMock()
    m.CallbackAPIVersion = MagicMock()
    m.CallbackAPIVersion.VERSION2 = 2
    m.CONNACK_ACCEPTED = 0
    m.publish.return_value = MagicMock(rc=0)
    m.subscribe.return_value = MagicMock()
    return m


@pytest.fixture
def server_instance(server_config_path, mock_mqtt):
    """McGoServer with mocked MQTT but real config and internal logic."""
    with patch("mcgo.server.mqtt.Client", return_value=mock_mqtt):
        from mcgo.server import McGoServer

        s = McGoServer(server_config_path)
        yield s


@pytest.fixture
def client_instance(client_config_path, mock_mqtt):
    """McGoClient with mocked MQTT but real config and internal logic."""
    with patch("mcgo.client.mqtt.Client", return_value=mock_mqtt):
        from mcgo.client import McGoClient

        c = McGoClient(client_config_path)
        yield c
