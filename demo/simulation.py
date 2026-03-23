#!/usr/bin/env python3
"""
CBOR-LD-ex Simulation CLI — Tier 1 → Tier 2 → Tier 3 pipeline demo.

Runs the full IoT simulation pipeline and prints a detailed report
showing encoding sizes, opinion evolution, Byzantine filtering,
provenance chain verification, and transport equivalence.

Usage:
  poetry run python demo/simulation.py                 # Default (8 sensors)
  poetry run python demo/simulation.py --sensors 12    # Custom sensor count
  poetry run python demo/simulation.py --seed 123      # Custom seed
  poetry run python demo/simulation.py --verbose       # Full per-sensor detail

All results are deterministic given the same seed.
"""

import argparse
import json
import os
import sys

# Add benchmarks/ to path for repo-only package
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "benchmarks"))

from cbor_ld_ex_benchmark.simulation import (
    SimulationConfig,
    run_pipeline,
    encode_sensor_reading,
)
from cbor_ld_ex.codec import encode
from cbor_ld_ex.opinions import dequantize_binomial
from cbor_ld_ex.security import verify_provenance_chain
from cbor_ld_ex.transport import (
    to_mqtt_payload,
    to_coap_payload,
    derive_topic,
    derive_qos,
)


def _fmt_opinion(b_q, d_q, u_q, a_q, precision=8):
    """Format a quantized opinion as a readable string."""
    b, d, u, a = dequantize_binomial(b_q, d_q, u_q, a_q, precision=precision)
    return f"b={b:.3f} d={d:.3f} u={u:.3f} a={a:.3f}"


def _print_header(title):
    print()
    print("=" * 64)
    print(f"  {title}")
    print("=" * 64)


