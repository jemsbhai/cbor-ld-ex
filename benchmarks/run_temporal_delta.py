"""
EXP-008: Temporal/delta streaming overhead on Intel Lab data.

Measures wire savings from delta-mode opinion encoding for successive
readings from the same sensor. Delta mode (PrecisionMode.DELTA_8)
encodes a 2-byte opinion delta instead of the full 3-byte opinion,
saving 1 byte per reading after the first.

Usage (from repo root):
    poetry run python benchmarks/run_temporal_delta.py

Output:
    papers/cborld-ex-main/tables/temporal_delta.md
"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cbor_ld_ex_benchmark.real_data import (
    load_intel_lab,
    map_intel_lab_record,
    encode_as_json_ld,
)

from cbor_ld_ex.annotations import Annotation, encode_annotation
from cbor_ld_ex.codec import ContextRegistry, encode as cborld_ex_encode
from cbor_ld_ex.headers import (
    Tier1Header,
    ComplianceStatus,
    PrecisionMode,
    opinion_payload_size,
)
from cbor_ld_ex.opinions import quantize_binomial, dequantize_binomial

# ── Configuration ────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parent.parent
INTEL_LAB_PATH = REPO_ROOT / "datasets" / "intel-lab" / "data.txt"
TARGET_MOTE = 1
STREAM_LENGTH = 100
W = 2  # Jøsang Beta prior weight (Definition 4.1 from jsonld-ex spec)

# Compliance thresholds for evidence→opinion mapping.
# Intel Lab: temperature within [15, 30]°C is "compliant".
TEMP_MIN = 15.0
TEMP_MAX = 30.0


def _evidence_to_opinion(readings, threshold_min, threshold_max):
    """Map a sliding window of readings to an SL opinion via Beta mapping.

    Jøsang's Beta mapping (jsonld-ex spec §4 Definition 4.1):
      b = r / (r + s + W)
      d = s / (r + s + W)
      u = W / (r + s + W)
    where r = readings within threshold, s = readings outside, W = 2.
    """
    r = sum(1 for v in readings if threshold_min <= v <= threshold_max)
    s = len(readings) - r
    total = r + s + W
    b = r / total
    d = s / total
    u = W / total
    a = 0.5  # default base rate
    return b, d, u, a


def main():
    print("=" * 60)
    print("EXP-008: Temporal/Delta Streaming Overhead")
    print(f"  Mote {TARGET_MOTE}, {STREAM_LENGTH} consecutive readings")
    print(f"  Compliance: temperature in [{TEMP_MIN}, {TEMP_MAX}]°C")
    print("=" * 60)

    if not INTEL_LAB_PATH.exists():
        print(f"\n  ERROR: {INTEL_LAB_PATH} not found")
        return

    # Load and filter to single mote.
    print("\n  Loading Intel Lab data...")
    all_records = load_intel_lab(INTEL_LAB_PATH)
    mote_records = [r for r in all_records if r["moteid"] == TARGET_MOTE]
    mote_records.sort(key=lambda r: r["epoch"])
    print(f"    {len(mote_records)} records for mote {TARGET_MOTE}")

    if len(mote_records) < STREAM_LENGTH:
        print(f"    ERROR: need {STREAM_LENGTH} records, only {len(mote_records)}")
        return

    stream = mote_records[:STREAM_LENGTH]

    # Build the registry from the first record.
    doc0, _, reg = map_intel_lab_record(stream[0])

    # ── Full-mode encoding (baseline) ────────────────────────────────
    print("\n  Encoding stream: full BITS_8 (baseline)...")
    full_sizes = []
    full_json_sizes = []

    # Use a sliding window of last 10 readings for evidence→opinion.
    WINDOW = 10

    for i, record in enumerate(stream):
        # Evidence window: last WINDOW readings up to and including current.
        window_start = max(0, i - WINDOW + 1)
        window_temps = [stream[j]["temperature"] for j in range(window_start, i + 1)]
        b, d, u, a = _evidence_to_opinion(window_temps, TEMP_MIN, TEMP_MAX)

        opinion = quantize_binomial(b, d, u, a, precision=8)
        header = Tier1Header(
            compliance_status=ComplianceStatus.COMPLIANT,
            delegation_flag=False,
            has_opinion=True,
            precision_mode=PrecisionMode.BITS_8,
        )
        ann = Annotation(header=header, opinion=opinion)
        doc, _, _ = map_intel_lab_record(record)
        wire = cborld_ex_encode(doc, ann, reg)
        full_sizes.append(len(wire))

        # JSON-LD baseline for comparison.
        json_wire = encode_as_json_ld(doc, ann)
        full_json_sizes.append(len(json_wire))

    # ── Delta-mode encoding ──────────────────────────────────────────
    print("  Encoding stream: first BITS_8, rest DELTA_8...")
    delta_sizes = []
    delta_fallback_count = 0

    prev_b_q = None
    prev_d_q = None
    for i, record in enumerate(stream):
        window_start = max(0, i - WINDOW + 1)
        window_temps = [stream[j]["temperature"] for j in range(window_start, i + 1)]
        b, d, u, a = _evidence_to_opinion(window_temps, TEMP_MIN, TEMP_MAX)

        opinion_full = quantize_binomial(b, d, u, a, precision=8)
        b_q, d_q, u_q, a_q = opinion_full

        if i == 0 or prev_b_q is None:
            # First reading: full opinion.
            header = Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.BITS_8,
            )
            ann = Annotation(header=header, opinion=opinion_full)
        else:
            # Compute signed deltas.
            db = b_q - prev_b_q
            dd = d_q - prev_d_q

            # Check int8 range [-128, 127]. If exceeded, fall back.
            if -128 <= db <= 127 and -128 <= dd <= 127:
                header = Tier1Header(
                    compliance_status=ComplianceStatus.COMPLIANT,
                    delegation_flag=False,
                    has_opinion=True,
                    precision_mode=PrecisionMode.DELTA_8,
                )
                ann = Annotation(header=header, opinion=(db, dd))
            else:
                # Fallback to full mode.
                delta_fallback_count += 1
                header = Tier1Header(
                    compliance_status=ComplianceStatus.COMPLIANT,
                    delegation_flag=False,
                    has_opinion=True,
                    precision_mode=PrecisionMode.BITS_8,
                )
                ann = Annotation(header=header, opinion=opinion_full)

        doc, _, _ = map_intel_lab_record(record)
        wire = cborld_ex_encode(doc, ann, reg)
        delta_sizes.append(len(wire))
        prev_b_q = b_q
        prev_d_q = d_q

    # ── Analysis ─────────────────────────────────────────────────────
    print("\n  Computing statistics...")

    total_full = sum(full_sizes)
    total_delta = sum(delta_sizes)
    total_json = sum(full_json_sizes)

    savings_bytes = total_full - total_delta
    savings_pct = 100.0 * savings_bytes / total_full if total_full > 0 else 0.0

    # Per-reading sizes.
    first_full = full_sizes[0]
    first_delta = delta_sizes[0]  # same (full mode for first)
    mean_full_rest = sum(full_sizes[1:]) / (STREAM_LENGTH - 1)
    mean_delta_rest = sum(delta_sizes[1:]) / (STREAM_LENGTH - 1)
    per_reading_saving = mean_full_rest - mean_delta_rest

    # Opinion payload size difference.
    full_opinion_bytes = opinion_payload_size(PrecisionMode.BITS_8)
    delta_opinion_bytes = opinion_payload_size(PrecisionMode.DELTA_8)

    # ── Output ───────────────────────────────────────────────────────
    lines = [
        "## EXP-008: Temporal/Delta Streaming Overhead",
        "",
        f"Dataset: Intel Lab, mote {TARGET_MOTE}, "
        f"{STREAM_LENGTH} consecutive readings (31s cadence)",
        f"Compliance threshold: temperature in [{TEMP_MIN}, {TEMP_MAX}]\u00b0C",
        f"Evidence window: {WINDOW} readings, Beta mapping (W={W})",
        f"Delta fallback to full mode: {delta_fallback_count} of {STREAM_LENGTH - 1} readings",
        "",
        "### Opinion Payload Size",
        "",
        f"| Mode | Opinion bytes |",
        f"|---|---:|",
        f"| BITS_8 (full) | {full_opinion_bytes} |",
        f"| DELTA_8 (delta) | {delta_opinion_bytes} |",
        f"| Saving per reading | {full_opinion_bytes - delta_opinion_bytes} |",
        "",
        "### Stream Wire Cost",
        "",
        f"| Metric | Full mode | Delta mode | JSON-LD |",
        f"|---|---:|---:|---:|",
        f"| First reading (bytes) | {first_full} | {first_delta} | {full_json_sizes[0]} |",
        f"| Mean subsequent (bytes) | {mean_full_rest:.1f} | {mean_delta_rest:.1f} | "
        f"{sum(full_json_sizes[1:]) / (STREAM_LENGTH - 1):.1f} |",
        f"| Per-reading saving | \u2014 | {per_reading_saving:.1f} | \u2014 |",
        f"| Total stream ({STREAM_LENGTH} readings) | {total_full} | {total_delta} | {total_json} |",
        f"| Stream saving | \u2014 | {savings_bytes} B ({savings_pct:.1f}%) | \u2014 |",
        "",
        "### Cumulative Savings Over Stream Length",
        "",
        "| Stream length | Full (bytes) | Delta (bytes) | Saving (bytes) | Saving (%) |",
        "|---:|---:|---:|---:|---:|",
    ]

    # Report at N = 1, 10, 25, 50, 100
    checkpoints = [1, 10, 25, 50, STREAM_LENGTH]
    for n in checkpoints:
        if n > STREAM_LENGTH:
            break
        cum_full = sum(full_sizes[:n])
        cum_delta = sum(delta_sizes[:n])
        saving = cum_full - cum_delta
        pct = 100.0 * saving / cum_full if cum_full > 0 else 0.0
        lines.append(f"| {n} | {cum_full} | {cum_delta} | {saving} | {pct:.1f}% |")

    lines.extend([
        "",
        f"*Delta savings are bounded by the opinion payload difference "
        f"({full_opinion_bytes - delta_opinion_bytes}B per reading). "
        f"Data fields are always fully encoded; delta applies only to the "
        f"opinion component of the annotation.*",
    ])

    full_md = "\n".join(lines)
    print("\n" + full_md)

    # Write output.
    tables_dir = REPO_ROOT / "papers" / "cborld-ex-main" / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)
    out_path = tables_dir / "temporal_delta.md"
    out_path.write_text(full_md, encoding="utf-8")
    print(f"\n  Written to {out_path}")


if __name__ == "__main__":
    main()
