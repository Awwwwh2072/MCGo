"""McGo server: MQTT broker connection, authentication, file tree management, file serving."""

from __future__ import annotations

import base64
import logging
import os
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional

import paho.mqtt.client as mqtt

from .auth import ServerAuth
from .chunking import chunk_data
from .config import ServerConfig, load_server_config
from .crypto import compress_data, decompress_data, encrypt_data
from .ignore import IgnoreRules
from .protocol import (
    TOPIC_CLIENT_HELLO_WILD,
    TOPIC_CLIENT_AUTH_RESPONSE_WILD,
    TOPIC_CLIENT_FILE_REQUEST_WILD,
    TOPIC_SERVER_TREE,
    TOPIC_SERVER_ANNOUNCE,
    client_file_request_topic,
    server_auth_result_topic,
    server_challenge_topic,
    server_file_abort_topic,
    server_file_chunk_topic,
    server_file_done_topic,
    server_file_meta_topic,
    deserialize_message,
    extract_client_id,
    new_file_id,
    serialize_message,
)
from .tree import FileTree
from .watcher import DirectoryWatcher

logger = logging.getLogger("mcgo.server")


class McGoServer:
    """MQTT-based file synchronization server."""

    def __init__(self, config_path: str):
        self._config = load_server_config(config_path)
        self._setup_logging()

        self._encryption_key = base64.b64decode(self._config.encryption_key)
        self._auth = ServerAuth(self._config.clients_file, self._config.challenge_timeout_seconds)
        self._tree = FileTree(self._config.scan_directory)
        self._ignore = IgnoreRules(
            os.path.join(os.path.dirname(config_path), self._config.ignore_file),
            self._config.scan_directory,
        )
        self._file_tree: dict = {}
        self._tree_lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=4)
        self._running = False

        client_id = f"mcgo-server-{os.getpid()}"
        self._mqtt = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        self._mqtt.on_connect = self._on_connect
        self._mqtt.on_message = self._on_message
        self._mqtt.on_disconnect = self._on_disconnect

        self._watcher: Optional[DirectoryWatcher] = None

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

    # --- Lifecycle ---

    def start(self) -> None:
        """Start the server: connect to broker, build initial tree, start watcher, enter event loop."""
        logger.info(f"Starting McGo server, scanning: {self._config.scan_directory}")

        # Ensure scan directory exists
        scan_dir = Path(self._config.scan_directory)
        scan_dir.mkdir(parents=True, exist_ok=True)

        # Build initial file tree
        self._build_tree()

        # Connect to MQTT broker
        try:
            self._mqtt.connect(self._config.mqtt_host, self._config.mqtt_port, keepalive=60)
        except Exception as e:
            logger.error(f"Failed to connect to MQTT broker: {e}")
            sys.exit(1)

        self._mqtt.loop_start()
        self._running = True

        # Start filesystem watcher
        self._watcher = DirectoryWatcher(
            self._config.scan_directory,
            callback=self._on_filesystem_changed,
            ignore_rules=self._ignore,
            debounce_seconds=1.0,
        )
        self._watcher.start()
        logger.info("McGo server is running. Press Ctrl+C to stop.")

        # Service loop – handle timeouts and keep alive
        try:
            while self._running:
                time.sleep(1)
                self._auth.cleanup_timeouts()
        except KeyboardInterrupt:
            self.stop()

    def stop(self) -> None:
        """Graceful shutdown."""
        logger.info("Shutting down McGo server...")
        self._running = False

        if self._watcher:
            self._watcher.stop()

        # Publish empty announce to clear retained
        try:
            self._mqtt.publish(TOPIC_SERVER_ANNOUNCE, b"", qos=0, retain=True)
        except Exception:
            pass

        self._mqtt.loop_stop()
        self._mqtt.disconnect()
        self._executor.shutdown(wait=False)
        logger.info("McGo server stopped.")

    # --- MQTT Callbacks ---

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc != mqtt.CONNACK_ACCEPTED:
            logger.error(f"MQTT connection failed with code {rc}")
            return

        logger.info(f"Connected to MQTT broker at {self._config.mqtt_host}:{self._config.mqtt_port}")

        # Subscribe to client topics
        client.subscribe(TOPIC_CLIENT_HELLO_WILD, qos=2)
        client.subscribe(TOPIC_CLIENT_AUTH_RESPONSE_WILD, qos=2)
        client.subscribe(TOPIC_CLIENT_FILE_REQUEST_WILD, qos=1)

        # Publish announcement and current tree (retained)
        self._publish_announce()
        self._publish_tree()

    def _on_disconnect(self, client, userdata, flags, rc, properties=None):
        if rc != 0:
            logger.warning(f"Disconnected from MQTT broker (rc={rc}), attempting reconnect...")
        else:
            logger.info("Disconnected from MQTT broker.")

    def _on_message(self, client, userdata, msg):
        try:
            self._dispatch_message(msg.topic, msg.payload)
        except Exception as e:
            logger.error(f"Error handling message on {msg.topic}: {e}")

    def _dispatch_message(self, topic: str, payload: bytes) -> None:
        """Route incoming messages to the appropriate handler."""
        parts = topic.split("/")
        # Expect mcgo/v1/client/{client_id}/{action}
        if len(parts) < 5:
            return
        if parts[0] != "mcgo" or parts[1] != "v1" or parts[2] != "client":
            return

        client_id = parts[3]
        action = parts[4]

        if action == "hello":
            data = deserialize_message(payload)
            self._handle_hello(client_id, data)
        elif action == "auth_response":
            data = deserialize_message(payload)
            self._handle_auth_response(client_id, data)
        elif action == "file_request":
            data = deserialize_message(payload)
            self._handle_file_request(client_id, data)

    # --- Authentication ---

    def _handle_hello(self, client_id: str, payload: dict) -> None:
        logger.info(f"Auth hello from client: {client_id}")

        if not self._auth.is_registered(client_id):
            logger.warning(f"Rejecting unknown client: {client_id}")
            result = serialize_message({"success": False, "message": f"Unknown client: {client_id}"})
            self._mqtt.publish(server_auth_result_topic(client_id), result, qos=2)
            return

        try:
            challenge_b64 = self._auth.start_challenge(client_id)
            challenge_msg = serialize_message({
                "challenge": challenge_b64,
                "timestamp": time.time(),
            })
            self._mqtt.publish(server_challenge_topic(client_id), challenge_msg, qos=2)
        except Exception as e:
            logger.error(f"Failed to start challenge for {client_id}: {e}")
            result = serialize_message({"success": False, "message": str(e)})
            self._mqtt.publish(server_auth_result_topic(client_id), result, qos=2)

    def _handle_auth_response(self, client_id: str, payload: dict) -> None:
        signature_b64 = payload.get("signature", "")
        success = self._auth.verify_response(client_id, signature_b64)

        result = serialize_message({
            "success": success,
            "message": "Authentication successful" if success else "Authentication failed",
        })
        self._mqtt.publish(server_auth_result_topic(client_id), result, qos=2)

        if success:
            logger.info(f"Client authenticated: {client_id}")
            # Publish current tree so the client can start syncing
            self._publish_tree()
        else:
            logger.warning(f"Authentication failed for client: {client_id}")

    # --- File requests ---

    def _handle_file_request(self, client_id: str, payload: dict) -> None:
        if not self._auth.is_authenticated(client_id):
            logger.warning(f"File request from unauthenticated client: {client_id}")
            return

        file_path = payload.get("path", "")
        file_id = payload.get("file_id", "")

        if not file_path or not file_id:
            logger.warning(f"Invalid file request from {client_id}: missing path or file_id")
            return

        logger.info(f"File request from {client_id}: {file_path} (fid={file_id})")
        self._executor.submit(self._send_file, client_id, file_id, file_path)

    def _send_file(self, client_id: str, file_id: str, file_path: str) -> None:
        """Load, compress, encrypt, chunk, and send a file to a client."""
        full_path = os.path.join(self._config.scan_directory, file_path)

        # Security: prevent path traversal
        real_full = os.path.realpath(full_path)
        real_scan = os.path.realpath(self._config.scan_directory)
        if not real_full.startswith(real_scan + os.sep) and real_full != real_scan:
            logger.warning(f"Path traversal attempt: {file_path}")
            self._mqtt.publish(server_file_abort_topic(file_id), b"", qos=1)
            return

        if not os.path.isfile(full_path):
            logger.warning(f"File not found: {full_path}")
            self._mqtt.publish(server_file_abort_topic(file_id), b"", qos=1)
            return

        try:
            with open(full_path, "rb") as f:
                data = f.read()
        except Exception as e:
            logger.error(f"Failed to read file {full_path}: {e}")
            self._mqtt.publish(server_file_abort_topic(file_id), b"", qos=1)
            return

        original_size = len(data)

        # Compress if beneficial
        ext = os.path.splitext(file_path)[1].lower()
        from .tree import _COMPRESSED_EXTENSIONS
        should_compress = ext not in _COMPRESSED_EXTENSIONS
        compressed = False
        if should_compress:
            try:
                compressed_data = compress_data(data)
                if len(compressed_data) < len(data):
                    data = compressed_data
                    compressed = True
            except Exception:
                pass  # Fall through with original data

        # Encrypt
        aad = file_id.encode("utf-8")
        try:
            encrypted = encrypt_data(self._encryption_key, data, aad)
        except Exception as e:
            logger.error(f"Encryption failed for {file_path}: {e}")
            self._mqtt.publish(server_file_abort_topic(file_id), b"", qos=1)
            return

        # Chunk
        chunks = chunk_data(encrypted)

        # Send metadata
        meta = serialize_message({
            "file_id": file_id,
            "path": file_path,
            "original_size": original_size,
            "chunk_size": 65536,
            "chunk_count": len(chunks),
            "compressed": compressed,
        })
        self._mqtt.publish(server_file_meta_topic(file_id), meta, qos=1)

        # Send chunks
        for seq, chunk_data_bytes in chunks:
            self._mqtt.publish(server_file_chunk_topic(file_id, seq), chunk_data_bytes, qos=1)

        # Signal completion
        self._mqtt.publish(server_file_done_topic(file_id), b"", qos=1)
        logger.info(f"File sent: {file_path} ({original_size} bytes, {len(chunks)} chunks, compressed={compressed})")

    # --- File tree management ---

    def _build_tree(self) -> None:
        """Scan the directory and rebuild the file tree."""
        try:
            new_tree = self._tree.scan(self._ignore)
            with self._tree_lock:
                self._file_tree = new_tree
            logger.info(f"File tree rebuilt: {len(new_tree.get('files', {}))} files")
        except Exception as e:
            logger.error(f"Failed to build file tree: {e}")

    def _on_filesystem_changed(self) -> None:
        """Callback invoked by the watcher when files change."""
        logger.debug("Filesystem change detected, rebuilding tree...")
        self._build_tree()
        self._publish_tree()

    def _publish_tree(self) -> None:
        """Publish the current file tree as a retained message."""
        with self._tree_lock:
            if not self._file_tree:
                return
            tree_json = self._tree.to_json(self._file_tree)

        self._mqtt.publish(TOPIC_SERVER_TREE, tree_json.encode("utf-8"), qos=0, retain=True)

    def _publish_announce(self) -> None:
        """Publish server announcement as a retained message."""
        announce = serialize_message({
            "server_id": f"mcgo-server-{os.getpid()}",
            "version": "0.1.0",
            "timestamp": time.time(),
        })
        self._mqtt.publish(TOPIC_SERVER_ANNOUNCE, announce, qos=0, retain=True)
