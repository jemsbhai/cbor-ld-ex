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
    encode_multinomial_bytes,
    decode_multinomial_bytes,
    encode_delta_bytes,
    decode_delta_bytes,
    compute_delta,
    apply_delta,
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

    def test_symmetric_clamping_tiebreaker_even_a(self):
        """Symmetric clamping (FORMAL_MODEL.md v0.4.0+ §4.2).

        b=0.5, d=0.5, u=0.0: both have frac=0.5, so tiebreaker fires.
        a=0.5 → â=round(0.5*255)=128, â & 1 == 0 → decrement d̂.
        Result: b̂=128, d̂=127, û=0.
        """
        b_q, d_q, u_q, a_q = quantize_binomial(0.5, 0.5, 0.0, 0.5, precision=8)

        assert b_q == 128, f"b̂ should be 128, got {b_q}"
        assert d_q == 127, f"d̂ should be 127 (â LSB=0 → decrement d̂), got {d_q}"
        assert u_q == 0
        assert b_q + d_q + u_q == 255

    def test_symmetric_clamping_tiebreaker_odd_a(self):
        """Tiebreaker with odd â: should decrement b̂ instead.

        b=0.5, d=0.5, u=0.0, a=0.49 → â=round(0.49*255)=round(124.95)=125.
        â & 1 == 1 → decrement b̂.
        Result: b̂=127, d̂=128, û=0.
        """
        b_q, d_q, u_q, a_q = quantize_binomial(0.5, 0.5, 0.0, 0.49, precision=8)

        assert b_q == 127, f"b̂ should be 127 (â LSB=1 → decrement b̂), got {b_q}"
        assert d_q == 128, f"d̂ should be 128, got {d_q}"
        assert u_q == 0
        assert a_q == 125
        assert b_q + d_q + u_q == 255

    def test_symmetric_clamping_symmetry_property(self):
        """Swapping b and d must swap the clamping target.

        This is the core symmetry guarantee of §4.2: neither component
        has structural priority.
        """
        # With even â: (0.5, 0.5, 0, 0.5) decrements d̂
        b1, d1, u1, a1 = quantize_binomial(0.5, 0.5, 0.0, 0.5, precision=8)
        # With odd â: (0.5, 0.5, 0, 0.49) decrements b̂
        b2, d2, u2, a2 = quantize_binomial(0.5, 0.5, 0.0, 0.49, precision=8)

        # The two results should be mirror images
        assert b1 == d2, f"Symmetry broken: b̂(even)={b1} != d̂(odd)={d2}"
        assert d1 == b2, f"Symmetry broken: d̂(even)={d1} != b̂(odd)={b2}"


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

    def test_multinomial_integer_simplex_projection(self):
        """Integer simplex projection (FORMAL_MODEL.md v0.4.2+ §4.4).

        All k+1 components quantized independently, excess distributed by
        fractional-part rank. Replaces the old iterative decrement loop.
        """
        # All beliefs close to 1/k, many rounding up
        beliefs = [0.25, 0.25, 0.25, 0.25]
        u = 0.0
        base_rates = [0.25, 0.25, 0.25, 0.25]

        beliefs_q, u_q, base_rates_q = quantize_multinomial(
            beliefs, u, base_rates, precision=8
        )

        # Core constraint: sum = 255
        assert sum(beliefs_q) + u_q == 255
        # All non-negative
        for b_q in beliefs_q:
            assert b_q >= 0, f"Negative belief component: {b_q}"
        assert u_q >= 0

    def test_multinomial_projection_max_adjustment_is_one(self):
        """Each component adjusted by at most ±1 from independent rounding.

        Property (i) of §4.4 Theorem 3(c): no component is adjusted twice.
        """
        beliefs = [0.2, 0.2, 0.2, 0.2, 0.1]
        u = 0.1
        base_rates = [0.2, 0.2, 0.2, 0.2, 0.2]

        beliefs_q, u_q, base_rates_q = quantize_multinomial(
            beliefs, u, base_rates, precision=8
        )

        # Check each component differs by at most 1 from independent rounding
        mv = 255
        for i, (bq, b_orig) in enumerate(zip(beliefs_q, beliefs)):
            independent = round(b_orig * mv)
            assert abs(bq - independent) <= 1, (
                f"beliefs_q[{i}] = {bq}, independent round = {independent}, "
                f"diff = {abs(bq - independent)} > 1"
            )
        u_independent = round(u * mv)
        assert abs(u_q - u_independent) <= 1, (
            f"u_q = {u_q}, independent round = {u_independent}, "
            f"diff = {abs(u_q - u_independent)} > 1"
        )

    def test_multinomial_projection_lower_index_tiebreaker(self):
        """When fractional parts are equal, lower index is prioritized.

        Per §4.4 Theorem 3(c): deterministic cross-platform behavior.
        """
        # All components have the same fractional part
        # 0.2 * 255 = 51.0 exactly — no excess, no correction needed
        # Need a case where equal fracs trigger tiebreaker.
        # 0.25 * 255 = 63.75 → rounds to 64 each.
        # 4 beliefs + u=0: 4*64 + 0 = 256 > 255. Excess = 1.
        # All fracs = 0.75. Tiebreaker: lower index decremented first.
        beliefs = [0.25, 0.25, 0.25, 0.25]
        u = 0.0
        base_rates = [0.25, 0.25, 0.25, 0.25]

        beliefs_q, u_q, _ = quantize_multinomial(
            beliefs, u, base_rates, precision=8
        )

        # Excess = 1. All fracs = 0.75, all tied.
        # Lower-index tiebreaker → index 0 gets decremented first.
        # Expected: beliefs_q[0] = 63, beliefs_q[1..3] = 64, u_q = 0
        assert beliefs_q[0] == 63, (
            f"Lower-index tiebreaker failed: beliefs_q[0] = {beliefs_q[0]}, expected 63"
        )
        assert all(beliefs_q[i] == 64 for i in range(1, 4)), (
            f"Other components modified unexpectedly: {beliefs_q[1:]}"
        )
        assert u_q == 0
        assert sum(beliefs_q) + u_q == 255

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

        At 32-bit, encode_opinion_bytes transmits 3 floats (b, d, a).
        û is derived by the decoder from b + d + u = 1.
        3 float32 values = 12 bytes.
        """
        data = encode_opinion_bytes(0.0, 0.0, 0.5, precision=32)
        assert len(data) == 12

    def test_8bit_byte_encoding_size(self):
        """8-bit: 3 bytes (b̂, d̂, â). û is derived, NOT transmitted.

        This is the core bit-packing insight: transmitting û would waste
        8 bits of zero information content because û = 255 - b̂ - d̂.
        """
        b_q, d_q, u_q, a_q = quantize_binomial(0.85, 0.05, 0.10, 0.50, precision=8)
        data = encode_opinion_bytes(b_q, d_q, a_q, precision=8)
        assert len(data) == 3

    def test_16bit_byte_encoding_size(self):
        """16-bit: 6 bytes (2 bytes each for b̂, d̂, â). û is derived."""
        b_q, d_q, u_q, a_q = quantize_binomial(0.85, 0.05, 0.10, 0.50, precision=16)
        data = encode_opinion_bytes(b_q, d_q, a_q, precision=16)
        assert len(data) == 6


# ---------------------------------------------------------------------------
# 8. Byte encoding roundtrip
# ---------------------------------------------------------------------------

class TestByteEncoding:
    """encode_opinion_bytes / decode_opinion_bytes roundtrip.

    Wire format transmits 3 values (b̂, d̂, â). The decoder derives
    û = (2ⁿ−1) − b̂ − d̂ and returns the full 4-tuple (b̂, d̂, û, â).
    """

    def test_8bit_encode_decode_roundtrip(self):
        """Encode 3 values, decode recovers all 4 including derived û."""
        b_q, d_q, u_q, a_q = 217, 13, 25, 128
        # Encode: transmit b̂, d̂, â (NOT û)
        data = encode_opinion_bytes(b_q, d_q, a_q, precision=8)
        assert len(data) == 3
        # Decode: recovers full 4-tuple, deriving û
        b_r, d_r, u_r, a_r = decode_opinion_bytes(data, precision=8)
        assert (b_r, d_r, u_r, a_r) == (217, 13, 25, 128)

    def test_16bit_encode_decode_roundtrip(self):
        """16-bit roundtrip."""
        b_q, d_q, u_q, a_q = quantize_binomial(0.85, 0.05, 0.10, 0.50, precision=16)
        data = encode_opinion_bytes(b_q, d_q, a_q, precision=16)
        assert len(data) == 6
        b_r, d_r, u_r, a_r = decode_opinion_bytes(data, precision=16)
        assert (b_r, d_r, u_r, a_r) == (b_q, d_q, u_q, a_q)

    def test_32bit_encode_decode_roundtrip(self):
        """32-bit mode: transmits 3 IEEE 754 floats (b, d, a).

        Decoder derives u = 1.0 - b - d from the float values.
        """
        b, d, u, a = 0.85, 0.05, 0.10, 0.50
        data = encode_opinion_bytes(b, d, a, precision=32)
        assert len(data) == 12
        b_r, d_r, u_r, a_r = decode_opinion_bytes(data, precision=32)

        assert abs(b_r - b) < 1e-6
        assert abs(d_r - d) < 1e-6
        assert abs(u_r - u) < 1e-6
        assert abs(a_r - a) < 1e-6

    def test_decode_derives_u_correctly(self):
        """Decoder derives û = (2ⁿ−1) − b̂ − d̂ for integer modes.

        This is the information-theoretic justification: û carries zero
        bits of new information, so transmitting it is pure waste.
        The decoder MUST reconstruct it exactly.
        """
        # Vacuous opinion: b=0, d=0, u=255
        data = encode_opinion_bytes(0, 0, 128, precision=8)
        b_r, d_r, u_r, a_r = decode_opinion_bytes(data, precision=8)
        assert u_r == 255  # derived: 255 - 0 - 0

        # Full belief: b=255, d=0, u=0
        data = encode_opinion_bytes(255, 0, 128, precision=8)
        b_r, d_r, u_r, a_r = decode_opinion_bytes(data, precision=8)
        assert u_r == 0  # derived: 255 - 255 - 0

        # Mixed: b=200, d=30, u=25
        data = encode_opinion_bytes(200, 30, 128, precision=8)
        b_r, d_r, u_r, a_r = decode_opinion_bytes(data, precision=8)
        assert u_r == 25  # derived: 255 - 200 - 30

    @given(opinion=valid_binomial_opinion())
    @settings(max_examples=200)
    def test_8bit_roundtrip_property(self, opinion):
        """Property: encode(b̂,d̂,â) → decode → (b̂,d̂,û,â) for any valid opinion."""
        b, d, u, a = opinion
        assume(u >= 0)

        b_q, d_q, u_q, a_q = quantize_binomial(b, d, u, a, precision=8)
        data = encode_opinion_bytes(b_q, d_q, a_q, precision=8)
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


# ---------------------------------------------------------------------------
# 10. Multinomial wire encoding — bit-packed, maximum efficiency
#
# Wire format (bit-packed via BitWriter, MSB-first):
#   [4 bits]  k (domain cardinality, 1-15; 0 reserved)
#   [n × (k-1)]  b̂₁..b̂_{k-1} (independently quantized)
#   [n × 1]      û (independently quantized)
#   [n × (k-1)]  â₁..â_{k-1} (independently quantized)
#   Pad to byte boundary with zero bits.
#
# NOT transmitted (derived by decoder):
#   b̂_k = (2ⁿ−1) − sum(b̂₁..b̂_{k-1}) − û
#   â_k = (2ⁿ−1) − sum(â₁..â_{k-1})
#
# precision_mode is NOT in the payload — it's already in the header.
# This eliminates the 6 wasted bits from the old spec format.
# ---------------------------------------------------------------------------


def _expected_multinomial_wire_bits(k: int, precision: int) -> int:
    """Compute expected wire bits for a multinomial opinion.

    4 bits for k + n*(k-1) for beliefs + n for û + n*(k-1) for base rates.
    Total = 4 + n*(2k-1).
    """
    return 4 + precision * (2 * k - 1)


def _expected_multinomial_wire_bytes(k: int, precision: int) -> int:
    """Compute expected wire bytes (padded to byte boundary)."""
    bits = _expected_multinomial_wire_bits(k, precision)
    return (bits + 7) // 8


class TestMultinomialWireEncoding:
    """Bit-packed multinomial wire encoding/decoding.

    Maximum efficiency: every wire bit carries information.
    No redundant precision_mode byte. No wasted reserved bits.
    b̂_k and â_k are NOT transmitted — derived by decoder.
    """

    # --- Wire size tests ---

    def test_wire_size_k4_8bit(self):
        """k=4, 8-bit: 4 + 8×7 = 60 bits = 8 bytes."""
        beliefs_q, u_q, br_q = quantize_multinomial(
            [0.6, 0.2, 0.1, 0.0], 0.1, [0.25, 0.25, 0.25, 0.25], precision=8
        )
        data = encode_multinomial_bytes(beliefs_q, u_q, br_q, precision=8)
        assert len(data) == _expected_multinomial_wire_bytes(4, 8)  # 8 bytes

    def test_wire_size_k3_8bit(self):
        """k=3, 8-bit: 4 + 8×5 = 44 bits = 6 bytes."""
        beliefs_q, u_q, br_q = quantize_multinomial(
            [0.5, 0.3, 0.1], 0.1, [0.4, 0.3, 0.3], precision=8
        )
        data = encode_multinomial_bytes(beliefs_q, u_q, br_q, precision=8)
        assert len(data) == _expected_multinomial_wire_bytes(3, 8)  # 6 bytes

    def test_wire_size_k2_8bit(self):
        """k=2, 8-bit: 4 + 8×3 = 28 bits = 4 bytes."""
        beliefs_q, u_q, br_q = quantize_multinomial(
            [0.7, 0.2], 0.1, [0.5, 0.5], precision=8
        )
        data = encode_multinomial_bytes(beliefs_q, u_q, br_q, precision=8)
        assert len(data) == _expected_multinomial_wire_bytes(2, 8)  # 4 bytes

    def test_wire_size_k4_16bit(self):
        """k=4, 16-bit: 4 + 16×7 = 116 bits = 15 bytes."""
        beliefs_q, u_q, br_q = quantize_multinomial(
            [0.6, 0.2, 0.1, 0.0], 0.1, [0.25, 0.25, 0.25, 0.25], precision=16
        )
        data = encode_multinomial_bytes(beliefs_q, u_q, br_q, precision=16)
        assert len(data) == _expected_multinomial_wire_bytes(4, 16)  # 15 bytes

    def test_wire_size_k4_32bit(self):
        """k=4, 32-bit: 4 + 32×7 = 228 bits = 29 bytes.

        32-bit uses raw floats (matching binomial convention).
        """
        data = encode_multinomial_bytes(
            [0.6, 0.2, 0.1, 0.0], 0.1, [0.25, 0.25, 0.25, 0.25], precision=32
        )
        assert len(data) == _expected_multinomial_wire_bytes(4, 32)  # 29 bytes

    def test_wire_size_smaller_than_old_spec(self):
        """Bit-packed format must be smaller than old spec (k byte + prec byte + values).

        Old spec: 1 (k) + 1 (prec+6 reserved) + (k-1)*n/8 + n/8 + (k-1)*n/8
                = 2 + n*(2k-1)/8 bytes.
        New: ceil((4 + n*(2k-1)) / 8) bytes.
        """
        for k in [2, 3, 4, 5, 8]:
            for precision in [8, 16]:
                old_spec_bytes = 2 + precision * (2 * k - 1) // 8
                new_bytes = _expected_multinomial_wire_bytes(k, precision)
                assert new_bytes < old_spec_bytes, (
                    f"k={k}, {precision}-bit: new ({new_bytes}B) >= old ({old_spec_bytes}B)"
                )

    # --- Round-trip tests ---

    def test_roundtrip_k4_8bit(self):
        """Full round-trip: quantize → encode → decode → dequantize."""
        beliefs = [0.6, 0.2, 0.1, 0.0]
        u = 0.1
        base_rates = [0.25, 0.25, 0.25, 0.25]

        beliefs_q, u_q, br_q = quantize_multinomial(beliefs, u, base_rates, precision=8)
        data = encode_multinomial_bytes(beliefs_q, u_q, br_q, precision=8)
        beliefs_r, u_r, br_r = decode_multinomial_bytes(data, precision=8)

        assert beliefs_r == beliefs_q
        assert u_r == u_q
        assert br_r == br_q

    def test_roundtrip_k3_16bit(self):
        beliefs = [0.5, 0.3, 0.1]
        u = 0.1
        base_rates = [0.4, 0.3, 0.3]

        beliefs_q, u_q, br_q = quantize_multinomial(beliefs, u, base_rates, precision=16)
        data = encode_multinomial_bytes(beliefs_q, u_q, br_q, precision=16)
        beliefs_r, u_r, br_r = decode_multinomial_bytes(data, precision=16)

        assert beliefs_r == beliefs_q
        assert u_r == u_q
        assert br_r == br_q

    def test_roundtrip_k2_8bit(self):
        """Binary multinomial round-trip."""
        beliefs = [0.7, 0.2]
        u = 0.1
        base_rates = [0.5, 0.5]

        beliefs_q, u_q, br_q = quantize_multinomial(beliefs, u, base_rates, precision=8)
        data = encode_multinomial_bytes(beliefs_q, u_q, br_q, precision=8)
        beliefs_r, u_r, br_r = decode_multinomial_bytes(data, precision=8)

        assert beliefs_r == beliefs_q
        assert u_r == u_q
        assert br_r == br_q

    def test_roundtrip_k4_32bit(self):
        """32-bit float round-trip.

        For 32-bit precision, the convention (matching binomial) is to
        pass raw [0,1] floats directly — NOT through quantize_multinomial.
        quantize_multinomial at 32-bit produces integers in [0, 2^32-1]
        which can't round-trip through float32 exactly.
        """
        beliefs = [0.6, 0.2, 0.1, 0.0]
        u = 0.1
        base_rates = [0.25, 0.25, 0.25, 0.25]

        # Pass raw floats, not quantized integers
        data = encode_multinomial_bytes(beliefs, u, base_rates, precision=32)
        beliefs_r, u_r, br_r = decode_multinomial_bytes(data, precision=32)

        # Float round-trip: transmitted k-1 values exact, derived k-th has IEEE 754 artifacts
        for i in range(len(beliefs) - 1):
            assert abs(beliefs_r[i] - beliefs[i]) < 1e-6, \
                f"beliefs[{i}]: {beliefs_r[i]} != {beliefs[i]}"
        # Derived b_k = 1.0 - sum(transmitted) - u
        assert abs(sum(beliefs_r) + u_r - 1.0) < 1e-5
        assert abs(u_r - u) < 1e-6
        for i in range(len(base_rates) - 1):
            assert abs(br_r[i] - base_rates[i]) < 1e-6

    # --- Derived component correctness ---

    def test_derived_bk_correct(self):
        """b̂_k must be correctly derived after decode: max_val - sum(b̂₁..b̂_{k-1}) - û."""
        beliefs = [0.5, 0.3, 0.1, 0.0]
        u = 0.1
        base_rates = [0.25, 0.25, 0.25, 0.25]

        beliefs_q, u_q, br_q = quantize_multinomial(beliefs, u, base_rates, precision=8)
        data = encode_multinomial_bytes(beliefs_q, u_q, br_q, precision=8)
        beliefs_r, u_r, br_r = decode_multinomial_bytes(data, precision=8)

        # The k-th belief is derived, not transmitted
        expected_bk = 255 - sum(beliefs_r[:-1]) - u_r
        assert beliefs_r[-1] == expected_bk, \
            f"Derived b̂_k = {beliefs_r[-1]} != {expected_bk}"

    def test_derived_ak_correct(self):
        """â_k must be correctly derived: max_val - sum(â₁..â_{k-1})."""
        beliefs = [0.5, 0.3, 0.1, 0.0]
        u = 0.1
        base_rates = [0.4, 0.3, 0.2, 0.1]

        beliefs_q, u_q, br_q = quantize_multinomial(beliefs, u, base_rates, precision=8)
        data = encode_multinomial_bytes(beliefs_q, u_q, br_q, precision=8)
        beliefs_r, u_r, br_r = decode_multinomial_bytes(data, precision=8)

        expected_ak = 255 - sum(br_r[:-1])
        assert br_r[-1] == expected_ak, \
            f"Derived â_k = {br_r[-1]} != {expected_ak}"

    # --- Constraint preservation through wire ---

    def test_constraint_preserved_through_wire_k4(self):
        """sum(beliefs_r) + u_r = 255 after encode → decode."""
        beliefs = [0.4, 0.3, 0.2, 0.0]
        u = 0.1
        base_rates = [0.25, 0.25, 0.25, 0.25]

        beliefs_q, u_q, br_q = quantize_multinomial(beliefs, u, base_rates, precision=8)
        data = encode_multinomial_bytes(beliefs_q, u_q, br_q, precision=8)
        beliefs_r, u_r, br_r = decode_multinomial_bytes(data, precision=8)

        assert sum(beliefs_r) + u_r == 255, \
            f"Constraint violated: {sum(beliefs_r)} + {u_r} = {sum(beliefs_r) + u_r}"
        assert sum(br_r) == 255, \
            f"Base rate constraint violated: {sum(br_r)}"

    # --- Elision verification ---

    def test_bk_and_ak_not_on_wire(self):
        """Wire size proves b̂_k and â_k are not transmitted.

        If they were, the wire would be 4 + n*(2k+1) bits.
        With elision: 4 + n*(2k-1) bits — saving 2n bits (2 values).
        """
        for k in [2, 3, 4, 5]:
            beliefs = [1.0/k] * k
            # Adjust last to ensure exact sum
            beliefs[-1] = 1.0 - sum(beliefs[:-1]) - 0.1
            u = 0.1
            base_rates = [1.0/k] * k
            base_rates[-1] = 1.0 - sum(base_rates[:-1])

            beliefs_q, u_q, br_q = quantize_multinomial(
                beliefs, u, base_rates, precision=8
            )
            data = encode_multinomial_bytes(beliefs_q, u_q, br_q, precision=8)

            # Wire size with elision (our format)
            expected_with_elision = _expected_multinomial_wire_bytes(k, 8)
            # Wire size WITHOUT elision would be ceil((4 + 8*(2k+1))/8)
            expected_without_elision = (4 + 8 * (2 * k + 1) + 7) // 8

            assert len(data) == expected_with_elision, \
                f"k={k}: wire size {len(data)} != expected {expected_with_elision}"
            assert len(data) < expected_without_elision, \
                f"k={k}: elision did not save bytes: {len(data)} >= {expected_without_elision}"

    # --- Shannon efficiency ---

    def test_k_field_efficiency(self):
        """k field: 4 wire bits for 15 valid states.

        Shannon info = log₂(15) ≈ 3.91 bits. Efficiency = 3.91/4 = 97.6%.
        This is near-optimal — a 3-bit field would only support k ≤ 7.
        """
        info_bits = math.log2(15)  # 15 valid k values (1-15)
        wire_bits = 4
        efficiency = info_bits / wire_bits
        assert efficiency > 0.97, f"k field efficiency {efficiency:.3f} < 0.97"

    # --- k boundary values ---

    def test_k_15_max_domain(self):
        """k=15 (maximum supported domain cardinality) round-trips."""
        k = 15
        # Uniform beliefs with small u
        b_val = 0.06
        beliefs = [b_val] * k
        u = 1.0 - sum(beliefs)
        base_rates = [1.0/k] * k
        base_rates[-1] = 1.0 - sum(base_rates[:-1])

        beliefs_q, u_q, br_q = quantize_multinomial(beliefs, u, base_rates, precision=8)
        data = encode_multinomial_bytes(beliefs_q, u_q, br_q, precision=8)
        beliefs_r, u_r, br_r = decode_multinomial_bytes(data, precision=8)

        assert len(data) == _expected_multinomial_wire_bytes(15, 8)
        assert beliefs_r == beliefs_q
        assert u_r == u_q

    def test_k_1_degenerate(self):
        """k=1 (degenerate: single outcome) round-trips."""
        beliefs_q, u_q, br_q = quantize_multinomial(
            [0.8], 0.2, [1.0], precision=8
        )
        data = encode_multinomial_bytes(beliefs_q, u_q, br_q, precision=8)
        beliefs_r, u_r, br_r = decode_multinomial_bytes(data, precision=8)

        assert beliefs_r == beliefs_q
        assert u_r == u_q
        # k=1: 4 + 8*(2*1-1) = 12 bits = 2 bytes
        assert len(data) == 2

    # --- Hypothesis property-based ---

    @given(
        k=st.integers(min_value=2, max_value=10),
        precision=st.sampled_from([8, 16]),
        data=st.data(),
    )
    @settings(max_examples=200)
    def test_roundtrip_property(self, k, precision, data):
        """Property: encode → decode recovers exact quantized values for any valid multinomial."""
        # Generate k random beliefs summing to < 1, with u = remainder
        raw_beliefs = [data.draw(st.floats(min_value=0.01, max_value=0.5)) for _ in range(k)]
        total_b = sum(raw_beliefs)
        assume(total_b < 0.99)  # need room for u > 0
        # Normalize so sum(beliefs) + u = 1
        u = 1.0 - total_b
        assume(u > 0.001)

        # Base rates: uniform
        base_rates = [1.0 / k] * k
        base_rates[-1] = 1.0 - sum(base_rates[:-1])

        beliefs_q, u_q, br_q = quantize_multinomial(raw_beliefs, u, base_rates, precision=precision)
        wire = encode_multinomial_bytes(beliefs_q, u_q, br_q, precision=precision)
        beliefs_r, u_r, br_r = decode_multinomial_bytes(wire, precision=precision)

        assert beliefs_r == beliefs_q, f"beliefs mismatch: {beliefs_r} != {beliefs_q}"
        assert u_r == u_q, f"u mismatch: {u_r} != {u_q}"
        assert br_r == br_q, f"base_rates mismatch: {br_r} != {br_q}"

        # Constraint preserved through wire
        assert sum(beliefs_r) + u_r == (1 << precision) - 1
        assert sum(br_r) == (1 << precision) - 1

    @given(
        k=st.integers(min_value=2, max_value=10),
        precision=st.sampled_from([8, 16]),
        data=st.data(),
    )
    @settings(max_examples=200)
    def test_wire_size_property(self, k, precision, data):
        """Property: wire size is exactly ceil((4 + n*(2k-1)) / 8) bytes."""
        raw_beliefs = [data.draw(st.floats(min_value=0.01, max_value=0.5)) for _ in range(k)]
        total_b = sum(raw_beliefs)
        assume(total_b < 0.99)
        u = 1.0 - total_b
        assume(u > 0.001)

        base_rates = [1.0 / k] * k
        base_rates[-1] = 1.0 - sum(base_rates[:-1])

        beliefs_q, u_q, br_q = quantize_multinomial(raw_beliefs, u, base_rates, precision=precision)
        wire = encode_multinomial_bytes(beliefs_q, u_q, br_q, precision=precision)

        expected = _expected_multinomial_wire_bytes(k, precision)
        assert len(wire) == expected, \
            f"k={k}, {precision}-bit: wire {len(wire)}B != expected {expected}B"


# ---------------------------------------------------------------------------
# 11. Delta mode — precision_mode=11, 2-byte signed payload (§7.6)
# ---------------------------------------------------------------------------

class TestDeltaWireFormat:
    """Delta encoding: 2 bytes (Δb̂, Δd̂) as signed int8. Zero overhead."""

    def test_encode_delta_size_exactly_2_bytes(self):
        """Delta payload is exactly 2 bytes — no framing, no padding."""
        data = encode_delta_bytes(10, -5)
        assert len(data) == 2

    def test_encode_delta_zero(self):
        """Zero deltas: both bytes are 0x00."""
        data = encode_delta_bytes(0, 0)
        assert data == bytes([0x00, 0x00])

    def test_encode_delta_positive(self):
        """Positive deltas: +10, +20."""
        data = encode_delta_bytes(10, 20)
        assert data == bytes([10, 20])

    def test_encode_delta_negative(self):
        """Negative deltas encoded as signed int8 (two's complement).

        -1 = 0xFF, -128 = 0x80.
        """
        data = encode_delta_bytes(-1, -128)
        assert data == bytes([0xFF, 0x80])

    def test_encode_delta_max_range(self):
        """Maximum range: +127, -128."""
        data = encode_delta_bytes(127, -128)
        assert data == bytes([127, 0x80])

    def test_decode_delta_roundtrip(self):
        """Encode → decode recovers exact signed values."""
        for delta_b, delta_d in [(0, 0), (10, -5), (127, -128), (-1, 1), (-50, 50)]:
            data = encode_delta_bytes(delta_b, delta_d)
            rb, rd = decode_delta_bytes(data)
            assert rb == delta_b, f"delta_b: {rb} != {delta_b}"
            assert rd == delta_d, f"delta_d: {rd} != {delta_d}"

    def test_encode_delta_rejects_overflow(self):
        """Deltas outside [-128, 127] must be rejected (§7.6 fallback)."""
        with pytest.raises(ValueError):
            encode_delta_bytes(128, 0)
        with pytest.raises(ValueError):
            encode_delta_bytes(0, -129)

    @given(
        delta_b=st.integers(min_value=-128, max_value=127),
        delta_d=st.integers(min_value=-128, max_value=127),
    )
    @settings(max_examples=500)
    def test_roundtrip_property(self, delta_b, delta_d):
        """Property: any valid (delta_b, delta_d) round-trips exactly."""
        data = encode_delta_bytes(delta_b, delta_d)
        assert len(data) == 2
        rb, rd = decode_delta_bytes(data)
        assert rb == delta_b
        assert rd == delta_d


class TestComputeAndApplyDelta:
    """compute_delta + apply_delta: stateful delta encoding helpers."""

    def test_compute_delta_no_change(self):
        """Identical opinions produce zero deltas."""
        prev = (200, 30, 25, 128)
        curr = (200, 30, 25, 128)
        delta_b, delta_d = compute_delta(prev, curr)
        assert delta_b == 0
        assert delta_d == 0

    def test_compute_delta_small_change(self):
        """Small changes produce small deltas."""
        prev = (200, 30, 25, 128)
        curr = (205, 28, 22, 128)  # b̂+5, d̂-2
        delta_b, delta_d = compute_delta(prev, curr)
        assert delta_b == 5
        assert delta_d == -2

    def test_compute_delta_overflow_raises(self):
        """Deltas exceeding signed int8 range must raise (§7.6 fallback)."""
        prev = (220, 10, 25, 128)
        curr = (50, 180, 25, 128)  # Δb̂ = -170, overflows int8
        with pytest.raises(ValueError, match="[Oo]verflow|[Rr]ange|[Ff]allback"):
            compute_delta(prev, curr)

    def test_apply_delta_reconstructs(self):
        """apply_delta recovers the current opinion from prev + deltas."""
        prev = (200, 30, 25, 128)
        curr = (205, 28, 22, 128)
        delta_b, delta_d = compute_delta(prev, curr)
        reconstructed = apply_delta(prev, delta_b, delta_d)
        # b̂_new, d̂_new, û_new, â_prev
        assert reconstructed == (205, 28, 22, 128)

    def test_apply_delta_derives_u(self):
        """û is derived: 255 - b̂_new - d̂_new. Never transmitted."""
        prev = (100, 100, 55, 128)
        reconstructed = apply_delta(prev, 10, -10)
        b, d, u, a = reconstructed
        assert b == 110
        assert d == 90
        assert u == 255 - 110 - 90  # = 55
        assert a == 128  # unchanged

    def test_apply_delta_preserves_base_rate(self):
        """Base rate â comes from previous opinion, NOT from wire."""
        prev = (200, 30, 25, 77)
        reconstructed = apply_delta(prev, -10, 5)
        assert reconstructed[3] == 77

    def test_apply_delta_rejects_invalid_result(self):
        """Receiver MUST reject deltas producing invalid opinions (§7.6)."""
        prev = (10, 10, 235, 128)
        # Δb̂ = -20 would make b̂_new = -10 < 0
        with pytest.raises(ValueError, match="[Ii]nvalid|[Cc]onstraint|[Nn]egative"):
            apply_delta(prev, -20, 0)

    def test_compute_apply_roundtrip(self):
        """compute_delta → encode → decode → apply_delta recovers opinion."""
        prev = (180, 40, 35, 100)
        curr = (175, 45, 35, 100)
        delta_b, delta_d = compute_delta(prev, curr)
        wire = encode_delta_bytes(delta_b, delta_d)
        rb, rd = decode_delta_bytes(wire)
        result = apply_delta(prev, rb, rd)
        assert result == curr

    @given(
        b_prev=st.integers(min_value=0, max_value=255),
        d_prev=st.integers(min_value=0, max_value=255),
        a_q=st.integers(min_value=0, max_value=255),
        delta_b=st.integers(min_value=-128, max_value=127),
        delta_d=st.integers(min_value=-128, max_value=127),
    )
    @settings(max_examples=1000)
    def test_apply_delta_axiom3_property(self, b_prev, d_prev, a_q, delta_b, delta_d):
        """Property: if apply_delta succeeds, b̂+d̂+û = 255."""
        assume(b_prev + d_prev <= 255)
        u_prev = 255 - b_prev - d_prev
        prev = (b_prev, d_prev, u_prev, a_q)

        b_new = b_prev + delta_b
        d_new = d_prev + delta_d
        # Skip invalid results
        assume(b_new >= 0 and d_new >= 0 and b_new + d_new <= 255)

        result = apply_delta(prev, delta_b, delta_d)
        assert result[0] + result[1] + result[2] == 255
