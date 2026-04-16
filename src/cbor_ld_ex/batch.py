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
# Protocol-mandated bit-width range — §4.8
#
# The batch compression protocol restricts per-coordinate quantization
# bit-width to b ∈ {2, 3, 4, 5, 6, 7, 8}.
#
#   Lower bound (b ≥ 2): b = 1 is a sign-only regime, not a
#   rate-distortion regime. Theorem 15's analytical MSE bound is
#   defined for b ≥ 2, and the reference ρ values in the paper are
#   measured for b ∈ {2, 3, 4, 5}.
#
#   Upper bound (b ≤ 8): at b = 8 the Lloyd-Max codebook MSE is already
#   well below the pipeline's norm_q floor (~1/131070), so additional
#   bits consume wire bytes without meaningful MSE reduction.
#
# This constraint is enforced at every public entry point of the batch
# pipeline (encode_batch, decode_batch, lloyd_max_codebook) via
# _validate_bits. Violations raise ValueError immediately, before any
# wire-format processing — see TestBitWidthRangeValidation.
# =========================================================================

_MIN_BITS = 2
_MAX_BITS = 8


def _validate_bits(bits: object) -> None:
    """Validate bit-width against the protocol range [2, 8].

    Called at the entry of every public function that accepts a `bits`
    parameter. Fails fast with ValueError — no silent acceptance of
    out-of-range values.

    Args:
        bits: Value to validate. Must be an int in [2, 8].

    Raises:
        ValueError: If bits is not an int, is a bool, or is outside [2, 8].
            The error message cites the valid range.
    """
    # Reject bool first: isinstance(True, int) is True in Python, but
    # using True/False as a bit-width is almost certainly a bug.
    if isinstance(bits, bool):
        raise ValueError(
            f"bits must be an int in [{_MIN_BITS}, {_MAX_BITS}], "
            f"got bool: {bits!r}"
        )
    if not isinstance(bits, int):
        raise ValueError(
            f"bits must be an int in [{_MIN_BITS}, {_MAX_BITS}], "
            f"got {type(bits).__name__}: {bits!r}"
        )
    if bits < _MIN_BITS or bits > _MAX_BITS:
        raise ValueError(
            f"bits must be in [{_MIN_BITS}, {_MAX_BITS}], got {bits}"
        )


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


# =========================================================================
# Fast Walsh-Hadamard Transform (FWHT) — §4.8.3
#
# The normalized Walsh-Hadamard matrix of order D = 2^k:
#   H_D[i,j] = (1/√D) × (-1)^<i,j>
# where <i,j> is the bitwise dot product of the binary representations
# of i and j.
#
# Properties:
#   - Self-inverse: H · H · x = x (when normalized by 1/√D)
#   - Orthogonal: preserves L2 norm (‖H·x‖₂ = ‖x‖₂)
#   - O(D log D) via butterfly decomposition
#   - No matrix storage — the transform is implicit
#
# The butterfly algorithm computes the unnormalized transform in-place,
# then scales by 1/√D. For D=256 (N=50 sensors): 2048 add/sub + 256 mul.
# =========================================================================

import bisect as _bisect
import math as _math
import struct as _struct


def _is_power_of_2(n: int) -> bool:
    """Check if n is a positive power of 2."""
    return n > 0 and (n & (n - 1)) == 0


def fwht(x: list[float]) -> list[float]:
    """Normalized Fast Walsh-Hadamard Transform.

    Computes y = H_D · x where H_D is the normalized Hadamard matrix
    of order D = len(x). D must be a power of 2.

    The transform is self-inverse: fwht(fwht(x)) = x.

    Algorithm: in-place butterfly with O(D log D) additions/subtractions,
    followed by scaling by 1/√D.

    Args:
        x: Input vector of length D (must be a power of 2).

    Returns:
        Transformed vector of length D.

    Raises:
        ValueError: If len(x) is 0 or not a power of 2.
    """
    d = len(x)
    if d == 0:
        raise ValueError("Input must be non-empty")
    if not _is_power_of_2(d):
        raise ValueError(
            f"Input length must be a power of 2, got {d}"
        )

    # Work on a copy to avoid mutating input
    y = list(x)

    # Butterfly: log2(D) stages
    half = 1
    while half < d:
        step = half * 2
        for j in range(0, d, step):
            for i in range(half):
                a = y[j + i]
                b = y[j + i + half]
                y[j + i] = a + b
                y[j + i + half] = a - b
        half = step

    # Normalize by 1/√D
    norm = 1.0 / _math.sqrt(d)
    for i in range(d):
        y[i] *= norm

    return y


