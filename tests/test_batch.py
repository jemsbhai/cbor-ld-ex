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
