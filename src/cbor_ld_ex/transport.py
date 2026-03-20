"""
Transport adapters and benchmark engine for CBOR-LD-ex.

Provides thin wrappers for MQTT and CoAP transport, plus a 6-way
encoding comparison that proves CBOR-LD-ex is smallest AND richest.

Transport adapters:
  - MQTT: to_mqtt_payload / from_mqtt_payload, topic + QoS derivation
  - CoAP: to_coap_payload / from_coap_payload, content format ID

Both transports carry the same payload — the CBOR-LD-ex codec output.
The transport layer adds protocol-specific metadata (topic, QoS,
content-format) but does not alter the payload encoding.

Benchmark (full_benchmark):
  6-way comparison for an IoT scenario:
    1. JSON-LD (raw text)
    2. jsonld-ex CBOR-LD (context-only compression)
    3. Our CBOR-LD data-only (full key+value compression, no annotation)
    4. jsonld-ex CBOR-LD + annotation (annotation as JSON in payload)
    5. Our CBOR-LD + standard CBOR annotation (same info, CBOR encoding)
    6. CBOR-LD-ex (bit-packed annotation)

References:
  MQTT v3.1.1 (OASIS), v5.0 (OASIS)
  CoAP RFC 7252
  802.15.4 MTU: 127 bytes
"""

import json
import re
from typing import Optional

import cbor2

from cbor_ld_ex.annotations import Annotation
from cbor_ld_ex.codec import (
    ContextRegistry,
    encode,
    decode,
    payload_comparison,
    annotation_information_bits,
    ANNOTATION_TERM_ID,
)
from cbor_ld_ex.headers import (
    Tier1Header,
    Tier2Header,
    Tier3Header,
    ComplianceStatus,
    PrecisionMode,
)
from cbor_ld_ex.opinions import dequantize_binomial

# jsonld-ex baselines for benchmark comparison
from jsonld_ex.cbor_ld import to_cbor as jex_to_cbor
from jsonld_ex.mqtt import to_mqtt_payload as jex_to_mqtt_payload


# =====================================================================
# Constants
# =====================================================================

# CoAP Content-Format ID for CBOR-LD-ex (experimental range).
# Standard CBOR is 60, CBOR-LD does not yet have an assigned ID.
# We use 60000, matching our CBOR tag number, in the experimental range.
COAP_CONTENT_FORMAT_CBOR_LD_EX = 60000

# 802.15.4 MTU (bytes)
_802154_MTU = 127

# Approximate CoAP overhead for a minimal NON message (bytes)
_COAP_OVERHEAD = 16

# Maximum payload that fits in a single 802.15.4 frame
_MAX_SINGLE_FRAME_PAYLOAD = _802154_MTU - _COAP_OVERHEAD


# =====================================================================
# MQTT Transport
# =====================================================================

def to_mqtt_payload(
    doc: dict,
    annotation: Annotation,
    context_registry: Optional[ContextRegistry] = None,
) -> bytes:
    """Encode a CBOR-LD-ex message as an MQTT payload.

    The payload is the standard CBOR-LD-ex codec output — no
    MQTT-specific framing. MQTT carries the raw CBOR bytes as the
    PUBLISH payload.

    Args:
        doc: JSON-LD document.
        annotation: CBOR-LD-ex annotation.
        context_registry: Optional ContextRegistry for compression.

    Returns:
        CBOR bytes ready for MQTT PUBLISH.
    """
    return encode(doc, annotation, context_registry=context_registry)


def from_mqtt_payload(
    payload: bytes,
    context_registry: Optional[ContextRegistry] = None,
) -> tuple[dict, Annotation]:
    """Decode an MQTT payload to a CBOR-LD-ex message.

    Args:
        payload: Raw CBOR bytes from MQTT SUBSCRIBE.
        context_registry: Same registry used during encoding.

    Returns:
        (doc, annotation) tuple.
    """
    return decode(payload, context_registry=context_registry)


