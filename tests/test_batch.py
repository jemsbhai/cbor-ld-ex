"""
Phase 1 tests: xoshiro128++ PRNG for CBOR-LD-ex batch compression.

Test vectors generated from the canonical Blackman & Vigna reference
C implementation (https://prng.di.unimi.it/xoshiro128plusplus.c),
compiled and executed to produce bit-exact expected outputs.

Seeding uses SplitMix64 as recommended by the authors:
  uint32 seed → zero-extend to uint64 → SplitMix64 → 2×uint64 →
  split each into 2×uint32 (little-endian) → fill 4-word state.

The PRNG is protocol-mandated: any conformant CBOR-LD-ex implementation
MUST produce identical output from the same seed. These tests verify
bit-for-bit correctness against the reference.

All tests target: src/cbor_ld_ex/batch.py
"""

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from cbor_ld_ex.batch import (
    Xoshiro128PlusPlus,
    splitmix64,
    fwht,
    fwht_inverse,
    simplex_project,
    lloyd_max_codebook,
    quantize_lloyd_max,
    dequantize_lloyd_max,
    rht_forward,
    rht_inverse,
    encode_batch,
    decode_batch,
    batch_wire_bits,
    batch_information_bits,
    batch_overhead_bits,
    batch_padding_waste_bits,
    batch_efficiency,
)


# =========================================================================
# SplitMix64 seeder — canonical test vectors
# =========================================================================

class TestSplitMix64:
    """SplitMix64 seeder verified against canonical C implementation.

    Reference: https://prng.di.unimi.it/splitmix64.c
    Constants: additive = 0x9E3779B97F4A7C15 (golden ratio × 2^64)
               mix1 = 0xBF58476D1CE4E5B9
               mix2 = 0x94D049BB133111EB
    """

    def test_seed_0_first_four(self):
        """SplitMix64(0): first 4 outputs match reference C implementation."""
        expected = [
            0xE220A8397B1DCDAF,
            0x6E789E6AA1B965F4,
            0x06C45D188009454F,
            0xF88BB8A8724C81EC,
        ]
        state = 0
        for exp in expected:
            state, result = splitmix64(state)
            assert result == exp, f"Expected 0x{exp:016X}, got 0x{result:016X}"

    def test_seed_42_first_four(self):
        """SplitMix64(42): first 4 outputs match reference C implementation."""
        expected = [
            0xBDD732262FEB6E95,
            0x28EFE333B266F103,
            0x47526757130F9F52,
            0x581CE1FF0E4AE394,
        ]
        state = 42
        for exp in expected:
            state, result = splitmix64(state)
            assert result == exp, f"Expected 0x{exp:016X}, got 0x{result:016X}"

    def test_output_is_uint64(self):
        """All outputs must be in [0, 2^64 - 1]."""
        state = 12345
        for _ in range(100):
            state, result = splitmix64(state)
            assert 0 <= result < (1 << 64)


# =========================================================================
# xoshiro128++ — state initialization from uint32 seed
# =========================================================================

class TestXoshiro128PPSeeding:
    """State initialization: uint32 seed → SplitMix64 → 4×uint32 state.

    Verified against the canonical C reference output.
    """

    def test_seed_0_state(self):
        """seed=0: state matches reference C output."""
        rng = Xoshiro128PlusPlus(0)
        assert rng.state == (0x7B1DCDAF, 0xE220A839, 0xA1B965F4, 0x6E789E6A)

    def test_seed_1_state(self):
        """seed=1: state matches reference."""
        rng = Xoshiro128PlusPlus(1)
        assert rng.state == (0x89025CC1, 0x910A2DEC, 0x658EEC67, 0xBEEB8DA1)

    def test_seed_42_state(self):
        """seed=42: state matches reference."""
        rng = Xoshiro128PlusPlus(42)
        assert rng.state == (0x2FEB6E95, 0xBDD73226, 0xB266F103, 0x28EFE333)

    def test_seed_deadbeef_state(self):
        """seed=0xDEADBEEF: state matches reference."""
        rng = Xoshiro128PlusPlus(0xDEADBEEF)
        assert rng.state == (0x68C9EB9B, 0x4ADFB90F, 0x41A10922, 0xDE586A31)

    def test_seed_max_u32_state(self):
        """seed=0xFFFFFFFF: state matches reference."""
        rng = Xoshiro128PlusPlus(0xFFFFFFFF)
        assert rng.state == (0xAFF181C0, 0x73B13BA2, 0x1340D3B4, 0x61204305)

    def test_explicit_state(self):
        """Direct state initialization bypasses SplitMix64."""
        rng = Xoshiro128PlusPlus.from_state(1, 2, 3, 4)
        assert rng.state == (1, 2, 3, 4)

    def test_all_zero_state_rejected(self):
        """All-zero state is invalid per the spec (period degenerates)."""
        with pytest.raises(ValueError, match="[Zz]ero"):
            Xoshiro128PlusPlus.from_state(0, 0, 0, 0)


# =========================================================================
# xoshiro128++ — output sequence verification
# =========================================================================

class TestXoshiro128PPOutputs:
    """Output sequences verified against canonical C reference.

    Each test uses the EXACT expected values from the reference C program.
    A single bit flip in any output means our implementation is wrong.
    """

    def test_seed_0_first_20(self):
        """seed=0: first 20 outputs match reference."""
        expected = [
            0x4653DAA3, 0x73922B58, 0xB82B4ADD, 0xD9FABD3B,
            0x3C8698C3, 0x1C9B58FF, 0xCCAC4646, 0x27E3FD16,
            0xED908642, 0xA326713B, 0x2B17C194, 0x76F76697,
            0x77337225, 0x0C31CFCF, 0x3475930C, 0xDF6B9966,
            0x494A92B0, 0x9AC1B0B2, 0x047AE083, 0x07F65401,
        ]
        rng = Xoshiro128PlusPlus(0)
        for i, exp in enumerate(expected):
            result = rng.next()
            assert result == exp, \
                f"Output {i}: expected 0x{exp:08X}, got 0x{result:08X}"

    def test_seed_1_first_20(self):
        """seed=1: first 20 outputs match reference."""
        expected = [
            0x7FF78DE4, 0x9A170265, 0xDAC127B8, 0x9859E914,
            0x4D4B41B3, 0xBA2AFB67, 0xEDC318AD, 0x8AABECA1,
            0xA7B6CF05, 0x3DDFE677, 0x5E95E82A, 0x6BE78294,
            0x8FDDF90A, 0xDA7C88E5, 0xA4EC2A82, 0x37387C83,
            0x11426A62, 0x87FFDEA1, 0xE1E3EE1A, 0xD392FBB4,
        ]
        rng = Xoshiro128PlusPlus(1)
        for i, exp in enumerate(expected):
            result = rng.next()
            assert result == exp, \
                f"Output {i}: expected 0x{exp:08X}, got 0x{result:08X}"

    def test_seed_42_first_20(self):
        """seed=42: first 20 outputs match reference."""
        expected = [
            0x9D9452C1, 0x6909D440, 0x6148A68F, 0x54829A5B,
            0xC648DE34, 0xEDC89AA6, 0xFF162EB3, 0xAB6356AA,
            0xA080A165, 0xDA937A4B, 0x94E03472, 0x241DD195,
            0x3BFE1456, 0x7A3A38CB, 0x556C48E7, 0x892727DE,
            0x9ECCB0D4, 0x1E52435E, 0x556647BE, 0x7567C727,
        ]
        rng = Xoshiro128PlusPlus(42)
        for i, exp in enumerate(expected):
            result = rng.next()
            assert result == exp, \
                f"Output {i}: expected 0x{exp:08X}, got 0x{result:08X}"

    def test_seed_deadbeef_first_20(self):
        """seed=0xDEADBEEF: first 20 outputs match reference."""
        expected = [
            0xF9F4D1BE, 0x7064DD42, 0x0DF5E7C2, 0x10D7074F,
            0x92177A10, 0x5FB06F48, 0xB440AAD5, 0x2882C90B,
            0x3519B8A9, 0x77E465CB, 0x5C3199F6, 0xF99B2B3A,
            0x9D70E8AC, 0x6C8F2E88, 0x28789489, 0xE31B9DDA,
            0x45667240, 0xB70C5C39, 0xE652F9A2, 0x6E51D92F,
        ]
        rng = Xoshiro128PlusPlus(0xDEADBEEF)
        for i, exp in enumerate(expected):
            result = rng.next()
            assert result == exp, \
                f"Output {i}: expected 0x{exp:08X}, got 0x{result:08X}"

    def test_explicit_state_1_0_0_0(self):
        """Explicit state [1,0,0,0]: tests raw algorithm without seeder."""
        expected = [
            0x00000081, 0x00000081, 0x00040000, 0x20080881,
            0x48450300, 0x10180242, 0xEA5B1391, 0xE0D486E1,
            0x16803819, 0xCB547FA3, 0xAF3FB022, 0x386AF69C,
            0x06838D3C, 0x1709ECDD, 0xD47DC5F2, 0x6E6C4506,
            0x45385EB5, 0x433591FA, 0x11D9A2AD, 0x1248E0E2,
        ]
        rng = Xoshiro128PlusPlus.from_state(1, 0, 0, 0)
        for i, exp in enumerate(expected):
            result = rng.next()
            assert result == exp, \
                f"Output {i}: expected 0x{exp:08X}, got 0x{result:08X}"

    def test_explicit_state_1_2_3_4(self):
        """Explicit state [1,2,3,4]: tests raw algorithm."""
        expected = [
            0x00000281, 0x00180387, 0xC0183387, 0xD1AE3B02,
            0x31E2310A, 0xFD275AB0, 0xE67F7CEC, 0x50D07F0F,
            0x1D896E9B, 0x2506D3C4, 0xC00995C8, 0xDE9A7E9B,
            0xFC2FE274, 0xBCB2421F, 0x576F2751, 0x2472D5BA,
            0xBB2A2B80, 0x6B5D4D62, 0x5EC5D3C5, 0xE6742D30,
        ]
        rng = Xoshiro128PlusPlus.from_state(1, 2, 3, 4)
        for i, exp in enumerate(expected):
            result = rng.next()
            assert result == exp, \
                f"Output {i}: expected 0x{exp:08X}, got 0x{result:08X}"

    def test_long_sequence_skip_995(self):
        """seed=12345: outputs 996–1000 match after skipping 995.

        Verifies long-sequence consistency — accumulating errors
        in the state update would cause divergence.
        """
        expected = [0xCD086CDD, 0x078D2421, 0x68038CAD, 0x618259EF, 0xA4FDBF4A]
        rng = Xoshiro128PlusPlus(12345)
        for _ in range(995):
            rng.next()
        for i, exp in enumerate(expected):
            result = rng.next()
            assert result == exp, \
                f"Output {996+i}: expected 0x{exp:08X}, got 0x{result:08X}"


# =========================================================================
# xoshiro128++ — output properties
# =========================================================================

