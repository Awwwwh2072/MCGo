"""Tests for mcgo.chunking – file chunking and reassembly."""

from mcgo.chunking import calculate_chunk_count, chunk_data, reassemble_chunks


class TestChunkData:
    def test_exact_multiples(self):
        data = b"A" * 131072  # exactly 2 * 64KB
        chunks = chunk_data(data)
        assert len(chunks) == 2
        assert chunks[0] == (0, b"A" * 65536)
        assert chunks[1] == (1, b"A" * 65536)

    def test_partial_last_chunk(self):
        data = b"B" * 70000  # 64KB + 4464 bytes
        chunks = chunk_data(data)
        assert len(chunks) == 2
        assert len(chunks[0][1]) == 65536
        assert len(chunks[1][1]) == 70000 - 65536

    def test_empty_data(self):
        assert chunk_data(b"") == []

    def test_smaller_than_chunk_size(self):
        data = b"hello"
        chunks = chunk_data(data)
        assert len(chunks) == 1
        assert chunks[0] == (0, b"hello")

    def test_custom_chunk_size(self):
        data = b"C" * 250
        chunks = chunk_data(data, chunk_size=100)
        assert len(chunks) == 3
        assert chunks[0] == (0, b"C" * 100)
        assert chunks[1] == (1, b"C" * 100)
        assert chunks[2] == (2, b"C" * 50)

    def test_sequence_numbers_are_sequential(self):
        data = b"D" * 200000
        chunks = chunk_data(data)
        for i, (seq, _) in enumerate(chunks):
            assert seq == i


class TestReassembleChunks:
    def test_in_order(self):
        assert reassemble_chunks({0: b"ab", 1: b"cd"}) == b"abcd"

    def test_out_of_order(self):
        assert reassemble_chunks({1: b"cd", 0: b"ab"}) == b"abcd"

    def test_single_chunk(self):
        assert reassemble_chunks({0: b"hello"}) == b"hello"

    def test_empty_dict(self):
        assert reassemble_chunks({}) == b""

    def test_sparse_sequence(self):
        assert reassemble_chunks({0: b"AB", 5: b"XY"}) == b"ABXY"


class TestCalculateChunkCount:
    def test_exact_fit(self):
        assert calculate_chunk_count(65536) == 1

    def test_one_byte_over(self):
        assert calculate_chunk_count(65537) == 2

    def test_zero_size(self):
        assert calculate_chunk_count(0) == 0

    def test_custom_chunk_size(self):
        assert calculate_chunk_count(200, chunk_size=100) == 2

    def test_empty_with_custom_size(self):
        assert calculate_chunk_count(0, chunk_size=100) == 0


class TestChunkReassembleIntegration:
    def test_roundtrip_exact(self, tmp_path):
        """Chunk arbitrary data and reassemble it."""
        original = bytes(range(256)) * 500  # 128000 bytes, not exact multiple
        chunks = chunk_data(original)
        chunk_dict = {seq: data for seq, data in chunks}
        assert reassemble_chunks(chunk_dict) == original

    def test_roundtrip_large(self):
        original = b"L" * 500_000
        chunks = chunk_data(original)
        chunk_dict = {seq: data for seq, data in chunks}
        assert reassemble_chunks(chunk_dict) == original

    def test_roundtrip_with_custom_size(self):
        original = b"Z" * 1234
        chunks = chunk_data(original, chunk_size=200)
        chunk_dict = {seq: data for seq, data in chunks}
        assert reassemble_chunks(chunk_dict) == original
