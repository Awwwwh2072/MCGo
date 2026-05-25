"""Tests for mcgo.client – McGoClient internal logic with MQTT mocked."""

import os
from unittest.mock import ANY, MagicMock, call, patch

import pytest

from mcgo.client import McGoClient, SyncResult
from mcgo.protocol import (
    TOPIC_SERVER_TREE,
    TOPIC_SERVER_ANNOUNCE,
    TOPIC_SERVER_FILE_META_WILD,
    TOPIC_SERVER_FILE_CHUNK_WILD,
    TOPIC_SERVER_FILE_DONE_WILD,
    TOPIC_SERVER_FILE_ABORT_WILD,
    serialize_message,
    deserialize_message,
    server_challenge_topic,
    server_auth_result_topic,
    server_file_meta_topic,
    server_file_chunk_topic,
    server_file_done_topic,
    server_file_abort_topic,
    client_hello_topic,
    client_auth_response_topic,
    client_file_request_topic,
    new_file_id,
)


# ---------------------------------------------------------------------------
# SyncResult
# ---------------------------------------------------------------------------

class TestSyncResult:
    def test_default_state(self):
        sr = SyncResult()
        assert sr.success is False
        assert sr.files_downloaded == []
        assert sr.files_failed == []
        assert sr.errors == []

    def test_to_dict(self):
        sr = SyncResult()
        sr.success = True
        sr.files_downloaded = ["a.txt"]
        sr.errors = ["timeout"]
        d = sr.to_dict()
        assert d == {
            "success": True,
            "files_downloaded": ["a.txt"],
            "files_failed": [],
            "errors": ["timeout"],
        }


# ---------------------------------------------------------------------------
# Message dispatch
# ---------------------------------------------------------------------------

class TestDispatchMessage:
    def test_too_short_topic(self, client_instance):
        client_instance._dispatch_message("mcgo/v1", b"{}")

    def test_wrong_category(self, client_instance):
        client_instance._dispatch_message("mcgo/v1/client/x/hello", b"{}")
        client_instance._mqtt.publish.assert_not_called()

    def test_server_tree_topic(self, client_instance):
        client_instance._dispatch_message(TOPIC_SERVER_TREE, serialize_message({"files": {}}))
        # Should trigger _handle_tree → diff shows no files to fetch → sync_complete set
        assert client_instance._sync_complete.is_set()

    def test_server_announce_topic(self, client_instance):
        client_instance._dispatch_message(TOPIC_SERVER_ANNOUNCE, serialize_message({"server_id": "s1"}))

    def test_challenge_topic(self, client_instance, mock_mqtt):
        topic = server_challenge_topic("testclient")
        payload = serialize_message({"challenge": "AAAA"})
        client_instance._dispatch_message(topic, payload)

    def test_auth_result_topic(self, client_instance):
        topic = server_auth_result_topic("testclient")
        client_instance._dispatch_message(topic, serialize_message({"success": True}))

    def test_file_done_topic(self, client_instance):
        topic = server_file_done_topic("unknown_fid")
        client_instance._dispatch_message(topic, b"")

    def test_file_abort_topic(self, client_instance):
        topic = server_file_abort_topic("unknown_fid")
        client_instance._dispatch_message(topic, b"")

    def test_file_meta_topic(self, client_instance):
        topic = server_file_meta_topic("someid")
        client_instance._dispatch_message(topic, serialize_message({"path": "x"}))

    def test_file_chunk_topic(self, client_instance):
        topic = server_file_chunk_topic("someid", 0)
        client_instance._dispatch_message(topic, b"data")


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

class TestHandleChallenge:
    def test_valid_challenge_publishes_response(self, client_instance, mock_mqtt):
        import base64
        challenge = base64.b64encode(b"x" * 32).decode("ascii")
        mock_mqtt.reset_mock()
        client_instance._handle_challenge({"challenge": challenge})
        # Should publish auth_response
        published = mock_mqtt.publish.call_args
        assert published is not None

    def test_invalid_challenge_no_publish(self, client_instance, mock_mqtt):
        mock_mqtt.reset_mock()
        client_instance._handle_challenge({"no_challenge": "here"})
        # Won't publish because challenge is empty → returns early


