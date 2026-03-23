"""
Tests for the simulation module — Tier 1 → Tier 2 → Tier 3 pipeline.

Organized in six sections:

1. SENSOR GENERATION — Tier 1 simulated sensors produce valid, deterministic
   readings with quantized opinions and proper CBOR-LD-ex encoding.

2. GATEWAY PROCESSING — Tier 2 gateway applies temporal decay per source age,
   Byzantine-filters outliers, and produces a fused Tier 2 annotation.

3. CLOUD PROCESSING — Tier 3 cloud builds and verifies a provenance chain,
   producing an audit summary.

4. END-TO-END PIPELINE — Full Tier 1 → Tier 2 → Tier 3 pipeline runs
   without error and produces consistent results.

5. TRANSPORT VERIFICATION — MQTT and CoAP adapters produce identical
   CBOR-LD-ex payloads at every hop (transport-agnostic proof).

6. SCIENTIFIC INVARIANTS — Axiom preservation across the pipeline:
   opinions remain valid, quantization constraint holds, encoding
   round-trips correctly at every tier.

Design principles:
  - Deterministic via fixed seed — identical results on every run.
  - One deliberate outlier sensor so Byzantine filtering is exercised.
  - Provenance chain is tamper-verified at Tier 3.
  - Every encoding/decoding hop is verified, not just the final output.

Depends on: all core phases + benchmark module + simulation module.
"""

import math
import time

import pytest

# Module under test
from cbor_ld_ex_benchmark.simulation import (
    # Configuration
    SimulationConfig,
    DEFAULT_CONFIG,
    # Tier 1 — sensor generation
    SensorReading,
    generate_sensor_readings,
    encode_sensor_reading,
    # Tier 2 — gateway processing
    GatewayResult,
    process_gateway,
    # Tier 3 — cloud processing
    CloudAudit,
    process_cloud,
    # End-to-end
    PipelineResult,
    run_pipeline,
)

# Core CBOR-LD-ex imports for independent verification
from cbor_ld_ex.annotations import Annotation, encode_annotation, decode_annotation
from cbor_ld_ex.codec import encode, decode, ContextRegistry
from cbor_ld_ex.headers import (
    Tier1Header,
    Tier2Header,
    ComplianceStatus,
    OperatorId,
    PrecisionMode,
)
from cbor_ld_ex.opinions import quantize_binomial, dequantize_binomial
from cbor_ld_ex.security import (
    ProvenanceEntry,
    verify_provenance_chain,
    CHAIN_ORIGIN_SENTINEL,
)
from cbor_ld_ex.temporal import (
    DECAY_EXPONENTIAL,
    decode_half_life,
)
from cbor_ld_ex.transport import (
    to_mqtt_payload,
    from_mqtt_payload,
    to_coap_payload,
    from_coap_payload,
    derive_topic,
    derive_qos,
)


# =========================================================================
# Section 1: SENSOR GENERATION (Tier 1)
# =========================================================================


