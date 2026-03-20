"""
Phase 1 tests: Quantization codec for Subjective Logic opinions.

Tests are derived from FORMAL_MODEL.md §4 (Quantization Theory):
  - Definition 9:  Q_n(x) = round(x * (2^n - 1))
  - Definition 10: Constrained binomial quantization (derive û)
  - Definition 11: Constrained multinomial quantization (derive b̂_k)
  - Theorem 1:  b̂ + d̂ + û = 2^n - 1 exactly
  - Theorem 2:  Per-component error bounds
  - Theorem 3:  Multinomial constraint preservation

All tests target: src/cbor_ld_ex/opinions.py
"""

import math
import struct

import pytest
from hypothesis import given, assume, settings
from hypothesis import strategies as st

from cbor_ld_ex.opinions import (
    quantize_binomial,
    dequantize_binomial,
    quantize_multinomial,
    dequantize_multinomial,
    encode_opinion_bytes,
    decode_opinion_bytes,
)


# ---------------------------------------------------------------------------
# Hypothesis strategies for valid SL opinions
# ---------------------------------------------------------------------------

def valid_binomial_opinion():
    """Generate a valid binomial opinion (b, d, u, a) with b+d+u=1."""
    return (
        st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
        .flatmap(lambda b:
            st.floats(
                min_value=0.0,
                max_value=1.0 - b,
                allow_nan=False,
                allow_infinity=False,
            ).map(lambda d: (b, d, 1.0 - b - d))
        )
        .flatmap(lambda bdu:
            st.floats(
                min_value=0.0,
                max_value=1.0,
                allow_nan=False,
                allow_infinity=False,
            ).map(lambda a: (bdu[0], bdu[1], bdu[2], a))
        )
    )


# ---------------------------------------------------------------------------
# 1. Binomial roundtrip — worked example from FORMAL_MODEL.md §C.3
# ---------------------------------------------------------------------------

class TestQuantizeBinomialRoundtrip:
    """Tests for constrained binomial quantization roundtrip (Def 10)."""

    def test_roundtrip_8bit_worked_example(self):
        """Worked example from FORMAL_MODEL.md Appendix C.3.

        ω = (0.85, 0.05, 0.10, 0.50) at 8-bit:
          b̂ = round(0.85 * 255) = 217
          d̂ = round(0.05 * 255) = 13
          û = 255 - 217 - 13    = 25
          â = round(0.50 * 255) = 128
        """
        b_q, d_q, u_q, a_q = quantize_binomial(0.85, 0.05, 0.10, 0.50, precision=8)

        assert b_q == 217
        assert d_q == 13
        assert u_q == 25
        assert a_q == 128

        # Dequantize and verify
        b_r, d_r, u_r, a_r = dequantize_binomial(b_q, d_q, u_q, a_q, precision=8)

        # Theorem 1(b): sum equals 1.0 in real arithmetic. In IEEE 754,
        # three independent divisions may differ from 1.0 by a few ULPs.
        # The exact guarantee is in the integer domain (Theorem 1a above).
        assert math.isclose(b_r + d_r + u_r, 1.0, abs_tol=2e-15)

        # Error bounds (Theorem 2): each independently quantized component
        # has error ≤ 1/(2*(2^n - 1)) = 1/510 ≈ 0.00196
        assert abs(b_r - 0.85) <= 1.0 / (2 * 255)
        assert abs(d_r - 0.05) <= 1.0 / (2 * 255)
        assert abs(a_r - 0.50) <= 1.0 / (2 * 255)

        # Derived component u has error ≤ 1/(2^n - 1) = 1/255 ≈ 0.00392
        assert abs(u_r - 0.10) <= 1.0 / 255

    def test_roundtrip_16bit(self):
        """16-bit quantization of the same opinion — tighter error bounds."""
        b_q, d_q, u_q, a_q = quantize_binomial(0.85, 0.05, 0.10, 0.50, precision=16)

        max_val = 2**16 - 1  # 65535
        assert b_q == round(0.85 * max_val)
        assert d_q == round(0.05 * max_val)
        assert u_q == max_val - b_q - d_q
        assert a_q == round(0.50 * max_val)

        # Quantized domain sum (Theorem 1a)
        assert b_q + d_q + u_q == max_val

        b_r, d_r, u_r, a_r = dequantize_binomial(b_q, d_q, u_q, a_q, precision=16)
        assert math.isclose(b_r + d_r + u_r, 1.0, abs_tol=2e-15)


