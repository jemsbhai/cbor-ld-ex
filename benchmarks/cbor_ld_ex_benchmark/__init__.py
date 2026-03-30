"""
CBOR-LD-ex Benchmark Suite — publication-quality evaluation framework.

NOT part of the core cbor-ld-ex package. This is repo-only evaluation
tooling for generating the benchmark tables and scientific claims in
the paper.

Design principles:
  - Every number in the paper is reproducible from this module.
  - Scenario matrix covers the IoT deployment space systematically:
    document sizes × annotation tiers × precisions × extension levels.
  - Context registries compress keys + vocabulary values (realistic),
    NOT instance-specific values like @id or timestamps (honest).
  - Derived metrics are computed from first principles, not parroted.
  - Output formats: Markdown (README), LaTeX (paper), CSV (analysis).

Usage:
  from cbor_ld_ex_benchmark import build_scenario_matrix, run_benchmark_suite
  from cbor_ld_ex_benchmark import format_markdown_table, format_latex_table

  scenarios = build_scenario_matrix()
  suite = run_benchmark_suite(scenarios)
  print(format_markdown_table(suite))
  print(format_latex_table(suite))
"""

import csv as csv_mod
import io
import json
import math
from dataclasses import dataclass, field
from typing import Optional

from cbor_ld_ex.annotations import Annotation, encode_annotation
from cbor_ld_ex.codec import (
    ContextRegistry,
    annotation_information_bits,
    ANNOTATION_TERM_ID,
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
from cbor_ld_ex.temporal import (
    ExtensionBlock,
    TemporalBlock,
    Trigger,
    DECAY_EXPONENTIAL,
    DECAY_LINEAR,
    TRIGGER_EXPIRY,
    TRIGGER_REVIEW_DUE,
    encode_half_life,
)
from cbor_ld_ex.transport import full_benchmark


# =====================================================================
# Constants — must match transport.py
# =====================================================================

_802154_MTU = 127
_COAP_OVERHEAD = 16
_MAX_SINGLE_FRAME_PAYLOAD = _802154_MTU - _COAP_OVERHEAD  # 111 bytes


# =====================================================================
# Data structures
# =====================================================================

@dataclass
class Scenario:
    """A single benchmark scenario: document + annotation + registry.

    Attributes:
        label: Unique human-readable identifier, format "profile/config".
        doc: JSON-LD document to encode.
        annotation: CBOR-LD-ex annotation for the document.
        context_registry: ContextRegistry for key+value compression.
        metadata: Dimension metadata for filtering and analysis.
            Required keys: doc_profile, tier, precision, extensions.
    """
    label: str
    doc: dict
    annotation: Annotation
    context_registry: Optional[ContextRegistry]
    metadata: dict


@dataclass
class ScenarioResult:
    """Result of running a single benchmark scenario.

    Attributes:
        scenario: The scenario that produced this result.
        encodings: Dict from full_benchmark() — 6 encoding comparisons.
        derived_metrics: Independently computed derived metrics.
    """
    scenario: Scenario
    encodings: dict
    derived_metrics: dict


@dataclass
class BenchmarkSuite:
    """Complete benchmark results with summary statistics.

    Attributes:
        results: Per-scenario results.
        summary: Aggregate statistics across all scenarios.
    """
    results: list
    summary: dict


# =====================================================================
# Document profiles — realistic IoT payloads spanning small → large
#
# Each profile represents a different IoT domain and document size.
# Field counts range from 5 (constrained sensor) to 15 (fleet aggregate).
#
# Design choice: @context URL is shared across all profiles (same
# ontology). @type is unique per profile (different device types).
# =====================================================================

def build_document_profiles() -> dict:
    """Build the set of benchmark document profiles.

    Returns a dict mapping profile name → JSON-LD document.
    Profiles are ordered by field count (ascending).

    Profiles:
      minimal_temperature (5 fields):
        Simplest possible IoT reading — one sensor, one value.
        Represents the constrained-device floor.

      environmental_monitor (8 fields):
        Multi-sensor environmental station — temperature, humidity,
        pressure, air quality. Common edge deployment.

      industrial_machine (12 fields):
        Machine health monitoring — motor temp, vibration, spindle
        speed, power, operating hours. Industrial IoT.

      aggregate_fleet (15 fields):
        Fleet-level compliance report — aggregate statistics,
        jurisdiction, data classification. Gateway/cloud artifact.
    """
    return {
        "minimal_temperature": {
            "@context": "https://w3id.org/iot/compliance/v1",
            "@type": "TemperatureReading",
            "@id": "urn:sensor:temp-042",
            "value": 22.5,
            "unit": "Celsius",
        },
        "environmental_monitor": {
            "@context": "https://w3id.org/iot/compliance/v1",
            "@type": "EnvironmentalReading",
            "@id": "urn:sensor:env-007",
            "temperature": 22.5,
            "humidity": 65.3,
            "pressure": 1013.25,
            "airQuality": "good",
            "observedAt": "2026-03-20T10:00:00Z",
        },
        "industrial_machine": {
            "@context": "https://w3id.org/iot/compliance/v1",
            "@type": "MachineHealthReport",
            "@id": "urn:machine:cnc-101",
            "motorTemperature": 78.2,
            "vibrationLevel": 0.42,
            "spindleSpeed": 12000,
            "powerConsumption": 3.7,
            "operatingHours": 4521,
            "maintenanceStatus": "nominal",
            "coolantLevel": "adequate",
            "errorCode": 0,
            "observedAt": "2026-03-20T10:00:00Z",
        },
        "aggregate_fleet": {
            "@context": "https://w3id.org/iot/compliance/v1",
            "@type": "FleetComplianceReport",
            "@id": "urn:fleet:eu-west-42",
            "sensorCount": 128,
            "avgTemperature": 21.7,
            "maxTemperature": 34.2,
            "minTemperature": 12.1,
            "avgHumidity": 58.9,
            "complianceRate": 0.94,
            "jurisdiction": "EU-GDPR",
            "reportingPeriod": "PT1H",
            "dataClassification": "internal",
            "processingBasis": "legitimate_interest",
            "retentionDays": 90,
            "observedAt": "2026-03-20T10:00:00Z",
        },
    }


# =====================================================================
# Annotation configurations — systematic tier × precision × extension
#
# Opinions use a fixed reference opinion (b=0.7, d=0.1, u=0.2, a=0.5)
# across all configurations. This isolates the encoding overhead from
# the opinion values themselves. The opinion is realistic: high belief,
# low disbelief, moderate uncertainty, neutral base rate.
# =====================================================================

# Reference opinion — used by all configs for comparability
_REF_OPINION = (0.7, 0.1, 0.2, 0.5)  # b, d, u, a


def _make_opinion(precision: int) -> tuple:
    """Create a quantized opinion tuple for the given precision.

    For 8/16-bit: quantize the reference opinion via constrained
    quantization (Theorem 1 guaranteed).
    For 32-bit: use float values directly (IEEE 754).
    """
    b, d, u, a = _REF_OPINION
    if precision == 32:
        return (b, d, u, a)
    return quantize_binomial(b, d, u, a, precision=precision)


def _make_temporal_block() -> TemporalBlock:
    """Create a reference temporal block: exponential decay, 1-hour half-life."""
    return TemporalBlock(
        decay_fn=DECAY_EXPONENTIAL,
        half_life_encoded=encode_half_life(3600.0),  # 1 hour
    )


def _make_trigger() -> Trigger:
    """Create a reference expiry trigger: gamma = 0.5 (Q8 = 128)."""
    return Trigger(
        trigger_type=TRIGGER_EXPIRY,
        parameter=128,  # gamma_q = 128 → 0.502 residual factor
    )


def build_annotation_configs() -> list:
    """Build the set of annotation configurations for benchmarking.

    Returns a list of (label, Annotation) tuples covering:
      - All tiers (1, 2)
      - All precision modes (8, 16, 32)
      - All compliance statuses (compliant, non_compliant, insufficient)
      - Extension levels: none, temporal, temporal+trigger

    12 configurations total. Labels encode the dimensions:
      t{tier}-{precision}bit-{status}[-{extension}]
    """
    configs = []

    # ── Tier 1 configurations ────────────────────────────────────

    # Vary compliance status (8-bit, no extensions)
    for status, status_label in [
        (ComplianceStatus.COMPLIANT, "compliant"),
        (ComplianceStatus.NON_COMPLIANT, "noncompliant"),
        (ComplianceStatus.INSUFFICIENT, "insufficient"),
    ]:
        configs.append((
            f"t1-8bit-{status_label}",
            Annotation(
                header=Tier1Header(
                    compliance_status=status,
                    delegation_flag=False,
                    has_opinion=True,
                    precision_mode=PrecisionMode.BITS_8,
                ),
                opinion=_make_opinion(8),
            ),
        ))

    # Vary precision (Tier 1, compliant, no extensions)
    for precision, pm in [
        (16, PrecisionMode.BITS_16),
        (32, PrecisionMode.BITS_32),
    ]:
        configs.append((
            f"t1-{precision}bit-compliant",
            Annotation(
                header=Tier1Header(
                    compliance_status=ComplianceStatus.COMPLIANT,
                    delegation_flag=False,
                    has_opinion=True,
                    precision_mode=pm,
                ),
                opinion=_make_opinion(precision),
            ),
        ))

    # Tier 1 with temporal extension (8-bit, compliant)
    configs.append((
        "t1-8bit-temporal",
        Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.BITS_8,
            ),
            opinion=_make_opinion(8),
            extensions=ExtensionBlock(
                temporal=_make_temporal_block(),
            ),
        ),
    ))

    # Tier 1 with temporal + trigger (8-bit, compliant)
    configs.append((
        "t1-8bit-temporal+trigger",
        Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.BITS_8,
            ),
            opinion=_make_opinion(8),
            extensions=ExtensionBlock(
                temporal=_make_temporal_block(),
                triggers=[_make_trigger()],
            ),
        ),
    ))

    # Tier 1 delta mode (§7.6) — 3-byte annotation (1 header + 2 delta)
    configs.append((
        "t1-delta-compliant",
        Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.DELTA_8,
            ),
            opinion=(5, -3),  # representative small delta
        ),
    ))

    # ── Tier 2 configurations ────────────────────────────────────

    # Tier 2 base fields — cumulative fusion from 5 sources
    def _t2_header(status, pm):
        return Tier2Header(
            compliance_status=status,
            delegation_flag=False,
            has_opinion=True,
            precision_mode=pm,
            operator_id=OperatorId.CUMULATIVE_FUSION,
            reasoning_context=1,    # context code 1
            context_version=1,
            has_multinomial=False,
            sub_tier_depth=0,
            source_count=5,
        )

    # Vary precision (Tier 2, compliant, no extensions)
    for precision, pm in [
        (8, PrecisionMode.BITS_8),
        (16, PrecisionMode.BITS_16),
        (32, PrecisionMode.BITS_32),
    ]:
        configs.append((
            f"t2-{precision}bit-compliant",
            Annotation(
                header=_t2_header(ComplianceStatus.COMPLIANT, pm),
                opinion=_make_opinion(precision),
            ),
        ))

    # Tier 2, non-compliant
    configs.append((
        "t2-8bit-noncompliant",
        Annotation(
            header=_t2_header(ComplianceStatus.NON_COMPLIANT, PrecisionMode.BITS_8),
            opinion=_make_opinion(8),
        ),
    ))

    # Tier 2 with temporal extension
    configs.append((
        "t2-8bit-temporal",
        Annotation(
            header=_t2_header(ComplianceStatus.COMPLIANT, PrecisionMode.BITS_8),
            opinion=_make_opinion(8),
            extensions=ExtensionBlock(
                temporal=_make_temporal_block(),
            ),
        ),
    ))

    # Tier 2 with temporal + trigger
    configs.append((
        "t2-8bit-temporal+trigger",
        Annotation(
            header=_t2_header(ComplianceStatus.COMPLIANT, PrecisionMode.BITS_8),
            opinion=_make_opinion(8),
            extensions=ExtensionBlock(
                temporal=_make_temporal_block(),
                triggers=[_make_trigger()],
            ),
        ),
    ))

    # Tier 2 delta mode (§7.6)
    configs.append((
        "t2-delta-compliant",
        Annotation(
            header=_t2_header(ComplianceStatus.COMPLIANT, PrecisionMode.DELTA_8),
            opinion=(5, -3),
        ),
    ))

    return configs