class TestSensorGeneration:
    """Tests for generate_sensor_readings()."""

    def test_returns_correct_count(self):
        config = SimulationConfig(sensor_count=8, seed=42)
        readings = generate_sensor_readings(config)
        assert len(readings) == 8

    def test_deterministic_with_same_seed(self):
        config = SimulationConfig(sensor_count=6, seed=42)
        r1 = generate_sensor_readings(config)
        r2 = generate_sensor_readings(config)
        for a, b in zip(r1, r2):
            assert a.doc == b.doc
            assert a.annotation.opinion == b.annotation.opinion

    def test_different_seeds_produce_different_readings(self):
        r1 = generate_sensor_readings(SimulationConfig(sensor_count=4, seed=1))
        r2 = generate_sensor_readings(SimulationConfig(sensor_count=4, seed=2))
        # At least one reading should differ
        values1 = [r.doc["value"] for r in r1]
        values2 = [r.doc["value"] for r in r2]
        assert values1 != values2

    def test_all_readings_are_sensor_readings(self):
        readings = generate_sensor_readings(DEFAULT_CONFIG)
        for r in readings:
            assert isinstance(r, SensorReading)
            assert isinstance(r.doc, dict)
            assert isinstance(r.annotation, Annotation)
            assert isinstance(r.timestamp, (int, float))

    def test_all_docs_are_valid_jsonld(self):
        readings = generate_sensor_readings(DEFAULT_CONFIG)
        for r in readings:
            assert "@context" in r.doc
            assert "@type" in r.doc
            assert "@id" in r.doc
            assert "value" in r.doc

    def test_all_docs_have_unique_ids(self):
        readings = generate_sensor_readings(DEFAULT_CONFIG)
        ids = [r.doc["@id"] for r in readings]
        assert len(set(ids)) == len(ids), f"Duplicate sensor IDs: {ids}"

    def test_all_annotations_are_tier1(self):
        readings = generate_sensor_readings(DEFAULT_CONFIG)
        for r in readings:
            assert isinstance(r.annotation.header, Tier1Header)

    def test_all_annotations_have_opinions(self):
        readings = generate_sensor_readings(DEFAULT_CONFIG)
        for r in readings:
            assert r.annotation.header.has_opinion
            assert r.annotation.opinion is not None
            assert len(r.annotation.opinion) == 4  # (b_q, d_q, u_q, a_q)

    def test_opinions_satisfy_axiom3(self):
        """b̂ + d̂ + û = 2ⁿ − 1 exactly for all sensor opinions."""
        readings = generate_sensor_readings(DEFAULT_CONFIG)
        for r in readings:
            b_q, d_q, u_q, a_q = r.annotation.opinion
            assert b_q + d_q + u_q == 255, \
                f"Axiom 3 violated: {b_q} + {d_q} + {u_q} = {b_q + d_q + u_q}"

    def test_has_one_outlier(self):
        """At least one sensor should have high disbelief (outlier).

        This ensures Byzantine filtering is actually exercised.
        """
        readings = generate_sensor_readings(DEFAULT_CONFIG)
        outliers = []
        for r in readings:
            b_q, d_q, u_q, a_q = r.annotation.opinion
            b, d, u, a = dequantize_binomial(b_q, d_q, u_q, a_q, precision=8)
            if d > 0.5:
                outliers.append(r)
        assert len(outliers) >= 1, "No outlier sensor found"

    def test_timestamps_are_in_the_past(self):
        """All sensor timestamps should be slightly in the past (simulated age)."""
        readings = generate_sensor_readings(DEFAULT_CONFIG)
        for r in readings:
            assert r.timestamp > 0
            # Timestamps should vary (different ages for decay)
        timestamps = [r.timestamp for r in readings]
        assert len(set(timestamps)) > 1, "All timestamps identical — no age variation"

    def test_temperature_values_realistic(self):
        """Temperature readings should be in a plausible range."""
        readings = generate_sensor_readings(DEFAULT_CONFIG)
        for r in readings:
            val = r.doc["value"]
            assert isinstance(val, (int, float))
            assert -50 <= val <= 100, f"Unrealistic temperature: {val}"


class TestSensorEncoding:
    """Tests for encode_sensor_reading()."""

    def test_produces_valid_cbor_ld_ex_bytes(self):
        readings = generate_sensor_readings(DEFAULT_CONFIG)
        for r in readings:
            payload = encode_sensor_reading(r)
            assert isinstance(payload, bytes)
            assert len(payload) > 0

    def test_round_trips_through_codec(self):
        """encode → decode recovers doc and annotation."""
        readings = generate_sensor_readings(DEFAULT_CONFIG)
        for r in readings:
            payload = encode_sensor_reading(r)
            doc, ann = decode(payload, context_registry=r.context_registry)
            assert doc == r.doc
            assert ann.opinion == r.annotation.opinion
            assert ann.header.compliance_status == r.annotation.header.compliance_status

    def test_mqtt_and_coap_identical_payloads(self):
        """Transport-agnostic: MQTT and CoAP carry the same bytes."""
        readings = generate_sensor_readings(DEFAULT_CONFIG)
        for r in readings:
            mqtt_payload = to_mqtt_payload(r.doc, r.annotation, r.context_registry)
            coap_payload = to_coap_payload(r.doc, r.annotation, r.context_registry)
            assert mqtt_payload == coap_payload, "MQTT ≠ CoAP payload"


# =========================================================================
# Section 2: GATEWAY PROCESSING (Tier 2)
# =========================================================================


