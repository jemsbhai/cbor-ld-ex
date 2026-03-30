"""
Phase 6 tests: Full CBOR-LD-ex codec — encode/decode pipeline and payload comparison.

Tests are derived from FORMAL_MODEL.md:
  - §5.3: Full message structure (CBOR-LD data + Tag(60000) annotation)
  - Appendix C: Worked example (JSON-LD → CBOR-LD → CBOR-LD-ex comparison)
  - Axiom 1: Full-stack stripping property
  - Axiom 2: Algebraic closure through encode/decode
  - Axiom 3: Quantization invariant through full pipeline

Depends on:
  - Phase 1: opinions.py (quantization codec)
  - Phase 2: headers.py (tier-dependent headers)
  - Phase 3: annotations.py (annotation assembly + CBOR tag)
  - jsonld-ex: Opinion, cumulative_fuse (published SL algebra)

All tests target: src/cbor_ld_ex/codec.py
"""

import json
import math

import cbor2
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from cbor_ld_ex.annotations import (
    Annotation,
    CBOR_TAG_CBORLD_EX,
    encode_annotation,
)
from cbor_ld_ex.headers import (
    Tier1Header,
    Tier2Header,
    Tier3Header,
    ComplianceStatus,
    OperatorId,
    PrecisionMode,
)
from cbor_ld_ex.opinions import (
    quantize_binomial,
    dequantize_binomial,
)
from cbor_ld_ex.codec import (
    encode,
    decode,
    payload_comparison,
    annotation_information_bits,
    provenance_block_information_bits,
    ContextRegistry,
    ANNOTATION_TERM_ID,
)
from cbor_ld_ex.security import (
    ProvenanceEntry,
    CHAIN_ORIGIN_SENTINEL,
    AUDIT_CHAIN_ORIGIN_SENTINEL,
    encode_provenance_entry,
    compute_entry_digest,
)

# SL algebra from the parent library — the published reference implementation
from jsonld_ex.confidence_algebra import Opinion, cumulative_fuse


# ═══════════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════════

# IoT compliance context registry per Appendix C scenario.
# CBOR-LD compresses BOTH keys (term names → integers) AND values
# (known IRIs, type names, units → integers). Only instance-specific
# data (sensor URIs, timestamps, numeric readings) remains as-is.
IOT_CONTEXT_REGISTRY = ContextRegistry(
    key_map={
        "@context": 0,
        "@type": 1,
        "@id": 2,
        "value": 3,
        "unit": 4,
        "observedAt": 5,
    },
    value_map={
        "https://w3id.org/iot/compliance/v1": 100,
        "TemperatureReading": 101,
        "Celsius": 102,
    },
)

# Appendix C worked example — temperature sensor reading
APPENDIX_C_DOC = {
    "@context": "https://w3id.org/iot/compliance/v1",
    "@type": "TemperatureReading",
    "@id": "urn:sensor:temp-042",
    "value": 22.5,
    "unit": "Celsius",
    "observedAt": "2026-03-12T10:00:00Z",
}

# Tier 1 annotation from Appendix C.3: compliant, 85% belief
APPENDIX_C_ANNOTATION = Annotation(
    header=Tier1Header(
        compliance_status=ComplianceStatus.COMPLIANT,
        delegation_flag=False,
        has_opinion=True,
        precision_mode=PrecisionMode.BITS_8,
    ),
    opinion=(217, 13, 25, 128),  # Q_8(0.85, 0.05, 0.10, 0.50)
)


# ═══════════════════════════════════════════════════════════════════
# 1. CONTEXT REGISTRY
# ═══════════════════════════════════════════════════════════════════

class TestContextRegistry:
    """Context registry compresses JSON-LD keys AND values to integers."""

    def test_compress_known_keys(self):
        """Keys in the key_map are mapped to their integer codes."""
        registry = IOT_CONTEXT_REGISTRY
        doc = {"@type": "TemperatureReading", "value": 22.5}
        compressed = registry.compress(doc)
        assert 1 in compressed      # @type → 1
        assert 3 in compressed      # value → 3
        assert "@type" not in compressed
        assert "value" not in compressed

    def test_compress_known_values(self):
        """String values in the value_map are mapped to integer codes."""
        registry = IOT_CONTEXT_REGISTRY
        doc = {"@type": "TemperatureReading", "unit": "Celsius"}
        compressed = registry.compress(doc)
        assert compressed[1] == 101  # "TemperatureReading" → 101
        assert compressed[4] == 102  # "Celsius" → 102

    def test_compress_context_iri(self):
        """The @context IRI is compressed via value_map."""
        registry = IOT_CONTEXT_REGISTRY
        doc = {"@context": "https://w3id.org/iot/compliance/v1"}
        compressed = registry.compress(doc)
        assert compressed[0] == 100  # IRI → 100

    def test_compress_preserves_unknown_strings(self):
        """String values NOT in value_map pass through unchanged."""
        registry = IOT_CONTEXT_REGISTRY
        doc = {"@id": "urn:sensor:temp-042", "observedAt": "2026-03-12T10:00:00Z"}
        compressed = registry.compress(doc)
        # Instance-specific strings are NOT in the value map
        assert compressed[2] == "urn:sensor:temp-042"
        assert compressed[5] == "2026-03-12T10:00:00Z"

    def test_compress_preserves_non_string_values(self):
        """Non-string values (numbers, bools, etc.) are never compressed."""
        registry = IOT_CONTEXT_REGISTRY
        doc = {"value": 22.5}
        compressed = registry.compress(doc)
        assert compressed[3] == 22.5  # numeric value untouched

    def test_compress_unknown_keys_preserved(self):
        """Keys NOT in the key_map pass through unchanged."""
        registry = IOT_CONTEXT_REGISTRY
        doc = {"@type": "TemperatureReading", "customField": 42}
        compressed = registry.compress(doc)
        assert 1 in compressed             # @type compressed
        assert "customField" in compressed  # not in key_map → preserved

    def test_decompress_roundtrip(self):
        """compress → decompress recovers original document."""
        registry = IOT_CONTEXT_REGISTRY
        doc = {
            "@context": "https://w3id.org/iot/compliance/v1",
            "@type": "TemperatureReading",
            "value": 22.5,
            "unit": "Celsius",
        }
        compressed = registry.compress(doc)
        decompressed = registry.decompress(compressed)
        assert decompressed == doc

    def test_full_appendix_c_roundtrip(self):
        """Full Appendix C document survives compress → decompress."""
        registry = IOT_CONTEXT_REGISTRY
        compressed = registry.compress(APPENDIX_C_DOC)
        decompressed = registry.decompress(compressed)
        assert decompressed == APPENDIX_C_DOC

    def test_empty_registry_is_identity(self):
        """With no mappings, compress is a no-op."""
        registry = ContextRegistry(key_map={})
        doc = {"@type": "X", "value": 42}
        compressed = registry.compress(doc)
        assert compressed == doc

    def test_key_map_rejects_duplicate_codes(self):
        """Two keys mapping to the same integer is invalid."""
        with pytest.raises(ValueError):
            ContextRegistry(key_map={"@type": 1, "@id": 1})

    def test_value_map_rejects_duplicate_codes(self):
        """Two values mapping to the same integer is invalid."""
        with pytest.raises(ValueError):
            ContextRegistry(key_map={}, value_map={"Celsius": 100, "Kelvin": 100})

    def test_cross_map_collision_rejected(self):
        """Integer code collision between key_map and value_map is invalid."""
        with pytest.raises(ValueError):
            ContextRegistry(
                key_map={"@type": 1},
                value_map={"TemperatureReading": 1},  # collides with @type
            )

    def test_annotation_term_id_reserved(self):
        """ANNOTATION_TERM_ID (60000) is reserved and cannot be used."""
        with pytest.raises(ValueError, match="reserved"):
            ContextRegistry(key_map={"@annotation": ANNOTATION_TERM_ID})
        with pytest.raises(ValueError, match="reserved"):
            ContextRegistry(key_map={}, value_map={"annotate": ANNOTATION_TERM_ID})

    def test_compress_nested_dict_not_affected(self):
        """Nested dicts are instance data — not compressed."""
        registry = IOT_CONTEXT_REGISTRY
        doc = {"@type": "TemperatureReading", "value": {"unit": "Celsius"}}
        compressed = registry.compress(doc)
        # "TemperatureReading" at top level IS compressed
        assert compressed[1] == 101
        # But the nested dict is left as-is (value is dict, not string)
        assert compressed[3] == {"unit": "Celsius"}


# ═══════════════════════════════════════════════════════════════════
# 2. FULL ENCODE / DECODE — TIER 1
# ═══════════════════════════════════════════════════════════════════

