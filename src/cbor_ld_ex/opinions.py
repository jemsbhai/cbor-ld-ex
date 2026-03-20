"""
Quantization codec for Subjective Logic opinions.

Implements constrained quantization per FORMAL_MODEL.md §4:
  - Definition 9:  Q_n(x) = round(x * (2^n - 1))
  - Definition 10: Constrained binomial quantization (derive û)
  - Definition 11: Constrained multinomial quantization (derive b̂_k)
  - Theorem 1:  b̂ + d̂ + û = 2^n - 1 exactly
  - Theorem 3:  Multinomial constraint preservation

Precision modes (Table 1, §4.3):
  8-bit  (mode 00): 4 bytes per opinion tuple
  16-bit (mode 01): 8 bytes per opinion tuple
  32-bit (mode 10): 16 bytes (IEEE 754 float32, no quantization)
"""

import struct
from typing import Union

# Valid precision modes per Table 1
_VALID_PRECISIONS = {8, 16, 32}


def _max_val(precision: int) -> int:
    """Return 2^n - 1 for the given precision."""
    return (1 << precision) - 1


def _validate_precision(precision: int) -> None:
    """Raise ValueError if precision is not 8, 16, or 32."""
    if precision not in _VALID_PRECISIONS:
        raise ValueError(
            f"Invalid precision {precision}. Must be one of {sorted(_VALID_PRECISIONS)}."
        )


def _validate_binomial(b: float, d: float, u: float, a: float) -> None:
    """Validate a binomial opinion per Definition 1.

    Checks:
      - b, d, u >= 0
      - 0 <= a <= 1
      - b + d + u = 1 (within floating-point tolerance)
    """
    if b < -1e-9 or d < -1e-9 or u < -1e-9:
        raise ValueError(
            f"Opinion components must be non-negative: b={b}, d={d}, u={u}"
        )
    if a < -1e-9 or a > 1.0 + 1e-9:
        raise ValueError(f"Base rate must be in [0, 1]: a={a}")
    total = b + d + u
    if abs(total - 1.0) > 1e-6:
        raise ValueError(
            f"Opinion components must sum to 1.0: b + d + u = {total}"
        )


def _quantize_single(x: float, max_val: int) -> int:
    """Definition 9: Q_n(x) = round(x * (2^n - 1))."""
    return round(x * max_val)


def _dequantize_single(k: int, max_val: int) -> float:
    """Definition 9 inverse: Q_n^{-1}(k) = k / (2^n - 1)."""
    return k / max_val


# -------------------------------------------------------------------------
# Binomial quantization — Definition 10, Theorem 1
# -------------------------------------------------------------------------

def quantize_binomial(
    b: float, d: float, u: float, a: float, precision: int = 8
) -> tuple[int, int, int, int]:
    """Constrained binomial quantization (Definition 10).

    Quantizes b and d independently via rounding, then derives
    û = (2^n - 1) - b̂ - d̂. The uncertainty component is NEVER
    independently quantized — this is what preserves b̂ + d̂ + û = 2^n - 1
    exactly (Theorem 1a).

    Clamping rule (Theorem 1c): If b̂ + d̂ > max_val (possible when
    u ≈ 0 and both b and d round up), d̂ is decremented by 1.
    This introduces a documented marginal bias toward belief.

    Returns:
        (b̂, d̂, û, â) — quantized integer values.
    """
    _validate_precision(precision)
    _validate_binomial(b, d, u, a)

    mv = _max_val(precision)

    b_q = _quantize_single(b, mv)
    d_q = _quantize_single(d, mv)

    # Clamping rule: if b̂ + d̂ > max_val, decrement d̂ (bias toward belief)
    if b_q + d_q > mv:
        d_q -= 1

    # Derived uncertainty — never independently quantized
    u_q = mv - b_q - d_q

    a_q = _quantize_single(a, mv)

    return (b_q, d_q, u_q, a_q)


def dequantize_binomial(
    b_q: int, d_q: int, u_q: int, a_q: int, precision: int = 8
) -> tuple[float, float, float, float]:
    """Inverse quantization: reconstruct opinion from quantized values.

    Theorem 1(b) guarantees:
        Q_n^{-1}(b̂) + Q_n^{-1}(d̂) + Q_n^{-1}(û) = 1.0 exactly.

    This holds because b̂ + d̂ + û = 2^n - 1 by construction, and
    dividing each by (2^n - 1) gives a sum of exactly 1.
    """
    _validate_precision(precision)
    mv = _max_val(precision)

    # Each component is independently dequantized via Definition 9 inverse.
    # Theorem 1(b) guarantees the sum equals 1.0 in real arithmetic:
    #   (b̂ + d̂ + û) / (2ⁿ-1) = (2ⁿ-1) / (2ⁿ-1) = 1
    # In IEEE 754, the sum of three separate divisions may differ from
    # 1.0 by at most ~5 ULPs (~1e-15). This is an artifact of binary
    # floating-point, not a violation of the theorem. The INTEGER-domain
    # guarantee (Theorem 1a: b̂ + d̂ + û = 2ⁿ-1) is always exact.
    return (
        _dequantize_single(b_q, mv),
        _dequantize_single(d_q, mv),
        _dequantize_single(u_q, mv),
        _dequantize_single(a_q, mv),
    )


# -------------------------------------------------------------------------
# Multinomial quantization — Definition 11, Theorem 3
# -------------------------------------------------------------------------

