"""
Phase 7 tests: Transport adapters (MQTT + CoAP) and benchmark comparison.

Tests are organized in three sections:

1. MQTT transport: CBOR-LD-ex message → MQTT payload, topic, QoS
2. CoAP transport: CBOR-LD-ex message → CoAP payload, content format
3. Benchmark: 6-way comparison proving CBOR-LD-ex is smallest AND richest

The benchmark is the paper deliverable. It proves:
  - CBOR-LD-ex < CBOR-LD < JSON-LD (size)
  - CBOR-LD-ex carries MORE semantic fields than CBOR-LD
  - Our ContextRegistry compression beats jsonld-ex context-only compression
  - Both MQTT and CoAP carry identical CBOR-LD-ex payloads

Depends on:
  - All previous phases (opinions, headers, annotations, temporal, security, codec)
  - jsonld-ex: cbor_ld.to_cbor, mqtt.to_mqtt_payload (baseline comparison)
"""

import json
import math

import cbor2
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from cbor_ld_ex.annotations import Annotation
from cbor_ld_ex.headers import (
    Tier1Header,
    Tier2Header,
    ComplianceStatus,
    OperatorId,
    PrecisionMode,
)
from cbor_ld_ex.opinions import quantize_binomial
from cbor_ld_ex.codec import encode, decode, ContextRegistry

from cbor_ld_ex.transport import (
    # MQTT
    to_mqtt_payload,
    from_mqtt_payload,
    derive_topic,
    derive_qos,
    # CoAP
    to_coap_payload,
    from_coap_payload,
    COAP_CONTENT_FORMAT_CBOR_LD_EX,
    # Benchmark
    full_benchmark,
)

# jsonld-ex baselines
from jsonld_ex.cbor_ld import to_cbor as jex_to_cbor
from jsonld_ex.mqtt import to_mqtt_payload as jex_to_mqtt_payload


# =========================================================================
# Shared test fixtures
# =========================================================================

def _iot_doc():
    """A realistic IoT temperature reading document."""
    return {
        "@context": "https://w3id.org/iot/compliance/v1",
        "@type": "TemperatureReading",
        "@id": "urn:sensor:temp-042",
        "value": 22.5,
        "unit": "Celsius",
        "observedAt": "2026-03-20T10:00:00Z",
    }


def _iot_annotation():
    """A Tier 1 compliance annotation."""
    return Annotation(
        header=Tier1Header(
            compliance_status=ComplianceStatus.COMPLIANT,
            delegation_flag=False,
            has_opinion=True,
            precision_mode=PrecisionMode.BITS_8,
        ),
        opinion=quantize_binomial(0.85, 0.05, 0.10, 0.50),
    )