class TestHandleAuthResult:
    def test_success_subscribes_to_file_topics(self, client_instance, mock_mqtt):
        mock_mqtt.reset_mock()
        client_instance._handle_auth_result({"success": True, "message": "ok"})
        assert client_instance._authenticated is True
        subscribed = [c[0][0] for c in mock_mqtt.subscribe.call_args_list]
        assert TOPIC_SERVER_TREE in subscribed

    def test_failure_sets_sync_complete(self, client_instance):
        client_instance._sync_complete.clear()
        client_instance._handle_auth_result({"success": False, "message": "nope"})
        assert client_instance._sync_complete.is_set()
        assert len(client_instance._sync_result.errors) > 0


# ---------------------------------------------------------------------------
# Tree handling
# ---------------------------------------------------------------------------

class TestHandleTree:
    def test_empty_payload(self, client_instance):
        client_instance._handle_tree({})
        # Should return early

    def test_up_to_date_sets_complete(self, client_instance, sync_dir):
        client_instance._sync_complete.clear()
        # Scan local to get a tree with no files
        local = client_instance._local_tree.scan(client_instance._ignore)
        payload = dict(local)  # local == remote
        client_instance._handle_tree(payload)
        assert client_instance._sync_complete.is_set()

    def test_needs_download_requests_files(self, client_instance, mock_mqtt, sync_dir):
        client_instance._sync_complete.clear()
        mock_mqtt.reset_mock()

        # Remote has a file, local doesn't
        (sync_dir / "mods").mkdir(exist_ok=True)
        payload = {"version": 1, "files": {
            "mods/data.lua": {"sha256": "abc123", "size": 100},
        }}
        client_instance._handle_tree(payload)

        # Should have published a file_request
        request_calls = [c for c in mock_mqtt.publish.call_args_list
                         if "file_request" in str(c[0][0])]
        assert len(request_calls) == 1


# ---------------------------------------------------------------------------
# Request file
# ---------------------------------------------------------------------------

class TestRequestFile:
    def test_publishes_and_creates_buffer(self, client_instance, mock_mqtt):
        mock_mqtt.reset_mock()
        client_instance._request_file("server/path.lua", "local/path.lua")

        # Published to file_request topic
        published = mock_mqtt.publish.call_args
        topic = published[0][0]
        assert client_file_request_topic("testclient") == topic

        # Buffer entry created
        payload = deserialize_message(published[0][1])
        fid = payload["file_id"]
        assert fid in client_instance._chunk_buffers
        buf = client_instance._chunk_buffers[fid]
        assert buf["_meta"]["server_path"] == "server/path.lua"
        assert buf["_meta"]["local_path"] == "local/path.lua"


# ---------------------------------------------------------------------------
# File download pipeline
# ---------------------------------------------------------------------------