# ---------------------------------------------------------------------------
# 2. Constraint preservation — Theorem 1 via Hypothesis
# ---------------------------------------------------------------------------

class TestConstraintPreservation:
    """Theorem 1: b̂ + d̂ + û = 2^n - 1 for all valid opinions."""

    @given(opinion=valid_binomial_opinion())
    @settings(max_examples=500)
    def test_quantized_sum_8bit(self, opinion):
        """Property: quantized sum is exactly 255 for any valid opinion."""
        b, d, u, a = opinion
        assume(u >= 0)  # Guard against floating-point edge cases

        b_q, d_q, u_q, a_q = quantize_binomial(b, d, u, a, precision=8)

        # Theorem 1(a): exact sum in quantized domain
        assert b_q + d_q + u_q == 255, (
            f"Constraint violated: {b_q} + {d_q} + {u_q} = {b_q + d_q + u_q} != 255 "
            f"for opinion ({b}, {d}, {u}, {a})"
        )

        # All components non-negative (Theorem 1(c))
        assert b_q >= 0
        assert d_q >= 0
        assert u_q >= 0

    @given(opinion=valid_binomial_opinion())
    @settings(max_examples=500)
    def test_reconstructed_sum_8bit(self, opinion):
        """Property: reconstructed sum is exactly 1.0 for any valid opinion."""
        b, d, u, a = opinion
        assume(u >= 0)

        b_q, d_q, u_q, a_q = quantize_binomial(b, d, u, a, precision=8)
        b_r, d_r, u_r, a_r = dequantize_binomial(b_q, d_q, u_q, a_q, precision=8)

        # Theorem 1(b): sum equals 1.0 in real arithmetic. In IEEE 754,
        # three independent divisions may differ by a few ULPs (~1e-16).
        # Integer-domain Theorem 1(a) is tested exactly in test_quantized_sum_8bit.
        assert math.isclose(b_r + d_r + u_r, 1.0, abs_tol=2e-15), (
            f"Reconstructed sum: {b_r} + {d_r} + {u_r} = {b_r + d_r + u_r}"
        )

    @given(opinion=valid_binomial_opinion())
    @settings(max_examples=200)
    def test_quantized_sum_16bit(self, opinion):
        """Theorem 1 at 16-bit precision."""
        b, d, u, a = opinion
        assume(u >= 0)

        b_q, d_q, u_q, a_q = quantize_binomial(b, d, u, a, precision=16)
        assert b_q + d_q + u_q == 65535
        assert b_q >= 0 and d_q >= 0 and u_q >= 0


# ---------------------------------------------------------------------------
# 3. Clamping edge case — Theorem 1(c) proof condition
# ---------------------------------------------------------------------------

