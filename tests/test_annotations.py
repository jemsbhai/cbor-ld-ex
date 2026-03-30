"""
Phase 3 tests: Annotation assembly and CBOR tag wrapping.

Tests are derived from FORMAL_MODEL.md:
  - §3.4 Definition 6: Annotation algebraic type
  - §5.3: CBOR Tag(60000) integration
  - Axiom 1: Stripping property (backward compatibility)
  - Appendix C: Worked example (5-byte Tier 1 message)

All tests target: src/cbor_ld_ex/annotations.py
"""

import math

import cbor2
import pytest

from cbor_ld_ex.annotations import (
    Annotation,
    CBOR_TAG_CBORLD_EX,
    encode_annotation,
    decode_annotation,
    wrap_cbor_tag,
    strip_cbor_tag,
)
from cbor_ld_ex.headers import (
    Tier1Header,
    Tier2Header,
    Tier3Header,
    ComplianceStatus,
    OperatorId,
    PrecisionMode,
)


# ---------------------------------------------------------------------------
# 1. Tier 1 annotation — worked example from §C.3
# ---------------------------------------------------------------------------

class TestTier1Annotation:
    """Tier 1 annotation: header + opinion = 5 bytes (§C.3)."""

    def test_full_message_4_bytes(self):
        """Tier 1 with 8-bit opinion: 1 header + 3 opinion = 4 bytes.

        Wire format transmits (b̂, d̂, â) only — û is derived by decoder.
        """
        header = Tier1Header(
            compliance_status=ComplianceStatus.COMPLIANT,
            delegation_flag=False,
            has_opinion=True,
            precision_mode=PrecisionMode.BITS_8,
        )
        ann = Annotation(
            header=header,
            opinion=(217, 13, 25, 128),  # quantized (b̂, d̂, û, â)
        )
        data = encode_annotation(ann)
        assert len(data) == 4

    def test_header_only_1_byte(self):
        """Tier 1 with no opinion: 1 byte."""
        header = Tier1Header(
            compliance_status=ComplianceStatus.COMPLIANT,
            delegation_flag=False,
            has_opinion=False,
            precision_mode=PrecisionMode.BITS_8,
        )
        ann = Annotation(header=header)
        data = encode_annotation(ann)
        assert len(data) == 1

    def test_roundtrip_with_opinion(self):
        """Encode → decode → same annotation."""
        header = Tier1Header(
            compliance_status=ComplianceStatus.COMPLIANT,
            delegation_flag=False,
            has_opinion=True,
            precision_mode=PrecisionMode.BITS_8,
        )
        ann = Annotation(
            header=header,
            opinion=(217, 13, 25, 128),
        )
        data = encode_annotation(ann)
        decoded = decode_annotation(data)

        assert isinstance(decoded.header, Tier1Header)
        assert decoded.header.compliance_status == ComplianceStatus.COMPLIANT
        assert decoded.header.has_opinion is True
        assert decoded.opinion == (217, 13, 25, 128)

    def test_roundtrip_no_opinion(self):
        """Header-only annotation roundtrips."""
        header = Tier1Header(
            compliance_status=ComplianceStatus.NON_COMPLIANT,
            delegation_flag=True,
            has_opinion=False,
            precision_mode=PrecisionMode.BITS_8,
        )
        ann = Annotation(header=header)
        data = encode_annotation(ann)
        decoded = decode_annotation(data)

        assert isinstance(decoded.header, Tier1Header)
        assert decoded.header.compliance_status == ComplianceStatus.NON_COMPLIANT
        assert decoded.header.delegation_flag is True
        assert decoded.opinion is None


# ---------------------------------------------------------------------------
# 2. Tier 2 annotation
# ---------------------------------------------------------------------------

