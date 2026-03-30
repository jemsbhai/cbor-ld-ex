"""
Tier-dependent header codec for CBOR-LD-ex.

Implements header encoding/decoding per FORMAL_MODEL.md §5 (Wire Format):
  - Tier 1 (00): 1-byte constrained header
  - Tier 2 (01): 4-byte edge gateway header
  - Tier 3 (10): 4-byte + variable extension cloud header
  - Table 2: Operator ID assignments

Bit layouts (byte 0 shared across all tiers):

  Byte 0: [compliance_status:2][delegation_flag:1][origin_tier:2]
          [has_opinion:1][precision_mode:2]

  Tier 1: Byte 0 only (1 byte total).

  Tier 2 Byte 1: [operator_id:4][reasoning_context:4]
  Tier 2 Byte 2: [context_version:4][has_multinomial:1][sub_tier_depth:3]
  Tier 2 Byte 3: [source_count:8]
  (4 bytes total)

  Tier 3 Byte 1: [operator_id:4][reasoning_context:4]
  Tier 3 Byte 2: [has_extended_context:1][has_provenance_chain:1]
                  [has_multinomial:1][has_trust_info:1][sub_tier_depth:4]
  Tier 3 Byte 3: [reserved:8]
  (4 bytes total; extension blocks follow based on flags)

NOTE: The original FORMAL_MODEL.md §5.1 Tier 2 layout had 36 bits in
a "4-byte header" — an inconsistency. Resolution (agreed with author):
  - context_version reduced from 6 bits to 4 bits (0–15)
  - sub_tier_depth reduced from 4 bits to 3 bits (0–7)
  - reserved bit removed
This fits Tier 2 in exactly 32 bits while preserving the full 8-bit
source_count (0–255), which is more operationally important.
"""

from dataclasses import dataclass
from enum import IntEnum


# -------------------------------------------------------------------------
# Enums — Definition 4, Table 1, Table 2
# -------------------------------------------------------------------------

class ComplianceStatus(IntEnum):
    """Definition 4: 2-bit compliance status."""
    COMPLIANT = 0b00
    NON_COMPLIANT = 0b01
    INSUFFICIENT = 0b10


class PrecisionMode(IntEnum):
    """Table 1: 2-bit precision mode selector (v0.4.0+: mode 11 = delta)."""
    BITS_8 = 0b00
    BITS_16 = 0b01
    BITS_32 = 0b10
    DELTA_8 = 0b11


class OperatorId(IntEnum):
    """Table 2: 4-bit operator ID assignments (§5.2)."""
    NONE = 0b0000
    CUMULATIVE_FUSION = 0b0001
    TRUST_DISCOUNT = 0b0010
    DEDUCTION = 0b0011
    JURISDICTIONAL_MEET = 0b0100
    COMPLIANCE_PROPAGATION = 0b0101
    CONSENT_ASSESSMENT = 0b0110
    TEMPORAL_DECAY = 0b0111
    ERASURE_PROPAGATION = 0b1000
    WITHDRAWAL_OVERRIDE = 0b1001
    EXPIRY_TRIGGER = 0b1010
    REVIEW_TRIGGER = 0b1011
    REGULATORY_CHANGE = 0b1100


def opinion_payload_size(mode: PrecisionMode) -> int:
    """Return the opinion payload size in bytes for a given precision mode.

    Per Table 1 (§4.3) and §7.6:
      - BITS_8 (00):  3 bytes (b̂, d̂, â)
      - BITS_16 (01): 6 bytes
      - BITS_32 (10): 12 bytes (float32 × 3)
      - DELTA_8 (11): 2 bytes (Δb̂, Δd̂) — â unchanged from previous
    """
    return {PrecisionMode.BITS_8: 3, PrecisionMode.BITS_16: 6,
            PrecisionMode.BITS_32: 12, PrecisionMode.DELTA_8: 2}[mode]


# Origin tier codes (2-bit field, bits 3-4 of byte 0)
_TIER_CONSTRAINED = 0b00
_TIER_EDGE = 0b01
_TIER_CLOUD = 0b10
_TIER_RESERVED = 0b11


