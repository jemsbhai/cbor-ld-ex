"""
CBOR-LD-ex Simulation — Tier 1 → Tier 2 → Tier 3 pipeline.

NOT part of the core cbor-ld-ex package. This is repo-only evaluation
tooling demonstrating the full CBOR-LD-ex pipeline across a simulated
IoT network.

Pipeline:
  Tier 1 (Constrained sensors):
    N temperature sensors produce readings with Subjective Logic opinions.
    One sensor is a deliberate outlier (high disbelief) to exercise
    Byzantine filtering. Each reading is encoded as CBOR-LD-ex.

  Tier 2 (Edge gateway):
    Receives all Tier 1 messages. Applies temporal decay per source age
    (older readings are less trustworthy). Runs Byzantine filtering
    (removes the outlier). Fuses surviving opinions via cumulative fusion.
    Emits a single Tier 2 annotation with source_count, operator
    provenance, and temporal/Byzantine metadata.

  Tier 3 (Cloud):
    Receives the Tier 2 message. Builds a provenance chain: one entry
    per surviving sensor + one entry for the fusion step. Verifies
    chain integrity. Produces an audit summary.

Design:
  - Deterministic via seed — identical results on every run.
  - In-process — no actual MQTT/CoAP brokers needed. Transport adapters
    are already test-proven in test_transport.py.
  - All encoding/decoding happens through the real CBOR-LD-ex codec.
  - Opinions flow through the full jsonld-ex algebra (decay, Byzantine
    filter, cumulative fusion) before re-quantization.

References:
  jsonld-ex: confidence_algebra (Opinion, cumulative_fuse)
  jsonld-ex: confidence_decay (decay_opinion, exponential_decay)
  jsonld-ex: confidence_byzantine (byzantine_fuse, ByzantineConfig, cohesion_score)
"""

import random
import time
from dataclasses import dataclass, field
from typing import Optional

from cbor_ld_ex.annotations import Annotation, encode_annotation
from cbor_ld_ex.codec import ContextRegistry, encode, decode, ANNOTATION_TERM_ID
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
    encode_provenance_entry,
    compute_entry_digest,
    ByzantineMetadata,
    CHAIN_ORIGIN_SENTINEL,
    STRATEGY_MOST_CONFLICTING,
    verify_provenance_chain,
)
from cbor_ld_ex.temporal import (
    ExtensionBlock,
    TemporalBlock,
    DECAY_EXPONENTIAL,
    encode_half_life,
    decode_half_life,
    compute_decay_factor,
    apply_decay_quantized,
)

# jsonld-ex algebra — float-domain operations
from jsonld_ex.confidence_algebra import Opinion, cumulative_fuse
from jsonld_ex.confidence_byzantine import (
    byzantine_fuse,
    ByzantineConfig,
    cohesion_score,
)


# =====================================================================
# Configuration
# =====================================================================

@dataclass
class SimulationConfig:
    """Configuration for the simulation pipeline.

    Attributes:
        sensor_count: Number of Tier 1 sensors (including 1 outlier).
        seed: Random seed for deterministic reproducibility.
        base_temperature: Nominal temperature for healthy sensors (°C).
        temperature_noise: Max random deviation from base (°C).
        half_life_seconds: Temporal decay half-life (seconds).
        base_timestamp: Simulated "now" (Unix epoch seconds).
            Defaults to a fixed value for reproducibility.
        max_age_seconds: Maximum simulated sensor reading age (seconds).
        byzantine_threshold: Discord threshold for Byzantine filtering.
    """
    sensor_count: int = 8
    seed: int = 42
    base_temperature: float = 22.5
    temperature_noise: float = 3.0
    half_life_seconds: float = 3600.0  # 1 hour
    base_timestamp: int = 1_711_000_000  # Fixed epoch for reproducibility
    max_age_seconds: float = 600.0  # 10 minutes max age
    byzantine_threshold: float = 0.15