class TestTier2Annotation:
    """Tier 2 annotation: 4-byte header + opinion payload."""

    def test_with_binomial_8bit(self):
        """Tier 2 + binomial 8-bit: 4 header + 3 opinion = 7 bytes."""
        header = Tier2Header(
            compliance_status=ComplianceStatus.COMPLIANT,
            delegation_flag=False,
            has_opinion=True,
            precision_mode=PrecisionMode.BITS_8,
            operator_id=OperatorId.JURISDICTIONAL_MEET,
            reasoning_context=1,
            context_version=3,
            has_multinomial=False,
            sub_tier_depth=0,
            source_count=5,
        )
        ann = Annotation(
            header=header,
            opinion=(200, 30, 25, 128),
        )
        data = encode_annotation(ann)
        assert len(data) == 7

    def test_roundtrip_tier2(self):
        """Full Tier 2 roundtrip."""
        header = Tier2Header(
            compliance_status=ComplianceStatus.INSUFFICIENT,
            delegation_flag=True,
            has_opinion=True,
            precision_mode=PrecisionMode.BITS_16,
            operator_id=OperatorId.CUMULATIVE_FUSION,
            reasoning_context=2,
            context_version=7,
            has_multinomial=False,
            sub_tier_depth=1,
            source_count=10,
        )
        ann = Annotation(
            header=header,
            opinion=(50000, 10000, 5535, 32768),
        )
        data = encode_annotation(ann)
        decoded = decode_annotation(data)

        assert isinstance(decoded.header, Tier2Header)
        assert decoded.header.operator_id == OperatorId.CUMULATIVE_FUSION
        assert decoded.header.source_count == 10
        assert decoded.opinion == (50000, 10000, 5535, 32768)

    def test_tier2_no_opinion(self):
        """Tier 2 with has_opinion=False: 4 bytes."""
        header = Tier2Header(
            compliance_status=ComplianceStatus.COMPLIANT,
            delegation_flag=False,
            has_opinion=False,
            precision_mode=PrecisionMode.BITS_8,
            operator_id=OperatorId.NONE,
            reasoning_context=0,
            context_version=0,
            has_multinomial=False,
            sub_tier_depth=0,
            source_count=0,
        )
        ann = Annotation(header=header)
        data = encode_annotation(ann)
        assert len(data) == 4


# ---------------------------------------------------------------------------
# 3. Tier 3 annotation
# ---------------------------------------------------------------------------

class TestTier3Annotation:
    """Tier 3 annotation: 4-byte header + opinion + extensions."""

    def test_minimum_4_bytes(self):
        """Tier 3 no opinion, no extensions: 4 bytes."""
        header = Tier3Header(
            compliance_status=ComplianceStatus.COMPLIANT,
            delegation_flag=False,
            has_opinion=False,
            precision_mode=PrecisionMode.BITS_8,
            operator_id=OperatorId.NONE,
            reasoning_context=0,
            has_extended_context=False,
            has_provenance_chain=False,
            has_multinomial=False,
            has_trust_info=False,
            sub_tier_depth=0,
        )
        ann = Annotation(header=header)
        data = encode_annotation(ann)
        assert len(data) == 4

    def test_roundtrip_tier3_with_opinion(self):
        """Tier 3 with 8-bit opinion roundtrips."""
        header = Tier3Header(
            compliance_status=ComplianceStatus.NON_COMPLIANT,
            delegation_flag=False,
            has_opinion=True,
            precision_mode=PrecisionMode.BITS_8,
            operator_id=OperatorId.COMPLIANCE_PROPAGATION,
            reasoning_context=1,
            has_extended_context=False,
            has_provenance_chain=False,
            has_multinomial=False,
            has_trust_info=False,
            sub_tier_depth=2,
        )
        ann = Annotation(
            header=header,
            opinion=(180, 50, 25, 100),
        )
        data = encode_annotation(ann)
        decoded = decode_annotation(data)

        assert isinstance(decoded.header, Tier3Header)
        assert decoded.header.operator_id == OperatorId.COMPLIANCE_PROPAGATION
        assert decoded.opinion == (180, 50, 25, 100)


# ---------------------------------------------------------------------------
# 4. CBOR Tag(60000) wrapping — §5.3
# ---------------------------------------------------------------------------

