"""
Temporal extensions for CBOR-LD-ex.

Implements SECTION7_TEMPORAL.md:
  - Bit-packed extension block (has_temporal, has_triggers)
  - Log-scale half-life encoding (8 bits, ~1 second to ~388 days)
  - Three decay functions matching jsonld-ex (exponential, linear, step)
  - Quantized decay (dequantize → decay → re-quantize, Axiom 3 preserved)
  - Quantized expiry trigger (b → d transfer, u unchanged)
  - Trigger encoding (expiry, review_due, regulatory_change, withdrawal)

Wire format:
  Extensions are appended after [header][opinion] in the annotation byte
  string. Their presence is detected by remaining bytes — zero cost when
  absent. Layout is BIT-PACKED, not byte-aligned.

  [1 bit]  has_temporal
  [1 bit]  has_triggers
  IF has_temporal: [2 bits] decay_fn, [8 bits] half_life_encoded
  IF has_triggers: [3 bits] trigger_count, per-trigger data
  Pad to byte boundary with zeros.

All temporal operators preserve:
  Axiom 2: valid opinion in → valid opinion out
  Axiom 3: b̂ + d̂ + û = 2ⁿ − 1 exactly (via constrained re-quantization)
"""

import math
from dataclasses import dataclass
from typing import Optional

from cbor_ld_ex.opinions import (
    quantize_binomial,
    dequantize_binomial,
)


# =====================================================================
# Constants
# =====================================================================

# Decay function codes (2 bits)
DECAY_EXPONENTIAL = 0  # 00
DECAY_LINEAR = 1       # 01
DECAY_STEP = 2         # 10
# 3 = reserved

# Trigger type codes (2 bits)
TRIGGER_EXPIRY = 0       # 00
TRIGGER_REVIEW_DUE = 1   # 01
TRIGGER_REG_CHANGE = 2   # 10
TRIGGER_WITHDRAWAL = 3   # 11

# Half-life log-scale: seconds = 2^(value * MAX_EXPONENT / 255)
# MAX_EXPONENT = 25 → 2^25 ≈ 33.5M seconds ≈ 388 days
HALF_LIFE_MAX_EXPONENT = 25


# =====================================================================
# Data structures
# =====================================================================

@dataclass
class TemporalBlock:
    """Temporal metadata: decay function and half-life.

    decay_fn: 2-bit code (DECAY_EXPONENTIAL, DECAY_LINEAR, DECAY_STEP).
    half_life_encoded: 8-bit log-scale encoded half-life (0–255).
    """
    decay_fn: int          # 0–2
    half_life_encoded: int  # 0–255


@dataclass
class Trigger:
    """A compliance trigger event.

    trigger_type: 2-bit code (TRIGGER_EXPIRY, etc.).
    parameter: 8-bit quantized parameter.
      - expiry: gamma_q (residual lawfulness factor, Q8)
      - review_due: acceleration_q (Q8)
      - reg_change: unused (0)
      - withdrawal: unused (0)
    """
    trigger_type: int  # 0–3
    parameter: int     # 0–255 (only on wire for expiry, review_due)


@dataclass
class ExtensionBlock:
    """Bit-packed extension block appended after [header][opinion].

    temporal: TemporalBlock or None
    triggers: list of Trigger or None (None means no triggers)
    """
    temporal: Optional[TemporalBlock] = None
    triggers: Optional[list[Trigger]] = None


# =====================================================================
# Half-life log-scale codec
#
# 8 bits → 256 distinct values spanning ~1 second to ~388 days.
# Formula: seconds = 2^(value * MAX_EXPONENT / 255)
# Shannon efficiency: log2(256)/8 = 100%.
# Each step ≈ 7% change — perceptually uniform on a log scale.
# =====================================================================

def encode_half_life(seconds: float) -> int:
    """Encode a half-life in seconds to 8-bit log-scale.

    Args:
        seconds: Half-life in seconds. Must be positive.

    Returns:
        Integer in [0, 255].

    Raises:
        ValueError: If seconds <= 0.
    """
    if seconds <= 0.0:
        raise ValueError(
            f"Half-life must be positive, got: {seconds}"
        )
    # seconds = 2^(value * MAX_EXPONENT / 255)
    # log2(seconds) = value * MAX_EXPONENT / 255
    # value = log2(seconds) * 255 / MAX_EXPONENT
    if seconds <= 1.0:
        return 0
    log2_s = math.log2(seconds)
    value = round(log2_s * 255.0 / HALF_LIFE_MAX_EXPONENT)
    return max(0, min(255, value))


def decode_half_life(value: int) -> float:
    """Decode 8-bit log-scale value to half-life in seconds.

    Args:
        value: Integer in [0, 255].

    Returns:
        Half-life in seconds (always positive).
    """
    exponent = value * HALF_LIFE_MAX_EXPONENT / 255.0
    return 2.0 ** exponent