class TestXoshiro128PPProperties:
    """Statistical and structural properties of the PRNG output."""

    def test_output_is_uint32(self):
        """All outputs must be in [0, 2^32 - 1]."""
        rng = Xoshiro128PlusPlus(42)
        for _ in range(1000):
            result = rng.next()
            assert 0 <= result < (1 << 32)

    def test_deterministic_same_seed(self):
        """Same seed produces identical sequence."""
        rng1 = Xoshiro128PlusPlus(42)
        rng2 = Xoshiro128PlusPlus(42)
        for _ in range(100):
            assert rng1.next() == rng2.next()

    def test_different_seeds_differ(self):
        """Different seeds produce different sequences (with overwhelming probability)."""
        rng1 = Xoshiro128PlusPlus(0)
        rng2 = Xoshiro128PlusPlus(1)
        outputs1 = [rng1.next() for _ in range(20)]
        outputs2 = [rng2.next() for _ in range(20)]
        assert outputs1 != outputs2

    @given(seed=st.integers(min_value=0, max_value=0xFFFFFFFF))
    @settings(max_examples=50)
    def test_no_immediate_cycle(self, seed):
        """No sequence cycles within the first 100 outputs.

        Period is 2^128 − 1, so cycling in 100 steps would be catastrophic.
        """
        rng = Xoshiro128PlusPlus(seed)
        outputs = [rng.next() for _ in range(100)]
        # Not all the same value
        assert len(set(outputs)) > 1

    def test_next_bits_extraction(self):
        """next_bits(n) extracts the top n bits of the next output.

        This is the interface the RHT will use to generate sign vectors
        and permutations.
        """
        rng1 = Xoshiro128PlusPlus(42)
        rng2 = Xoshiro128PlusPlus(42)

        # next_bits(1) = top 1 bit of next()
        full_output = rng1.next()
        top_bit = full_output >> 31
        one_bit = rng2.next_bits(1)
        assert one_bit == top_bit

    def test_next_bits_range(self):
        """next_bits(n) output is in [0, 2^n - 1]."""
        rng = Xoshiro128PlusPlus(42)
        for n in [1, 2, 4, 8, 16, 32]:
            for _ in range(20):
                val = rng.next_bits(n)
                assert 0 <= val < (1 << n), f"n={n}: got {val}"


# =========================================================================
# Fast Walsh-Hadamard Transform (FWHT) — §4.8.3
# =========================================================================

import math


class TestFWHTKnownVectors:
    """Known small-case transforms verified by hand.

    The normalized Walsh-Hadamard matrix of order D:
      H_D[i,j] = (1/√D) × (-1)^<i,j>
    where <i,j> is the bitwise dot product.

    H_2 = (1/√2) [[1, 1], [1, -1]]
    H_4 = (1/2) [[1,1,1,1],[1,-1,1,-1],[1,1,-1,-1],[1,-1,-1,1]]
    """

    def test_d2_identity_vector(self):
        """H_2 · [1, 0] = [1/√2, 1/√2]."""
        result = fwht([1.0, 0.0])
        s = 1.0 / math.sqrt(2)
        assert len(result) == 2
        assert abs(result[0] - s) < 1e-12
        assert abs(result[1] - s) < 1e-12

    def test_d2_unit_vector(self):
        """H_2 · [1, 1] = [√2, 0]."""
        result = fwht([1.0, 1.0])
        s2 = math.sqrt(2)
        assert abs(result[0] - s2) < 1e-12
        assert abs(result[1] - 0.0) < 1e-12

    def test_d4_known(self):
        """H_4 · [1, 0, 0, 0] = [0.5, 0.5, 0.5, 0.5]."""
        result = fwht([1.0, 0.0, 0.0, 0.0])
        for val in result:
            assert abs(val - 0.5) < 1e-12

    def test_d4_alternating(self):
        """H_4 · [1, -1, 1, -1] = [0, 0, 0, 2].

        The pattern [1,-1,1,-1] is the 3rd Walsh function (bit reversal
        of index 3 = 0b11), so the transform concentrates at index 3.
        Wait — let me verify: H_4[i,j] = (1/2)(-1)^<i,j>.
        Column 0: <i,0>=0 for all i, so H_4[:,0] = [1,1,1,1]/2.
        Input [1,-1,1,-1]: sum = 1*1 + (-1)*(-1) + 1*1 + (-1)*(-1) for row with
        matching sign pattern.

        Actually, the normalized result of H_4 · [1,-1,1,-1]:
        Row 0: (1+(-1)+1+(-1))/2 = 0
        Row 1: (1-(-1)+1-(-1))/2 = (1+1+1+1)/2 = 2
        Row 2: (1+(-1)-1-(-1))/2 = (1-1-1+1)/2 = 0
        Row 3: (1-(-1)-1+(-1))/2 = (1+1-1-1)/2 = 0
        Result: [0, 2, 0, 0]
        """
        result = fwht([1.0, -1.0, 1.0, -1.0])
        assert abs(result[0] - 0.0) < 1e-12
        assert abs(result[1] - 2.0) < 1e-12
        assert abs(result[2] - 0.0) < 1e-12
        assert abs(result[3] - 0.0) < 1e-12


class TestFWHTSelfInverse:
    """Normalized FWHT is self-inverse: H · H · x = x.

    This is THE critical property for batch encode/decode roundtrip.
    If this fails, the inverse RHT won't recover the original vector.
    """

    @pytest.mark.parametrize("d", [2, 4, 8, 16, 32, 64, 128, 256])
    def test_roundtrip_unit_vector(self, d):
        """fwht(fwht([1,0,...,0])) = [1,0,...,0] for all power-of-2 sizes."""
        x = [0.0] * d
        x[0] = 1.0
        y = fwht(x)
        z = fwht(y)
        for i in range(d):
            assert abs(z[i] - x[i]) < 1e-10, \
                f"d={d}, i={i}: expected {x[i]}, got {z[i]}"

    @pytest.mark.parametrize("d", [2, 4, 8, 16, 32, 64, 128, 256])
    def test_roundtrip_random_vector(self, d):
        """fwht(fwht(x)) = x for random vectors at all sizes."""
        import random
        random.seed(42 + d)
        x = [random.gauss(0, 1) for _ in range(d)]
        y = fwht(x)
        z = fwht(y)
        for i in range(d):
            assert abs(z[i] - x[i]) < 1e-9, \
                f"d={d}, i={i}: expected {x[i]:.10f}, got {z[i]:.10f}"

    def test_inverse_function_matches(self):
        """fwht_inverse(fwht(x)) = x using the explicit inverse function."""
        import random
        random.seed(99)
        x = [random.gauss(0, 1) for _ in range(64)]
        y = fwht(x)
        z = fwht_inverse(y)
        for i in range(64):
            assert abs(z[i] - x[i]) < 1e-9


class TestFWHTOrthogonality:
    """Normalized FWHT preserves L2 norm: ‖H·x‖₂ = ‖x‖₂.

    This is Parseval's theorem for the Walsh-Hadamard transform.
    Critical for distortion-rate analysis: the MSE in the rotated
    domain equals the MSE in the original domain.
    """

    @pytest.mark.parametrize("d", [2, 4, 8, 16, 64, 256])
    def test_norm_preservation(self, d):
        """L2 norm is preserved after transform."""
        import random
        random.seed(42 + d)
        x = [random.gauss(0, 1) for _ in range(d)]
        y = fwht(x)
        norm_x = math.sqrt(sum(v**2 for v in x))
        norm_y = math.sqrt(sum(v**2 for v in y))
        assert abs(norm_x - norm_y) < 1e-9 * norm_x, \
            f"d={d}: norm_x={norm_x:.10f}, norm_y={norm_y:.10f}"

    def test_zero_vector(self):
        """H · 0 = 0."""
        result = fwht([0.0] * 16)
        for val in result:
            assert abs(val) < 1e-15


class TestFWHTEdgeCases:
    """Edge cases and input validation."""

    def test_d1_passthrough(self):
        """D=1: H_1 = [1], so transform is identity."""
        result = fwht([3.14])
        assert abs(result[0] - 3.14) < 1e-12

    def test_non_power_of_2_rejected(self):
        """Non-power-of-2 lengths must be rejected."""
        with pytest.raises(ValueError, match="power of 2"):
            fwht([1.0, 2.0, 3.0])

    def test_empty_rejected(self):
        """Empty input must be rejected."""
        with pytest.raises(ValueError):
            fwht([])

    @pytest.mark.parametrize("d", [2, 4, 8, 16, 32, 64, 128, 256])
    def test_output_length_equals_input(self, d):
        """Output has same length as input."""
        x = [1.0] * d
        y = fwht(x)
        assert len(y) == d

    @given(
        d_exp=st.integers(min_value=0, max_value=8),
    )
    @settings(max_examples=9)
    def test_linearity(self, d_exp):
        """H · (ax + by) = a(H·x) + b(H·y) — linearity."""
        import random
        d = 1 << d_exp
        random.seed(42 + d)
        x = [random.gauss(0, 1) for _ in range(d)]
        y = [random.gauss(0, 1) for _ in range(d)]
        a, b = 2.5, -1.3
        combined = [a * xi + b * yi for xi, yi in zip(x, y)]

        hx = fwht(x)
        hy = fwht(y)
        h_combined = fwht(combined)
        expected = [a * hxi + b * hyi for hxi, hyi in zip(hx, hy)]

        for i in range(d):
            assert abs(h_combined[i] - expected[i]) < 1e-9 * (abs(expected[i]) + 1e-15), \
                f"d={d}, i={i}: linearity violated"


# =========================================================================
# L2 Simplex Projection — §4.8.5 (Duchi et al. 2008)
# =========================================================================


class TestSimplexProjectKnownVectors:
    """Hand-verified projections onto the probability k-simplex.

    The L2 simplex projection finds the closest point on the simplex
    {x : x_i ≥ 0, ∑x_i = 1} to a given input vector.

    Algorithm: sort descending, find threshold via cumulative sums,
    shift and clamp. O(k log k) from the sort.
    """

    def test_already_on_simplex(self):
        """Point already on simplex is unchanged."""
        x = [0.3, 0.5, 0.2]
        result = simplex_project(x)
        for i in range(3):
            assert abs(result[i] - x[i]) < 1e-12

    def test_uniform_on_simplex(self):
        """Uniform distribution [1/3, 1/3, 1/3] is unchanged."""
        x = [1/3, 1/3, 1/3]
        result = simplex_project(x)
        for i in range(3):
            assert abs(result[i] - 1/3) < 1e-12

    def test_vertex_on_simplex(self):
        """Vertex [1, 0, 0] is unchanged."""
        x = [1.0, 0.0, 0.0]
        result = simplex_project(x)
        assert abs(result[0] - 1.0) < 1e-12
        assert abs(result[1] - 0.0) < 1e-12
        assert abs(result[2] - 0.0) < 1e-12

    def test_needs_scaling_only(self):
        """[0.4, 0.4, 0.4] sums to 1.2, needs uniform shift down.

        Threshold = (1.2 - 1.0) / 3 = 0.0667
        Projected: [0.333, 0.333, 0.333]
        """
        result = simplex_project([0.4, 0.4, 0.4])
        for val in result:
            assert abs(val - 1/3) < 1e-10

    def test_needs_clamping(self):
        """[0.5, 0.5, -0.5] has a negative component.

        After projection: the negative component gets clamped to 0,
        and the remaining budget is distributed.
        Sorted descending: [0.5, 0.5, -0.5]
        Cumsum: 0.5, 1.0, 0.5
        k=1: (0.5-1)/1 = -0.5, μ[1]=0.5 > -0.5 → continue
        k=2: (1.0-1)/2 = 0.0, μ[2]=-0.5 ≤ 0.0 → threshold = 0.0
        Projected: max(0, 0.5-0) = 0.5, max(0, 0.5-0) = 0.5, max(0, -0.5-0) = 0.0
        """
        result = simplex_project([0.5, 0.5, -0.5])
        assert abs(result[0] - 0.5) < 1e-12
        assert abs(result[1] - 0.5) < 1e-12
        assert abs(result[2] - 0.0) < 1e-12

    def test_all_negative(self):
        """[-1, -2, -3]: all negative, projected to vertex nearest to input.

        The closest simplex point to a vector where all components are
        negative is the vertex with mass on the least-negative component.
        Result: [1, 0, 0]
        """
        result = simplex_project([-1.0, -2.0, -3.0])
        assert abs(result[0] - 1.0) < 1e-12
        assert abs(result[1] - 0.0) < 1e-12
        assert abs(result[2] - 0.0) < 1e-12

    def test_all_equal_large(self):
        """[10, 10, 10]: uniform, far from simplex.

        Threshold = (30 - 1) / 3 = 29/3 ≈ 9.667
        Projected: [10 - 29/3] = [1/3, 1/3, 1/3]
        """
        result = simplex_project([10.0, 10.0, 10.0])
        for val in result:
            assert abs(val - 1/3) < 1e-10

    def test_2d_simplex(self):
        """2D case: [0.8, 0.8] → [0.5, 0.5]."""
        result = simplex_project([0.8, 0.8])
        assert abs(result[0] - 0.5) < 1e-12
        assert abs(result[1] - 0.5) < 1e-12

    def test_1d_simplex(self):
        """1D case: any scalar projects to [1.0]."""
        assert abs(simplex_project([5.0])[0] - 1.0) < 1e-12
        assert abs(simplex_project([-3.0])[0] - 1.0) < 1e-12
        assert abs(simplex_project([1.0])[0] - 1.0) < 1e-12

    def test_opinion_triple_restoration(self):
        """Realistic case: noisy opinion triple after inverse RHT.

        (b̃, d̃, ũ) = (0.36, 0.27, 0.42) sums to 1.05.
        After projection must sum to exactly 1.0 with all ≥ 0.
        """
        result = simplex_project([0.36, 0.27, 0.42])
        assert abs(sum(result) - 1.0) < 1e-12
        assert all(v >= -1e-15 for v in result)


