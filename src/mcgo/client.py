"""McGo client: MQTT broker connection, authentication, tree sync, file download."""

from __future__ import annotations

import base64
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

import paho.mqtt.client as mqtt

from .auth import ClientAuth
from .chunking import reassemble_chunks
from .config import ClientConfig, load_client_config
from .crypto import decompress_data, decrypt_data
from .ignore import IgnoreRules
from .protocol import (
    TOPIC_SERVER_ANNOUNCE,
    TOPIC_SERVER_TREE,
    TOPIC_SERVER_AUTH_RESULT_WILD,
    TOPIC_SERVER_CHALLENGE_WILD,
    TOPIC_SERVER_FILE_ABORT_WILD,
    TOPIC_SERVER_FILE_CHUNK_WILD,
    TOPIC_SERVER_FILE_DONE_WILD,
    TOPIC_SERVER_FILE_META_WILD,
    client_auth_response_topic,
    client_file_request_topic,
    client_hello_topic,
    client_status_topic,
    deserialize_message,
    extract_file_id,
    extract_chunk_seq,
    new_file_id,
    serialize_message,
    server_auth_result_topic,
    server_challenge_topic,
)
from .tree import FileTree

logger = logging.getLogger("mcgo.client")


class SyncResult:
    """Result of a sync operation – returned for C# interop."""
    def __init__(self):
        self.success: bool = False
        self.files_downloaded: list[str] = []
        self.files_failed: list[str] = []
        self.errors: list[str] = []

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "files_downloaded": self.files_downloaded,
            "files_failed": self.files_failed,
            "errors": self.errors,
        }