class TestTier1Codec:
    """Full encode/decode of Tier 1 CBOR-LD-ex messages."""

    def test_encode_produces_bytes(self):
        """encode() returns a bytes object."""
        result = encode(APPENDIX_C_DOC, APPENDIX_C_ANNOTATION)
        assert isinstance(result, bytes)

    def test_encode_produces_valid_cbor(self):
        """Output is valid CBOR (parseable by cbor2)."""
        result = encode(APPENDIX_C_DOC, APPENDIX_C_ANNOTATION)
        decoded = cbor2.loads(result)
        assert isinstance(decoded, dict)

    def test_decode_recovers_document(self):
        """decode() recovers the original JSON-LD document."""
        encoded = encode(APPENDIX_C_DOC, APPENDIX_C_ANNOTATION)
        doc, ann = decode(encoded)
        assert doc["@type"] == "TemperatureReading"
        assert doc["value"] == 22.5
        assert doc["unit"] == "Celsius"
        assert doc["@id"] == "urn:sensor:temp-042"

    def test_decode_recovers_annotation(self):
        """decode() recovers the annotation with correct fields."""
        encoded = encode(APPENDIX_C_DOC, APPENDIX_C_ANNOTATION)
        doc, ann = decode(encoded)
        assert isinstance(ann.header, Tier1Header)
        assert ann.header.compliance_status == ComplianceStatus.COMPLIANT
        assert ann.header.has_opinion is True
        assert ann.opinion == (217, 13, 25, 128)

    def test_roundtrip_preserves_all_fields(self):
        """Full encode → decode roundtrip preserves document and annotation."""
        encoded = encode(APPENDIX_C_DOC, APPENDIX_C_ANNOTATION)
        doc, ann = decode(encoded)

        # Document fields
        for key in APPENDIX_C_DOC:
            assert doc[key] == APPENDIX_C_DOC[key], f"Mismatch on key '{key}'"

        # Annotation fields
        assert ann.header.compliance_status == ComplianceStatus.COMPLIANT
        assert ann.header.delegation_flag is False
        assert ann.header.precision_mode == PrecisionMode.BITS_8
        assert ann.opinion == (217, 13, 25, 128)

    def test_encode_with_context_registry(self):
        """Context registry compresses keys, reducing CBOR size."""
        without_registry = encode(APPENDIX_C_DOC, APPENDIX_C_ANNOTATION)
        with_registry = encode(
            APPENDIX_C_DOC, APPENDIX_C_ANNOTATION,
            context_registry=IOT_CONTEXT_REGISTRY,
        )
        # Integer keys are shorter than string keys in CBOR
        assert len(with_registry) < len(without_registry)

    def test_roundtrip_with_context_registry(self):
        """encode with registry → decode with same registry → original doc."""
        encoded = encode(
            APPENDIX_C_DOC, APPENDIX_C_ANNOTATION,
            context_registry=IOT_CONTEXT_REGISTRY,
        )
        doc, ann = decode(encoded, context_registry=IOT_CONTEXT_REGISTRY)

        for key in APPENDIX_C_DOC:
            assert doc[key] == APPENDIX_C_DOC[key], f"Mismatch on key '{key}'"

    def test_header_only_no_opinion(self):
        """Tier 1 header-only annotation (has_opinion=False) roundtrips."""
        header = Tier1Header(
            compliance_status=ComplianceStatus.NON_COMPLIANT,
            delegation_flag=True,
            has_opinion=False,
            precision_mode=PrecisionMode.BITS_8,
        )
        ann = Annotation(header=header)
        doc = {"@type": "Alert", "severity": "high"}

        encoded = encode(doc, ann)
        doc_out, ann_out = decode(encoded)

        assert doc_out["@type"] == "Alert"
        assert ann_out.header.compliance_status == ComplianceStatus.NON_COMPLIANT
        assert ann_out.header.delegation_flag is True
        assert ann_out.opinion is None

    def test_encode_size_appendix_c(self):
        """Full key+value compression with integer annotation key.

        With the protocol-defined integer annotation key (60000, 3 CBOR
        bytes) instead of a string key ("@annotation", 12 bytes), the
        framing overhead is dramatically reduced.

        Annotation wire cost breakdown:
          - Map key: 3 bytes (CBOR integer 60000)
          - Tag(60000) framing: 4 bytes
          - Annotation payload: 4 bytes (1-byte header + 3-byte opinion)
          - Total: 11 bytes for ~30 bits of semantic information

        The opinion is 3 bytes because û is derived, not transmitted.

        The irreducible instance strings (sensor URI + ISO timestamp)
        consume ~42+ bytes in any encoding — these are instance data,
        not vocabulary terms, so context compression cannot help.
        """
        json_bytes = json.dumps(
            APPENDIX_C_DOC, separators=(',', ':'),
        ).encode('utf-8')
        json_ld_size = len(json_bytes)

        # CBOR-LD baseline: data only with full compression
        cbor_data = IOT_CONTEXT_REGISTRY.compress(APPENDIX_C_DOC)
        cbor_ld_size = len(cbor2.dumps(cbor_data))

        # CBOR-LD-ex: data + annotation with full compression
        cbor_ld_ex_encoded = encode(
            APPENDIX_C_DOC, APPENDIX_C_ANNOTATION,
            context_registry=IOT_CONTEXT_REGISTRY,
        )
        cbor_ld_ex_size = len(cbor_ld_ex_encoded)

        # 1. CBOR-LD must be dramatically smaller than JSON-LD
        assert cbor_ld_size < json_ld_size * 0.5, (
            f"CBOR-LD ({cbor_ld_size}B) should be <50% of JSON-LD ({json_ld_size}B)"
        )

        # 2. CBOR-LD-ex must still be much smaller than JSON-LD
        assert cbor_ld_ex_size < json_ld_size * 0.6, (
            f"CBOR-LD-ex ({cbor_ld_ex_size}B) should be <60% of JSON-LD ({json_ld_size}B)"
        )

        # 3. Annotation overhead is bounded and small
        #    Integer key (3B) + Tag framing (4B) + payload (5B) = 12B
        overhead = cbor_ld_ex_size - cbor_ld_size
        assert overhead <= 15, (
            f"Annotation overhead {overhead}B exceeds 15B bound"
        )
        assert overhead >= 4, (
            f"Annotation overhead {overhead}B suspiciously small"
        )

        # 4. The annotation payload is 4 bytes of actual semantic content
        #    (1 header + 3 opinion: b̂, d̂, â. û is NOT transmitted.)
        ann_payload_size = len(encode_annotation(APPENDIX_C_ANNOTATION))
        assert ann_payload_size == 4  # 1 header + 3 opinion (no û)
        framing_cost = overhead - ann_payload_size
        # Integer key + tag framing should be ~7 bytes
        assert framing_cost < 10, (
            f"CBOR framing cost {framing_cost}B is excessive"
        )

    def test_encode_16bit_opinion(self):
        """Tier 1 with 16-bit precision roundtrips correctly."""
        b_q, d_q, u_q, a_q = quantize_binomial(0.85, 0.05, 0.10, 0.50, precision=16)
        header = Tier1Header(
            compliance_status=ComplianceStatus.COMPLIANT,
            delegation_flag=False,
            has_opinion=True,
            precision_mode=PrecisionMode.BITS_16,
        )
        ann = Annotation(header=header, opinion=(b_q, d_q, u_q, a_q))

        encoded = encode(APPENDIX_C_DOC, ann)
        doc_out, ann_out = decode(encoded)

        assert ann_out.header.precision_mode == PrecisionMode.BITS_16
        assert ann_out.opinion == (b_q, d_q, u_q, a_q)


# ═══════════════════════════════════════════════════════════════════
# 3. FULL ENCODE / DECODE — TIER 2
# ═══════════════════════════════════════════════════════════════════