class TestSimplexProjectProperties:
    """Mathematical properties of L2 simplex projection."""

    def test_output_on_simplex(self):
        """Output always satisfies simplex constraints."""
        import random
        random.seed(42)
        for _ in range(200):
            k = random.randint(2, 10)
            x = [random.gauss(0, 2) for _ in range(k)]
            result = simplex_project(x)
            assert abs(sum(result) - 1.0) < 1e-10, \
                f"Sum = {sum(result)}, expected 1.0"
            for i, v in enumerate(result):
                assert v >= -1e-12, \
                    f"Component {i} = {v} < 0"

    def test_idempotent(self):
        """project(project(x)) = project(x) — projection is idempotent."""
        import random
        random.seed(42)
        for _ in range(100):
            x = [random.gauss(0, 2) for _ in range(5)]
            p1 = simplex_project(x)
            p2 = simplex_project(p1)
            for i in range(5):
                assert abs(p1[i] - p2[i]) < 1e-12

    def test_nearest_point(self):
        """Projection is the L2-nearest simplex point.

        For any other simplex point z, ‖proj - x‖ ≤ ‖z - x‖.
        We test against 100 random simplex points.
        """
        import random
        random.seed(42)
        x = [0.5, -0.3, 1.2, 0.1, -0.1]
        proj = simplex_project(x)
        dist_proj = math.sqrt(sum((p - xi)**2 for p, xi in zip(proj, x)))

        for _ in range(100):
            # Generate random simplex point via Dirichlet
            raw = [random.expovariate(1.0) for _ in range(5)]
            total = sum(raw)
            z = [r / total for r in raw]
            dist_z = math.sqrt(sum((zi - xi)**2 for zi, xi in zip(z, x)))
            assert dist_proj <= dist_z + 1e-9, \
                f"Projection is not nearest: d(proj)={dist_proj}, d(z)={dist_z}"

    @given(
        b=st.floats(min_value=-2.0, max_value=2.0),
        d=st.floats(min_value=-2.0, max_value=2.0),
        u=st.floats(min_value=-2.0, max_value=2.0),
    )
    @settings(max_examples=300)
    def test_opinion_projection_always_valid(self, b, d, u):
        """Property: projection of any (b,d,u) triple produces a valid opinion.

        This is the constraint restoration guarantee from §4.8.5.
        """
        assume(math.isfinite(b) and math.isfinite(d) and math.isfinite(u))
        result = simplex_project([b, d, u])
        assert abs(sum(result) - 1.0) < 1e-10
        assert all(v >= -1e-12 for v in result)

    def test_does_not_amplify_error(self):
        """Theorem 14: projection does not increase distance to true point.

        For a true simplex point x_true and noisy x̃ = x_true + noise:
          ‖project(x̃) - x_true‖ ≤ ‖x̃ - x_true‖
        """
        import random
        random.seed(42)
        for _ in range(200):
            # Generate true simplex point
            raw = [random.expovariate(1.0) for _ in range(3)]
            total = sum(raw)
            x_true = [r / total for r in raw]

            # Add noise
            noise = [random.gauss(0, 0.1) for _ in range(3)]
            x_noisy = [t + n for t, n in zip(x_true, noise)]

            # Project
            x_proj = simplex_project(x_noisy)

            # Distance comparison
            dist_noisy = math.sqrt(sum((n - t)**2 for n, t in zip(x_noisy, x_true)))
            dist_proj = math.sqrt(sum((p - t)**2 for p, t in zip(x_proj, x_true)))

            assert dist_proj <= dist_noisy + 1e-10, \
                f"Projection amplified error: {dist_proj} > {dist_noisy}"


# =========================================================================
# Lloyd-Max Optimal Scalar Quantizer — §4.8 Phase 4
#
# The Lloyd-Max algorithm iteratively optimizes a scalar quantizer for
# a known distribution, minimizing MSE. For CBOR-LD-ex batch compression,
# the target distribution is the post-RHT marginal:
#   - Gaussian asymptotic mode (dim=None): N(0.5, 1/36), valid for D ≥ ~64
#   - Beta-exact mode (dim=D): derived from uniform on S^(D-1)
#
# The codebook consists of:
#   - boundaries: 2^b - 1 decision boundaries
#   - centroids: 2^b reconstruction levels
#
# After convergence, the Lloyd-Max quantizer satisfies:
#   1. Each boundary = midpoint of adjacent centroids (nearest-neighbor)
#   2. Each centroid = conditional mean of distribution within its cell
#
# References:
#   Lloyd, S.P. (1982). Least Squares Quantization in PCM.
#   Max, J. (1960). Quantizing for Minimum Distortion.
# =========================================================================