def fwht_inverse(y: list[float]) -> list[float]:
    """Inverse normalized Fast Walsh-Hadamard Transform.

    Since the normalized FWHT is self-inverse (H = H⁻¹),
    this is identical to the forward transform.

    Provided as a separate function for API clarity — callers
    can express intent ("I'm inverting") without knowing the
    self-inverse property.

    Args:
        y: Transformed vector of length D (must be a power of 2).

    Returns:
        Reconstructed vector of length D.
    """
    return fwht(y)


# =========================================================================
# L2 Simplex Projection — §4.8.5 (Duchi et al. 2008)
#
# Projects a vector onto the probability simplex:
#   Δ = {x ∈ ℝ^k : x_i ≥ 0, ∑x_i = 1}
#
# This is the L2-nearest point on the simplex to the input vector.
# Used for constraint restoration after inverse RHT + dequantization.
#
# Algorithm (Duchi et al. 2008, "Efficient Projections onto the
# ℓ1-Ball for Learning in High Dimensions", ICML 2008):
#   1. Sort components descending
#   2. Find threshold θ via cumulative sum
#   3. Shift and clamp: x_proj_i = max(0, x_i - θ)
#
# Complexity: O(k log k) from the sort. For k=3 (opinion triples),
# this is negligible.
#
# Properties:
#   - Output is on the simplex: ∑x_proj_i = 1, x_proj_i ≥ 0
#   - L2-nearest point (by KKT conditions)
#   - Idempotent: project(project(x)) = project(x)
#   - Does not amplify error (Theorem 14): ‖proj - true‖ ≤ ‖noisy - true‖
# =========================================================================


def simplex_project(x: list[float]) -> list[float]:
    """Project a vector onto the probability simplex (Duchi et al. 2008).

    Finds the L2-nearest point on {v : v_i ≥ 0, ∑v_i = 1}.

    Args:
        x: Input vector of length k (≥ 1).

    Returns:
        Projected vector of length k on the probability simplex.

    Raises:
        ValueError: If input is empty.
    """
    k = len(x)
    if k == 0:
        raise ValueError("Input must be non-empty")
    if k == 1:
        return [1.0]

    # Step 1: Sort descending
    mu = sorted(x, reverse=True)

    # Step 2: Find the threshold θ
    # θ = (cumsum_j - 1) / j for the largest j where μ[j] > θ
    cumsum = 0.0
    theta = 0.0
    for j in range(k):
        cumsum += mu[j]
        t = (cumsum - 1.0) / (j + 1)
        if j == k - 1 or mu[j + 1] <= t:
            theta = t
            break

    # Step 3: Shift and clamp
    return [max(0.0, xi - theta) for xi in x]


# =========================================================================
# Lloyd-Max Optimal Scalar Quantizer — §4.8 Phase 4
#
# The Lloyd-Max algorithm iteratively minimizes MSE for a known
# distribution by alternating two steps:
#   1. Nearest-neighbor: each boundary = midpoint of adjacent centroids
#   2. Centroid: each centroid = conditional mean within its cell
#
# Two distribution modes:
#   - Gaussian asymptotic (dim=None): N(0.5, 1/36) truncated to [0,1]
#     Valid for D ≥ ~64 by concentration of measure.
#   - Beta-exact (dim=D): exact sphere marginal after affine mapping.
#     Accurate for all D, required when D < ~64.
#
# Both distributions are symmetric about 0.5 (the sphere marginal
# is symmetric because Beta(α,α) is symmetric). This symmetry is
# preserved in the converged codebook.
#
# Codebook is tiny: 2^b centroids + 2^b-1 boundaries per configuration.
# For 3-bit: 15 floats = 60 bytes. Computed once, cached.
#
# References:
#   Lloyd, S.P. (1982). Least Squares Quantization in PCM.
#   Max, J. (1960). Quantizing for Minimum Distortion.
# =========================================================================

# Cache: (bits, dim) -> (boundaries, centroids)
_codebook_cache: dict[tuple[int, int | None], tuple[list[float], list[float]]] = {}


