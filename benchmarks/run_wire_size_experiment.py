"""
Run the wire-size experiment on all four real datasets.

Usage (from repo root):
    poetry run python benchmarks/run_wire_size_experiment.py

Outputs:
    papers/cborld-ex-main/tables/wire_sizes.md   (Markdown)
    papers/cborld-ex-main/tables/wire_sizes.csv  (CSV)

Requires downloaded datasets at:
    datasets/intel-lab/data.txt
    datasets/uci-air-quality/AirQualityUCI.csv
    E:/data/code/claudecode/aiiot2026/data/ciciot2023/Merged01.csv
    E:/data/code/claudecode/cborldex/papers/cborld-ex-main/datasets/swat-a8/SWaT.A8_June 2021
"""

import csv
import sys
import time
from pathlib import Path

# Add benchmarks/ to path so we can import the module.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from cbor_ld_ex_benchmark.real_data import (
    load_intel_lab,
    load_uci_air_quality,
    load_ciciot,
    load_swat_a8,
    run_wire_size_experiment,
    MTU_CONSTANTS,
)

# ── Dataset paths ────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parent.parent

DATASETS = {
    "intel_lab": {
        "path": REPO_ROOT / "datasets" / "intel-lab" / "data.txt",
        "loader": load_intel_lab,
        "label": "Intel Lab",
        "max_rows": None,  # ~2.3M rows, takes a few seconds
        "sample": 10000,   # sample for speed; set None for full
    },
    "uci_aq": {
        "path": REPO_ROOT / "datasets" / "uci-air-quality" / "AirQualityUCI.csv",
        "loader": load_uci_air_quality,
        "label": "UCI Air Quality",
        "max_rows": None,
        "sample": None,  # only 9K rows, run all
    },
    "ciciot": {
        "path": Path("E:/data/code/claudecode/aiiot2026/data/ciciot2023/Merged01.csv"),
        "loader": load_ciciot,
        "label": "CIC-IoT-2023",
        "max_rows": 10000,  # 712K rows; sample
        "sample": None,
    },
    "swat_a8": {
        "path": REPO_ROOT / "papers" / "cborld-ex-main" / "datasets" / "swat-a8" / "SWaT.A8_June 2021",
        "loader": load_swat_a8,
        "label": "SWaT A8",
        "max_rows": 10000,  # multiple large CSVs; sample
        "sample": None,
    },
}

# ── Format display names (paper-ready) ───────────────────────────────

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


def load_dataset(name: str, cfg: dict) -> list[dict]:
    """Load a dataset, applying max_rows and sampling."""
    path = cfg["path"]
    if not Path(path).exists():
        print(f"  SKIP {cfg['label']}: {path} not found")
        return []

    print(f"  Loading {cfg['label']} from {path}...")
    t0 = time.time()

    if name in ("ciciot", "swat_a8"):
        records = cfg["loader"](path, max_rows=cfg.get("max_rows"))
    else:
        records = cfg["loader"](path)

    elapsed = time.time() - t0
    print(f"    {len(records)} records loaded in {elapsed:.1f}s")

    # Subsample if configured.
    sample_n = cfg.get("sample")
    if sample_n and len(records) > sample_n:
        # Deterministic: take every N-th record.
        step = len(records) // sample_n
        records = records[::step][:sample_n]
        print(f"    Subsampled to {len(records)} records (step={step})")

    return records


def format_markdown_table(results: list[dict]) -> str:
    """Generate Markdown table: datasets as columns, formats as rows."""
    dataset_labels = [r["dataset_label"] for r in results]

    lines = []
    # Header
    header = "| Format | " + " | ".join(dataset_labels) + " |"
    sep = "|---|" + "|".join(["---:"] * len(results)) + "|"
    lines.append(header)
    lines.append(sep)

    # Rows: one per format
    for fmt in FORMAT_ORDER:
        label = FORMAT_LABELS[fmt]
        cells = []
        for r in results:
            stats = r["result"]["per_format"][fmt]
            if r["result"]["n_records"] == 1:
                cells.append(f"{stats['mean']:.0f}")
            else:
                cells.append(f"{stats['mean']:.1f} \u00b1 {stats['std']:.1f}")
        lines.append(f"| {label} | " + " | ".join(cells) + " |")

    return "\n".join(lines)


