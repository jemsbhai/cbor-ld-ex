"""
Security primitives for CBOR-LD-ex.

Implements SECTION9_SECURITY.md and FORMAL_MODEL.md §9:
  - Annotation digest: truncate(SHA-256(annotation_bytes), 64 bits)
  - Byzantine fusion metadata: bit-packed (4 bytes)
  - Provenance chain entries: bit-packed (16 bytes each)
  - Chain verification: chained digests for tamper/truncation detection

Security is orthogonal to Axioms 1–3:
  - Digests and metadata are about annotations, not opinion values
  - No quantization or opinion algebra involved
  - Stripping annotations (Axiom 1) also strips security metadata

Wire formats:

Annotation digest (8 bytes):
  truncate(SHA-256(annotation_bytes), 64 bits)

Byzantine metadata (4 bytes):
  Byte 0:   [original_source_count:8]
  Byte 1:   [removed_count:8]
  Byte 2:   [cohesion_q:8]              (Q8: 0→0.0, 255→1.0)
  Byte 3:   [strategy:2][reserved:6]

Provenance entry (16 bytes — 128 bits, zero waste):
  Byte 0:   [origin_tier:2][operator_id:4][precision_mode:2]
  Byte 1:   b̂ (uint8)
  Byte 2:   d̂ (uint8)
  Byte 3:   â (uint8)
  Bytes 4-7:  timestamp (uint32, big-endian, seconds since Unix epoch)
  Bytes 8-15: prev_digest (64 bits, chained SHA-256)
"""

import hashlib
import struct
from dataclasses import dataclass


# =====================================================================
# Constants
# =====================================================================

# Annotation digest: truncated SHA-256
DIGEST_SIZE_BYTES = 8  # 64 bits

# Byzantine removal strategy codes (2 bits)
STRATEGY_MOST_CONFLICTING = 0  # 00
STRATEGY_LEAST_TRUSTED = 1     # 01
STRATEGY_COMBINED = 2          # 10
# 3 = reserved

# Provenance entry size
PROVENANCE_ENTRY_SIZE = 16  # 128 bits, zero waste

# Chain origin sentinel: 8 zero bytes as prev_digest for the first entry
CHAIN_ORIGIN_SENTINEL = b"\x00" * DIGEST_SIZE_BYTES


# =====================================================================
# Annotation Digest
#
# truncate(SHA-256(annotation_bytes), 64 bits)
# Collision resistance: ~2^32 (birthday bound on 64-bit output).
# Sufficient for IoT message integrity. Tier 3 systems requiring
# stronger guarantees should use full SHA-256 at the application layer.
# =====================================================================

def compute_annotation_digest(annotation_bytes: bytes) -> bytes:
    """Compute truncated SHA-256 digest of annotation bytes.

    Args:
        annotation_bytes: Raw annotation byte string.

    Returns:
        8-byte (64-bit) truncated SHA-256 digest.
    """
    return hashlib.sha256(annotation_bytes).digest()[:DIGEST_SIZE_BYTES]


def verify_annotation_digest(annotation_bytes: bytes, expected: bytes) -> bool:
    """Verify that annotation bytes match an expected digest.

    Args:
        annotation_bytes: Raw annotation byte string.
        expected: Expected 8-byte digest.

    Returns:
        True if digest matches, False otherwise.
    """
    return compute_annotation_digest(annotation_bytes) == expected


# =====================================================================
# Byzantine Fusion Metadata — 4 bytes
#
# Byte 0:   [original_source_count:8]
# Byte 1:   [removed_count:8]
# Byte 2:   [cohesion_q:8]
# Byte 3:   [strategy:2][reserved:6]
#
# 32 bits total. All fields are byte-aligned because the field widths
# (8, 8, 8, 2+6) naturally align. No bit-packing gymnastics needed —
# the layout is efficient as-is.
# =====================================================================

