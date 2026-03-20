"""
Phase 4 tests: Temporal extensions for CBOR-LD-ex.

Tests are derived from SECTION7_TEMPORAL.md and FORMAL_MODEL.md §7:
  - Bit-packed extension block (has_temporal, has_triggers flags)
  - Half-life log-scale encoding (8 bits, 25 orders of magnitude base-2)
  - Quantized decay (dequantize → decay → re-quantize, Axiom 3 preserved)
  - Quantized expiry trigger (b → d transfer, u unchanged)
  - Decay factor computation (exponential, linear, step)
  - Wire-level bit-exactness for extension blocks
  - Integration with annotation encode/decode pipeline

All temporal operators MUST preserve:
  - Axiom 2: valid opinion in → valid opinion out
  - Axiom 3: b̂ + d̂ + û = 2ⁿ − 1 exactly after re-quantization

Depends on:
  - Phase 1: opinions.py (quantization codec)
  - Phase 2: headers.py (tier-dependent headers)
  - Phase 3: annotations.py (annotation assembly)
  - jsonld-ex: Opinion, decay_opinion (reference implementation)
"""

import math

import pytest
from hypothesis import given, assume, settings
from hypothesis import strategies as st

from cbor_ld_ex.annotations import (
    Annotation,
    encode_annotation,
    decode_annotation,
)
from cbor_ld_ex.headers import (
    Tier1Header,
    Tier2Header,
    ComplianceStatus,
    OperatorId,
    PrecisionMode,
)
from cbor_ld_ex.opinions import (
    quantize_binomial,
    dequantize_binomial,
)
from cbor_ld_ex.temporal import (
    # Data structures
    TemporalBlock,
    Trigger,
    ExtensionBlock,
    # Wire format
    encode_extensions,
    decode_extensions,
    # Half-life codec
    encode_half_life,
    decode_half_life,
    HALF_LIFE_MAX_EXPONENT,
    # Decay function codes
    DECAY_EXPONENTIAL,
    DECAY_LINEAR,
    DECAY_STEP,
    # Trigger type codes
    TRIGGER_EXPIRY,
    TRIGGER_REVIEW_DUE,
    TRIGGER_REG_CHANGE,
    TRIGGER_WITHDRAWAL,
    # Quantized operators
    apply_decay_quantized,
    apply_expiry_quantized,
    # Decay factor computation
    compute_decay_factor,
)

# Reference implementation for cross-checking
from jsonld_ex.confidence_algebra import Opinion
from jsonld_ex.confidence_decay import (
    decay_opinion,
    exponential_decay,
    linear_decay,
    step_decay,
)


# =========================================================================
# Hypothesis strategies
# =========================================================================

def valid_opinion_strategy():
    """Generate a valid SL opinion (b, d, u, a) with b+d+u=1."""
    return (
        st.floats(min_value=0.0, max_value=1.0,
                   allow_nan=False, allow_infinity=False)
        .flatmap(lambda b:
            st.floats(min_value=0.0, max_value=1.0 - b,
                       allow_nan=False, allow_infinity=False)
            .map(lambda d: (b, d, 1.0 - b - d))
        )
        .flatmap(lambda bdu:
            st.floats(min_value=0.0, max_value=1.0,
                       allow_nan=False, allow_infinity=False)
            .map(lambda a: (bdu[0], bdu[1], bdu[2], a))
        )
    )


def quantized_opinion_strategy(precision=8):
    """Generate a valid quantized opinion at given precision."""
    max_val = (1 << precision) - 1
    return (
        st.integers(min_value=0, max_value=max_val)
        .flatmap(lambda b_q:
            st.integers(min_value=0, max_value=max_val - b_q)
            .map(lambda d_q: (b_q, d_q, max_val - b_q - d_q))
        )
        .flatmap(lambda bdu:
            st.integers(min_value=0, max_value=max_val)
            .map(lambda a_q: (bdu[0], bdu[1], bdu[2], a_q))
        )
    )


# =========================================================================
# 1. Half-life log-scale encoding
#
# 8 bits → 256 distinct values spanning ~1 second to ~388 days.
# Formula: seconds = 2^(value * MAX_EXPONENT / 255)
# Shannon efficiency: log2(256)/8 = 100%
# =========================================================================

