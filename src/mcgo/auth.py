"""Authentication state machines for server and client."""

from __future__ import annotations

import base64
import os
import time
from typing import Optional

from cryptography.hazmat.primitives.asymmetric import rsa

from .crypto import sign_challenge, verify_challenge
from .config import load_clients_registry


class AuthError(Exception):
    """Raised when authentication fails."""


class ServerAuth:
    """Manages challenge-response authentication for connected clients."""

    def __init__(self, clients_file: str, challenge_timeout: int = 30):
        self._challenge_timeout = challenge_timeout
        self._challenges: dict[str, dict] = {}  # client_id -> {challenge, timestamp, public_key}
        self._clients: dict[str, rsa.RSAPublicKey] = {}  # client_id -> public_key
        self._load_clients(clients_file)

    def _load_clients(self, clients_file: str) -> None:
        """Load registered clients and their public keys."""
        registry = load_clients_registry(clients_file)
        from .crypto import load_public_key
        for client_id, pubkey_path in registry.items():
            try:
                self._clients[client_id] = load_public_key(pubkey_path)
            except Exception as e:
                print(f"Warning: failed to load public key for '{client_id}': {e}")

    def is_registered(self, client_id: str) -> bool:
        return client_id in self._clients

    def start_challenge(self, client_id: str) -> str:
        """Generate a challenge for a client. Returns base64-encoded challenge."""
        if client_id not in self._clients:
            raise AuthError(f"Unknown client: {client_id}")

        challenge = os.urandom(32)
        challenge_b64 = base64.b64encode(challenge).decode("ascii")

        self._challenges[client_id] = {
            "challenge": challenge,
            "timestamp": time.time(),
            "public_key": self._clients[client_id],
        }

        return challenge_b64

    def verify_response(self, client_id: str, signature_b64: str) -> bool:
        """Verify a client's signed challenge response."""
        entry = self._challenges.get(client_id)
        if entry is None:
            return False

        try:
            signature = base64.b64decode(signature_b64)
        except Exception:
            return False

        public_key = entry["public_key"]
        challenge = entry["challenge"]

        if verify_challenge(public_key, challenge, signature):
            del self._challenges[client_id]
            return True
        return False

    def is_authenticated(self, client_id: str) -> bool:
        """Check if a client has completed authentication (challenge removed = authenticated)."""
        return client_id in self._clients and client_id not in self._challenges

    def cleanup_timeouts(self) -> None:
        """Remove expired pending challenges."""
        now = time.time()
        expired = [
            cid for cid, entry in self._challenges.items()
            if now - entry["timestamp"] > self._challenge_timeout
        ]
        for cid in expired:
            del self._challenges[cid]

    def remove_challenge(self, client_id: str) -> None:
        self._challenges.pop(client_id, None)


class ClientAuth:
    """Client-side authentication handling."""

    def __init__(self, private_key_path: str):
        from .crypto import load_private_key
        self._private_key = load_private_key(private_key_path)
        self._pending_challenge: Optional[bytes] = None

    def handle_challenge(self, payload: dict) -> Optional[bytes]:
        """Extract challenge from server message. Returns the raw challenge bytes."""
        challenge_b64 = payload.get("challenge", "")
        try:
            self._pending_challenge = base64.b64decode(challenge_b64)
            return self._pending_challenge
        except Exception:
            self._pending_challenge = None
            return None

    def build_response(self) -> dict:
        """Sign the pending challenge and return the auth_response payload."""
        if self._pending_challenge is None:
            raise AuthError("No pending challenge to respond to")

        signature = sign_challenge(self._private_key, self._pending_challenge)
        signature_b64 = base64.b64encode(signature).decode("ascii")
        self._pending_challenge = None
        return {"signature": signature_b64}