class TestTier2Codec:
    """Full encode/decode of Tier 2 (edge gateway) messages."""

    def test_encode_fused_message(self):
        """Tier 2 fused opinion with source_count=5 roundtrips."""
        header = Tier2Header(
            compliance_status=ComplianceStatus.COMPLIANT,
            delegation_flag=False,
            has_opinion=True,
            precision_mode=PrecisionMode.BITS_8,
            operator_id=OperatorId.JURISDICTIONAL_MEET,
            reasoning_context=1,  # compliance context
            context_version=3,
            has_multinomial=False,
            sub_tier_depth=0,
            source_count=5,
        )
        ann = Annotation(header=header, opinion=(200, 30, 25, 128))
        doc = {
            "@type": "FusedReading",
            "region": "EU",
            "sensorCount": 5,
        }

        encoded = encode(doc, ann)
        doc_out, ann_out = decode(encoded)

        assert isinstance(ann_out.header, Tier2Header)
        assert ann_out.header.operator_id == OperatorId.JURISDICTIONAL_MEET
        assert ann_out.header.source_count == 5
        assert ann_out.header.reasoning_context == 1
        assert ann_out.opinion == (200, 30, 25, 128)
        assert doc_out["sensorCount"] == 5

    def test_tier2_cumulative_fusion_operator(self):
        """Tier 2 message recording cumulative_fusion operator roundtrips."""
        header = Tier2Header(
            compliance_status=ComplianceStatus.COMPLIANT,
            delegation_flag=False,
            has_opinion=True,
            precision_mode=PrecisionMode.BITS_8,
            operator_id=OperatorId.CUMULATIVE_FUSION,
            reasoning_context=0,
            context_version=1,
            has_multinomial=False,
            sub_tier_depth=0,
            source_count=10,
        )
        ann = Annotation(header=header, opinion=(220, 10, 25, 128))
        doc = {"@type": "AggregatedReading", "sources": 10}

        encoded = encode(doc, ann)
        doc_out, ann_out = decode(encoded)

        assert ann_out.header.operator_id == OperatorId.CUMULATIVE_FUSION
        assert ann_out.header.source_count == 10

    def test_tier2_no_opinion(self):
        """Tier 2 delegation (no opinion) roundtrips."""
        header = Tier2Header(
            compliance_status=ComplianceStatus.INSUFFICIENT,
            delegation_flag=True,
            has_opinion=False,
            precision_mode=PrecisionMode.BITS_8,
            operator_id=OperatorId.NONE,
            reasoning_context=0,
            context_version=0,
            has_multinomial=False,
            sub_tier_depth=0,
            source_count=0,
        )
        ann = Annotation(header=header)
        doc = {"@type": "Delegation", "target": "tier3"}

        encoded = encode(doc, ann)
        doc_out, ann_out = decode(encoded)

        assert ann_out.header.delegation_flag is True
        assert ann_out.header.compliance_status == ComplianceStatus.INSUFFICIENT
        assert ann_out.opinion is None


# ═══════════════════════════════════════════════════════════════════
# 4. FULL ENCODE / DECODE — TIER 3
# ═══════════════════════════════════════════════════════════════════

class TestTier3Codec:
    """Full encode/decode of Tier 3 (cloud) messages.

    Note: Provenance chain encoding is deferred to Phase 5.
    These tests cover Tier 3 with header + opinion only.
    """

    def test_tier3_basic_roundtrip(self):
        """Tier 3 header + opinion roundtrips through full codec."""
        header = Tier3Header(
            compliance_status=ComplianceStatus.COMPLIANT,
            delegation_flag=False,
            has_opinion=True,
            precision_mode=PrecisionMode.BITS_8,
            operator_id=OperatorId.COMPLIANCE_PROPAGATION,
            reasoning_context=1,
            has_extended_context=False,
            has_provenance_chain=False,
            has_multinomial=False,
            has_trust_info=False,
            sub_tier_depth=2,
        )
        ann = Annotation(header=header, opinion=(180, 50, 25, 100))
        doc = {"@type": "ComplianceReport", "jurisdiction": "GDPR"}

        encoded = encode(doc, ann)
        doc_out, ann_out = decode(encoded)

        assert isinstance(ann_out.header, Tier3Header)
        assert ann_out.header.operator_id == OperatorId.COMPLIANCE_PROPAGATION
        assert ann_out.header.sub_tier_depth == 2
        assert ann_out.opinion == (180, 50, 25, 100)
        assert doc_out["jurisdiction"] == "GDPR"

    def test_tier3_32bit_precision(self):
        """Tier 3 with 32-bit float precision (IEEE 754 direct)."""
        header = Tier3Header(
            compliance_status=ComplianceStatus.NON_COMPLIANT,
            delegation_flag=False,
            has_opinion=True,
            precision_mode=PrecisionMode.BITS_32,
            operator_id=OperatorId.ERASURE_PROPAGATION,
            reasoning_context=4,  # erasure context
            has_extended_context=False,
            has_provenance_chain=False,
            has_multinomial=False,
            has_trust_info=False,
            sub_tier_depth=0,
        )
        # 32-bit mode: floats directly, no quantization
        ann = Annotation(header=header, opinion=(0.85, 0.05, 0.10, 0.50))
        doc = {"@type": "ErasureReport"}

        encoded = encode(doc, ann)
        doc_out, ann_out = decode(encoded)

        # Float roundtrip through struct.pack(">ffff") may lose some precision
        b, d, u, a = ann_out.opinion
        assert abs(b - 0.85) < 1e-6
        assert abs(d - 0.05) < 1e-6
        assert abs(u - 0.10) < 1e-6
        assert abs(a - 0.50) < 1e-6


# ═══════════════════════════════════════════════════════════════════
# 5. AXIOM 1 — FULL STACK STRIPPING PROPERTY
# ═══════════════════════════════════════════════════════════════════

class TestAxiom1FullStack:
    """Axiom 1: CBOR-LD-ex → strip annotation → valid CBOR-LD → valid JSON-LD.

    The stripping function σ removes the annotation tag. The remaining
    CBOR content is valid CBOR-LD (a CBOR map of the data document).
    """

    def test_strip_annotation_leaves_valid_cbor(self):
        """Remove annotation key → remaining CBOR map is valid CBOR-LD."""
        encoded = encode(APPENDIX_C_DOC, APPENDIX_C_ANNOTATION)
        cbor_map = cbor2.loads(encoded)

        # Remove the annotation term ID (integer key 60000)
        stripped = {
            k: v for k, v in cbor_map.items()
            if k != ANNOTATION_TERM_ID
        }

        # The stripped map contains all the original data
        assert stripped["@type"] == "TemperatureReading"
        assert stripped["value"] == 22.5

    def test_strip_with_registry_decompress_to_jsonld(self):
        """Full stack: CBOR-LD-ex → strip → decompress → valid JSON-LD."""
        encoded = encode(
            APPENDIX_C_DOC, APPENDIX_C_ANNOTATION,
            context_registry=IOT_CONTEXT_REGISTRY,
        )
        cbor_map = cbor2.loads(encoded)

        # Strip annotation term ID
        stripped = {
            k: v for k, v in cbor_map.items()
            if k != ANNOTATION_TERM_ID
        }

        # Decompress integer keys/values back to strings
        decompressed = IOT_CONTEXT_REGISTRY.decompress(stripped)

        # Result is a valid JSON-LD document (all original fields present)
        assert decompressed["@type"] == "TemperatureReading"
        assert decompressed["@id"] == "urn:sensor:temp-042"
        assert decompressed["value"] == 22.5
        assert decompressed["unit"] == "Celsius"
        assert decompressed["observedAt"] == "2026-03-12T10:00:00Z"

    def test_standard_cbor_parser_ignores_tag(self):
        """A standard CBOR parser sees Tag(60000) and does not fail.

        Per RFC 8949 §3.4: unrecognized tags are presented alongside
        their content. The parser doesn't crash.
        """
        encoded = encode(APPENDIX_C_DOC, APPENDIX_C_ANNOTATION)
        # cbor2.loads on a CBOR-LD-ex message must succeed
        cbor_map = cbor2.loads(encoded)
        assert isinstance(cbor_map, dict)

    def test_json_serializable_after_strip(self):
        """After stripping annotations, the data is JSON-serializable.

        This completes the full chain: CBOR-LD-ex → CBOR-LD → JSON-LD.
        """
        encoded = encode(APPENDIX_C_DOC, APPENDIX_C_ANNOTATION)
        cbor_map = cbor2.loads(encoded)

        stripped = {
            k: v for k, v in cbor_map.items()
            if k != ANNOTATION_TERM_ID
        }

        # Must be JSON-serializable (no CBOR-only types remain)
        # Without registry, data keys are strings — directly JSON-safe.
        json_str = json.dumps(stripped)
        recovered = json.loads(json_str)
        assert recovered["@type"] == "TemperatureReading"


# ═══════════════════════════════════════════════════════════════════
# 6. AXIOM 2 — ALGEBRAIC CLOSURE THROUGH CODEC
# ═══════════════════════════════════════════════════════════════════