# =====================================================================
# Context registry builder — realistic key + vocabulary compression
#
# Compresses ALL string keys to integers (this is what CBOR-LD does).
# Compresses vocabulary-level string VALUES (context URLs, type names,
# unit labels, status enums) but NOT instance-specific values (@id,
# timestamps) which change per reading and can't be in a static registry.
#
# This is the honest approach: we don't overstate compression by
# pretending a static registry knows every possible @id or timestamp.
# =====================================================================

# Keys whose values are instance-specific and should NOT be compressed.
# These change per reading — a static context registry cannot know them.
_INSTANCE_SPECIFIC_KEYS = frozenset({"@id", "observedAt"})


def build_context_registry(doc: dict) -> ContextRegistry:
    """Build a ContextRegistry that compresses a given document.

    Key compression: every string key → unique integer (1, 2, 3, ...).
    Value compression: vocabulary-level string values → unique integer
    (500, 501, ...). Instance-specific values (@id, timestamps) are
    left as strings — a static registry can't know them.

    The integer code ranges are chosen to avoid collisions:
      - Key codes: 1 .. N (where N = number of keys, typically < 20)
      - Value codes: 500 .. 500+M (where M = number of vocabulary values)
      - Neither range includes ANNOTATION_TERM_ID (60000)

    Args:
        doc: JSON-LD document whose keys and vocabulary values to map.

    Returns:
        ContextRegistry with full key compression and vocabulary
        value compression.
    """
    # Map every string key to a unique small integer
    key_map = {}
    key_code = 1
    for key in doc:
        if isinstance(key, str):
            key_map[key] = key_code
            key_code += 1

    # Map vocabulary-level string values to unique integers.
    # Skip instance-specific values (change per reading).
    value_map = {}
    val_code = 500
    for key, value in doc.items():
        if key in _INSTANCE_SPECIFIC_KEYS:
            continue
        if isinstance(value, str) and value not in value_map:
            value_map[value] = val_code
            val_code += 1

    return ContextRegistry(key_map=key_map, value_map=value_map)