class TestLloydMaxCodebook:
    """Phase 4: Lloyd-Max optimal scalar quantizer for post-RHT distribution.

    Tests cover both Gaussian asymptotic mode (dim=None) and Beta-exact
    mode (dim=D), verifying shape, ordering, symmetry, optimality conditions,
    and MSE superiority over uniform quantization.
    """

    # --- Shape and ordering ---

    @pytest.mark.parametrize("bits", [2, 3, 4, 5])
    def test_codebook_shape_gaussian(self, bits):
        """Gaussian mode: 2^b centroids and 2^b - 1 boundaries."""
        boundaries, centroids = lloyd_max_codebook(bits)
        assert len(centroids) == 2**bits
        assert len(boundaries) == 2**bits - 1

    @pytest.mark.parametrize("bits,dim", [(2, 32), (3, 64), (4, 128), (3, 256)])
    def test_codebook_shape_beta(self, bits, dim):
        """Beta-exact mode: 2^b centroids and 2^b - 1 boundaries."""
        boundaries, centroids = lloyd_max_codebook(bits, dim=dim)
        assert len(centroids) == 2**bits
        assert len(boundaries) == 2**bits - 1

    @pytest.mark.parametrize("bits", [2, 3, 4, 5])
    def test_boundaries_strictly_increasing(self, bits):
        """Decision boundaries must be strictly increasing."""
        boundaries, _ = lloyd_max_codebook(bits)
        for i in range(len(boundaries) - 1):
            assert boundaries[i] < boundaries[i + 1], \
                f"bits={bits}: b[{i}]={boundaries[i]} >= b[{i+1}]={boundaries[i+1]}"

    @pytest.mark.parametrize("bits", [2, 3, 4, 5])
    def test_centroids_strictly_increasing(self, bits):
        """Reconstruction centroids must be strictly increasing."""
        _, centroids = lloyd_max_codebook(bits)
        for i in range(len(centroids) - 1):
            assert centroids[i] < centroids[i + 1], \
                f"bits={bits}: c[{i}]={centroids[i]} >= c[{i+1}]={centroids[i+1]}"

    @pytest.mark.parametrize("bits", [2, 3, 4])
    def test_centroids_within_cells(self, bits):
        """Each centroid lies strictly within its Voronoi cell."""
        boundaries, centroids = lloyd_max_codebook(bits)
        # First centroid < first boundary
        assert centroids[0] < boundaries[0], \
            f"c[0]={centroids[0]} >= b[0]={boundaries[0]}"
        # Last centroid > last boundary
        assert centroids[-1] > boundaries[-1], \
            f"c[-1]={centroids[-1]} <= b[-1]={boundaries[-1]}"
        # Interior centroids between adjacent boundaries
        for i in range(len(boundaries) - 1):
            assert boundaries[i] < centroids[i + 1] < boundaries[i + 1], \
                f"c[{i+1}]={centroids[i+1]} not in ({boundaries[i]}, {boundaries[i+1]})"

    # --- Symmetry ---

    @pytest.mark.parametrize("bits", [2, 3, 4])
    def test_symmetry_gaussian(self, bits):
        """Gaussian N(0.5, 1/36) is symmetric about 0.5.

        Therefore: b[i] + b[K-2-i] = 1.0 and c[i] + c[K-1-i] = 1.0
        where K = 2^bits.
        """
        boundaries, centroids = lloyd_max_codebook(bits)
        n_b = len(boundaries)
        n_c = len(centroids)
        for i in range(n_b):
            assert abs(boundaries[i] + boundaries[n_b - 1 - i] - 1.0) < 1e-6, \
                f"bits={bits}: b[{i}]+b[{n_b-1-i}] = {boundaries[i]+boundaries[n_b-1-i]}"
        for i in range(n_c):
            assert abs(centroids[i] + centroids[n_c - 1 - i] - 1.0) < 1e-6, \
                f"bits={bits}: c[{i}]+c[{n_c-1-i}] = {centroids[i]+centroids[n_c-1-i]}"

    @pytest.mark.parametrize("bits,dim", [(2, 64), (3, 128)])
    def test_symmetry_beta(self, bits, dim):
        """Beta-exact mode: sphere marginal is symmetric about 0.5.

        The marginal of a uniform point on S^(D-1), after the affine
        mapping x = t/C + 0.5, is symmetric about 0.5.
        """
        boundaries, centroids = lloyd_max_codebook(bits, dim=dim)
        n_b = len(boundaries)
        n_c = len(centroids)
        for i in range(n_b):
            assert abs(boundaries[i] + boundaries[n_b - 1 - i] - 1.0) < 1e-5, \
                f"dim={dim}, bits={bits}: b[{i}]+b[{n_b-1-i}] != 1.0"
        for i in range(n_c):
            assert abs(centroids[i] + centroids[n_c - 1 - i] - 1.0) < 1e-5, \
                f"dim={dim}, bits={bits}: c[{i}]+c[{n_c-1-i}] != 1.0"

    # --- Optimality conditions ---

    @pytest.mark.parametrize("bits", [2, 3, 4, 5])
    def test_nearest_neighbor_boundaries(self, bits):
        """After convergence, each boundary = midpoint of adjacent centroids.

        This is the nearest-neighbor (Voronoi) condition: the boundary
        between cells i and i+1 is where distances to c_i and c_{i+1}
        are equal, i.e. the midpoint in 1D.
        """
        boundaries, centroids = lloyd_max_codebook(bits)
        for i in range(len(boundaries)):
            midpoint = (centroids[i] + centroids[i + 1]) / 2
            assert abs(boundaries[i] - midpoint) < 1e-6, \
                f"bits={bits}, i={i}: boundary={boundaries[i]}, midpoint={midpoint}"

    def test_centroid_conditional_mean_gaussian(self):
        """Each centroid = E[X | cell] under N(0.5, 1/36).

        Independent verification using scipy to compute the truncated
        normal conditional expectation analytically. This is the
        centroid optimality condition (non-circular: scipy computes
        the integral, our implementation runs Lloyd-Max iterations).
        """
        scipy_stats = pytest.importorskip("scipy.stats")
        from scipy.integrate import quad

        mu, sigma = 0.5, 1.0 / 6.0
        dist = scipy_stats.truncnorm(
            (0.0 - mu) / sigma, (1.0 - mu) / sigma, loc=mu, scale=sigma
        )

        for bits in [2, 3, 4]:
            boundaries, centroids = lloyd_max_codebook(bits)
            edges = [0.0] + list(boundaries) + [1.0]

            for i in range(len(centroids)):
                a, b = edges[i], edges[i + 1]
                p_cell = dist.cdf(b) - dist.cdf(a)
                if p_cell < 1e-15:
                    continue  # Skip negligible-probability cells
                integrand = lambda x: x * dist.pdf(x)
                expected_val, _ = quad(integrand, a, b)
                expected_val /= p_cell

                assert abs(centroids[i] - expected_val) < 1e-5, \
                    f"bits={bits}, cell {i}: centroid={centroids[i]}, " \
                    f"E[X|cell]={expected_val}"

    # --- MSE quality ---

    @pytest.mark.parametrize("bits", [2, 3, 4])
    def test_lloyd_max_beats_uniform_gaussian(self, bits):
        """Lloyd-Max MSE ≤ uniform MSE under N(0.5, 1/36) samples.

        This is THE key property: the optimized codebook must do at
        least as well as naive uniform quantization for the actual
        distribution encountered after the RHT.
        """
        import random
        random.seed(42)

        n_samples = 50000
        sigma = 1.0 / 6.0
        samples = [
            max(0.0, min(1.0, random.gauss(0.5, sigma)))
            for _ in range(n_samples)
        ]

        # Lloyd-Max MSE
        boundaries, centroids = lloyd_max_codebook(bits)
        mse_lm = 0.0
        for x in samples:
            code = quantize_lloyd_max(x, boundaries)
            recon = dequantize_lloyd_max(code, centroids)
            mse_lm += (x - recon) ** 2
        mse_lm /= n_samples

        # Uniform MSE
        levels = 2**bits - 1
        mse_uniform = 0.0
        for x in samples:
            code_u = max(0, min(levels, round(x * levels)))
            recon_u = code_u / levels
            mse_uniform += (x - recon_u) ** 2
        mse_uniform /= n_samples

        assert mse_lm <= mse_uniform * (1 + 1e-6), \
            f"bits={bits}: Lloyd-Max MSE {mse_lm:.6e} > uniform MSE {mse_uniform:.6e}"

    @pytest.mark.parametrize("bits", [2, 3])
    def test_lloyd_max_beats_uniform_beta(self, bits):
        """Lloyd-Max MSE ≤ uniform MSE under Beta-exact distribution.

        Samples drawn from the sphere marginal (Gaussian vectors
        normalized to unit sphere), mapped to [0,1] via x = t/C + 0.5.
        """
        import random
        random.seed(42)

        dim = 128
        n_samples = 20000
        c_val = 6.0 / math.sqrt(dim)

        samples = []
        for _ in range(n_samples):
            v = [random.gauss(0, 1) for _ in range(dim)]
            norm = math.sqrt(sum(xi**2 for xi in v))
            t = v[0] / norm  # marginal of uniform on S^(D-1)
            x = t / c_val + 0.5
            x = max(0.0, min(1.0, x))
            samples.append(x)

        # Lloyd-Max (Beta-exact)
        boundaries, centroids = lloyd_max_codebook(bits, dim=dim)
        mse_lm = sum(
            (x - dequantize_lloyd_max(
                quantize_lloyd_max(x, boundaries), centroids
            )) ** 2
            for x in samples
        ) / n_samples

        # Uniform
        levels = 2**bits - 1
        mse_u = sum(
            (x - max(0, min(levels, round(x * levels))) / levels) ** 2
            for x in samples
        ) / n_samples

        assert mse_lm <= mse_u * (1 + 1e-6), \
            f"dim={dim}, bits={bits}: Lloyd-Max MSE {mse_lm:.6e} > uniform {mse_u:.6e}"

    def test_mse_decreases_with_bits(self):
        """More bits → strictly lower MSE (monotonicity sanity check)."""
        import random
        random.seed(42)

        n_samples = 20000
        sigma = 1.0 / 6.0
        samples = [
            max(0.0, min(1.0, random.gauss(0.5, sigma)))
            for _ in range(n_samples)
        ]

        prev_mse = float('inf')
        for bits in [2, 3, 4, 5]:
            boundaries, centroids = lloyd_max_codebook(bits)
            mse = sum(
                (x - dequantize_lloyd_max(
                    quantize_lloyd_max(x, boundaries), centroids
                )) ** 2
                for x in samples
            ) / n_samples
            assert mse < prev_mse, \
                f"bits={bits}: MSE {mse:.6e} >= previous {prev_mse:.6e}"
            prev_mse = mse

    # --- Convergence between modes ---

    def test_gaussian_and_beta_converge_large_d(self):
        """For large D, Beta-exact codebook ≈ Gaussian codebook.

        Concentration of measure: the sphere marginal → N(0, 1/D) as
        D → ∞, so after the affine mapping both distributions converge
        to N(0.5, 1/36). The codebooks should therefore converge.
        """
        bits = 3
        b_gauss, c_gauss = lloyd_max_codebook(bits)

        for dim in [256, 512]:
            b_beta, c_beta = lloyd_max_codebook(bits, dim=dim)

            for i in range(len(b_gauss)):
                assert abs(b_gauss[i] - b_beta[i]) < 0.01, \
                    f"dim={dim}: Gaussian b[{i}]={b_gauss[i]:.6f}, " \
                    f"Beta b[{i}]={b_beta[i]:.6f}"

            for i in range(len(c_gauss)):
                assert abs(c_gauss[i] - c_beta[i]) < 0.01, \
                    f"dim={dim}: Gaussian c[{i}]={c_gauss[i]:.6f}, " \
                    f"Beta c[{i}]={c_beta[i]:.6f}"

    # --- Cache ---

    def test_cache_returns_identical(self):
        """Same parameters return the exact same codebook objects."""
        b1, c1 = lloyd_max_codebook(3)
        b2, c2 = lloyd_max_codebook(3)
        assert b1 is b2, "Cache should return the same list object"
        assert c1 is c2, "Cache should return the same list object"

    def test_cache_distinct_for_different_params(self):
        """Different parameters return different codebooks."""
        b3, _ = lloyd_max_codebook(3)
        b4, _ = lloyd_max_codebook(4)
        assert b3 is not b4

        # Beta vs Gaussian at same bits
        b_gauss, _ = lloyd_max_codebook(3)
        b_beta, _ = lloyd_max_codebook(3, dim=32)
        assert b_gauss is not b_beta


class TestLloydMaxQuantize:
    """Phase 4: quantize and dequantize using Lloyd-Max codebook."""

    @pytest.mark.parametrize("bits", [2, 3, 4])
    def test_centroids_quantize_to_own_code(self, bits):
        """Each centroid maps to its own index."""
        boundaries, centroids = lloyd_max_codebook(bits)
        for i, c in enumerate(centroids):
            code = quantize_lloyd_max(c, boundaries)
            assert code == i, \
                f"bits={bits}: centroid[{i}]={c} mapped to code {code}"

    @pytest.mark.parametrize("bits", [2, 3, 4])
    def test_codes_in_valid_range(self, bits):
        """All codes are in [0, 2^b - 1]."""
        import random
        random.seed(42)
        boundaries, _ = lloyd_max_codebook(bits)
        max_code = 2**bits - 1
        for _ in range(1000):
            x = random.random()
            code = quantize_lloyd_max(x, boundaries)
            assert 0 <= code <= max_code, \
                f"bits={bits}: code {code} out of range [0, {max_code}]"

    def test_quantize_monotonic(self):
        """Monotonicity: x1 < x2 implies quantize(x1) ≤ quantize(x2)."""
        boundaries, _ = lloyd_max_codebook(3)
        xs = [i / 1000.0 for i in range(1001)]
        codes = [quantize_lloyd_max(x, boundaries) for x in xs]
        for i in range(len(codes) - 1):
            assert codes[i] <= codes[i + 1], \
                f"x={xs[i]:.3f}→{codes[i]}, x={xs[i+1]:.3f}→{codes[i+1]}"

    @pytest.mark.parametrize("bits", [2, 3, 4])
    def test_dequantize_returns_centroid(self, bits):
        """Dequantize maps each code to the corresponding centroid."""
        _, centroids = lloyd_max_codebook(bits)
        for i in range(2**bits):
            val = dequantize_lloyd_max(i, centroids)
            assert val == centroids[i], \
                f"bits={bits}: dequantize({i}) = {val}, expected {centroids[i]}"

    def test_extreme_values(self):
        """Values well outside [0,1] map to extreme codes.

        Since all boundaries are within (0, 1), a value below all
        boundaries gets code 0, and above all boundaries gets the
        maximum code. No explicit clamping needed — follows from
        the comparison logic.
        """
        boundaries, _ = lloyd_max_codebook(3)
        max_code = 2**3 - 1
        assert quantize_lloyd_max(-0.5, boundaries) == 0
        assert quantize_lloyd_max(1.5, boundaries) == max_code

    @pytest.mark.parametrize("bits", [2, 3, 4])
    def test_roundtrip_near_centroids(self, bits):
        """Values near centroids survive roundtrip with minimal error."""
        boundaries, centroids = lloyd_max_codebook(bits)
        for i, c in enumerate(centroids):
            # Slightly perturb centroid
            for delta in [-1e-8, 0.0, 1e-8]:
                x = c + delta
                code = quantize_lloyd_max(x, boundaries)
                recon = dequantize_lloyd_max(code, centroids)
                assert abs(recon - c) < 1e-6, \
                    f"bits={bits}, centroid {i}: x={x}, recon={recon}"


# =========================================================================
# Randomized Hadamard Transform (RHT) — §4.8 Phase 5a
#
# Definition 34: The RHT applies a random sign flip, random permutation,
# and normalized Walsh-Hadamard transform. The inverse reverses all three.
#
# Protocol-critical: both encoder and decoder generate identical sign
# vectors and permutations from the same xoshiro128++ seed. Bit
# consumption order: first D bits for signs (MSB-first from each next()
# call), then Fisher-Yates shuffle bits for the permutation.
#
# Properties:
#   - Roundtrip: rht_inverse(rht_forward(v, seed), seed) = v
#   - Orthogonal: ‖rht_forward(v, seed)‖ = ‖v‖ (norm preservation)
#   - Deterministic: same seed → same output
# =========================================================================


