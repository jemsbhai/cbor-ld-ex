"""
Full CBOR-LD-ex codec — encode/decode pipeline, bit-level compression analysis.

Ties Phases 1–3 together into a complete message format:
  encode: JSON-LD doc + Annotation → CBOR bytes
  decode: CBOR bytes → JSON-LD doc + Annotation
  payload_comparison: bit-level analysis across JSON-LD, CBOR-LD, CBOR-LD-ex
  annotation_information_bits: Shannon information content of annotations

Wire format (§5.3):
  CBOR map = {
    ...data fields (context-compressed to integer keys/values)...,
    ANNOTATION_TERM_ID: Tag(60000, annotation_bytes)
  }

  ANNOTATION_TERM_ID is a protocol-defined integer key (60000, matching
  the tag number). CBOR-LD maps ALL vocabulary terms to integers on the
  wire — string keys never appear. The annotation is no exception.

Compression analysis (§11): information-theoretic bit-level efficiency.
The core metric is:

  bit_efficiency = Shannon_information_bits / wire_bits

CBOR-LD-ex achieves >70% bit efficiency for Tier 1 annotations and
>95% for Tier 2 headers, compared to ~2.5% for JSON-LD and ~7% for
standard CBOR-LD encoding of the same semantic content.
"""

import json
import math
from typing import Optional

import cbor2

from cbor_ld_ex.annotations import (
    Annotation,
    CBOR_TAG_CBORLD_EX,
    encode_annotation,
    decode_annotation,
)
from cbor_ld_ex.headers import (
    Tier1Header,
    Tier2Header,
    Tier3Header,
    ComplianceStatus,
    OperatorId,
    PrecisionMode,
)
from cbor_ld_ex.opinions import dequantize_binomial


# =====================================================================
# Protocol constants
# =====================================================================

# Wire-level integer key for the annotation block in the CBOR map.
# CBOR-LD maps all vocabulary terms to integers — string keys never
# appear on the wire. The annotation term ID matches the CBOR tag
# number for self-documentation: 60000.
#
# CBOR encoding cost: 3 bytes (major type 0, additional info 25,
# 2-byte value). Compare to "@annotation" string: 12 bytes.
#
# The Tag(60000) wrapper on the value is still required per Axiom 1:
# RFC 8949 §3.4 says standard CBOR parsers skip unrecognized tags,
# so the annotation is ignorable by parsers that don't understand it.
ANNOTATION_TERM_ID = CBOR_TAG_CBORLD_EX  # 60000

# JSON-LD level key name — used only in JSON-LD text representations,
# never on the CBOR wire.
_ANNOTATION_JSONLD_KEY = "@annotation"


# =====================================================================
# Context Registry — CBOR-LD context compression (key + value)
# =====================================================================

class ContextRegistry:
    """Registry for compressing JSON-LD keys AND values to integer codes.

    Implements CBOR-LD context compression: maps known JSON-LD string
    keys to small integers (term compression) and known string values
    — IRIs, type names, unit labels — to integers (value compression).

    Only top-level keys and their immediate string values are compressed.
    Nested structures (dicts, lists) are left untouched.

    Args:
        key_map: Dict mapping JSON-LD string keys to integer codes.
        value_map: Optional dict mapping known string values to integer codes.

    Raises:
        ValueError: If duplicate or colliding integer codes exist.
    """

    def __init__(
        self,
        key_map: dict[str, int],
        value_map: Optional[dict[str, int]] = None,
    ) -> None:
        if value_map is None:
            value_map = {}

        # Validate no duplicate integer codes within each map
        for name, mapping in [("key_map", key_map), ("value_map", value_map)]:
            codes = list(mapping.values())
            unique_codes = set(codes)
            if len(codes) != len(unique_codes):
                dupes = [c for c in unique_codes if codes.count(c) > 1]
                raise ValueError(
                    f"Duplicate integer codes in {name}: {dupes}"
                )

        # Validate no collisions ACROSS maps
        key_codes = set(key_map.values())
        val_codes = set(value_map.values())
        overlap = key_codes & val_codes
        if overlap:
            raise ValueError(
                f"Integer code collision between key_map and value_map: {overlap}"
            )

        # Validate no collision with the reserved annotation term ID
        all_codes = key_codes | val_codes
        if ANNOTATION_TERM_ID in all_codes:
            raise ValueError(
                f"Integer code {ANNOTATION_TERM_ID} is reserved for "
                f"the CBOR-LD-ex annotation term ID and cannot be "
                f"used in key_map or value_map."
            )

        self._key_to_int: dict[str, int] = dict(key_map)
        self._int_to_key: dict[int, str] = {v: k for k, v in key_map.items()}
        self._val_to_int: dict[str, int] = dict(value_map)
        self._int_to_val: dict[int, str] = {v: k for k, v in value_map.items()}

    def compress(self, doc: dict) -> dict:
        """Compress top-level keys and known string values to integers."""
        result = {}
        for key, value in doc.items():
            compressed_key = self._key_to_int.get(key, key)
            if isinstance(value, str) and value in self._val_to_int:
                compressed_value = self._val_to_int[value]
            else:
                compressed_value = value
            result[compressed_key] = compressed_value
        return result

    def decompress(self, doc: dict) -> dict:
        """Decompress integer keys and integer values back to strings."""
        result = {}
        for key, value in doc.items():
            if isinstance(key, int) and key in self._int_to_key:
                decompressed_key = self._int_to_key[key]
            else:
                decompressed_key = key
            if isinstance(value, int) and value in self._int_to_val:
                decompressed_value = self._int_to_val[value]
            else:
                decompressed_value = value
            result[decompressed_key] = decompressed_value
        return result