def lloyd_max_codebook(
    bits: int,
    dim: int | None = None,
    iterations: int = 300,
) -> tuple[list[float], list[float]]:
    """Compute Lloyd-Max optimal scalar quantizer for post-RHT distribution.

    Two modes:
      - Gaussian asymptotic (dim=None): N(0.5, 1/36) truncated to [0,1].
        Accurate for D ≥ ~64 where concentration of measure makes the
        sphere marginal approximately Gaussian.
      - Beta-exact (dim=D): exact marginal distribution of a single
        coordinate of a uniform point on S^(D-1), mapped to [0,1] via
        the protocol's affine transform x = t/C + 0.5, C = 6/√D.

    The codebook is cached: repeated calls with the same (bits, dim)
    return the same objects without recomputation.

    Args:
        bits: Number of quantization bits. Must be an int in [2, 8]
            (protocol-mandated range, enforced at runtime).
        dim: Padded dimension D for Beta-exact mode, or None for Gaussian.
        iterations: Maximum Lloyd-Max iterations (default 300).

    Returns:
        (boundaries, centroids): boundaries is a list of 2^b − 1
        decision boundaries (sorted ascending), centroids is a list
        of 2^b reconstruction levels (sorted ascending).

    Raises:
        ValueError: If bits is not an int in [2, 8].
        ImportError: If scipy is not available.
    """
    _validate_bits(bits)
    cache_key = (bits, dim)
    if cache_key in _codebook_cache:
        return _codebook_cache[cache_key]

    # Lazy import — scipy is a compute dependency, not a deploy dependency.
    # On constrained devices, ship pre-computed codebooks.
    try:
        from scipy.stats import truncnorm as _truncnorm
        from scipy.stats import beta as _beta_dist
        from scipy.integrate import quad as _quad
    except ImportError:
        raise ImportError(
            "scipy is required for Lloyd-Max codebook computation. "
            "Install it with: pip install scipy"
        )

    k = 2 ** bits  # number of reconstruction levels

    # ---- Build target distribution (PDF and CDF on [0, 1]) ----

    if dim is None:
        # Gaussian asymptotic: N(0.5, 1/36) truncated to [0, 1]
        mu, sigma = 0.5, 1.0 / 6.0
        a_clip = (0.0 - mu) / sigma   # = -3.0
        b_clip = (1.0 - mu) / sigma   # = +3.0
        dist = _truncnorm(a_clip, b_clip, loc=mu, scale=sigma)
        pdf = dist.pdf
        cdf = dist.cdf
    else:
        # Beta-exact: sphere marginal mapped to [0, 1]
        #
        # A single coordinate t of a uniform point on S^(D-1) has
        # PDF ∝ (1 − t²)^((D−3)/2) on [−1, 1].
        # Equivalently, u = (t+1)/2 ~ Beta(α, α) with α = (D−1)/2.
        #
        # After the protocol's affine mapping x = t/C + 0.5:
        #   u(x) = C·(x − 0.5)/2 + 0.5
        #   du/dx = C/2
        #   f_X(x) = f_U(u(x)) · (C/2) / P(X ∈ [0,1])
        #
        # We use scipy's Beta CDF directly (no numerical integration
        # for CDF), and numerical integration only for conditional means.
        c_val = 6.0 / _math.sqrt(float(dim))
        alpha_param = (dim - 1) / 2.0
        beta_rv = _beta_dist(alpha_param, alpha_param)

        def _x_to_u(x: float) -> float:
            """Map x ∈ [0,1] to u ∈ [0,1] (Beta variable)."""
            return max(0.0, min(1.0, c_val * (x - 0.5) / 2.0 + 0.5))

        u_lo = _x_to_u(0.0)
        u_hi = _x_to_u(1.0)
        p_total = beta_rv.cdf(u_hi) - beta_rv.cdf(u_lo)

        def cdf(x: float) -> float:
            if x <= 0.0:
                return 0.0
            if x >= 1.0:
                return 1.0
            u = _x_to_u(x)
            return (beta_rv.cdf(u) - beta_rv.cdf(u_lo)) / p_total

        def pdf(x: float) -> float:
            u = c_val * (x - 0.5) / 2.0 + 0.5
            if u <= 0.0 or u >= 1.0:
                return 0.0
            return beta_rv.pdf(u) * (c_val / 2.0) / p_total

    # ---- Lloyd-Max iteration ----

    # Initialize centroids uniformly in [0, 1]
    centroids = [(i + 0.5) / k for i in range(k)]

    for _ in range(iterations):
        # Step 1: Boundaries = midpoints of adjacent centroids
        boundaries = [
            (centroids[i] + centroids[i + 1]) / 2.0
            for i in range(k - 1)
        ]

        # Step 2: Centroids = conditional means within cells
        edges = [0.0] + boundaries + [1.0]
        new_centroids = []
        for i in range(k):
            lo, hi = edges[i], edges[i + 1]
            p_cell = cdf(hi) - cdf(lo)
            if p_cell < 1e-15:
                # Negligible probability — keep old centroid
                new_centroids.append(centroids[i])
                continue
            # E[X | lo ≤ X ≤ hi] = ∫ x·f(x) dx / P(cell)
            numerator, _ = _quad(lambda x: x * pdf(x), lo, hi)
            new_centroids.append(numerator / p_cell)

        centroids = new_centroids

    # Final boundaries from converged centroids
    boundaries = [
        (centroids[i] + centroids[i + 1]) / 2.0
        for i in range(k - 1)
    ]

    _codebook_cache[cache_key] = (boundaries, centroids)
    return boundaries, centroids


