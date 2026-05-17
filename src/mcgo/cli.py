"""Command-line interface for McGo server and client."""

from __future__ import annotations

import argparse
import os
import sys

from . import __version__


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="mcgo",
        description="McGo - MQTT-based file synchronization",
    )
    parser.add_argument("--version", action="version", version=f"mcgo {__version__}")
    sub = parser.add_subparsers(dest="command", help="Server or client mode")

    # --- Server ---
    server_parser = sub.add_parser("server", help="Run McGo server")
    server_parser.add_argument("--config", default="./mcgo_server.toml", help="Config file path")
    server_parser.add_argument("--init", action="store_true", help="Create default server config and exit")
    server_parser.add_argument("--gen-keys", action="store_true", help="Generate server RSA key pair and exit")
    server_parser.add_argument("--gen-encryption-key", action="store_true", help="Generate a random encryption key")

    # --- Client ---
    client_parser = sub.add_parser("client", help="Run McGo client")
    client_parser.add_argument("--config", default="./mcgo_client.toml", help="Config file path")
    client_parser.add_argument("--init", action="store_true", help="Create default client config and exit")
    client_parser.add_argument("--gen-keys", action="store_true", help="Generate client RSA key pair and exit")
    client_parser.add_argument("--sync", action="store_true", help="One-shot sync and exit")
    client_parser.add_argument("--watch", action="store_true", help="Continuous watch mode")

    args = parser.parse_args()

    if args.command == "server":
        _handle_server(args)
    elif args.command == "client":
        _handle_client(args)
    else:
        parser.print_help()
        sys.exit(1)


def _handle_server(args) -> None:
    if args.init:
        from .config import create_default_server_config
        create_default_server_config(args.config)
        return

    if args.gen_keys:
        _gen_keys("server", args.config)
        return

    if args.gen_encryption_key:
        from .crypto import generate_encryption_key
        print(generate_encryption_key())
        return

    # Run server
    from .server import McGoServer
    server = McGoServer(args.config)
    server.start()


def _handle_client(args) -> None:
    if args.init:
        from .config import create_default_client_config
        create_default_client_config(args.config)
        return

    if args.gen_keys:
        _gen_keys("client", args.config)
        return

    from .client import McGoClient
    client = McGoClient(args.config)

    if args.sync:
        result = client.sync()
        import json
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        # Default: watch mode
        client.start_watch()


def _gen_keys(mode: str, config_path: str) -> None:
    """Generate RSA key pair and save to keys/ directory."""
    from .crypto import generate_rsa_keypair, save_private_key, save_public_key

    private_pem, public_pem = generate_rsa_keypair()

    # Determine output paths from config if it exists, otherwise use defaults
    keys_dir = os.path.join(os.path.dirname(os.path.abspath(config_path)), "keys")
    os.makedirs(keys_dir, exist_ok=True)

    if mode == "server":
        priv_path = os.path.join(keys_dir, "server_private.pem")
        pub_path = os.path.join(keys_dir, "server_public.pem")
    else:
        priv_path = os.path.join(keys_dir, "client_private.pem")
        pub_path = os.path.join(keys_dir, "client_public.pem")

    if os.path.exists(priv_path):
        print(f"Private key already exists: {priv_path}", file=sys.stderr)
        sys.exit(1)

    save_private_key(priv_path, private_pem)
    save_public_key(pub_path, public_pem)

    print(f"Private key saved: {priv_path}")
    print(f"Public key saved:  {pub_path}")

    if mode == "server":
        print()
        print("To register a client, add to clients.toml:")
        print(f'[client.YOUR_CLIENT_ID]')
        print(f'public_key_path = "keys/client_public.pem"')