class TestHalfLifeCodec:
    """Log-scale half-life encoding: 8 bits, 25 orders of magnitude (base 2)."""

    def test_half_life_encode_one_second(self):
        """1 second → encoded value 0."""
        assert encode_half_life(1.0) == 0

    def test_half_life_decode_zero(self):
        """Encoded 0 → 1.0 second (2^0)."""
        assert decode_half_life(0) == pytest.approx(1.0)

    def test_half_life_decode_max(self):
        """Encoded 255 → 2^25 ≈ 33.5M seconds ≈ 388 days."""
        decoded = decode_half_life(255)
        assert decoded == pytest.approx(2.0 ** HALF_LIFE_MAX_EXPONENT)

    def test_half_life_roundtrip_one_hour(self):
        """3600 seconds round-trips within ~7% (one log step)."""
        encoded = encode_half_life(3600.0)
        decoded = decode_half_life(encoded)
        # Log-scale granularity: each step is ~2^(25/255) ≈ 7%
        assert decoded == pytest.approx(3600.0, rel=0.08)

    def test_half_life_roundtrip_one_day(self):
        """86400 seconds round-trips within ~7%."""
        encoded = encode_half_life(86400.0)
        decoded = decode_half_life(encoded)
        assert decoded == pytest.approx(86400.0, rel=0.08)

    def test_half_life_roundtrip_90_days(self):
        """90 days round-trips within ~7%."""
        seconds = 90 * 86400.0
        encoded = encode_half_life(seconds)
        decoded = decode_half_life(encoded)
        assert decoded == pytest.approx(seconds, rel=0.08)

    def test_half_life_monotonic(self):
        """Larger encoded values always decode to larger half-lives."""
        prev = 0.0
        for v in range(256):
            cur = decode_half_life(v)
            assert cur >= prev, (
                f"Monotonicity violated: decode({v})={cur} < decode({v-1})={prev}"
            )
            prev = cur

    def test_half_life_all_values_positive(self):
        """Every encoded value decodes to a positive number of seconds."""
        for v in range(256):
            assert decode_half_life(v) > 0

    def test_half_life_encode_rejects_non_positive(self):
        """Zero and negative half-lives are invalid."""
        with pytest.raises(ValueError):
            encode_half_life(0.0)
        with pytest.raises(ValueError):
            encode_half_life(-1.0)

    def test_half_life_encode_clamps_to_range(self):
        """Values beyond max range clamp to 255."""
        huge = 1e12  # way beyond 388 days
        assert encode_half_life(huge) == 255

    def test_half_life_encode_tiny_clamps_to_zero(self):
        """Sub-second values clamp to 0."""
        assert encode_half_life(0.01) == 0


# =========================================================================
# 2. Extension block bit-packed wire format
#
# Presence detected by remaining bytes in annotation byte string.
# Layout:
#   [1 bit] has_temporal
#   [1 bit] has_triggers
#   IF has_temporal: [2 bits] decay_fn, [8 bits] half_life_encoded
#   IF has_triggers: [3 bits] trigger_count, per-trigger data
#   Pad to byte boundary.
# =========================================================================