@dataclass
class ByzantineMetadata:
    """Byzantine fusion metadata for Tier 2 annotations.

    Records the outcome of Byzantine-resistant filtering so that
    Tier 3 can assess fusion quality without re-processing raw data.

    Attributes:
        original_count: Number of sources before filtering (0–255).
        removed_count: Number of sources removed (0–255).
        cohesion_q: Quantized group cohesion of survivors (Q8: 0–255).
        strategy: Removal strategy code (0–2).
    """
    original_count: int   # 8 bits
    removed_count: int    # 8 bits
    cohesion_q: int       # 8 bits (Q8: 0=0.0, 255=1.0)
    strategy: int         # 2 bits


def encode_byzantine_metadata(meta: ByzantineMetadata) -> bytes:
    """Encode Byzantine metadata to 4 bytes.

    Byte 3: [strategy:2][000000:6] — strategy in the top 2 bits.
    """
    byte3 = (meta.strategy & 0x03) << 6
    return bytes([
        meta.original_count & 0xFF,
        meta.removed_count & 0xFF,
        meta.cohesion_q & 0xFF,
        byte3,
    ])


def decode_byzantine_metadata(data: bytes) -> ByzantineMetadata:
    """Decode 4 bytes to Byzantine metadata."""
    return ByzantineMetadata(
        original_count=data[0],
        removed_count=data[1],
        cohesion_q=data[2],
        strategy=(data[3] >> 6) & 0x03,
    )


# =====================================================================
# Provenance Entry — 16 bytes (128 bits, zero waste)
#
# Byte 0:     [origin_tier:2][operator_id:4][precision_mode:2]
# Bytes 1-3:  opinion (b̂, d̂, â) — 3 × uint8
# Bytes 4-7:  timestamp (uint32 big-endian, seconds since epoch)
# Bytes 8-15: prev_digest (64-bit chained SHA-256)
#
# Total: 2+4+2 + 24 + 32 + 64 = 128 bits = 16 bytes. Zero waste.
#
# Note: û is NOT stored — derived as 255 − b̂ − d̂ (Axiom 3).
# This matches the opinion wire format: 3 values, not 4.
# =====================================================================

@dataclass
class ProvenanceEntry:
    """A single entry in the provenance chain.

    Records one processing step in the Tier 1 → Tier 2 → Tier 3
    pipeline. The chained prev_digest creates a tamper-evident
    hash chain (Theorem 8, FORMAL_MODEL.md §9.4).

    Attributes:
        origin_tier: Tier that produced this entry (0=constrained, 1=edge, 2=cloud).
        operator_id: Which operator was applied (Table 2, §5.2).
        precision_mode: Opinion quantization precision (0=8-bit, 1=16-bit, 2=32-bit).
        b_q: Quantized belief (uint8 for 8-bit mode).
        d_q: Quantized disbelief (uint8 for 8-bit mode).
        a_q: Quantized base rate (uint8 for 8-bit mode).
        timestamp: Unix epoch seconds (uint32).
        prev_digest: SHA-256 digest of the previous entry (8 bytes).
    """
    origin_tier: int      # 2 bits
    operator_id: int      # 4 bits
    precision_mode: int   # 2 bits
    b_q: int              # 8 bits
    d_q: int              # 8 bits
    a_q: int              # 8 bits
    timestamp: int        # 32 bits
    prev_digest: bytes    # 64 bits


def compute_entry_digest(entry_bytes: bytes) -> bytes:
    """Compute the chained digest for a provenance entry.

    Uses the same truncated SHA-256 as annotation digests.

    Args:
        entry_bytes: 16-byte serialized provenance entry.

    Returns:
        8-byte truncated SHA-256 digest.
    """
    return hashlib.sha256(entry_bytes).digest()[:DIGEST_SIZE_BYTES]