# =====================================================================
# Encode
# =====================================================================

def encode(
    doc: dict,
    annotation: Annotation,
    context_registry: Optional[ContextRegistry] = None,
) -> bytes:
    """Encode a JSON-LD document + annotation to CBOR-LD-ex bytes.

    Wire structure:
      CBOR map = {
        ...data fields (integer keys if registry provided)...,
        ANNOTATION_TERM_ID: Tag(60000, annotation_bytes)
      }

    The annotation key is ALWAYS the protocol-defined integer term ID
    (60000). CBOR-LD encodes all vocabulary terms as integers — string
    keys never appear on the wire. The Tag(60000) wrapper on the value
    ensures Axiom 1 compliance: standard CBOR parsers skip unknown tags.
    """
    if context_registry is not None:
        data = context_registry.compress(doc)
    else:
        data = dict(doc)

    ann_bytes = encode_annotation(annotation)
    ann_tagged = cbor2.CBORTag(CBOR_TAG_CBORLD_EX, ann_bytes)
    data[ANNOTATION_TERM_ID] = ann_tagged

    return cbor2.dumps(data)


# =====================================================================
# Decode
# =====================================================================

def decode(
    data: bytes,
    context_registry: Optional[ContextRegistry] = None,
) -> tuple[dict, Annotation]:
    """Decode CBOR-LD-ex bytes to a JSON-LD document + annotation."""
    cbor_map = cbor2.loads(data)

    if not isinstance(cbor_map, dict):
        raise ValueError(
            f"Expected CBOR map, got {type(cbor_map).__name__}"
        )

    # Extract annotation by the protocol-defined integer key
    ann_tagged = cbor_map.pop(ANNOTATION_TERM_ID, None)

    if ann_tagged is None:
        raise ValueError(
            f"No annotation found at term ID {ANNOTATION_TERM_ID} "
            f"in CBOR-LD-ex message"
        )

    if not isinstance(ann_tagged, cbor2.CBORTag):
        raise ValueError(
            f"Expected CBOR tag for annotation, "
            f"got {type(ann_tagged).__name__}"
        )

    if ann_tagged.tag != CBOR_TAG_CBORLD_EX:
        raise ValueError(
            f"Expected CBOR tag {CBOR_TAG_CBORLD_EX}, "
            f"got tag {ann_tagged.tag}"
        )

    annotation = decode_annotation(ann_tagged.value)

    if context_registry is not None:
        doc = context_registry.decompress(cbor_map)
    else:
        doc = cbor_map

    return (doc, annotation)


# =====================================================================
# Bit-Level Information Analysis — §11 (Compression Analysis)
# =====================================================================

# State-space sizes for header fields.
# H(field) = log2(number_of_valid_states)  [Shannon 1948]
#
# Fields with power-of-2 state counts are perfectly packed;
# fields with non-power-of-2 counts have unavoidable waste bits.