# =====================================================================
# Scenario matrix — systematic cross-product with realistic pairing
#
# Not a blind cross-product: Tier 1 (constrained devices) is only
# paired with small/medium documents. Large aggregate reports are
# Tier 2/3 artifacts — a 15-field fleet report from a constrained
# sensor is unrealistic and would violate the 802.15.4 frame claim.
#
# Pairing rules:
#   small/medium docs (≤ 8 fields) → all annotation configs
#   large docs (> 8 fields) → Tier 2 configs only
# =====================================================================

_SMALL_DOC_THRESHOLD = 8  # max fields for Tier 1 pairing


def build_scenario_matrix() -> list:
    """Build the full scenario matrix for benchmarking.

    Returns a deterministic, ordered list of Scenario objects.
    Each scenario has a unique label of the form "profile/config".

    The matrix is a REALISTIC cross-product:
      - Small/medium documents × all annotation configs (Tier 1 + 2)
      - Large documents × Tier 2 annotation configs only

    This reflects the real deployment pattern: constrained Tier 1
    devices produce small readings, not 15-field aggregate reports.
    """
    profiles = build_document_profiles()
    configs = build_annotation_configs()

    scenarios = []

    for profile_name, doc in profiles.items():
        registry = build_context_registry(doc)
        is_small = len(doc) <= _SMALL_DOC_THRESHOLD

        for config_label, annotation in configs:
            # Large docs only pair with Tier 2 configs
            tier = 1 if isinstance(annotation.header, Tier1Header) else 2
            if not is_small and tier == 1:
                continue

            # Determine extension level label
            if annotation.extensions is None:
                ext_level = "none"
            elif annotation.extensions.triggers is not None:
                ext_level = "temporal+trigger"
            else:
                ext_level = "temporal"

            # Determine precision
            precision_map = {
                PrecisionMode.BITS_8: 8,
                PrecisionMode.BITS_16: 16,
                PrecisionMode.BITS_32: 32,
                PrecisionMode.DELTA_8: 8,  # delta uses 8-bit baseline
            }
            precision = precision_map[annotation.header.precision_mode]

            scenarios.append(Scenario(
                label=f"{profile_name}/{config_label}",
                doc=doc,
                annotation=annotation,
                context_registry=registry,
                metadata={
                    "doc_profile": profile_name,
                    "tier": tier,
                    "precision": precision,
                    "extensions": ext_level,
                },
            ))

    return scenarios