# =====================================================================
# Decay factor computation
#
# Three built-in functions matching jsonld-ex exactly:
#   exponential: λ(t,τ) = 2^(−t/τ)
#   linear:      λ(t,τ) = max(0, 1 − t/(2τ))
#   step:        λ(t,τ) = 1 if t < τ else 0
# =====================================================================

def compute_decay_factor(
    decay_fn: int, half_life: float, elapsed: float
) -> float:
    """Compute the decay factor for a given function, half-life, and elapsed time.

    Args:
        decay_fn: Decay function code (DECAY_EXPONENTIAL, etc.).
        half_life: Half-life in seconds (positive).
        elapsed: Time elapsed in seconds (non-negative).

    Returns:
        Decay factor in [0, 1].

    Raises:
        ValueError: If decay_fn is unknown.
    """
    if decay_fn == DECAY_EXPONENTIAL:
        return 2.0 ** (-elapsed / half_life)
    elif decay_fn == DECAY_LINEAR:
        return max(0.0, 1.0 - elapsed / (2.0 * half_life))
    elif decay_fn == DECAY_STEP:
        return 1.0 if elapsed < half_life else 0.0
    else:
        raise ValueError(
            f"Unknown decay function code: {decay_fn}. "
            f"Must be 0 (exponential), 1 (linear), or 2 (step)."
        )


# =====================================================================
# Quantized operators
#
# All operators follow the same pattern:
#   1. Dequantize to float domain
#   2. Apply operator at full precision
#   3. Re-quantize via constrained quantization
# Axiom 3 is preserved by construction (step 3 derives û).
# =====================================================================

def apply_decay_quantized(
    b_q: int, d_q: int, u_q: int, a_q: int,
    decay_factor: float,
    precision: int = 8,
) -> tuple[int, int, int, int]:
    """Apply temporal decay to a quantized opinion.

    Dequantizes → decays at float precision → re-quantizes.
    Axiom 3 guaranteed: constrained quantization derives û.

    Args:
        b_q, d_q, u_q, a_q: Quantized opinion components.
        decay_factor: Factor in [0, 1].
        precision: Quantization precision (8 or 16).

    Returns:
        (b̂', d̂', û', â) — decayed and re-quantized.
    """
    b, d, u, a = dequantize_binomial(b_q, d_q, u_q, a_q, precision=precision)

    # Decay: scale belief and disbelief, derive uncertainty
    b_dec = decay_factor * b
    d_dec = decay_factor * d
    u_dec = 1.0 - b_dec - d_dec

    # Clamp for IEEE 754 artifacts
    if u_dec < 0.0:
        u_dec = 0.0

    # Re-quantize — constrained quantization preserves Axiom 3
    return quantize_binomial(b_dec, d_dec, u_dec, a, precision=precision)


def apply_expiry_quantized(
    b_q: int, d_q: int, u_q: int, a_q: int,
    gamma_q: int,
    precision: int = 8,
) -> tuple[int, int, int, int]:
    """Apply expiry trigger to a quantized opinion.

    Expiry transfers lawfulness → violation:
      b' = γ · b
      d' = d + (1 − γ) · b
      u' = u  (unchanged in continuous domain)

    Constraint proof: b' + d' + u' = γb + d + (1−γ)b + u = b + d + u = 1. ∎

    Dequantizes → applies transfer → re-quantizes.
    Axiom 3 guaranteed by constrained re-quantization.

    Args:
        b_q, d_q, u_q, a_q: Quantized opinion components.
        gamma_q: Quantized residual factor (0=hard expiry, 255≈no effect).
        precision: Quantization precision (8 or 16).

    Returns:
        (b̂', d̂', û', â) — post-expiry, re-quantized.
    """
    max_val = (1 << precision) - 1
    b, d, u, a = dequantize_binomial(b_q, d_q, u_q, a_q, precision=precision)
    gamma = gamma_q / max_val

    # Expiry transfer: belief → disbelief
    b_new = gamma * b
    d_new = d + (1.0 - gamma) * b
    u_new = u  # Unchanged — expiry is a known fact, not an epistemic gap

    # Clamp for IEEE 754 artifacts
    # b_new + d_new + u_new should equal 1.0 analytically,
    # but floating-point may introduce tiny errors.
    total = b_new + d_new + u_new
    if abs(total - 1.0) > 1e-9:
        # Normalize to maintain constraint before quantization
        b_new /= total
        d_new /= total
        u_new /= total

    return quantize_binomial(b_new, d_new, u_new, a, precision=precision)


# =====================================================================
# Bit writer / reader
#
# Packs and unpacks individual bits into/from byte arrays.
# MSB-first within each byte, matching network byte order.
# =====================================================================