class TestClampingEdgeCases:
    """When b+d=1 (u=0) and both round up, clamping must fire."""

    def test_zero_uncertainty(self):
        """b=0.5, d=0.5, u=0.0 — classic clamping trigger at 8-bit.

        round(0.5 * 255) = round(127.5) = 128 (banker's rounding)
        b̂ + d̂ = 128 + 128 = 256 > 255 → must clamp d̂ to 127.
        Result: û = 255 - 128 - 127 = 0 ≥ 0 ✓
        """
        b_q, d_q, u_q, a_q = quantize_binomial(0.5, 0.5, 0.0, 0.5, precision=8)

        assert u_q >= 0, f"û = {u_q} < 0, clamping failed"
        assert b_q + d_q + u_q == 255

    def test_near_zero_uncertainty(self):
        """b=0.999, d=0.001, u=0.0 at 8-bit."""
        b_q, d_q, u_q, a_q = quantize_binomial(0.999, 0.001, 0.0, 0.5, precision=8)

        assert u_q >= 0
        assert b_q + d_q + u_q == 255

    def test_one_zero_zero(self):
        """b=1.0, d=0.0, u=0.0 — boundary."""
        b_q, d_q, u_q, a_q = quantize_binomial(1.0, 0.0, 0.0, 0.5, precision=8)

        assert b_q == 255
        assert d_q == 0
        assert u_q == 0
        assert b_q + d_q + u_q == 255

    def test_zero_one_zero(self):
        """b=0.0, d=1.0, u=0.0 — boundary."""
        b_q, d_q, u_q, a_q = quantize_binomial(0.0, 1.0, 0.0, 0.5, precision=8)

        assert b_q == 0
        assert d_q == 255
        assert u_q == 0
        assert b_q + d_q + u_q == 255

    def test_clamping_preserves_belief_bias(self):
        """Per FORMAL_MODEL.md: clamping decrements d̂, not b̂.

        When clamping fires, b̂ is preserved and d̂ absorbs the correction.
        This introduces a marginal bias toward belief — documented and intentional.
        """
        # Find a case where clamping fires: b + d = 1.0 with both rounding up
        # 0.5, 0.5 at 8-bit is the canonical case
        b_q, d_q, u_q, _ = quantize_binomial(0.5, 0.5, 0.0, 0.5, precision=8)

        # b̂ should be round(0.5 * 255) = 128, preserved
        assert b_q == 128, f"Belief was modified during clamping: b̂ = {b_q}"
        # d̂ should have been decremented
        assert d_q <= 128, f"Disbelief was not clamped: d̂ = {d_q}"


# ---------------------------------------------------------------------------
# 4. Error bounds — Theorem 2
# ---------------------------------------------------------------------------

class TestErrorBounds:
    """Theorem 2: per-component quantization error bounds."""

    @given(opinion=valid_binomial_opinion())
    @settings(max_examples=500)
    def test_independently_quantized_error_8bit(self, opinion):
        """For b, d, a: |x - Q⁻¹(Q(x))| ≤ 1/(2*(2^n-1))."""
        b, d, u, a = opinion
        assume(u >= 0)

        b_q, d_q, u_q, a_q = quantize_binomial(b, d, u, a, precision=8)
        b_r, d_r, u_r, a_r = dequantize_binomial(b_q, d_q, u_q, a_q, precision=8)

        max_error_independent = 1.0 / (2 * 255)  # ≈ 0.00196

        assert abs(b_r - b) <= max_error_independent + 1e-15, (
            f"|b_r - b| = {abs(b_r - b)} > {max_error_independent}"
        )
        assert abs(d_r - d) <= max_error_independent + 1e-15, (
            f"|d_r - d| = {abs(d_r - d)} > {max_error_independent}"
        )
        assert abs(a_r - a) <= max_error_independent + 1e-15, (
            f"|a_r - a| = {abs(a_r - a)} > {max_error_independent}"
        )

    @given(opinion=valid_binomial_opinion())
    @settings(max_examples=500)
    def test_derived_component_error_8bit(self, opinion):
        """For u (derived): |u - Q⁻¹(û)| ≤ 1/(2^n-1)."""
        b, d, u, a = opinion
        assume(u >= 0)

        b_q, d_q, u_q, a_q = quantize_binomial(b, d, u, a, precision=8)
        _, _, u_r, _ = dequantize_binomial(b_q, d_q, u_q, a_q, precision=8)

        max_error_derived = 1.0 / 255  # ≈ 0.00392

        assert abs(u_r - u) <= max_error_derived + 1e-15, (
            f"|u_r - u| = {abs(u_r - u)} > {max_error_derived}"
        )


# ---------------------------------------------------------------------------
# 5. Vacuous and extreme opinions
# ---------------------------------------------------------------------------