def _iot_context_registry():
    """Context registry matching the IoT doc."""
    return ContextRegistry(
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


def _tier2_annotation():
    """A Tier 2 fused annotation with operator provenance."""
    return Annotation(
        header=Tier2Header(
            compliance_status=ComplianceStatus.COMPLIANT,
            delegation_flag=False,
            has_opinion=True,
            precision_mode=PrecisionMode.BITS_8,
            operator_id=OperatorId.CUMULATIVE_FUSION,
            reasoning_context=0,
            context_version=1,
            has_multinomial=False,
            sub_tier_depth=0,
            source_count=5,
        ),
        opinion=quantize_binomial(0.80, 0.10, 0.10, 0.50),
    )


# =========================================================================
# 1. MQTT Transport
# =========================================================================

class TestMqttTransport:
    """CBOR-LD-ex over MQTT."""

    def test_mqtt_payload_is_valid_cbor(self):
        """MQTT payload is valid CBOR bytes."""
        doc = _iot_doc()
        ann = _iot_annotation()
        registry = _iot_context_registry()

        payload = to_mqtt_payload(doc, ann, context_registry=registry)
        # Must be parseable as CBOR
        parsed = cbor2.loads(payload)
        assert isinstance(parsed, dict)

    def test_mqtt_roundtrip(self):
        """Encode → MQTT payload → decode recovers doc + annotation."""
        doc = _iot_doc()
        ann = _iot_annotation()
        registry = _iot_context_registry()

        payload = to_mqtt_payload(doc, ann, context_registry=registry)
        recovered_doc, recovered_ann = from_mqtt_payload(
            payload, context_registry=registry,
        )

        assert recovered_doc["value"] == 22.5
        assert recovered_doc["@type"] == "TemperatureReading"
        assert recovered_ann.opinion is not None
        b, d, u, a = recovered_ann.opinion
        assert b + d + u == 255  # Axiom 3

    def test_mqtt_payload_is_cbor_ld_ex(self):
        """MQTT payload is exactly the CBOR-LD-ex codec output."""
        doc = _iot_doc()
        ann = _iot_annotation()
        registry = _iot_context_registry()

        mqtt_payload = to_mqtt_payload(doc, ann, context_registry=registry)
        codec_output = encode(doc, ann, context_registry=registry)
        assert mqtt_payload == codec_output

    def test_mqtt_topic_derivation(self):
        """Topic derived from @type and @id."""
        doc = _iot_doc()
        ann = _iot_annotation()
        topic = derive_topic(doc, ann)

        assert "TemperatureReading" in topic
        assert "temp-042" in topic

    def test_mqtt_topic_prefix(self):
        """Default topic prefix is 'cbor-ld-ex'."""
        doc = _iot_doc()
        ann = _iot_annotation()
        topic = derive_topic(doc, ann)
        assert topic.startswith("cbor-ld-ex/")

    def test_mqtt_topic_custom_prefix(self):
        """Custom topic prefix."""
        doc = _iot_doc()
        ann = _iot_annotation()
        topic = derive_topic(doc, ann, prefix="iot/compliance")
        assert topic.startswith("iot/compliance/")

    def test_mqtt_qos_from_opinion(self):
        """QoS derived from projected probability of the opinion."""
        doc = _iot_doc()

        # High confidence → QoS 2
        high_ann = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.BITS_8,
            ),
            opinion=quantize_binomial(0.95, 0.02, 0.03, 0.50),
        )
        assert derive_qos(doc, high_ann) == 2

        # Medium confidence → QoS 1
        mid_ann = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.BITS_8,
            ),
            opinion=quantize_binomial(0.60, 0.10, 0.30, 0.50),
        )
        assert derive_qos(doc, mid_ann) == 1

        # Low confidence → QoS 0
        low_ann = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.INSUFFICIENT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.BITS_8,
            ),
            opinion=quantize_binomial(0.10, 0.20, 0.70, 0.50),
        )
        assert derive_qos(doc, low_ann) == 0

    def test_mqtt_qos_no_opinion(self):
        """No opinion → QoS 1 (default)."""
        doc = _iot_doc()
        ann = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=False,
                precision_mode=PrecisionMode.BITS_8,
            ),
        )
        assert derive_qos(doc, ann) == 1

    def test_mqtt_no_registry(self):
        """MQTT payload works without context registry (string keys)."""
        doc = _iot_doc()
        ann = _iot_annotation()

        payload = to_mqtt_payload(doc, ann)
        recovered_doc, recovered_ann = from_mqtt_payload(payload)

        assert recovered_doc["value"] == 22.5
        assert recovered_ann.opinion is not None

    def test_mqtt_tier2_annotation(self):
        """Tier 2 annotation round-trips through MQTT."""
        doc = _iot_doc()
        ann = _tier2_annotation()
        registry = _iot_context_registry()

        payload = to_mqtt_payload(doc, ann, context_registry=registry)
        recovered_doc, recovered_ann = from_mqtt_payload(
            payload, context_registry=registry,
        )

        assert recovered_ann.opinion is not None
        b, d, u, a = recovered_ann.opinion
        assert b + d + u == 255


# =========================================================================
# 2. CoAP Transport
# =========================================================================