class TestAxiom2ClosureThroughCodec:
    """Axiom 2: Encode Tier 1 → decode → fuse → encode as Tier 2.

    Uses the published cumulative_fuse from jsonld-ex, demonstrating
    real integration between the two libraries.
    """

    def test_fuse_two_tier1_to_tier2(self):
        """Two Tier 1 messages → decode → fuse opinions → valid Tier 2 message.

        This is the core Tier 2 gateway operation: receive sensor data,
        combine evidence, emit fused result.
        """
        # Sensor A: 85% belief, 5% disbelief, 10% uncertainty
        ann_a = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.BITS_8,
            ),
            opinion=quantize_binomial(0.85, 0.05, 0.10, 0.50),
        )
        doc_a = {"@type": "TemperatureReading", "value": 22.5, "sensor": "A"}

        # Sensor B: 70% belief, 10% disbelief, 20% uncertainty
        ann_b = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.BITS_8,
            ),
            opinion=quantize_binomial(0.70, 0.10, 0.20, 0.50),
        )
        doc_b = {"@type": "TemperatureReading", "value": 22.3, "sensor": "B"}

        # Encode both Tier 1 messages
        encoded_a = encode(doc_a, ann_a)
        encoded_b = encode(doc_b, ann_b)

        # Decode at the gateway
        _, dec_ann_a = decode(encoded_a)
        _, dec_ann_b = decode(encoded_b)

        # Dequantize to get float opinions for fusion
        omega_a = dequantize_binomial(*dec_ann_a.opinion)
        omega_b = dequantize_binomial(*dec_ann_b.opinion)

        # Fuse using the published jsonld-ex algebra
        sl_a = Opinion(
            belief=omega_a[0], disbelief=omega_a[1],
            uncertainty=omega_a[2], base_rate=omega_a[3],
        )
        sl_b = Opinion(
            belief=omega_b[0], disbelief=omega_b[1],
            uncertainty=omega_b[2], base_rate=omega_b[3],
        )
        fused = cumulative_fuse(sl_a, sl_b)

        # Fused opinion must satisfy SL constraint
        assert math.isclose(
            fused.belief + fused.disbelief + fused.uncertainty, 1.0,
            abs_tol=1e-9,
        )

        # Re-quantize the fused opinion for Tier 2
        fused_q = quantize_binomial(
            fused.belief, fused.disbelief, fused.uncertainty, fused.base_rate,
        )

        # Build Tier 2 annotation
        tier2_ann = Annotation(
            header=Tier2Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.BITS_8,
                operator_id=OperatorId.CUMULATIVE_FUSION,
                reasoning_context=1,
                context_version=1,
                has_multinomial=False,
                sub_tier_depth=0,
                source_count=2,
            ),
            opinion=fused_q,
        )
        tier2_doc = {
            "@type": "FusedReading",
            "meanValue": (22.5 + 22.3) / 2,
            "sources": 2,
        }

        # Encode as Tier 2 — must succeed (closure)
        tier2_encoded = encode(tier2_doc, tier2_ann)
        assert isinstance(tier2_encoded, bytes)

        # Decode and verify
        tier2_doc_out, tier2_ann_out = decode(tier2_encoded)
        assert isinstance(tier2_ann_out.header, Tier2Header)
        assert tier2_ann_out.header.source_count == 2
        assert tier2_ann_out.header.operator_id == OperatorId.CUMULATIVE_FUSION

        # The fused opinion must satisfy Axiom 3 (SL constraint) after
        # going through quantize → encode → decode → dequantize
        fused_out = dequantize_binomial(*tier2_ann_out.opinion)
        assert math.isclose(
            fused_out[0] + fused_out[1] + fused_out[2], 1.0,
            abs_tol=2e-15,
        )

    def test_fuse_reduces_uncertainty(self):
        """Cumulative fusion of independent sources reduces uncertainty.

        This is a fundamental SL property: combining independent evidence
        should yield a less uncertain result.
        """
        # Two moderately uncertain opinions
        omega_1 = Opinion(belief=0.6, disbelief=0.1, uncertainty=0.3, base_rate=0.5)
        omega_2 = Opinion(belief=0.5, disbelief=0.2, uncertainty=0.3, base_rate=0.5)
        fused = cumulative_fuse(omega_1, omega_2)

        assert fused.uncertainty < omega_1.uncertainty
        assert fused.uncertainty < omega_2.uncertainty

        # Encode the fused result through the codec
        fused_q = quantize_binomial(
            fused.belief, fused.disbelief, fused.uncertainty, fused.base_rate,
        )
        ann = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.BITS_8,
            ),
            opinion=fused_q,
        )
        encoded = encode({"@type": "FusedResult"}, ann)
        _, ann_out = decode(encoded)

        # Verify the quantized uncertainty is also lower
        fused_deq = dequantize_binomial(*ann_out.opinion)
        assert fused_deq[2] < 0.3  # lower than either input's uncertainty


# ═══════════════════════════════════════════════════════════════════
# 7. AXIOM 3 — QUANTIZATION INVARIANT THROUGH FULL PIPELINE
# ═══════════════════════════════════════════════════════════════════

class TestAxiom3ThroughCodec:
    """Axiom 3: b + d + u = 1.0 exactly after full encode/decode cycle."""

    def test_constraint_preserved_8bit(self):
        """8-bit quantized opinion through full codec preserves b+d+u=1."""
        opinion_q = quantize_binomial(0.85, 0.05, 0.10, 0.50, precision=8)
        ann = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.BITS_8,
            ),
            opinion=opinion_q,
        )

        encoded = encode({"@type": "Test"}, ann)
        _, ann_out = decode(encoded)

        # Integer-domain constraint (Theorem 1a)
        b_q, d_q, u_q, a_q = ann_out.opinion
        assert b_q + d_q + u_q == 255

        # Float-domain constraint (Theorem 1b)
        b, d, u, a = dequantize_binomial(b_q, d_q, u_q, a_q)
        assert math.isclose(b + d + u, 1.0, abs_tol=2e-15)

    def test_constraint_preserved_16bit(self):
        """16-bit quantized opinion through full codec preserves b+d+u=1."""
        opinion_q = quantize_binomial(0.72, 0.18, 0.10, 0.60, precision=16)
        ann = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.BITS_16,
            ),
            opinion=opinion_q,
        )

        encoded = encode({"@type": "Test"}, ann)
        _, ann_out = decode(encoded)

        b_q, d_q, u_q, a_q = ann_out.opinion
        assert b_q + d_q + u_q == 65535

    @given(
        b=st.floats(min_value=0.0, max_value=1.0),
        d=st.floats(min_value=0.0, max_value=1.0),
    )
    @settings(max_examples=200)
    def test_constraint_property_random_opinions(self, b, d):
        """Property test: random valid opinions preserve constraint through codec."""
        # Construct valid opinion (b + d ≤ 1)
        if b + d > 1.0:
            return  # skip invalid combinations

        u = 1.0 - b - d
        if u < 0:
            return  # floating-point edge case

        opinion_q = quantize_binomial(b, d, u, 0.5)
        ann = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.BITS_8,
            ),
            opinion=opinion_q,
        )

        encoded = encode({"@type": "Test"}, ann)
        _, ann_out = decode(encoded)

        b_q, d_q, u_q, a_q = ann_out.opinion
        assert b_q + d_q + u_q == 255, (
            f"Axiom 3 violated: {b_q} + {d_q} + {u_q} = {b_q + d_q + u_q} ≠ 255"
        )


# ═══════════════════════════════════════════════════════════════════
# 8. PAYLOAD SIZE COMPARISON
# ═══════════════════════════════════════════════════════════════════