class TestRHTRoundtrip:
    """Phase 5a: rht_inverse(rht_forward(v, seed), seed) = v.

    THE critical correctness property. If this fails, the batch
    codec cannot recover the original opinions.
    """

    @pytest.mark.parametrize("d", [4, 8, 16, 32, 64, 128, 256])
    def test_roundtrip_random_vector(self, d):
        """Random vector survives forward → inverse at all power-of-2 sizes."""
        import random
        random.seed(42 + d)
        v = [random.gauss(0, 1) for _ in range(d)]
        seed = 12345

        w = rht_forward(v, seed)
        v_recovered = rht_inverse(w, seed)

        assert len(v_recovered) == d
        for i in range(d):
            assert abs(v_recovered[i] - v[i]) < 1e-9, \
                f"d={d}, i={i}: expected {v[i]:.10f}, got {v_recovered[i]:.10f}"

    def test_roundtrip_opinion_like(self):
        """Roundtrip with opinion-like data (values in [0,1], padded).

        Simulates the actual batch pipeline: 10 opinions → 30 values →
        pad to D=32 → RHT → inverse RHT → recover first 30.
        """
        import random
        random.seed(99)

        n_opinions = 10
        # 3 free params per opinion: b, d, a
        raw = []
        for _ in range(n_opinions):
            b = random.random() * 0.5
            d = random.random() * (1.0 - b)
            a = random.random()
            raw.extend([b, d, a])

        # Pad to power of 2
        d = 32  # next power of 2 >= 30
        v_padded = raw + [0.0] * (d - len(raw))

        w = rht_forward(v_padded, seed=42)
        v_recovered = rht_inverse(w, seed=42)

        for i in range(len(raw)):
            assert abs(v_recovered[i] - raw[i]) < 1e-9, \
                f"i={i}: expected {raw[i]:.10f}, got {v_recovered[i]:.10f}"
        # Padding should also recover
        for i in range(len(raw), d):
            assert abs(v_recovered[i]) < 1e-9

    @pytest.mark.parametrize("seed", [0, 1, 42, 0xDEADBEEF, 0xFFFFFFFF])
    def test_roundtrip_multiple_seeds(self, seed):
        """Roundtrip works for various seeds."""
        import random
        random.seed(seed & 0xFFFF)
        v = [random.gauss(0, 1) for _ in range(64)]

        w = rht_forward(v, seed)
        v_recovered = rht_inverse(w, seed)

        for i in range(64):
            assert abs(v_recovered[i] - v[i]) < 1e-9

    def test_roundtrip_unit_vectors(self):
        """Each standard basis vector survives roundtrip."""
        d = 16
        seed = 42
        for k in range(d):
            v = [0.0] * d
            v[k] = 1.0
            w = rht_forward(v, seed)
            v_recovered = rht_inverse(w, seed)
            for i in range(d):
                assert abs(v_recovered[i] - v[i]) < 1e-9, \
                    f"basis e_{k}, i={i}"


class TestRHTOrthogonality:
    """The RHT is orthogonal: ‖w‖ = ‖v‖ (norm preservation).

    Critical for distortion-rate analysis: quantization MSE in the
    rotated domain equals MSE in the original domain.
    """

    @pytest.mark.parametrize("d", [8, 16, 32, 64, 128, 256])
    def test_norm_preservation(self, d):
        """L2 norm is preserved after forward RHT."""
        import random
        random.seed(42 + d)
        v = [random.gauss(0, 1) for _ in range(d)]

        w = rht_forward(v, seed=42)

        norm_v = math.sqrt(sum(x**2 for x in v))
        norm_w = math.sqrt(sum(x**2 for x in w))

        assert abs(norm_v - norm_w) < 1e-9 * norm_v, \
            f"d={d}: ‖v‖={norm_v:.10f}, ‖w‖={norm_w:.10f}"

    def test_norm_preservation_opinion_data(self):
        """Norm preserved for opinion-like data (bounded [0,1] values)."""
        import random
        random.seed(99)
        v = [random.random() for _ in range(64)]

        w = rht_forward(v, seed=123)

        norm_v = math.sqrt(sum(x**2 for x in v))
        norm_w = math.sqrt(sum(x**2 for x in w))

        assert abs(norm_v - norm_w) < 1e-9 * norm_v


class TestRHTDeterminism:
    """Protocol-mandated: same seed produces bit-identical output.

    Any conformant implementation MUST produce the same sign vector
    and permutation from the same seed (Definition 34d).
    """

    def test_same_seed_same_output(self):
        """Two calls with same seed produce identical results."""
        import random
        random.seed(42)
        v = [random.gauss(0, 1) for _ in range(64)]

        w1 = rht_forward(v, seed=42)
        w2 = rht_forward(v, seed=42)

        for i in range(64):
            assert w1[i] == w2[i], f"i={i}: {w1[i]} != {w2[i]}"

    def test_different_seeds_differ(self):
        """Different seeds produce different rotations."""
        import random
        random.seed(42)
        v = [random.gauss(0, 1) for _ in range(16)]  # dense vector

        w1 = rht_forward(v, seed=0)
        w2 = rht_forward(v, seed=1)

        # At least some coordinates must differ
        diffs = sum(1 for a, b in zip(w1, w2) if abs(a - b) > 1e-12)
        assert diffs > 0, "Different seeds produced identical output"

    def test_different_seeds_produce_different_internals(self):
        """Directly verify signs and permutations differ for different seeds.

        This is a diagnostic test ensuring the PRNG generates distinct
        internal state for different seeds. Addresses the concern that
        seeds 0 and 1 might accidentally produce identical RHT parameters.
        """
        from cbor_ld_ex.batch import (
            _generate_signs, _generate_permutation,
        )
        d = 16

        rng0 = Xoshiro128PlusPlus(0)
        signs0 = _generate_signs(rng0, d)
        perm0 = _generate_permutation(rng0, d)

        rng1 = Xoshiro128PlusPlus(1)
        signs1 = _generate_signs(rng1, d)
        perm1 = _generate_permutation(rng1, d)

        # Signs must differ (probability of identical: 2^{-16} < 2e-5)
        assert signs0 != signs1, \
            f"seeds 0,1 produced identical sign vectors: {signs0}"
        # Permutations must differ (probability of identical: 1/16! ~ 5e-14)
        assert perm0 != perm1, \
            f"seeds 0,1 produced identical permutations: {perm0}"

    def test_output_not_equal_to_plain_fwht(self):
        """RHT applies sign flip + permutation, not just FWHT.

        The RHT output must differ from plain fwht(v) because the
        sign vector and permutation scramble coordinates first.
        """
        import random
        random.seed(42)
        v = [random.gauss(0, 1) for _ in range(32)]

        w_rht = rht_forward(v, seed=42)
        w_plain = fwht(v)

        diffs = sum(1 for a, b in zip(w_rht, w_plain) if abs(a - b) > 1e-12)
        assert diffs > 0, "RHT output equals plain FWHT — signs/perm not applied"


class TestRHTEdgeCases:
    """Edge cases for the RHT."""

    def test_d1_passthrough(self):
        """D=1: only one sign flip possible, H_1 = [1], so v or -v."""
        v = [3.14]
        w = rht_forward(v, seed=0)
        assert len(w) == 1
        # Roundtrip must work
        v_back = rht_inverse(w, seed=0)
        assert abs(v_back[0] - 3.14) < 1e-12

    def test_d2_roundtrip(self):
        """D=2: smallest non-trivial case."""
        v = [1.0, 2.0]
        w = rht_forward(v, seed=42)
        v_back = rht_inverse(w, seed=42)
        assert abs(v_back[0] - 1.0) < 1e-12
        assert abs(v_back[1] - 2.0) < 1e-12

    def test_zero_vector(self):
        """Zero vector maps to zero vector (linearity)."""
        v = [0.0] * 32
        w = rht_forward(v, seed=42)
        for val in w:
            assert abs(val) < 1e-15

    def test_non_power_of_2_rejected(self):
        """Non-power-of-2 input raises ValueError (from underlying fwht)."""
        with pytest.raises(ValueError):
            rht_forward([1.0, 2.0, 3.0], seed=42)


# =========================================================================
# Batch Encode/Decode Pipeline — §4.8 Phase 5b
#
# Full pipeline combining all primitives:
#   Encode: stack → pad → RHT → normalize → quantize → pack
#   Decode: unpack → dequantize → denormalize → inv RHT → unpad → restore
#
# Wire format (§4.8.4, Definition 35):
#   [4 bytes]  seed (uint32)
#   [2 bytes]  norm_q (uint16)
#   [ceil(D × b / 8) bytes]  packed quantized coordinates
#   Total: 6 + ceil(D×b/8) bytes. No extra bytes. Period.
#
# Constraint restoration (§4.8.5, Definition 36):
#   Step A: L2 simplex projection for (b, d, u)
#   Step B: Clamp a to [0, 1]
# =========================================================================


def _make_opinions(n, seed=42):
    """Generate n random valid SL opinions for testing.

    Returns list of (b, d, u, a) with b+d+u=1, all ≥ 0, a ∈ [0,1].
    """
    import random
    random.seed(seed)
    opinions = []
    for _ in range(n):
        # Random simplex point via Dirichlet(1,1,1)
        raw = [random.expovariate(1.0) for _ in range(3)]
        total = sum(raw)
        b, d, u = raw[0] / total, raw[1] / total, raw[2] / total
        a = random.random()
        opinions.append((b, d, u, a))
    return opinions


def _next_pow2(n: int) -> int:
    """Next power of 2 >= n."""
    p = 1
    while p < n:
        p <<= 1
    return p


class TestBatchWireFormat:
    """Phase 5b: Wire format correctness.

    The wire format is exactly 6 + ceil(D×b/8) bytes.
    Seed MSB carries the quantizer mode flag (v0.4.5).
    Every byte is accounted for.
    """

    @pytest.mark.parametrize("n_opinions,bits", [
        (8, 3), (10, 3), (10, 4), (20, 3), (32, 3),
        (32, 4), (50, 3), (100, 3),
    ])
    def test_wire_size_exact(self, n_opinions, bits):
        """Wire size = 6 + ceil(D × b / 8) bytes, no more, no less."""
        opinions = _make_opinions(n_opinions)
        data = encode_batch(opinions, bits, seed=42)

        d = _next_pow2(3 * n_opinions)
        expected_size = 6 + math.ceil(d * bits / 8)
        assert len(data) == expected_size, \
            f"N={n_opinions}, b={bits}: got {len(data)} bytes, " \
            f"expected {expected_size} (D={d})"

    def test_seed_and_mode_in_first_4_bytes(self):
        """First 4 bytes = seed_mode: MSB is mode flag, bits 30-0 are seed.

        v0.4.5: The old test used 0xDEADBEEF and expected exact match,
        which passed by coincidence (MSB=1 matched lloyd_max mode=1).
        This test explicitly verifies the mode flag and masked seed.
        """
        import struct
        opinions = _make_opinions(10)
        seed = 0x12345678  # MSB=0, no ambiguity
        data = encode_batch(opinions, bits=3, seed=seed, quantizer='lloyd_max')
        seed_mode = struct.unpack('>I', data[:4])[0]
        assert seed_mode >> 31 == 1, "Lloyd-Max mode flag must be 1"
        assert (seed_mode & 0x7FFFFFFF) == seed, \
            f"Seed in lower 31 bits: expected 0x{seed:08X}, " \
            f"got 0x{seed_mode & 0x7FFFFFFF:08X}"

        # Same seed, uniform mode — MSB must be 0
        data_u = encode_batch(opinions, bits=3, seed=seed, quantizer='uniform')
        seed_mode_u = struct.unpack('>I', data_u[:4])[0]
        assert seed_mode_u >> 31 == 0, "Uniform mode flag must be 0"
        assert (seed_mode_u & 0x7FFFFFFF) == seed

    def test_norm_in_bytes_4_5(self):
        """Bytes 4–5 contain the uint16 quantized norm."""
        import struct
        opinions = _make_opinions(10)
        data = encode_batch(opinions, bits=3, seed=42)
        norm_q = struct.unpack('>H', data[4:6])[0]
        # Norm of 30 opinion values in [0,1] should be positive
        assert 0 < norm_q <= 65535


