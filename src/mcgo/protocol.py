"""MQTT topic constants, message schemas, and serialization helpers for McGo protocol."""

from __future__ import annotations

import json
import uuid
from typing import Any, Callable, Optional

# --- MQTT Topic patterns ---
# {client_id} and {file_id} are format placeholders

TOPIC_SERVER_ANNOUNCE = "mcgo/v1/server/announce"
TOPIC_SERVER_TREE = "mcgo/v1/server/tree"
TOPIC_SERVER_CHALLENGE = "mcgo/v1/server/challenge/{client_id}"
TOPIC_SERVER_AUTH_RESULT = "mcgo/v1/server/auth_result/{client_id}"
TOPIC_SERVER_FILE_META = "mcgo/v1/server/file/{file_id}/meta"
TOPIC_SERVER_FILE_CHUNK = "mcgo/v1/server/file/{file_id}/chunk/{seq}"
TOPIC_SERVER_FILE_DONE = "mcgo/v1/server/file/{file_id}/done"
TOPIC_SERVER_FILE_ABORT = "mcgo/v1/server/file/{file_id}/abort"

TOPIC_CLIENT_HELLO = "mcgo/v1/client/{client_id}/hello"
TOPIC_CLIENT_AUTH_RESPONSE = "mcgo/v1/client/{client_id}/auth_response"
TOPIC_CLIENT_FILE_REQUEST = "mcgo/v1/client/{client_id}/file_request"
TOPIC_CLIENT_STATUS = "mcgo/v1/client/{client_id}/status"

# Wildcard subscriptions
TOPIC_SERVER_CHALLENGE_WILD = "mcgo/v1/server/challenge/+"
TOPIC_SERVER_AUTH_RESULT_WILD = "mcgo/v1/server/auth_result/+"
TOPIC_SERVER_FILE_META_WILD = "mcgo/v1/server/file/+/meta"
TOPIC_SERVER_FILE_CHUNK_WILD = "mcgo/v1/server/file/+/chunk/+"
TOPIC_SERVER_FILE_DONE_WILD = "mcgo/v1/server/file/+/done"
TOPIC_SERVER_FILE_ABORT_WILD = "mcgo/v1/server/file/+/abort"

TOPIC_CLIENT_HELLO_WILD = "mcgo/v1/client/+/hello"
TOPIC_CLIENT_AUTH_RESPONSE_WILD = "mcgo/v1/client/+/auth_response"
TOPIC_CLIENT_FILE_REQUEST_WILD = "mcgo/v1/client/+/file_request"


# --- Topic builders ---

def server_challenge_topic(client_id: str) -> str:
    return TOPIC_SERVER_CHALLENGE.format(client_id=client_id)


def server_auth_result_topic(client_id: str) -> str:
    return TOPIC_SERVER_AUTH_RESULT.format(client_id=client_id)


def server_file_meta_topic(file_id: str) -> str:
    return TOPIC_SERVER_FILE_META.format(file_id=file_id)


def server_file_chunk_topic(file_id: str, seq: int) -> str:
    return TOPIC_SERVER_FILE_CHUNK.format(file_id=file_id, seq=seq)


def server_file_done_topic(file_id: str) -> str:
    return TOPIC_SERVER_FILE_DONE.format(file_id=file_id)


def server_file_abort_topic(file_id: str) -> str:
    return TOPIC_SERVER_FILE_ABORT.format(file_id=file_id)


def client_hello_topic(client_id: str) -> str:
    return TOPIC_CLIENT_HELLO.format(client_id=client_id)


def client_auth_response_topic(client_id: str) -> str:
    return TOPIC_CLIENT_AUTH_RESPONSE.format(client_id=client_id)


def client_file_request_topic(client_id: str) -> str:
    return TOPIC_CLIENT_FILE_REQUEST.format(client_id=client_id)


def client_status_topic(client_id: str) -> str:
    return TOPIC_CLIENT_STATUS.format(client_id=client_id)


# --- Client ID extraction from topics ---

def extract_client_id(topic: str) -> Optional[str]:
    """Extract client_id from a client-published topic like mcgo/v1/client/{id}/..."""
    parts = topic.split("/")
    if len(parts) >= 4 and parts[0] == "mcgo" and parts[1] == "v1" and parts[2] == "client":
        return parts[3]
    return None


def extract_file_id(topic: str) -> Optional[str]:
    """Extract file_id from a file transfer topic like mcgo/v1/server/file/{id}/..."""
    parts = topic.split("/")
    if len(parts) >= 5 and parts[0] == "mcgo" and parts[1] == "v1" and parts[2] == "server" and parts[3] == "file":
        return parts[4]
    return None


def extract_chunk_seq(topic: str) -> Optional[int]:
    """Extract chunk sequence number from a chunk topic."""
    parts = topic.split("/")
    if len(parts) >= 7 and parts[5] == "chunk":
        try:
            return int(parts[6])
        except (ValueError, IndexError):
            return None
    return None


# --- Message serialization ---

def serialize_message(data: dict[str, Any]) -> bytes:
    """Serialize a dict to JSON bytes for MQTT payload."""
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


def deserialize_message(payload: bytes) -> dict[str, Any]:
    """Deserialize JSON bytes from MQTT payload to dict."""
    try:
        result = json.loads(payload.decode("utf-8"))
        if not isinstance(result, dict):
            return {}
        return result
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}


def new_file_id() -> str:
    """Generate a unique file transfer ID."""
    return uuid.uuid4().hex