DEFAULT_CONFIG = SimulationConfig()


# =====================================================================
# Instance-specific keys (not compressed by context registry)
# =====================================================================

_INSTANCE_SPECIFIC_KEYS = frozenset({"@id", "observedAt"})


def _build_sensor_registry() -> ContextRegistry:
    """Build a context registry for sensor temperature readings.

    Compresses ALL string keys to integers and vocabulary-level string
    values (context URL, type name, unit) to integers. Instance-specific
    values (@id, observedAt) are left as strings.
    """
    key_map = {
        "@context": 1,
        "@type": 2,
        "@id": 3,
        "value": 4,
        "unit": 5,
        "observedAt": 6,
    }
    value_map = {
        "https://w3id.org/iot/compliance/v1": 500,
        "TemperatureReading": 501,
        "Celsius": 502,
    }
    return ContextRegistry(key_map=key_map, value_map=value_map)


def _build_gateway_registry() -> ContextRegistry:
    """Build a context registry for gateway aggregate readings."""
    key_map = {
        "@context": 1,
        "@type": 2,
        "@id": 3,
        "avgTemperature": 4,
        "sensorCount": 5,
        "observedAt": 6,
    }
    value_map = {
        "https://w3id.org/iot/compliance/v1": 500,
        "GatewayAggregate": 501,
    }
    return ContextRegistry(key_map=key_map, value_map=value_map)


# =====================================================================
# Tier 1 — Sensor generation
# =====================================================================

@dataclass
class SensorReading:
    """A single Tier 1 sensor reading with CBOR-LD-ex annotation.

    Attributes:
        doc: JSON-LD document with the temperature reading.
        annotation: Tier 1 CBOR-LD-ex annotation with quantized opinion.
        timestamp: Simulated reading timestamp (Unix epoch seconds).
        context_registry: Registry for encoding this reading.
    """
    doc: dict
    annotation: Annotation
    timestamp: int
    context_registry: ContextRegistry


def generate_sensor_readings(config: SimulationConfig) -> list:
    """Generate N simulated temperature sensor readings.

    All sensors are healthy EXCEPT the last one, which is a deliberate
    outlier with high disbelief (the sensor is malfunctioning and
    reporting low-confidence, contradictory data). This ensures
    Byzantine filtering is exercised.

    Each sensor reading has:
      - A slightly different temperature (noise around base)
      - A slightly different timestamp (simulating reading age)
      - A Tier 1 annotation with a quantized Subjective Logic opinion

    Healthy sensors: high belief (0.65–0.85), low disbelief, moderate uncertainty
    Outlier sensor: low belief, HIGH disbelief (> 0.5), high uncertainty

    Args:
        config: Simulation configuration.

    Returns:
        List of SensorReading objects (deterministic given seed).
    """
    rng = random.Random(config.seed)
    registry = _build_sensor_registry()
    readings = []

    for i in range(config.sensor_count):
        is_outlier = (i == config.sensor_count - 1)

        # Simulated reading age: spread readings across [0, max_age]
        age_seconds = int(config.max_age_seconds * i / max(1, config.sensor_count - 1))
        reading_timestamp = config.base_timestamp - age_seconds

        # Temperature value
        if is_outlier:
            # Outlier reports an anomalous temperature
            temp = config.base_temperature + config.temperature_noise * 3
        else:
            temp = round(
                config.base_temperature + rng.uniform(
                    -config.temperature_noise, config.temperature_noise
                ),
                1,
            )

        # Opinion
        if is_outlier:
            # High disbelief: sensor is malfunctioning
            b, d, u = 0.1, 0.6, 0.3
        else:
            # Healthy: high belief, slight variation
            b = round(0.65 + rng.uniform(0.0, 0.20), 3)
            u = round(0.10 + rng.uniform(0.0, 0.10), 3)
            d = round(1.0 - b - u, 3)
            # Ensure non-negative
            if d < 0:
                d = 0.0
                u = round(1.0 - b, 3)

        a = 0.5  # neutral base rate
        opinion_q = quantize_binomial(b, d, u, a, precision=8)

        doc = {
            "@context": "https://w3id.org/iot/compliance/v1",
            "@type": "TemperatureReading",
            "@id": f"urn:sensor:temp-{i:03d}",
            "value": temp,
            "unit": "Celsius",
            "observedAt": f"2026-03-20T10:{age_seconds // 60:02d}:{age_seconds % 60:02d}Z",
        }

        annotation = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.BITS_8,
            ),
            opinion=opinion_q,
        )

        readings.append(SensorReading(
            doc=doc,
            annotation=annotation,
            timestamp=reading_timestamp,
            context_registry=registry,
        ))

    return readings