def format_compression_table(results: list[dict]) -> str:
    """Generate compression ratio table (vs JSON-LD = 1.00)."""
    dataset_labels = [r["dataset_label"] for r in results]

    lines = []
    header = "| Format | " + " | ".join(dataset_labels) + " |"
    sep = "|---|" + "|".join(["---:"] * len(results)) + "|"
    lines.append("")
    lines.append("### Compression Ratio vs JSON-LD")
    lines.append("")
    lines.append(header)
    lines.append(sep)

    for fmt in FORMAT_ORDER:
        label = FORMAT_LABELS[fmt]
        cells = []
        for r in results:
            ratio = r["result"]["compression_ratios"][fmt]
            cells.append(f"{ratio:.3f}")
        lines.append(f"| {label} | " + " | ".join(cells) + " |")

    return "\n".join(lines)


def format_frame_fit_table(results: list[dict]) -> str:
    """Generate frame-fit percentage table."""
    lines = []
    lines.append("")
    lines.append("### Frame-Fit Percentage")

    mtu_order = ["LoRaWAN_SF12", "LoRaWAN_SF10", "802.15.4",
                 "LoRaWAN_SF7", "BLE"]

    for r in results:
        n = r["result"]["n_records"]
        if n == 0:
            continue
        lines.append("")
        lines.append(f"**{r['dataset_label']}** (n={n})")
        lines.append("")
        header = "| Format | " + " | ".join(
            f"{m} ({MTU_CONSTANTS[m]}B)" for m in mtu_order
        ) + " |"
        sep = "|---|" + "|".join(["---:"] * len(mtu_order)) + "|"
        lines.append(header)
        lines.append(sep)

        for fmt in FORMAT_ORDER:
            label = FORMAT_LABELS[fmt]
            cells = []
            for mtu_name in mtu_order:
                count = r["result"]["frame_fit"][mtu_name][fmt]
                pct = 100.0 * count / n
                cells.append(f"{pct:.1f}%")
            lines.append(f"| {label} | " + " | ".join(cells) + " |")

    return "\n".join(lines)


def write_csv(results: list[dict], path: Path):
    """Write per-format mean sizes as CSV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        header = ["format"] + [r["dataset_label"] for r in results]
        writer.writerow(header)
        for fmt in FORMAT_ORDER:
            row = [FORMAT_LABELS[fmt]]
            for r in results:
                row.append(f"{r['result']['per_format'][fmt]['mean']:.1f}")
            writer.writerow(row)
    print(f"  CSV written to {path}")


def main():
    print("=" * 60)
    print("CBOR-LD-ex Wire-Size Experiment")
    print("=" * 60)

    all_results = []

    for name, cfg in DATASETS.items():
        print()
        records = load_dataset(name, cfg)
        if not records:
            continue

        print(f"  Running experiment ({len(records)} records)...")
        t0 = time.time()
        result = run_wire_size_experiment(records, name)
        elapsed = time.time() - t0
        print(f"    Done in {elapsed:.1f}s")

        all_results.append({
            "dataset_name": name,
            "dataset_label": cfg["label"],
            "result": result,
        })

    if not all_results:
        print("\nNo datasets found. See docstring for paths.")
        return

    # Generate output.
    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)

    md_sizes = format_markdown_table(all_results)
    md_ratios = format_compression_table(all_results)
    md_fit = format_frame_fit_table(all_results)

    full_md = "\n".join([
        "## Wire-Size Experiment Results",
        "",
        "### Mean Wire Size (bytes)",
        "",
        md_sizes,
        md_ratios,
        md_fit,
    ])

    print("\n" + full_md)

    # Write files.
    tables_dir = REPO_ROOT / "papers" / "cborld-ex-main" / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)

    md_path = tables_dir / "wire_sizes.md"
    md_path.write_text(full_md)
    print(f"\n  Markdown written to {md_path}")

    csv_path = tables_dir / "wire_sizes.csv"
    write_csv(all_results, csv_path)


if __name__ == "__main__":
    main()