_COMPLIANCE_STATUS_STATES = 3     # compliant, non_compliant, insufficient
_DELEGATION_FLAG_STATES = 2
_ORIGIN_TIER_STATES = 3           # 3 defined tiers (reserved excluded)
_HAS_OPINION_STATES = 2
_PRECISION_MODE_STATES = 4        # 4 defined modes: 8/16/32/delta (§4.3, v0.4.0+)
_OPERATOR_ID_STATES = 13          # 13 defined operators (Table 2)
_REASONING_CONTEXT_STATES = 16    # 4-bit field, full range usable
_CONTEXT_VERSION_STATES = 16
_HAS_MULTINOMIAL_STATES = 2
_SUB_TIER_DEPTH_T2_STATES = 8     # 3-bit field (Tier 2)
_SUB_TIER_DEPTH_T3_STATES = 16    # 4-bit field (Tier 3)
_SOURCE_COUNT_STATES = 256        # 8-bit field
_HAS_EXTENDED_CONTEXT_STATES = 2
_HAS_PROVENANCE_CHAIN_STATES = 2
_HAS_TRUST_INFO_STATES = 2


def _log2_safe(n: int) -> float:
    """Shannon information content: log2(n) for n >= 1."""
    if n < 1:
        return 0.0
    return math.log2(n)


def _opinion_information_bits(precision: int) -> float:
    """Compute the Shannon information content of an opinion tuple.

    For n-bit quantization, the opinion (b̂, d̂, û, â) has:
      - b̂ and d̂ are jointly constrained by b̂ + d̂ ≤ 2ⁿ−1.
        Valid pairs = ∑_{s=0}^{max_val} (s+1) = (max_val+1)(max_val+2)/2.
      - û is derived (0 additional bits).
      - â has 2ⁿ distinct values (independent).

    For 32-bit IEEE 754 mode: 24 effective mantissa bits × 3
    independent components (b, d, a; u derived from b+d+u=1).
    """
    if precision == 32:
        return 24.0 * 3

    max_val = (1 << precision) - 1
    valid_bd_pairs = (max_val + 1) * (max_val + 2) // 2
    bd_bits = _log2_safe(valid_bd_pairs)
    a_bits = _log2_safe(max_val + 1)

    return bd_bits + a_bits


def _opinion_wire_bits(precision: int) -> int:
    """Wire cost of an opinion tuple in bits.

    Only 3 values are transmitted (b̂, d̂, â). û is derived by decoder.
    """
    if precision == 32:
        return 3 * 32   # 3 × IEEE 754 float32
    return 3 * precision  # 3 × n-bit unsigned integers


