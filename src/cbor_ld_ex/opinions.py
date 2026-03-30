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

import math
import struct
from typing import Union

from cbor_ld_ex.bitpack import BitWriter, BitReader

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
    """Constrained binomial quantization (Definition 10, §4.2).

    Quantizes b and d independently via rounding, then derives
    û = (2^n - 1) - b̂ - d̂. The uncertainty component is NEVER
    independently quantized — this is what preserves b̂ + d̂ + û = 2^n - 1
    exactly (Theorem 1a).

    Symmetric clamping rule (Theorem 1c, v0.4.0+): If b̂ + d̂ > max_val,
    decrement the component whose pre-rounding value had the larger
    fractional part. Tiebreaker: â LSB (even → decrement d̂, odd → b̂).
    This eliminates the asymmetric belief bias of the v0.3.0 rule.

    Returns:
        (b̂, d̂, û, â) — quantized integer values.
    """
    _validate_precision(precision)
    _validate_binomial(b, d, u, a)

    # 32-bit mode: IEEE 754, no quantization. Return raw floats.
    # This matches the wire format (encode_opinion_bytes packs float32)
    # and eliminates the footgun of producing uint32-scale integers
    # that can't round-trip through float32.
    if precision == 32:
        return (float(b), float(d), float(u), float(a))

    mv = _max_val(precision)

    b_q = _quantize_single(b, mv)
    d_q = _quantize_single(d, mv)
    a_q = _quantize_single(a, mv)

    # Symmetric clamping rule (§4.2, v0.4.0+):
    # If b̂ + d̂ > max_val, decrement the component whose pre-rounding
    # value had the larger fractional part (rounded up by more).
    # Tiebreaker: â LSB (even → decrement d̂, odd → decrement b̂).
    if b_q + d_q > mv:
        frac_b = b * mv - math.floor(b * mv)
        frac_d = d * mv - math.floor(d * mv)

        if frac_b > frac_d:
            b_q -= 1
        elif frac_d > frac_b:
            d_q -= 1
        else:
            # Tiebreaker: â LSB
            if a_q & 1 == 0:
                d_q -= 1
            else:
                b_q -= 1

    # Derived uncertainty — never independently quantized
    u_q = mv - b_q - d_q

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

    # 32-bit mode: values are already raw floats. Identity.
    if precision == 32:
        return (float(b_q), float(d_q), float(u_q), float(a_q))

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
    """Constrained multinomial quantization (Definition 11, §4.4 v0.4.2+).

    Uses integer simplex projection (Theorem 3c): quantize ALL k+1
    components independently, compute excess, distribute ±1 corrections
    by fractional-part rank with lower-index tiebreaker.

    Replaces the v0.4.1 iterative decrement loop which had a flaw:
    static fractional parts always targeted the same component.

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

    # 32-bit mode: IEEE 754, no quantization. Return raw floats.
    if precision == 32:
        return (
            [float(b) for b in beliefs],
            float(u),
            [float(a) for a in base_rates],
        )

    mv = _max_val(precision)

    # Integer simplex projection (§4.4, v0.4.2+):
    # Step 1: Quantize ALL k+1 components independently
    all_values = beliefs + [u]
    v = [_quantize_single(x, mv) for x in all_values]
    fracs = [x * mv - math.floor(x * mv) for x in all_values]

    # Step 2: Compute excess
    excess = sum(v) - mv

    # Step 3-4: Distribute corrections by fractional-part rank
    if excess > 0:
        # Over-budget: decrement components that rounded up the most
        # Sort by frac DESCENDING, lower index wins ties
        ranked = sorted(range(len(v)), key=lambda i: (-fracs[i], i))
        for j in range(excess):
            v[ranked[j]] -= 1
    elif excess < 0:
        # Under-budget: increment components that rounded down the most
        # Sort by frac ASCENDING, lower index wins ties
        ranked = sorted(range(len(v)), key=lambda i: (fracs[i], i))
        for j in range(-excess):
            v[ranked[j]] += 1

    # Step 5: Assign
    beliefs_q = v[:k]
    u_q = v[k]

    # Same projection for base rates (k components, sum = mv)
    base_rates_v = [_quantize_single(base_rates[i], mv) for i in range(k)]
    br_fracs = [base_rates[i] * mv - math.floor(base_rates[i] * mv) for i in range(k)]
    br_excess = sum(base_rates_v) - mv

    if br_excess > 0:
        ranked = sorted(range(k), key=lambda i: (-br_fracs[i], i))
        for j in range(br_excess):
            base_rates_v[ranked[j]] -= 1
    elif br_excess < 0:
        ranked = sorted(range(k), key=lambda i: (br_fracs[i], i))
        for j in range(-br_excess):
            base_rates_v[ranked[j]] += 1

    base_rates_q = base_rates_v

    return (beliefs_q, u_q, base_rates_q)


def dequantize_multinomial(
    beliefs_q: list[int], u_q: int, base_rates_q: list[int], precision: int = 8
) -> tuple[list[float], float, list[float]]:
    """Inverse multinomial quantization.

    Theorem 3(b) guarantees sum(beliefs_r) + u_r = 1.0 exactly,
    because sum(beliefs_q) + u_q = 2^n - 1 by construction.
    """
    _validate_precision(precision)

    # 32-bit mode: values are already raw floats. Identity.
    if precision == 32:
        return (
            [float(b) for b in beliefs_q],
            float(u_q),
            [float(a) for a in base_rates_q],
        )

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
    a_q: Union[int, float],
    precision: int = 8,
) -> bytes:
    """Serialize quantized opinion to bytes — transmits 3 values only.

    The uncertainty component û is NEVER transmitted. It carries zero
    bits of information because û = (2ⁿ−1) − b̂ − d̂ (integer modes)
    or u = 1 − b − d (float mode). The decoder derives it.

    Wire format (Table 1, revised for information-theoretic efficiency):
      8-bit:  3 bytes — b̂(uint8), d̂(uint8), â(uint8)
      16-bit: 6 bytes — b̂(uint16), d̂(uint16), â(uint16), big-endian
      32-bit: 12 bytes — b(float32), d(float32), a(float32), big-endian

    Args:
        b_q: Quantized belief (or float for 32-bit mode).
        d_q: Quantized disbelief (or float for 32-bit mode).
        a_q: Quantized base rate (or float for 32-bit mode).
        precision: Quantization precision (8, 16, or 32).

    Returns:
        Packed bytes (3, 6, or 12 bytes depending on precision).
    """
    _validate_precision(precision)

    if precision == 8:
        return struct.pack(">BBB", b_q, d_q, a_q)
    elif precision == 16:
        return struct.pack(">HHH", b_q, d_q, a_q)
    else:  # precision == 32
        return struct.pack(">fff", float(b_q), float(d_q), float(a_q))


def decode_opinion_bytes(
    data: bytes, precision: int = 8
) -> tuple:
    """Deserialize bytes to quantized opinion values — derives û.

    Reads 3 transmitted values (b̂, d̂, â) and derives the uncertainty
    component: û = (2ⁿ−1) − b̂ − d̂ for integer modes, or
    u = 1.0 − b − d for float mode.

    Returns:
      8-bit / 16-bit: (b_q, d_q, u_q, a_q) as ints — 4 values
      32-bit: (b, d, u, a) as floats — 4 values

    The caller always receives a 4-tuple. The wire format is 3 values;
    the 4th is reconstructed here.
    """
    _validate_precision(precision)

    if precision == 8:
        b_q, d_q, a_q = struct.unpack(">BBB", data)
        u_q = _max_val(8) - b_q - d_q
        return (b_q, d_q, u_q, a_q)
    elif precision == 16:
        b_q, d_q, a_q = struct.unpack(">HHH", data)
        u_q = _max_val(16) - b_q - d_q
        return (b_q, d_q, u_q, a_q)
    else:  # precision == 32
        b, d, a = struct.unpack(">fff", data)
        u = 1.0 - b - d
        return (b, d, u, a)


# -------------------------------------------------------------------------
# Multinomial byte encoding — bit-packed wire format
#
# Wire format (bit-packed via BitWriter, MSB-first):
#   [4 bits]  k (domain cardinality, 1–15; 0 reserved)
#   [n × (k-1)]  b̂₁..b̂_{k-1} (independently quantized)
#   [n × 1]      û (independently quantized)
#   [n × (k-1)]  â₁..â_{k-1} (independently quantized)
#   Pad to byte boundary with zero bits.
#
# NOT transmitted (derived by decoder):
#   b̂_k = (2ⁿ−1) − sum(b̂₁..b̂_{k-1}) − û
#   â_k = (2ⁿ−1) − sum(â₁..â_{k-1})
#
# precision_mode is NOT in the payload — it's already in the annotation
# header. This eliminates the wasted bits from the old spec format.
#
# Total wire bits: 4 + n*(2k-1).
# Total wire bytes: ceil((4 + n*(2k-1)) / 8).
# -------------------------------------------------------------------------

def encode_multinomial_bytes(
    beliefs_q: list[int],
    u_q: int,
    base_rates_q: list[int],
    precision: int = 8,
) -> bytes:
    """Bit-packed multinomial opinion wire encoding.

    Transmits k-1 beliefs, û, and k-1 base rates. The k-th belief
    and k-th base rate are derived by the decoder (zero information
    content — same principle as binomial û elision).

    Wire layout (MSB-first, bit-packed):
      [4 bits]       k (domain cardinality)
      [n × (k-1)]   b̂₁..b̂_{k-1}
      [n]            û
      [n × (k-1)]   â₁..â_{k-1}
      Pad to byte boundary.

    Args:
        beliefs_q: All k quantized belief values (including derived k-th).
        u_q: Quantized uncertainty.
        base_rates_q: All k quantized base rate values (including derived k-th).
        precision: Quantization precision (8, 16, or 32).

    Returns:
        Bit-packed bytes.
    """
    _validate_precision(precision)

    k = len(beliefs_q)
    if k < 1 or k > 15:
        raise ValueError(f"Domain cardinality k must be 1–15, got {k}")
    if len(base_rates_q) != k:
        raise ValueError(
            f"beliefs_q and base_rates_q must have same length: "
            f"{k} vs {len(base_rates_q)}"
        )

    w = BitWriter()

    # k: 4 bits
    w.write(k, 4)

    # Determine value width for bit-packing
    if precision == 32:
        value_width = 32
    else:
        value_width = precision

    # k-1 beliefs (skip the k-th — derived by decoder)
    for i in range(k - 1):
        if precision == 32:
            # Pack float32 as its uint32 bit pattern
            bits = struct.unpack(">I", struct.pack(">f", float(beliefs_q[i])))[0]
            w.write(bits, 32)
        else:
            w.write(beliefs_q[i], value_width)

    # û
    if precision == 32:
        bits = struct.unpack(">I", struct.pack(">f", float(u_q)))[0]
        w.write(bits, 32)
    else:
        w.write(u_q, value_width)

    # k-1 base rates (skip the k-th — derived by decoder)
    for i in range(k - 1):
        if precision == 32:
            bits = struct.unpack(">I", struct.pack(">f", float(base_rates_q[i])))[0]
            w.write(bits, 32)
        else:
            w.write(base_rates_q[i], value_width)

    return w.to_bytes()


def decode_multinomial_bytes(
    data: bytes,
    precision: int = 8,
) -> tuple[list, int, list]:
    """Bit-packed multinomial opinion wire decoding.

    Reads k from the first 4 bits, then k-1 beliefs, û, k-1 base rates.
    Derives the k-th belief and k-th base rate.

    Args:
        data: Bit-packed bytes from encode_multinomial_bytes().
        precision: Quantization precision (8, 16, or 32).

    Returns:
        (beliefs_q, u_q, base_rates_q) where beliefs_q and base_rates_q
        have length k (including derived k-th values).
    """
    _validate_precision(precision)

    r = BitReader(data)

    # k: 4 bits
    k = r.read(4)
    if k < 1 or k > 15:
        raise ValueError(f"Invalid domain cardinality k={k}")

    mv = _max_val(precision) if precision != 32 else None

    if precision == 32:
        value_width = 32
    else:
        value_width = precision

    def _read_value():
        raw = r.read(value_width)
        if precision == 32:
            return struct.unpack(">f", struct.pack(">I", raw))[0]
        return raw

    # k-1 beliefs
    beliefs_q = [_read_value() for _ in range(k - 1)]

    # û
    u_q = _read_value()

    # k-1 base rates
    base_rates_q = [_read_value() for _ in range(k - 1)]

    # Derive k-th belief: max_val - sum(b̂₁..b̂_{k-1}) - û
    if precision == 32:
        b_k = 1.0 - sum(beliefs_q) - u_q
        beliefs_q.append(b_k)
        a_k = 1.0 - sum(base_rates_q)
        base_rates_q.append(a_k)
    else:
        b_k = mv - sum(beliefs_q) - u_q
        beliefs_q.append(b_k)
        a_k = mv - sum(base_rates_q)
        base_rates_q.append(a_k)

    return (beliefs_q, u_q, base_rates_q)