class TestGatewayProcessing:
    """Tests for process_gateway()."""

    def test_returns_gateway_result(self):
        readings = generate_sensor_readings(DEFAULT_CONFIG)
        result = process_gateway(readings, DEFAULT_CONFIG)
        assert isinstance(result, GatewayResult)

    def test_gateway_produces_tier2_annotation(self):
        readings = generate_sensor_readings(DEFAULT_CONFIG)
        result = process_gateway(readings, DEFAULT_CONFIG)
        assert isinstance(result.annotation.header, Tier2Header)

    def test_gateway_opinion_has_valid_axiom3(self):
        """Fused opinion must satisfy b̂ + d̂ + û = 255."""
        readings = generate_sensor_readings(DEFAULT_CONFIG)
        result = process_gateway(readings, DEFAULT_CONFIG)
        b_q, d_q, u_q, a_q = result.annotation.opinion
        assert b_q + d_q + u_q == 255, \
            f"Axiom 3 violated after fusion: {b_q} + {d_q} + {u_q}"

    def test_gateway_applies_temporal_decay(self):
        """Gateway annotation should have temporal extension metadata."""
        readings = generate_sensor_readings(DEFAULT_CONFIG)
        result = process_gateway(readings, DEFAULT_CONFIG)
        assert result.annotation.extensions is not None
        assert result.annotation.extensions.temporal is not None

    def test_gateway_operator_is_cumulative_fusion(self):
        readings = generate_sensor_readings(DEFAULT_CONFIG)
        result = process_gateway(readings, DEFAULT_CONFIG)
        assert result.annotation.header.operator_id == OperatorId.CUMULATIVE_FUSION

    def test_source_count_reflects_surviving_sensors(self):
        """source_count should equal sensors that survived Byzantine filtering."""
        readings = generate_sensor_readings(DEFAULT_CONFIG)
        result = process_gateway(readings, DEFAULT_CONFIG)
        assert result.annotation.header.source_count == result.surviving_count
        assert result.surviving_count < len(readings), \
            "No sensors filtered — outlier not detected?"
        assert result.surviving_count > 0, "All sensors filtered!"

    def test_byzantine_metadata_present(self):
        """Gateway should record Byzantine filtering metadata."""
        readings = generate_sensor_readings(DEFAULT_CONFIG)
        result = process_gateway(readings, DEFAULT_CONFIG)
        assert result.byzantine_metadata is not None
        assert result.byzantine_metadata.original_count == len(readings)
        assert result.byzantine_metadata.removed_count > 0, \
            "Byzantine filtering removed nothing"
        assert result.byzantine_metadata.removed_count == \
            len(readings) - result.surviving_count

    def test_gateway_encodes_as_valid_cbor_ld_ex(self):
        readings = generate_sensor_readings(DEFAULT_CONFIG)
        result = process_gateway(readings, DEFAULT_CONFIG)
        payload = encode(result.doc, result.annotation,
                         context_registry=result.context_registry)
        assert isinstance(payload, bytes)
        assert len(payload) > 0

    def test_gateway_round_trips(self):
        """Gateway output should round-trip through codec."""
        readings = generate_sensor_readings(DEFAULT_CONFIG)
        result = process_gateway(readings, DEFAULT_CONFIG)
        payload = encode(result.doc, result.annotation,
                         context_registry=result.context_registry)
        doc, ann = decode(payload, context_registry=result.context_registry)
        assert doc == result.doc
        assert ann.opinion == result.annotation.opinion

    def test_fused_belief_higher_than_outlier(self):
        """After filtering the outlier, fused belief should be reasonably high."""
        readings = generate_sensor_readings(DEFAULT_CONFIG)
        result = process_gateway(readings, DEFAULT_CONFIG)
        b_q, d_q, u_q, a_q = result.annotation.opinion
        b, d, u, a = dequantize_binomial(b_q, d_q, u_q, a_q, precision=8)
        # Healthy sensors have high belief; fused result should reflect this
        assert b > 0.3, f"Fused belief {b:.3f} suspiciously low"

    def test_deterministic(self):
        """Same seed → same gateway result."""
        r1 = generate_sensor_readings(DEFAULT_CONFIG)
        g1 = process_gateway(r1, DEFAULT_CONFIG)
        r2 = generate_sensor_readings(DEFAULT_CONFIG)
        g2 = process_gateway(r2, DEFAULT_CONFIG)
        assert g1.annotation.opinion == g2.annotation.opinion
        assert g1.surviving_count == g2.surviving_count


# =========================================================================
# Section 3: CLOUD PROCESSING (Tier 3)
# =========================================================================