def encode_sensor_reading(reading: SensorReading) -> bytes:
    """Encode a sensor reading to CBOR-LD-ex bytes.

    Args:
        reading: A SensorReading from generate_sensor_readings().

    Returns:
        CBOR-LD-ex encoded bytes.
    """
    return encode(
        reading.doc,
        reading.annotation,
        context_registry=reading.context_registry,
    )


# =====================================================================
# Tier 2 — Gateway processing
#
# Pipeline:
#   1. Dequantize each sensor's opinion to float domain
#   2. Apply temporal decay based on each sensor's age
#   3. Byzantine-filter outliers (using jsonld-ex)
#   4. Cumulative-fuse surviving decayed opinions (using jsonld-ex)
#   5. Re-quantize the fused opinion
#   6. Build Tier 2 annotation with extensions and metadata
# =====================================================================

@dataclass
class GatewayResult:
    """Result of Tier 2 gateway processing.

    Attributes:
        doc: Gateway aggregate JSON-LD document.
        annotation: Tier 2 CBOR-LD-ex annotation with fused opinion.
        context_registry: Registry for encoding the gateway message.
        surviving_count: Number of sensors that survived filtering.
        surviving_sensor_ids: @id values of surviving sensors.
        byzantine_metadata: Byzantine filtering metadata.
        decayed_opinions: Float-domain opinions after decay (surviving only).
    """
    doc: dict
    annotation: Annotation
    context_registry: ContextRegistry
    surviving_count: int
    surviving_sensor_ids: set
    byzantine_metadata: ByzantineMetadata
    decayed_opinions: list


