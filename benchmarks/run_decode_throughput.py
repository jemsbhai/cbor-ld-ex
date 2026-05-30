"""
EXP-007: Software decode throughput (9 formats × 4 datasets).

Measures decode speed — the metric that matters for constrained devices
receiving data. Encode is done once; decode is the hot path.

Usage (from repo root):
    poetry run python benchmarks/run_decode_throughput.py

Outputs:
    papers/cborld-ex-main/tables/decode_throughput.md
    papers/cborld-ex-main/tables/decode_throughput.csv

Protocol:
  1. Load 100 representative records per dataset
  2. Pre-encode each record in all 9 formats
  3. For each format: timeit decode of all 100 records × N iterations
  4. Report μs/record and records/second (mean ± std)
  5. Warmup excluded; GC disabled during measurement
"""

import csv
import gc
import sys
import time
import timeit
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cbor_ld_ex_benchmark.real_data import (
    # Loaders
    load_intel_lab,
    load_uci_air_quality,
    load_ciciot,
    load_swat_a8,
    # Mappers
    map_intel_lab_record,
    map_uci_aq_record,
    map_ciciot_record,
    map_swat_a8_record,
    # Encoders
    encode_as_json_ld,
    encode_as_senml_json,
    encode_as_senml_cbor,
    encode_as_cbor_ld,
    encode_as_cbor_ld_ex,
    encode_as_jsonldex_cbor_ld,
    encode_as_protobuf,
    encode_as_flatbuffers,
    encode_as_msgpack,
    # Decoders
    decode_json_ld,
    decode_senml_json,
    decode_senml_cbor,
    decode_cbor_ld,
    decode_cbor_ld_ex,
    decode_protobuf,
    decode_flatbuffers,
    decode_msgpack,
)

# ── Configuration ────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parent.parent

N_RECORDS = 100       # records per dataset for throughput measurement
N_ITERATIONS = 100    # decode iterations per timing run
N_REPEATS = 5         # independent timing runs (for std)
N_WARMUP = 3          # warmup iterations (excluded)

DATASETS = {
    "intel_lab": {
        "path": REPO_ROOT / "datasets" / "intel-lab" / "data.txt",
        "loader": load_intel_lab,
        "mapper": map_intel_lab_record,
        "label": "Intel Lab",
    },
    "uci_aq": {
        "path": REPO_ROOT / "datasets" / "uci-air-quality" / "AirQualityUCI.csv",
        "loader": load_uci_air_quality,
        "mapper": map_uci_aq_record,
        "label": "UCI Air Quality",
    },
    "ciciot": {
        "path": Path("E:/data/code/claudecode/aiiot2026/data/ciciot2023/Merged01.csv"),
        "loader": load_ciciot,
        "mapper": map_ciciot_record,
        "label": "CIC-IoT-2023",
    },
    "swat_a8": {
        "path": REPO_ROOT / "papers" / "cborld-ex-main" / "datasets" / "swat-a8" / "SWaT.A8_June 2021",
        "loader": load_swat_a8,
        "mapper": map_swat_a8_record,
        "label": "SWaT A8",
    },
}

FORMAT_LABELS = {
    "json_ld": "JSON-LD",
    "jsonldex_cbor_ld": "jsonld-ex",
    "cbor_ld": "CBOR-LD",
    "cbor_ld_ex": "CBOR-LD-ex",
    "senml_json": "SenML/JSON",
    "senml_cbor": "SenML/CBOR",
    "protobuf": "Protobuf",
    "flatbuffers": "FlatBuffers",
    "msgpack": "MessagePack",
}

FORMAT_ORDER = list(FORMAT_LABELS.keys())


# ── Helpers ──────────────────────────────────────────────────────────