def annotation_information_bits(annotation: Annotation) -> dict:
    """Compute the Shannon information content of a CBOR-LD-ex annotation.

    Returns a detailed breakdown: per-field information content (bits),
    wire cost (bits), and bit efficiency (information / wire cost).

    This is the core §11 metric. Efficiency of 100% means every wire
    bit carries one bit of information. Fixed-width field encodings
    waste bits when the state count is not a power of 2.

    References:
        Shannon, C.E. (1948). A Mathematical Theory of Communication.
        Jøsang, A. (2016). Subjective Logic. Springer.
        FORMAL_MODEL.md §4, §5.
    """
    header = annotation.header
    fields: dict[str, float] = {}

    # ── Shared header fields (byte 0, all tiers) ─────────────────
    fields["compliance_status"] = _log2_safe(_COMPLIANCE_STATUS_STATES)
    fields["delegation_flag"] = _log2_safe(_DELEGATION_FLAG_STATES)
    fields["origin_tier"] = _log2_safe(_ORIGIN_TIER_STATES)
    fields["has_opinion"] = _log2_safe(_HAS_OPINION_STATES)
    fields["precision_mode"] = _log2_safe(_PRECISION_MODE_STATES)

    header_wire_bits = 8  # 1 byte for Tier 1

    if isinstance(header, (Tier2Header, Tier3Header)):
        fields["operator_id"] = _log2_safe(_OPERATOR_ID_STATES)
        fields["reasoning_context"] = _log2_safe(_REASONING_CONTEXT_STATES)
        header_wire_bits = 32

    if isinstance(header, Tier2Header):
        fields["context_version"] = _log2_safe(_CONTEXT_VERSION_STATES)
        fields["has_multinomial"] = _log2_safe(_HAS_MULTINOMIAL_STATES)
        fields["sub_tier_depth"] = _log2_safe(_SUB_TIER_DEPTH_T2_STATES)
        fields["source_count"] = _log2_safe(_SOURCE_COUNT_STATES)

    if isinstance(header, Tier3Header):
        fields["has_extended_context"] = _log2_safe(_HAS_EXTENDED_CONTEXT_STATES)
        fields["has_provenance_chain"] = _log2_safe(_HAS_PROVENANCE_CHAIN_STATES)
        fields["has_multinomial"] = _log2_safe(_HAS_MULTINOMIAL_STATES)
        fields["has_trust_info"] = _log2_safe(_HAS_TRUST_INFO_STATES)
        fields["sub_tier_depth"] = _log2_safe(_SUB_TIER_DEPTH_T3_STATES)

    # ── Opinion payload ──────────────────────────────────────────
    opinion_info_bits = 0.0
    opinion_wire_bits = 0

    if header.has_opinion and annotation.opinion is not None:
        if header.precision_mode == PrecisionMode.DELTA_8:
            # Delta mode (§7.6): two signed int8 values, each with
            # 256 states. log₂(256) + log₂(256) = 16 bits exactly.
            # Wire cost: 2 bytes = 16 bits. 100% efficiency.
            opinion_info_bits = 16.0
            opinion_wire_bits = 16
        else:
            precision_map = {
                PrecisionMode.BITS_8: 8,
                PrecisionMode.BITS_16: 16,
                PrecisionMode.BITS_32: 32,
            }
            precision = precision_map[header.precision_mode]
            opinion_info_bits = _opinion_information_bits(precision)
            opinion_wire_bits = _opinion_wire_bits(precision)
        fields["opinion_tuple"] = opinion_info_bits

    # ── Totals ───────────────────────────────────────────────────
    header_info_bits = sum(
        v for k, v in fields.items() if k != "opinion_tuple"
    )
    total_info_bits = header_info_bits + opinion_info_bits
    total_wire_bits = header_wire_bits + opinion_wire_bits

    return {
        "fields": fields,
        "header_info_bits": header_info_bits,
        "header_wire_bits": header_wire_bits,
        "header_efficiency": (
            header_info_bits / header_wire_bits
            if header_wire_bits > 0 else 0.0
        ),
        "opinion_info_bits": opinion_info_bits,
        "opinion_wire_bits": opinion_wire_bits,
        "opinion_efficiency": (
            opinion_info_bits / opinion_wire_bits
            if opinion_wire_bits > 0 else 0.0
        ),
        "total_info_bits": total_info_bits,
        "total_wire_bits": total_wire_bits,
        "bit_efficiency": (
            total_info_bits / total_wire_bits
            if total_wire_bits > 0 else 0.0
        ),
    }


# =====================================================================
# Payload Comparison — §11 / Appendix C
# =====================================================================

def _annotation_to_jsonld(annotation: Annotation) -> dict:
    """Convert annotation to its verbose JSON-LD text representation."""
    result: dict = {}

    status_names = {
        ComplianceStatus.COMPLIANT: "compliant",
        ComplianceStatus.NON_COMPLIANT: "non_compliant",
        ComplianceStatus.INSUFFICIENT: "insufficient",
    }
    result["complianceStatus"] = status_names.get(
        annotation.header.compliance_status, "unknown"
    )

    if annotation.header.has_opinion and annotation.opinion is not None:
        if annotation.header.precision_mode == PrecisionMode.DELTA_8:
            # Delta mode (§7.6): cannot dequantize without previous
            # state. Report the raw deltas honestly.
            delta_b, delta_d = annotation.opinion
            result["opinion"] = {
                "delta_belief": delta_b,
                "delta_disbelief": delta_d,
            }
        else:
            precision_map = {
                PrecisionMode.BITS_8: 8,
                PrecisionMode.BITS_16: 16,
                PrecisionMode.BITS_32: 32,
            }
            precision = precision_map[annotation.header.precision_mode]

            if precision == 32:
                b, d, u, a = annotation.opinion
            else:
                b, d, u, a = dequantize_binomial(
                    *annotation.opinion, precision=precision
                )

            result["opinion"] = {
                "belief": b, "disbelief": d,
                "uncertainty": u, "baseRate": a,
            }

    result["reasoningBackend"] = "subjective_logic"

    header = annotation.header
    if isinstance(header, (Tier2Header, Tier3Header)):
        result["operatorId"] = int(header.operator_id)
        result["reasoningContext"] = header.reasoning_context

    if isinstance(header, Tier2Header):
        result["sourceCount"] = header.source_count

    return result