def derive_topic(
    doc: dict,
    annotation: Annotation,
    prefix: str = "cbor-ld-ex",
) -> str:
    """Derive an MQTT topic from document metadata and annotation.

    Pattern: {prefix}/{@type}/{@id_fragment}/{compliance_status}

    The compliance status in the topic allows subscribers to filter
    by compliance state at the MQTT level (topic-based filtering is
    cheaper than payload inspection on constrained brokers).

    Args:
        doc: JSON-LD document.
        annotation: CBOR-LD-ex annotation.
        prefix: Topic prefix.

    Returns:
        MQTT topic string.
    """
    # Extract @type
    type_val = doc.get("@type", "unknown")
    if isinstance(type_val, list):
        type_val = type_val[0] if type_val else "unknown"
    type_str = _local_name(str(type_val))

    # Extract @id fragment
    id_val = doc.get("@id", "unknown")
    id_str = _local_name(str(id_val))

    # Compliance status
    status_names = {
        ComplianceStatus.COMPLIANT: "compliant",
        ComplianceStatus.NON_COMPLIANT: "non_compliant",
        ComplianceStatus.INSUFFICIENT: "insufficient",
    }
    status = status_names.get(annotation.header.compliance_status, "unknown")

    # Sanitise segments
    type_str = _sanitise_topic(type_str)
    id_str = _sanitise_topic(id_str)

    return f"{prefix}/{type_str}/{id_str}/{status}"


def derive_qos(doc: dict, annotation: Annotation) -> int:
    """Derive MQTT QoS level from the annotation's opinion.

    Uses the projected probability P(ω) = b + a·u:
      - P(ω) >= 0.9  → QoS 2 (exactly once) — high confidence
      - 0.5 <= P(ω) < 0.9 → QoS 1 (at least once) — normal
      - P(ω) < 0.5   → QoS 0 (at most once) — low confidence

    If no opinion is present, defaults to QoS 1.

    Args:
        doc: JSON-LD document (unused, reserved for future heuristics).
        annotation: CBOR-LD-ex annotation.

    Returns:
        MQTT QoS level: 0, 1, or 2.
    """
    if not annotation.header.has_opinion or annotation.opinion is None:
        return 1  # Default

    # Compute projected probability
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
            *annotation.opinion, precision=precision,
        )

    projected = b + a * u

    if projected >= 0.9:
        return 2
    elif projected >= 0.5:
        return 1
    else:
        return 0


def _local_name(iri: str) -> str:
    """Extract the local/fragment part of an IRI or URN."""
    if "#" in iri:
        return iri.rsplit("#", 1)[-1]
    if "/" in iri:
        return iri.rsplit("/", 1)[-1]
    if ":" in iri:
        return iri.rsplit(":", 1)[-1]
    return iri


def _sanitise_topic(segment: str) -> str:
    """Remove MQTT-illegal characters from a topic segment."""
    sanitised = re.sub(r"[#+\x00]", "_", segment)
    sanitised = sanitised.lstrip("$")
    return sanitised or "unknown"


# =====================================================================
# CoAP Transport
# =====================================================================

def to_coap_payload(
    doc: dict,
    annotation: Annotation,
    context_registry: Optional[ContextRegistry] = None,
) -> bytes:
    """Encode a CBOR-LD-ex message as a CoAP payload.

    Identical to MQTT — the payload is the CBOR-LD-ex codec output.
    CoAP-specific metadata (Content-Format option, Uri-Path) is set
    by the CoAP client, not embedded in the payload.

    Args:
        doc: JSON-LD document.
        annotation: CBOR-LD-ex annotation.
        context_registry: Optional ContextRegistry for compression.

    Returns:
        CBOR bytes for CoAP payload.
    """
    return encode(doc, annotation, context_registry=context_registry)