def process_gateway(
    readings: list,
    config: SimulationConfig,
) -> GatewayResult:
    """Process Tier 1 sensor readings at the edge gateway.

    Steps:
      1. Dequantize each sensor's quantized opinion to float.
      2. Apply exponential decay based on reading age.
      3. Run Byzantine filtering to remove outliers.
      4. Cumulatively fuse surviving opinions.
      5. Re-quantize the fused opinion via constrained quantization.
      6. Build Tier 2 annotation with temporal and Byzantine metadata.

    Args:
        readings: List of SensorReading from Tier 1.
        config: Simulation configuration.

    Returns:
        GatewayResult with the fused Tier 2 annotation.
    """
    # Step 1 & 2: dequantize and apply temporal decay
    half_life = config.half_life_seconds
    decayed_opinions = []
    sensor_ids = []

    for r in readings:
        b_q, d_q, u_q, a_q = r.annotation.opinion
        b, d, u, a = dequantize_binomial(b_q, d_q, u_q, a_q, precision=8)

        # Age = base_timestamp - reading_timestamp
        age = config.base_timestamp - r.timestamp
        decay_factor = compute_decay_factor(DECAY_EXPONENTIAL, half_life, age)

        # Apply decay in float domain: b' = λb, d' = λd, u' = 1 - b' - d'
        b_decayed = decay_factor * b
        d_decayed = decay_factor * d
        u_decayed = 1.0 - b_decayed - d_decayed

        opinion = Opinion(
            belief=b_decayed,
            disbelief=d_decayed,
            uncertainty=u_decayed,
            base_rate=a,
        )
        decayed_opinions.append(opinion)
        sensor_ids.append(r.doc["@id"])

    # Step 3: Byzantine filtering
    byz_config = ByzantineConfig(
        threshold=config.byzantine_threshold,
        strategy="most_conflicting",
    )
    byz_report = byzantine_fuse(decayed_opinions, config=byz_config)

    # Extract surviving opinions and sensor IDs
    surviving_indices = byz_report.surviving_indices
    surviving_opinions = [decayed_opinions[i] for i in surviving_indices]
    surviving_ids = {sensor_ids[i] for i in surviving_indices}

    # The fused opinion comes from the byzantine_fuse report
    fused = byz_report.fused

    # Step 5: Re-quantize
    fused_q = quantize_binomial(
        fused.belief, fused.disbelief, fused.uncertainty, fused.base_rate,
        precision=8,
    )

    # Cohesion score for Byzantine metadata
    coh = byz_report.cohesion_score
    cohesion_q = min(255, max(0, round(coh * 255)))

    byz_meta = ByzantineMetadata(
        original_count=len(readings),
        removed_count=len(readings) - len(surviving_indices),
        cohesion_q=cohesion_q,
        strategy=STRATEGY_MOST_CONFLICTING,
    )

    # Temporal extension metadata
    temporal = TemporalBlock(
        decay_fn=DECAY_EXPONENTIAL,
        half_life_encoded=encode_half_life(half_life),
    )
    extensions = ExtensionBlock(temporal=temporal)

    # Build Tier 2 annotation
    annotation = Annotation(
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
            source_count=len(surviving_indices),
        ),
        opinion=fused_q,
        extensions=extensions,
    )

    # Compute average temperature from surviving sensors
    surviving_temps = [
        readings[i].doc["value"] for i in surviving_indices
    ]
    avg_temp = round(sum(surviving_temps) / len(surviving_temps), 1)

    doc = {
        "@context": "https://w3id.org/iot/compliance/v1",
        "@type": "GatewayAggregate",
        "@id": "urn:gateway:edge-001",
        "avgTemperature": avg_temp,
        "sensorCount": len(surviving_indices),
        "observedAt": f"2026-03-20T10:10:00Z",
    }

    registry = _build_gateway_registry()

    return GatewayResult(
        doc=doc,
        annotation=annotation,
        context_registry=registry,
        surviving_count=len(surviving_indices),
        surviving_sensor_ids=surviving_ids,
        byzantine_metadata=byz_meta,
        decayed_opinions=surviving_opinions,
    )


# =====================================================================
# Tier 3 — Cloud processing
#
# Pipeline:
#   1. Build provenance chain:
#      - One entry per surviving Tier 1 sensor (operator = NONE)
#      - One entry for the Tier 2 fusion step (operator = CUMULATIVE_FUSION)
#   2. Verify chain integrity
#   3. Produce audit summary
# =====================================================================

@dataclass
class CloudAudit:
    """Result of Tier 3 cloud processing.

    Attributes:
        provenance_chain: Verified provenance chain (list of ProvenanceEntry).
        summary: Human-readable audit summary dict.
    """
    provenance_chain: list
    summary: dict