class TestCborTagWrapping:
    """CBOR Tag integration per §5.3."""

    def test_tag_number(self):
        """Tag number is 60000 (experimental, per §5.3)."""
        assert CBOR_TAG_CBORLD_EX == 60000

    def test_wrap_produces_valid_cbor(self):
        """wrap_cbor_tag output is valid CBOR."""
        annotation_bytes = bytes([0x04, 0xD9, 0x0D, 0x19, 0x80])
        tagged = wrap_cbor_tag(annotation_bytes)

        # Must be parseable by cbor2
        decoded = cbor2.loads(tagged)
        assert isinstance(decoded, cbor2.CBORTag)
        assert decoded.tag == 60000

    def test_wrap_unwrap_roundtrip(self):
        """wrap → strip recovers original bytes."""
        annotation_bytes = bytes([0x04, 0xD9, 0x0D, 0x19, 0x80])
        tagged = wrap_cbor_tag(annotation_bytes)
        recovered = strip_cbor_tag(tagged)
        assert recovered == annotation_bytes

    def test_strip_from_non_tagged_raises(self):
        """strip_cbor_tag on non-tagged data should raise."""
        # Just a plain CBOR byte string, no tag
        plain = cbor2.dumps(b"\x04\xD9")
        with pytest.raises(ValueError):
            strip_cbor_tag(plain)


# ---------------------------------------------------------------------------
# 5. Axiom 1 — stripping property
# ---------------------------------------------------------------------------

class TestAxiom1Stripping:
    """Axiom 1: every CBOR-LD-ex message is valid CBOR-LD when stripped.

    At the annotation level, this means: strip Tag(60000) from the
    annotation block, and the remaining CBOR content is unaffected.
    A CBOR parser that doesn't know tag 60000 can still parse the
    message — it just ignores the tag per RFC 8949 §3.4.
    """

    def test_tag_ignorable_by_standard_cbor_parser(self):
        """A standard CBOR decoder sees Tag(60000, byte_string).

        Per RFC 8949 §3.4, unrecognized tags are presented with their
        content — the parser does not fail.
        """
        header = Tier1Header(
            compliance_status=ComplianceStatus.COMPLIANT,
            delegation_flag=False,
            has_opinion=True,
            precision_mode=PrecisionMode.BITS_8,
        )
        ann = Annotation(header=header, opinion=(217, 13, 25, 128))
        ann_bytes = encode_annotation(ann)
        tagged = wrap_cbor_tag(ann_bytes)

        # Standard cbor2 decoder handles unknown tags gracefully
        decoded = cbor2.loads(tagged)
        assert isinstance(decoded, cbor2.CBORTag)
        assert decoded.tag == 60000
        # The content is the raw annotation bytes
        assert decoded.value == ann_bytes

    def test_annotation_embedded_in_cbor_map(self):
        """Simulate a CBOR-LD-ex message: CBOR-LD data + annotation.

        The annotation is a sibling entry in the CBOR map.
        Stripping = removing the annotation key. The rest is valid CBOR.
        """
        # Simulated CBOR-LD data payload
        data_payload = {
            1: "TemperatureReading",  # compressed @type
            2: 22.5,                  # value
            3: "Celsius",             # unit
        }

        # Build annotation
        header = Tier1Header(
            compliance_status=ComplianceStatus.COMPLIANT,
            delegation_flag=False,
            has_opinion=True,
            precision_mode=PrecisionMode.BITS_8,
        )
        ann = Annotation(header=header, opinion=(217, 13, 25, 128))
        ann_bytes = encode_annotation(ann)
        ann_tagged = cbor2.CBORTag(CBOR_TAG_CBORLD_EX, ann_bytes)

        # Full CBOR-LD-ex message: data + annotation (integer term ID)
        full_message = {**data_payload, CBOR_TAG_CBORLD_EX: ann_tagged}
        encoded_message = cbor2.dumps(full_message)

        # A CBOR-LD parser can decode the full message
        decoded_message = cbor2.loads(encoded_message)
        assert decoded_message[1] == "TemperatureReading"
        assert decoded_message[2] == 22.5

        # Stripping: remove annotation term ID → valid CBOR-LD
        stripped = {k: v for k, v in decoded_message.items()
                    if k != CBOR_TAG_CBORLD_EX}
        assert stripped == data_payload


# ---------------------------------------------------------------------------
# 6. Delta mode annotations — §7.6, precision_mode=11
# ---------------------------------------------------------------------------