class TestExtensionBlockWireFormat:
    """Bit-packed extension block encoding and decoding."""

    def test_empty_extension_zero_bytes(self):
        """No temporal, no triggers → 0 bytes on wire."""
        ext = ExtensionBlock(temporal=None, triggers=None)
        assert encode_extensions(ext) == b""

    def test_temporal_only_two_bytes(self):
        """Temporal block without triggers → exactly 2 bytes (12 bits + 4 pad)."""
        ext = ExtensionBlock(
            temporal=TemporalBlock(
                decay_fn=DECAY_EXPONENTIAL,
                half_life_encoded=121,
            ),
            triggers=None,
        )
        data = encode_extensions(ext)
        assert len(data) == 2

    def test_temporal_only_bit_layout(self):
        """Verify exact bit positions for temporal-only block.

        has_temporal=1, has_triggers=0, decay_fn=00, half_life=121(0b01111001)

        Bit stream: 1 0 | 00 | 01111001 | 0000 (pad)
        byte 0: 10000111 = 0x87
        byte 1: 10010000 = 0x90
        """
        ext = ExtensionBlock(
            temporal=TemporalBlock(
                decay_fn=DECAY_EXPONENTIAL,  # 00
                half_life_encoded=121,       # 0b01111001
            ),
            triggers=None,
        )
        data = encode_extensions(ext)
        assert data == bytes([0x87, 0x90])

    def test_temporal_linear_decay_bit_layout(self):
        """decay_fn=01 (linear), half_life=200(0b11001000).

        Bit stream: 1 0 | 01 | 11001000 | 0000 (pad)
        byte 0: 10011100 = 0x9C
        byte 1: 10000000 = 0x80
        """
        ext = ExtensionBlock(
            temporal=TemporalBlock(
                decay_fn=DECAY_LINEAR,  # 01
                half_life_encoded=200,  # 0b11001000
            ),
            triggers=None,
        )
        data = encode_extensions(ext)
        assert data == bytes([0x9C, 0x80])

    def test_temporal_step_decay_bit_layout(self):
        """decay_fn=10 (step), half_life=0(0b00000000).

        Bit stream: 1 0 | 10 | 00000000 | 0000 (pad)
        byte 0: 10100000 = 0xA0
        byte 1: 00000000 = 0x00
        """
        ext = ExtensionBlock(
            temporal=TemporalBlock(
                decay_fn=DECAY_STEP,  # 10
                half_life_encoded=0,  # 0b00000000
            ),
            triggers=None,
        )
        data = encode_extensions(ext)
        assert data == bytes([0xA0, 0x00])

    def test_triggers_only_expiry(self):
        """Single expiry trigger without temporal metadata.

        has_temporal=0, has_triggers=1
        trigger_count=001 (1)
        trigger_type=00 (expiry)
        gamma_q=0 (hard expiry, 8 bits)

        Bit stream: 0 1 | 001 | 00 | 00000000 | 0 (pad)
        = 01 001 00 0  |  0000000 0
        byte 0: 01001000 = 0x48
        byte 1: 00000000 = 0x00
        """
        ext = ExtensionBlock(
            temporal=None,
            triggers=[Trigger(trigger_type=TRIGGER_EXPIRY, parameter=0)],
        )
        data = encode_extensions(ext)
        assert len(data) == 2
        assert data == bytes([0x48, 0x00])

    def test_temporal_and_one_expiry_trigger(self):
        """Temporal + 1 expiry trigger (gamma=0).

        has_temporal=1, has_triggers=1
        decay_fn=00, half_life=121(01111001)
        trigger_count=001, trigger_type=00, gamma_q=00000000

        Bit stream: 1 1 00 01111001 001 00 00000000 0000000
        Total: 2+2+8+3+2+8 = 25 bits → 4 bytes (7 pad)
        """
        ext = ExtensionBlock(
            temporal=TemporalBlock(
                decay_fn=DECAY_EXPONENTIAL,
                half_life_encoded=121,
            ),
            triggers=[Trigger(trigger_type=TRIGGER_EXPIRY, parameter=0)],
        )
        data = encode_extensions(ext)
        assert len(data) == 4  # ceil(25/8) = 4

    def test_temporal_and_one_review_trigger(self):
        """Temporal + 1 review_due trigger (acceleration=128).

        trigger_type=01 (review_due), acceleration_q=128(10000000)
        Same structure as expiry: 25 bits → 4 bytes.
        """
        ext = ExtensionBlock(
            temporal=TemporalBlock(
                decay_fn=DECAY_EXPONENTIAL,
                half_life_encoded=100,
            ),
            triggers=[Trigger(trigger_type=TRIGGER_REVIEW_DUE, parameter=128)],
        )
        data = encode_extensions(ext)
        assert len(data) == 4

    def test_reg_change_trigger_no_payload(self):
        """Regulatory change trigger has 0 bits of payload.

        has_temporal=0, has_triggers=1
        trigger_count=001, trigger_type=10 (reg_change)

        Bit stream: 0 1 001 10 0 = 8 bits → 1 byte
        byte 0: 01001100 = 0x4C
        """
        ext = ExtensionBlock(
            temporal=None,
            triggers=[Trigger(trigger_type=TRIGGER_REG_CHANGE, parameter=0)],
        )
        data = encode_extensions(ext)
        assert len(data) == 1
        assert data == bytes([0x4C])

    def test_withdrawal_trigger_no_payload(self):
        """Withdrawal trigger has 0 bits of payload.

        trigger_type=11 (withdrawal)
        Bit stream: 0 1 001 11 0 = 8 bits → 1 byte
        byte 0: 01001110 = 0x4E
        """
        ext = ExtensionBlock(
            temporal=None,
            triggers=[Trigger(trigger_type=TRIGGER_WITHDRAWAL, parameter=0)],
        )
        data = encode_extensions(ext)
        assert len(data) == 1
        assert data == bytes([0x4E])

    def test_multiple_triggers(self):
        """3 triggers: expiry(gamma=255) + review(accel=64) + reg_change.

        trigger_count=011(3)
        Trigger 1: type=00(expiry), gamma=11111111(255) → 10 bits
        Trigger 2: type=01(review), accel=01000000(64) → 10 bits
        Trigger 3: type=10(reg_change) → 2 bits

        Without temporal:
        flags: 01 = 2 bits
        trigger_count: 011 = 3 bits
        trigger data: 10+10+2 = 22 bits
        Total: 2+3+22 = 27 bits → 4 bytes (5 pad)
        """
        ext = ExtensionBlock(
            temporal=None,
            triggers=[
                Trigger(trigger_type=TRIGGER_EXPIRY, parameter=255),
                Trigger(trigger_type=TRIGGER_REVIEW_DUE, parameter=64),
                Trigger(trigger_type=TRIGGER_REG_CHANGE, parameter=0),
            ],
        )
        data = encode_extensions(ext)
        assert len(data) == 4

    def test_roundtrip_temporal_only(self):
        """Encode → decode round-trip for temporal-only block."""
        original = ExtensionBlock(
            temporal=TemporalBlock(
                decay_fn=DECAY_LINEAR,
                half_life_encoded=200,
            ),
            triggers=None,
        )
        data = encode_extensions(original)
        recovered = decode_extensions(data)
        assert recovered.temporal is not None
        assert recovered.temporal.decay_fn == DECAY_LINEAR
        assert recovered.temporal.half_life_encoded == 200
        assert recovered.triggers is None

    def test_roundtrip_triggers_only(self):
        """Encode → decode round-trip for triggers-only block."""
        original = ExtensionBlock(
            temporal=None,
            triggers=[
                Trigger(trigger_type=TRIGGER_EXPIRY, parameter=128),
            ],
        )
        data = encode_extensions(original)
        recovered = decode_extensions(data)
        assert recovered.temporal is None
        assert recovered.triggers is not None
        assert len(recovered.triggers) == 1
        assert recovered.triggers[0].trigger_type == TRIGGER_EXPIRY
        assert recovered.triggers[0].parameter == 128

    def test_roundtrip_temporal_and_triggers(self):
        """Full round-trip: temporal + multiple triggers."""
        original = ExtensionBlock(
            temporal=TemporalBlock(
                decay_fn=DECAY_STEP,
                half_life_encoded=50,
            ),
            triggers=[
                Trigger(trigger_type=TRIGGER_EXPIRY, parameter=0),
                Trigger(trigger_type=TRIGGER_REVIEW_DUE, parameter=200),
                Trigger(trigger_type=TRIGGER_REG_CHANGE, parameter=0),
                Trigger(trigger_type=TRIGGER_WITHDRAWAL, parameter=0),
            ],
        )
        data = encode_extensions(original)
        recovered = decode_extensions(data)

        assert recovered.temporal is not None
        assert recovered.temporal.decay_fn == DECAY_STEP
        assert recovered.temporal.half_life_encoded == 50

        assert recovered.triggers is not None
        assert len(recovered.triggers) == 4
        assert recovered.triggers[0].trigger_type == TRIGGER_EXPIRY
        assert recovered.triggers[0].parameter == 0
        assert recovered.triggers[1].trigger_type == TRIGGER_REVIEW_DUE
        assert recovered.triggers[1].parameter == 200
        assert recovered.triggers[2].trigger_type == TRIGGER_REG_CHANGE
        assert recovered.triggers[3].trigger_type == TRIGGER_WITHDRAWAL

    @given(
        decay_fn=st.integers(min_value=0, max_value=2),
        half_life_encoded=st.integers(min_value=0, max_value=255),
    )
    @settings(max_examples=200, deadline=None)
    def test_roundtrip_temporal_property(self, decay_fn, half_life_encoded):
        """Property: any valid temporal block round-trips exactly."""
        original = ExtensionBlock(
            temporal=TemporalBlock(decay_fn=decay_fn, half_life_encoded=half_life_encoded),
            triggers=None,
        )
        data = encode_extensions(original)
        recovered = decode_extensions(data)
        assert recovered.temporal.decay_fn == decay_fn
        assert recovered.temporal.half_life_encoded == half_life_encoded

    @given(
        trigger_type=st.integers(min_value=0, max_value=3),
        parameter=st.integers(min_value=0, max_value=255),
    )
    @settings(max_examples=200, deadline=None)
    def test_roundtrip_single_trigger_property(self, trigger_type, parameter):
        """Property: any single trigger round-trips exactly."""
        original = ExtensionBlock(
            temporal=None,
            triggers=[Trigger(trigger_type=trigger_type, parameter=parameter)],
        )
        data = encode_extensions(original)
        recovered = decode_extensions(data)
        assert len(recovered.triggers) == 1
        assert recovered.triggers[0].trigger_type == trigger_type
        # For reg_change and withdrawal (no payload), parameter is not on wire
        if trigger_type in (TRIGGER_EXPIRY, TRIGGER_REVIEW_DUE):
            assert recovered.triggers[0].parameter == parameter

    def test_trigger_count_max_seven(self):
        """Maximum 7 triggers (3-bit count field)."""
        triggers = [Trigger(trigger_type=TRIGGER_REG_CHANGE, parameter=0)] * 7
        ext = ExtensionBlock(temporal=None, triggers=triggers)
        data = encode_extensions(ext)
        recovered = decode_extensions(data)
        assert len(recovered.triggers) == 7

    def test_trigger_count_zero_raises(self):
        """Empty trigger list is invalid — use triggers=None instead."""
        ext = ExtensionBlock(temporal=None, triggers=[])
        with pytest.raises(ValueError, match="[Ee]mpty|[Zz]ero|[Nn]o trigger"):
            encode_extensions(ext)

    def test_trigger_count_exceeds_seven_raises(self):
        """More than 7 triggers exceeds the 3-bit field."""
        triggers = [Trigger(trigger_type=TRIGGER_REG_CHANGE, parameter=0)] * 8
        ext = ExtensionBlock(temporal=None, triggers=triggers)
        with pytest.raises(ValueError, match="[Ee]xceed|[Mm]ax|7"):
            encode_extensions(ext)