class _BitWriter:
    """Accumulates bits MSB-first, pads final byte with zeros."""

    def __init__(self) -> None:
        self._bits: list[int] = []

    def write(self, value: int, width: int) -> None:
        """Write `width` bits from `value` (MSB first)."""
        for i in range(width - 1, -1, -1):
            self._bits.append((value >> i) & 1)

    def to_bytes(self) -> bytes:
        """Pad to byte boundary and return bytes."""
        if not self._bits:
            return b""
        # Pad to multiple of 8
        while len(self._bits) % 8 != 0:
            self._bits.append(0)
        result = bytearray()
        for i in range(0, len(self._bits), 8):
            byte = 0
            for j in range(8):
                byte = (byte << 1) | self._bits[i + j]
            result.append(byte)
        return bytes(result)


class _BitReader:
    """Reads individual bits from a byte array, MSB-first."""

    def __init__(self, data: bytes) -> None:
        self._bits: list[int] = []
        for byte in data:
            for i in range(7, -1, -1):
                self._bits.append((byte >> i) & 1)
        self._pos = 0

    def read(self, width: int) -> int:
        """Read `width` bits and return as integer."""
        value = 0
        for _ in range(width):
            value = (value << 1) | self._bits[self._pos]
            self._pos += 1
        return value

    @property
    def remaining(self) -> int:
        """Number of unread bits."""
        return len(self._bits) - self._pos


# =====================================================================
# Extension block wire format
#
# Bit-packed, appended after [header][opinion]. Detected by remaining
# bytes in the annotation byte string. Zero cost when absent.
#
# [1 bit]  has_temporal
# [1 bit]  has_triggers
# IF has_temporal: [2 bits] decay_fn, [8 bits] half_life_encoded
# IF has_triggers: [3 bits] trigger_count (1–7), per-trigger data
#   PER TRIGGER:
#     [2 bits] trigger_type
#     IF expiry(00):     [8 bits] gamma_q
#     IF review_due(01): [8 bits] accel_q
#     IF reg_change(10): 0 bits
#     IF withdrawal(11): 0 bits
# Pad remaining bits to byte boundary with zeros.
# =====================================================================

def _trigger_has_payload(trigger_type: int) -> bool:
    """Return True if this trigger type carries an 8-bit payload."""
    return trigger_type in (TRIGGER_EXPIRY, TRIGGER_REVIEW_DUE)


def encode_extensions(ext: ExtensionBlock) -> bytes:
    """Encode an extension block to bit-packed bytes.

    Returns empty bytes if no extensions are present.

    Raises:
        ValueError: If trigger list is empty or exceeds 7.
    """
    has_temporal = ext.temporal is not None
    has_triggers = ext.triggers is not None

    if not has_temporal and not has_triggers:
        return b""

    # Validate triggers
    if has_triggers:
        if len(ext.triggers) == 0:
            raise ValueError(
                "Empty trigger list is invalid. Use triggers=None "
                "for no triggers."
            )
        if len(ext.triggers) > 7:
            raise ValueError(
                f"Trigger count {len(ext.triggers)} exceeds maximum of 7 "
                f"(3-bit field)."
            )

    w = _BitWriter()

    # Flags
    w.write(1 if has_temporal else 0, 1)
    w.write(1 if has_triggers else 0, 1)

    # Temporal block
    if has_temporal:
        w.write(ext.temporal.decay_fn & 0x03, 2)
        w.write(ext.temporal.half_life_encoded & 0xFF, 8)

    # Triggers
    if has_triggers:
        w.write(len(ext.triggers), 3)
        for trigger in ext.triggers:
            w.write(trigger.trigger_type & 0x03, 2)
            if _trigger_has_payload(trigger.trigger_type):
                w.write(trigger.parameter & 0xFF, 8)

    return w.to_bytes()


def decode_extensions(data: bytes) -> ExtensionBlock:
    """Decode bit-packed extension block from bytes.

    Args:
        data: Raw bytes (may include padding bits at end).

    Returns:
        ExtensionBlock with temporal and/or triggers populated.
    """
    r = _BitReader(data)

    has_temporal = bool(r.read(1))
    has_triggers = bool(r.read(1))

    temporal = None
    if has_temporal:
        decay_fn = r.read(2)
        half_life_encoded = r.read(8)
        temporal = TemporalBlock(
            decay_fn=decay_fn,
            half_life_encoded=half_life_encoded,
        )

    triggers = None
    if has_triggers:
        trigger_count = r.read(3)
        triggers = []
        for _ in range(trigger_count):
            trigger_type = r.read(2)
            parameter = 0
            if _trigger_has_payload(trigger_type):
                parameter = r.read(8)
            triggers.append(Trigger(
                trigger_type=trigger_type,
                parameter=parameter,
            ))

    return ExtensionBlock(temporal=temporal, triggers=triggers)