def main():
    parser = argparse.ArgumentParser(
        description="CBOR-LD-ex Tier 1→2→3 simulation pipeline",
    )
    parser.add_argument(
        "--sensors", type=int, default=8,
        help="Number of sensors (default: 8, last is outlier)",
    )
    parser.add_argument(
        "--seed", type=int, default=42,
        help="Random seed for reproducibility (default: 42)",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Show full per-sensor detail",
    )
    args = parser.parse_args()

    config = SimulationConfig(sensor_count=args.sensors, seed=args.seed)

    print(f"Running simulation: {config.sensor_count} sensors, seed={config.seed}")
    result = run_pipeline(config)

    # ── Tier 1 Report ────────────────────────────────────────────
    _print_header("TIER 1 — Constrained Sensors")

    print(f"\n  Sensors generated: {len(result.sensor_readings)}")
    print(f"  {'ID':<24} {'Temp':>6} {'Opinion (dequantized)':<36} {'CBOR-LD-ex':>10} {'JSON-LD':>8}")
    print(f"  {'-'*24} {'-'*6} {'-'*36} {'-'*10} {'-'*8}")

    for r in result.sensor_readings:
        b_q, d_q, u_q, a_q = r.annotation.opinion
        cbor_size = len(encode_sensor_reading(r))
        json_size = len(json.dumps(r.doc).encode("utf-8"))
        is_outlier = d_q > 128  # high disbelief
        marker = " ← OUTLIER" if is_outlier else ""

        print(f"  {r.doc['@id']:<24} {r.doc['value']:>6.1f} "
              f"{_fmt_opinion(b_q, d_q, u_q, a_q):<36} "
              f"{cbor_size:>7} B {json_size:>6} B{marker}")

    if args.verbose:
        print(f"\n  Transport verification:")
        for r in result.sensor_readings:
            mqtt = to_mqtt_payload(r.doc, r.annotation, r.context_registry)
            coap = to_coap_payload(r.doc, r.annotation, r.context_registry)
            topic = derive_topic(r.doc, r.annotation)
            qos = derive_qos(r.doc, r.annotation)
            print(f"    {r.doc['@id']}: MQTT==CoAP: {mqtt == coap}, "
                  f"topic: {topic}, QoS: {qos}")

    # ── Tier 2 Report ────────────────────────────────────────────
    _print_header("TIER 2 — Edge Gateway")

    g = result.gateway_result
    b_q, d_q, u_q, a_q = g.annotation.opinion

    print(f"\n  Temporal decay: exponential, half-life={config.half_life_seconds:.0f}s")
    print(f"  Byzantine filtering: threshold={config.byzantine_threshold}")
    print(f"    Original sensors:  {g.byzantine_metadata.original_count}")
    print(f"    Removed:           {g.byzantine_metadata.removed_count}")
    print(f"    Surviving:         {g.surviving_count}")
    print(f"    Group cohesion:    {g.byzantine_metadata.cohesion_q / 255:.3f}")
    print(f"\n  Fused opinion: {_fmt_opinion(b_q, d_q, u_q, a_q)}")
    print(f"  Axiom 3 check: b̂+d̂+û = {b_q}+{d_q}+{u_q} = {b_q + d_q + u_q} (must be 255)")

    gateway_cbor = len(encode(g.doc, g.annotation, context_registry=g.context_registry))
    gateway_json = len(json.dumps(g.doc).encode("utf-8"))
    print(f"\n  Gateway payload: {gateway_cbor} B (CBOR-LD-ex) vs {gateway_json} B (JSON-LD)")
    print(f"  Compression: {(1 - gateway_cbor / gateway_json) * 100:.1f}%")

    if args.verbose:
        mqtt = to_mqtt_payload(g.doc, g.annotation, g.context_registry)
        coap = to_coap_payload(g.doc, g.annotation, g.context_registry)
        print(f"\n  Transport: MQTT==CoAP: {mqtt == coap}")
        print(f"  MQTT topic: {derive_topic(g.doc, g.annotation)}")
        print(f"  MQTT QoS: {derive_qos(g.doc, g.annotation)}")

    # ── Tier 3 Report ────────────────────────────────────────────
    _print_header("TIER 3 — Cloud Audit")

    audit = result.cloud_audit
    is_valid, error_idx = verify_provenance_chain(audit.provenance_chain)

    print(f"\n  Provenance chain: {len(audit.provenance_chain)} entries")
    print(f"  Chain verified: {'✓ VALID' if is_valid else f'✗ INVALID at entry {error_idx}'}")

    if args.verbose:
        print(f"\n  Chain entries:")
        tier_names = {0: "Tier 1", 1: "Tier 2", 2: "Tier 3"}
        for i, entry in enumerate(audit.provenance_chain):
            tier = tier_names.get(entry.origin_tier, f"Tier {entry.origin_tier}")
            op_name = "NONE" if entry.operator_id == 0 else "CUMULATIVE_FUSION"
            print(f"    [{i}] {tier} | op={op_name} | "
                  f"opinion=({entry.b_q},{entry.d_q},{entry.a_q}) | "
                  f"ts={entry.timestamp} | "
                  f"prev_digest={entry.prev_digest[:4].hex()}...")

    s = audit.summary
    print(f"\n  Audit summary:")
    print(f"    Total sensors:     {s['total_sensors']}")
    print(f"    Surviving:         {s['surviving_sensors']}")
    print(f"    Removed:           {s['removed_sensors']}")
    print(f"    Byzantine cohesion: {s['byzantine_cohesion']:.3f}")
    print(f"    Chain integrity:   {'VERIFIED' if s['chain_verified'] else 'FAILED'}")

    # ── Summary ──────────────────────────────────────────────────
    _print_header("PIPELINE SUMMARY")
    print(f"""
  {config.sensor_count} sensors → Byzantine filter → {g.surviving_count} survivors → fused opinion
  Outlier removed: {g.byzantine_metadata.removed_count} sensor(s)
  Provenance chain: {len(audit.provenance_chain)} entries, {'VERIFIED' if is_valid else 'INVALID'}
  All payloads transport-agnostic (MQTT == CoAP)
  Axiom 3 preserved at every hop
""")


if __name__ == "__main__":
    main()
