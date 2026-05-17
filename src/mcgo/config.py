"""Configuration loading, validation, and default generation for McGo server and client."""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


class ConfigError(Exception):
    """Raised when configuration validation fails."""


@dataclass
class ServerConfig:
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_tls: bool = False
    mqtt_username: str = ""
    mqtt_password: str = ""
    scan_directory: str = "./files"
    ignore_file: str = ".mcgoignore"
    encryption_key: str = ""
    server_private_key: str = "keys/server_private.pem"
    clients_file: str = "clients.toml"
    challenge_timeout_seconds: int = 30
    log_level: str = "INFO"
    log_file: str = ""


@dataclass
class ClientConfig:
    client_id: str = "default"
    display_name: str = "Default Client"
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_tls: bool = False
    mqtt_username: str = ""
    mqtt_password: str = ""
    sync_directory: str = "./sync"
    ignore_file: str = ".mcgoignore"
    encryption_key: str = ""
    client_private_key: str = "keys/client_private.pem"
    server_public_key: str = "keys/server_public.pem"
    auth_timeout_seconds: int = 30
    log_level: str = "INFO"
    log_file: str = ""


def _resolve_path(path: str, config_dir: str) -> str:
    """Resolve a potentially relative path against the config file's directory."""
    p = Path(path)
    if p.is_absolute():
        return str(p)
    return str(Path(config_dir) / p)


def _validate_encryption_key(key: str) -> None:
    """Validate that the encryption_key is valid base64-encoded 32 bytes."""
    if not key:
        raise ConfigError("encryption_key must not be empty")
    import base64
    try:
        raw = base64.b64decode(key)
    except Exception:
        raise ConfigError("encryption_key must be valid base64")
    if len(raw) != 32:
        raise ConfigError(f"encryption_key must decode to 32 bytes (got {len(raw)})")


def _validate_port(port: int) -> None:
    if not (1 <= port <= 65535):
        raise ConfigError(f"mqtt_port must be in range 1-65535 (got {port})")


def load_server_config(path: str) -> ServerConfig:
    """Load and validate a server TOML configuration file."""
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigError(f"Config file not found: {path}")

    with open(config_path, "rb") as f:
        raw = tomllib.load(f)

    config_dir = str(config_path.parent)

    server_raw = raw.get("server", {})
    auth_raw = raw.get("auth", {})
    logging_raw = raw.get("logging", {})

    cfg = ServerConfig(
        mqtt_host=server_raw.get("mqtt_host", "localhost"),
        mqtt_port=server_raw.get("mqtt_port", 1883),
        mqtt_tls=server_raw.get("mqtt_tls", False),
        mqtt_username=server_raw.get("mqtt_username", ""),
        mqtt_password=server_raw.get("mqtt_password", ""),
        scan_directory=_resolve_path(server_raw.get("scan_directory", "./files"), config_dir),
        ignore_file=server_raw.get("ignore_file", ".mcgoignore"),
        encryption_key=server_raw.get("encryption_key", ""),
        server_private_key=_resolve_path(auth_raw.get("server_private_key", "keys/server_private.pem"), config_dir),
        clients_file=_resolve_path(auth_raw.get("clients_file", "clients.toml"), config_dir),
        challenge_timeout_seconds=auth_raw.get("challenge_timeout_seconds", 30),
        log_level=logging_raw.get("level", "INFO"),
        log_file=logging_raw.get("file", ""),
    )

    _validate_port(cfg.mqtt_port)
    _validate_encryption_key(cfg.encryption_key)

    return cfg


def load_client_config(path: str) -> ClientConfig:
    """Load and validate a client TOML configuration file."""
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigError(f"Config file not found: {path}")

    with open(config_path, "rb") as f:
        raw = tomllib.load(f)

    config_dir = str(config_path.parent)

    client_raw = raw.get("client", {})
    auth_raw = raw.get("auth", {})
    logging_raw = raw.get("logging", {})

    cfg = ClientConfig(
        client_id=client_raw.get("client_id", "default"),
        display_name=client_raw.get("display_name", "Default Client"),
        mqtt_host=client_raw.get("mqtt_host", "localhost"),
        mqtt_port=client_raw.get("mqtt_port", 1883),
        mqtt_tls=client_raw.get("mqtt_tls", False),
        mqtt_username=client_raw.get("mqtt_username", ""),
        mqtt_password=client_raw.get("mqtt_password", ""),
        sync_directory=_resolve_path(client_raw.get("sync_directory", "./sync"), config_dir),
        ignore_file=client_raw.get("ignore_file", ".mcgoignore"),
        encryption_key=client_raw.get("encryption_key", ""),
        client_private_key=_resolve_path(auth_raw.get("client_private_key", "keys/client_private.pem"), config_dir),
        server_public_key=_resolve_path(auth_raw.get("server_public_key", "keys/server_public.pem"), config_dir),
        auth_timeout_seconds=auth_raw.get("auth_timeout_seconds", 30),
        log_level=logging_raw.get("level", "INFO"),
        log_file=logging_raw.get("file", ""),
    )

    _validate_port(cfg.mqtt_port)
    _validate_encryption_key(cfg.encryption_key)

    return cfg


def load_clients_registry(path: str) -> dict[str, str]:
    """Load the clients.toml registry. Returns dict of client_id -> public_key_path.

    Supports both flat format:
        [client.myid]
        public_key_path = "..."
    And nested format (TOML parses dotted keys as nested tables):
        [client.myid]  -> parsed as {'client': {'myid': {...}}}
    """
    p = Path(path)
    if not p.exists():
        return {}
    with open(p, "rb") as f:
        raw = tomllib.load(f)
    result = {}
    # TOML parses [client.X] as {'client': {'X': {...}}}
    clients_section = raw.get("client", {})
    if isinstance(clients_section, dict):
        for client_id, client_data in clients_section.items():
            if isinstance(client_data, dict):
                pubkey_path = client_data.get("public_key_path", "")
                if pubkey_path:
                    result[client_id] = _resolve_path(pubkey_path, str(p.parent))
    return result


def create_default_server_config(path: str) -> None:
    """Write a default server config TOML file."""
    content = '''[server]
# MQTT broker connection
mqtt_host = "localhost"
mqtt_port = 1883
mqtt_tls = false
mqtt_username = ""
mqtt_password = ""

# File system
scan_directory = "./files"
ignore_file = ".mcgoignore"

# Encryption key (32 bytes, base64-encoded)
# Generate with: python -m mcgo server --gen-encryption-key
encryption_key = ""

[auth]
server_private_key = "keys/server_private.pem"
clients_file = "clients.toml"
challenge_timeout_seconds = 30

[logging]
level = "INFO"
file = ""
'''
    _write_if_not_exists(path, content)


def create_default_client_config(path: str) -> None:
    """Write a default client config TOML file."""
    content = '''[client]
client_id = "default"
display_name = "Default Client"

# MQTT broker connection
mqtt_host = "localhost"
mqtt_port = 1883
mqtt_tls = false
mqtt_username = ""
mqtt_password = ""

# File system
sync_directory = "./sync"
ignore_file = ".mcgoignore"

# Encryption key (must match server)
encryption_key = ""

[auth]
client_private_key = "keys/client_private.pem"
server_public_key = "keys/server_public.pem"
auth_timeout_seconds = 30

[logging]
level = "INFO"
file = ""
'''
    _write_if_not_exists(path, content)


def _write_if_not_exists(path: str, content: str) -> None:
    p = Path(path)
    if p.exists():
        print(f"Config file already exists: {path}", file=sys.stderr)
        sys.exit(1)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    print(f"Created: {path}")