class TestCoapTransport:
    """CBOR-LD-ex over CoAP."""

    def test_coap_payload_is_valid_cbor(self):
        """CoAP payload is valid CBOR bytes."""
        doc = _iot_doc()
        ann = _iot_annotation()
        registry = _iot_context_registry()

        payload = to_coap_payload(doc, ann, context_registry=registry)
        parsed = cbor2.loads(payload)
        assert isinstance(parsed, dict)

    def test_coap_roundtrip(self):
        """Encode → CoAP payload → decode recovers doc + annotation."""
        doc = _iot_doc()
        ann = _iot_annotation()
        registry = _iot_context_registry()

        payload = to_coap_payload(doc, ann, context_registry=registry)
        recovered_doc, recovered_ann = from_coap_payload(
            payload, context_registry=registry,
        )

        assert recovered_doc["value"] == 22.5
        assert recovered_ann.opinion is not None
        b, d, u, a = recovered_ann.opinion
        assert b + d + u == 255

    def test_coap_payload_identical_to_mqtt(self):
        """CoAP and MQTT carry the same CBOR-LD-ex payload.

        The transport is just a wrapper — the payload is the codec output.
        """
        doc = _iot_doc()
        ann = _iot_annotation()
        registry = _iot_context_registry()

        mqtt_payload = to_mqtt_payload(doc, ann, context_registry=registry)
        coap_payload = to_coap_payload(doc, ann, context_registry=registry)
        assert mqtt_payload == coap_payload

    def test_coap_content_format(self):
        """CoAP content-format ID is defined and > 10000 (experimental range)."""
        assert isinstance(COAP_CONTENT_FORMAT_CBOR_LD_EX, int)
        assert COAP_CONTENT_FORMAT_CBOR_LD_EX >= 10000

    def test_coap_fits_single_802154_frame(self):
        """Tier 1 CBOR-LD-ex message fits in a single 802.15.4 frame.

        802.15.4 MTU is 127 bytes. CoAP overhead is ~16 bytes for a
        minimal NON message. Payload must be ≤ 111 bytes.
        """
        doc = _iot_doc()
        ann = _iot_annotation()
        registry = _iot_context_registry()

        payload = to_coap_payload(doc, ann, context_registry=registry)
        assert len(payload) <= 111, (
            f"Payload {len(payload)} bytes exceeds 802.15.4 single-frame "
            f"limit (111 bytes after CoAP overhead)"
        )


# =========================================================================
# 3. Benchmark — 6-way comparison
#
# The paper deliverable. Proves CBOR-LD-ex is smallest AND richest.
# =========================================================================