def from_coap_payload(
    payload: bytes,
    context_registry: Optional[ContextRegistry] = None,
) -> tuple[dict, Annotation]:
    """Decode a CoAP payload to a CBOR-LD-ex message.

    Args:
        payload: Raw CBOR bytes from CoAP response.
        context_registry: Same registry used during encoding.

    Returns:
        (doc, annotation) tuple.
    """
    return decode(payload, context_registry=context_registry)


# =====================================================================
# Benchmark — 6-way encoding comparison
#
# The paper deliverable. Compares:
#   1. JSON-LD (raw text, no compression)
#   2. jsonld-ex CBOR-LD (context-only compression, no annotation)
#   3. Our CBOR-LD data-only (full key+value compression, no annotation)
#   4. jsonld-ex CBOR-LD + annotation (annotation embedded as JSON)
#   5. Our CBOR-LD + standard CBOR annotation (same info, CBOR k/v)
#   6. CBOR-LD-ex (bit-packed annotation)
# =====================================================================

def _annotation_to_jsonld_dict(annotation: Annotation) -> dict:
    """Convert annotation to its verbose JSON-LD representation."""
    result = {}

    status_names = {
        ComplianceStatus.COMPLIANT: "compliant",
        ComplianceStatus.NON_COMPLIANT: "non_compliant",
        ComplianceStatus.INSUFFICIENT: "insufficient",
    }
    result["complianceStatus"] = status_names.get(
        annotation.header.compliance_status, "unknown"
    )

    if annotation.header.has_opinion and annotation.opinion is not None:
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
                *annotation.opinion, precision=precision,
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