def encode_provenance_entry(entry: ProvenanceEntry) -> bytes:
    """Encode a provenance entry to exactly 16 bytes.

    Byte 0: [origin_tier:2][operator_id:4][precision_mode:2]
    Bytes 1-3: b̂, d̂, â
    Bytes 4-7: timestamp (uint32 big-endian)
    Bytes 8-15: prev_digest
    """
    byte0 = (
        (entry.origin_tier & 0x03) << 6
        | (entry.operator_id & 0x0F) << 2
        | (entry.precision_mode & 0x03)
    )

    return (
        bytes([byte0, entry.b_q & 0xFF, entry.d_q & 0xFF, entry.a_q & 0xFF])
        + struct.pack(">I", entry.timestamp & 0xFFFFFFFF)
        + entry.prev_digest[:DIGEST_SIZE_BYTES]
    )


def decode_provenance_entry(data: bytes) -> ProvenanceEntry:
    """Decode 16 bytes to a provenance entry."""
    byte0 = data[0]
    origin_tier = (byte0 >> 6) & 0x03
    operator_id = (byte0 >> 2) & 0x0F
    precision_mode = byte0 & 0x03

    b_q = data[1]
    d_q = data[2]
    a_q = data[3]

    timestamp = struct.unpack(">I", data[4:8])[0]
    prev_digest = data[8:16]

    return ProvenanceEntry(
        origin_tier=origin_tier,
        operator_id=operator_id,
        precision_mode=precision_mode,
        b_q=b_q,
        d_q=d_q,
        a_q=a_q,
        timestamp=timestamp,
        prev_digest=bytes(prev_digest),
    )


# =====================================================================
# Provenance Chain — encode, decode, verify
#
# A chain is a sequence of entries where each entry's prev_digest
# is the SHA-256 digest of the previous entry's serialized bytes.
# The first entry uses CHAIN_ORIGIN_SENTINEL (8 zero bytes).
#
# Verification checks:
#   1. First entry must have sentinel prev_digest
#   2. Each subsequent entry's prev_digest must match the digest
#      of the previous entry's serialized form
# =====================================================================

def encode_provenance_chain(entries: list[ProvenanceEntry]) -> bytes:
    """Encode a provenance chain to bytes.

    Args:
        entries: Ordered list of provenance entries.

    Returns:
        Concatenated entry bytes (16 bytes per entry).
    """
    return b"".join(encode_provenance_entry(e) for e in entries)


def decode_provenance_chain(data: bytes, count: int) -> list[ProvenanceEntry]:
    """Decode bytes to a list of provenance entries.

    Args:
        data: Raw bytes (must be count × 16 bytes).
        count: Number of entries to decode.

    Returns:
        List of ProvenanceEntry objects.

    Raises:
        ValueError: If data length doesn't match count × 16.
    """
    expected = count * PROVENANCE_ENTRY_SIZE
    if len(data) != expected:
        raise ValueError(
            f"Expected {expected} bytes for {count} entries, got {len(data)}"
        )
    return [
        decode_provenance_entry(data[i * PROVENANCE_ENTRY_SIZE:(i + 1) * PROVENANCE_ENTRY_SIZE])
        for i in range(count)
    ]


def verify_provenance_chain(
    entries: list[ProvenanceEntry],
) -> tuple[bool, int]:
    """Verify the integrity of a provenance chain.

    Checks:
      1. First entry has CHAIN_ORIGIN_SENTINEL as prev_digest.
      2. Each subsequent entry's prev_digest matches the truncated
         SHA-256 of the previous entry's serialized bytes.

    Args:
        entries: Ordered list of provenance entries.

    Returns:
        (is_valid, error_index) where:
          - is_valid: True if the chain is intact.
          - error_index: Index of the first invalid entry, or -1 if valid.
    """
    if not entries:
        return (True, -1)

    # Check 1: first entry must have sentinel
    if entries[0].prev_digest != CHAIN_ORIGIN_SENTINEL:
        return (False, 0)

    # Check 2: chained digests
    for i in range(1, len(entries)):
        prev_bytes = encode_provenance_entry(entries[i - 1])
        expected_digest = compute_entry_digest(prev_bytes)
        if entries[i].prev_digest != expected_digest:
            return (False, i)

    return (True, -1)