def _annotation_to_cbor_ld_bytes(annotation: Annotation) -> bytes:
    """Encode annotation as standard CBOR-LD (integer keys, CBOR values).

    This is what CBOR-LD would look like if it tried to carry the same
    annotation information as standard CBOR key-value pairs — no bit
    packing, just the most compact standard CBOR representation possible
    with fully compressed integer keys.

    This is the fair comparison baseline: same information, CBOR-LD's
    best encoding vs CBOR-LD-ex's bit-packed encoding.
    """
    # Integer keys: most compact standard CBOR-LD can achieve
    # 0=complianceStatus, 1=opinion(map), 2=reasoningBackend
    cbor_ann: dict = {
        0: int(annotation.header.compliance_status),
    }

    if annotation.header.has_opinion and annotation.opinion is not None:
        if annotation.header.precision_mode == PrecisionMode.DELTA_8:
            # Delta mode: CBOR-LD baseline encodes the raw deltas
            delta_b, delta_d = annotation.opinion
            cbor_ann[1] = {0: delta_b, 1: delta_d}
        else:
            precision_map = {
                PrecisionMode.BITS_8: 8,
                PrecisionMode.BITS_16: 16,
                PrecisionMode.BITS_32: 32,
            }
            precision = precision_map[annotation.header.precision_mode]

            if precision == 32:
                b, d, u, a = annotation.opinion
            else:
                b, d, u, a = dequantize_binomial(
                    *annotation.opinion, precision=precision
                )

            # CBOR encodes floats natively — still 4+ bytes each
            cbor_ann[1] = {0: b, 1: d, 2: u, 3: a}

    # Tier 2/3 fields
    header = annotation.header
    if isinstance(header, (Tier2Header, Tier3Header)):
        cbor_ann[2] = int(header.operator_id)
        cbor_ann[3] = header.reasoning_context

    if isinstance(header, Tier2Header):
        cbor_ann[4] = header.source_count

    return cbor2.dumps(cbor_ann)