class TestCloudProcessing:
    """Tests for process_cloud()."""

    def test_returns_cloud_audit(self):
        readings = generate_sensor_readings(DEFAULT_CONFIG)
        gateway = process_gateway(readings, DEFAULT_CONFIG)
        audit = process_cloud(readings, gateway, DEFAULT_CONFIG)
        assert isinstance(audit, CloudAudit)

    def test_provenance_chain_not_empty(self):
        readings = generate_sensor_readings(DEFAULT_CONFIG)
        gateway = process_gateway(readings, DEFAULT_CONFIG)
        audit = process_cloud(readings, gateway, DEFAULT_CONFIG)
        assert len(audit.provenance_chain) > 0

    def test_provenance_chain_verifies(self):
        """The provenance chain must pass tamper verification."""
        readings = generate_sensor_readings(DEFAULT_CONFIG)
        gateway = process_gateway(readings, DEFAULT_CONFIG)
        audit = process_cloud(readings, gateway, DEFAULT_CONFIG)
        is_valid, error_idx = verify_provenance_chain(audit.provenance_chain)
        assert is_valid, f"Provenance chain invalid at entry {error_idx}"

    def test_first_entry_has_sentinel(self):
        """First provenance entry must have the chain origin sentinel."""
        readings = generate_sensor_readings(DEFAULT_CONFIG)
        gateway = process_gateway(readings, DEFAULT_CONFIG)
        audit = process_cloud(readings, gateway, DEFAULT_CONFIG)
        assert audit.provenance_chain[0].prev_digest == CHAIN_ORIGIN_SENTINEL

    def test_provenance_entries_have_correct_operator_ids(self):
        """Tier 1 entries: NONE. Gateway fusion entry: CUMULATIVE_FUSION."""
        readings = generate_sensor_readings(DEFAULT_CONFIG)
        gateway = process_gateway(readings, DEFAULT_CONFIG)
        audit = process_cloud(readings, gateway, DEFAULT_CONFIG)
        chain = audit.provenance_chain

        # Last entry should be the fusion step
        fusion_entry = chain[-1]
        assert fusion_entry.operator_id == int(OperatorId.CUMULATIVE_FUSION), \
            f"Last entry operator {fusion_entry.operator_id} != CUMULATIVE_FUSION"

        # Earlier entries should be NONE (raw sensor data, no operator applied)
        for entry in chain[:-1]:
            assert entry.operator_id == int(OperatorId.NONE), \
                f"Sensor entry operator {entry.operator_id} != NONE"

    def test_provenance_entry_opinions_match_sources(self):
        """Each Tier 1 provenance entry's opinion should match the
        corresponding surviving sensor's opinion."""
        readings = generate_sensor_readings(DEFAULT_CONFIG)
        gateway = process_gateway(readings, DEFAULT_CONFIG)
        audit = process_cloud(readings, gateway, DEFAULT_CONFIG)
        chain = audit.provenance_chain

        # Sensor entries are chain[:-1], each records (b_q, d_q, a_q)
        # from the surviving sensors
        surviving_opinions = [
            (r.annotation.opinion[0], r.annotation.opinion[1], r.annotation.opinion[3])
            for r in readings
            if r.doc["@id"] in gateway.surviving_sensor_ids
        ]
        for i, entry in enumerate(chain[:-1]):
            assert (entry.b_q, entry.d_q, entry.a_q) in surviving_opinions, \
                f"Provenance entry {i} opinion not found in surviving sensors"

    def test_audit_summary_present(self):
        """Audit should include a human-readable summary."""
        readings = generate_sensor_readings(DEFAULT_CONFIG)
        gateway = process_gateway(readings, DEFAULT_CONFIG)
        audit = process_cloud(readings, gateway, DEFAULT_CONFIG)
        assert isinstance(audit.summary, dict)
        assert "total_sensors" in audit.summary
        assert "surviving_sensors" in audit.summary
        assert "removed_sensors" in audit.summary
        assert "chain_verified" in audit.summary
        assert audit.summary["chain_verified"] is True

    def test_audit_sensor_counts_consistent(self):
        readings = generate_sensor_readings(DEFAULT_CONFIG)
        gateway = process_gateway(readings, DEFAULT_CONFIG)
        audit = process_cloud(readings, gateway, DEFAULT_CONFIG)
        s = audit.summary
        assert s["total_sensors"] == len(readings)
        assert s["surviving_sensors"] == gateway.surviving_count
        assert s["removed_sensors"] == len(readings) - gateway.surviving_count


# =========================================================================
# Section 4: END-TO-END PIPELINE
# =========================================================================