class TestSpecialOpinions:
    """Edge cases: vacuous opinion, full belief, full disbelief, full uncertainty."""

    def test_vacuous_opinion(self):
        """ω_V = (0, 0, 1, 0.5) — complete ignorance."""
        b_q, d_q, u_q, a_q = quantize_binomial(0.0, 0.0, 1.0, 0.5, precision=8)

        assert b_q == 0
        assert d_q == 0
        assert u_q == 255
        assert a_q == 128
        assert b_q + d_q + u_q == 255

    def test_full_belief(self):
        """ω = (1.0, 0.0, 0.0, 0.5) — complete belief."""
        b_q, d_q, u_q, a_q = quantize_binomial(1.0, 0.0, 0.0, 0.5, precision=8)

        assert b_q == 255
        assert d_q == 0
        assert u_q == 0

    def test_full_disbelief(self):
        """ω = (0.0, 1.0, 0.0, 0.5) — complete disbelief."""
        b_q, d_q, u_q, a_q = quantize_binomial(0.0, 1.0, 0.0, 0.5, precision=8)

        assert b_q == 0
        assert d_q == 255
        assert u_q == 0

    def test_full_uncertainty(self):
        """ω = (0.0, 0.0, 1.0, 0.5) — same as vacuous."""
        b_q, d_q, u_q, a_q = quantize_binomial(0.0, 0.0, 1.0, 0.5, precision=8)

        assert b_q == 0
        assert d_q == 0
        assert u_q == 255

    def test_base_rate_extremes(self):
        """Base rate at 0.0 and 1.0."""
        _, _, _, a_q = quantize_binomial(0.5, 0.3, 0.2, 0.0, precision=8)
        assert a_q == 0

        _, _, _, a_q = quantize_binomial(0.5, 0.3, 0.2, 1.0, precision=8)
        assert a_q == 255


# ---------------------------------------------------------------------------
# 6. Multinomial quantization — Definition 11, Theorem 3
# ---------------------------------------------------------------------------

class TestMultinomialQuantization:
    """Constrained multinomial quantization (Def 11, Theorem 3)."""

    def test_roundtrip_quaternary_8bit(self):
        """k=4 domain, 8-bit precision.

        beliefs = (0.6, 0.2, 0.1, 0.0), u = 0.1
        base_rates = (0.25, 0.25, 0.25, 0.25)

        Transmit: b̂₁, b̂₂, b̂₃ (3 beliefs) + û
        Derive:   b̂₄ = 255 - b̂₁ - b̂₂ - b̂₃ - û
        """
        beliefs = [0.6, 0.2, 0.1, 0.0]
        u = 0.1
        base_rates = [0.25, 0.25, 0.25, 0.25]

        beliefs_q, u_q, base_rates_q = quantize_multinomial(
            beliefs, u, base_rates, precision=8
        )

        # Theorem 3(a): quantized sum = 255
        assert sum(beliefs_q) + u_q == 255, (
            f"Multinomial constraint violated: "
            f"sum(beliefs_q)={sum(beliefs_q)} + u_q={u_q} = {sum(beliefs_q) + u_q}"
        )

        # All components non-negative (Theorem 3(c))
        for i, b_q in enumerate(beliefs_q):
            assert b_q >= 0, f"beliefs_q[{i}] = {b_q} < 0"
        assert u_q >= 0

        # Dequantize and verify exact sum
        beliefs_r, u_r, base_rates_r = dequantize_multinomial(
            beliefs_q, u_q, base_rates_q, precision=8
        )

        # Theorem 3(b): sum equals 1.0 in real arithmetic. In IEEE 754,
        # k+1 independent divisions may differ by a few ULPs.
        # Integer-domain Theorem 3(a) is tested exactly above.
        assert math.isclose(sum(beliefs_r) + u_r, 1.0, abs_tol=2e-15), (
            f"Reconstructed sum: {sum(beliefs_r)} + {u_r} = {sum(beliefs_r) + u_r}"
        )

    def test_multinomial_clamping(self):
        """Edge case where derived k-th belief would go negative.

        If rounding errors push sum of k-1 quantized beliefs + û > 255,
        the encoder must clamp (reduce largest b̂_i by 1).
        """
        # Construct a case likely to trigger clamping:
        # All beliefs close to 1/k, many of them rounding up
        beliefs = [0.25, 0.25, 0.25, 0.25]
        u = 0.0
        base_rates = [0.25, 0.25, 0.25, 0.25]

        beliefs_q, u_q, base_rates_q = quantize_multinomial(
            beliefs, u, base_rates, precision=8
        )

        # Must hold regardless of clamping
        assert sum(beliefs_q) + u_q == 255
        for b_q in beliefs_q:
            assert b_q >= 0

    def test_multinomial_binary_domain(self):
        """k=2 multinomial should behave like binomial.

        beliefs = [0.7, 0.2], u = 0.1 is equivalent to
        binomial (b=0.7, d=0.2, u=0.1) for the belief/uncertainty split.
        """
        beliefs = [0.7, 0.2]
        u = 0.1
        base_rates = [0.5, 0.5]

        beliefs_q, u_q, base_rates_q = quantize_multinomial(
            beliefs, u, base_rates, precision=8
        )

        assert sum(beliefs_q) + u_q == 255
        assert len(beliefs_q) == 2

    def test_multinomial_base_rate_constraint(self):
        """Base rates must also sum correctly in quantized form."""
        beliefs = [0.5, 0.3, 0.1, 0.0]
        u = 0.1
        base_rates = [0.4, 0.3, 0.2, 0.1]

        beliefs_q, u_q, base_rates_q = quantize_multinomial(
            beliefs, u, base_rates, precision=8
        )

        # Base rates should sum to 255 in quantized domain
        assert sum(base_rates_q) == 255, (
            f"Base rate sum: {sum(base_rates_q)} != 255"
        )