class McGoClient:
    """MQTT-based file sync client. Can be used from CLI or instantiated directly for C# interop."""

    def __init__(self, config_path: str):
        self._config = load_client_config(config_path)
        self._setup_logging()
        self._config_dir = os.path.dirname(os.path.abspath(config_path))

        self._encryption_key = base64.b64decode(self._config.encryption_key)
        self._auth = ClientAuth(self._config.client_private_key)
        self._local_tree = FileTree(self._config.sync_directory)
        self._ignore = IgnoreRules(
            os.path.join(self._config_dir, self._config.ignore_file),
            self._config.sync_directory,
        )
        self._remote_tree: dict = {}
        self._authenticated = False
        self._auth_event = threading.Event()
        self._auth_success = False
        self._sync_complete = threading.Event()
        self._running = False

        # Chunk buffer: file_id -> {seq: bytes, ..., "_meta": dict}
        self._chunk_buffers: dict[str, dict] = {}
        self._buffer_lock = threading.Lock()

        # Sync result
        self._sync_result = SyncResult()

        client_id = f"{self._config.client_id}-{os.getpid()}"
        self._mqtt = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        self._mqtt.on_connect = self._on_connect
        self._mqtt.on_message = self._on_message
        self._mqtt.on_disconnect = self._on_disconnect

    def _setup_logging(self) -> None:
        level = getattr(logging, self._config.log_level.upper(), logging.INFO)
        handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
        if self._config.log_file:
            handlers.append(logging.FileHandler(self._config.log_file, encoding="utf-8"))
        logging.basicConfig(
            level=level,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            handlers=handlers,
        )

    # --- Public API ---

    def sync(self, timeout: float = 60.0) -> dict:
        """One-shot sync: connect, auth, fetch tree, download missing files, return result dict."""
        self._sync_result = SyncResult()
        self._sync_complete.clear()

        sync_dir = Path(self._config.sync_directory)
        sync_dir.mkdir(parents=True, exist_ok=True)

        try:
            self._mqtt.connect(self._config.mqtt_host, self._config.mqtt_port, keepalive=60)
        except Exception as e:
            logger.error(f"Failed to connect to MQTT broker: {e}")
            self._sync_result.errors.append(str(e))
            return self._sync_result.to_dict()

        self._mqtt.loop_start()
        self._running = True

        # Wait for sync to complete or timeout
        if not self._sync_complete.wait(timeout=timeout):
            logger.warning("Sync timed out")
            self._sync_result.errors.append("Sync timed out")

        self._sync_result.success = len(self._sync_result.files_downloaded) > 0 or (
            len(self._sync_result.files_failed) == 0 and len(self._sync_result.errors) == 0
        )

        self._mqtt.loop_stop()
        self._mqtt.disconnect()
        self._running = False
        return self._sync_result.to_dict()

    def start_watch(self) -> None:
        """Continuous watch mode: stay connected and react to tree updates."""
        sync_dir = Path(self._config.sync_directory)
        sync_dir.mkdir(parents=True, exist_ok=True)

        try:
            self._mqtt.connect(self._config.mqtt_host, self._config.mqtt_port, keepalive=60)
        except Exception as e:
            logger.error(f"Failed to connect to MQTT broker: {e}")
            sys.exit(1)

        self._mqtt.loop_start()
        self._running = True
        logger.info("McGo client is running in watch mode. Press Ctrl+C to stop.")

        try:
            while self._running:
                time.sleep(1)
                if self._authenticated:
                    self._publish_status()
        except KeyboardInterrupt:
            self.stop()

    def stop(self) -> None:
        """Graceful shutdown."""
        logger.info("Shutting down McGo client...")
        self._running = False
        self._mqtt.loop_stop()
        self._mqtt.disconnect()
        logger.info("McGo client stopped.")

    # --- MQTT Callbacks ---

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc != mqtt.CONNACK_ACCEPTED:
            logger.error(f"MQTT connection failed with code {rc}")
            self._sync_complete.set()
            return

        logger.info(f"Connected to MQTT broker at {self._config.mqtt_host}:{self._config.mqtt_port}")

        # Subscribe to server topics for this client
        challenge_topic = server_challenge_topic(self._config.client_id)
        result_topic = server_auth_result_topic(self._config.client_id)
        client.subscribe(challenge_topic, qos=2)
        client.subscribe(result_topic, qos=2)

        # Send hello to initiate authentication
        hello = serialize_message({"client_id": self._config.client_id})
        client.publish(client_hello_topic(self._config.client_id), hello, qos=2)

    def _on_disconnect(self, client, userdata, flags, rc, properties=None):
        if rc != 0:
            logger.warning(f"Disconnected from MQTT broker (rc={rc})")
        self._sync_complete.set()

    def _on_message(self, client, userdata, msg):
        try:
            self._dispatch_message(msg.topic, msg.payload)
        except Exception as e:
            logger.error(f"Error handling message on {msg.topic}: {e}")

    def _dispatch_message(self, topic: str, payload: bytes) -> None:
        parts = topic.split("/")
        if len(parts) < 4 or parts[0] != "mcgo" or parts[1] != "v1":
            return

        category = parts[2]  # "server"

        if category != "server":
            return

        if topic == TOPIC_SERVER_TREE:
            data = deserialize_message(payload)
            self._handle_tree(data)

        elif topic == TOPIC_SERVER_ANNOUNCE:
            data = deserialize_message(payload)
            logger.info(f"Server announce: {data}")

        elif topic.endswith("/done"):
            file_id = extract_file_id(topic)
            if file_id:
                self._handle_file_done(file_id)

        elif topic.endswith("/abort"):
            file_id = extract_file_id(topic)
            if file_id:
                self._handle_file_abort(file_id)

        elif "/chunk/" in topic:
            file_id = extract_file_id(topic)
            seq = extract_chunk_seq(topic)
            if file_id is not None and seq is not None:
                self._handle_file_chunk(file_id, seq, payload)

        elif topic.endswith("/meta"):
            file_id = extract_file_id(topic)
            if file_id:
                data = deserialize_message(payload)
                self._handle_file_meta(file_id, data)

        elif "/challenge/" in topic:
            data = deserialize_message(payload)
            self._handle_challenge(data)

        elif "/auth_result/" in topic:
            data = deserialize_message(payload)
            self._handle_auth_result(data)

    # --- Authentication ---

    def _handle_challenge(self, payload: dict) -> None:
        challenge = self._auth.handle_challenge(payload)
        if challenge is None:
            logger.error("Invalid challenge received")
            return

        response = self._auth.build_response()
        self._mqtt.publish(
            client_auth_response_topic(self._config.client_id),
            serialize_message(response),
            qos=2,
        )

    def _handle_auth_result(self, payload: dict) -> None:
        success = payload.get("success", False)
        message = payload.get("message", "")

        if success:
            logger.info(f"Authentication successful: {message}")
            self._authenticated = True
            # Subscribe to tree topic
            self._mqtt.subscribe(TOPIC_SERVER_TREE, qos=0)
            # Subscribe to file transfer topics
            self._mqtt.subscribe(TOPIC_SERVER_FILE_META_WILD, qos=1)
            self._mqtt.subscribe(TOPIC_SERVER_FILE_CHUNK_WILD, qos=1)
            self._mqtt.subscribe(TOPIC_SERVER_FILE_DONE_WILD, qos=1)
            self._mqtt.subscribe(TOPIC_SERVER_FILE_ABORT_WILD, qos=1)
        else:
            logger.error(f"Authentication failed: {message}")
            self._sync_result.errors.append(f"Authentication failed: {message}")
            self._sync_complete.set()

    # --- File tree handling ---

    def _handle_tree(self, payload: dict) -> None:
        if not payload or "files" not in payload:
            return

        logger.info(f"Received file tree: {len(payload.get('files', {}))} files")
        self._remote_tree = payload

        # Build local tree
        local = self._local_tree.scan(self._ignore)

        # Diff
        to_fetch = FileTree.diff(local, payload)

        if not to_fetch:
            logger.info("All files up to date.")
            self._sync_complete.set()
            return

        logger.info(f"Need to fetch {len(to_fetch)} files")
        for entry in to_fetch:
            self._request_file(entry["path"])

    def _request_file(self, file_path: str) -> None:
        file_id = new_file_id()
        request = serialize_message({"file_id": file_id, "path": file_path})
        self._mqtt.publish(
            client_file_request_topic(self._config.client_id),
            request,
            qos=1,
        )

        with self._buffer_lock:
            self._chunk_buffers[file_id] = {
                "_meta": {"path": file_path, "chunk_count": 0, "compressed": False},
                "_received": 0,
            }

    # --- File chunk handling ---

    def _handle_file_meta(self, file_id: str, payload: dict) -> None:
        with self._buffer_lock:
            if file_id in self._chunk_buffers:
                self._chunk_buffers[file_id]["_meta"] = payload
                # Pre-allocate space for expected chunks
                self._chunk_buffers[file_id]["_expected"] = payload.get("chunk_count", 0)
        logger.debug(f"File meta: {payload.get('path')} ({payload.get('chunk_count')} chunks)")

    def _handle_file_chunk(self, file_id: str, seq: int, payload: bytes) -> None:
        with self._buffer_lock:
            if file_id not in self._chunk_buffers:
                return
            self._chunk_buffers[file_id][seq] = payload
            self._chunk_buffers[file_id]["_received"] += 1

    def _handle_file_done(self, file_id: str) -> None:
        with self._buffer_lock:
            buf = self._chunk_buffers.pop(file_id, None)

        if buf is None:
            logger.warning(f"Got done for unknown file_id: {file_id}")
            return

        meta = buf.pop("_meta", {})
        buf.pop("_received", None)
        buf.pop("_expected", None)

        file_path = meta.get("path", "unknown")
        compressed = meta.get("compressed", False)

        # Reassemble chunks
        try:
            data = reassemble_chunks({k: v for k, v in buf.items() if isinstance(k, int)})
        except Exception as e:
            logger.error(f"Failed to reassemble chunks for {file_path}: {e}")
            self._sync_result.files_failed.append(file_path)
            self._check_sync_complete()
            return

        # Decrypt
        aad = file_id.encode("utf-8")
        try:
            data = decrypt_data(self._encryption_key, data, aad)
        except Exception as e:
            logger.error(f"Failed to decrypt {file_path}: {e}")
            self._sync_result.files_failed.append(file_path)
            self._check_sync_complete()
            return

        # Decompress
        if compressed:
            try:
                data = decompress_data(data)
            except Exception as e:
                logger.error(f"Failed to decompress {file_path}: {e}")
                self._sync_result.files_failed.append(file_path)
                self._check_sync_complete()
                return

        # Write to disk
        dest = os.path.join(self._config.sync_directory, file_path)
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(data)
            logger.info(f"Downloaded: {file_path} ({len(data)} bytes)")
            self._sync_result.files_downloaded.append(file_path)
        except Exception as e:
            logger.error(f"Failed to write {file_path}: {e}")
            self._sync_result.files_failed.append(file_path)

        self._check_sync_complete()

    def _handle_file_abort(self, file_id: str) -> None:
        with self._buffer_lock:
            buf = self._chunk_buffers.pop(file_id, None)
        if buf:
            path = buf.get("_meta", {}).get("path", "unknown")
            logger.warning(f"File transfer aborted: {path}")
            self._sync_result.files_failed.append(path)
        self._check_sync_complete()

    def _check_sync_complete(self) -> None:
        """Check if all requested files have been processed and signal completion."""
        with self._buffer_lock:
            if self._chunk_buffers:
                return
        # All buffers cleared – sync is done
        self._sync_complete.set()

    # --- Status ---

    def _publish_status(self) -> None:
        status = serialize_message({
            "client_id": self._config.client_id,
            "display_name": self._config.display_name,
            "timestamp": time.time(),
            "authenticated": self._authenticated,
        })
        self._mqtt.publish(
            client_status_topic(self._config.client_id),
            status,
            qos=0,
        )