class TestBenchmark:
    """6-way encoding comparison for the IoT scenario."""

    def test_benchmark_returns_all_encodings(self):
        """Benchmark returns size data for all 6 encodings."""
        doc = _iot_doc()
        ann = _iot_annotation()
        registry = _iot_context_registry()

        result = full_benchmark(doc, ann, context_registry=registry)

        assert "json_ld" in result
        assert "jex_cbor_ld" in result
        assert "our_cbor_ld_data_only" in result
        assert "jex_cbor_ld_with_annotation" in result
        assert "our_cbor_ld_with_annotation" in result
        assert "cbor_ld_ex" in result

    def test_json_ld_is_largest(self):
        """JSON-LD is the largest encoding (baseline)."""
        doc = _iot_doc()
        ann = _iot_annotation()
        registry = _iot_context_registry()

        result = full_benchmark(doc, ann, context_registry=registry)
        json_ld_size = result["json_ld"]["size"]

        for name, data in result.items():
            if name != "json_ld":
                assert data["size"] <= json_ld_size, (
                    f"{name} ({data['size']} bytes) is larger than "
                    f"JSON-LD ({json_ld_size} bytes)"
                )

    def test_cbor_ld_ex_smallest_with_annotation(self):
        """CBOR-LD-ex is smaller than any other encoding that carries
        the same annotation information."""
        doc = _iot_doc()
        ann = _iot_annotation()
        registry = _iot_context_registry()

        result = full_benchmark(doc, ann, context_registry=registry)
        ex_size = result["cbor_ld_ex"]["size"]

        # Must be smaller than jsonld-ex CBOR with annotation
        jex_ann_size = result["jex_cbor_ld_with_annotation"]["size"]
        assert ex_size < jex_ann_size, (
            f"CBOR-LD-ex ({ex_size}) not smaller than "
            f"jsonld-ex CBOR+annotation ({jex_ann_size})"
        )

        # Must be smaller than our CBOR-LD with standard CBOR annotation
        our_ann_size = result["our_cbor_ld_with_annotation"]["size"]
        assert ex_size < our_ann_size, (
            f"CBOR-LD-ex ({ex_size}) not smaller than "
            f"our CBOR-LD+annotation ({our_ann_size})"
        )

    def test_our_context_compression_beats_jsonld_ex(self):
        """Our full key+value ContextRegistry compression produces smaller
        payloads than jsonld-ex's context-only compression.

        This is a fair data-only comparison (no annotations).
        """
        doc = _iot_doc()
        ann = _iot_annotation()
        registry = _iot_context_registry()

        result = full_benchmark(doc, ann, context_registry=registry)

        our_size = result["our_cbor_ld_data_only"]["size"]
        jex_size = result["jex_cbor_ld"]["size"]

        assert our_size < jex_size, (
            f"Our CBOR-LD ({our_size}) not smaller than "
            f"jsonld-ex CBOR-LD ({jex_size}). "
            f"Full key+value compression should beat context-only."
        )

    def test_cbor_ld_ex_semantic_fields(self):
        """CBOR-LD-ex carries more semantic fields than any other encoding."""
        doc = _iot_doc()
        ann = _iot_annotation()
        registry = _iot_context_registry()

        result = full_benchmark(doc, ann, context_registry=registry)

        ex_fields = set(result["cbor_ld_ex"]["semantic_fields"])
        jex_fields = set(result["jex_cbor_ld"]["semantic_fields"])

        # CBOR-LD-ex must be a strict superset of jsonld-ex fields
        assert ex_fields > jex_fields, (
            f"CBOR-LD-ex fields {ex_fields} not a strict superset of "
            f"jsonld-ex fields {jex_fields}"
        )

    def test_benchmark_bit_efficiency(self):
        """CBOR-LD-ex annotation bit efficiency > 70%."""
        doc = _iot_doc()
        ann = _iot_annotation()
        registry = _iot_context_registry()

        result = full_benchmark(doc, ann, context_registry=registry)
        efficiency = result["cbor_ld_ex"]["annotation_bit_efficiency"]
        assert efficiency > 0.70, (
            f"Annotation bit efficiency {efficiency:.1%} < 70%"
        )

    def test_benchmark_messages_per_frame(self):
        """CBOR-LD-ex fits more messages per 802.15.4 frame than others."""
        doc = _iot_doc()
        ann = _iot_annotation()
        registry = _iot_context_registry()

        result = full_benchmark(doc, ann, context_registry=registry)

        ex_per_frame = result["cbor_ld_ex"]["msgs_per_802154_frame"]
        json_per_frame = result["json_ld"]["msgs_per_802154_frame"]

        assert ex_per_frame >= json_per_frame

    def test_benchmark_tier2_scenario(self):
        """Benchmark works for Tier 2 (edge gateway) annotations too."""
        doc = _iot_doc()
        ann = _tier2_annotation()
        registry = _iot_context_registry()

        result = full_benchmark(doc, ann, context_registry=registry)

        # Still smallest with annotation
        ex_size = result["cbor_ld_ex"]["size"]
        jex_ann_size = result["jex_cbor_ld_with_annotation"]["size"]
        assert ex_size < jex_ann_size

    def test_benchmark_compression_ratio_vs_jsonld(self):
        """CBOR-LD-ex achieves > 80% compression vs JSON-LD."""
        doc = _iot_doc()
        ann = _iot_annotation()
        registry = _iot_context_registry()

        result = full_benchmark(doc, ann, context_registry=registry)
        ratio = 1.0 - (result["cbor_ld_ex"]["size"] / result["json_ld"]["size"])
        assert ratio > 0.80, (
            f"Compression ratio {ratio:.1%} < 80% vs JSON-LD"
        )