class TestBitLevelAnalysis:
    """Information-theoretic bit-level analysis of annotation encoding.

    Shannon (1948): H(X) = log2(|state_space|) gives the minimum bits
    needed to encode a field. Bit efficiency = H / wire_bits measures
    how close the encoding is to this theoretical minimum.

    This is the scientifically rigorous metric — not just byte counting.
    """

    def test_tier1_header_information_content(self):
        """Tier 1 header carries ~6.76 bits of information in 8 bits.

        Fields: compliance_status(3), delegation_flag(2), origin_tier(3),
        has_opinion(2), precision_mode(3).
        H = log2(3)+log2(2)+log2(3)+log2(2)+log2(3) ≈ 6.755 bits.
        Wire cost: 8 bits. Efficiency: ~84.4%.
        """
        result = annotation_information_bits(APPENDIX_C_ANNOTATION)
        expected_header = (
            math.log2(3)   # compliance_status
            + math.log2(2) # delegation_flag
            + math.log2(3) # origin_tier
            + math.log2(2) # has_opinion
            + math.log2(4) # precision_mode (4 defined: 8/16/32/delta)
        )
        assert math.isclose(result["header_info_bits"], expected_header, rel_tol=1e-9)
        assert result["header_wire_bits"] == 8
        assert result["header_efficiency"] > 0.84

    def test_8bit_opinion_information_content(self):
        """8-bit opinion carries ~23.01 bits of information in 24 wire bits.

        (b̂, d̂) are jointly constrained: b̂ + d̂ ≤ 255.
        Valid pairs = 256×257/2 = 32896 → log2(32896) ≈ 15.006 bits.
        â is unconstrained: 256 values → 8 bits.
        û is derived: 0 additional bits, NOT transmitted.
        Total: ~23.006 bits in 24 wire bits. Efficiency: ~95.9%.
        """
        result = annotation_information_bits(APPENDIX_C_ANNOTATION)
        expected_bd = math.log2(256 * 257 // 2)
        expected_a = math.log2(256)
        expected_opinion = expected_bd + expected_a

        assert math.isclose(
            result["opinion_info_bits"], expected_opinion, rel_tol=1e-9,
        )
        assert result["opinion_wire_bits"] == 24  # 3 bytes, not 4
        assert result["opinion_efficiency"] > 0.95  # near-optimal

    def test_tier1_total_bit_efficiency(self):
        """Tier 1 annotation: ~29.76 bits of info in 32 wire bits.

        Bit efficiency > 93% — only ~2.2 bits of waste from byte-
        alignment of non-power-of-2 state counts. This is near-optimal.
        """
        result = annotation_information_bits(APPENDIX_C_ANNOTATION)
        assert result["total_wire_bits"] == 32  # 4 bytes (was 5 before û removal)
        assert result["total_info_bits"] > 29.5
        assert result["bit_efficiency"] > 0.93

    def test_tier2_header_near_optimal(self):
        """Tier 2 header packs ~30.46 bits into 32 bits = 95.2% efficiency.

        The Tier 2 header is near-optimal: almost every bit carries
        information. The ~1.5 bit waste comes from compliance_status(3)
        in 2 bits, origin_tier(3) in 2 bits, precision_mode(3) in 2 bits,
        and operator_id(13) in 4 bits.
        """
        tier2_ann = Annotation(
            header=Tier2Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=False,
                precision_mode=PrecisionMode.BITS_8,
                operator_id=OperatorId.JURISDICTIONAL_MEET,
                reasoning_context=1,
                context_version=3,
                has_multinomial=False,
                sub_tier_depth=0,
                source_count=5,
            ),
        )
        result = annotation_information_bits(tier2_ann)
        assert result["header_wire_bits"] == 32
        assert result["header_info_bits"] > 30.0
        assert result["header_efficiency"] > 0.95

    def test_16bit_opinion_higher_info(self):
        """16-bit opinion carries more information than 8-bit."""
        ann_8 = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.BITS_8,
            ),
            opinion=(217, 13, 25, 128),
        )
        ann_16 = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.BITS_16,
            ),
            opinion=(55705, 3277, 6553, 32768),
        )
        r8 = annotation_information_bits(ann_8)
        r16 = annotation_information_bits(ann_16)

        assert r16["opinion_info_bits"] > r8["opinion_info_bits"]
        assert r16["opinion_wire_bits"] > r8["opinion_wire_bits"]
        # Both should be near-optimal now that û is not transmitted
        assert r8["opinion_efficiency"] > 0.95
        assert r16["opinion_efficiency"] > 0.95

    def test_header_only_no_opinion_info(self):
        """Annotation with has_opinion=False has zero opinion bits."""
        ann = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=False,
                precision_mode=PrecisionMode.BITS_8,
            ),
        )
        result = annotation_information_bits(ann)
        assert result["opinion_info_bits"] == 0.0
        assert result["opinion_wire_bits"] == 0
        assert result["total_wire_bits"] == 8  # header only

    def test_per_field_breakdown_sums_correctly(self):
        """Per-field info bits sum to the reported totals."""
        result = annotation_information_bits(APPENDIX_C_ANNOTATION)
        fields = result["fields"]

        header_fields_sum = sum(
            v for k, v in fields.items() if k != "opinion_tuple"
        )
        assert math.isclose(
            header_fields_sum, result["header_info_bits"], rel_tol=1e-9,
        )

        total = header_fields_sum + fields.get("opinion_tuple", 0.0)
        assert math.isclose(
            total, result["total_info_bits"], rel_tol=1e-9,
        )


class TestDeltaBitAnalysis:
    """Delta mode (§7.6) bit-level analysis.

    §11.2: Delta Tier 1 = 23.170 Shannon bits in 24 wire bits = 96.5%.
    Delta opinion = 16 info bits in 16 wire bits = 100% efficiency.
    These are verified claims from §11.4 claim 6.
    """

    def test_delta_opinion_100_percent_efficient(self):
        """Delta opinion: log₂(256) + log₂(256) = 16 bits in 16 wire bits.

        Each signed int8 delta has 256 states = 8 bits of information.
        Two deltas = 16 bits. Wire cost = 2 bytes = 16 bits. Zero waste.
        """
        ann = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.DELTA_8,
            ),
            opinion=(5, -3),
        )
        result = annotation_information_bits(ann)
        assert math.isclose(result["opinion_info_bits"], 16.0, rel_tol=1e-9)
        assert result["opinion_wire_bits"] == 16
        assert math.isclose(result["opinion_efficiency"], 1.0, rel_tol=1e-9)

    def test_delta_tier1_total_96_5_percent(self):
        """§11.2: 7.170 + 16.000 = 23.170 bits in 24 wire bits = 96.5%."""
        ann = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.DELTA_8,
            ),
            opinion=(5, -3),
        )
        result = annotation_information_bits(ann)
        expected_total = (
            math.log2(3) + math.log2(2) + math.log2(3)
            + math.log2(2) + math.log2(4) + 16.0
        )
        assert math.isclose(
            result["total_info_bits"], expected_total, rel_tol=1e-9,
        )
        assert result["total_wire_bits"] == 24
        # §11.4 claim 6: 96.5% delta efficiency
        assert result["bit_efficiency"] > 0.965
        assert result["bit_efficiency"] < 0.97

    def test_delta_higher_efficiency_than_full_8bit(self):
        """Delta mode achieves higher bit efficiency than full 8-bit.

        §11.4 claim 6: 96.5% (delta) vs 94.3% (full 8-bit).
        Both smaller AND higher information density.
        """
        full_ann = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.BITS_8,
            ),
            opinion=(217, 13, 25, 128),
        )
        delta_ann = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.DELTA_8,
            ),
            opinion=(5, -3),
        )
        full_result = annotation_information_bits(full_ann)
        delta_result = annotation_information_bits(delta_ann)
        assert delta_result["bit_efficiency"] > full_result["bit_efficiency"]

    def test_delta_smaller_wire_than_full(self):
        """Delta annotation (3 bytes) is smaller than full 8-bit (4 bytes)."""
        delta_ann = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.DELTA_8,
            ),
            opinion=(5, -3),
        )
        full_ann = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.BITS_8,
            ),
            opinion=(217, 13, 25, 128),
        )
        assert annotation_information_bits(delta_ann)["total_wire_bits"] == 24
        assert annotation_information_bits(full_ann)["total_wire_bits"] == 32


class TestDeltaPayloadComparison:
    """payload_comparison() with delta annotations."""

    def test_delta_comparison_produces_valid_result(self):
        """payload_comparison with delta annotation doesn't crash,
        produces all expected keys."""
        delta_ann = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.DELTA_8,
            ),
            opinion=(5, -3),
        )
        doc = {"@type": "TemperatureReading", "value": 22.5}
        result = payload_comparison(doc, delta_ann)

        assert "json_ld_size" in result
        assert "cbor_ld_ex_size" in result
        assert "annotation_analysis" in result
        assert result["cbor_ld_ex_annotation_bit_efficiency"] > 0.96

    def test_delta_cbor_ld_ex_still_smaller(self):
        """Core thesis holds for delta: CBOR-LD-ex < CBOR-LD for same info."""
        delta_ann = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.DELTA_8,
            ),
            opinion=(5, -3),
        )
        doc = {"@type": "TemperatureReading", "value": 22.5}
        result = payload_comparison(doc, delta_ann)
        assert result["cbor_ld_ex_smaller_than_cbor_ld"] is True