def quantize_lloyd_max(x: float, boundaries: list[float]) -> int:
    """Quantize a scalar value using Lloyd-Max decision boundaries.

    Uses binary search for O(log K) lookup.

    Args:
        x: Value to quantize.
        boundaries: Sorted decision boundaries (length K−1).

    Returns:
        Code index in [0, K−1] where K = len(boundaries) + 1.
    """
    return _bisect.bisect_right(boundaries, x)


def dequantize_lloyd_max(code: int, centroids: list[float]) -> float:
    """Reconstruct a value from its Lloyd-Max quantization code.

    Args:
        code: Quantization code in [0, K−1].
        centroids: Reconstruction levels (length K).

    Returns:
        Centroid value for the given code.
    """
    return centroids[code]


# =========================================================================
# Randomized Hadamard Transform (RHT) — §4.8.3 (Definition 34)
#
# The RHT combines three operations:
#   1. Random permutation P (Fisher-Yates from PRNG)
#   2. Random sign flips σ ∈ {−1, +1}^D (from PRNG)
#   3. Normalized Walsh-Hadamard transform H_D
#
# Forward:  w = H_D · (σ ⊙ P(v))
# Inverse:  v = P⁻¹(σ ⊙ H_D · w)
#
# Bit consumption order from xoshiro128++ (Definition 34d):
#   First D bits for σ (MSB-first from each next() call),
#   then Fisher-Yates shuffle bits for P.
#
# Both encoder and decoder MUST generate identical σ and P from
# the same seed. This is protocol-critical.
# =========================================================================


def _generate_signs(rng: Xoshiro128PlusPlus, d: int) -> list[int]:
    """Generate a random sign vector from the PRNG.

    Consumes D bits from the PRNG, MSB-first from each next() call.
    Each sign is +1 or -1.

    Args:
        rng: Initialized xoshiro128++ PRNG.
        d: Dimension (number of signs to generate).

    Returns:
        List of D values, each +1 or -1.
    """
    signs = []
    bits_remaining = 0
    current_word = 0

    for _ in range(d):
        if bits_remaining == 0:
            current_word = rng.next()
            bits_remaining = 32
        # Extract MSB of remaining bits
        bit = (current_word >> (bits_remaining - 1)) & 1
        bits_remaining -= 1
        signs.append(2 * bit - 1)  # 0 → -1, 1 → +1

    return signs


def _generate_permutation(rng: Xoshiro128PlusPlus, d: int) -> list[int]:
    """Generate a random permutation via Fisher-Yates shuffle.

    For i from D-1 down to 1, pick j uniform in [0, i] using
    rejection sampling from next_bits(⌈log₂(i+1)⌉).

    Args:
        rng: Initialized xoshiro128++ PRNG (state continues from
             after sign generation).
        d: Dimension.

    Returns:
        Permutation as a list: perm[k] = index to read from.
    """
    perm = list(range(d))

    for i in range(d - 1, 0, -1):
        # Number of bits needed: ceil(log2(i+1))
        n_bits = (i + 1).bit_length()
        # Rejection sampling: draw until value <= i
        while True:
            j = rng.next_bits(n_bits)
            if j <= i:
                break
        perm[i], perm[j] = perm[j], perm[i]

    return perm


