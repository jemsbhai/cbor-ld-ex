#!/usr/bin/env python3
"""
CBOR-LD-ex Benchmark CLI — publication-ready encoding comparison.

Runs the 6-way benchmark across a systematic IoT scenario matrix
and outputs results as Markdown, LaTeX (booktabs), and/or CSV.

Usage:
  poetry run python demo/benchmark.py                  # Markdown to stdout
  poetry run python demo/benchmark.py --format latex   # LaTeX to stdout
  poetry run python demo/benchmark.py --format csv     # CSV to stdout
  poetry run python demo/benchmark.py --format all     # All formats to files
  poetry run python demo/benchmark.py --summary        # Include summary stats

Output files (--format all):
  demo/output/benchmark.md
  demo/output/benchmark.tex
  demo/output/benchmark.csv

All results are deterministic and reproducible.
"""

import argparse
import os
import sys

# Add benchmarks/ to path for repo-only package
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "benchmarks"))

from cbor_ld_ex_benchmark import (
    build_scenario_matrix,
    run_benchmark_suite,
    format_markdown_table,
    format_latex_table,
    format_csv,
)


def _format_summary(suite) -> str:
    """Format summary statistics as readable text."""
    s = suite.summary
    lines = []
    lines.append("=" * 60)
    lines.append("BENCHMARK SUMMARY")
    lines.append("=" * 60)
    lines.append(f"Scenarios evaluated: {s['scenario_count']}")
    lines.append("")

    # Per-encoding size statistics
    lines.append("Per-encoding payload sizes (bytes):")
    lines.append(f"  {'Encoding':<35} {'Min':>5} {'Max':>5} {'Mean':>7} {'Median':>7}")
    lines.append(f"  {'-'*35} {'-'*5} {'-'*5} {'-'*7} {'-'*7}")
    display_names = {
        "json_ld": "JSON-LD",
        "jex_cbor_ld": "jsonld-ex CBOR-LD",
        "our_cbor_ld_data_only": "Our CBOR-LD (data only)",
        "jex_cbor_ld_with_annotation": "jsonld-ex CBOR-LD + annotation",
        "our_cbor_ld_with_annotation": "Our CBOR-LD + CBOR annotation",
        "cbor_ld_ex": "CBOR-LD-ex (bit-packed)",
    }
    for enc_name, stats in s["per_encoding_stats"].items():
        name = display_names.get(enc_name, enc_name)
        lines.append(
            f"  {name:<35} {stats['min_bytes']:>5} "
            f"{stats['max_bytes']:>5} {stats['mean_bytes']:>7.1f} "
            f"{stats['median_bytes']:>7.1f}"
        )

    lines.append("")
    lines.append(f"Geometric mean compression vs JSON-LD: "
                 f"{s['geometric_mean_compression'] * 100:.1f}%")
    lines.append(f"Best case:  {s['best_case']['label']} "
                 f"({s['best_case']['compression'] * 100:.1f}% compression)")
    lines.append(f"Worst case: {s['worst_case']['label']} "
                 f"({s['worst_case']['compression'] * 100:.1f}% compression)")
    lines.append("=" * 60)

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="CBOR-LD-ex publication-quality benchmark",
    )
    parser.add_argument(
        "--format",
        choices=["markdown", "latex", "csv", "all"],
        default="markdown",
        help="Output format (default: markdown)",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Include summary statistics",
    )
    parser.add_argument(
        "--output-dir",
        default=os.path.join(os.path.dirname(__file__), "output"),
        help="Output directory for --format all (default: demo/output/)",
    )
    args = parser.parse_args()

    # Build and run
    print("Building scenario matrix...", file=sys.stderr)
    scenarios = build_scenario_matrix()
    print(f"Running {len(scenarios)} scenarios...", file=sys.stderr)
    suite = run_benchmark_suite(scenarios)
    print("Done.\n", file=sys.stderr)

    if args.format == "all":
        os.makedirs(args.output_dir, exist_ok=True)

        md_path = os.path.join(args.output_dir, "benchmark.md")
        tex_path = os.path.join(args.output_dir, "benchmark.tex")
        csv_path = os.path.join(args.output_dir, "benchmark.csv")

        with open(md_path, "w", encoding="utf-8") as f:
            f.write("# CBOR-LD-ex 6-Way Benchmark Results\n\n")
            f.write(format_markdown_table(suite))
            if args.summary:
                f.write("\n```\n")
                f.write(_format_summary(suite))
                f.write("\n```\n")

        with open(tex_path, "w", encoding="utf-8") as f:
            f.write("% CBOR-LD-ex 6-Way Benchmark Results\n")
            f.write("% Auto-generated — do not edit manually\n")
            f.write("% Requires: \\usepackage{booktabs}\n\n")
            f.write(format_latex_table(suite))

        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            f.write(format_csv(suite))

        print(f"Written: {md_path}", file=sys.stderr)
        print(f"Written: {tex_path}", file=sys.stderr)
        print(f"Written: {csv_path}", file=sys.stderr)

    else:
        if args.format == "markdown":
            print(format_markdown_table(suite))
        elif args.format == "latex":
            print(format_latex_table(suite))
        elif args.format == "csv":
            print(format_csv(suite))

    if args.summary:
        print(_format_summary(suite), file=sys.stderr)


if __name__ == "__main__":
    main()