class TestPayloadComparison:
    """payload_comparison() — byte-level, bit-level, and fair comparison."""

    def test_returns_byte_level_keys(self):
        """Result contains all byte-level fields."""
        result = payload_comparison(APPENDIX_C_DOC, APPENDIX_C_ANNOTATION)
        assert "json_ld_size" in result
        assert "cbor_ld_size" in result
        assert "cbor_ld_with_ann_size" in result
        assert "cbor_ld_ex_size" in result

    def test_returns_bit_level_keys(self):
        """Result contains all bit-level analysis fields."""
        result = payload_comparison(APPENDIX_C_DOC, APPENDIX_C_ANNOTATION)
        assert "annotation_info_bits" in result
        assert "jsonld_annotation_bits" in result
        assert "cbor_ld_annotation_bits" in result
        assert "cbor_ld_ex_annotation_bits" in result
        assert "jsonld_annotation_bit_efficiency" in result
        assert "cbor_ld_annotation_bit_efficiency" in result
        assert "cbor_ld_ex_annotation_bit_efficiency" in result
        assert "annotation_analysis" in result

    def test_cbor_ld_ex_smaller_than_cbor_ld_with_same_info(self):
        """THE CORE RESULT: CBOR-LD-ex < CBOR-LD for the SAME information.

        When both carry the same annotation semantics (compliance status,
        SL opinion, operator provenance), CBOR-LD-ex's bit-packing
        produces a strictly smaller message than standard CBOR-LD's
        key-value encoding. This is the entire thesis.
        """
        result = payload_comparison(APPENDIX_C_DOC, APPENDIX_C_ANNOTATION)
        assert result["cbor_ld_ex_smaller_than_cbor_ld"] is True
        assert result["cbor_ld_ex_size"] < result["cbor_ld_with_ann_size"]

    def test_cbor_ld_ex_still_smaller_than_json_ld(self):
        """CBOR-LD-ex with full annotations is still smaller than JSON-LD."""
        result = payload_comparison(APPENDIX_C_DOC, APPENDIX_C_ANNOTATION)
        assert result["cbor_ld_ex_size"] < result["json_ld_size"]

    def test_annotation_ratio_jsonld_vs_ex(self):
        """Bit-packed annotation achieves >30× compression over JSON-LD.

        The 4-byte Tier 1 annotation (32 bits) replaces ~148 bytes
        (1184 bits) of JSON-LD text. Ratio: ~37×.
        """
        result = payload_comparison(APPENDIX_C_DOC, APPENDIX_C_ANNOTATION)
        assert result["annotation_ratio_jsonld_vs_ex"] > 30.0

    def test_annotation_ratio_cbor_ld_vs_ex(self):
        """Bit-packed annotation beats standard CBOR-LD by >10×.

        Even with fully compressed integer keys, standard CBOR-LD still
        uses 4+ bytes per float for the opinion, plus map/nested-map
        overhead. Bit-packing the constrained opinion into 3 bytes total
        (using the SL invariant b+d+u=1 to elide û) is fundamentally
        more efficient.
        """
        result = payload_comparison(APPENDIX_C_DOC, APPENDIX_C_ANNOTATION)
        assert result["annotation_ratio_cbor_ld_vs_ex"] > 10.0

    def test_three_tier_bit_efficiency(self):
        """CBOR-LD-ex >93%, CBOR-LD ~7–10%, JSON-LD <5% bit efficiency.

        Same Shannon information content, three radically different
        encodings. JSON-LD wastes >95% on ASCII syntax. CBOR-LD wastes
        ~90% on float overhead. CBOR-LD-ex wastes <7% (byte-alignment
        from non-power-of-2 state counts — near information-theoretic
        optimum).
        """
        result = payload_comparison(APPENDIX_C_DOC, APPENDIX_C_ANNOTATION)
        assert result["cbor_ld_ex_annotation_bit_efficiency"] > 0.93
        assert result["cbor_ld_annotation_bit_efficiency"] < 0.15
        assert result["jsonld_annotation_bit_efficiency"] < 0.05

    def test_comparison_with_context_registry(self):
        """Context registry improves byte-level data compression."""
        without = payload_comparison(APPENDIX_C_DOC, APPENDIX_C_ANNOTATION)
        with_reg = payload_comparison(
            APPENDIX_C_DOC, APPENDIX_C_ANNOTATION,
            context_registry=IOT_CONTEXT_REGISTRY,
        )
        assert with_reg["cbor_ld_size"] <= without["cbor_ld_size"]
        assert with_reg["cbor_ld_ex_size"] <= without["cbor_ld_ex_size"]
        # Bit-level annotation efficiency is independent of data compression
        assert math.isclose(
            with_reg["cbor_ld_ex_annotation_bit_efficiency"],
            without["cbor_ld_ex_annotation_bit_efficiency"],
            rel_tol=1e-9,
        )

    def test_semantic_completeness_field(self):
        """CBOR-LD-ex carries structured semantic fields that others lack."""
        result = payload_comparison(APPENDIX_C_DOC, APPENDIX_C_ANNOTATION)
        sem = result["semantic_fields"]
        assert sem["json_ld"] == ["data"]
        assert sem["cbor_ld"] == ["data"]
        assert "compliance_status" in sem["cbor_ld_ex"]
        assert "opinion" in sem["cbor_ld_ex"]


# ═══════════════════════════════════════════════════════════════════
# 9. EDGE CASES AND ERROR HANDLING
# ═══════════════════════════════════════════════════════════════════

class TestCodecEdgeCases:
    """Edge cases and error handling in the full codec."""

    def test_empty_document(self):
        """Empty document {} roundtrips."""
        ann = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.INSUFFICIENT,
                delegation_flag=False,
                has_opinion=False,
                precision_mode=PrecisionMode.BITS_8,
            ),
        )
        encoded = encode({}, ann)
        doc_out, ann_out = decode(encoded)
        assert doc_out == {}
        assert ann_out.header.compliance_status == ComplianceStatus.INSUFFICIENT

    def test_document_with_nested_values(self):
        """Documents with nested dicts/lists roundtrip correctly."""
        doc = {
            "@type": "SensorArray",
            "readings": [22.5, 23.1, 21.8],
            "metadata": {"location": "room-42", "floor": 3},
        }
        ann = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.BITS_8,
            ),
            opinion=(200, 30, 25, 128),
        )

        encoded = encode(doc, ann)
        doc_out, _ = decode(encoded)

        assert doc_out["readings"] == [22.5, 23.1, 21.8]
        assert doc_out["metadata"]["location"] == "room-42"
        assert doc_out["metadata"]["floor"] == 3

    def test_document_with_unicode(self):
        """Unicode strings in document roundtrip through CBOR."""
        doc = {"@type": "Label", "name": "Ünïcödë Tëst 日本語"}
        ann = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=False,
                precision_mode=PrecisionMode.BITS_8,
            ),
        )

        encoded = encode(doc, ann)
        doc_out, _ = decode(encoded)
        assert doc_out["name"] == "Ünïcödë Tëst 日本語"

    def test_decode_corrupted_annotation_raises(self):
        """Corrupted annotation bytes should raise a meaningful error."""
        # Valid CBOR map with a broken annotation tag
        broken_map = {
            "@type": "Test",
            ANNOTATION_TERM_ID: cbor2.CBORTag(CBOR_TAG_CBORLD_EX, b"\xff\xff"),
        }
        broken_cbor = cbor2.dumps(broken_map)

        with pytest.raises((ValueError, Exception)):
            decode(broken_cbor)

    def test_large_document(self):
        """Codec handles larger documents without failure."""
        doc = {f"field_{i}": f"value_{i}" for i in range(100)}
        doc["@type"] = "LargePayload"

        ann = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.BITS_8,
            ),
            opinion=(200, 30, 25, 128),
        )

        encoded = encode(doc, ann)
        doc_out, ann_out = decode(encoded)

        assert doc_out["@type"] == "LargePayload"
        assert len(doc_out) == 101  # 100 fields + @type
        assert ann_out.opinion == (200, 30, 25, 128)

    def test_vacuous_opinion_through_codec(self):
        """Vacuous opinion ω_V = (0, 0, 1, 0.5) roundtrips correctly.

        The vacuous opinion represents complete ignorance — an important
        initial state for new devices joining the network.
        """
        opinion_q = quantize_binomial(0.0, 0.0, 1.0, 0.5)
        ann = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.INSUFFICIENT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.BITS_8,
            ),
            opinion=opinion_q,
        )

        encoded = encode({"@type": "NewDevice"}, ann)
        _, ann_out = decode(encoded)

        b_q, d_q, u_q, a_q = ann_out.opinion
        assert b_q == 0
        assert d_q == 0
        assert u_q == 255  # all uncertainty
        assert b_q + d_q + u_q == 255

    def test_extreme_belief_through_codec(self):
        """Dogmatic opinion (1, 0, 0, 0.5) roundtrips."""
        opinion_q = quantize_binomial(1.0, 0.0, 0.0, 0.5)
        ann = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.BITS_8,
            ),
            opinion=opinion_q,
        )

        encoded = encode({"@type": "Certain"}, ann)
        _, ann_out = decode(encoded)

        b_q, d_q, u_q, a_q = ann_out.opinion
        assert b_q == 255
        assert d_q == 0
        assert u_q == 0


# ═══════════════════════════════════════════════════════════════════
# 10. DELTA STREAM INTEGRATION — codec + stream end-to-end
# ═══════════════════════════════════════════════════════════════════

from cbor_ld_ex.stream import (
    DeltaStreamDecoder,
    StreamResult,
    DeltaWithoutBaselineError,
    DeltaConstraintError,
)