class TestEndToEndPipeline:
    """Tests for run_pipeline()."""

    def test_returns_pipeline_result(self):
        result = run_pipeline(DEFAULT_CONFIG)
        assert isinstance(result, PipelineResult)

    def test_pipeline_has_all_stages(self):
        result = run_pipeline(DEFAULT_CONFIG)
        assert result.sensor_readings is not None
        assert result.gateway_result is not None
        assert result.cloud_audit is not None

    def test_pipeline_sensor_count_matches_config(self):
        config = SimulationConfig(sensor_count=10, seed=42)
        result = run_pipeline(config)
        assert len(result.sensor_readings) == 10

    def test_pipeline_deterministic(self):
        r1 = run_pipeline(DEFAULT_CONFIG)
        r2 = run_pipeline(DEFAULT_CONFIG)
        assert r1.gateway_result.annotation.opinion == \
            r2.gateway_result.annotation.opinion
        assert r1.cloud_audit.summary == r2.cloud_audit.summary

    def test_pipeline_provenance_verifies(self):
        result = run_pipeline(DEFAULT_CONFIG)
        is_valid, _ = verify_provenance_chain(result.cloud_audit.provenance_chain)
        assert is_valid

    def test_pipeline_all_encodings_valid(self):
        """Every hop produces valid CBOR-LD-ex bytes that round-trip."""
        result = run_pipeline(DEFAULT_CONFIG)

        # Tier 1: all sensor payloads
        for r in result.sensor_readings:
            payload = encode_sensor_reading(r)
            doc, ann = decode(payload, context_registry=r.context_registry)
            assert ann.opinion == r.annotation.opinion

        # Tier 2: gateway payload
        g = result.gateway_result
        payload = encode(g.doc, g.annotation, context_registry=g.context_registry)
        doc, ann = decode(payload, context_registry=g.context_registry)
        assert ann.opinion == g.annotation.opinion

    def test_pipeline_compression_demonstrated(self):
        """CBOR-LD-ex payload should be smaller than JSON-LD at every hop."""
        import json
        result = run_pipeline(DEFAULT_CONFIG)

        # Check Tier 1
        for r in result.sensor_readings:
            cbor_size = len(encode_sensor_reading(r))
            json_size = len(json.dumps(r.doc).encode("utf-8"))
            assert cbor_size < json_size, \
                f"Sensor {r.doc['@id']}: CBOR-LD-ex ({cbor_size}B) >= JSON ({json_size}B)"

        # Check Tier 2
        g = result.gateway_result
        cbor_size = len(encode(g.doc, g.annotation, context_registry=g.context_registry))
        json_size = len(json.dumps(g.doc).encode("utf-8"))
        assert cbor_size < json_size


# =========================================================================
# Section 5: TRANSPORT VERIFICATION
# =========================================================================


class TestTransportVerification:
    """Verify transport-agnostic payload identity at every pipeline hop."""

    def test_tier1_mqtt_coap_identical(self):
        """All Tier 1 payloads: MQTT == CoAP."""
        result = run_pipeline(DEFAULT_CONFIG)
        for r in result.sensor_readings:
            mqtt = to_mqtt_payload(r.doc, r.annotation, r.context_registry)
            coap = to_coap_payload(r.doc, r.annotation, r.context_registry)
            assert mqtt == coap, f"Tier 1 {r.doc['@id']}: MQTT ≠ CoAP"

    def test_tier2_mqtt_coap_identical(self):
        """Tier 2 gateway payload: MQTT == CoAP."""
        result = run_pipeline(DEFAULT_CONFIG)
        g = result.gateway_result
        mqtt = to_mqtt_payload(g.doc, g.annotation, g.context_registry)
        coap = to_coap_payload(g.doc, g.annotation, g.context_registry)
        assert mqtt == coap, "Tier 2: MQTT ≠ CoAP"

    def test_mqtt_topic_derivation(self):
        """MQTT topics should be well-formed for all hops."""
        result = run_pipeline(DEFAULT_CONFIG)
        # Tier 1
        for r in result.sensor_readings:
            topic = derive_topic(r.doc, r.annotation)
            assert topic.startswith("cbor-ld-ex/")
            assert "//" not in topic  # no empty segments
        # Tier 2
        g = result.gateway_result
        topic = derive_topic(g.doc, g.annotation)
        assert topic.startswith("cbor-ld-ex/")

    def test_qos_levels_valid(self):
        """QoS levels should be 0, 1, or 2."""
        result = run_pipeline(DEFAULT_CONFIG)
        for r in result.sensor_readings:
            qos = derive_qos(r.doc, r.annotation)
            assert qos in (0, 1, 2)
        g = result.gateway_result
        qos = derive_qos(g.doc, g.annotation)
        assert qos in (0, 1, 2)