# =========================================================================
# 3. Quantized decay — Axiom 3 preservation
#
# Dequantize → decay at float precision → re-quantize.
# Constrained quantization guarantees b̂+d̂+û = 2ⁿ−1 exactly.
# =========================================================================

class TestApplyDecayQuantized:
    """Quantized decay preserves Axiom 3 and matches reference implementation."""

    @given(
        opinion=quantized_opinion_strategy(8),
        factor=st.floats(min_value=0.0, max_value=1.0,
                         allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=1000, deadline=None)
    def test_decay_preserves_axiom3_8bit(self, opinion, factor):
        """Axiom 3: b̂+d̂+û = 255 after decay, for any input and factor."""
        b_q, d_q, u_q, a_q = opinion
        rb, rd, ru, ra = apply_decay_quantized(b_q, d_q, u_q, a_q, factor, precision=8)
        assert rb + rd + ru == 255, (
            f"Axiom 3 violated after decay: {rb}+{rd}+{ru}={rb+rd+ru} "
            f"(input=({b_q},{d_q},{u_q}), factor={factor})"
        )

    @given(
        opinion=quantized_opinion_strategy(16),
        factor=st.floats(min_value=0.0, max_value=1.0,
                         allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=500, deadline=None)
    def test_decay_preserves_axiom3_16bit(self, opinion, factor):
        """Axiom 3 at 16-bit precision."""
        b_q, d_q, u_q, a_q = opinion
        rb, rd, ru, ra = apply_decay_quantized(b_q, d_q, u_q, a_q, factor, precision=16)
        assert rb + rd + ru == 65535

    def test_decay_factor_zero_yields_vacuous(self):
        """Factor 0 → vacuous opinion (0, 0, max_val, a)."""
        b_q, d_q, u_q, a_q = 217, 13, 25, 128
        rb, rd, ru, ra = apply_decay_quantized(b_q, d_q, u_q, a_q, 0.0, precision=8)
        assert rb == 0
        assert rd == 0
        assert ru == 255
        assert ra == a_q  # base rate unchanged

    def test_decay_factor_one_yields_same(self):
        """Factor 1 → opinion unchanged."""
        b_q, d_q, u_q, a_q = 217, 13, 25, 128
        rb, rd, ru, ra = apply_decay_quantized(b_q, d_q, u_q, a_q, 1.0, precision=8)
        assert rb == b_q
        assert rd == d_q
        assert ru == u_q
        assert ra == a_q

    def test_decay_base_rate_preserved(self):
        """Decay never changes the base rate."""
        b_q, d_q, u_q, a_q = 200, 30, 25, 77
        rb, rd, ru, ra = apply_decay_quantized(b_q, d_q, u_q, a_q, 0.5, precision=8)
        assert ra == 77

    @given(
        opinion=quantized_opinion_strategy(8),
        factor=st.floats(min_value=0.01, max_value=0.99,
                         allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=500, deadline=None)
    def test_decay_increases_uncertainty(self, opinion, factor):
        """Decay always increases (or maintains) uncertainty."""
        b_q, d_q, u_q, a_q = opinion
        rb, rd, ru, ra = apply_decay_quantized(b_q, d_q, u_q, a_q, factor, precision=8)
        assert ru >= u_q or (b_q == 0 and d_q == 0), (
            f"Uncertainty decreased: {u_q} → {ru} "
            f"(input=({b_q},{d_q},{u_q}), factor={factor})"
        )

    def test_decay_matches_jsonld_ex_reference(self):
        """Quantized decay produces the same result as dequantize → jsonld-ex
        decay → re-quantize."""
        b_q, d_q, u_q, a_q = quantize_binomial(0.85, 0.05, 0.10, 0.50, precision=8)
        factor = 0.5

        # Our function
        rb, rd, ru, ra = apply_decay_quantized(b_q, d_q, u_q, a_q, factor, precision=8)

        # Reference: manual dequantize → decay → re-quantize
        b, d, u, a = dequantize_binomial(b_q, d_q, u_q, a_q, precision=8)
        op = Opinion(belief=b, disbelief=d, uncertainty=u, base_rate=a)
        # decay_opinion uses exponential_decay internally, but we just want
        # to apply a raw factor, so we compute manually
        b_decayed = factor * b
        d_decayed = factor * d
        u_decayed = 1.0 - b_decayed - d_decayed
        ref_b, ref_d, ref_u, ref_a = quantize_binomial(
            b_decayed, d_decayed, u_decayed, a, precision=8,
        )

        assert rb == ref_b
        assert rd == ref_d
        assert ru == ref_u
        assert ra == ref_a


# =========================================================================
# 4. Quantized expiry trigger — Axiom 3 preservation
#
# Expiry transfers lawfulness → violation:
#   b' = gamma * b
#   d' = d + (1 - gamma) * b
#   u' = u  (unchanged)
#
# Constraint: b' + d' + u' = gamma*b + d + (1-gamma)*b + u = b+d+u = 1. ∎
# =========================================================================

class TestApplyExpiryQuantized:
    """Quantized expiry trigger preserves Axiom 3."""

    @given(
        opinion=quantized_opinion_strategy(8),
        gamma_q=st.integers(min_value=0, max_value=255),
    )
    @settings(max_examples=1000, deadline=None)
    def test_expiry_preserves_axiom3_8bit(self, opinion, gamma_q):
        """Axiom 3: b̂+d̂+û = 255 after expiry, for any input and gamma."""
        b_q, d_q, u_q, a_q = opinion
        rb, rd, ru, ra = apply_expiry_quantized(b_q, d_q, u_q, a_q, gamma_q, precision=8)
        assert rb + rd + ru == 255, (
            f"Axiom 3 violated after expiry: {rb}+{rd}+{ru}={rb+rd+ru} "
            f"(input=({b_q},{d_q},{u_q}), gamma_q={gamma_q})"
        )

    def test_expiry_hard_gamma_zero(self):
        """Hard expiry (gamma=0): all belief transfers to disbelief.

        b' = 0, d' = d + b, u' = u.
        """
        b_q, d_q, u_q, a_q = quantize_binomial(0.85, 0.05, 0.10, 0.50, precision=8)
        rb, rd, ru, ra = apply_expiry_quantized(b_q, d_q, u_q, a_q, 0, precision=8)

        assert rb == 0, "Hard expiry must zero out belief"
        # d' = d + b (in dequantized domain, then re-quantized)
        # u should be unchanged (within quantization tolerance)
        assert rb + rd + ru == 255

    def test_expiry_no_effect_gamma_255(self):
        """No-op expiry (gamma=255, i.e. gamma≈1): opinion unchanged."""
        b_q, d_q, u_q, a_q = 217, 13, 25, 128
        rb, rd, ru, ra = apply_expiry_quantized(b_q, d_q, u_q, a_q, 255, precision=8)

        assert rb == b_q
        assert rd == d_q
        assert ru == u_q

    def test_expiry_half_gamma_128(self):
        """Half expiry (gamma=128 ≈ 0.502): ~half of belief transfers."""
        b_q, d_q, u_q, a_q = quantize_binomial(0.80, 0.10, 0.10, 0.50, precision=8)
        rb, rd, ru, ra = apply_expiry_quantized(b_q, d_q, u_q, a_q, 128, precision=8)

        # Belief should roughly halve
        b_orig, _, _, _ = dequantize_binomial(b_q, d_q, u_q, a_q, precision=8)
        b_new, _, _, _ = dequantize_binomial(rb, rd, ru, ra, precision=8)
        assert b_new < b_orig
        assert b_new == pytest.approx(b_orig * 0.502, abs=0.01)

    def test_expiry_base_rate_preserved(self):
        """Expiry never changes the base rate."""
        b_q, d_q, u_q, a_q = 200, 30, 25, 77
        rb, rd, ru, ra = apply_expiry_quantized(b_q, d_q, u_q, a_q, 64, precision=8)
        assert ra == 77

    def test_expiry_on_vacuous_opinion(self):
        """Expiry on vacuous opinion (b=0) is a no-op regardless of gamma."""
        b_q, d_q, u_q, a_q = 0, 0, 255, 128
        rb, rd, ru, ra = apply_expiry_quantized(b_q, d_q, u_q, a_q, 0, precision=8)
        assert rb == 0
        assert rd == 0
        assert ru == 255


# =========================================================================
# 5. Decay factor computation
#
# Three built-in functions matching jsonld-ex:
#   exponential: λ(t,τ) = 2^(-t/τ)
#   linear:      λ(t,τ) = max(0, 1 - t/(2τ))
#   step:        λ(t,τ) = 1 if t < τ else 0
# =========================================================================

class TestComputeDecayFactor:
    """Decay factor computation from wire parameters."""

    def test_exponential_at_zero_elapsed(self):
        """No time elapsed → factor = 1.0."""
        assert compute_decay_factor(DECAY_EXPONENTIAL, 3600.0, 0.0) == 1.0

    def test_exponential_at_half_life(self):
        """At exactly one half-life → factor = 0.5."""
        assert compute_decay_factor(DECAY_EXPONENTIAL, 3600.0, 3600.0) == pytest.approx(0.5)

    def test_exponential_at_two_half_lives(self):
        """At two half-lives → factor = 0.25."""
        assert compute_decay_factor(DECAY_EXPONENTIAL, 100.0, 200.0) == pytest.approx(0.25)

    def test_linear_at_zero(self):
        """No time elapsed → factor = 1.0."""
        assert compute_decay_factor(DECAY_LINEAR, 3600.0, 0.0) == 1.0

    def test_linear_at_half_life(self):
        """At half-life → factor = 0.5. Linear: λ(t,τ) = max(0, 1−t/(2τ))."""
        assert compute_decay_factor(DECAY_LINEAR, 3600.0, 3600.0) == pytest.approx(0.5)

    def test_linear_at_double_half_life(self):
        """At 2τ → factor = 0.0 (hard zero)."""
        assert compute_decay_factor(DECAY_LINEAR, 3600.0, 7200.0) == 0.0

    def test_linear_beyond_double_half_life(self):
        """Beyond 2τ → still 0.0 (clamped)."""
        assert compute_decay_factor(DECAY_LINEAR, 100.0, 500.0) == 0.0

    def test_step_before_half_life(self):
        """Before half-life → factor = 1.0."""
        assert compute_decay_factor(DECAY_STEP, 3600.0, 3599.0) == 1.0

    def test_step_at_half_life(self):
        """At half-life → factor = 0.0 (drops to zero)."""
        assert compute_decay_factor(DECAY_STEP, 3600.0, 3600.0) == 0.0

    def test_step_after_half_life(self):
        """After half-life → factor = 0.0."""
        assert compute_decay_factor(DECAY_STEP, 100.0, 101.0) == 0.0

    def test_unknown_decay_fn_raises(self):
        """Reserved/unknown decay function code raises ValueError."""
        with pytest.raises(ValueError):
            compute_decay_factor(3, 100.0, 50.0)

    @given(
        half_life=st.floats(min_value=1.0, max_value=1e6,
                            allow_nan=False, allow_infinity=False),
        elapsed=st.floats(min_value=0.0, max_value=1e6,
                          allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=500, deadline=None)
    def test_exponential_matches_jsonld_ex(self, half_life, elapsed):
        """Our exponential matches jsonld-ex exponential_decay exactly."""
        ours = compute_decay_factor(DECAY_EXPONENTIAL, half_life, elapsed)
        ref = exponential_decay(elapsed, half_life)
        assert ours == pytest.approx(ref, abs=1e-12)

    @given(
        half_life=st.floats(min_value=1.0, max_value=1e6,
                            allow_nan=False, allow_infinity=False),
        elapsed=st.floats(min_value=0.0, max_value=1e6,
                          allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=500, deadline=None)
    def test_linear_matches_jsonld_ex(self, half_life, elapsed):
        """Our linear matches jsonld-ex linear_decay."""
        ours = compute_decay_factor(DECAY_LINEAR, half_life, elapsed)
        ref = linear_decay(elapsed, half_life)
        assert ours == pytest.approx(ref, abs=1e-12)

    @given(
        half_life=st.floats(min_value=1.0, max_value=1e6,
                            allow_nan=False, allow_infinity=False),
        elapsed=st.floats(min_value=0.0, max_value=1e6,
                          allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=500, deadline=None)
    def test_step_matches_jsonld_ex(self, half_life, elapsed):
        """Our step matches jsonld-ex step_decay."""
        ours = compute_decay_factor(DECAY_STEP, half_life, elapsed)
        ref = step_decay(elapsed, half_life)
        assert ours == pytest.approx(ref, abs=1e-12)

    @given(
        decay_fn=st.integers(min_value=0, max_value=2),
        half_life=st.floats(min_value=1.0, max_value=1e6,
                            allow_nan=False, allow_infinity=False),
        elapsed=st.floats(min_value=0.0, max_value=1e6,
                          allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=500, deadline=None)
    def test_factor_always_in_unit_interval(self, decay_fn, half_life, elapsed):
        """Every decay function returns a factor in [0, 1]."""
        factor = compute_decay_factor(decay_fn, half_life, elapsed)
        assert 0.0 <= factor <= 1.0


# =========================================================================
# 6. Integration with Annotation encode/decode pipeline
#
# Extensions are detected by remaining bytes in the annotation byte string
# after header + opinion. Zero cost when absent.
# =========================================================================

class TestAnnotationWithExtensions:
    """Temporal extensions integrate with the annotation pipeline."""

    def test_annotation_without_extensions_unchanged(self):
        """Existing annotations without extensions still work identically."""
        ann = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.BITS_8,
            ),
            opinion=quantize_binomial(0.85, 0.05, 0.10, 0.50),
        )
        data = encode_annotation(ann)
        assert len(data) == 4  # 1 header + 3 opinion, no extensions

        recovered = decode_annotation(data)
        assert recovered.opinion == ann.opinion
        assert recovered.extensions is None

    def test_annotation_tier1_with_temporal(self):
        """Tier 1 annotation + temporal extension.

        4 bytes (annotation) + 2 bytes (temporal) = 6 bytes total.
        """
        ext = ExtensionBlock(
            temporal=TemporalBlock(
                decay_fn=DECAY_EXPONENTIAL,
                half_life_encoded=121,
            ),
            triggers=None,
        )
        ann = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.BITS_8,
            ),
            opinion=quantize_binomial(0.85, 0.05, 0.10, 0.50),
            extensions=ext,
        )
        data = encode_annotation(ann)
        assert len(data) == 6  # 1 header + 3 opinion + 2 extension

        recovered = decode_annotation(data)
        assert recovered.opinion is not None
        assert recovered.extensions is not None
        assert recovered.extensions.temporal.decay_fn == DECAY_EXPONENTIAL
        assert recovered.extensions.temporal.half_life_encoded == 121
        assert recovered.extensions.triggers is None

    def test_annotation_tier2_with_temporal_and_trigger(self):
        """Tier 2 annotation + temporal + 1 trigger.

        7 bytes (T2 annotation) + 4 bytes (temporal+trigger) = 11 bytes total.
        """
        ext = ExtensionBlock(
            temporal=TemporalBlock(
                decay_fn=DECAY_LINEAR,
                half_life_encoded=167,
            ),
            triggers=[Trigger(trigger_type=TRIGGER_EXPIRY, parameter=0)],
        )
        ann = Annotation(
            header=Tier2Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.BITS_8,
                operator_id=OperatorId.TEMPORAL_DECAY,
                reasoning_context=0,
                context_version=1,
                has_multinomial=False,
                sub_tier_depth=0,
                source_count=5,
            ),
            opinion=quantize_binomial(0.70, 0.10, 0.20, 0.50),
            extensions=ext,
        )
        data = encode_annotation(ann)
        assert len(data) == 11  # 4 header + 3 opinion + 4 extension

        recovered = decode_annotation(data)
        assert recovered.extensions is not None
        assert recovered.extensions.temporal.decay_fn == DECAY_LINEAR
        assert recovered.extensions.temporal.half_life_encoded == 167
        assert len(recovered.extensions.triggers) == 1
        assert recovered.extensions.triggers[0].trigger_type == TRIGGER_EXPIRY
        assert recovered.extensions.triggers[0].parameter == 0

    def test_annotation_extension_detection(self):
        """Decoder correctly distinguishes 'no extensions' from 'extensions present'."""
        opinion = quantize_binomial(0.80, 0.10, 0.10, 0.50)

        # Without extensions: exactly 4 bytes
        ann_no_ext = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.BITS_8,
            ),
            opinion=opinion,
        )
        data_no_ext = encode_annotation(ann_no_ext)
        recovered_no_ext = decode_annotation(data_no_ext)
        assert recovered_no_ext.extensions is None

        # With extensions: more than 4 bytes
        ann_ext = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.BITS_8,
            ),
            opinion=opinion,
            extensions=ExtensionBlock(
                temporal=TemporalBlock(decay_fn=DECAY_STEP, half_life_encoded=10),
                triggers=None,
            ),
        )
        data_ext = encode_annotation(ann_ext)
        recovered_ext = decode_annotation(data_ext)
        assert recovered_ext.extensions is not None

    @given(
        decay_fn=st.integers(min_value=0, max_value=2),
        half_life_encoded=st.integers(min_value=0, max_value=255),
        opinion=st.tuples(
            st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
            st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
        ).filter(lambda t: t[0] + t[1] <= 1.0).map(
            lambda t: (t[0], t[1], 1.0 - t[0] - t[1], 0.5)
        ),
    )
    @settings(max_examples=200, deadline=None)
    def test_annotation_with_temporal_roundtrip_property(self, decay_fn, half_life_encoded, opinion):
        """Property: annotation with temporal extension round-trips through encode/decode."""
        b, d, u, a = opinion
        q = quantize_binomial(b, d, u, a, precision=8)

        ann = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.BITS_8,
            ),
            opinion=q,
            extensions=ExtensionBlock(
                temporal=TemporalBlock(decay_fn=decay_fn, half_life_encoded=half_life_encoded),
                triggers=None,
            ),
        )
        data = encode_annotation(ann)
        recovered = decode_annotation(data)

        # Opinion round-trips exactly
        assert recovered.opinion == ann.opinion
        # Temporal round-trips exactly
        assert recovered.extensions.temporal.decay_fn == decay_fn
        assert recovered.extensions.temporal.half_life_encoded == half_life_encoded
        # Axiom 3 still holds
        rb, rd, ru, ra = recovered.opinion
        assert rb + rd + ru == 255


