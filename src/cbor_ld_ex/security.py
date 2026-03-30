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
  Byte 0:   [origin_tier:2][operator_id:4][precision_bit_high:1][has_correction:1]
  Byte 1:   b̂ (uint8)
  Byte 2:   d̂ (uint8)
  Byte 3:   â (uint8)
  Bytes 4-7:  timestamp (uint32, big-endian, seconds since Unix epoch)
  Bytes 8-15: prev_digest (64 bits, chained SHA-256)

  §4.7.5 amendment: bit 0 of byte 0 repurposed as has_correction flag.
  Provenance opinions are always 8-bit (precision_mode=0b00), so the low
  bit was always 0. Backward-compatible: existing entries decode identically.
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

# Audit-grade (Definition 27b): 128-bit digest, 24-byte entries
AUDIT_DIGEST_SIZE_BYTES = 16  # 128 bits
AUDIT_ENTRY_SIZE = 24         # 8 (header+opinion+timestamp) + 16 (digest)
AUDIT_CHAIN_ORIGIN_SENTINEL = b"\x00" * AUDIT_DIGEST_SIZE_BYTES


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
# Byte 0:     [origin_tier:2][operator_id:4][precision_bit_high:1][has_correction:1]
# Bytes 1-3:  opinion (b̂, d̂, â) — 3 × uint8
# Bytes 4-7:  timestamp (uint32 big-endian, seconds since epoch)
# Bytes 8-15: prev_digest (64-bit chained SHA-256)
#
# Total: 2+4+1+1 + 24 + 32 + 64 = 128 bits = 16 bytes. Zero waste.
#
# Note: û is NOT stored — derived as 255 − b̂ − d̂ (Axiom 3).
# This matches the opinion wire format: 3 values, not 4.
#
# §4.7.5 amendment: bit 0 of byte 0 repurposed as has_correction.
# Provenance opinions are always 8-bit (precision_mode=0b00), so
# precision_bit_high is always 0. Backward-compatible.
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
        precision_mode: Opinion quantization precision (0=8-bit always for provenance).
        b_q: Quantized belief (uint8 for 8-bit mode).
        d_q: Quantized disbelief (uint8 for 8-bit mode).
        a_q: Quantized base rate (uint8 for 8-bit mode).
        timestamp: Unix epoch seconds (uint32).
        prev_digest: SHA-256 digest of the previous entry (8 bytes).
        has_correction: §4.7.5 — True if this entry has 1-bit residual correction.
        c_b: Belief correction bit (0 or 1). Only meaningful when has_correction=True.
        c_d: Disbelief correction bit (0 or 1).
        c_a: Base rate correction bit (0 or 1).
    """
    origin_tier: int      # 2 bits
    operator_id: int      # 4 bits
    precision_mode: int   # 2 bits (always 0 for provenance; bit 0 repurposed)
    b_q: int              # 8 bits
    d_q: int              # 8 bits
    a_q: int              # 8 bits
    timestamp: int        # 32 bits
    prev_digest: bytes    # 64 bits
    has_correction: bool = False  # §4.7.5: bit 0 of byte 0
    c_b: int = 0          # correction bit for belief
    c_d: int = 0          # correction bit for disbelief
    c_a: int = 0          # correction bit for base rate


def compute_entry_digest(entry_bytes: bytes, audit_grade: bool = False) -> bytes:
    """Compute the chained digest for a provenance entry.

    Args:
        entry_bytes: Serialized provenance entry (16 or 24 bytes).
        audit_grade: If True, return 128-bit (16-byte) digest.
                     If False, return 64-bit (8-byte) digest.

    Returns:
        Truncated SHA-256 digest (8 or 16 bytes).
    """
    size = AUDIT_DIGEST_SIZE_BYTES if audit_grade else DIGEST_SIZE_BYTES
    return hashlib.sha256(entry_bytes).digest()[:size]


def encode_provenance_entry(entry: ProvenanceEntry, audit_grade: bool = False) -> bytes:
    """Encode a provenance entry to bytes.

    Standard (16 bytes):
      Byte 0: [origin_tier:2][operator_id:4][precision_bit_high:1][has_correction:1]
      Bytes 1-3: b̂, d̂, â
      Bytes 4-7: timestamp (uint32 big-endian)
      Bytes 8-15: prev_digest (64-bit)

    Audit-grade (24 bytes): same layout, bytes 8-23: prev_digest (128-bit).

    §4.7.5: bit 0 of byte 0 carries has_correction. Bit 1 (precision_bit_high)
    is always 0 for provenance entries (8-bit opinions only).
    """
    digest_size = AUDIT_DIGEST_SIZE_BYTES if audit_grade else DIGEST_SIZE_BYTES
    # §4.7.5 byte 0 layout: [origin_tier:2][operator_id:4][precision_bit_high:1][has_correction:1]
    # precision_bit_high = bit 1 of precision_mode (always 0 for provenance, but encode faithfully)
    precision_bit_high = (entry.precision_mode >> 1) & 0x01
    has_corr_bit = 1 if entry.has_correction else 0
    byte0 = (
        (entry.origin_tier & 0x03) << 6
        | (entry.operator_id & 0x0F) << 2
        | (precision_bit_high << 1)
        | has_corr_bit
    )

    return (
        bytes([byte0, entry.b_q & 0xFF, entry.d_q & 0xFF, entry.a_q & 0xFF])
        + struct.pack(">I", entry.timestamp & 0xFFFFFFFF)
        + entry.prev_digest[:digest_size]
    )


def decode_provenance_entry(data: bytes, audit_grade: bool = False) -> ProvenanceEntry:
    """Decode bytes to a provenance entry.

    Standard: 16 bytes (64-bit digest). Audit-grade: 24 bytes (128-bit digest).

    §4.7.5: bit 0 of byte 0 is has_correction. Bit 1 is precision_bit_high
    (always 0). precision_mode is reconstructed as the full 2-bit field
    for backward compatibility (will be 0b00 or 0b01 depending on
    has_correction).
    """
    digest_size = AUDIT_DIGEST_SIZE_BYTES if audit_grade else DIGEST_SIZE_BYTES
    byte0 = data[0]
    origin_tier = (byte0 >> 6) & 0x03
    operator_id = (byte0 >> 2) & 0x0F
    # §4.7.5: bit 1 = precision_bit_high, bit 0 = has_correction
    has_correction = bool(byte0 & 0x01)
    precision_bit_high = (byte0 >> 1) & 0x01
    precision_mode = precision_bit_high << 1  # Reconstruct: 0 or 2 (bit 0 is repurposed)

    b_q = data[1]
    d_q = data[2]
    a_q = data[3]

    timestamp = struct.unpack(">I", data[4:8])[0]
    prev_digest = data[8:8 + digest_size]

    return ProvenanceEntry(
        origin_tier=origin_tier,
        operator_id=operator_id,
        precision_mode=precision_mode,
        b_q=b_q,
        d_q=d_q,
        a_q=a_q,
        timestamp=timestamp,
        prev_digest=bytes(prev_digest),
        has_correction=has_correction,
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

def encode_provenance_chain(entries: list[ProvenanceEntry], audit_grade: bool = False) -> bytes:
    """Encode a provenance chain to bytes.

    Args:
        entries: Ordered list of provenance entries.
        audit_grade: If True, use 24-byte entries with 128-bit digests.

    Returns:
        Concatenated entry bytes (16 or 24 bytes per entry).
    """
    return b"".join(encode_provenance_entry(e, audit_grade=audit_grade) for e in entries)


def decode_provenance_chain(data: bytes, count: int, audit_grade: bool = False) -> list[ProvenanceEntry]:
    """Decode bytes to a list of provenance entries.

    Args:
        data: Raw bytes (must be count × entry_size bytes).
        count: Number of entries to decode.
        audit_grade: If True, entries are 24 bytes each.

    Returns:
        List of ProvenanceEntry objects.

    Raises:
        ValueError: If data length doesn't match count × entry_size.
    """
    entry_size = AUDIT_ENTRY_SIZE if audit_grade else PROVENANCE_ENTRY_SIZE
    expected = count * entry_size
    if len(data) != expected:
        raise ValueError(
            f"Expected {expected} bytes for {count} entries, got {len(data)}"
        )
    return [
        decode_provenance_entry(data[i * entry_size:(i + 1) * entry_size], audit_grade=audit_grade)
        for i in range(count)
    ]


def verify_provenance_chain(
    entries: list[ProvenanceEntry],
    audit_grade: bool = False,
) -> tuple[bool, int]:
    """Verify the integrity of a provenance chain.

    Checks:
      1. First entry has the correct sentinel (8 or 16 zero bytes).
      2. Each subsequent entry's prev_digest matches the truncated
         SHA-256 of the previous entry's serialized bytes.

    Args:
        entries: Ordered list of provenance entries.
        audit_grade: If True, expect 128-bit digests and 16-byte sentinel.

    Returns:
        (is_valid, error_index) where:
          - is_valid: True if the chain is intact.
          - error_index: Index of the first invalid entry, or -1 if valid.
    """
    if not entries:
        return (True, -1)

    # Check 1: first entry must have correct sentinel
    sentinel = AUDIT_CHAIN_ORIGIN_SENTINEL if audit_grade else CHAIN_ORIGIN_SENTINEL
    if entries[0].prev_digest != sentinel:
        return (False, 0)

    # Check 2: chained digests
    for i in range(1, len(entries)):
        prev_bytes = encode_provenance_entry(entries[i - 1], audit_grade=audit_grade)
        expected_digest = compute_entry_digest(prev_bytes, audit_grade=audit_grade)
        if entries[i].prev_digest != expected_digest:
            return (False, i)

    return (True, -1)


# =====================================================================
# §4.7.5 Provenance Block — chain_length + entries + correction block
#
# Wire format:
#   [1 byte]              chain_length (L)
#   [entry_size*L bytes]  entries (16 or 24 bytes each)
#   [ceil(3*C/8) bytes]   correction block (C = count of has_correction=True)
#
# Correction block: 3 bits per corrected entry (c_b, c_d, c_a) in chain
# order, MSB-first (matching §7.4 convention), zero-padded to byte boundary.
#
# When no entries have has_correction=True, the correction block is
# omitted entirely — zero overhead for chains without correction.
# =====================================================================

def encode_provenance_block(
    entries: list[ProvenanceEntry],
    audit_grade: bool = False,
) -> bytes:
    """Encode a provenance chain as a self-describing block.

    Format: [chain_length:1][entries][correction_block].
    The correction block is present only if any entry has has_correction=True.

    Args:
        entries: Ordered list of provenance entries.
        audit_grade: If True, use 24-byte entries with 128-bit digests.

    Returns:
        Packed block bytes.
    """
    chain_length = len(entries)
    result = bytes([chain_length & 0xFF])

    # Encode all entries
    for e in entries:
        result += encode_provenance_entry(e, audit_grade=audit_grade)

    # Collect correction triples for entries with has_correction=True
    correction_bits = []
    for e in entries:
        if e.has_correction:
            correction_bits.extend([e.c_b & 1, e.c_d & 1, e.c_a & 1])

    # Pack correction bits MSB-first, zero-pad to byte boundary
    if correction_bits:
        # Pad to multiple of 8
        while len(correction_bits) % 8 != 0:
            correction_bits.append(0)
        # Pack into bytes
        for i in range(0, len(correction_bits), 8):
            byte_val = 0
            for j in range(8):
                byte_val = (byte_val << 1) | correction_bits[i + j]
            result += bytes([byte_val])

    return result


def decode_provenance_block(
    data: bytes,
    audit_grade: bool = False,
) -> list[ProvenanceEntry]:
    """Decode a provenance block to a list of entries with correction bits.

    Inverse of encode_provenance_block. Uses has_correction flags in each
    entry to determine how many correction triples to read from the
    appended correction block.

    Args:
        data: Raw block bytes.
        audit_grade: If True, entries are 24 bytes each.

    Returns:
        List of ProvenanceEntry objects with correction bits populated.
    """
    if not data:
        return []

    entry_size = AUDIT_ENTRY_SIZE if audit_grade else PROVENANCE_ENTRY_SIZE
    chain_length = data[0]

    if chain_length == 0:
        return []

    # Decode entries
    entries = []
    corrected_indices = []
    offset = 1
    for i in range(chain_length):
        entry = decode_provenance_entry(
            data[offset:offset + entry_size], audit_grade=audit_grade
        )
        if entry.has_correction:
            corrected_indices.append(i)
        entries.append(entry)
        offset += entry_size

    # Decode correction block
    if corrected_indices:
        correction_bytes = data[offset:]
        # Unpack all bits MSB-first
        all_bits = []
        for b in correction_bytes:
            for shift in range(7, -1, -1):
                all_bits.append((b >> shift) & 1)

        # Assign 3 bits per corrected entry
        for j, idx in enumerate(corrected_indices):
            entries[idx].c_b = all_bits[3 * j]
            entries[idx].c_d = all_bits[3 * j + 1]
            entries[idx].c_a = all_bits[3 * j + 2]

    return entries