class TestBatchWireFormatModeFlag:
    """Wire format mode flag: seed MSB encodes quantizer mode (spec v0.4.5).

    The 4-byte seed_mode field is:
      - Bit 31 (MSB): quantizer mode m (0 = uniform, 1 = Lloyd-Max)
      - Bits 30-0: PRNG seed s (range [0, 2^31 - 1])

    This makes the wire format self-describing at zero additional byte cost.
    """

    def test_lloyd_max_sets_msb(self):
        """Lloyd-Max mode (m=1) sets bit 31 of the seed field."""
        import struct
        opinions = _make_opinions(10)
        data = encode_batch(opinions, bits=3, seed=42, quantizer='lloyd_max')
        seed_mode = struct.unpack('>I', data[:4])[0]
        mode = seed_mode >> 31
        assert mode == 1, f"Expected mode=1 (Lloyd-Max), got {mode}"

    def test_uniform_clears_msb(self):
        """Uniform mode (m=0) clears bit 31 of the seed field."""
        import struct
        opinions = _make_opinions(10)
        data = encode_batch(opinions, bits=3, seed=42, quantizer='uniform')
        seed_mode = struct.unpack('>I', data[:4])[0]
        mode = seed_mode >> 31
        assert mode == 0, f"Expected mode=0 (uniform), got {mode}"

    def test_seed_in_lower_31_bits(self):
        """The PRNG seed is stored in bits 30-0."""
        import struct
        opinions = _make_opinions(10)
        seed = 0x12345678  # bit 31 = 0, so no conflict
        data = encode_batch(opinions, bits=3, seed=seed, quantizer='lloyd_max')
        seed_mode = struct.unpack('>I', data[:4])[0]
        extracted_seed = seed_mode & 0x7FFFFFFF
        assert extracted_seed == seed, \
            f"Expected seed 0x{seed:08X}, got 0x{extracted_seed:08X}"

    def test_seed_masked_to_31_bits(self):
        """Seeds >= 2^31 are masked to 31 bits (MSB reserved for mode)."""
        import struct
        opinions = _make_opinions(10)
        seed = 0xDEADBEEF  # bit 31 = 1
        data = encode_batch(opinions, bits=3, seed=seed, quantizer='uniform')
        seed_mode = struct.unpack('>I', data[:4])[0]
        mode = seed_mode >> 31
        extracted_seed = seed_mode & 0x7FFFFFFF
        assert mode == 0, "Mode should be 0 (uniform), not contaminated by seed MSB"
        assert extracted_seed == (seed & 0x7FFFFFFF), \
            f"Seed should be masked: expected 0x{seed & 0x7FFFFFFF:08X}, " \
            f"got 0x{extracted_seed:08X}"

    def test_decode_auto_detects_lloyd_max(self):
        """decode_batch with quantizer=None auto-detects Lloyd-Max from wire."""
        opinions = _make_opinions(20)
        data = encode_batch(opinions, bits=3, seed=42, quantizer='lloyd_max')
        decoded = decode_batch(data, 20, bits=3, quantizer=None)
        assert len(decoded) == 20
        # Must match explicit Lloyd-Max decode
        decoded_explicit = decode_batch(data, 20, bits=3, quantizer='lloyd_max')
        for d_auto, d_exp in zip(decoded, decoded_explicit):
            for j in range(4):
                assert d_auto[j] == d_exp[j], \
                    f"Auto-detect mismatch: {d_auto} != {d_exp}"

    def test_decode_auto_detects_uniform(self):
        """decode_batch with quantizer=None auto-detects uniform from wire."""
        opinions = _make_opinions(20)
        data = encode_batch(opinions, bits=3, seed=42, quantizer='uniform')
        decoded = decode_batch(data, 20, bits=3, quantizer=None)
        assert len(decoded) == 20
        decoded_explicit = decode_batch(data, 20, bits=3, quantizer='uniform')
        for d_auto, d_exp in zip(decoded, decoded_explicit):
            for j in range(4):
                assert d_auto[j] == d_exp[j], \
                    f"Auto-detect mismatch: {d_auto} != {d_exp}"

    def test_decode_mismatch_raises(self):
        """decode_batch raises if explicit quantizer contradicts wire mode."""
        opinions = _make_opinions(10)
        data = encode_batch(opinions, bits=3, seed=42, quantizer='lloyd_max')
        with pytest.raises(ValueError, match="[Qq]uantizer.*mode|[Mm]ode.*mismatch"):
            decode_batch(data, 10, bits=3, quantizer='uniform')

    def test_wire_size_unchanged_both_modes(self):
        """Mode flag steals from seed - wire size stays 6 + ceil(D*b/8)."""
        for quantizer in ('lloyd_max', 'uniform'):
            opinions = _make_opinions(20)
            data = encode_batch(opinions, bits=3, seed=42, quantizer=quantizer)
            d = _next_pow2(60)
            expected = 6 + math.ceil(d * 3 / 8)
            assert len(data) == expected, \
                f"q={quantizer}: got {len(data)}, expected {expected}"

    def test_round_trip_both_modes_auto_detect(self):
        """Both modes round-trip correctly with auto-detect decode."""
        opinions = _make_opinions(32)
        for quantizer in ('lloyd_max', 'uniform'):
            data = encode_batch(opinions, bits=4, seed=99, quantizer=quantizer)
            decoded = decode_batch(data, 32, bits=4)  # auto-detect (quantizer=None)
            for orig, dec in zip(opinions, decoded):
                for j in range(4):
                    assert abs(orig[j] - dec[j]) < 0.5, \
                        f"q={quantizer}: large error {abs(orig[j]-dec[j]):.4f}"

    def test_rht_uses_masked_seed_not_full_field(self):
        """The RHT must use the 31-bit seed, not the full seed_mode field.

        Encodes the same opinions with the same 31-bit seed but different modes.
        The RHT output differs (different quantizer), but the RHT permutation
        and signs must be identical (same seed). We verify by checking that
        the seed extracted from the wire is consistent.
        """
        import struct
        opinions = _make_opinions(10)
        seed = 42
        data_lm = encode_batch(opinions, bits=3, seed=seed, quantizer='lloyd_max')
        data_u = encode_batch(opinions, bits=3, seed=seed, quantizer='uniform')

        sm_lm = struct.unpack('>I', data_lm[:4])[0]
        sm_u = struct.unpack('>I', data_u[:4])[0]

        # Same 31-bit seed
        assert (sm_lm & 0x7FFFFFFF) == (sm_u & 0x7FFFFFFF) == seed
        # Different MSB (mode flag)
        assert (sm_lm >> 31) == 1  # Lloyd-Max
        assert (sm_u >> 31) == 0   # uniform


class TestBatchRoundtrip:
    """Phase 5b: encode → decode recovers opinions within quantization tolerance.

    The tolerance depends on bit-width: more bits → lower error.
    At 3 bits with 8 levels, per-coordinate error ≤ 1/(2×7) ≈ 0.071.
    After RHT mixing + norm quantization, per-opinion error is larger
    but bounded.
    """

    @pytest.mark.parametrize("n_opinions,bits,quantizer", [
        (8, 3, 'uniform'), (10, 3, 'uniform'), (20, 4, 'uniform'),
        (32, 3, 'uniform'), (50, 4, 'uniform'),
        (8, 3, 'lloyd_max'), (10, 4, 'lloyd_max'), (20, 3, 'lloyd_max'),
        (32, 4, 'lloyd_max'), (50, 3, 'lloyd_max'),
    ])
    def test_total_mse_within_theoretical_bound(self, n_opinions, bits, quantizer):
        """Total MSE over free parameters (b, d, a) is within derived bound.

        Derivation:
          After RHT + quantization + inverse RHT, the error budget is:

          1. Each of D rotated coordinates has quantization error |e_j|.
             For uniform: |e_j| <= 1/(2(2^b - 1)).
             For Lloyd-Max: e_j is smaller on average, but bounded same.
          2. RHT is orthogonal: ||v - v_recon||^2 = ||w - w_recon||^2
          3. Denormalization scales each error by norm * C:
             ||v - v_recon||^2 = (norm*C)^2 * sum(e_j^2)
          4. C = 6/sqrt(D), so C^2 = 36/D. Therefore:
             ||v - v_recon||^2 = norm^2 * (36/D) * sum(e_j^2)
          5. Worst case: sum(e_j^2) <= D/(4(2^b-1)^2)
             => ||v - v_recon||^2 <= 9 * norm^2 / (2^b - 1)^2

          Per-opinion MSE = ||v - v_recon||^2 / N

          Norm quantization adds epsilon_norm ~ O(10^-8), negligible.
          Simplex projection does not increase MSE (Theorem 14).

        This bound is tight: it depends only on ||v||^2, N, and b.
        The D factor cancels from the C normalization.
        """
        opinions = _make_opinions(n_opinions)
        data = encode_batch(opinions, bits=bits, seed=42, quantizer=quantizer)
        decoded = decode_batch(data, n_opinions, bits=bits, quantizer=quantizer)

        # Compute actual total MSE over free parameters (b, d, a)
        # Note: u is derived, so we check (b, d, u, a) but the bound
        # is derived from the 3 free parameters (b, d, a) in the stacked vector
        actual_mse = sum(
            (orig[0] - dec[0])**2 + (orig[1] - dec[1])**2 + (orig[3] - dec[3])**2
            for orig, dec in zip(opinions, decoded)
        ) / n_opinions

        # Compute ||v||^2 = sum of b_i^2 + d_i^2 + a_i^2
        v_norm_sq = sum(
            o[0]**2 + o[1]**2 + o[3]**2 for o in opinions
        )

        # Theoretical bound: 9 * ||v||^2 / (N * (2^b - 1)^2)
        k_minus_1 = 2**bits - 1
        theoretical_bound = 9.0 * v_norm_sq / (n_opinions * k_minus_1**2)

        # Add norm quantization margin: (norm_max / (2*65535))^2 * 36
        # This is a small second-order term
        norm_max = math.sqrt(3.0 * n_opinions)
        norm_err = norm_max / (2 * 65535)
        norm_margin = 36.0 * norm_err**2  # scales through C^2 * D = 36
        bound = theoretical_bound + norm_margin

        assert actual_mse <= bound, \
            f"N={n_opinions}, b={bits}, q={quantizer}: " \
            f"MSE {actual_mse:.6e} > bound {bound:.6e} " \
            f"(theoretical {theoretical_bound:.6e})"

    @pytest.mark.parametrize("n_opinions,bits", [
        (8, 3), (20, 4), (32, 3), (50, 4),
    ])
    def test_u_component_mse_bounded_by_free_param_mse(self, n_opinions, bits):
        """Theorem 14: simplex projection does not amplify MSE.

        The u component is derived as u = 1 - b - d. After reconstruction,
        u_raw = 1 - b_raw - d_raw. Simplex projection yields (b_proj, d_proj,
        u_proj) with ||proj - true||^2 <= ||raw - true||^2.

        Therefore per-opinion MSE over (b, d, u) <= per-opinion MSE over
        (b_raw, d_raw, u_raw), which is bounded by the free-parameter
        MSE (since u_raw = 1 - b_raw - d_raw and u_true = 1 - b - d,
        the u_raw error is ||e_b + e_d|| which is at most 2x the
        free-parameter error).
        """
        opinions = _make_opinions(n_opinions)
        data = encode_batch(opinions, bits=bits, seed=42)
        decoded = decode_batch(data, n_opinions, bits=bits)

        # MSE over (b, d, u)
        mse_bdu = sum(
            (orig[0] - dec[0])**2 +
            (orig[1] - dec[1])**2 +
            (orig[2] - dec[2])**2
            for orig, dec in zip(opinions, decoded)
        ) / n_opinions

        # MSE over (b, d, a) — the free parameters
        mse_free = sum(
            (orig[0] - dec[0])**2 +
            (orig[1] - dec[1])**2 +
            (orig[3] - dec[3])**2
            for orig, dec in zip(opinions, decoded)
        ) / n_opinions

        # The (b,d,u) MSE should be comparable to free-param MSE.
        # Simplex projection guarantees (b,d,u) MSE <= pre-projection MSE.
        # Pre-projection u error = -(e_b + e_d), so pre-projection
        # (b,d,u) MSE = e_b^2 + e_d^2 + (e_b+e_d)^2 <= 3*(e_b^2+e_d^2)
        # plus the a error is independent, so:
        # mse_bdu <= 3 * mse_free is a safe bound
        assert mse_bdu <= 3 * mse_free + 1e-10, \
            f"N={n_opinions}, b={bits}: bdu MSE {mse_bdu:.6e} > 3 * free MSE {mse_free:.6e}"

    def test_higher_bits_lower_mse(self):
        """More bits → strictly lower total MSE (monotonicity)."""
        opinions = _make_opinions(32)
        prev_mse = float('inf')

        for bits in [3, 4, 5, 6]:
            data = encode_batch(opinions, bits=bits, seed=42)
            decoded = decode_batch(data, 32, bits=bits)

            mse = sum(
                sum((o[j] - d[j])**2 for j in range(4))
                for o, d in zip(opinions, decoded)
            ) / (32 * 4)

            assert mse < prev_mse, \
                f"bits={bits}: MSE {mse:.6e} >= previous {prev_mse:.6e}"
            prev_mse = mse

    def test_lloyd_max_mse_leq_uniform_mse(self):
        """Lloyd-Max should achieve MSE <= uniform at same bit-width.

        This is the whole point of Lloyd-Max optimization.
        """
        opinions = _make_opinions(32)

        for bits in [3, 4]:
            data_u = encode_batch(opinions, bits=bits, seed=42, quantizer='uniform')
            decoded_u = decode_batch(data_u, 32, bits=bits, quantizer='uniform')

            data_lm = encode_batch(opinions, bits=bits, seed=42, quantizer='lloyd_max')
            decoded_lm = decode_batch(data_lm, 32, bits=bits, quantizer='lloyd_max')

            mse_u = sum(
                sum((o[j] - d[j])**2 for j in range(4))
                for o, d in zip(opinions, decoded_u)
            ) / (32 * 4)

            mse_lm = sum(
                sum((o[j] - d[j])**2 for j in range(4))
                for o, d in zip(opinions, decoded_lm)
            ) / (32 * 4)

            assert mse_lm <= mse_u * (1 + 1e-3), \
                f"bits={bits}: Lloyd-Max MSE {mse_lm:.6e} > uniform {mse_u:.6e}"