# ---------------------------------------------------------------------------
# 7. Precision modes — 8, 16, 32-bit
# ---------------------------------------------------------------------------

class TestPrecisionModes:
    """Verify all three precision modes produce correct sizes and values."""

    def test_8bit_value_range(self):
        """8-bit: all quantized values in [0, 255]."""
        b_q, d_q, u_q, a_q = quantize_binomial(0.85, 0.05, 0.10, 0.50, precision=8)
        for val in (b_q, d_q, u_q, a_q):
            assert 0 <= val <= 255

    def test_16bit_value_range(self):
        """16-bit: all quantized values in [0, 65535]."""
        b_q, d_q, u_q, a_q = quantize_binomial(0.85, 0.05, 0.10, 0.50, precision=16)
        for val in (b_q, d_q, u_q, a_q):
            assert 0 <= val <= 65535

    def test_32bit_uses_floats(self):
        """32-bit mode: IEEE 754 floats, no quantization.

        At 32-bit, encode_opinion_bytes should store raw float32 values.
        """
        data = encode_opinion_bytes(0, 0, 0, 0, precision=32)
        # 4 float32 values = 16 bytes
        assert len(data) == 16

    def test_8bit_byte_encoding_size(self):
        """8-bit: 4 bytes (1 byte per component)."""
        b_q, d_q, u_q, a_q = quantize_binomial(0.85, 0.05, 0.10, 0.50, precision=8)
        data = encode_opinion_bytes(b_q, d_q, u_q, a_q, precision=8)
        assert len(data) == 4

    def test_16bit_byte_encoding_size(self):
        """16-bit: 8 bytes (2 bytes per component)."""
        b_q, d_q, u_q, a_q = quantize_binomial(0.85, 0.05, 0.10, 0.50, precision=16)
        data = encode_opinion_bytes(b_q, d_q, u_q, a_q, precision=16)
        assert len(data) == 8


# ---------------------------------------------------------------------------
# 8. Byte encoding roundtrip
# ---------------------------------------------------------------------------