def _invert_permutation(perm: list[int]) -> list[int]:
    """Compute the inverse of a permutation.

    If perm maps position k to perm[k], the inverse maps
    perm[k] back to k.
    """
    inv = [0] * len(perm)
    for k, v in enumerate(perm):
        inv[v] = k
    return inv


def rht_forward(v: list[float], seed: int) -> list[float]:
    """Apply the Randomized Hadamard Transform (Definition 34).

    Computes w = H_D · (σ ⊙ P(v)) where:
      - P is a random permutation from xoshiro128++(seed)
      - σ is a random sign vector from xoshiro128++(seed)
      - H_D is the normalized Walsh-Hadamard matrix

    Bit consumption from PRNG: D bits for σ, then Fisher-Yates
    bits for P, all from the same seeded PRNG sequence.

    Args:
        v: Input vector of length D (must be a power of 2).
        seed: uint32 PRNG seed.

    Returns:
        Rotated vector w of length D.
    """
    d = len(v)
    rng = Xoshiro128PlusPlus(seed)

    # Consume bits in protocol order: signs first, then permutation
    signs = _generate_signs(rng, d)
    perm = _generate_permutation(rng, d)

    # Apply permutation: permuted[k] = v[perm[k]]
    permuted = [v[perm[k]] for k in range(d)]

    # Apply sign flips: signed[k] = signs[k] * permuted[k]
    signed = [signs[k] * permuted[k] for k in range(d)]

    # Apply normalized FWHT
    return fwht(signed)


def rht_inverse(w: list[float], seed: int) -> list[float]:
    """Apply the inverse Randomized Hadamard Transform.

    Computes v = P⁻¹(σ ⊙ H_D · w) where σ, P are regenerated
    from the same seed used in the forward transform.

    Args:
        w: Rotated vector of length D (must be a power of 2).
        seed: uint32 PRNG seed (same as used in rht_forward).

    Returns:
        Recovered vector v of length D.
    """
    d = len(w)
    rng = Xoshiro128PlusPlus(seed)

    # Regenerate signs and permutation in the same order
    signs = _generate_signs(rng, d)
    perm = _generate_permutation(rng, d)
    inv_perm = _invert_permutation(perm)

    # Inverse FWHT (self-inverse)
    h_w = fwht(w)

    # Undo sign flips
    unsigned = [signs[k] * h_w[k] for k in range(d)]

    # Undo permutation: v[perm[k]] = unsigned[k], i.e. v[j] = unsigned[inv_perm[j]]
    return [unsigned[inv_perm[k]] for k in range(d)]


# =========================================================================
# Batch Encode/Decode Pipeline — §4.8 Phase 5b
#
# Full pipeline: stack → pad → RHT → normalize → quantize → pack (encode)
#                unpack → dequantize → denormalize → inv RHT → unpad →
#                restore constraints (decode)
#
# Wire format (§4.8.4, Definition 35, spec v0.4.5):
#   [4 bytes]  seed_mode (uint32, big-endian):
#              Bit 31 (MSB): quantizer mode (0=uniform, 1=Lloyd-Max)
#              Bits 30–0: PRNG seed for RHT (31-bit range)
#   [2 bytes]  norm_q (uint16, big-endian)
#   [ceil(D × b / 8) bytes]  packed quantized coordinates (MSB-first)
#
# Total: 6 + ceil(D×b/8) bytes. No extra bytes.
# =========================================================================


def _f32(x: float) -> float:
    """Round-trip through IEEE 754 float32 for cross-platform determinism.

    The spec mandates float32 for C and norm computations (§4.8.4).
    """
    return _struct.unpack('>f', _struct.pack('>f', x))[0]


# Protocol-critical C = 6.0f / sqrtf(D) values, pinned to Rust reference.
#
# The spec mandates pure f32 arithmetic: C = 6.0f / sqrtf((float)(D)).
# Python's `_f32(6.0 / math.sqrt(D))` computes in f64 then rounds to f32,
# which diverges by 1 ULP at D=32, 128, 512, 2048 due to double rounding.
# This lookup table eliminates all platform-dependent arithmetic.
#
# Values computed via: `6.0f32 / (D as f32).sqrt()` in Rust 1.x.
# Verified against rustc output (IEEE 754 binary32, correctly rounded).
_C_LOOKUP: dict[int, float] = {
    d: _struct.unpack('>f', _struct.pack('>I', bits))[0]
    for d, bits in [
        (8,    0x4007c3b7),
        (16,   0x3fc00000),
        (32,   0x3f87c3b7),
        (64,   0x3f400000),
        (128,  0x3f07c3b7),
        (256,  0x3ec00000),
        (512,  0x3e87c3b7),
        (1024, 0x3e400000),
        (2048, 0x3e07c3b7),
        (4096, 0x3dc00000),
    ]
}


