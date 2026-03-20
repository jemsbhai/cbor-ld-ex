"""
Annotation assembly and CBOR tag wrapping for CBOR-LD-ex.

Combines headers (§5) and opinion payloads (§4) into complete
annotation blocks, and wraps them in CBOR Tag(60000) per §5.3.

Wire structure:
  [header bytes] [opinion bytes if has_opinion]

CBOR integration (§5.3):
  Tag(60000) → byte string (annotation block)
"""

from dataclasses import dataclass
from typing import Optional

import cbor2

from cbor_ld_ex.headers import (
    Tier1Header,
    Tier2Header,
    Tier3Header,
    PrecisionMode,
    encode_header,
    decode_header,
)
from cbor_ld_ex.opinions import (
    encode_opinion_bytes,
    decode_opinion_bytes,
)

CBOR_TAG_CBORLD_EX = 60000

# Map PrecisionMode to the int values expected by opinions.py
_PRECISION_MAP = {
    PrecisionMode.BITS_8: 8,
    PrecisionMode.BITS_16: 16,
    PrecisionMode.BITS_32: 32,
}


@dataclass
class Annotation:
    """An assembled annotation: header + optional opinion payload.

    Corresponds to Definition 6 (Annotation algebraic type) at the
    wire level. Higher-level variants (temporal, provenance chain)
    will be added in later phases.
    """
    header: Tier1Header | Tier2Header | Tier3Header
    opinion: Optional[tuple] = None  # quantized opinion (b̂, d̂, û, â)


def _header_size(header: Tier1Header | Tier2Header | Tier3Header) -> int:
    """Return the fixed header size in bytes."""
    if isinstance(header, Tier1Header):
        return 1
    elif isinstance(header, (Tier2Header, Tier3Header)):
        return 4
    raise TypeError(f"Unknown header type: {type(header)}")


def encode_annotation(ann: Annotation) -> bytes:
    """Encode an annotation to bytes: header + opinion payload.

    If the header's has_opinion flag is True, the opinion tuple is
    serialized after the header bytes using the header's precision_mode.
    """
    header_bytes = encode_header(ann.header)

    if ann.header.has_opinion and ann.opinion is not None:
        precision = _PRECISION_MAP[ann.header.precision_mode]
        b_q, d_q, u_q, a_q = ann.opinion
        # Wire format: transmit (b̂, d̂, â) only. û is derived by decoder.
        opinion_bytes = encode_opinion_bytes(b_q, d_q, a_q, precision=precision)
        return header_bytes + opinion_bytes

    return header_bytes


def decode_annotation(data: bytes) -> Annotation:
    """Decode bytes into an Annotation.

    Reads the header (dispatching on origin_tier), then reads the
    opinion payload if has_opinion is set.
    """
    # Decode header — this handles tier dispatch internally
    header = decode_header(data)
    offset = _header_size(header)

    opinion = None
    if header.has_opinion:
        precision = _PRECISION_MAP[header.precision_mode]
        opinion_data = data[offset:]
        opinion = decode_opinion_bytes(opinion_data, precision=precision)

    return Annotation(header=header, opinion=opinion)


def wrap_cbor_tag(annotation_bytes: bytes) -> bytes:
    """Wrap annotation bytes in CBOR Tag(60000) per §5.3.

    Returns CBOR-encoded Tag(60000, byte_string).
    Per RFC 8949 §3.4, a CBOR decoder encountering an unrecognized
    tag presents both the tag number and content — standard parsers
    will not fail on this.
    """
    tagged = cbor2.CBORTag(CBOR_TAG_CBORLD_EX, annotation_bytes)
    return cbor2.dumps(tagged)


def strip_cbor_tag(tagged_data: bytes) -> bytes:
    """Strip CBOR Tag(60000), returning the raw annotation bytes.

    Raises ValueError if the data is not tagged with 60000.
    This implements the stripping function σ from Axiom 1.
    """
    decoded = cbor2.loads(tagged_data)

    if not isinstance(decoded, cbor2.CBORTag):
        raise ValueError(
            "Expected CBOR tagged value, got "
            f"{type(decoded).__name__}"
        )

    if decoded.tag != CBOR_TAG_CBORLD_EX:
        raise ValueError(
            f"Expected CBOR tag {CBOR_TAG_CBORLD_EX}, "
            f"got tag {decoded.tag}"
        )

    return decoded.value