class TestByteEncoding:
    """encode_opinion_bytes / decode_opinion_bytes roundtrip."""

    def test_8bit_encode_decode_roundtrip(self):
        """Encode quantized values to bytes and decode back."""
        b_q, d_q, u_q, a_q = 217, 13, 25, 128
        data = encode_opinion_bytes(b_q, d_q, u_q, a_q, precision=8)
        b_r, d_r, u_r, a_r = decode_opinion_bytes(data, precision=8)

        assert (b_r, d_r, u_r, a_r) == (217, 13, 25, 128)

    def test_16bit_encode_decode_roundtrip(self):
        """16-bit roundtrip."""
        b_q, d_q, u_q, a_q = quantize_binomial(0.85, 0.05, 0.10, 0.50, precision=16)
        data = encode_opinion_bytes(b_q, d_q, u_q, a_q, precision=16)
        b_r, d_r, u_r, a_r = decode_opinion_bytes(data, precision=16)

        assert (b_r, d_r, u_r, a_r) == (b_q, d_q, u_q, a_q)

    def test_32bit_encode_decode_roundtrip(self):
        """32-bit mode stores IEEE 754 floats directly.

        encode_opinion_bytes at precision=32 takes the ORIGINAL float
        values (not quantized ints), packs as float32, and roundtrips.
        """
        # For 32-bit, we pass the raw float values (as float-ish ints or
        # the function signature may differ — see implementation note below).
        # The key test: pack 4 float32 values and recover them.
        b, d, u, a = 0.85, 0.05, 0.10, 0.50
        data = encode_opinion_bytes(b, d, u, a, precision=32)
        b_r, d_r, u_r, a_r = decode_opinion_bytes(data, precision=32)

        # float32 has ~7 decimal digits of precision
        assert abs(b_r - b) < 1e-6
        assert abs(d_r - d) < 1e-6
        assert abs(u_r - u) < 1e-6
        assert abs(a_r - a) < 1e-6

    @given(opinion=valid_binomial_opinion())
    @settings(max_examples=200)
    def test_8bit_roundtrip_property(self, opinion):
        """Property: encode → decode is identity for quantized values."""
        b, d, u, a = opinion
        assume(u >= 0)

        b_q, d_q, u_q, a_q = quantize_binomial(b, d, u, a, precision=8)
        data = encode_opinion_bytes(b_q, d_q, u_q, a_q, precision=8)
        b_r, d_r, u_r, a_r = decode_opinion_bytes(data, precision=8)

        assert (b_r, d_r, u_r, a_r) == (b_q, d_q, u_q, a_q)


# ---------------------------------------------------------------------------
# 9. Invalid input handling
# ---------------------------------------------------------------------------

class TestInputValidation:
    """Encoder should reject invalid opinions."""

    def test_negative_belief(self):
        """Negative belief is not a valid SL opinion."""
        with pytest.raises(ValueError):
            quantize_binomial(-0.1, 0.5, 0.6, 0.5, precision=8)

    def test_negative_disbelief(self):
        with pytest.raises(ValueError):
            quantize_binomial(0.5, -0.1, 0.6, 0.5, precision=8)

    def test_sum_not_one(self):
        """b + d + u must equal 1.0 (within floating-point tolerance)."""
        with pytest.raises(ValueError):
            quantize_binomial(0.5, 0.5, 0.5, 0.5, precision=8)

    def test_base_rate_out_of_range(self):
        """Base rate must be in [0, 1]."""
        with pytest.raises(ValueError):
            quantize_binomial(0.5, 0.3, 0.2, 1.5, precision=8)

    def test_invalid_precision(self):
        """Only 8, 16, 32 are valid precision modes."""
        with pytest.raises(ValueError):
            quantize_binomial(0.5, 0.3, 0.2, 0.5, precision=7)

    def test_multinomial_beliefs_dont_sum_with_u(self):
        """sum(beliefs) + u must equal 1.0."""
        with pytest.raises(ValueError):
            quantize_multinomial([0.5, 0.5], 0.5, [0.5, 0.5], precision=8)

    def test_multinomial_base_rates_dont_sum(self):
        """sum(base_rates) must equal 1.0."""
        with pytest.raises(ValueError):
            quantize_multinomial([0.5, 0.3], 0.2, [0.3, 0.3], precision=8)

    def test_multinomial_mismatched_lengths(self):
        """beliefs and base_rates must have the same length."""
        with pytest.raises(ValueError):
            quantize_multinomial([0.5, 0.3], 0.2, [0.5, 0.25, 0.25], precision=8)