def _get_c_const(d: int) -> float:
    """Get the concentration constant C = 6.0f / sqrtf(D) for padded dimension D.

    Uses a lookup table of Rust-canonical float32 values to ensure
    bit-exact cross-platform determinism (§4.8.4). Falls back to
    _f32 computation for D values not in the table (D > 4096).
    """
    c = _C_LOOKUP.get(d)
    if c is not None:
        return c
    # Fallback for very large D (N > ~1365 opinions)
    return _f32(6.0 / _math.sqrt(float(d)))


def _next_power_of_2(n: int) -> int:
    """Smallest power of 2 >= n."""
    p = 1
    while p < n:
        p <<= 1
    return p


def _pack_codes(codes: list[int], bits: int) -> bytes:
    """Pack quantized codes into bytes, MSB-first.

    Each code is `bits` wide. Codes are written left-to-right into
    a byte stream, MSB-first. Trailing bits in the last byte are
    zero-padded.

    Args:
        codes: List of quantized codes, each in [0, 2^bits - 1].
        bits: Bits per code.

    Returns:
        Packed bytes of length ceil(len(codes) * bits / 8).
    """
    total_bits = len(codes) * bits
    n_bytes = (total_bits + 7) // 8
    buf = bytearray(n_bytes)

    bit_pos = 0  # current bit position in the output stream
    for code in codes:
        # Write `bits` bits of `code` starting at `bit_pos`
        for b in range(bits - 1, -1, -1):  # MSB first
            bit_val = (code >> b) & 1
            byte_idx = bit_pos >> 3
            bit_idx = 7 - (bit_pos & 7)  # MSB-first within each byte
            buf[byte_idx] |= bit_val << bit_idx
            bit_pos += 1

    return bytes(buf)


def _unpack_codes(data: bytes, n_codes: int, bits: int) -> list[int]:
    """Unpack quantized codes from bytes, MSB-first.

    Inverse of _pack_codes.

    Args:
        data: Packed byte data.
        n_codes: Number of codes to extract.
        bits: Bits per code.

    Returns:
        List of n_codes integer codes.
    """
    codes = []
    bit_pos = 0
    for _ in range(n_codes):
        code = 0
        for _ in range(bits):
            byte_idx = bit_pos >> 3
            bit_idx = 7 - (bit_pos & 7)
            code = (code << 1) | ((data[byte_idx] >> bit_idx) & 1)
            bit_pos += 1
        codes.append(code)
    return codes


