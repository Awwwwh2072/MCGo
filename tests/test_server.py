"""Tests for mcgo.server – McGoServer internal logic with MQTT mocked."""

import json
from unittest.mock import MagicMock, ANY, patch

import pytest

from mcgo.protocol import (
    TOPIC_CLIENT_HELLO_WILD,
    TOPIC_CLIENT_AUTH_RESPONSE_WILD,
    TOPIC_CLIENT_FILE_REQUEST_WILD,
    TOPIC_SERVER_ANNOUNCE,
    TOPIC_SERVER_TREE,
    serialize_message,
    deserialize_message,
    server_challenge_topic,
    server_auth_result_topic,
    server_file_meta_topic,
    server_file_done_topic,
    server_file_abort_topic,
    server_file_chunk_topic,
    client_file_request_topic,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _set_authenticated(server, client_id="testclient"):
    """Mark a client as authenticated by monkeypatching ServerAuth."""
    server._auth.is_authenticated = MagicMock(return_value=True)


# ---------------------------------------------------------------------------
# Message dispatch
# ---------------------------------------------------------------------------

class TestDispatchMessage:
    def test_too_short_topic(self, server_instance):
        server_instance._dispatch_message("mcgo/v1/client", b"{}")
        # Should return early, no errors

    def test_wrong_prefix(self, server_instance):
        server_instance._dispatch_message("other/v1/client/alice/hello", b"{}")
        server_instance._mqtt.publish.assert_not_called()

    def test_wrong_second_segment(self, server_instance):
        server_instance._dispatch_message("mcgo/v2/client/alice/hello", b"{}")
        server_instance._mqtt.publish.assert_not_called()

    def test_unknown_action(self, server_instance):
        server_instance._dispatch_message("mcgo/v1/client/alice/unknown", b"{}")
        # Should return silently

    def test_hello_routes_to_handler(self, server_instance, mock_mqtt):
        """hello is dispatched to _handle_hello."""
        payload = serialize_message({"client_id": "testclient"})
        server_instance._dispatch_message("mcgo/v1/client/testclient/hello", payload)
        # Unknown client gets auth_result failure
        mock_mqtt.publish.assert_called()

    def test_auth_response_routes_to_handler(self, server_instance):
        payload = serialize_message({"signature": "bogus"})
        server_instance._dispatch_message("mcgo/v1/client/testclient/auth_response", payload)
        server_instance._mqtt.publish.assert_called()

    def test_file_request_routes_to_handler(self, server_instance):
        payload = serialize_message({"path": "test.txt", "file_id": "abc123"})
        server_instance._dispatch_message("mcgo/v1/client/testclient/file_request", payload)
        # Unauthenticated → no publish for file stuff, but handler is called


# ---------------------------------------------------------------------------
# Hello handling
# ---------------------------------------------------------------------------

class TestHandleHello:
    def test_unknown_client(self, server_instance, mock_mqtt):
        server_instance._handle_hello("unknown_client", {})
        publish_args = mock_mqtt.publish.call_args
        topic = publish_args[0][0]
        payload = publish_args[0][1]
        assert topic == server_auth_result_topic("unknown_client")
        result = deserialize_message(payload)
        assert result["success"] is False

    def test_known_client_gets_challenge(self, server_instance, mock_mqtt, tmp_path,
                                         rsa_public_key_bytes, rsa_private_key_bytes):
        # Register testclient
        keys = tmp_path / "keys"
        keys.mkdir(exist_ok=True)
        pub_path = keys / "client_public.pem"
        pub_path.write_bytes(rsa_public_key_bytes)
        reg_path = tmp_path / "clients.toml"
        pub_safe = str(pub_path).replace("\\", "/")
        reg_path.write_text(f'[client.testclient]\npublic_key_path = "{pub_safe}"\n', encoding="utf-8")
        server_instance._auth._load_clients(str(reg_path))

        mock_mqtt.reset_mock()
        server_instance._handle_hello("testclient", {})
        # Should publish a challenge
        called_topics = [c[0][0] for c in mock_mqtt.publish.call_args_list]
        challenge_topic = server_challenge_topic("testclient")
        assert challenge_topic in called_topics


# ---------------------------------------------------------------------------
# Auth response handling
# ---------------------------------------------------------------------------

class TestHandleAuthResponse:
    def test_failed_auth(self, server_instance, mock_mqtt):
        server_instance._handle_auth_response("testclient", {"signature": "bad"})
        publish_args = mock_mqtt.publish.call_args
        payload = deserialize_message(publish_args[0][1])
        assert payload["success"] is False

    def test_successful_auth_publishes_tree(self, server_instance, mock_mqtt, tmp_path,
                                                   rsa_public_key_bytes, rsa_private_key_bytes):
        # Register testclient
        keys = tmp_path / "keys"
        keys.mkdir(exist_ok=True)
        pub_path = keys / "client_public.pem"
        pub_path.write_bytes(rsa_public_key_bytes)
        reg_path = tmp_path / "clients.toml"
        pub_safe = str(pub_path).replace("\\", "/")
        reg_path.write_text(f'[client.testclient]\npublic_key_path = "{pub_safe}"\n', encoding="utf-8")
        server_instance._auth._load_clients(str(reg_path))

        # Start challenge first, sign it properly, then verify
        from mcgo.crypto import sign_challenge, load_private_key
        import base64

        # Load client private key
        priv_path = str(tmp_path / "keys" / "client_private.pem")
        # Write the private key since the fixture has it but at a different location
        from pathlib import Path as _Path
        _Path(priv_path).write_bytes(rsa_private_key_bytes)
        priv = load_private_key(priv_path)

        challenge_b64 = server_instance._auth.start_challenge("testclient")
        challenge = base64.b64decode(challenge_b64)
        sig = sign_challenge(priv, challenge)
        sig_b64 = base64.b64encode(sig).decode("ascii")

        # Put tree in place so _publish_tree works
        server_instance._file_tree = {"version": 1, "files": {"f.txt": {"sha256": "abc"}}}

        mock_mqtt.reset_mock()
        server_instance._handle_auth_response("testclient", {"signature": sig_b64})

        # Check auth_result success
        found_auth_success = False
        found_tree = False
        for c in mock_mqtt.publish.call_args_list:
            topic = c[0][0]
            if topic == server_auth_result_topic("testclient"):
                payload = deserialize_message(c[0][1])
                if payload.get("success"):
                    found_auth_success = True
            if topic == TOPIC_SERVER_TREE:
                found_tree = True

        assert found_auth_success
        assert found_tree


# ---------------------------------------------------------------------------
# File request handling
# ---------------------------------------------------------------------------

class TestHandleFileRequest:
    def test_unauthenticated_ignored(self, server_instance, mock_mqtt):
        mock_mqtt.reset_mock()
        server_instance._handle_file_request("testclient", {"path": "x.txt", "file_id": "f1"})
        mock_mqtt.publish.assert_not_called()

    def test_missing_path_or_file_id(self, server_instance):
        _set_authenticated(server_instance)
        server_instance._mqtt.reset_mock()
        server_instance._handle_file_request("testclient", {"path": "", "file_id": ""})
        server_instance._mqtt.publish.assert_not_called()

    def test_valid_request(self, server_instance, scan_dir):
        _set_authenticated(server_instance)
        # Create a test file
        (scan_dir / "test.txt").write_text("hello", encoding="utf-8")
        server_instance._file_tree = {
            "version": 1,
            "files": {"test.txt": {"sha256": "abc"}},
        }

        server_instance._handle_file_request("testclient", {
            "path": "test.txt",
            "file_id": "req1",
        })
        # The actual _send_file runs in executor; we test _send_file directly


# ---------------------------------------------------------------------------
# File send logic
# ---------------------------------------------------------------------------

class TestSendFile:
    def test_happy_path(self, server_instance, mock_mqtt, scan_dir):
        (scan_dir / "data.txt").write_text("Hello, test data!", encoding="utf-8")
        mock_mqtt.reset_mock()
        server_instance._send_file("testclient", "fid001", "data.txt")

        # Collect published topics
        topics = [c[0][0] for c in mock_mqtt.publish.call_args_list]
        assert server_file_meta_topic("fid001") in topics
        assert server_file_done_topic("fid001") in topics
        # Should have at least one chunk
        chunk_topics = [t for t in topics if "/chunk/" in t]
        assert len(chunk_topics) >= 1

    def test_path_traversal_prevented(self, server_instance, mock_mqtt):
        mock_mqtt.reset_mock()
        server_instance._send_file("testclient", "fid002", "../../etc/passwd")
        mock_mqtt.publish.assert_called_with(server_file_abort_topic("fid002"), b"", qos=1)

    def test_file_not_found(self, server_instance, mock_mqtt):
        mock_mqtt.reset_mock()
        server_instance._send_file("testclient", "fid003", "nonexistent.txt")
        mock_mqtt.publish.assert_called_with(server_file_abort_topic("fid003"), b"", qos=1)

    def test_compressed_file_flag(self, server_instance, mock_mqtt, scan_dir):
        (scan_dir / "data.txt").write_text("A" * 5000, encoding="utf-8")
        mock_mqtt.reset_mock()
        server_instance._send_file("testclient", "fid004", "data.txt")

        meta_call = [c for c in mock_mqtt.publish.call_args_list
                     if c[0][0] == server_file_meta_topic("fid004")][0]
        meta = deserialize_message(meta_call[0][1])
        # Text file should be compressible
        assert meta.get("compressed") is True

    def test_already_compressed_skips(self, server_instance, mock_mqtt, scan_dir):
        # Create a .zip file (won't recompress)
        p = scan_dir / "archive.zip"
        p.write_bytes(b"PK\x03\x04" + b"\x00" * 1000)  # fake zip header
        mock_mqtt.reset_mock()
        server_instance._send_file("testclient", "fid005", "archive.zip")

        meta_call = [c for c in mock_mqtt.publish.call_args_list
                     if c[0][0] == server_file_meta_topic("fid005")][0]
        meta = deserialize_message(meta_call[0][1])
        assert meta.get("compressed") is False

    def test_chunks_published_in_sequence(self, server_instance, mock_mqtt, scan_dir):
        (scan_dir / "big.txt").write_text("X" * 200000, encoding="utf-8")
        mock_mqtt.reset_mock()
        server_instance._send_file("testclient", "fid006", "big.txt")

        chunk_calls = [c for c in mock_mqtt.publish.call_args_list
                       if "/chunk/" in c[0][0]]
        seqs = []
        for c in chunk_calls:
            topic = c[0][0]
            parts = topic.split("/")
            seqs.append(int(parts[-1]))
        assert seqs == list(range(len(seqs)))


# ---------------------------------------------------------------------------
# Multi-directory file send
# ---------------------------------------------------------------------------

class TestMultiSendFile:
    def test_routes_to_correct_directory(self, multi_server_instance, mock_mqtt, tmp_path):
        # Create a file in the "files" scan entry
        files_dir = tmp_path / "files"
        (files_dir / "data.txt").write_text("from files dir", encoding="utf-8")
        mock_mqtt.reset_mock()
        multi_server_instance._send_file("testclient", "fid100", "files/data.txt")
        meta_call = [c for c in mock_mqtt.publish.call_args_list
                     if c[0][0] == server_file_meta_topic("fid100")]
        assert len(meta_call) == 1

    def test_routes_to_mods_directory(self, multi_server_instance, mock_mqtt, tmp_path):
        mods_dir = tmp_path / "mods"
        (mods_dir / "mod_info.txt").write_text("from mods dir", encoding="utf-8")
        mock_mqtt.reset_mock()
        multi_server_instance._send_file("testclient", "fid101", "mods/mod_info.txt")
        meta_call = [c for c in mock_mqtt.publish.call_args_list
                     if c[0][0] == server_file_meta_topic("fid101")]
        assert len(meta_call) == 1

    def test_unknown_prefix_aborts(self, multi_server_instance, mock_mqtt):
        mock_mqtt.reset_mock()
        multi_server_instance._send_file("testclient", "fid102", "unknown/file.txt")
        mock_mqtt.publish.assert_called_with(server_file_abort_topic("fid102"), b"", qos=1)

    def test_path_traversal_with_prefix(self, multi_server_instance, mock_mqtt):
        mock_mqtt.reset_mock()
        multi_server_instance._send_file("testclient", "fid103", "files/../../../etc/passwd")
        mock_mqtt.publish.assert_called_with(server_file_abort_topic("fid103"), b"", qos=1)


# ---------------------------------------------------------------------------
# Tree management
# ---------------------------------------------------------------------------

class TestTreeManagement:
    def test_build_tree(self, server_instance, scan_dir):
        (scan_dir / "f.txt").write_text("data", encoding="utf-8")
        server_instance._build_tree()
        assert "files" in server_instance._file_tree
        assert "f.txt" in server_instance._file_tree["files"]

    def test_on_filesystem_changed(self, server_instance, mock_mqtt, scan_dir):
        (scan_dir / "g.txt").write_text("more", encoding="utf-8")
        mock_mqtt.reset_mock()
        server_instance._on_filesystem_changed()
        # Should result in a tree publish
        tree_calls = [c for c in mock_mqtt.publish.call_args_list
                      if c[0][0] == TOPIC_SERVER_TREE]
        assert len(tree_calls) >= 1

    def test_publish_tree(self, server_instance, mock_mqtt):
        server_instance._file_tree = {"version": 1, "files": {}}
        mock_mqtt.reset_mock()
        server_instance._publish_tree()
        mock_mqtt.publish.assert_called_with(
            TOPIC_SERVER_TREE, ANY, qos=0, retain=True,
        )

    def test_publish_tree_empty_skips(self, server_instance, mock_mqtt):
        server_instance._file_tree = {}
        mock_mqtt.reset_mock()
        server_instance._publish_tree()
        mock_mqtt.publish.assert_not_called()

    def test_publish_announce(self, server_instance, mock_mqtt):
        mock_mqtt.reset_mock()
        server_instance._publish_announce()
        args = mock_mqtt.publish.call_args
        assert args[0][0] == TOPIC_SERVER_ANNOUNCE
        announce = deserialize_message(args[0][1])
        assert "server_id" in announce
        assert announce["version"] == "0.1.0"


class TestMultiTreeManagement:
    def test_build_tree_with_prefixes(self, multi_server_instance, tmp_path):
        (tmp_path / "files" / "a.txt").write_text("file_a", encoding="utf-8")
        (tmp_path / "mods" / "b.txt").write_text("file_b", encoding="utf-8")
        multi_server_instance._build_tree()
        tree = multi_server_instance._file_tree
        assert "files" in tree
        assert "files/a.txt" in tree["files"]
        assert "mods/b.txt" in tree["files"]
        assert tree.get("base_path") == "multi"

    def test_build_tree_num_scan_entries(self, multi_server_instance):
        assert len(multi_server_instance._scan_entries) == 2
        assert len(multi_server_instance._trees) == 2
        assert len(multi_server_instance._ignore_rules) == 2

    def test_resolve_scan_entry(self, multi_server_instance):
        e = multi_server_instance._resolve_scan_entry("files/data.txt")
        assert e is not None
        assert e.prefix == "files"
        e2 = multi_server_instance._resolve_scan_entry("mods/data.txt")
        assert e2 is not None
        assert e2.prefix == "mods"

    def test_resolve_scan_entry_unknown(self, multi_server_instance):
        assert multi_server_instance._resolve_scan_entry("unknown/x.txt") is None


# ---------------------------------------------------------------------------
# On connect
# ---------------------------------------------------------------------------

class TestOnConnect:
    def test_success(self, server_instance, mock_mqtt):
        mock_mqtt.reset_mock()
        server_instance._mqtt.CONNACK_ACCEPTED = 0
        server_instance._on_connect(server_instance._mqtt, None, None, 0, None)

        # Should subscribe to three wildcard topics
        subscribed = [c[0][0] for c in mock_mqtt.subscribe.call_args_list]
        assert TOPIC_CLIENT_HELLO_WILD in subscribed
        assert TOPIC_CLIENT_AUTH_RESPONSE_WILD in subscribed
        assert TOPIC_CLIENT_FILE_REQUEST_WILD in subscribed

        # Should publish announce and tree
        published = [c[0][0] for c in mock_mqtt.publish.call_args_list]
        assert TOPIC_SERVER_ANNOUNCE in published

    def test_failed_connect(self, server_instance, mock_mqtt):
        mock_mqtt.reset_mock()
        server_instance._on_connect(server_instance._mqtt, None, None, 1, None)
        mock_mqtt.subscribe.assert_not_called()
        mock_mqtt.publish.assert_not_called()


# ---------------------------------------------------------------------------
# On disconnect
# ---------------------------------------------------------------------------

class TestOnDisconnect:
    def test_normal_disconnect(self, server_instance):
        server_instance._on_disconnect(None, None, None, 0, None)
        # Should log, no error

    def test_unexpected_disconnect(self, server_instance):
        server_instance._on_disconnect(None, None, None, 1, None)
        # Should log warning, no exception