def full_benchmark(
    doc: dict,
    annotation: Annotation,
    context_registry: Optional[ContextRegistry] = None,
) -> dict:
    """Run 6-way encoding comparison.

    Returns a dict keyed by encoding name, each containing:
      - size: payload size in bytes
      - bytes: raw payload bytes
      - semantic_fields: list of semantic fields carried
      - annotation_size: annotation overhead in bytes (0 if none)
      - msgs_per_802154_frame: how many fit in a 127-byte frame
      - annotation_bit_efficiency: Shannon info / wire bits (if applicable)

    Args:
        doc: JSON-LD document.
        annotation: CBOR-LD-ex annotation.
        context_registry: ContextRegistry for our compression.
    """
    # ── Annotation as JSON-LD dict ───────────────────────────────
    ann_jsonld = _annotation_to_jsonld_dict(annotation)

    # ── 1. JSON-LD (raw text) ────────────────────────────────────
    json_ld_full = dict(doc)
    json_ld_full["@annotation"] = ann_jsonld
    json_ld_bytes = json.dumps(
        json_ld_full, separators=(",", ":"),
    ).encode("utf-8")

    # ── 2. jsonld-ex CBOR-LD (context-only compression) ──────────
    # jsonld-ex compresses @context URLs to integers, all other keys
    # remain as strings. No annotation semantics.
    jex_context_registry = {
        "https://w3id.org/iot/compliance/v1": 10,
        "https://schema.org/": 1,
    }
    jex_cbor_bytes = jex_to_cbor(doc, context_registry=jex_context_registry)

    # ── 3. Our CBOR-LD data-only (full key+value compression) ────
    if context_registry is not None:
        our_data_compressed = context_registry.compress(doc)
    else:
        our_data_compressed = dict(doc)
    our_cbor_data_only = cbor2.dumps(our_data_compressed)

    # ── 4. jsonld-ex CBOR-LD + annotation ────────────────────────
    # jsonld-ex carries annotation as a JSON-LD dict inside the CBOR.
    # This is what a naive approach would do: just add the annotation
    # as a regular field in the CBOR-LD payload.
    jex_with_ann = dict(doc)
    jex_with_ann["@annotation"] = ann_jsonld
    jex_cbor_with_ann = jex_to_cbor(
        jex_with_ann, context_registry=jex_context_registry,
    )

    # ── 5. Our CBOR-LD + standard CBOR annotation ────────────────
    # Same info as CBOR-LD-ex, but annotation encoded as standard
    # CBOR key-value pairs with integer keys. No bit-packing.
    cbor_ann_dict = {
        0: int(annotation.header.compliance_status),
    }
    if annotation.header.has_opinion and annotation.opinion is not None:
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
                *annotation.opinion, precision=precision,
            )
        cbor_ann_dict[1] = {0: b, 1: d, 2: u, 3: a}

    header = annotation.header
    if isinstance(header, (Tier2Header, Tier3Header)):
        cbor_ann_dict[2] = int(header.operator_id)
        cbor_ann_dict[3] = header.reasoning_context
    if isinstance(header, Tier2Header):
        cbor_ann_dict[4] = header.source_count

    our_data_with_ann = dict(our_data_compressed)
    our_data_with_ann[ANNOTATION_TERM_ID] = cbor_ann_dict
    our_cbor_with_ann = cbor2.dumps(our_data_with_ann)

    # ── 6. CBOR-LD-ex (bit-packed annotation) ────────────────────
    cbor_ld_ex_bytes = encode(
        doc, annotation, context_registry=context_registry,
    )

    # ── Annotation-only analysis ─────────────────────────────────
    from cbor_ld_ex.annotations import encode_annotation
    ann_payload = encode_annotation(annotation)
    ann_analysis = annotation_information_bits(annotation)

    # Annotation size for JSON-LD
    json_ann_bytes = json.dumps(
        ann_jsonld, separators=(",", ":"),
    ).encode("utf-8")

    # Annotation size for CBOR-LD (standard encoding)
    cbor_ann_bytes = cbor2.dumps(cbor_ann_dict)

    # ── Semantic fields ──────────────────────────────────────────
    base_fields = ["data"]
    jex_fields = ["data"]  # jsonld-ex CBOR-LD carries data only (no SL)

    jex_with_ann_fields = [
        "data", "compliance_status", "opinion", "reasoning_backend",
    ]
    if isinstance(header, (Tier2Header, Tier3Header)):
        jex_with_ann_fields.extend(["operator_id", "reasoning_context"])
    if isinstance(header, Tier2Header):
        jex_with_ann_fields.append("source_count")

    ex_fields = ["data", "compliance_status"]
    if annotation.header.has_opinion and annotation.opinion is not None:
        ex_fields.append("opinion")
    if isinstance(header, (Tier2Header, Tier3Header)):
        ex_fields.extend([
            "operator_provenance", "reasoning_context",
            "delegation_flag", "origin_tier",
        ])
    if isinstance(header, Tier2Header):
        ex_fields.extend([
            "source_count", "context_version",
            "has_multinomial", "sub_tier_depth",
        ])

    # ── Build result ─────────────────────────────────────────────
    def _entry(payload_bytes, ann_bytes_size, fields, bit_eff=None):
        size = len(payload_bytes)
        return {
            "size": size,
            "bytes": payload_bytes,
            "annotation_size": ann_bytes_size,
            "semantic_fields": fields,
            "msgs_per_802154_frame": max(1, _MAX_SINGLE_FRAME_PAYLOAD // size) if size > 0 else 0,
            "annotation_bit_efficiency": bit_eff,
        }

    return {
        "json_ld": _entry(
            json_ld_bytes, len(json_ann_bytes), base_fields + ["annotation_json"],
        ),
        "jex_cbor_ld": _entry(
            jex_cbor_bytes, 0, jex_fields,
        ),
        "our_cbor_ld_data_only": _entry(
            our_cbor_data_only, 0, base_fields,
        ),
        "jex_cbor_ld_with_annotation": _entry(
            jex_cbor_with_ann, len(json_ann_bytes), jex_with_ann_fields,
        ),
        "our_cbor_ld_with_annotation": _entry(
            our_cbor_with_ann, len(cbor_ann_bytes), jex_with_ann_fields,
        ),
        "cbor_ld_ex": _entry(
            cbor_ld_ex_bytes, len(ann_payload),
            ex_fields,
            bit_eff=ann_analysis["bit_efficiency"],
        ),
    }
