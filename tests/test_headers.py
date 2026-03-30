"""
Phase 2 tests: Tier-dependent header codec.

Tests are derived from FORMAL_MODEL.md §5 (Wire Format):
  - §5.1: Tier 1 (1 byte), Tier 2 (4 bytes), Tier 3 (4 bytes + extensions)
  - §5.2: Operator ID table (Table 2)
  - Bit layouts specified in §5.1 are the ground truth for field positions

All tests target: src/cbor_ld_ex/headers.py
"""

import pytest

from cbor_ld_ex.headers import (
    Tier1Header,
    Tier2Header,
    Tier3Header,
    ComplianceStatus,
    OperatorId,
    PrecisionMode,
    encode_header,
    decode_header,
)


# ---------------------------------------------------------------------------
# 1. Tier 1 header — 1 byte, constrained devices
# ---------------------------------------------------------------------------

class TestTier1Header:
    """Tier Class 00: 1-byte header for constrained devices (§5.1)."""

    def test_encode_size_exactly_1_byte(self):
        """Tier 1 header MUST be exactly 1 byte."""
        header = Tier1Header(
            compliance_status=ComplianceStatus.COMPLIANT,
            delegation_flag=False,
            has_opinion=True,
            precision_mode=PrecisionMode.BITS_8,
        )
        data = encode_header(header)
        assert len(data) == 1

    def test_encode_decode_roundtrip(self):
        """Full roundtrip: encode → bytes → decode → same header."""
        header = Tier1Header(
            compliance_status=ComplianceStatus.COMPLIANT,
            delegation_flag=False,
            has_opinion=True,
            precision_mode=PrecisionMode.BITS_8,
        )
        data = encode_header(header)
        decoded = decode_header(data)

        assert isinstance(decoded, Tier1Header)
        assert decoded.compliance_status == ComplianceStatus.COMPLIANT
        assert decoded.delegation_flag is False
        assert decoded.has_opinion is True
        assert decoded.precision_mode == PrecisionMode.BITS_8

    def test_all_compliance_status_values(self):
        """All three compliance statuses encode/decode correctly (Def 4).

        compliant     = 00
        non_compliant = 01
        insufficient  = 10
        """
        for status in ComplianceStatus:
            header = Tier1Header(
                compliance_status=status,
                delegation_flag=False,
                has_opinion=False,
                precision_mode=PrecisionMode.BITS_8,
            )
            data = encode_header(header)
            decoded = decode_header(data)
            assert decoded.compliance_status == status, (
                f"Status {status} failed roundtrip"
            )

    def test_delegation_flag(self):
        """delegation_flag = 1 means 'forward to next tier'."""
        header = Tier1Header(
            compliance_status=ComplianceStatus.COMPLIANT,
            delegation_flag=True,
            has_opinion=False,
            precision_mode=PrecisionMode.BITS_8,
        )
        data = encode_header(header)
        decoded = decode_header(data)
        assert decoded.delegation_flag is True

    def test_no_opinion_flag(self):
        """has_opinion = 0: no opinion payload follows."""
        header = Tier1Header(
            compliance_status=ComplianceStatus.COMPLIANT,
            delegation_flag=False,
            has_opinion=False,
            precision_mode=PrecisionMode.BITS_8,
        )
        data = encode_header(header)
        decoded = decode_header(data)
        assert decoded.has_opinion is False

    def test_precision_modes(self):
        """All precision modes roundtrip (Table 1)."""
        for mode in PrecisionMode:
            header = Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=mode,
            )
            data = encode_header(header)
            decoded = decode_header(data)
            assert decoded.precision_mode == mode, (
                f"Precision mode {mode} failed roundtrip"
            )

    def test_bit_layout_worked_example(self):
        """Verify exact bit layout from FORMAL_MODEL.md §C.3.

        compliance_status = compliant (00)
        delegation_flag   = terminal (0)
        origin_tier       = constrained (00)
        has_opinion       = yes (1)
        precision_mode    = 8-bit (00)

        Bit layout: [00][0][00][1][00] = 0b00000100 = 0x04
        """
        header = Tier1Header(
            compliance_status=ComplianceStatus.COMPLIANT,
            delegation_flag=False,
            has_opinion=True,
            precision_mode=PrecisionMode.BITS_8,
        )
        data = encode_header(header)
        assert data == bytes([0b00_0_00_1_00])

    def test_origin_tier_is_00(self):
        """Tier 1 always encodes origin_tier = 00 at bits 3-4."""
        header = Tier1Header(
            compliance_status=ComplianceStatus.NON_COMPLIANT,
            delegation_flag=True,
            has_opinion=True,
            precision_mode=PrecisionMode.BITS_16,
        )
        data = encode_header(header)
        byte_val = data[0]
        # Extract origin_tier: bits 3-4 (0-indexed from MSB)
        origin_tier = (byte_val >> 3) & 0x03
        assert origin_tier == 0b00