# =========================================================================
# Section 6: SCIENTIFIC INVARIANTS
# =========================================================================


class TestSimulationInvariants:
    """Axiom and invariant preservation across the full pipeline."""

    def test_axiom3_preserved_all_hops(self):
        """b̂ + d̂ + û = 255 at every tier — Tier 1 sensors AND Tier 2 fusion."""
        result = run_pipeline(DEFAULT_CONFIG)

        for r in result.sensor_readings:
            b_q, d_q, u_q, a_q = r.annotation.opinion
            assert b_q + d_q + u_q == 255, \
                f"Axiom 3 violated at Tier 1 {r.doc['@id']}"

        b_q, d_q, u_q, a_q = result.gateway_result.annotation.opinion
        assert b_q + d_q + u_q == 255, "Axiom 3 violated at Tier 2 fusion"

    def test_axiom2_valid_opinions_all_hops(self):
        """All dequantized opinions must have b, d, u ∈ [0,1] and sum ≈ 1."""
        result = run_pipeline(DEFAULT_CONFIG)

        all_opinions = [r.annotation.opinion for r in result.sensor_readings]
        all_opinions.append(result.gateway_result.annotation.opinion)

        for opinion in all_opinions:
            b_q, d_q, u_q, a_q = opinion
            b, d, u, a = dequantize_binomial(b_q, d_q, u_q, a_q, precision=8)
            assert 0 <= b <= 1, f"b={b} out of range"
            assert 0 <= d <= 1, f"d={d} out of range"
            assert 0 <= u <= 1, f"u={u} out of range"
            assert 0 <= a <= 1, f"a={a} out of range"
            assert abs(b + d + u - 1.0) < 1e-6, \
                f"b+d+u={b+d+u} ≠ 1.0"

    def test_fusion_reduces_uncertainty(self):
        """Cumulative fusion of concordant opinions should reduce uncertainty.

        This is a fundamental property of Subjective Logic: more concordant
        evidence → less uncertainty. The outlier is removed by Byzantine
        filtering, so the remaining opinions are concordant.
        """
        result = run_pipeline(DEFAULT_CONFIG)

        # Average uncertainty of surviving Tier 1 sensors
        g = result.gateway_result
        surviving_uncertainties = []
        for r in result.sensor_readings:
            if r.doc["@id"] in g.surviving_sensor_ids:
                b_q, d_q, u_q, a_q = r.annotation.opinion
                _, _, u, _ = dequantize_binomial(b_q, d_q, u_q, a_q, precision=8)
                surviving_uncertainties.append(u)

        avg_input_u = sum(surviving_uncertainties) / len(surviving_uncertainties)

        # Fused uncertainty
        b_q, d_q, u_q, a_q = g.annotation.opinion
        _, _, fused_u, _ = dequantize_binomial(b_q, d_q, u_q, a_q, precision=8)

        assert fused_u < avg_input_u, \
            f"Fusion did not reduce uncertainty: {fused_u:.3f} >= avg {avg_input_u:.3f}"

    def test_provenance_chain_length_correct(self):
        """Chain length = surviving sensors + 1 fusion step."""
        result = run_pipeline(DEFAULT_CONFIG)
        expected_len = result.gateway_result.surviving_count + 1
        actual_len = len(result.cloud_audit.provenance_chain)
        assert actual_len == expected_len, \
            f"Chain length {actual_len} != {expected_len} " \
            f"(surviving={result.gateway_result.surviving_count} + 1)"

    def test_encoding_size_tier2_smaller_than_jsonld(self):
        """The Tier 2 fused message should compress well vs JSON-LD."""
        import json
        result = run_pipeline(DEFAULT_CONFIG)
        g = result.gateway_result
        cbor_size = len(encode(g.doc, g.annotation, context_registry=g.context_registry))
        # JSON-LD would also include the annotation as JSON
        full_doc = dict(g.doc)
        full_doc["@annotation"] = {"compliance_status": "compliant"}
        json_size = len(json.dumps(full_doc).encode("utf-8"))
        assert cbor_size < json_size