def _prepare_encoded(records, dataset_name, cfg):
    """Pre-encode records in all 9 formats. Returns dict[fmt -> list[bytes]]."""
    mapper = cfg["mapper"]
    encoded = {fmt: [] for fmt in FORMAT_ORDER}

    for record in records:
        doc, ann, reg = mapper(record)

        encoded["json_ld"].append(encode_as_json_ld(doc, ann))
        encoded["jsonldex_cbor_ld"].append(encode_as_jsonldex_cbor_ld(doc, ann))
        encoded["cbor_ld"].append(encode_as_cbor_ld(doc, ann, reg))
        encoded["cbor_ld_ex"].append(encode_as_cbor_ld_ex(doc, ann, reg))
        encoded["senml_json"].append(encode_as_senml_json(record, dataset_name))
        encoded["senml_cbor"].append(encode_as_senml_cbor(record, dataset_name))
        encoded["protobuf"].append(encode_as_protobuf(record, dataset_name))
        encoded["flatbuffers"].append(encode_as_flatbuffers(record, dataset_name))
        encoded["msgpack"].append(encode_as_msgpack(record, dataset_name))

    return encoded


def _build_decode_fn(fmt, wire_list, dataset_name, reg):
    """Return a zero-arg callable that decodes all records in wire_list."""
    if fmt == "json_ld":
        def fn():
            for w in wire_list:
                decode_json_ld(w)
    elif fmt == "jsonldex_cbor_ld":
        # jsonld-ex CBOR-LD decode: cbor2.loads (no dedicated decoder)
        import cbor2
        def fn():
            for w in wire_list:
                cbor2.loads(w)
    elif fmt == "cbor_ld":
        def fn():
            for w in wire_list:
                decode_cbor_ld(w, reg)
    elif fmt == "cbor_ld_ex":
        def fn():
            for w in wire_list:
                decode_cbor_ld_ex(w, reg)
    elif fmt == "senml_json":
        def fn():
            for w in wire_list:
                decode_senml_json(w)
    elif fmt == "senml_cbor":
        def fn():
            for w in wire_list:
                decode_senml_cbor(w)
    elif fmt == "protobuf":
        def fn():
            for w in wire_list:
                decode_protobuf(w, dataset_name)
    elif fmt == "flatbuffers":
        def fn():
            for w in wire_list:
                decode_flatbuffers(w, dataset_name)
    elif fmt == "msgpack":
        def fn():
            for w in wire_list:
                decode_msgpack(w)
    else:
        raise ValueError(f"Unknown format: {fmt}")
    return fn


def _measure_throughput(decode_fn, n_records):
    """Measure decode throughput with warmup, GC control, multiple repeats.

    Returns dict with timing statistics.
    """
    # Warmup
    for _ in range(N_WARMUP):
        decode_fn()

    # Timed runs
    times = []
    for _ in range(N_REPEATS):
        gc.disable()
        t0 = time.perf_counter()
        for _ in range(N_ITERATIONS):
            decode_fn()
        t1 = time.perf_counter()
        gc.enable()
        elapsed = t1 - t0
        times.append(elapsed)

    total_decodes_per_run = n_records * N_ITERATIONS
    # Per-record time in seconds
    per_record_times = [t / total_decodes_per_run for t in times]

    import math
    mean_s = sum(per_record_times) / len(per_record_times)
    variance = sum((t - mean_s) ** 2 for t in per_record_times) / len(per_record_times)
    std_s = math.sqrt(variance)

    mean_us = mean_s * 1e6
    std_us = std_s * 1e6
    records_per_sec = 1.0 / mean_s if mean_s > 0 else float("inf")

    return {
        "mean_us": mean_us,
        "std_us": std_us,
        "records_per_sec": records_per_sec,
        "raw_times_s": times,
        "n_records": n_records,
        "n_iterations": N_ITERATIONS,
        "n_repeats": N_REPEATS,
    }