# =====================================================================
# Derived metrics — independently computed from raw encoding sizes
#
# Every metric here is a first-principles computation, not a copy
# from full_benchmark(). The tests verify these against independent
# re-computation from the raw data.
# =====================================================================

def compute_derived_metrics(scenario: Scenario, encodings: dict) -> dict:
    """Compute derived metrics from raw encoding sizes.

    Args:
        scenario: The benchmark scenario.
        encodings: Dict from full_benchmark() with per-encoding data.

    Returns:
        Dict of derived metrics:
          compression_vs_jsonld: fraction of JSON-LD size saved
          annotation_overhead_bytes: bit-packed annotation size
          annotation_overhead_ratio: annotation / total CBOR-LD-ex size
          annotation_bit_efficiency: Shannon info / wire bits
          cbor_ld_ex_msgs_per_frame: messages per 802.15.4 frame
    """
    jsonld_size = encodings["json_ld"]["size"]
    ex_size = encodings["cbor_ld_ex"]["size"]

    # Compression ratio vs JSON-LD
    compression_vs_jsonld = 1.0 - (ex_size / jsonld_size)

    # Annotation overhead
    ann_bytes = encode_annotation(scenario.annotation)
    annotation_overhead = len(ann_bytes)
    annotation_overhead_ratio = annotation_overhead / ex_size

    # Bit efficiency from information-theoretic analysis
    analysis = annotation_information_bits(scenario.annotation)
    bit_efficiency = analysis["bit_efficiency"]

    # 802.15.4 frame fit
    if ex_size <= _MAX_SINGLE_FRAME_PAYLOAD:
        msgs_per_frame = max(1, _MAX_SINGLE_FRAME_PAYLOAD // ex_size)
    else:
        msgs_per_frame = 0

    return {
        "compression_vs_jsonld": compression_vs_jsonld,
        "annotation_overhead_bytes": annotation_overhead,
        "annotation_overhead_ratio": annotation_overhead_ratio,
        "annotation_bit_efficiency": bit_efficiency,
        "cbor_ld_ex_msgs_per_frame": msgs_per_frame,
    }


# =====================================================================
# Scenario execution
# =====================================================================

def run_scenario(scenario: Scenario) -> ScenarioResult:
    """Run the 6-way benchmark for a single scenario.

    Calls full_benchmark() from transport.py, then computes derived
    metrics independently.

    Args:
        scenario: The benchmark scenario to run.

    Returns:
        ScenarioResult with raw encodings and derived metrics.
    """
    encodings = full_benchmark(
        scenario.doc,
        scenario.annotation,
        context_registry=scenario.context_registry,
    )
    derived = compute_derived_metrics(scenario, encodings)
    return ScenarioResult(
        scenario=scenario,
        encodings=encodings,
        derived_metrics=derived,
    )


# =====================================================================
# Summary statistics — aggregate analysis across all scenarios
# =====================================================================

def compute_summary_statistics(results: list) -> dict:
    """Compute aggregate statistics across all benchmark results.

    Args:
        results: List of ScenarioResult objects.

    Returns:
        Dict with:
          scenario_count: number of scenarios
          per_encoding_stats: {encoding: {min, max, mean, median}_bytes}
          geometric_mean_compression: geometric mean of compression ratios
          best_case: {label, compression} for highest compression
          worst_case: {label, compression} for lowest compression
    """
    n = len(results)
    encoding_names = [
        "json_ld",
        "jex_cbor_ld",
        "our_cbor_ld_data_only",
        "jex_cbor_ld_with_annotation",
        "our_cbor_ld_with_annotation",
        "cbor_ld_ex",
    ]

    # Per-encoding size statistics
    per_encoding_stats = {}
    for enc_name in encoding_names:
        sizes = sorted(r.encodings[enc_name]["size"] for r in results)
        mean_val = sum(sizes) / n
        if n % 2 == 1:
            median_val = sizes[n // 2]
        else:
            median_val = (sizes[n // 2 - 1] + sizes[n // 2]) / 2.0

        per_encoding_stats[enc_name] = {
            "min_bytes": sizes[0],
            "max_bytes": sizes[-1],
            "mean_bytes": mean_val,
            "median_bytes": median_val,
        }

    # Compression ratios (CBOR-LD-ex vs JSON-LD)
    ratios = [r.derived_metrics["compression_vs_jsonld"] for r in results]

    # Geometric mean of compression ratios
    log_sum = sum(math.log(r) for r in ratios)
    geometric_mean = math.exp(log_sum / n)

    # Best and worst case
    best_idx = max(range(n), key=lambda i: ratios[i])
    worst_idx = min(range(n), key=lambda i: ratios[i])

    return {
        "scenario_count": n,
        "per_encoding_stats": per_encoding_stats,
        "geometric_mean_compression": geometric_mean,
        "best_case": {
            "label": results[best_idx].scenario.label,
            "compression": ratios[best_idx],
        },
        "worst_case": {
            "label": results[worst_idx].scenario.label,
            "compression": ratios[worst_idx],
        },
    }


def run_benchmark_suite(scenarios: list) -> BenchmarkSuite:
    """Run the full benchmark suite.

    Executes all scenarios and computes summary statistics.

    Args:
        scenarios: List of Scenario objects from build_scenario_matrix().

    Returns:
        BenchmarkSuite with per-scenario results and summary.
    """
    results = [run_scenario(s) for s in scenarios]
    summary = compute_summary_statistics(results)
    return BenchmarkSuite(results=results, summary=summary)


# =====================================================================
# Formatting — publication-ready output
#
# Three formats:
#   Markdown: README, GitHub, review drafts
#   LaTeX: paper submission (booktabs, no \\hline)
#   CSV: machine-readable for external analysis (R, pandas, etc.)
# =====================================================================

# Column definitions for the comparison table.
# Each column: (header, key_or_func, format_spec, alignment)
_TABLE_COLUMNS = [
    ("Scenario", "label", "s", "l"),
    ("JSON-LD", "json_ld", "d", "r"),
    ("jex CBOR-LD", "jex_cbor_ld", "d", "r"),
    ("Our CBOR-LD", "our_cbor_ld_data_only", "d", "r"),
    ("jex+Ann", "jex_cbor_ld_with_annotation", "d", "r"),
    ("Our+Ann", "our_cbor_ld_with_annotation", "d", "r"),
    ("CBOR-LD-ex", "cbor_ld_ex", "d", "r"),
    ("Comp. %", "compression_pct", ".1f", "r"),
    ("Bit Eff. %", "bit_eff_pct", ".1f", "r"),
]


def _row_values(result: ScenarioResult) -> list:
    """Extract the display values for one result row."""
    e = result.encodings
    d = result.derived_metrics
    return [
        result.scenario.label,
        e["json_ld"]["size"],
        e["jex_cbor_ld"]["size"],
        e["our_cbor_ld_data_only"]["size"],
        e["jex_cbor_ld_with_annotation"]["size"],
        e["our_cbor_ld_with_annotation"]["size"],
        e["cbor_ld_ex"]["size"],
        d["compression_vs_jsonld"] * 100.0,
        d["annotation_bit_efficiency"] * 100.0,
    ]


def _format_cell(value, fmt: str) -> str:
    """Format a single cell value."""
    if fmt == "s":
        return str(value)
    elif fmt == "d":
        return str(int(value))
    else:
        return f"{value:{fmt}}"


def format_markdown_table(suite: BenchmarkSuite) -> str:
    """Format benchmark results as a Markdown table.

    Includes a header row, separator, and one data row per scenario.
    All size columns are in bytes. Compression and efficiency in %.

    Returns:
        Markdown-formatted table string.
    """
    headers = [col[0] for col in _TABLE_COLUMNS]
    fmts = [col[2] for col in _TABLE_COLUMNS]

    lines = []

    # Header
    lines.append("| " + " | ".join(headers) + " |")

    # Separator (right-align numeric columns)
    seps = []
    for col in _TABLE_COLUMNS:
        if col[3] == "r":
            seps.append("---:")
        else:
            seps.append(":---")
    lines.append("| " + " | ".join(seps) + " |")

    # Data rows
    for result in suite.results:
        vals = _row_values(result)
        cells = [_format_cell(v, f) for v, f in zip(vals, fmts)]
        lines.append("| " + " | ".join(cells) + " |")

    return "\n".join(lines) + "\n"


def _latex_escape(text: str) -> str:
    """Escape LaTeX special characters in text-mode strings."""
    # Order matters: & first (it's a column separator, but in cell text
    # it must be escaped), then others
    replacements = [
        ("\\", "\\textbackslash{}"),
        ("&", "\\&"),
        ("%", "\\%"),
        ("$", "\\$"),
        ("#", "\\#"),
        ("_", "\\_"),
        ("{", "\\{"),
        ("}", "\\}"),
        ("~", "\\textasciitilde{}"),
        ("^", "\\textasciicircum{}"),
    ]
    for old, new in replacements:
        text = text.replace(old, new)
    return text


def format_latex_table(suite: BenchmarkSuite) -> str:
    """Format benchmark results as a LaTeX table with booktabs.

    Uses booktabs (\\toprule, \\midrule, \\bottomrule) — never \\hline.
    Suitable for direct inclusion in a publication-quality paper.

    Returns:
        LaTeX table string.
    """
    headers = [col[0] for col in _TABLE_COLUMNS]
    fmts = [col[2] for col in _TABLE_COLUMNS]
    aligns = [col[3] for col in _TABLE_COLUMNS]

    lines = []

    # Table preamble
    col_spec = "".join(aligns)
    lines.append(f"\\begin{{tabular}}{{{col_spec}}}")
    lines.append("\\toprule")

    # Header row
    escaped_headers = [_latex_escape(h) for h in headers]
    lines.append(" & ".join(escaped_headers) + " \\\\")
    lines.append("\\midrule")

    # Data rows
    for result in suite.results:
        vals = _row_values(result)
        cells = []
        for val, fmt in zip(vals, fmts):
            if fmt == "s":
                cells.append(_latex_escape(str(val)))
            else:
                cells.append(_format_cell(val, fmt))
        lines.append(" & ".join(cells) + " \\\\")

    lines.append("\\bottomrule")
    lines.append("\\end{tabular}")

    return "\n".join(lines) + "\n"


def format_csv(suite: BenchmarkSuite) -> str:
    """Format benchmark results as CSV for machine analysis.

    Columns match the table columns plus scenario metadata dimensions.
    Suitable for import into pandas, R, or spreadsheet tools.

    Returns:
        CSV string with header row and one data row per scenario.
    """
    output = io.StringIO()
    writer = csv_mod.writer(output)

    # Header
    csv_headers = [
        "scenario_label",
        "doc_profile",
        "tier",
        "precision",
        "extensions",
        "json_ld_bytes",
        "jex_cbor_ld_bytes",
        "our_cbor_ld_bytes",
        "jex_cbor_ld_with_ann_bytes",
        "our_cbor_ld_with_ann_bytes",
        "cbor_ld_ex_bytes",
        "compression_vs_jsonld",
        "annotation_bit_efficiency",
        "annotation_overhead_bytes",
        "cbor_ld_ex_msgs_per_frame",
    ]
    writer.writerow(csv_headers)

    # Data rows
    for result in suite.results:
        e = result.encodings
        d = result.derived_metrics
        m = result.scenario.metadata
        writer.writerow([
            result.scenario.label,
            m["doc_profile"],
            m["tier"],
            m["precision"],
            m["extensions"],
            e["json_ld"]["size"],
            e["jex_cbor_ld"]["size"],
            e["our_cbor_ld_data_only"]["size"],
            e["jex_cbor_ld_with_annotation"]["size"],
            e["our_cbor_ld_with_annotation"]["size"],
            e["cbor_ld_ex"]["size"],
            f"{d['compression_vs_jsonld']:.6f}",
            f"{d['annotation_bit_efficiency']:.6f}",
            d["annotation_overhead_bytes"],
            d["cbor_ld_ex_msgs_per_frame"],
        ])

    return output.getvalue()