# =========================================================================
# 7. End-to-end: full codec pipeline with temporal extensions
# =========================================================================

class TestTemporalFullPipeline:
    """Temporal extensions through the full CBOR-LD-ex encode/decode codec."""

    def test_axiom1_stripping_with_temporal(self):
        """Axiom 1: stripping a message with temporal annotations
        yields valid CBOR-LD data."""
        import cbor2
        import json
        from cbor_ld_ex.codec import encode, decode, ContextRegistry, ANNOTATION_TERM_ID

        registry = ContextRegistry(
            key_map={"@context": 0, "@type": 1, "value": 2},
            value_map={"https://schema.org/": 100, "Observation": 101},
        )
        doc = {"@context": "https://schema.org/", "@type": "Observation", "value": 22.5}
        ann = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.BITS_8,
            ),
            opinion=quantize_binomial(0.85, 0.05, 0.10, 0.50),
            extensions=ExtensionBlock(
                temporal=TemporalBlock(decay_fn=DECAY_EXPONENTIAL, half_life_encoded=121),
                triggers=[Trigger(trigger_type=TRIGGER_EXPIRY, parameter=0)],
            ),
        )

        encoded = encode(doc, ann, context_registry=registry)

        # Strip annotation
        raw_map = cbor2.loads(encoded)
        stripped = {k: v for k, v in raw_map.items() if k != ANNOTATION_TERM_ID}

        # Still valid CBOR
        stripped_bytes = cbor2.dumps(stripped)
        re_decoded = cbor2.loads(stripped_bytes)
        assert isinstance(re_decoded, dict)

        # Decompresses to valid JSON-LD
        json_ld = registry.decompress(re_decoded)
        json.dumps(json_ld)  # must not raise
        assert json_ld["value"] == 22.5

    def test_axiom3_through_decay_pipeline(self):
        """Axiom 3 through full pipeline: encode → decode → decay → re-encode.

        Simulates a Tier 2 gateway receiving a Tier 1 message with temporal
        metadata, applying decay, and re-encoding the result.
        """
        from cbor_ld_ex.codec import encode, decode, ContextRegistry

        registry = ContextRegistry(
            key_map={"@context": 0, "value": 1},
            value_map={"https://schema.org/": 100},
        )

        # Tier 1 sends: opinion with temporal metadata
        opinion = quantize_binomial(0.90, 0.05, 0.05, 0.50, precision=8)
        ann = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.BITS_8,
            ),
            opinion=opinion,
            extensions=ExtensionBlock(
                temporal=TemporalBlock(decay_fn=DECAY_EXPONENTIAL, half_life_encoded=102),
                triggers=None,
            ),
        )
        doc = {"@context": "https://schema.org/", "value": 22.5}
        tier1_bytes = encode(doc, ann, context_registry=registry)

        # Tier 2 receives and decodes
        recovered_doc, recovered_ann = decode(tier1_bytes, context_registry=registry)
        assert recovered_ann.extensions is not None
        assert recovered_ann.extensions.temporal is not None

        # Tier 2 applies decay
        half_life_seconds = decode_half_life(recovered_ann.extensions.temporal.half_life_encoded)
        elapsed = half_life_seconds  # one half-life elapsed
        factor = compute_decay_factor(
            recovered_ann.extensions.temporal.decay_fn,
            half_life_seconds,
            elapsed,
        )
        assert factor == pytest.approx(0.5, abs=0.01)

        b_q, d_q, u_q, a_q = recovered_ann.opinion
        db, dd, du, da = apply_decay_quantized(b_q, d_q, u_q, a_q, factor, precision=8)

        # Axiom 3 still holds
        assert db + dd + du == 255

        # Re-encode as Tier 2 message
        tier2_ann = Annotation(
            header=Tier2Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.BITS_8,
                operator_id=OperatorId.TEMPORAL_DECAY,
                reasoning_context=0,
                context_version=1,
                has_multinomial=False,
                sub_tier_depth=0,
                source_count=1,
            ),
            opinion=(db, dd, du, da),
        )
        tier2_bytes = encode(recovered_doc, tier2_ann, context_registry=registry)

        # Final decode: Axiom 3 verified
        final_doc, final_ann = decode(tier2_bytes, context_registry=registry)
        fb, fd, fu, fa = final_ann.opinion
        assert fb + fd + fu == 255
