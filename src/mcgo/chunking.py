"""File chunking and reassembly for MQTT-based file transfer."""

from __future__ import annotations

from typing import Generator

DEFAULT_CHUNK_SIZE = 65536  # 64 KB


def chunk_data(data: bytes, chunk_size: int = DEFAULT_CHUNK_SIZE) -> list[tuple[int, bytes]]:
    """Split data into chunks. Returns list of (sequence_number, chunk_bytes)."""
    chunks: list[tuple[int, bytes]] = []
    seq = 0
    for offset in range(0, len(data), chunk_size):
        chunks.append((seq, data[offset:offset + chunk_size]))
        seq += 1
    return chunks


def reassemble_chunks(chunks: dict[int, bytes]) -> bytes:
    """Reassemble chunks into original data. chunks maps seq -> bytes."""
    result = bytearray()
    for seq in sorted(chunks):
        result.extend(chunks[seq])
    return bytes(result)


def calculate_chunk_count(data_size: int, chunk_size: int = DEFAULT_CHUNK_SIZE) -> int:
    """Calculate the number of chunks needed for data of given size."""
    return (data_size + chunk_size - 1) // chunk_size
