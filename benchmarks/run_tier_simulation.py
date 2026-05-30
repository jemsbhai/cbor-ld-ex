"""
EXP-009: End-to-end Tier 1→2→3 simulation on real Intel Lab data.

Demonstrates the full CBOR-LD-ex pipeline on real sensor readings:
  Tier 1: Individual mote readings with evidence-based SL opinions
  Tier 2: Edge gateway fuses opinions from N motes via cumulative fusion
  Tier 3: Cloud builds provenance chain + audit summary

Reports wire sizes at each tier and compression vs JSON-LD equivalent.

Usage (from repo root):
    poetry run python benchmarks/run_tier_simulation.py

Output:
    papers/cborld-ex-main/tables/tier_simulation.md
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cbor_ld_ex_benchmark.real_data import (
    load_intel_lab,
    map_intel_lab_record,
    encode_as_json_ld,
)

from cbor_ld_ex.annotations import Annotation, encode_annotation
from cbor_ld_ex.codec import (
    encode as cborld_ex_encode,
)
from cbor_ld_ex.headers import (
    Tier1Header,
    Tier2Header,
    Tier3Header,
    ComplianceStatus,
    OperatorId,
    PrecisionMode,
)
from cbor_ld_ex.opinions import quantize_binomial
from cbor_ld_ex.security import (
    ProvenanceEntry,
    encode_provenance_entry,
    compute_entry_digest,
    CHAIN_ORIGIN_SENTINEL,
)
from cbor_ld_ex.temporal import (
    ExtensionBlock,
    TemporalBlock,
    DECAY_EXPONENTIAL,
    encode_half_life,
)

from jsonld_ex.confidence_algebra import Opinion, cumulative_fuse

# ── Configuration ────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parent.parent
INTEL_LAB_PATH = REPO_ROOT / "datasets" / "intel-lab" / "data.txt"

N_MOTES = 5          # Number of motes to simulate Tier 1
READINGS_PER_MOTE = 10  # Readings per mote in the window
W = 2                 # Beta prior weight
TEMP_MIN = 15.0       # Compliance threshold
TEMP_MAX = 30.0
HALF_LIFE_SECONDS = 300  # 5-minute decay half-life for temporal


def _evidence_to_opinion(readings, threshold_min, threshold_max):
    """Beta mapping (Jøsang Definition 4.1)."""
    r = sum(1 for v in readings if threshold_min <= v <= threshold_max)
    s = len(readings) - r
    total = r + s + W
    b = r / total
    d = s / total
    u = W / total
    return b, d, u, 0.5


def main():
    print("=" * 60)
    print("EXP-009: Tier 1\u21922\u21923 Simulation on Real Intel Lab Data")
    print(f"  {N_MOTES} motes, {READINGS_PER_MOTE} readings each")
    print("=" * 60)

    if not INTEL_LAB_PATH.exists():
        print(f"\n  ERROR: {INTEL_LAB_PATH} not found")
        return

    print("\n  Loading Intel Lab data...")
    all_records = load_intel_lab(INTEL_LAB_PATH)

    # Get available mote IDs.
    mote_ids = sorted(set(r["moteid"] for r in all_records))
    selected_motes = mote_ids[:N_MOTES]
    print(f"    Using motes: {selected_motes}")

    # Build per-mote record streams.
    mote_streams = {}
    for mid in selected_motes:
        stream = [r for r in all_records if r["moteid"] == mid]
        stream.sort(key=lambda r: r["epoch"])
        mote_streams[mid] = stream[:READINGS_PER_MOTE]
    print(f"    {READINGS_PER_MOTE} readings per mote")

    # Registry from first record (shared across all tiers).
    doc0, _, reg = map_intel_lab_record(mote_streams[selected_motes[0]][0])

    # ── Tier 1: Individual mote readings ─────────────────────────────
    print("\n  Tier 1: Encoding individual mote readings...")
    tier1_sizes = []
    tier1_json_sizes = []
    tier1_opinions = {}  # mote_id -> (b, d, u, a) float opinion

    for mid in selected_motes:
        stream = mote_streams[mid]
        temps = [r["temperature"] for r in stream]
        b, d, u, a = _evidence_to_opinion(temps, TEMP_MIN, TEMP_MAX)

        opinion_q = quantize_binomial(b, d, u, a, precision=8)
        header = Tier1Header(
            compliance_status=ComplianceStatus.COMPLIANT,
            delegation_flag=False,
            has_opinion=True,
            precision_mode=PrecisionMode.BITS_8,
        )
        ann = Annotation(header=header, opinion=opinion_q)

        # Encode last reading from this mote.
        doc, _, _ = map_intel_lab_record(stream[-1])
        wire = cborld_ex_encode(doc, ann, reg)
        tier1_sizes.append(len(wire))

        json_wire = encode_as_json_ld(doc, ann)
        tier1_json_sizes.append(len(json_wire))

        tier1_opinions[mid] = (b, d, u, a)

    tier1_mean = sum(tier1_sizes) / len(tier1_sizes)
    tier1_json_mean = sum(tier1_json_sizes) / len(tier1_json_sizes)

    # ── Tier 2: Edge gateway fusion ──────────────────────────────────
    print("  Tier 2: Fusing opinions at edge gateway...")

    # Cumulative fusion of all mote opinions.
    opinions_float = [
        Opinion(belief=v[0], disbelief=v[1], uncertainty=v[2], base_rate=v[3])
        for v in tier1_opinions.values()
    ]
    fused = cumulative_fuse(*opinions_float)

    # Quantize fused opinion.
    fused_q = quantize_binomial(
        fused.belief, fused.disbelief, fused.uncertainty, fused.base_rate,
        precision=8,
    )

    # Build Tier 2 header.
    tier2_header = Tier2Header(
        compliance_status=ComplianceStatus.COMPLIANT,
        delegation_flag=False,
        has_opinion=True,
        precision_mode=PrecisionMode.BITS_8,
        operator_id=OperatorId.CUMULATIVE_FUSION,
        reasoning_context=1,
        context_version=1,
        has_multinomial=False,
        sub_tier_depth=0,
        source_count=len(selected_motes),
    )

    # Add temporal extension (decay half-life).
    half_life_enc = encode_half_life(HALF_LIFE_SECONDS)
    ext = ExtensionBlock(
        temporal=TemporalBlock(
            decay_fn=DECAY_EXPONENTIAL,
            half_life_encoded=half_life_enc,
        ),
        triggers=None,
    )

    tier2_ann = Annotation(header=tier2_header, opinion=fused_q, extensions=ext)

    # Encode Tier 2 message: aggregated reading.
    # Use mean values across motes for the data fields.
    agg_record = dict(mote_streams[selected_motes[0]][-1])
    agg_record["moteid"] = 0  # aggregated
    agg_record["temperature"] = sum(
        mote_streams[m][-1]["temperature"] for m in selected_motes
    ) / N_MOTES
    agg_record["humidity"] = sum(
        mote_streams[m][-1]["humidity"] for m in selected_motes
    ) / N_MOTES
    agg_record["light"] = sum(
        mote_streams[m][-1]["light"] for m in selected_motes
    ) / N_MOTES
    agg_record["voltage"] = sum(
        mote_streams[m][-1]["voltage"] for m in selected_motes
    ) / N_MOTES

    agg_doc, _, _ = map_intel_lab_record(agg_record)
    tier2_wire = cborld_ex_encode(agg_doc, tier2_ann, reg)
    tier2_size = len(tier2_wire)

    # JSON-LD equivalent at Tier 2.
    tier2_json_wire = encode_as_json_ld(agg_doc, tier2_ann)
    tier2_json_size = len(tier2_json_wire)

    # ── Tier 3: Cloud with provenance ────────────────────────────────
    print("  Tier 3: Building provenance chain...")

    # Build provenance chain: one entry per surviving mote + fusion step.
    chain = []
    prev_digest = CHAIN_ORIGIN_SENTINEL
    base_ts = int(time.time())

    for i, mid in enumerate(selected_motes):
        b, d, u, a = tier1_opinions[mid]
        oq = quantize_binomial(b, d, u, a, precision=8)
        entry = ProvenanceEntry(
            origin_tier=0,  # Tier 1 = constrained
            operator_id=OperatorId.NONE,
            precision_mode=0,  # 8-bit
            b_q=oq[0],
            d_q=oq[1],
            a_q=oq[3],
            timestamp=base_ts + i,
            prev_digest=prev_digest,
        )
        entry_bytes = encode_provenance_entry(entry)
        prev_digest = compute_entry_digest(entry_bytes)
        chain.append(entry_bytes)

    # Fusion step entry.
    fusion_entry = ProvenanceEntry(
        origin_tier=1,  # Tier 2 = edge
        operator_id=OperatorId.CUMULATIVE_FUSION,
        precision_mode=0,
        b_q=fused_q[0],
        d_q=fused_q[1],
        a_q=fused_q[3],
        timestamp=base_ts + len(selected_motes),
        prev_digest=prev_digest,
    )
    fusion_entry_bytes = encode_provenance_entry(fusion_entry)
    chain.append(fusion_entry_bytes)

    provenance_bytes = b"".join(chain)

    # Build Tier 3 header.
    tier3_header = Tier3Header(
        compliance_status=ComplianceStatus.COMPLIANT,
        delegation_flag=False,
        has_opinion=True,
        precision_mode=PrecisionMode.BITS_8,
        operator_id=OperatorId.CUMULATIVE_FUSION,
        reasoning_context=1,
        has_extended_context=False,
        has_provenance_chain=True,
        has_multinomial=False,
        has_trust_info=False,
        sub_tier_depth=0,
    )

    tier3_ann = Annotation(header=tier3_header, opinion=fused_q, extensions=ext)

    # Tier 3 wire: annotation + provenance chain appended.
    tier3_ann_bytes = encode_annotation(tier3_ann)
    tier3_total_bytes = tier3_ann_bytes + provenance_bytes

    # For the full CBOR-LD-ex Tier 3 message, wrap in the codec.
    # The provenance is an additional field in the compressed doc.
    tier3_doc = dict(agg_doc)
    tier3_wire = cborld_ex_encode(tier3_doc, tier3_ann, reg)
    tier3_size = len(tier3_wire) + len(provenance_bytes)

    # JSON-LD Tier 3 equivalent.
    tier3_json_doc = dict(agg_doc)
    tier3_json_doc["provenance"] = [
        {"entry": e.hex(), "size": len(e)} for e in chain
    ]
    tier3_json_wire = json.dumps(
        tier3_json_doc, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    tier3_json_size = len(tier3_json_wire)

    # ── Annotation-only sizes ────────────────────────────────────────
    tier1_ann_bytes = encode_annotation(
        Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False, has_opinion=True,
                precision_mode=PrecisionMode.BITS_8,
            ),
            opinion=quantize_binomial(0.8, 0.1, 0.1, 0.5, precision=8),
        )
    )
    tier2_ann_only = encode_annotation(tier2_ann)
    tier3_ann_only = encode_annotation(tier3_ann)

    # ── Output ───────────────────────────────────────────────────────
    print("\n  Computing results...")

    lines = [
        "## EXP-009: End-to-End Tier 1\u21922\u21923 on Real Intel Lab Data",
        "",
        f"Motes: {selected_motes} ({N_MOTES} devices)",
        f"{READINGS_PER_MOTE} readings per mote, Beta mapping (W={W})",
        f"Compliance: temperature in [{TEMP_MIN}, {TEMP_MAX}]\u00b0C",
        f"Fusion: cumulative (jsonld-ex), decay half-life {HALF_LIFE_SECONDS}s",
        "",
        "### Annotation Size by Tier",
        "",
        "| Tier | Header | Opinion | Extensions | Total |",
        "|---|---:|---:|---:|---:|",
        f"| Tier 1 (constrained) | 1B | 3B | 0B | {len(tier1_ann_bytes)}B |",
        f"| Tier 2 (edge) | 4B | 3B | ext | {len(tier2_ann_only)}B |",
        f"| Tier 3 (cloud) | 4B | 3B | ext | {len(tier3_ann_only)}B |",
        "",
        "### Full Message Size by Tier",
        "",
        "| Tier | CBOR-LD-ex | JSON-LD | Ratio |",
        "|---|---:|---:|---:|",
        f"| Tier 1 (per mote, mean) | {tier1_mean:.0f}B | {tier1_json_mean:.0f}B "
        f"| {tier1_mean / tier1_json_mean:.3f} |",
        f"| Tier 2 (edge aggregate) | {tier2_size}B | {tier2_json_size}B "
        f"| {tier2_size / tier2_json_size:.3f} |",
        f"| Tier 3 (cloud + provenance) | {tier3_size}B | {tier3_json_size}B "
        f"| {tier3_size / tier3_json_size:.3f} |",
        "",
        "### Provenance Chain",
        "",
        f"| Component | Size |",
        f"|---|---:|",
        f"| Per-source entry | {len(chain[0])}B |",
        f"| Fusion entry | {len(chain[-1])}B |",
        f"| Total chain ({len(chain)} entries) | {len(provenance_bytes)}B |",
        "",
        "### Tier 1 Per-Mote Detail",
        "",
        "| Mote | CBOR-LD-ex | JSON-LD | Opinion (b,d,u) |",
        "|---:|---:|---:|---|",
    ]

    for i, mid in enumerate(selected_motes):
        b, d, u, a = tier1_opinions[mid]
        lines.append(
            f"| {mid} | {tier1_sizes[i]}B | {tier1_json_sizes[i]}B "
            f"| ({b:.3f}, {d:.3f}, {u:.3f}) |"
        )

    lines.extend([
        "",
        f"### Fused Opinion (Tier 2)",
        f"",
        f"b={fused.belief:.4f}, d={fused.disbelief:.4f}, u={fused.uncertainty:.4f}, a={fused.base_rate:.4f}",
        "",
        "*Tier 2 grows by 3B header + temporal extension vs Tier 1. "
        "Tier 3 adds provenance chain. Compression ratio improves at "
        "higher tiers because the fixed annotation overhead amortizes "
        "over the same data payload.*",
    ])

    full_md = "\n".join(lines)
    print("\n" + full_md)

    tables_dir = REPO_ROOT / "papers" / "cborld-ex-main" / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)
    out_path = tables_dir / "tier_simulation.md"
    out_path.write_text(full_md, encoding="utf-8")
    print(f"\n  Written to {out_path}")


if __name__ == "__main__":
    main()