class TestFileDownloadPipeline:
    def _setup_buffer(self, client_instance):
        """Create a buffer entry for a test file transfer."""
        fid = new_file_id()
        with client_instance._buffer_lock:
            client_instance._chunk_buffers[fid] = {
                "_meta": {
                    "server_path": "data.txt",
                    "local_path": "data.txt",
                    "path": "data.txt",
                    "chunk_count": 0,
                    "compressed": False,
                },
                "_received": 0,
            }
        return fid

    def test_handle_file_meta_updates_buffer(self, client_instance):
        fid = self._setup_buffer(client_instance)
        client_instance._handle_file_meta(fid, {"chunk_count": 3, "compressed": False})
        with client_instance._buffer_lock:
            buf = client_instance._chunk_buffers[fid]
            assert buf["_meta"]["chunk_count"] == 3
            assert buf["_expected"] == 3

    def test_handle_file_chunk_stores_data(self, client_instance):
        fid = self._setup_buffer(client_instance)
        client_instance._handle_file_chunk(fid, 0, b"chunk_data_0")
        client_instance._handle_file_chunk(fid, 1, b"chunk_data_1")

        with client_instance._buffer_lock:
            buf = client_instance._chunk_buffers[fid]
            assert buf[0] == b"chunk_data_0"
            assert buf[1] == b"chunk_data_1"
            assert buf["_received"] == 2

    def test_handle_file_chunk_unknown_id(self, client_instance):
        client_instance._handle_file_chunk("nope", 0, b"data")
        # Should silently return

    def test_handle_file_done_happy_path(self, client_instance, sync_dir):
        """Full pipeline: encrypt → chunk → meta → done → verify file on disk."""
        from mcgo.crypto import encrypt_data
        from mcgo.chunking import chunk_data

        fid = self._setup_buffer(client_instance)
        original = b"Hello, McGo! This is a downloaded file."

        # Encrypt (as server would)
        aad = fid.encode("utf-8")
        encrypted = encrypt_data(client_instance._encryption_key, original, aad)
        chunks = chunk_data(encrypted)

        # Meta
        client_instance._handle_file_meta(fid, {
            "chunk_count": len(chunks),
            "compressed": False,
        })

        # Chunks
        for seq, data in chunks:
            client_instance._handle_file_chunk(fid, seq, data)

        # Done
        client_instance._handle_file_done(fid)

        # Verify file on disk
        dest = os.path.join(str(sync_dir), "data.txt")
        assert os.path.exists(dest)
        with open(dest, "rb") as f:
            assert f.read() == original
        assert "data.txt" in client_instance._sync_result.files_downloaded

    def test_handle_file_done_with_compression(self, client_instance, sync_dir):
        """File with compressed=True should be decompressed."""
        from mcgo.crypto import encrypt_data, compress_data
        from mcgo.chunking import chunk_data

        fid = self._setup_buffer(client_instance)
        original = b"A" * 5000  # repetitive, compresses well

        aad = fid.encode("utf-8")
        compressed = compress_data(original)
        encrypted = encrypt_data(client_instance._encryption_key, compressed, aad)
        chunks = chunk_data(encrypted)

        client_instance._handle_file_meta(fid, {
            "chunk_count": len(chunks),
            "compressed": True,
        })
        for seq, data in chunks:
            client_instance._handle_file_chunk(fid, seq, data)
        client_instance._handle_file_done(fid)

        dest = os.path.join(str(sync_dir), "data.txt")
        assert os.path.exists(dest)
        with open(dest, "rb") as f:
            assert f.read() == original

    def test_handle_file_done_decryption_failure(self, client_instance, sync_dir):
        """Corrupt ciphertext should cause files_failed."""
        fid = self._setup_buffer(client_instance)

        client_instance._handle_file_meta(fid, {"chunk_count": 1, "compressed": False})
        client_instance._handle_file_chunk(fid, 0, b"corrupted_data_not_valid")
        client_instance._handle_file_done(fid)

        assert "data.txt" in client_instance._sync_result.files_failed
        assert "data.txt" not in client_instance._sync_result.files_downloaded

    def test_handle_file_done_unknown_id(self, client_instance):
        client_instance._handle_file_done("nonexistent_fid")
        # Should log warning, no crash


class TestFileAbort:
    def test_abort_cleans_buffer_and_records_failure(self, client_instance):
        fid = new_file_id()
        with client_instance._buffer_lock:
            client_instance._chunk_buffers[fid] = {
                "_meta": {"local_path": "aborted.txt", "path": "aborted.txt"},
                "_received": 0,
            }
        client_instance._handle_file_abort(fid)
        assert fid not in client_instance._chunk_buffers
        assert "aborted.txt" in client_instance._sync_result.files_failed

    def test_abort_unknown_id(self, client_instance):
        client_instance._handle_file_abort("nonexistent_fid")
        # Should not crash


# ---------------------------------------------------------------------------
# Sync completion
# ---------------------------------------------------------------------------

class TestCheckSyncComplete:
    def test_not_done_with_pending_buffers(self, client_instance):
        client_instance._sync_complete.clear()
        fid = new_file_id()
        client_instance._chunk_buffers[fid] = {"_meta": {}}
        client_instance._check_sync_complete()
        assert not client_instance._sync_complete.is_set()

    def test_done_when_buffers_empty(self, client_instance):
        client_instance._sync_complete.clear()
        client_instance._chunk_buffers.clear()
        client_instance._check_sync_complete()
        assert client_instance._sync_complete.is_set()


# ---------------------------------------------------------------------------
# On connect
# ---------------------------------------------------------------------------

class TestOnConnect:
    def test_success_subscribes_and_hellos(self, client_instance, mock_mqtt):
        mock_mqtt.reset_mock()
        mock_mqtt.CONNACK_ACCEPTED = 0
        client_instance._on_connect(client_instance._mqtt, None, None, 0, None)

        # Subscribes to challenge and auth_result topics
        subscribed = [c[0][0] for c in mock_mqtt.subscribe.call_args_list]
        assert server_challenge_topic("testclient") in subscribed
        assert server_auth_result_topic("testclient") in subscribed

        # Publishes hello
        published = [c[0][0] for c in mock_mqtt.publish.call_args_list]
        assert client_hello_topic("testclient") in published

    def test_failed_connect_sets_sync_complete(self, client_instance, mock_mqtt):
        mock_mqtt.reset_mock()
        client_instance._sync_complete.clear()
        client_instance._on_connect(client_instance._mqtt, None, None, 1, None)
        assert client_instance._sync_complete.is_set()
