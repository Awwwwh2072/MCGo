"""Tests for mcgo.protocol – MQTT topics and message serialization."""

import json

from mcgo.protocol import (
    client_auth_response_topic,
    client_file_request_topic,
    client_hello_topic,
    client_status_topic,
    deserialize_message,
    extract_chunk_seq,
    extract_client_id,
    extract_file_id,
    new_file_id,
    serialize_message,
    server_auth_result_topic,
    server_challenge_topic,
    server_file_abort_topic,
    server_file_chunk_topic,
    server_file_done_topic,
    server_file_meta_topic,
)


# ---------------------------------------------------------------------------
# Topic builders
# ---------------------------------------------------------------------------

class TestServerTopicBuilders:
    def test_challenge_topic(self):
        assert server_challenge_topic("alice") == "mcgo/v1/server/challenge/alice"

    def test_auth_result_topic(self):
        assert server_auth_result_topic("alice") == "mcgo/v1/server/auth_result/alice"

    def test_file_meta_topic(self):
        assert server_file_meta_topic("abc123") == "mcgo/v1/server/file/abc123/meta"

    def test_file_chunk_topic(self):
        assert server_file_chunk_topic("fid", 3) == "mcgo/v1/server/file/fid/chunk/3"

    def test_file_done_topic(self):
        assert server_file_done_topic("fid") == "mcgo/v1/server/file/fid/done"

    def test_file_abort_topic(self):
        assert server_file_abort_topic("fid") == "mcgo/v1/server/file/fid/abort"


class TestClientTopicBuilders:
    def test_hello_topic(self):
        assert client_hello_topic("mybox") == "mcgo/v1/client/mybox/hello"

    def test_auth_response_topic(self):
        assert client_auth_response_topic("mybox") == "mcgo/v1/client/mybox/auth_response"

    def test_file_request_topic(self):
        assert client_file_request_topic("mybox") == "mcgo/v1/client/mybox/file_request"

    def test_status_topic(self):
        assert client_status_topic("mybox") == "mcgo/v1/client/mybox/status"


# ---------------------------------------------------------------------------
# Client ID extraction
# ---------------------------------------------------------------------------

class TestExtractClientId:
    def test_valid_topic(self):
        assert extract_client_id("mcgo/v1/client/mybox/hello") == "mybox"

    def test_different_action(self):
        assert extract_client_id("mcgo/v1/client/abc/auth_response") == "abc"

    def test_invalid_prefix(self):
        assert extract_client_id("other/v1/client/foo/hello") is None

    def test_too_short(self):
        assert extract_client_id("mcgo/v1/client") is None

    def test_wrong_version(self):
        assert extract_client_id("mcgo/v2/client/foo/hello") is None


# ---------------------------------------------------------------------------
# File ID extraction
# ---------------------------------------------------------------------------

class TestExtractFileId:
    def test_valid_meta(self):
        assert extract_file_id("mcgo/v1/server/file/abc123/meta") == "abc123"

    def test_valid_chunk(self):
        assert extract_file_id("mcgo/v1/server/file/abc123/chunk/5") == "abc123"

    def test_valid_done(self):
        assert extract_file_id("mcgo/v1/server/file/abc123/done") == "abc123"

    def test_invalid_too_short(self):
        assert extract_file_id("mcgo/v1/server/file") is None

    def test_invalid_prefix(self):
        assert extract_file_id("mcgo/v1/client/file/abc/meta") is None


# ---------------------------------------------------------------------------
# Chunk sequence extraction
# ---------------------------------------------------------------------------

class TestExtractChunkSeq:
    def test_valid(self):
        assert extract_chunk_seq("mcgo/v1/server/file/abc/chunk/42") == 42

    def test_zero_seq(self):
        assert extract_chunk_seq("mcgo/v1/server/file/abc/chunk/0") == 0

    def test_not_a_number(self):
        assert extract_chunk_seq("mcgo/v1/server/file/abc/chunk/foo") is None

    def test_not_chunk_topic(self):
        assert extract_chunk_seq("mcgo/v1/server/file/abc/meta") is None

    def test_too_short(self):
        assert extract_chunk_seq("mcgo/v1/server/file/abc/chunk") is None


# ---------------------------------------------------------------------------
# Message serialization
# ---------------------------------------------------------------------------

class TestSerializeDeserialize:
    def test_roundtrip(self):
        data = {"key": "value", "number": 42}
        serialized = serialize_message(data)
        assert isinstance(serialized, bytes)
        assert deserialize_message(serialized) == data

    def test_invalid_json(self):
        assert deserialize_message(b"not json") == {}

    def test_non_dict_json(self):
        payload = json.dumps([1, 2, 3]).encode("utf-8")
        assert deserialize_message(payload) == {}

    def test_unicode(self):
        data = {"name": "测试", "emoji": "🚀"}
        assert deserialize_message(serialize_message(data)) == data

    def test_empty_dict(self):
        assert deserialize_message(serialize_message({})) == {}


# ---------------------------------------------------------------------------
# File ID generation
# ---------------------------------------------------------------------------

class TestNewFileId:
    def test_is_hex_string(self):
        fid = new_file_id()
        assert len(fid) == 32
        int(fid, 16)  # does not raise

    def test_are_unique(self):
        ids = {new_file_id() for _ in range(100)}
        assert len(ids) == 100