class TestDeltaAnnotation:
    """Delta mode (precision_mode=DELTA_8) annotation encode/decode.

    Per §7.6: delta opinion is 2 bytes (Δb̂, Δd̂), not the standard
    3-byte (b̂, d̂, â) payload. Annotation.opinion stores a 2-tuple.
    """

    def test_tier1_delta_size_3_bytes(self):
        """Tier 1 + delta: 1-byte header + 2-byte payload = 3 bytes.

        This is the minimum CBOR-LD-ex annotation with an opinion.
        §11.2: 24 wire bits, 23.170 Shannon bits, 96.5% efficiency.
        """
        header = Tier1Header(
            compliance_status=ComplianceStatus.COMPLIANT,
            delegation_flag=False,
            has_opinion=True,
            precision_mode=PrecisionMode.DELTA_8,
        )
        ann = Annotation(
            header=header,
            opinion=(5, -3),  # (Δb̂, Δd̂) — 2-tuple
        )
        data = encode_annotation(ann)
        assert len(data) == 3

    def test_tier1_delta_roundtrip(self):
        """Encode → decode preserves 2-tuple delta opinion exactly."""
        header = Tier1Header(
            compliance_status=ComplianceStatus.COMPLIANT,
            delegation_flag=False,
            has_opinion=True,
            precision_mode=PrecisionMode.DELTA_8,
        )
        ann = Annotation(
            header=header,
            opinion=(10, -7),
        )
        data = encode_annotation(ann)
        decoded = decode_annotation(data)

        assert isinstance(decoded.header, Tier1Header)
        assert decoded.header.precision_mode == PrecisionMode.DELTA_8
        assert decoded.header.has_opinion is True
        assert decoded.opinion == (10, -7)

    def test_tier1_delta_zero_change(self):
        """Delta of (0, 0) roundtrips — no change in opinion."""
        header = Tier1Header(
            compliance_status=ComplianceStatus.COMPLIANT,
            delegation_flag=False,
            has_opinion=True,
            precision_mode=PrecisionMode.DELTA_8,
        )
        ann = Annotation(header=header, opinion=(0, 0))
        data = encode_annotation(ann)
        decoded = decode_annotation(data)
        assert decoded.opinion == (0, 0)

    def test_tier1_delta_extreme_values(self):
        """Extreme signed int8 boundaries: -128 and +127."""
        header = Tier1Header(
            compliance_status=ComplianceStatus.COMPLIANT,
            delegation_flag=False,
            has_opinion=True,
            precision_mode=PrecisionMode.DELTA_8,
        )
        ann = Annotation(header=header, opinion=(-128, 127))
        data = encode_annotation(ann)
        decoded = decode_annotation(data)
        assert decoded.opinion == (-128, 127)

    def test_tier2_delta_size_6_bytes(self):
        """Tier 2 + delta: 4-byte header + 2-byte payload = 6 bytes."""
        header = Tier2Header(
            compliance_status=ComplianceStatus.COMPLIANT,
            delegation_flag=False,
            has_opinion=True,
            precision_mode=PrecisionMode.DELTA_8,
            operator_id=OperatorId.TEMPORAL_DECAY,
            reasoning_context=1,
            context_version=0,
            has_multinomial=False,
            sub_tier_depth=0,
            source_count=1,
        )
        ann = Annotation(header=header, opinion=(3, -2))
        data = encode_annotation(ann)
        assert len(data) == 6

    def test_tier2_delta_roundtrip(self):
        """Tier 2 delta roundtrips with full header fields."""
        header = Tier2Header(
            compliance_status=ComplianceStatus.COMPLIANT,
            delegation_flag=False,
            has_opinion=True,
            precision_mode=PrecisionMode.DELTA_8,
            operator_id=OperatorId.TEMPORAL_DECAY,
            reasoning_context=3,
            context_version=2,
            has_multinomial=False,
            sub_tier_depth=1,
            source_count=5,
        )
        ann = Annotation(header=header, opinion=(-50, 20))
        data = encode_annotation(ann)
        decoded = decode_annotation(data)

        assert isinstance(decoded.header, Tier2Header)
        assert decoded.header.precision_mode == PrecisionMode.DELTA_8
        assert decoded.header.source_count == 5
        assert decoded.opinion == (-50, 20)

    def test_opinion_wire_size_delta(self):
        """_opinion_wire_size returns 2 for DELTA_8 mode."""
        from cbor_ld_ex.annotations import _opinion_wire_size
        assert _opinion_wire_size(PrecisionMode.DELTA_8) == 2
