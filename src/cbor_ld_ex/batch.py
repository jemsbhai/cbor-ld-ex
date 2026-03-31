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
        bits: Number of quantization bits (2–8).
        dim: Padded dimension D for Beta-exact mode, or None for Gaussian.
        iterations: Maximum Lloyd-Max iterations (default 300).

    Returns:
        (boundaries, centroids): boundaries is a list of 2^b − 1
        decision boundaries (sorted ascending), centroids is a list
        of 2^b reconstruction levels (sorted ascending).

    Raises:
        ImportError: If scipy is not available.
    """
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