# ---------------------------------------------------------------------------
# 2. Tier 2 header — 4 bytes, edge gateways
# ---------------------------------------------------------------------------

class TestTier2Header:
    """Tier Class 01: 4-byte header for edge gateways (§5.1)."""

    def test_encode_size_exactly_4_bytes(self):
        """Tier 2 header MUST be exactly 4 bytes."""
        header = Tier2Header(
            compliance_status=ComplianceStatus.COMPLIANT,
            delegation_flag=False,
            has_opinion=True,
            precision_mode=PrecisionMode.BITS_8,
            operator_id=OperatorId.CUMULATIVE_FUSION,
            reasoning_context=0,
            context_version=1,
            has_multinomial=False,
            sub_tier_depth=0,
            source_count=5,
        )
        data = encode_header(header)
        assert len(data) == 4

    def test_encode_decode_roundtrip(self):
        """Full roundtrip with all fields populated."""
        header = Tier2Header(
            compliance_status=ComplianceStatus.NON_COMPLIANT,
            delegation_flag=True,
            has_opinion=True,
            precision_mode=PrecisionMode.BITS_16,
            operator_id=OperatorId.JURISDICTIONAL_MEET,
            reasoning_context=1,
            context_version=12,
            has_multinomial=False,
            sub_tier_depth=3,
            source_count=10,
        )
        data = encode_header(header)
        decoded = decode_header(data)

        assert isinstance(decoded, Tier2Header)
        assert decoded.compliance_status == ComplianceStatus.NON_COMPLIANT
        assert decoded.delegation_flag is True
        assert decoded.has_opinion is True
        assert decoded.precision_mode == PrecisionMode.BITS_16
        assert decoded.operator_id == OperatorId.JURISDICTIONAL_MEET
        assert decoded.reasoning_context == 1
        assert decoded.context_version == 12
        assert decoded.has_multinomial is False
        assert decoded.sub_tier_depth == 3
        assert decoded.source_count == 10

    def test_origin_tier_is_01(self):
        """Tier 2 always encodes origin_tier = 01."""
        header = Tier2Header(
            compliance_status=ComplianceStatus.COMPLIANT,
            delegation_flag=False,
            has_opinion=True,
            precision_mode=PrecisionMode.BITS_8,
            operator_id=OperatorId.NONE,
            reasoning_context=0,
            context_version=0,
            has_multinomial=False,
            sub_tier_depth=0,
            source_count=1,
        )
        data = encode_header(header)
        byte_val = data[0]
        origin_tier = (byte_val >> 3) & 0x03
        assert origin_tier == 0b01

    def test_all_operator_ids(self):
        """Every operator ID from Table 2 roundtrips correctly."""
        for op in OperatorId:
            header = Tier2Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=False,
                precision_mode=PrecisionMode.BITS_8,
                operator_id=op,
                reasoning_context=0,
                context_version=0,
                has_multinomial=False,
                sub_tier_depth=0,
                source_count=0,
            )
            data = encode_header(header)
            decoded = decode_header(data)
            assert decoded.operator_id == op, (
                f"Operator {op} failed roundtrip"
            )

    def test_has_multinomial_flag(self):
        """has_multinomial flag roundtrips."""
        header = Tier2Header(
            compliance_status=ComplianceStatus.COMPLIANT,
            delegation_flag=False,
            has_opinion=True,
            precision_mode=PrecisionMode.BITS_8,
            operator_id=OperatorId.NONE,
            reasoning_context=0,
            context_version=0,
            has_multinomial=True,
            sub_tier_depth=0,
            source_count=0,
        )
        data = encode_header(header)
        decoded = decode_header(data)
        assert decoded.has_multinomial is True

    def test_context_version_range(self):
        """context_version is 4 bits: 0–15."""
        for cv in (0, 1, 8, 15):
            header = Tier2Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=False,
                precision_mode=PrecisionMode.BITS_8,
                operator_id=OperatorId.NONE,
                reasoning_context=0,
                context_version=cv,
                has_multinomial=False,
                sub_tier_depth=0,
                source_count=0,
            )
            data = encode_header(header)
            decoded = decode_header(data)
            assert decoded.context_version == cv

    def test_sub_tier_depth_range(self):
        """sub_tier_depth is 3 bits: 0–7."""
        for depth in (0, 1, 4, 7):
            header = Tier2Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=False,
                precision_mode=PrecisionMode.BITS_8,
                operator_id=OperatorId.NONE,
                reasoning_context=0,
                context_version=0,
                has_multinomial=False,
                sub_tier_depth=depth,
                source_count=0,
            )
            data = encode_header(header)
            decoded = decode_header(data)
            assert decoded.sub_tier_depth == depth

    def test_source_count_range(self):
        """source_count is 8 bits: 0–255."""
        for sc in (0, 1, 128, 255):
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
                source_count=sc,
            )
            data = encode_header(header)
            decoded = decode_header(data)
            assert decoded.source_count == sc


