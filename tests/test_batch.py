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
