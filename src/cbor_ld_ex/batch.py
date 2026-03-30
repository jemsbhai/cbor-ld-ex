"""
Batch compression for CBOR-LD-ex opinion vectors.

Implements FORMAL_MODEL.md §4.8 (PolarQuant-inspired batch compression):
  - xoshiro128++ PRNG (Blackman & Vigna 2019) — protocol-mandated determinism
  - Fast Walsh-Hadamard Transform — O(D log D) randomized rotation
  - Per-coordinate scalar quantization (uniform + Lloyd-Max)
  - L2 simplex projection + base rate clamping — constraint restoration
  - Batch encode/decode pipeline

The PRNG and RHT are protocol-critical: any conformant implementation
MUST produce bit-identical output from the same seed. The algorithms
are specified to the level of individual bit operations.

References:
  Blackman, D. & Vigna, S. (2019). Scrambled Linear PRNGs.
    https://prng.di.unimi.it/xoshiro128plusplus.c
  Ailon, N. & Chazelle, B. (2009). The Fast Johnson-Lindenstrauss Transform.
    SIAM J. Computing.
  Duchi, J. et al. (2008). Efficient Projections onto the ℓ1-Ball.
    ICML 2008.
  Han, I. et al. (2025). PolarQuant. AISTATS 2026.
  Zandieh, A. et al. (2025). TurboQuant. ICLR 2026.
"""

# Mask for 32-bit unsigned arithmetic
_U32_MASK = 0xFFFFFFFF
# Mask for 64-bit unsigned arithmetic
_U64_MASK = 0xFFFFFFFFFFFFFFFF


# =========================================================================
# SplitMix64 — seeder for xoshiro128++
#
# Canonical reference: https://prng.di.unimi.it/splitmix64.c
# The authors recommend using SplitMix64 to initialize xoshiro state
# from a single seed, to avoid correlation on similar seeds.
#
# Constants:
#   additive = 0x9E3779B97F4A7C15 (golden ratio × 2^64, truncated)
#   mix1 = 0xBF58476D1CE4E5B9
#   mix2 = 0x94D049BB133111EB
# =========================================================================

def splitmix64(state: int) -> tuple[int, int]:
    """One step of SplitMix64.

    Args:
        state: Current 64-bit state.

    Returns:
        (new_state, output): both uint64.
    """
    state = (state + 0x9E3779B97F4A7C15) & _U64_MASK
    z = state
    z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & _U64_MASK
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & _U64_MASK
    z = (z ^ (z >> 31)) & _U64_MASK
    return (state, z)


# =========================================================================
# xoshiro128++ 1.0 — protocol-mandated PRNG
#
# Canonical reference: https://prng.di.unimi.it/xoshiro128plusplus.c
# Written by David Blackman and Sebastiano Vigna (2019), public domain.
#
# 32-bit output, 128-bit state, period 2^128 − 1.
# Passes all known statistical tests (BigCrush, PractRand).
#
# The state MUST NOT be all zeros.
# =========================================================================

def _rotl32(x: int, k: int) -> int:
    """32-bit left rotation."""
    return ((x << k) | (x >> (32 - k))) & _U32_MASK


class Xoshiro128PlusPlus:
    """xoshiro128++ 1.0 PRNG (Blackman & Vigna 2019).

    Protocol-mandated for CBOR-LD-ex batch compression (§4.8).
    Any conformant implementation MUST produce identical output
    from the same seed.

    Seeding from uint32:
      1. Zero-extend seed to uint64
      2. Run SplitMix64 twice to get two uint64 values
      3. Split each uint64 into two uint32 (little-endian)
      4. Fill state[0..3]
    """

    __slots__ = ('_s0', '_s1', '_s2', '_s3')

    def __init__(self, seed: int) -> None:
        """Initialize from a uint32 seed via SplitMix64.

        Args:
            seed: 32-bit unsigned integer seed.
        """
        sm_state = seed & _U64_MASK  # zero-extend to 64 bits
        sm_state, r0 = splitmix64(sm_state)
        sm_state, r1 = splitmix64(sm_state)
        self._s0 = r0 & _U32_MASK
        self._s1 = (r0 >> 32) & _U32_MASK
        self._s2 = r1 & _U32_MASK
        self._s3 = (r1 >> 32) & _U32_MASK

    @classmethod
    def from_state(
        cls, s0: int, s1: int, s2: int, s3: int
    ) -> "Xoshiro128PlusPlus":
        """Initialize from explicit 4×uint32 state (bypasses SplitMix64).

        Args:
            s0, s1, s2, s3: 32-bit unsigned state words.

        Raises:
            ValueError: If all state words are zero.
        """
        if s0 == 0 and s1 == 0 and s2 == 0 and s3 == 0:
            raise ValueError(
                "xoshiro128++ state must not be all zero "
                "(degenerate period)"
            )
        obj = cls.__new__(cls)
        obj._s0 = s0 & _U32_MASK
        obj._s1 = s1 & _U32_MASK
        obj._s2 = s2 & _U32_MASK
        obj._s3 = s3 & _U32_MASK
        return obj

    @property
    def state(self) -> tuple[int, int, int, int]:
        """Current 4×uint32 state (read-only)."""
        return (self._s0, self._s1, self._s2, self._s3)

    def next(self) -> int:
        """Generate the next 32-bit pseudo-random number.

        This is the EXACT algorithm from the canonical C implementation.
        Every operation is annotated for reviewability.

        Returns:
            uint32 in [0, 2^32 − 1].
        """
        # result = rotl(s[0] + s[3], 7) + s[0]
        result = (_rotl32((self._s0 + self._s3) & _U32_MASK, 7) + self._s0) & _U32_MASK

        # t = s[1] << 9
        t = (self._s1 << 9) & _U32_MASK

        # State update (XOR cascade)
        self._s2 ^= self._s0
        self._s3 ^= self._s1
        self._s1 ^= self._s2
        self._s0 ^= self._s3

        self._s2 = (self._s2 ^ t) & _U32_MASK

        # s[3] = rotl(s[3], 11)
        self._s3 = _rotl32(self._s3, 11)

        return result

    def next_bits(self, n: int) -> int:
        """Extract the top n bits from the next output.

        For n < 32, returns the most significant n bits.
        The top bits have better statistical quality than the bottom bits.

        Args:
            n: Number of bits to extract (1 ≤ n ≤ 32).

        Returns:
            uint in [0, 2^n − 1].
        """
        return self.next() >> (32 - n)