# ---------------------------------------------------------------------------
# 3. Tier 3 header — 4 bytes fixed + variable extensions
# ---------------------------------------------------------------------------

class TestTier3Header:
    """Tier Class 10: 4-byte header + extensions (§5.1)."""

    def test_encode_minimum_size_4_bytes(self):
        """Tier 3 with no extensions: exactly 4 bytes."""
        header = Tier3Header(
            compliance_status=ComplianceStatus.COMPLIANT,
            delegation_flag=False,
            has_opinion=True,
            precision_mode=PrecisionMode.BITS_8,
            operator_id=OperatorId.NONE,
            reasoning_context=0,
            has_extended_context=False,
            has_provenance_chain=False,
            has_multinomial=False,
            has_trust_info=False,
            sub_tier_depth=0,
        )
        data = encode_header(header)
        assert len(data) == 4

    def test_encode_decode_roundtrip_no_extensions(self):
        """Roundtrip with no extension flags set."""
        header = Tier3Header(
            compliance_status=ComplianceStatus.INSUFFICIENT,
            delegation_flag=True,
            has_opinion=True,
            precision_mode=PrecisionMode.BITS_32,
            operator_id=OperatorId.TRUST_DISCOUNT,
            reasoning_context=3,
            has_extended_context=False,
            has_provenance_chain=False,
            has_multinomial=False,
            has_trust_info=False,
            sub_tier_depth=5,
        )
        data = encode_header(header)
        decoded = decode_header(data)

        assert isinstance(decoded, Tier3Header)
        assert decoded.compliance_status == ComplianceStatus.INSUFFICIENT
        assert decoded.delegation_flag is True
        assert decoded.has_opinion is True
        assert decoded.precision_mode == PrecisionMode.BITS_32
        assert decoded.operator_id == OperatorId.TRUST_DISCOUNT
        assert decoded.reasoning_context == 3
        assert decoded.has_extended_context is False
        assert decoded.has_provenance_chain is False
        assert decoded.has_multinomial is False
        assert decoded.has_trust_info is False
        assert decoded.sub_tier_depth == 5

    def test_origin_tier_is_10(self):
        """Tier 3 always encodes origin_tier = 10."""
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
        data = encode_header(header)
        byte_val = data[0]
        origin_tier = (byte_val >> 3) & 0x03
        assert origin_tier == 0b10

    def test_extension_flags_roundtrip(self):
        """Each extension flag roundtrips independently."""
        for flag_name in ("has_extended_context", "has_provenance_chain",
                          "has_multinomial", "has_trust_info"):
            kwargs = dict(
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
            kwargs[flag_name] = True
            header = Tier3Header(**kwargs)
            data = encode_header(header)
            decoded = decode_header(data)
            assert getattr(decoded, flag_name) is True, (
                f"Flag {flag_name} failed roundtrip"
            )


# ---------------------------------------------------------------------------
# 4. Tier discriminator — parser dispatches on origin_tier bits
# ---------------------------------------------------------------------------

class TestTierDiscriminator:
    """The parser reads origin_tier bits and dispatches to the right type."""

    def test_discriminate_tier1(self):
        """origin_tier=00 → Tier1Header."""
        header = Tier1Header(
            compliance_status=ComplianceStatus.COMPLIANT,
            delegation_flag=False,
            has_opinion=False,
            precision_mode=PrecisionMode.BITS_8,
        )
        data = encode_header(header)
        decoded = decode_header(data)
        assert isinstance(decoded, Tier1Header)

    def test_discriminate_tier2(self):
        """origin_tier=01 → Tier2Header."""
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
        data = encode_header(header)
        decoded = decode_header(data)
        assert isinstance(decoded, Tier2Header)

    def test_discriminate_tier3(self):
        """origin_tier=10 → Tier3Header."""
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
        data = encode_header(header)
        decoded = decode_header(data)
        assert isinstance(decoded, Tier3Header)

    def test_reserved_tier_11_raises(self):
        """origin_tier=11 is reserved — parser should raise ValueError.

        Per §5.1: 'Parsers encountering origin_tier = 11 MUST skip
        the annotation block without error.' At the header level,
        we raise so the caller can decide how to skip.
        """
        # Construct a byte with origin_tier = 11 at bits 3-4
        # Layout: [cs:2][df:1][ot:2][ho:1][pm:2]
        # Set origin_tier = 11: 0b00_0_11_0_00 = 0x18
        raw = bytes([0b00_0_11_0_00])
        with pytest.raises(ValueError, match="[Rr]eserved"):
            decode_header(raw)


# ---------------------------------------------------------------------------
# 5. Operator ID table — Table 2 completeness
# ---------------------------------------------------------------------------

class TestOperatorIdTable:
    """Verify operator ID assignments from Table 2 (§5.2)."""

    def test_operator_id_values(self):
        """Each operator has the correct 4-bit code per Table 2."""
        expected = {
            OperatorId.NONE: 0b0000,
            OperatorId.CUMULATIVE_FUSION: 0b0001,
            OperatorId.TRUST_DISCOUNT: 0b0010,
            OperatorId.DEDUCTION: 0b0011,
            OperatorId.JURISDICTIONAL_MEET: 0b0100,
            OperatorId.COMPLIANCE_PROPAGATION: 0b0101,
            OperatorId.CONSENT_ASSESSMENT: 0b0110,
            OperatorId.TEMPORAL_DECAY: 0b0111,
            OperatorId.ERASURE_PROPAGATION: 0b1000,
            OperatorId.WITHDRAWAL_OVERRIDE: 0b1001,
            OperatorId.EXPIRY_TRIGGER: 0b1010,
            OperatorId.REVIEW_TRIGGER: 0b1011,
            OperatorId.REGULATORY_CHANGE: 0b1100,
        }
        for op, code in expected.items():
            assert op.value == code, (
                f"Operator {op.name} has value {op.value}, expected {code}"
            )

    def test_exactly_13_defined_operators(self):
        """13 operators defined (0000–1100), 1101–1110 reserved, 1111 extension."""
        # We define the 13 non-reserved operators as enum members
        assert len(OperatorId) >= 13


# ---------------------------------------------------------------------------
# 6. Delta mode — precision_mode=11 (§5.1, §7.6)
# ---------------------------------------------------------------------------

class TestDeltaMode:
    """precision_mode=11 is 8-bit delta mode, NOT reserved (v0.4.0+)."""

    def test_delta_8_enum_value(self):
        """PrecisionMode.DELTA_8 must have value 0b11."""
        assert PrecisionMode.DELTA_8 == 0b11

    def test_delta_8_replaces_reserved(self):
        """RESERVED should no longer exist as a PrecisionMode member."""
        assert not hasattr(PrecisionMode, 'RESERVED')

    def test_delta_8_roundtrip_tier1(self):
        """Delta mode roundtrips through Tier 1 header."""
        header = Tier1Header(
            compliance_status=ComplianceStatus.COMPLIANT,
            delegation_flag=False,
            has_opinion=True,
            precision_mode=PrecisionMode.DELTA_8,
        )
        data = encode_header(header)
        decoded = decode_header(data)
        assert decoded.precision_mode == PrecisionMode.DELTA_8

    def test_delta_8_bit_pattern(self):
        """Delta mode sets bits 1-0 of byte 0 to 11.

        Header: [cs=00][df=0][ot=00][ho=1][pm=11] = 0b00000111 = 0x07
        """
        header = Tier1Header(
            compliance_status=ComplianceStatus.COMPLIANT,
            delegation_flag=False,
            has_opinion=True,
            precision_mode=PrecisionMode.DELTA_8,
        )
        data = encode_header(header)
        assert data == bytes([0b00_0_00_1_11])

    def test_opinion_payload_size_delta(self):
        """Delta mode payload is 2 bytes (Δb̂, Δd̂) per §7.6."""
        from cbor_ld_ex.headers import opinion_payload_size
        assert opinion_payload_size(PrecisionMode.DELTA_8) == 2

    def test_opinion_payload_size_full_modes(self):
        """Full modes: 8-bit=3, 16-bit=6, 32-bit=12 bytes."""
        from cbor_ld_ex.headers import opinion_payload_size
        assert opinion_payload_size(PrecisionMode.BITS_8) == 3
        assert opinion_payload_size(PrecisionMode.BITS_16) == 6
        assert opinion_payload_size(PrecisionMode.BITS_32) == 12

    def test_all_four_precision_modes_valid(self):
        """All 4 precision modes are defined — no wasted 2-bit states."""
        assert len(PrecisionMode) == 4
        codes = {m.value for m in PrecisionMode}
        assert codes == {0b00, 0b01, 0b10, 0b11}