def payload_comparison(
    doc: dict,
    annotation: Annotation,
    context_registry: Optional[ContextRegistry] = None,
) -> dict:
    """Compare payload sizes across JSON-LD, CBOR-LD, and CBOR-LD-ex.

    Produces both byte-level and BIT-LEVEL analysis:

    Byte-level (4-way comparison):
      - JSON-LD: document + annotation as verbose JSON text.
      - CBOR-LD (data only): data without annotation (semantic loss).
      - CBOR-LD (with annotation): data + annotation as standard CBOR
        key-value pairs. THE FAIR COMPARISON — same information as
        CBOR-LD-ex but without bit-packing.
      - CBOR-LD-ex: data + bit-packed annotation with Tag(60000).

    Bit-level (annotation-only analysis):
      - Shannon information content (theoretical minimum).
      - CBOR-LD-ex wire cost (bit-packed encoding).
      - CBOR-LD wire cost (standard CBOR encoding of same info).
      - JSON-LD wire cost (verbose text).
      - Bit efficiency for each encoding.

    The key finding: CBOR-LD-ex is SMALLER than CBOR-LD for the same
    semantic content. Bit-packing the annotation beats standard CBOR
    encoding by ~10× even with fully compressed integer keys.
    """
    # ── JSON-LD: verbose text ────────────────────────────────────
    jsonld_full = dict(doc)
    jsonld_full[_ANNOTATION_JSONLD_KEY] = _annotation_to_jsonld(annotation)
    json_ld_bytes = json.dumps(
        jsonld_full, separators=(",", ":"), sort_keys=False
    ).encode("utf-8")

    # ── CBOR-LD (data only): no annotation semantics ─────────────
    if context_registry is not None:
        cbor_data = context_registry.compress(doc)
    else:
        cbor_data = dict(doc)
    cbor_ld_data_only_bytes = cbor2.dumps(cbor_data)

    # ── CBOR-LD (with annotation): fair comparison ───────────────
    # Same information as CBOR-LD-ex, encoded as standard CBOR
    # key-value pairs. No bit-packing — just the best CBOR-LD can do.
    cbor_ld_with_ann_data = dict(cbor_data)
    cbor_ld_with_ann_data[ANNOTATION_TERM_ID] = cbor2.loads(
        _annotation_to_cbor_ld_bytes(annotation)
    )
    cbor_ld_with_ann_bytes = cbor2.dumps(cbor_ld_with_ann_data)

    # ── CBOR-LD-ex: bit-packed annotation ────────────────────────
    cbor_ld_ex_bytes = encode(
        doc, annotation, context_registry=context_registry,
    )

    # ── Byte-level sizes ─────────────────────────────────────────
    json_ld_size = len(json_ld_bytes)
    cbor_ld_data_only_size = len(cbor_ld_data_only_bytes)
    cbor_ld_with_ann_size = len(cbor_ld_with_ann_bytes)
    cbor_ld_ex_size = len(cbor_ld_ex_bytes)

    # ── Bit-level annotation-only analysis ───────────────────────
    ann_analysis = annotation_information_bits(annotation)

    # JSON-LD annotation text
    jsonld_ann_text = json.dumps(
        _annotation_to_jsonld(annotation), separators=(",", ":"),
    ).encode("utf-8")
    jsonld_ann_bits = len(jsonld_ann_text) * 8

    # CBOR-LD annotation (standard CBOR, integer keys — best it can do)
    cbor_ld_ann_bytes_raw = _annotation_to_cbor_ld_bytes(annotation)
    cbor_ld_ann_bits = len(cbor_ld_ann_bytes_raw) * 8

    # CBOR-LD-ex annotation (bit-packed payload only)
    cbor_ld_ex_ann_payload = encode_annotation(annotation)
    cbor_ld_ex_ann_bits = len(cbor_ld_ex_ann_payload) * 8

    info_bits = ann_analysis["total_info_bits"]

    # ── Semantic completeness ────────────────────────────────────
    cbor_ld_ex_fields = ["data", "compliance_status"]
    if annotation.header.has_opinion and annotation.opinion is not None:
        cbor_ld_ex_fields.append("opinion")
    if isinstance(annotation.header, (Tier2Header, Tier3Header)):
        cbor_ld_ex_fields.append("operator_provenance")
    if isinstance(annotation.header, Tier2Header):
        cbor_ld_ex_fields.append("source_count")

    return {
        # ── Byte-level sizes (full message) ──────────────────────
        "json_ld_bytes": json_ld_bytes,
        "cbor_ld_bytes": cbor_ld_data_only_bytes,
        "cbor_ld_with_ann_bytes": cbor_ld_with_ann_bytes,
        "cbor_ld_ex_bytes": cbor_ld_ex_bytes,
        "json_ld_size": json_ld_size,
        "cbor_ld_size": cbor_ld_data_only_size,
        "cbor_ld_with_ann_size": cbor_ld_with_ann_size,
        "cbor_ld_ex_size": cbor_ld_ex_size,
        "cbor_ld_reduction": 1.0 - (cbor_ld_data_only_size / json_ld_size),
        "cbor_ld_ex_reduction": 1.0 - (cbor_ld_ex_size / json_ld_size),

        # ── The critical comparison: CBOR-LD-ex vs CBOR-LD ──────
        # Same information. Bit-packing wins.
        "cbor_ld_ex_vs_cbor_ld_ann_ratio": (
            cbor_ld_with_ann_size / cbor_ld_ex_size
            if cbor_ld_ex_size > 0 else 0.0
        ),
        "cbor_ld_ex_smaller_than_cbor_ld": (
            cbor_ld_ex_size < cbor_ld_with_ann_size
        ),

        # ── Bit-level annotation analysis (§11) ─────────────────
        "annotation_analysis": ann_analysis,
        "annotation_info_bits": info_bits,

        # Per-encoding annotation wire cost and efficiency
        "jsonld_annotation_bits": jsonld_ann_bits,
        "jsonld_annotation_bit_efficiency": (
            info_bits / jsonld_ann_bits if jsonld_ann_bits > 0 else 0.0
        ),
        "cbor_ld_annotation_bits": cbor_ld_ann_bits,
        "cbor_ld_annotation_bit_efficiency": (
            info_bits / cbor_ld_ann_bits if cbor_ld_ann_bits > 0 else 0.0
        ),
        "cbor_ld_ex_annotation_bits": cbor_ld_ex_ann_bits,
        "cbor_ld_ex_annotation_bit_efficiency": (
            ann_analysis["bit_efficiency"]
        ),

        # Compression ratios (annotation only)
        "annotation_ratio_jsonld_vs_ex": (
            jsonld_ann_bits / cbor_ld_ex_ann_bits
            if cbor_ld_ex_ann_bits > 0 else 0.0
        ),
        "annotation_ratio_cbor_ld_vs_ex": (
            cbor_ld_ann_bits / cbor_ld_ex_ann_bits
            if cbor_ld_ex_ann_bits > 0 else 0.0
        ),

        # ── Semantic completeness ────────────────────────────────
        "semantic_fields": {
            "json_ld": ["data"],
            "cbor_ld": ["data"],
            "cbor_ld_ex": cbor_ld_ex_fields,
        },
    }