class TestDeltaStreamIntegration:
    """Full pipeline: encode → wire → decode → stream decoder → full opinion.

    Verifies that delta annotations survive the complete CBOR-LD-ex
    wire format and reconstruct correctly through the stateful decoder.
    This is the end-to-end proof that our efficiency claims (§11.4)
    hold while maintaining correct semantics.
    """

    def test_full_then_delta_end_to_end(self):
        """I-frame → P-frame through full codec + stream decoder."""
        decoder = DeltaStreamDecoder()
        doc = {"@type": "TemperatureReading", "value": 22.5}

        # I-frame: full 8-bit opinion
        full_ann = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.BITS_8,
            ),
            opinion=(200, 30, 25, 128),
        )
        wire_full = encode(doc, full_ann)
        _, decoded_full = decode(wire_full)
        r1 = decoder.process(decoded_full)

        assert r1.was_delta is False
        assert r1.reconstructed == (200, 30, 25, 128)

        # P-frame: delta opinion
        delta_ann = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.DELTA_8,
            ),
            opinion=(5, -3),
        )
        wire_delta = encode(doc, delta_ann)
        _, decoded_delta = decode(wire_delta)
        r2 = decoder.process(decoded_delta)

        assert r2.was_delta is True
        assert r2.wire_annotation.opinion == (5, -3)  # wire truth
        assert r2.reconstructed == (205, 27, 23, 128)  # full opinion

    def test_delta_wire_smaller_than_full(self):
        """Delta message is strictly smaller on the wire.

        §11.5: Tier 1 delta = 3 bytes annotation, full = 4 bytes.
        The CBOR framing is identical — only the payload shrinks.
        """
        doc = {"@type": "TemperatureReading", "value": 22.5}

        full_ann = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.BITS_8,
            ),
            opinion=(200, 30, 25, 128),
        )
        delta_ann = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.DELTA_8,
            ),
            opinion=(5, -3),
        )

        wire_full = encode(doc, full_ann)
        wire_delta = encode(doc, delta_ann)
        assert len(wire_delta) < len(wire_full)

    def test_axiom3_preserved_through_stream(self):
        """Axiom 3: b̂ + d̂ + û = 2ⁿ−1 after full pipeline with delta.

        The integer-domain constraint must survive:
          quantize → encode → wire → decode → stream reconstruct
        """
        decoder = DeltaStreamDecoder()
        doc = {"@type": "Test"}

        # Keyframe
        kf = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.BITS_8,
            ),
            opinion=quantize_binomial(0.85, 0.05, 0.10, 0.50),
        )
        _, dec_kf = decode(encode(doc, kf))
        decoder.process(dec_kf)

        # Delta
        delta = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.DELTA_8,
            ),
            opinion=(-10, 5),
        )
        _, dec_delta = decode(encode(doc, delta))
        result = decoder.process(dec_delta)

        b, d, u, a = result.reconstructed
        assert b + d + u == 255

    def test_sequential_deltas_through_codec(self):
        """Chain of 5 P-frames through full codec, all reconstruct correctly."""
        decoder = DeltaStreamDecoder()
        doc = {"@type": "Sensor", "value": 22.5}

        # I-frame
        kf_opinion = (100, 100, 55, 128)
        kf = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.BITS_8,
            ),
            opinion=kf_opinion,
        )
        _, dec = decode(encode(doc, kf))
        decoder.process(dec)

        # 5 sequential deltas simulating slowly drifting sensor
        deltas = [(3, -1), (-2, 2), (5, -5), (0, 1), (-1, 0)]
        expected_b, expected_d = 100, 100

        for delta_b, delta_d in deltas:
            expected_b += delta_b
            expected_d += delta_d
            expected_u = 255 - expected_b - expected_d

            d_ann = Annotation(
                header=Tier1Header(
                    compliance_status=ComplianceStatus.COMPLIANT,
                    delegation_flag=False,
                    has_opinion=True,
                    precision_mode=PrecisionMode.DELTA_8,
                ),
                opinion=(delta_b, delta_d),
            )
            _, dec = decode(encode(doc, d_ann))
            result = decoder.process(dec)

            assert result.reconstructed == (
                expected_b, expected_d, expected_u, 128
            )
            assert result.reconstructed[0] + result.reconstructed[1] + result.reconstructed[2] == 255

    def test_keyframe_reset_through_codec(self):
        """New I-frame mid-stream resets baseline through full pipeline."""
        decoder = DeltaStreamDecoder()
        doc = {"@type": "Sensor"}

        # I-frame 1
        kf1 = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.BITS_8,
            ),
            opinion=(200, 30, 25, 128),
        )
        _, dec = decode(encode(doc, kf1))
        decoder.process(dec)

        # P-frame
        d1 = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.DELTA_8,
            ),
            opinion=(10, -10),
        )
        _, dec = decode(encode(doc, d1))
        r = decoder.process(dec)
        assert r.reconstructed == (210, 20, 25, 128)

        # I-frame 2 — new baseline
        kf2 = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.BITS_8,
            ),
            opinion=(50, 50, 155, 64),
        )
        _, dec = decode(encode(doc, kf2))
        decoder.process(dec)

        # P-frame on new baseline
        d2 = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.DELTA_8,
            ),
            opinion=(5, 5),
        )
        _, dec = decode(encode(doc, d2))
        r = decoder.process(dec)
        assert r.reconstructed == (55, 55, 145, 64)

    def test_tier2_delta_through_codec(self):
        """Tier 2 delta through full codec preserves all header fields."""
        decoder = DeltaStreamDecoder()
        doc = {"@type": "FusedReading", "sources": 3}

        # Tier 2 I-frame
        kf = Annotation(
            header=Tier2Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.BITS_8,
                operator_id=OperatorId.TEMPORAL_DECAY,
                reasoning_context=2,
                context_version=1,
                has_multinomial=False,
                sub_tier_depth=0,
                source_count=3,
            ),
            opinion=(180, 50, 25, 128),
        )
        _, dec = decode(encode(doc, kf))
        decoder.process(dec)

        # Tier 2 P-frame
        delta = Annotation(
            header=Tier2Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.DELTA_8,
                operator_id=OperatorId.TEMPORAL_DECAY,
                reasoning_context=2,
                context_version=1,
                has_multinomial=False,
                sub_tier_depth=0,
                source_count=3,
            ),
            opinion=(10, -5),
        )
        wire = encode(doc, delta)
        _, dec = decode(wire)
        r = decoder.process(dec)

        # Header fields preserved
        assert isinstance(r.wire_annotation.header, Tier2Header)
        assert r.wire_annotation.header.operator_id == OperatorId.TEMPORAL_DECAY
        assert r.wire_annotation.header.source_count == 3
        # Opinion reconstructed
        assert r.reconstructed == (190, 45, 20, 128)

    def test_delta_with_context_registry(self):
        """Delta through codec with context compression — full pipeline."""
        decoder = DeltaStreamDecoder()

        # I-frame with context registry
        kf = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.BITS_8,
            ),
            opinion=(200, 30, 25, 128),
        )
        wire = encode(
            APPENDIX_C_DOC, kf, context_registry=IOT_CONTEXT_REGISTRY,
        )
        doc_out, dec = decode(wire, context_registry=IOT_CONTEXT_REGISTRY)
        decoder.process(dec)

        # Verify data survives
        assert doc_out["@type"] == "TemperatureReading"
        assert doc_out["value"] == 22.5

        # P-frame with same registry
        delta = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.DELTA_8,
            ),
            opinion=(5, -3),
        )
        wire = encode(
            APPENDIX_C_DOC, delta, context_registry=IOT_CONTEXT_REGISTRY,
        )
        doc_out, dec = decode(wire, context_registry=IOT_CONTEXT_REGISTRY)
        r = decoder.process(dec)

        assert doc_out["unit"] == "Celsius"
        assert r.reconstructed == (205, 27, 23, 128)
        assert r.reconstructed[0] + r.reconstructed[1] + r.reconstructed[2] == 255


# ===========================================================================
# §4.7.5 + §11: Provenance Block Shannon Analysis
# ===========================================================================