# ── Main ─────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("EXP-007: Decode Throughput Benchmark")
    print(f"  {N_RECORDS} records × {N_ITERATIONS} iterations × {N_REPEATS} repeats")
    print(f"  {N_WARMUP} warmup iterations (excluded)")
    print("=" * 60)

    all_results = []

    for dataset_name, cfg in DATASETS.items():
        path = cfg["path"]
        if not Path(path).exists():
            print(f"\n  SKIP {cfg['label']}: {path} not found")
            continue

        print(f"\n  Loading {cfg['label']}...")
        if dataset_name in ("ciciot", "swat_a8"):
            records = cfg["loader"](path, max_rows=N_RECORDS * 2)
        else:
            records = cfg["loader"](path)

        # Take N_RECORDS evenly spaced
        if len(records) > N_RECORDS:
            step = len(records) // N_RECORDS
            records = records[::step][:N_RECORDS]
        print(f"    Using {len(records)} records")

        # Need a registry for CBOR-LD family decodes
        mapper = cfg["mapper"]
        _, _, sample_reg = mapper(records[0])

        # Pre-encode
        print(f"  Pre-encoding in 9 formats...")
        encoded = _prepare_encoded(records, dataset_name, cfg)

        # Measure decode throughput per format
        dataset_results = {}
        for fmt in FORMAT_ORDER:
            decode_fn = _build_decode_fn(
                fmt, encoded[fmt], dataset_name, sample_reg
            )
            print(f"    {FORMAT_LABELS[fmt]:15s} ... ", end="", flush=True)
            result = _measure_throughput(decode_fn, len(records))
            dataset_results[fmt] = result
            print(f"{result['mean_us']:8.1f} ± {result['std_us']:5.1f} μs/rec "
                  f"({result['records_per_sec']:,.0f} rec/s)")

        all_results.append({
            "dataset_name": dataset_name,
            "dataset_label": cfg["label"],
            "results": dataset_results,
        })

    if not all_results:
        print("\nNo datasets found.")
        return

    # ── Output ───────────────────────────────────────────────────────

    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)

    # Markdown table: μs/record
    dataset_labels = [r["dataset_label"] for r in all_results]
    lines = [
        "## Decode Throughput Results",
        "",
        "### Decode Latency (μs/record)",
        "",
        "| Format | " + " | ".join(dataset_labels) + " |",
        "|---|" + "|".join(["---:"] * len(all_results)) + "|",
    ]
    for fmt in FORMAT_ORDER:
        label = FORMAT_LABELS[fmt]
        cells = []
        for r in all_results:
            res = r["results"][fmt]
            cells.append(f"{res['mean_us']:.1f} \u00b1 {res['std_us']:.1f}")
        lines.append(f"| {label} | " + " | ".join(cells) + " |")

    # Records/second table
    lines.extend([
        "",
        "### Decode Throughput (records/second)",
        "",
        "| Format | " + " | ".join(dataset_labels) + " |",
        "|---|" + "|".join(["---:"] * len(all_results)) + "|",
    ])
    for fmt in FORMAT_ORDER:
        label = FORMAT_LABELS[fmt]
        cells = []
        for r in all_results:
            rps = r["results"][fmt]["records_per_sec"]
            if rps >= 1e6:
                cells.append(f"{rps/1e6:.2f}M")
            elif rps >= 1e3:
                cells.append(f"{rps/1e3:.1f}K")
            else:
                cells.append(f"{rps:.0f}")
        lines.append(f"| {label} | " + " | ".join(cells) + " |")

    # Protocol note
    lines.extend([
        "",
        f"*Protocol: {N_RECORDS} records × {N_ITERATIONS} iterations "
        f"× {N_REPEATS} repeats, {N_WARMUP} warmup (excluded). "
        f"GC disabled during measurement. Host-labeled (not constrained device).*",
    ])

    full_md = "\n".join(lines)
    print("\n" + full_md)

    # Write files
    tables_dir = REPO_ROOT / "papers" / "cborld-ex-main" / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)

    md_path = tables_dir / "decode_throughput.md"
    md_path.write_text(full_md, encoding="utf-8")
    print(f"\n  Markdown: {md_path}")

    csv_path = tables_dir / "decode_throughput.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["format", "dataset", "mean_us", "std_us", "records_per_sec"])
        for r in all_results:
            for fmt in FORMAT_ORDER:
                res = r["results"][fmt]
                writer.writerow([
                    FORMAT_LABELS[fmt],
                    r["dataset_label"],
                    f"{res['mean_us']:.2f}",
                    f"{res['std_us']:.2f}",
                    f"{res['records_per_sec']:.0f}",
                ])
    print(f"  CSV: {csv_path}")


if __name__ == "__main__":
    main()