class TestBatchConstraintPreservation:
    """Phase 5b: Axiom 3 (b+d+u=1) and base rate validity (a ∈ [0,1]).

    This is a NON-NEGOTIABLE guarantee: every decoded opinion is a valid
    SL opinion, regardless of quantization noise. Enforced by simplex
    projection (Step A) and base rate clamping (Step B) of Definition 36.
    """

    @pytest.mark.parametrize("n_opinions,bits,quantizer", [
        (8, 3, 'uniform'), (10, 3, 'uniform'), (32, 3, 'uniform'),
        (50, 4, 'uniform'), (100, 3, 'uniform'),
        (8, 3, 'lloyd_max'), (32, 4, 'lloyd_max'), (50, 3, 'lloyd_max'),
    ])
    def test_axiom3_exact(self, n_opinions, bits, quantizer):
        """Every decoded opinion satisfies b+d+u=1 exactly."""
        opinions = _make_opinions(n_opinions)
        data = encode_batch(opinions, bits=bits, seed=42, quantizer=quantizer)
        decoded = decode_batch(data, n_opinions, bits=bits, quantizer=quantizer)

        for i, (b, d, u, a) in enumerate(decoded):
            total = b + d + u
            assert abs(total - 1.0) < 1e-12, \
                f"opinion {i}: b+d+u = {total} (quantizer={quantizer})"

    @pytest.mark.parametrize("n_opinions,bits,quantizer", [
        (8, 3, 'uniform'), (32, 4, 'uniform'),
        (8, 3, 'lloyd_max'), (32, 4, 'lloyd_max'),
    ])
    def test_components_non_negative(self, n_opinions, bits, quantizer):
        """Every decoded b, d, u ≥ 0."""
        opinions = _make_opinions(n_opinions)
        data = encode_batch(opinions, bits=bits, seed=42, quantizer=quantizer)
        decoded = decode_batch(data, n_opinions, bits=bits, quantizer=quantizer)

        for i, (b, d, u, a) in enumerate(decoded):
            assert b >= -1e-12, f"opinion {i}: b = {b}"
            assert d >= -1e-12, f"opinion {i}: d = {d}"
            assert u >= -1e-12, f"opinion {i}: u = {u}"

    @pytest.mark.parametrize("n_opinions,bits,quantizer", [
        (8, 3, 'uniform'), (32, 4, 'uniform'),
        (8, 3, 'lloyd_max'), (32, 4, 'lloyd_max'),
    ])
    def test_base_rate_in_unit_interval(self, n_opinions, bits, quantizer):
        """Every decoded a ∈ [0, 1]."""
        opinions = _make_opinions(n_opinions)
        data = encode_batch(opinions, bits=bits, seed=42, quantizer=quantizer)
        decoded = decode_batch(data, n_opinions, bits=bits, quantizer=quantizer)

        for i, (b, d, u, a) in enumerate(decoded):
            assert -1e-12 <= a <= 1.0 + 1e-12, \
                f"opinion {i}: a = {a}"


class TestBatchDeterminism:
    """Phase 5b: deterministic encoding.

    Same opinions + same seed = identical bytes.
    Protocol-mandated for decoder interoperability.
    """

    def test_same_seed_identical_bytes(self):
        """Two encodes with same seed produce identical wire bytes."""
        opinions = _make_opinions(20)
        data1 = encode_batch(opinions, bits=3, seed=42)
        data2 = encode_batch(opinions, bits=3, seed=42)
        assert data1 == data2

    def test_different_seeds_different_bytes(self):
        """Different seeds produce different wire bytes."""
        opinions = _make_opinions(20)
        data1 = encode_batch(opinions, bits=3, seed=42)
        data2 = encode_batch(opinions, bits=3, seed=43)
        # Seeds differ (first 4 bytes), and rotated coords differ
        assert data1 != data2

    def test_auto_seed_is_random(self):
        """When seed=None, a random seed is generated and embedded."""
        import struct
        opinions = _make_opinions(10)
        data1 = encode_batch(opinions, bits=3)
        data2 = encode_batch(opinions, bits=3)

        seed1 = struct.unpack('>I', data1[:4])[0]
        seed2 = struct.unpack('>I', data2[:4])[0]
        # Overwhelmingly likely to differ
        assert seed1 != seed2, "Two auto-seeds were identical"

    def test_auto_seed_roundtrips(self):
        """Auto-seeded encoding can be decoded (seed read from wire).

        Verifies that the seed embedded in the wire format is sufficient
        for the decoder. Uses the same MSE bound as test_total_mse.
        """
        opinions = _make_opinions(20)
        data = encode_batch(opinions, bits=4)
        decoded = decode_batch(data, 20, bits=4)

        assert len(decoded) == 20

        # MSE check (same bound derivation as test_total_mse)
        v_norm_sq = sum(o[0]**2 + o[1]**2 + o[3]**2 for o in opinions)
        k_minus_1 = 2**4 - 1
        bound = 9.0 * v_norm_sq / (20 * k_minus_1**2) + 1e-6
        actual_mse = sum(
            (orig[0]-dec[0])**2 + (orig[1]-dec[1])**2 + (orig[3]-dec[3])**2
            for orig, dec in zip(opinions, decoded)
        ) / 20
        assert actual_mse <= bound, \
            f"Auto-seed MSE {actual_mse:.6e} > bound {bound:.6e}"


class TestBatchEdgeCases:
    """Phase 5b: edge cases and input validation."""

    def test_minimum_batch_size(self):
        """N=1: single opinion batch (D=4 after padding 3→4)."""
        opinions = [(0.3, 0.2, 0.5, 0.7)]
        data = encode_batch(opinions, bits=4, seed=42)

        d = _next_pow2(3)  # = 4
        assert len(data) == 6 + math.ceil(d * 4 / 8)

        decoded = decode_batch(data, 1, bits=4)
        b, d_val, u, a = decoded[0]
        assert abs(b + d_val + u - 1.0) < 1e-12
        assert 0.0 <= a <= 1.0

    def test_exact_power_of_2_opinions(self):
        """N where 3N is already a power of 2: no padding waste.

        3N = 2^k → N = 2^k/3. Doesn't happen for integer N,
        but 3*16 = 48, next pow2 = 64. Test N=16.
        """
        opinions = _make_opinions(16)
        data = encode_batch(opinions, bits=3, seed=42)
        decoded = decode_batch(data, 16, bits=3)

        assert len(decoded) == 16
        for b, d, u, a in decoded:
            assert abs(b + d + u - 1.0) < 1e-12

    def test_extreme_opinions(self):
        """Opinions at simplex vertices and edges."""
        opinions = [
            (1.0, 0.0, 0.0, 0.0),  # pure belief
            (0.0, 1.0, 0.0, 0.5),  # pure disbelief
            (0.0, 0.0, 1.0, 1.0),  # pure uncertainty
            (0.5, 0.5, 0.0, 0.3),  # edge: no uncertainty
            (0.0, 0.0, 1.0, 0.0),  # vacuous, a=0
            (0.0, 0.0, 1.0, 1.0),  # vacuous, a=1
            (1/3, 1/3, 1/3, 0.5),  # uniform
            (0.0, 0.5, 0.5, 0.5),  # edge: no belief
        ]
        data = encode_batch(opinions, bits=4, seed=42)
        decoded = decode_batch(data, 8, bits=4)

        for i, (b, d, u, a) in enumerate(decoded):
            assert abs(b + d + u - 1.0) < 1e-12, \
                f"extreme opinion {i}: b+d+u = {b+d+u}"
            assert b >= -1e-12 and d >= -1e-12 and u >= -1e-12
            assert -1e-12 <= a <= 1.0 + 1e-12

    def test_empty_opinions_rejected(self):
        """Empty opinion list should be rejected."""
        with pytest.raises(ValueError):
            encode_batch([], bits=3, seed=42)


# =========================================================================
# Phase 6: Distortion-rate factor ρ verification
#
# Theorem 15 claims ρ_batch ≈ 2.7 for N ≥ 32 with Lloyd-Max.
# This is the TurboQuant-matching claim. It MUST be empirically verified.
#
# ρ = (actual MSE) / (information-theoretic optimal MSE)
# where the optimum for D-dimensional unit-variance Gaussian is:
#   MSE_opt = D * (1/(12 * 4^(b/D)))  [Zador bound]
# but for our per-coordinate formulation:
#   MSE_opt_per_coord = 1/(12 * 2^(2b))  [1D Zador]
#
# More precisely, ρ = MSE_actual / MSE_zador where
#   MSE_zador = (1/12) * 2^(-2b)  (1D optimal scalar quantizer)
# =========================================================================