class TestProvenanceBlockInformationBits:
    """Shannon information analysis for provenance blocks with correction.

    Per-entry bit layout (16 bytes = 128 wire bits, standard):
      Byte 0: [origin_tier:2][operator_id:4][precision_bit_high:1][has_correction:1]
      Bytes 1-3: b̂, d̂, â (opinion, always 8-bit for provenance per §9.4)
      Bytes 4-7: timestamp (uint32)
      Bytes 8-15: prev_digest (64-bit)

    Shannon information per entry (standard):
      origin_tier:       log₂(3)     = 1.585 bits
      operator_id:       log₂(13)    = 3.700 bits
      precision_bit_high: log₂(1)    = 0.000 bits (dead bit, always 0)
      has_correction:    log₂(2)     = 1.000 bits
      opinion(b̂,d̂):    log₂(32896) = 15.006 bits (b̂+d̂≤255 constraint)
      â:                 log₂(256)   = 8.000 bits
      timestamp:         log₂(2³²)   = 32.000 bits
      prev_digest:       log₂(2⁶⁴)   = 64.000 bits
      TOTAL:                          125.291 bits in 128 wire bits = 97.88%

    Waste breakdown: 2.709 bits total
      - origin_tier non-power-of-2:     0.415 bits
      - operator_id non-power-of-2:     0.300 bits
      - precision_bit_high dead bit:    1.000 bits
      - opinion constraint residual:    0.994 bits
    """

    def _make_chain(self, length, corrected_indices=None, audit_grade=False):
        """Build a valid provenance chain."""
        sentinel = AUDIT_CHAIN_ORIGIN_SENTINEL if audit_grade else CHAIN_ORIGIN_SENTINEL
        entries = []
        prev = sentinel
        for i in range(length):
            has_corr = i in (corrected_indices or set())
            e = ProvenanceEntry(
                origin_tier=1, operator_id=i % 13, precision_mode=0,
                b_q=200 - i, d_q=30 + i, a_q=128,
                timestamp=1710230400 + i,
                prev_digest=prev,
                has_correction=has_corr,
                c_b=1 if has_corr else 0,
                c_d=0,
                c_a=1 if has_corr else 0,
            )
            entry_bytes = encode_provenance_entry(e, audit_grade=audit_grade)
            prev = compute_entry_digest(entry_bytes, audit_grade=audit_grade)
            entries.append(e)
        return entries

    def test_per_entry_info_standard(self):
        """Per-entry Shannon info = 125.291 bits in 128 wire bits."""
        entries = self._make_chain(1)
        result = provenance_block_information_bits(entries)
        expected_entry_info = (
            math.log2(3)      # origin_tier
            + math.log2(13)   # operator_id
            + 0.0             # precision_bit_high (dead)
            + math.log2(2)    # has_correction
            + math.log2(256 * 257 // 2)  # opinion (b̂,d̂) constrained
            + math.log2(256)  # â
            + 32.0            # timestamp
            + 64.0            # prev_digest
        )
        assert math.isclose(
            result["per_entry_info_bits"], expected_entry_info, rel_tol=1e-9
        )
        assert result["per_entry_wire_bits"] == 128
        assert math.isclose(
            result["per_entry_efficiency"],
            expected_entry_info / 128,
            rel_tol=1e-9,
        )

    def test_per_entry_info_audit_grade(self):
        """Audit-grade: 189.291 bits in 192 wire bits = 98.59%."""
        entries = self._make_chain(1, audit_grade=True)
        result = provenance_block_information_bits(entries, audit_grade=True)
        expected_entry_info = (
            math.log2(3) + math.log2(13) + 0.0 + math.log2(2)
            + math.log2(256 * 257 // 2) + math.log2(256)
            + 32.0 + 128.0  # 128-bit digest
        )
        assert math.isclose(
            result["per_entry_info_bits"], expected_entry_info, rel_tol=1e-9
        )
        assert result["per_entry_wire_bits"] == 192
        assert result["per_entry_efficiency"] > 0.985

    def test_no_correction_zero_overhead(self):
        """Chain with no corrections: correction overhead is exactly 0."""
        entries = self._make_chain(5)
        result = provenance_block_information_bits(entries)
        assert result["num_corrected"] == 0
        assert result["correction_info_bits"] == 0
        assert result["correction_wire_bits"] == 0
        assert result["correction_pad_bits"] == 0

    def test_all_corrected_L10_overhead_matches_spec(self):
        """§4.7.5 Table: L=10 all corrected → +2.5% overhead.

        10 × 3 = 30 correction bits → ceil(30/8)*8 = 32 wire bits.
        Correction info: 30 bits. Correction wire: 32 bits. Pad: 2 bits.
        Block wire baseline (no corrections): 8 + 10*128 = 1288 bits.
        With corrections: 1288 + 32 = 1320 bits.
        Overhead: 32/1288 = 2.48%.
        """
        entries = self._make_chain(10, corrected_indices=set(range(10)))
        result = provenance_block_information_bits(entries)
        assert result["num_corrected"] == 10
        assert result["correction_info_bits"] == 30
        assert result["correction_wire_bits"] == 32
        assert result["correction_pad_bits"] == 2
        # Overhead percentage
        baseline_wire = 8 + 10 * 128  # chain_length + entries
        overhead_pct = result["correction_wire_bits"] / baseline_wire * 100
        assert abs(overhead_pct - 2.48) < 0.1

    def test_mixed_corrections_pad_accounting(self):
        """Mixed: 3 corrected of 5 → 9 bits → 16 wire → 7 pad bits."""
        entries = self._make_chain(5, corrected_indices={0, 2, 4})
        result = provenance_block_information_bits(entries)
        assert result["num_corrected"] == 3
        assert result["correction_info_bits"] == 9
        assert result["correction_wire_bits"] == 16  # ceil(9/8)*8
        assert result["correction_pad_bits"] == 7

    def test_single_correction_worst_case_pad(self):
        """1 corrected entry: 3 info bits in 8 wire bits = 5 pad bits.

        This is the worst case for correction block padding.
        """
        entries = self._make_chain(3, corrected_indices={1})
        result = provenance_block_information_bits(entries)
        assert result["correction_info_bits"] == 3
        assert result["correction_wire_bits"] == 8
        assert result["correction_pad_bits"] == 5

    def test_eight_corrections_zero_pad(self):
        """8 corrected entries: 24 bits → 24 wire bits → 0 pad. Perfect."""
        entries = self._make_chain(8, corrected_indices=set(range(8)))
        result = provenance_block_information_bits(entries)
        assert result["correction_info_bits"] == 24
        assert result["correction_wire_bits"] == 24
        assert result["correction_pad_bits"] == 0

    def test_total_bits_sum_correctly(self):
        """Total = chain_length + entries + correction. No hidden overhead."""
        entries = self._make_chain(5, corrected_indices={1, 3})
        result = provenance_block_information_bits(entries)
        expected_total_info = (
            result["chain_length_info_bits"]
            + 5 * result["per_entry_info_bits"]
            + result["correction_info_bits"]
        )
        expected_total_wire = (
            result["chain_length_wire_bits"]
            + 5 * result["per_entry_wire_bits"]
            + result["correction_wire_bits"]
        )
        assert math.isclose(
            result["total_info_bits"], expected_total_info, rel_tol=1e-12
        )
        assert result["total_wire_bits"] == expected_total_wire
        assert math.isclose(
            result["bit_efficiency"],
            expected_total_info / expected_total_wire,
            rel_tol=1e-12,
        )

    def test_chain_length_field_perfect_efficiency(self):
        """chain_length is uint8: 256 states in 8 bits = 100% efficient."""
        entries = self._make_chain(3)
        result = provenance_block_information_bits(entries)
        assert result["chain_length_info_bits"] == 8.0
        assert result["chain_length_wire_bits"] == 8

    def test_precision_bit_high_waste_accounted(self):
        """precision_bit_high carries 0 info — must appear in fields breakdown."""
        entries = self._make_chain(1)
        result = provenance_block_information_bits(entries)
        assert result["fields"]["precision_bit_high"] == 0.0
        # It costs 1 wire bit but carries 0 info
        total_byte0_info = (
            result["fields"]["origin_tier"]
            + result["fields"]["operator_id"]
            + result["fields"]["precision_bit_high"]
            + result["fields"]["has_correction"]
        )
        assert total_byte0_info < 8.0  # byte 0 is not perfectly packed
        assert result["fields"]["has_correction"] == 1.0  # but this bit earns its keep

    def test_empty_chain(self):
        """Empty chain: only chain_length byte."""
        result = provenance_block_information_bits([])
        assert result["total_info_bits"] == 8.0  # chain_length only
        assert result["total_wire_bits"] == 8
        assert result["bit_efficiency"] == 1.0
        assert result["num_corrected"] == 0

    def test_standard_entry_efficiency_above_97(self):
        """Standard provenance entry: >97% bit efficiency.

        125.291 / 128 = 97.88%. Every field earns its space.
        The 2.709 bits of waste are from non-power-of-2 state counts
        and the dead precision_bit_high — unavoidable given the byte 0 layout.
        """
        entries = self._make_chain(1)
        result = provenance_block_information_bits(entries)
        assert result["per_entry_efficiency"] > 0.97
        # Total block efficiency (includes chain_length)
        assert result["bit_efficiency"] > 0.97