def process_cloud(
    readings: list,
    gateway: GatewayResult,
    config: SimulationConfig,
) -> CloudAudit:
    """Process the Tier 2 gateway result at the cloud tier.

    Builds a provenance chain recording the full audit trail from
    individual sensor readings through gateway fusion, then verifies
    the chain's cryptographic integrity.

    Chain structure:
      Entry 0..N-1: One per surviving Tier 1 sensor (operator=NONE)
        - Records each sensor's original opinion in the chain
        - prev_digest: chained SHA-256 (entry 0 uses sentinel)
      Entry N: Tier 2 fusion step (operator=CUMULATIVE_FUSION)
        - Records the fused opinion
        - prev_digest: digest of entry N-1

    Args:
        readings: Original Tier 1 sensor readings.
        gateway: Gateway result from process_gateway().
        config: Simulation configuration.

    Returns:
        CloudAudit with verified provenance chain and audit summary.
    """
    chain = []
    prev_digest = CHAIN_ORIGIN_SENTINEL

    # Entries for surviving Tier 1 sensors
    surviving_readings = [
        r for r in readings
        if r.doc["@id"] in gateway.surviving_sensor_ids
    ]

    for r in surviving_readings:
        b_q, d_q, u_q, a_q = r.annotation.opinion

        entry = ProvenanceEntry(
            origin_tier=0,  # Tier 1 = constrained
            operator_id=int(OperatorId.NONE),
            precision_mode=int(PrecisionMode.BITS_8),
            b_q=b_q,
            d_q=d_q,
            a_q=a_q,
            timestamp=r.timestamp,
            prev_digest=prev_digest,
        )
        chain.append(entry)

        # Compute digest of this entry for the next entry's prev_digest
        entry_bytes = encode_provenance_entry(entry)
        prev_digest = compute_entry_digest(entry_bytes)

    # Entry for the Tier 2 fusion step
    fused_b_q, fused_d_q, fused_u_q, fused_a_q = gateway.annotation.opinion

    fusion_entry = ProvenanceEntry(
        origin_tier=1,  # Tier 2 = edge
        operator_id=int(OperatorId.CUMULATIVE_FUSION),
        precision_mode=int(PrecisionMode.BITS_8),
        b_q=fused_b_q,
        d_q=fused_d_q,
        a_q=fused_a_q,
        timestamp=config.base_timestamp,
        prev_digest=prev_digest,
    )
    chain.append(fusion_entry)

    # Verify chain integrity
    is_valid, error_idx = verify_provenance_chain(chain)

    # Build audit summary
    summary = {
        "total_sensors": len(readings),
        "surviving_sensors": gateway.surviving_count,
        "removed_sensors": len(readings) - gateway.surviving_count,
        "chain_length": len(chain),
        "chain_verified": is_valid,
        "chain_error_index": error_idx,
        "byzantine_cohesion": gateway.byzantine_metadata.cohesion_q / 255.0,
        "byzantine_strategy": "most_conflicting",
        "fused_opinion": {
            "b_q": fused_b_q,
            "d_q": fused_d_q,
            "u_q": fused_u_q,
            "a_q": fused_a_q,
        },
    }

    return CloudAudit(
        provenance_chain=chain,
        summary=summary,
    )


# =====================================================================
# End-to-end pipeline
# =====================================================================

@dataclass
class PipelineResult:
    """Complete pipeline result: Tier 1 → Tier 2 → Tier 3.

    Attributes:
        sensor_readings: All Tier 1 sensor readings.
        gateway_result: Tier 2 gateway processing result.
        cloud_audit: Tier 3 cloud audit result.
    """
    sensor_readings: list
    gateway_result: GatewayResult
    cloud_audit: CloudAudit


def run_pipeline(config: SimulationConfig = DEFAULT_CONFIG) -> PipelineResult:
    """Run the full Tier 1 → Tier 2 → Tier 3 simulation pipeline.

    Deterministic given the config seed. All encoding/decoding uses
    the real CBOR-LD-ex codec. Opinions flow through the full
    jsonld-ex algebra (decay, Byzantine filter, cumulative fusion).

    Args:
        config: Simulation configuration.

    Returns:
        PipelineResult with all three tiers' outputs.
    """
    # Tier 1: generate sensor readings
    readings = generate_sensor_readings(config)

    # Tier 2: gateway processing
    gateway = process_gateway(readings, config)

    # Tier 3: cloud audit
    audit = process_cloud(readings, gateway, config)

    return PipelineResult(
        sensor_readings=readings,
        gateway_result=gateway,
        cloud_audit=audit,
    )