class TestDistortionRateFactor:
    """Phase 6: empirical verification of ρ ≈ 2.7 for Lloyd-Max.

    The claim in Theorem 15 is that batch encoding matches
    TurboQuant's asymptotic factor. This test verifies it
    empirically with actual encode/decode cycles.
    """

    @pytest.mark.parametrize("bits", [2, 3, 4, 5])
    def test_rho_lloyd_max_at_codebook_level(self, bits):
        """ρ for Lloyd-Max codebook on N(0.5, 1/36): 1.0 < ρ < 3.5.

        Theorem 15's ρ ≈ 2.7 claim is about the scalar quantizer's
        performance on the post-RHT distribution, NOT the end-to-end
        pipeline MSE (which is scaled by norm²·C²).

        The correct measurement: draw samples from the post-RHT
        distribution N(0.5, 1/36), quantize with Lloyd-Max, compute
        per-sample MSE, divide by Shannon rate-distortion bound.

        Shannon R-D for Gaussian: D(R) = σ² × 2^(−2b)
        where σ² = 1/36 is the variance of N(0.5, 1/6).
        This is the information-theoretic minimum MSE for any b-bit
        encoder/decoder pair on a Gaussian source.

        The classic result for Lloyd-Max on Gaussian is ρ ≈ πe/3 ≈ 2.84.
        Our truncated Gaussian should be slightly better (less tail waste).
        """
        import random
        random.seed(42)

        boundaries, centroids = lloyd_max_codebook(bits)
        n_samples = 50000
        sigma = 1.0 / 6.0
        sigma_sq = sigma ** 2  # 1/36

        mse = 0.0
        for _ in range(n_samples):
            x = max(0.0, min(1.0, random.gauss(0.5, sigma)))
            code = quantize_lloyd_max(x, boundaries)
            recon = dequantize_lloyd_max(code, centroids)
            mse += (x - recon) ** 2
        mse /= n_samples

        # Shannon rate-distortion function for Gaussian:
        # D(R) = σ² × 2^(-2b)
        mse_shannon = sigma_sq * 2.0 ** (-2 * bits)
        rho = mse / mse_shannon

        assert rho > 1.0, f"rho={rho:.3f} < 1.0 (violates information theory)"
        assert rho < 3.5, f"bits={bits}: rho={rho:.3f} >= 3.5"

    def test_rho_lloyd_max_less_than_uniform(self):
        """Lloyd-Max ρ < uniform ρ at the codebook level.

        This is the empirical evidence for Pillar 3: Lloyd-Max
        achieves a better distortion-rate factor than uniform.
        """
        import random

        for bits in [3, 4]:
            random.seed(42)
            n_samples = 50000
            sigma = 1.0 / 6.0

            boundaries_lm, centroids_lm = lloyd_max_codebook(bits)
            levels = 2 ** bits - 1

            mse_lm = 0.0
            mse_u = 0.0
            for _ in range(n_samples):
                x = max(0.0, min(1.0, random.gauss(0.5, sigma)))
                # Lloyd-Max
                code_lm = quantize_lloyd_max(x, boundaries_lm)
                recon_lm = dequantize_lloyd_max(code_lm, centroids_lm)
                mse_lm += (x - recon_lm) ** 2
                # Uniform
                code_u = max(0, min(levels, round(x * levels)))
                recon_u = code_u / levels
                mse_u += (x - recon_u) ** 2
            mse_lm /= n_samples
            mse_u /= n_samples

            assert mse_lm < mse_u, \
                f"bits={bits}: Lloyd-Max MSE {mse_lm:.6e} >= uniform {mse_u:.6e}"

    @pytest.mark.parametrize("n_opinions", [32, 50, 100])
    def test_end_to_end_lloyd_max_beats_uniform(self, n_opinions):
        """End-to-end: Lloyd-Max pipeline MSE ≤ uniform pipeline MSE.

        Full pipeline test (not codebook-level). This verifies the
        advantage survives through the entire encode/decode cycle.
        """
        bits = 3
        n_trials = 10

        mse_lm_total = 0.0
        mse_u_total = 0.0

        for trial in range(n_trials):
            opinions = _make_opinions(n_opinions, seed=trial)

            data_lm = encode_batch(opinions, bits=bits, seed=trial, quantizer='lloyd_max')
            decoded_lm = decode_batch(data_lm, n_opinions, bits=bits, quantizer='lloyd_max')

            data_u = encode_batch(opinions, bits=bits, seed=trial, quantizer='uniform')
            decoded_u = decode_batch(data_u, n_opinions, bits=bits, quantizer='uniform')

            mse_lm_total += sum(
                sum((o[j]-d[j])**2 for j in range(4))
                for o, d in zip(opinions, decoded_lm)
            ) / (4 * n_opinions)

            mse_u_total += sum(
                sum((o[j]-d[j])**2 for j in range(4))
                for o, d in zip(opinions, decoded_u)
            ) / (4 * n_opinions)

        assert mse_lm_total < mse_u_total, \
            f"N={n_opinions}: Lloyd-Max avg MSE {mse_lm_total/n_trials:.6e} " \
            f">= uniform {mse_u_total/n_trials:.6e}"


# =========================================================================
# Phase 7: Shannon analysis for batch compression
#
# Pure functions computing bit-level efficiency metrics:
#   - Wire cost: total bits on the wire
#   - Information bits: useful payload (3N×b)
#   - Overhead: seed (32) + norm_q (16) = 48 bits fixed
#   - Padding waste: (D − 3N) × b bits from power-of-2 padding
#   - Efficiency: information_bits / wire_bits
# =========================================================================


class TestBatchShannonAnalysis:
    """Phase 7: Shannon analysis functions for batch compression."""

    # --- Wire cost ---

    @pytest.mark.parametrize("n_opinions,bits,expected_bits", [
        # N=8: 3N=24, D=32, wire = 48 + 32*3 = 144 bits = 18 bytes
        # total wire bits = (6 + ceil(32*3/8)) * 8 = (6+12)*8 = 144
        (8, 3, (6 + 12) * 8),
        # N=32: 3N=96, D=128, wire = 48 + 128*3 = 432 bits
        (32, 3, (6 + 48) * 8),
        # N=10: 3N=30, D=32, wire = 48 + 32*4 = 176 bits
        (10, 4, (6 + 16) * 8),
    ])
    def test_wire_bits(self, n_opinions, bits, expected_bits):
        """Wire bits = (6 + ceil(D×b/8)) × 8."""
        result = batch_wire_bits(n_opinions, bits)
        assert result == expected_bits, \
            f"N={n_opinions}, b={bits}: got {result}, expected {expected_bits}"

    # --- Information bits ---

    @pytest.mark.parametrize("n_opinions,bits", [
        (8, 3), (10, 4), (32, 3), (50, 3), (100, 4),
    ])
    def test_information_bits(self, n_opinions, bits):
        """Information bits = 3N × b (useful payload)."""
        result = batch_information_bits(n_opinions, bits)
        assert result == 3 * n_opinions * bits

    # --- Overhead ---

    def test_overhead_is_48_bits(self):
        """Fixed overhead = seed(32) + norm_q(16) = 48 bits.

        This is constant regardless of N or b.
        """
        for n in [8, 32, 100]:
            for b in [3, 4, 5]:
                assert batch_overhead_bits(n, b) == 48

    # --- Padding waste ---

    @pytest.mark.parametrize("n_opinions,bits,expected_waste", [
        # N=8: 3N=24, D=32, waste = (32-24)*3 = 24
        (8, 3, (32 - 24) * 3),
        # N=32: 3N=96, D=128, waste = (128-96)*3 = 96
        (32, 3, (128 - 96) * 3),
        # N=10: 3N=30, D=32, waste = (32-30)*4 = 8
        (10, 4, (32 - 30) * 4),
        # N=16: 3N=48, D=64, waste = (64-48)*3 = 48
        (16, 3, (64 - 48) * 3),
    ])
    def test_padding_waste_bits(self, n_opinions, bits, expected_waste):
        """Padding waste = (D − 3N) × b bits."""
        result = batch_padding_waste_bits(n_opinions, bits)
        assert result == expected_waste

    # --- Efficiency ---

    @pytest.mark.parametrize("n_opinions,bits", [
        (8, 3), (10, 4), (32, 3), (50, 3), (100, 3),
    ])
    def test_efficiency_in_valid_range(self, n_opinions, bits):
        """Efficiency is in (0, 1]."""
        eff = batch_efficiency(n_opinions, bits)
        assert 0.0 < eff <= 1.0, f"N={n_opinions}, b={bits}: eff={eff}"

    def test_efficiency_increases_at_favorable_n(self):
        """Efficiency increases with N when padding waste is low.

        Efficiency follows a SAWTOOTH pattern: it jumps up right after
        a power-of-2 boundary (minimal padding) and decays as N grows
        toward the next boundary (increasing padding waste). So
        monotonicity only holds for N values that don't cross a bad
        padding boundary.

        We test N values where 3N is close to a power of 2 (low waste).
        """
        bits = 3
        # These N values have 3N close to the next power of 2:
        # N=8: 3N=24, D=32, waste=8  (25%)
        # N=11: 3N=33, D=64, waste=31 (48%) -- bad, skip
        # N=21: 3N=63, D=64, waste=1  (1.6%) -- great
        # N=42: 3N=126, D=128, waste=2 (1.6%) -- great
        # N=85: 3N=255, D=256, waste=1 (0.4%) -- great
        prev_eff = 0.0
        for n in [8, 21, 42, 85]:
            eff = batch_efficiency(n, bits)
            assert eff > prev_eff, \
                f"N={n}: eff={eff:.4f} <= previous {prev_eff:.4f}"
            prev_eff = eff

    def test_efficiency_sawtooth_pattern(self):
        """Efficiency drops when 3N crosses a power-of-2 boundary.

        This documents the sawtooth behavior honestly. N=10 (3N=30, D=32)
        has less padding than N=11 (3N=33, D=64), so N=10 is more efficient.
        """
        bits = 3
        eff_10 = batch_efficiency(10, bits)  # 3N=30, D=32, waste=2
        eff_11 = batch_efficiency(11, bits)  # 3N=33, D=64, waste=31
        assert eff_10 > eff_11, \
            f"N=10 eff={eff_10:.4f} should exceed N=11 eff={eff_11:.4f} (sawtooth)"

    def test_efficiency_identity(self):
        """Efficiency = information_bits / wire_bits."""
        for n in [8, 32, 50]:
            for b in [3, 4]:
                eff = batch_efficiency(n, b)
                expected = batch_information_bits(n, b) / batch_wire_bits(n, b)
                assert abs(eff - expected) < 1e-12

    # --- Batch vs individual comparison ---

    @pytest.mark.parametrize("n_opinions", [8, 16, 32, 50, 100])
    def test_batch_beats_individual_for_n_ge_8(self, n_opinions):
        """Batch encoding uses fewer bytes than individual for N ≥ 8 at 3-bit.

        Individual cost: 3N bytes (8-bit per component, û-elision).
        Batch cost: 6 + ceil(D×b/8) bytes.
        """
        bits = 3
        individual_bytes = 3 * n_opinions
        d = _next_pow2(3 * n_opinions)
        batch_bytes = 6 + math.ceil(d * bits / 8)
        assert batch_bytes < individual_bytes, \
            f"N={n_opinions}: batch {batch_bytes} >= individual {individual_bytes}"