def quantize_multinomial(
    beliefs: list[float], u: float, base_rates: list[float], precision: int = 8
) -> tuple[list[int], int, list[int]]:
    """Constrained multinomial quantization (Definition 11).

    For domain cardinality k:
      - Quantize k-1 belief components and u independently
      - Derive b̂_k = (2^n - 1) - sum(b̂_1..b̂_{k-1}) - û
      - Apply analogous constrained quantization to base rates

    Clamping (Theorem 3c): If the derived b̂_k < 0, reduce the
    largest b̂_i (for i < k) by 1 and recompute.

    Returns:
        (beliefs_q, u_q, base_rates_q)
    """
    _validate_precision(precision)

    k = len(beliefs)

    # Validation
    if len(base_rates) != k:
        raise ValueError(
            f"beliefs and base_rates must have same length: "
            f"{k} vs {len(base_rates)}"
        )
    if any(b < -1e-9 for b in beliefs):
        raise ValueError(f"All belief components must be non-negative: {beliefs}")
    if u < -1e-9:
        raise ValueError(f"Uncertainty must be non-negative: u={u}")
    total = sum(beliefs) + u
    if abs(total - 1.0) > 1e-6:
        raise ValueError(
            f"sum(beliefs) + u must equal 1.0: got {total}"
        )
    if any(a < -1e-9 for a in base_rates):
        raise ValueError(f"All base rates must be non-negative: {base_rates}")
    br_total = sum(base_rates)
    if abs(br_total - 1.0) > 1e-6:
        raise ValueError(
            f"sum(base_rates) must equal 1.0: got {br_total}"
        )

    mv = _max_val(precision)

    # Quantize k-1 beliefs independently, plus uncertainty
    beliefs_q = [_quantize_single(beliefs[i], mv) for i in range(k - 1)]
    u_q = _quantize_single(u, mv)

    # Derive k-th belief component
    b_k_q = mv - sum(beliefs_q) - u_q

    # Clamping (Theorem 3c): if derived b̂_k < 0, reduce largest b̂_i
    while b_k_q < 0:
        # Find index of the largest quantized belief among the first k-1
        max_idx = max(range(len(beliefs_q)), key=lambda i: beliefs_q[i])
        beliefs_q[max_idx] -= 1
        b_k_q = mv - sum(beliefs_q) - u_q

    beliefs_q.append(b_k_q)

    # Constrained quantization for base rates: same approach
    base_rates_q = [_quantize_single(base_rates[i], mv) for i in range(k - 1)]
    a_k_q = mv - sum(base_rates_q)

    # Clamp base rates if needed
    while a_k_q < 0:
        max_idx = max(range(len(base_rates_q)), key=lambda i: base_rates_q[i])
        base_rates_q[max_idx] -= 1
        a_k_q = mv - sum(base_rates_q)

    base_rates_q.append(a_k_q)

    return (beliefs_q, u_q, base_rates_q)


def dequantize_multinomial(
    beliefs_q: list[int], u_q: int, base_rates_q: list[int], precision: int = 8
) -> tuple[list[float], float, list[float]]:
    """Inverse multinomial quantization.

    Theorem 3(b) guarantees sum(beliefs_r) + u_r = 1.0 exactly,
    because sum(beliefs_q) + u_q = 2^n - 1 by construction.
    """
    _validate_precision(precision)
    mv = _max_val(precision)

    # Each component independently dequantized. See dequantize_binomial
    # docstring for the IEEE 754 vs real arithmetic note.
    beliefs_r = [_dequantize_single(bq, mv) for bq in beliefs_q]
    u_r = _dequantize_single(u_q, mv)
    base_rates_r = [_dequantize_single(aq, mv) for aq in base_rates_q]

    return (beliefs_r, u_r, base_rates_r)


# -------------------------------------------------------------------------
# Byte encoding — wire format for opinion payloads
# -------------------------------------------------------------------------

def encode_opinion_bytes(
    b_q: Union[int, float],
    d_q: Union[int, float],
    u_q: Union[int, float],
    a_q: Union[int, float],
    precision: int = 8,
) -> bytes:
    """Serialize quantized opinion to bytes.

    Precision modes (Table 1):
      8-bit:  4 bytes — 1 byte per component (uint8)
      16-bit: 8 bytes — 2 bytes per component (uint16, big-endian)
      32-bit: 16 bytes — 4 IEEE 754 float32 values (big-endian)
    """
    _validate_precision(precision)

    if precision == 8:
        return struct.pack(">BBBB", b_q, d_q, u_q, a_q)
    elif precision == 16:
        return struct.pack(">HHHH", b_q, d_q, u_q, a_q)
    else:  # precision == 32
        return struct.pack(">ffff", float(b_q), float(d_q), float(u_q), float(a_q))


def decode_opinion_bytes(
    data: bytes, precision: int = 8
) -> tuple:
    """Deserialize bytes to quantized opinion values.

    Returns:
      8-bit / 16-bit: (b_q, d_q, u_q, a_q) as ints
      32-bit: (b, d, u, a) as floats
    """
    _validate_precision(precision)

    if precision == 8:
        return struct.unpack(">BBBB", data)
    elif precision == 16:
        return struct.unpack(">HHHH", data)
    else:  # precision == 32
        return struct.unpack(">ffff", data)