def encode_batch(
    opinions: list[tuple[float, float, float, float]],
    bits: int,
    seed: int | None = None,
    quantizer: str = 'lloyd_max',
) -> bytes:
    """Encode a batch of SL opinions into compact wire format.

    Pipeline (Definition 34 + 35):
      1. Stack (b, d, a) from each opinion into v ∈ ℝ^(3N)
      2. Pad to D = 2^⌈log₂(3N)⌉ with zeros
      3. RHT: w = H_D · (σ ⊙ P(v_padded))
      4. Normalize: x_j = w_j / (norm × C) + 0.5, clamp to [0,1]
      5. Quantize each x_j at b bits
      6. Pack into wire format: seed_mode(4) + norm_q(2) + packed_coords

    Args:
        opinions: List of N opinions, each (b, d, u, a) with b+d+u=1.
        bits: Quantization bit-width per coordinate. Must be an int
            in [2, 8] (protocol-mandated range, enforced at runtime).
        seed: PRNG seed (31-bit, range [0, 2^31-1]), or None for random.
        quantizer: None (default) to auto-detect from wire mode flag,
            or 'lloyd_max'/'uniform' to validate against wire mode.
            Raises ValueError if explicit quantizer contradicts wire.

    Returns:
        Wire bytes of length 6 + ceil(D × bits / 8).

    Raises:
        ValueError: If bits is not an int in [2, 8], or if opinions is empty.
    """
    _validate_bits(bits)
    if not opinions:
        raise ValueError("opinions must be non-empty")

    n = len(opinions)

    # Generate seed if needed
    if seed is None:
        import os
        seed = _struct.unpack('>I', os.urandom(4))[0]
    seed = seed & 0x7FFFFFFF  # 31-bit seed (MSB reserved for mode flag)

    # Step 1: Stack free parameters (b, d, a) — u is derived on decode
    v = []
    for b_val, d_val, u_val, a_val in opinions:
        v.extend([b_val, d_val, a_val])

    # Step 2: Pad to power of 2
    d = _next_power_of_2(3 * n)
    v_padded = v + [0.0] * (d - len(v))

    # Compute L2 norm of the stacked vector (before RHT, but RHT preserves it)
    norm = _math.sqrt(sum(x * x for x in v_padded))

    # Step 3: RHT
    w = rht_forward(v_padded, seed)

    # Step 4: Normalize to [0, 1]
    c_const = _get_c_const(d)  # Rust-canonical float32 lookup

    if norm < 1e-30:
        # Degenerate case: all-zero input
        x_norm = [0.5] * d
    else:
        denom = norm * c_const
        x_norm = [max(0.0, min(1.0, w_j / denom + 0.5)) for w_j in w]

    # Step 5: Quantize
    if quantizer == 'uniform':
        levels = 2 ** bits - 1
        codes = [max(0, min(levels, round(x * levels))) for x in x_norm]
    elif quantizer == 'lloyd_max':
        boundaries, _ = lloyd_max_codebook(bits, dim=d)
        codes = [quantize_lloyd_max(x, boundaries) for x in x_norm]
    else:
        raise ValueError(f"Unknown quantizer: {quantizer!r}")

    # Step 6: Norm quantization (§4.8.4)
    norm_max = _f32(_math.sqrt(float(3 * n)))  # float32
    if norm_max < 1e-30:
        norm_q = 0
    else:
        norm_q = max(0, min(65535, round(norm / norm_max * 65535)))

    # Pack wire format (spec v0.4.5: seed MSB = quantizer mode flag)
    mode_bit = 1 if quantizer == 'lloyd_max' else 0
    seed_mode = (mode_bit << 31) | seed
    header = _struct.pack('>IH', seed_mode, norm_q)
    packed = _pack_codes(codes, bits)

    return header + packed


def decode_batch(
    data: bytes,
    n_opinions: int,
    bits: int,
    quantizer: str | None = None,
) -> list[tuple[float, float, float, float]]:
    """Decode a batch of SL opinions from wire format.

    Pipeline (inverse of encode_batch):
      1. Unpack seed_mode (extract mode flag + 31-bit seed), norm_q, quantized coordinates
      2. Dequantize each coordinate
      3. Denormalize: w_j = (x_j - 0.5) × norm × C
      4. Inverse RHT: v_padded = P⁻¹(σ ⊙ H_D · w)
      5. Unpad: take first 3N values
      6. Restore constraints (Definition 36):
         Step A: simplex_project([b̃, d̃, 1−b̃−d̃])
         Step B: clamp ã to [0, 1]

    Args:
        data: Wire bytes from encode_batch.
        n_opinions: Number of opinions N that were encoded.
        bits: Quantization bit-width per coordinate. Must be an int
            in [2, 8] (protocol-mandated range, enforced at runtime).
        quantizer: 'lloyd_max' (default) or 'uniform'.

    Returns:
        List of N opinions, each (b, d, u, a) with b+d+u=1, a ∈ [0,1].

    Raises:
        ValueError: If bits is not an int in [2, 8].
    """
    _validate_bits(bits)
    # Unpack header (spec v0.4.5: seed MSB = quantizer mode flag)
    seed_mode, norm_q = _struct.unpack('>IH', data[:6])
    wire_mode = seed_mode >> 31
    seed = seed_mode & 0x7FFFFFFF

    # Auto-detect or validate quantizer from wire mode flag
    wire_quantizer = 'lloyd_max' if wire_mode == 1 else 'uniform'
    if quantizer is None:
        quantizer = wire_quantizer
    elif quantizer != wire_quantizer:
        raise ValueError(
            f"Quantizer mode mismatch: wire indicates '{wire_quantizer}' "
            f"(mode={wire_mode}) but quantizer='{quantizer}' was requested"
        )

    # Compute padded dimension
    d = _next_power_of_2(3 * n_opinions)

    # Unpack quantized coordinates
    codes = _unpack_codes(data[6:], d, bits)

    # Dequantize
    if quantizer == 'uniform':
        levels = 2 ** bits - 1
        x_dequant = [code / levels for code in codes]
    elif quantizer == 'lloyd_max':
        _, centroids = lloyd_max_codebook(bits, dim=d)
        x_dequant = [dequantize_lloyd_max(code, centroids) for code in codes]
    else:
        raise ValueError(f"Unknown quantizer: {quantizer!r}")

    # Reconstruct norm
    norm_max = _f32(_math.sqrt(float(3 * n_opinions)))
    norm = float(norm_q) / 65535.0 * norm_max

    # Denormalize
    c_const = _get_c_const(d)  # Rust-canonical float32 lookup

    if norm < 1e-30:
        w = [0.0] * d
    else:
        denom = norm * c_const
        w = [(x - 0.5) * denom for x in x_dequant]

    # Inverse RHT
    v_padded = rht_inverse(w, seed)

    # Unpad and restore constraints (Definition 36)
    result = []
    for i in range(n_opinions):
        b_raw = v_padded[3 * i]
        d_raw = v_padded[3 * i + 1]
        a_raw = v_padded[3 * i + 2]

        # Step A: simplex projection for (b, d, u)
        u_raw = 1.0 - b_raw - d_raw
        projected = simplex_project([b_raw, d_raw, u_raw])
        b_proj, d_proj, u_proj = projected[0], projected[1], projected[2]

        # Step B: clamp base rate
        a_proj = max(0.0, min(1.0, a_raw))

        result.append((b_proj, d_proj, u_proj, a_proj))

    return result