# -------------------------------------------------------------------------
# Header dataclasses
# -------------------------------------------------------------------------

@dataclass
class Tier1Header:
    """Tier Class 00 — 1-byte constrained device header."""
    compliance_status: ComplianceStatus
    delegation_flag: bool
    has_opinion: bool
    precision_mode: PrecisionMode


@dataclass
class Tier2Header:
    """Tier Class 01 — 4-byte edge gateway header."""
    compliance_status: ComplianceStatus
    delegation_flag: bool
    has_opinion: bool
    precision_mode: PrecisionMode
    operator_id: OperatorId
    reasoning_context: int      # 4 bits (0–15)
    context_version: int        # 4 bits (0–15)
    has_multinomial: bool
    sub_tier_depth: int         # 3 bits (0–7)
    source_count: int           # 8 bits (0–255)


@dataclass
class Tier3Header:
    """Tier Class 10 — 4-byte fixed + variable extensions."""
    compliance_status: ComplianceStatus
    delegation_flag: bool
    has_opinion: bool
    precision_mode: PrecisionMode
    operator_id: OperatorId
    reasoning_context: int      # 4 bits (0–15)
    has_extended_context: bool
    has_provenance_chain: bool
    has_multinomial: bool
    has_trust_info: bool
    sub_tier_depth: int         # 4 bits (0–15)


# -------------------------------------------------------------------------
# Encoding — shared byte 0 helper
# -------------------------------------------------------------------------

def _encode_byte0(
    compliance_status: ComplianceStatus,
    delegation_flag: bool,
    origin_tier: int,
    has_opinion: bool,
    precision_mode: PrecisionMode,
) -> int:
    """Pack byte 0: [cs:2][df:1][ot:2][ho:1][pm:2].

    Bit positions (MSB first, left to right):
      bits 7-6: compliance_status
      bit  5:   delegation_flag
      bits 4-3: origin_tier
      bit  2:   has_opinion
      bits 1-0: precision_mode
    """
    return (
        (int(compliance_status) & 0x03) << 6
        | (int(delegation_flag) & 0x01) << 5
        | (origin_tier & 0x03) << 3
        | (int(has_opinion) & 0x01) << 2
        | (int(precision_mode) & 0x03)
    )


def _decode_byte0(byte_val: int) -> dict:
    """Unpack byte 0 into field dict."""
    return {
        "compliance_status": ComplianceStatus((byte_val >> 6) & 0x03),
        "delegation_flag": bool((byte_val >> 5) & 0x01),
        "origin_tier": (byte_val >> 3) & 0x03,
        "has_opinion": bool((byte_val >> 2) & 0x01),
        "precision_mode": PrecisionMode(byte_val & 0x03),
    }


# -------------------------------------------------------------------------
# Encode
# -------------------------------------------------------------------------

def encode_header(header: Tier1Header | Tier2Header | Tier3Header) -> bytes:
    """Encode a header to bytes per §5.1 bit layout."""

    if isinstance(header, Tier1Header):
        byte0 = _encode_byte0(
            header.compliance_status,
            header.delegation_flag,
            _TIER_CONSTRAINED,
            header.has_opinion,
            header.precision_mode,
        )
        return bytes([byte0])

    elif isinstance(header, Tier2Header):
        byte0 = _encode_byte0(
            header.compliance_status,
            header.delegation_flag,
            _TIER_EDGE,
            header.has_opinion,
            header.precision_mode,
        )

        # Byte 1: [operator_id:4][reasoning_context:4]
        byte1 = (
            (int(header.operator_id) & 0x0F) << 4
            | (header.reasoning_context & 0x0F)
        )

        # Byte 2: [context_version:4][has_multinomial:1][sub_tier_depth:3]
        byte2 = (
            (header.context_version & 0x0F) << 4
            | (int(header.has_multinomial) & 0x01) << 3
            | (header.sub_tier_depth & 0x07)
        )

        # Byte 3: [source_count:8]
        byte3 = header.source_count & 0xFF

        return bytes([byte0, byte1, byte2, byte3])

    elif isinstance(header, Tier3Header):
        byte0 = _encode_byte0(
            header.compliance_status,
            header.delegation_flag,
            _TIER_CLOUD,
            header.has_opinion,
            header.precision_mode,
        )

        # Byte 1: [operator_id:4][reasoning_context:4]
        byte1 = (
            (int(header.operator_id) & 0x0F) << 4
            | (header.reasoning_context & 0x0F)
        )

        # Byte 2: [hec:1][hpc:1][hm:1][hti:1][sub_tier_depth:4]
        byte2 = (
            (int(header.has_extended_context) & 0x01) << 7
            | (int(header.has_provenance_chain) & 0x01) << 6
            | (int(header.has_multinomial) & 0x01) << 5
            | (int(header.has_trust_info) & 0x01) << 4
            | (header.sub_tier_depth & 0x0F)
        )

        # Byte 3: reserved (all zeros)
        byte3 = 0x00

        return bytes([byte0, byte1, byte2, byte3])

    else:
        raise TypeError(f"Unknown header type: {type(header)}")