# =========================================================================
# Shannon Analysis for Batch Compression — §4.8 Phase 7
#
# Pure functions computing bit-level efficiency metrics.
# These are used by the benchmark suite and paper tables.
#
# Wire format: seed_mode(4) + norm_q(2) + packed_coords(ceil(D×b/8))
#   Total: 6 + ceil(D×b/8) bytes
#   Overhead: 48 bits (seed 32 + norm_q 16)
#   Payload: D × b bits (of which 3N×b are useful, rest is padding)
# =========================================================================


def batch_wire_bits(n_opinions: int, bits: int) -> int:
    """Total bits on the wire for a batch of N opinions.

    Wire format: seed_mode(4 bytes) + norm_q(2 bytes) + packed_coords.
    Total = (6 + ceil(D × b / 8)) × 8 bits.

    Args:
        n_opinions: Number of opinions N.
        bits: Quantization bit-width b.

    Returns:
        Total wire cost in bits.
    """
    d = _next_power_of_2(3 * n_opinions)
    wire_bytes = 6 + (d * bits + 7) // 8
    return wire_bytes * 8


def batch_information_bits(n_opinions: int, bits: int) -> int:
    """Useful information bits: 3N × b.

    Each opinion contributes 3 free parameters (b, d, a),
    each quantized at b bits.

    Args:
        n_opinions: Number of opinions N.
        bits: Quantization bit-width b.

    Returns:
        Information payload in bits.
    """
    return 3 * n_opinions * bits


def batch_overhead_bits(n_opinions: int, bits: int) -> int:
    """Fixed overhead: seed(32 bits) + norm_q(16 bits) = 48 bits.

    This is constant regardless of N or b.

    Args:
        n_opinions: Number of opinions N (unused, for API consistency).
        bits: Quantization bit-width b (unused, for API consistency).

    Returns:
        Fixed overhead in bits (always 48).
    """
    return 48


def batch_padding_waste_bits(n_opinions: int, bits: int) -> int:
    """Wasted bits from power-of-2 padding: (D − 3N) × b.

    The padded dimension D = 2^ceil(log2(3N)) may exceed 3N,
    resulting in zero-padded coordinates that consume wire bits
    but carry no information.

    Args:
        n_opinions: Number of opinions N.
        bits: Quantization bit-width b.

    Returns:
        Padding waste in bits.
    """
    d = _next_power_of_2(3 * n_opinions)
    return (d - 3 * n_opinions) * bits


def batch_efficiency(n_opinions: int, bits: int) -> float:
    """Shannon efficiency: information_bits / wire_bits.

    Measures what fraction of wire bits carry useful information.
    Bounded in (0, 1]. Increases with N as overhead is amortized.

    Args:
        n_opinions: Number of opinions N.
        bits: Quantization bit-width b.

    Returns:
        Efficiency ratio in (0, 1].
    """
    return batch_information_bits(n_opinions, bits) / batch_wire_bits(n_opinions, bits)