# -------------------------------------------------------------------------
# Decode
# -------------------------------------------------------------------------

def decode_header(data: bytes) -> Tier1Header | Tier2Header | Tier3Header:
    """Decode bytes to a header, dispatching on origin_tier bits.

    The origin_tier field (bits 4-3 of byte 0) determines the header
    layout. The parser reads these 2 bits and immediately knows how
    many bytes to consume and how to interpret them.
    """
    if len(data) < 1:
        raise ValueError("Header data must be at least 1 byte")

    fields = _decode_byte0(data[0])
    origin_tier = fields["origin_tier"]

    if origin_tier == _TIER_CONSTRAINED:
        # Tier 1: byte 0 only
        return Tier1Header(
            compliance_status=fields["compliance_status"],
            delegation_flag=fields["delegation_flag"],
            has_opinion=fields["has_opinion"],
            precision_mode=fields["precision_mode"],
        )

    elif origin_tier == _TIER_EDGE:
        # Tier 2: 4 bytes
        if len(data) < 4:
            raise ValueError(
                f"Tier 2 header requires 4 bytes, got {len(data)}"
            )

        byte1 = data[1]
        byte2 = data[2]
        byte3 = data[3]

        return Tier2Header(
            compliance_status=fields["compliance_status"],
            delegation_flag=fields["delegation_flag"],
            has_opinion=fields["has_opinion"],
            precision_mode=fields["precision_mode"],
            operator_id=OperatorId((byte1 >> 4) & 0x0F),
            reasoning_context=(byte1 & 0x0F),
            context_version=(byte2 >> 4) & 0x0F,
            has_multinomial=bool((byte2 >> 3) & 0x01),
            sub_tier_depth=byte2 & 0x07,
            source_count=byte3,
        )

    elif origin_tier == _TIER_CLOUD:
        # Tier 3: 4 bytes fixed
        if len(data) < 4:
            raise ValueError(
                f"Tier 3 header requires at least 4 bytes, got {len(data)}"
            )

        byte1 = data[1]
        byte2 = data[2]
        # byte3 is reserved

        return Tier3Header(
            compliance_status=fields["compliance_status"],
            delegation_flag=fields["delegation_flag"],
            has_opinion=fields["has_opinion"],
            precision_mode=fields["precision_mode"],
            operator_id=OperatorId((byte1 >> 4) & 0x0F),
            reasoning_context=(byte1 & 0x0F),
            has_extended_context=bool((byte2 >> 7) & 0x01),
            has_provenance_chain=bool((byte2 >> 6) & 0x01),
            has_multinomial=bool((byte2 >> 5) & 0x01),
            has_trust_info=bool((byte2 >> 4) & 0x01),
            sub_tier_depth=byte2 & 0x0F,
        )

    elif origin_tier == _TIER_RESERVED:
        raise ValueError(
            "Reserved origin_tier (0b11): cannot parse header. "
            "Caller should skip this annotation block."
        )

    else:
        # Unreachable — 2-bit field covers 0–3
        raise ValueError(f"Unexpected origin_tier: {origin_tier}")
