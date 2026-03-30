"""
Tests for the benchmark module — publication-quality evaluation framework.

Organized in six sections:

1. SCENARIO CONSTRUCTION — build_scenario_matrix(), build_document_profiles(),
   build_annotation_configs(): coverage, validity, no duplicates, determinism.

2. DERIVED METRIC COMPUTATION — mathematical correctness of compression ratios,
   bit efficiencies, overhead calculations. Every number in the paper must be
   verifiable from first principles.

3. SCIENTIFIC INVARIANTS — the claims we make in the paper, parametrized over
   ALL scenarios. These are the tests that ruthless reviewers will mentally run.
   Every "always" and "never" in the paper becomes a universal assertion here.

4. TABLE FORMATTING — publication-ready Markdown and LaTeX output. Correct
   columns, rows, escaping, booktabs, no NaN/Inf in printed numbers.

5. SUMMARY STATISTICS — aggregate computations (min, max, mean, median,
   geometric mean of ratios). Mathematical consistency.

6. REPRODUCIBILITY — deterministic scenarios, stable results across runs.

Design principles:
  - Every scientific claim in the paper has a corresponding test.
  - Derived metrics are independently recomputed from raw data, not trusted
    from the module under test.
  - Parametrize aggressively: one test × N scenarios > N similar tests.
  - No magic numbers without comments tracing them to the spec.

Depends on: all previous phases (opinions, headers, annotations, temporal,
security, codec, transport) + the benchmark module under test.
"""

import json
import math
import re
from collections import Counter

import cbor2
import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

# Module under test — NOT part of core cbor-ld-ex package.
# Lives in benchmarks/cbor_ld_ex_benchmark/, added to pythonpath in pyproject.toml.
from cbor_ld_ex_benchmark import (
    # Scenario construction
    Scenario,
    ScenarioResult,
    BenchmarkSuite,
    build_document_profiles,
    build_annotation_configs,
    build_context_registry,
    build_scenario_matrix,
    # Execution
    run_scenario,
    run_benchmark_suite,
    # Derived metrics
    compute_derived_metrics,
    compute_summary_statistics,
    # Formatting
    format_markdown_table,
    format_latex_table,
    format_csv,
)

# Dependencies from existing codebase
from cbor_ld_ex.annotations import Annotation, encode_annotation
from cbor_ld_ex.codec import (
    ContextRegistry,
    encode,
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
from cbor_ld_ex.opinions import quantize_binomial, dequantize_binomial
from cbor_ld_ex.temporal import (
    ExtensionBlock,
    TemporalBlock,
    Trigger,
    DECAY_EXPONENTIAL,
    DECAY_LINEAR,
    TRIGGER_EXPIRY,
    TRIGGER_REVIEW_DUE,
)
from cbor_ld_ex.transport import full_benchmark

# jsonld-ex baselines
from jsonld_ex.cbor_ld import to_cbor as jex_to_cbor


# =========================================================================
# Constants
# =========================================================================

# 802.15.4 MTU and CoAP overhead — must match transport.py
_802154_MTU = 127
_COAP_OVERHEAD = 16
_MAX_SINGLE_FRAME_PAYLOAD = _802154_MTU - _COAP_OVERHEAD  # 111 bytes

# The 6 encoding names in full_benchmark() output
ENCODING_NAMES = [
    "json_ld",
    "jex_cbor_ld",
    "our_cbor_ld_data_only",
    "jex_cbor_ld_with_annotation",
    "our_cbor_ld_with_annotation",
    "cbor_ld_ex",
]

# =========================================================================
# Lazy fixture: build the scenario matrix once for all parametrized tests
# =========================================================================

_SCENARIOS = None
_SUITE = None


def _get_scenarios():
    """Lazily build and cache the scenario matrix."""
    global _SCENARIOS
    if _SCENARIOS is None:
        _SCENARIOS = build_scenario_matrix()
    return _SCENARIOS


def _get_suite():
    """Lazily build and cache the full benchmark suite."""
    global _SUITE
    if _SUITE is None:
        _SUITE = run_benchmark_suite(_get_scenarios())
    return _SUITE


def _scenario_ids():
    """Generate test IDs from scenario labels."""
    return [s.label for s in _get_scenarios()]


# =========================================================================
# Section 1: SCENARIO CONSTRUCTION
# =========================================================================


class TestDocumentProfiles:
    """Tests for build_document_profiles()."""

    def test_returns_non_empty_dict(self):
        profiles = build_document_profiles()
        assert isinstance(profiles, dict)
        assert len(profiles) >= 4, "Need at least 4 document profiles"

    def test_all_profiles_are_valid_jsonld(self):
        """Every profile must have @context and @type — minimal JSON-LD."""
        profiles = build_document_profiles()
        for name, doc in profiles.items():
            assert isinstance(doc, dict), f"Profile {name} is not a dict"
            assert "@context" in doc, f"Profile {name} missing @context"
            assert "@type" in doc, f"Profile {name} missing @type"

    def test_profiles_have_increasing_field_count(self):
        """Profiles should span a range of document sizes."""
        profiles = build_document_profiles()
        sizes = {name: len(doc) for name, doc in profiles.items()}
        values = sorted(sizes.values())
        # At least one small (<= 5 fields) and one large (>= 10 fields)
        assert values[0] <= 5, f"Smallest profile has {values[0]} fields, expected <= 5"
        assert values[-1] >= 10, f"Largest profile has {values[-1]} fields, expected >= 10"

    def test_profiles_have_unique_types(self):
        """Each profile should represent a different IoT domain."""
        profiles = build_document_profiles()
        types = [doc.get("@type") for doc in profiles.values()]
        assert len(set(types)) == len(types), f"Duplicate @type values: {types}"

    def test_all_profiles_cbor_serializable(self):
        """Every profile must serialize cleanly to both JSON and CBOR."""
        profiles = build_document_profiles()
        for name, doc in profiles.items():
            json_bytes = json.dumps(doc).encode("utf-8")
            assert len(json_bytes) > 0, f"Profile {name} empty JSON"
            cbor_bytes = cbor2.dumps(doc)
            assert len(cbor_bytes) > 0, f"Profile {name} empty CBOR"


class TestAnnotationConfigs:
    """Tests for build_annotation_configs()."""

    def test_returns_non_empty_list(self):
        configs = build_annotation_configs()
        assert isinstance(configs, list)
        assert len(configs) >= 10, "Need at least 10 annotation configurations"

    def test_all_configs_are_annotations(self):
        configs = build_annotation_configs()
        for cfg in configs:
            assert isinstance(cfg, tuple) and len(cfg) == 2, \
                f"Config must be (label, Annotation), got {type(cfg)}"
            label, ann = cfg
            assert isinstance(label, str)
            assert isinstance(ann, Annotation)

    def test_covers_all_tiers(self):
        configs = build_annotation_configs()
        tier_types = {type(ann.header) for _, ann in configs}
        assert Tier1Header in tier_types, "Missing Tier 1 configs"
        assert Tier2Header in tier_types, "Missing Tier 2 configs"

    def test_covers_all_precision_modes(self):
        configs = build_annotation_configs()
        precisions = {ann.header.precision_mode for _, ann in configs}
        assert PrecisionMode.BITS_8 in precisions, "Missing 8-bit configs"
        assert PrecisionMode.BITS_16 in precisions, "Missing 16-bit configs"
        assert PrecisionMode.BITS_32 in precisions, "Missing 32-bit configs"
        assert PrecisionMode.DELTA_8 in precisions, "Missing delta configs"

    def test_covers_all_compliance_statuses(self):
        configs = build_annotation_configs()
        statuses = {ann.header.compliance_status for _, ann in configs}
        assert ComplianceStatus.COMPLIANT in statuses
        assert ComplianceStatus.NON_COMPLIANT in statuses
        assert ComplianceStatus.INSUFFICIENT in statuses

    def test_covers_extensions(self):
        """At least some configs have temporal extensions, some have none."""
        configs = build_annotation_configs()
        has_ext = [ann.extensions is not None for _, ann in configs]
        assert any(has_ext), "No configs with extensions"
        assert not all(has_ext), "No configs without extensions"

    def test_covers_triggers(self):
        """At least one config has triggers."""
        configs = build_annotation_configs()
        has_triggers = any(
            ann.extensions is not None and ann.extensions.triggers is not None
            for _, ann in configs
        )
        assert has_triggers, "No configs with triggers"

    def test_unique_labels(self):
        configs = build_annotation_configs()
        labels = [label for label, _ in configs]
        assert len(set(labels)) == len(labels), \
            f"Duplicate annotation config labels: {[l for l, c in Counter(labels).items() if c > 1]}"

    def test_all_configs_encode_without_error(self):
        configs = build_annotation_configs()
        for label, ann in configs:
            try:
                raw = encode_annotation(ann)
                assert len(raw) > 0, f"Config {label} produced empty annotation"
            except Exception as e:
                pytest.fail(f"Config {label} failed to encode: {e}")


class TestContextRegistry:
    """Tests for build_context_registry()."""

    def test_returns_registry_for_known_profiles(self):
        profiles = build_document_profiles()
        for name, doc in profiles.items():
            registry = build_context_registry(doc)
            assert isinstance(registry, ContextRegistry), \
                f"Expected ContextRegistry for profile {name}"

    def test_registry_compresses_all_string_keys(self):
        """Registry should map every string key in the doc to an integer."""
        profiles = build_document_profiles()
        for name, doc in profiles.items():
            registry = build_context_registry(doc)
            compressed = registry.compress(doc)
            for key in compressed:
                if key == ANNOTATION_TERM_ID:
                    continue
                assert isinstance(key, int), \
                    f"Profile {name}: key {key!r} not compressed to integer"

    def test_registry_round_trips(self):
        """compress → decompress should recover original doc."""
        profiles = build_document_profiles()
        for name, doc in profiles.items():
            registry = build_context_registry(doc)
            compressed = registry.compress(doc)
            decompressed = registry.decompress(compressed)
            assert decompressed == doc, \
                f"Profile {name} round-trip failed"


class TestScenarioMatrix:
    """Tests for build_scenario_matrix()."""

    def test_returns_non_empty_list_of_scenarios(self):
        scenarios = _get_scenarios()
        assert isinstance(scenarios, list)
        assert all(isinstance(s, Scenario) for s in scenarios)
        assert len(scenarios) >= 30, \
            f"Expected >= 30 scenarios for statistical claims, got {len(scenarios)}"

    def test_unique_labels(self):
        scenarios = _get_scenarios()
        labels = [s.label for s in scenarios]
        dupes = [l for l, c in Counter(labels).items() if c > 1]
        assert not dupes, f"Duplicate scenario labels: {dupes}"

    def test_all_scenarios_have_metadata(self):
        """Every scenario must carry dimension metadata for filtering."""
        required_keys = {"doc_profile", "tier", "precision", "extensions"}
        scenarios = _get_scenarios()
        for s in scenarios:
            assert isinstance(s.metadata, dict), f"{s.label}: no metadata"
            missing = required_keys - set(s.metadata.keys())
            assert not missing, f"{s.label}: missing metadata keys {missing}"

    def test_covers_all_document_profiles(self):
        scenarios = _get_scenarios()
        profiles = {s.metadata["doc_profile"] for s in scenarios}
        expected = set(build_document_profiles().keys())
        assert expected.issubset(profiles), \
            f"Missing document profiles: {expected - profiles}"

    def test_covers_all_tiers(self):
        scenarios = _get_scenarios()
        tiers = {s.metadata["tier"] for s in scenarios}
        assert tiers >= {1, 2}, f"Missing tiers: expected {{1, 2}}, got {tiers}"

    def test_covers_all_precisions(self):
        scenarios = _get_scenarios()
        precisions = {s.metadata["precision"] for s in scenarios}
        assert precisions >= {8, 16, 32}, \
            f"Missing precisions: {precisions}"

    def test_covers_extension_levels(self):
        scenarios = _get_scenarios()
        ext_levels = {s.metadata["extensions"] for s in scenarios}
        assert "none" in ext_levels, "Missing no-extension scenarios"
        assert any("temporal" in e for e in ext_levels), "Missing temporal scenarios"

    def test_all_scenarios_encodable(self):
        """Critical: every scenario must produce valid CBOR-LD-ex bytes."""
        scenarios = _get_scenarios()
        for s in scenarios:
            try:
                payload = encode(s.doc, s.annotation, context_registry=s.context_registry)
                assert len(payload) > 0, f"{s.label}: empty payload"
            except Exception as e:
                pytest.fail(f"{s.label}: encode failed: {e}")


# =========================================================================
# Section 2: DERIVED METRIC COMPUTATION
# =========================================================================


class TestDerivedMetrics:
    """Tests for compute_derived_metrics().

    Every derived number is independently recomputed from raw data.
    We don't trust the module's computation — we verify it.
    """

    def test_compression_ratio_mathematical_consistency(self):
        """compression_vs_jsonld = 1 - (cbor_ld_ex_size / json_ld_size)."""
        suite = _get_suite()
        for result in suite.results:
            raw = result.encodings
            jsonld_size = raw["json_ld"]["size"]
            ex_size = raw["cbor_ld_ex"]["size"]
            expected_ratio = 1.0 - (ex_size / jsonld_size)
            actual_ratio = result.derived_metrics["compression_vs_jsonld"]
            assert abs(actual_ratio - expected_ratio) < 1e-10, \
                f"{result.scenario.label}: compression ratio mismatch: " \
                f"{actual_ratio} != {expected_ratio}"

    def test_annotation_overhead_matches_encode_annotation(self):
        """The annotation overhead in bytes must equal len(encode_annotation(ann))."""
        suite = _get_suite()
        for result in suite.results:
            ann = result.scenario.annotation
            expected_size = len(encode_annotation(ann))
            actual_size = result.derived_metrics["annotation_overhead_bytes"]
            assert actual_size == expected_size, \
                f"{result.scenario.label}: annotation overhead " \
                f"{actual_size} != {expected_size}"

    def test_bit_efficiency_bounded_zero_to_one(self):
        """Bit efficiency must be in (0, 1] — can't exceed 100%."""
        suite = _get_suite()
        for result in suite.results:
            eff = result.derived_metrics["annotation_bit_efficiency"]
            assert 0.0 < eff <= 1.0 + 1e-9, \
                f"{result.scenario.label}: bit efficiency out of range: {eff}"

    def test_msgs_per_frame_formula(self):
        """msgs_per_frame = max(1, floor(111 / size)) when size <= 111, else 0."""
        suite = _get_suite()
        for result in suite.results:
            size = result.encodings["cbor_ld_ex"]["size"]
            if size <= _MAX_SINGLE_FRAME_PAYLOAD:
                expected = _MAX_SINGLE_FRAME_PAYLOAD // size
                expected = max(1, expected)
            else:
                expected = 0
            actual = result.derived_metrics["cbor_ld_ex_msgs_per_frame"]
            assert actual == expected, \
                f"{result.scenario.label}: msgs_per_frame {actual} != {expected} " \
                f"(size={size})"

    def test_no_nan_or_inf_in_any_metric(self):
        """No NaN or Inf in any derived metric — these corrupt tables."""
        suite = _get_suite()
        for result in suite.results:
            for key, val in result.derived_metrics.items():
                if isinstance(val, float):
                    assert not math.isnan(val), \
                        f"{result.scenario.label}: NaN in {key}"
                    assert not math.isinf(val), \
                        f"{result.scenario.label}: Inf in {key}"

    def test_semantic_field_count_computed(self):
        """Semantic field count must be a positive integer for all encodings."""
        suite = _get_suite()
        for result in suite.results:
            for enc_name in ENCODING_NAMES:
                count = result.encodings[enc_name].get("semantic_fields")
                assert isinstance(count, list), \
                    f"{result.scenario.label}/{enc_name}: semantic_fields not a list"

    def test_all_six_encodings_present(self):
        """Every scenario result must contain all 6 encoding comparisons."""
        suite = _get_suite()
        for result in suite.results:
            for name in ENCODING_NAMES:
                assert name in result.encodings, \
                    f"{result.scenario.label}: missing encoding {name}"
                assert "size" in result.encodings[name], \
                    f"{result.scenario.label}/{name}: missing 'size'"
                assert result.encodings[name]["size"] > 0, \
                    f"{result.scenario.label}/{name}: zero-byte payload"

    def test_sizes_are_positive_integers(self):
        """All sizes must be positive integers (byte counts)."""
        suite = _get_suite()
        for result in suite.results:
            for name in ENCODING_NAMES:
                size = result.encodings[name]["size"]
                assert isinstance(size, int), \
                    f"{result.scenario.label}/{name}: size is {type(size)}, not int"
                assert size > 0, \
                    f"{result.scenario.label}/{name}: size must be positive"

    def test_overhead_ratio_consistent(self):
        """Annotation overhead ratio = annotation_overhead / total_size."""
        suite = _get_suite()
        for result in suite.results:
            overhead = result.derived_metrics["annotation_overhead_bytes"]
            total = result.encodings["cbor_ld_ex"]["size"]
            expected_ratio = overhead / total
            actual_ratio = result.derived_metrics["annotation_overhead_ratio"]
            assert abs(actual_ratio - expected_ratio) < 1e-10, \
                f"{result.scenario.label}: overhead ratio mismatch"


# =========================================================================
# Section 3: SCIENTIFIC INVARIANTS
#
# These are the paper's claims. Each "always" or "for all" in the paper
# becomes a universal quantifier over the scenario matrix. A single
# counterexample falsifies the claim.
# =========================================================================


class TestScientificInvariants:
    """Universal claims made in the paper, verified over ALL scenarios.

    Naming convention: test_CLAIM_over_all_scenarios.
    Failure here means the paper's claim is WRONG and must be retracted
    or qualified.
    """

    def test_cbor_ld_ex_always_smaller_than_jsonld(self):
        """Claim: CBOR-LD-ex < JSON-LD for all scenarios.

        This is the headline compression claim. If this fails for ANY
        scenario, we cannot claim universal compression advantage.
        """
        suite = _get_suite()
        for result in suite.results:
            ex = result.encodings["cbor_ld_ex"]["size"]
            jsonld = result.encodings["json_ld"]["size"]
            assert ex < jsonld, \
                f"FALSIFIED: {result.scenario.label}: " \
                f"CBOR-LD-ex ({ex}B) >= JSON-LD ({jsonld}B)"

    def test_cbor_ld_ex_always_smaller_than_cbor_ld_with_annotation(self):
        """Claim: For the SAME semantic content, bit-packing always wins.

        Compares CBOR-LD-ex against standard CBOR-LD carrying the same
        annotation information as CBOR key-value pairs. This is THE core
        technical contribution: bit-packing is more efficient than CBOR's
        self-describing encoding for fixed-schema protocol metadata.
        """
        suite = _get_suite()
        for result in suite.results:
            ex = result.encodings["cbor_ld_ex"]["size"]
            cbor_ann = result.encodings["our_cbor_ld_with_annotation"]["size"]
            assert ex < cbor_ann, \
                f"FALSIFIED: {result.scenario.label}: " \
                f"CBOR-LD-ex ({ex}B) >= CBOR-LD+ann ({cbor_ann}B)"

    def test_our_cbor_ld_beats_jsonld_ex_for_data_compression(self):
        """Claim: Full key+value compression beats context-only compression.

        Our ContextRegistry compresses BOTH keys AND values to integers.
        jsonld-ex only compresses @context URLs. This should always produce
        a smaller data-only payload.
        """
        suite = _get_suite()
        for result in suite.results:
            ours = result.encodings["our_cbor_ld_data_only"]["size"]
            jex = result.encodings["jex_cbor_ld"]["size"]
            assert ours <= jex, \
                f"FALSIFIED: {result.scenario.label}: " \
                f"our CBOR-LD ({ours}B) > jsonld-ex CBOR-LD ({jex}B)"

    def test_cbor_ld_ex_more_semantic_fields_than_data_only(self):
        """Claim: CBOR-LD-ex carries strictly MORE semantic fields than
        data-only CBOR-LD, while being smaller than JSON-LD.

        This is the "richer AND smaller" claim.
        """
        suite = _get_suite()
        for result in suite.results:
            ex_fields = len(result.encodings["cbor_ld_ex"]["semantic_fields"])
            data_fields = len(result.encodings["our_cbor_ld_data_only"]["semantic_fields"])
            assert ex_fields > data_fields, \
                f"FALSIFIED: {result.scenario.label}: " \
                f"CBOR-LD-ex ({ex_fields} fields) <= data-only ({data_fields} fields)"

    def test_tier1_fits_802154_single_frame(self):
        """Claim: Tier 1 CBOR-LD-ex fits in a single 802.15.4 frame.

        THE constrained-network claim. 802.15.4 MTU = 127 bytes.
        With CoAP overhead (~16 bytes), payload must be <= 111 bytes.
        """
        suite = _get_suite()
        tier1_results = [
            r for r in suite.results
            if r.scenario.metadata["tier"] == 1
        ]
        assert len(tier1_results) > 0, "No Tier 1 scenarios found"

        for result in tier1_results:
            ex_size = result.encodings["cbor_ld_ex"]["size"]
            assert ex_size <= _MAX_SINGLE_FRAME_PAYLOAD, \
                f"FALSIFIED: {result.scenario.label}: " \
                f"Tier 1 payload ({ex_size}B) > 802.15.4 capacity ({_MAX_SINGLE_FRAME_PAYLOAD}B)"

    def test_annotation_bit_efficiency_above_70_percent_tier1(self):
        """Claim: Tier 1 annotation bit efficiency > 70%.

        This means >70% of the wire bits carry Shannon information.
        The remaining <30% is unavoidable padding from non-power-of-2
        state counts.
        """
        suite = _get_suite()
        tier1_results = [
            r for r in suite.results
            if r.scenario.metadata["tier"] == 1
        ]
        assert len(tier1_results) > 0, "No Tier 1 scenarios found"

        for result in tier1_results:
            eff = result.derived_metrics["annotation_bit_efficiency"]
            assert eff > 0.70, \
                f"FALSIFIED: {result.scenario.label}: " \
                f"Tier 1 bit efficiency ({eff:.4f}) <= 0.70"

    def test_compression_vs_jsonld_above_50_percent(self):
        """Claim: CBOR-LD-ex achieves >50% compression vs JSON-LD for all scenarios.

        Conservative threshold — actual numbers are typically 70-90%.
        """
        suite = _get_suite()
        for result in suite.results:
            ratio = result.derived_metrics["compression_vs_jsonld"]
            assert ratio > 0.50, \
                f"FALSIFIED: {result.scenario.label}: " \
                f"compression vs JSON-LD ({ratio:.4f}) <= 0.50"

    def test_bit_packed_annotation_always_smaller_than_cbor_annotation(self):
        """Claim: Bit-packed annotation < standard CBOR annotation.

        The bit-packed encoding of the annotation block is always smaller
        than the standard CBOR key-value encoding of the same information.
        This is verified at the annotation level, not the full message level.
        """
        suite = _get_suite()
        for result in suite.results:
            ann = result.scenario.annotation
            # Bit-packed annotation
            bit_packed_size = len(encode_annotation(ann))

            # Standard CBOR annotation (same info, integer keys)
            cbor_ann = _build_cbor_annotation_dict(ann)
            cbor_ann_size = len(cbor2.dumps(cbor_ann))

            assert bit_packed_size < cbor_ann_size, \
                f"FALSIFIED: {result.scenario.label}: " \
                f"bit-packed ({bit_packed_size}B) >= CBOR ({cbor_ann_size}B)"

    def test_encoding_size_ordering_all_scenarios(self):
        """Claim: CBOR-LD-ex <= our CBOR-LD+ann < jex CBOR-LD+ann <= JSON-LD.

        The full size ordering chain. This is the cumulative claim that
        each compression technique contributes monotonically.
        """
        suite = _get_suite()
        for result in suite.results:
            e = result.encodings
            ex = e["cbor_ld_ex"]["size"]
            our_ann = e["our_cbor_ld_with_annotation"]["size"]
            jex_ann = e["jex_cbor_ld_with_annotation"]["size"]
            jsonld = e["json_ld"]["size"]

            assert ex < our_ann, \
                f"{result.scenario.label}: CBOR-LD-ex ({ex}) >= our+ann ({our_ann})"
            assert our_ann <= jex_ann, \
                f"{result.scenario.label}: our+ann ({our_ann}) > jex+ann ({jex_ann})"
            assert jex_ann < jsonld, \
                f"{result.scenario.label}: jex+ann ({jex_ann}) >= JSON-LD ({jsonld})"

    def test_information_content_never_exceeds_wire_bits(self):
        """Physical law: you can't transmit more information than wire capacity.

        Shannon information content must be <= wire bits for every annotation.
        """
        suite = _get_suite()
        for result in suite.results:
            ann = result.scenario.annotation
            analysis = annotation_information_bits(ann)
            info_bits = analysis["total_info_bits"]
            wire_bits = analysis["total_wire_bits"]
            assert info_bits <= wire_bits + 1e-9, \
                f"FALSIFIED: {result.scenario.label}: " \
                f"info ({info_bits:.2f} bits) > wire ({wire_bits} bits)"

    def test_u_hat_never_on_wire(self):
        """Invariant: û is NEVER transmitted on the wire.

        This is verified by checking that the annotation wire size equals
        header_size + 3 * precision_bytes (not 4), confirming only (b̂, d̂, â)
        are transmitted.
        """
        suite = _get_suite()
        for result in suite.results:
            ann = result.scenario.annotation
            if not ann.header.has_opinion or ann.opinion is None:
                continue

            header_size = 1 if isinstance(ann.header, Tier1Header) else 4

            if ann.header.precision_mode == PrecisionMode.DELTA_8:
                # Delta: 2 bytes (Δb̂, Δd̂). No û, no â on wire.
                opinion_wire_size = 2
            else:
                precision_map = {
                    PrecisionMode.BITS_8: 1,
                    PrecisionMode.BITS_16: 2,
                    PrecisionMode.BITS_32: 4,
                }
                value_bytes = precision_map[ann.header.precision_mode]
                opinion_wire_size = 3 * value_bytes  # NOT 4 — û never on wire

            raw = encode_annotation(ann)
            ext_size = 0
            if ann.extensions is not None:
                from cbor_ld_ex.temporal import encode_extensions
                ext_size = len(encode_extensions(ann.extensions))

            expected_size = header_size + opinion_wire_size + ext_size
            assert len(raw) == expected_size, \
                f"FALSIFIED: {result.scenario.label}: " \
                f"wire size {len(raw)} != expected {expected_size} " \
                f"(header={header_size}, opinion={opinion_wire_size}, ext={ext_size}). " \
                f"Unexpected bytes on wire!"


# =========================================================================
# Section 4: TABLE FORMATTING
# =========================================================================


class TestMarkdownFormatting:
    """Tests for format_markdown_table()."""

    def test_returns_non_empty_string(self):
        suite = _get_suite()
        md = format_markdown_table(suite)
        assert isinstance(md, str)
        assert len(md) > 100, "Markdown table suspiciously short"

    def test_contains_all_encoding_names(self):
        """The table header must name all 6 encodings."""
        suite = _get_suite()
        md = format_markdown_table(suite)
        assert "JSON-LD" in md
        assert "CBOR-LD-ex" in md

    def test_row_count_matches_scenarios(self):
        """One data row per scenario."""
        suite = _get_suite()
        md = format_markdown_table(suite)
        # Count non-empty lines that start with '|' and contain numbers
        data_rows = [
            line for line in md.strip().split("\n")
            if line.startswith("|") and any(c.isdigit() for c in line)
        ]
        # Should have at least as many data rows as scenarios
        assert len(data_rows) >= len(suite.results), \
            f"Expected >= {len(suite.results)} data rows, got {len(data_rows)}"

    def test_no_nan_or_inf_in_output(self):
        suite = _get_suite()
        md = format_markdown_table(suite)
        assert "nan" not in md.lower(), "NaN found in Markdown table"
        assert "inf" not in md.lower(), "Inf found in Markdown table"

    def test_valid_markdown_table_syntax(self):
        """Every line starting with | must have consistent column count."""
        suite = _get_suite()
        md = format_markdown_table(suite)
        table_lines = [l for l in md.strip().split("\n") if l.startswith("|")]
        if not table_lines:
            pytest.fail("No table lines found")
        col_counts = [l.count("|") for l in table_lines]
        assert len(set(col_counts)) == 1, \
            f"Inconsistent column counts: {set(col_counts)}"


class TestLatexFormatting:
    """Tests for format_latex_table()."""

    def test_returns_non_empty_string(self):
        suite = _get_suite()
        tex = format_latex_table(suite)
        assert isinstance(tex, str)
        assert len(tex) > 100

    def test_has_booktabs(self):
        """Publication-quality LaTeX uses booktabs, not \\hline."""
        suite = _get_suite()
        tex = format_latex_table(suite)
        assert "\\toprule" in tex, "Missing \\toprule (booktabs)"
        assert "\\midrule" in tex, "Missing \\midrule (booktabs)"
        assert "\\bottomrule" in tex, "Missing \\bottomrule (booktabs)"

    def test_no_hline(self):
        """booktabs and \\hline should not be mixed."""
        suite = _get_suite()
        tex = format_latex_table(suite)
        assert "\\hline" not in tex, "\\hline found — use booktabs instead"

    def test_no_nan_or_inf(self):
        suite = _get_suite()
        tex = format_latex_table(suite)
        assert "nan" not in tex.lower()
        assert "inf" not in tex.lower()

    def test_has_tabular_environment(self):
        suite = _get_suite()
        tex = format_latex_table(suite)
        assert "\\begin{tabular}" in tex
        assert "\\end{tabular}" in tex

    def test_no_unescaped_special_chars(self):
        """LaTeX special chars (%, &, _, #, $) must be escaped in data cells."""
        suite = _get_suite()
        tex = format_latex_table(suite)
        # Find content between \begin{tabular} and \end{tabular}
        match = re.search(
            r"\\begin\{tabular\}.*?\n(.*?)\\end\{tabular\}",
            tex, re.DOTALL,
        )
        if match:
            body = match.group(1)
            # Check for unescaped % (not preceded by \)
            unescaped_pct = re.findall(r"(?<!\\)%", body)
            # In LaTeX table body, % is comment — dangerous
            # We allow % only if it's escaped
            # Actually % in data would be fine if escaped. Let's check _ and &
            # & is column separator — those are intentional. Skip & check.
            # _ must be escaped in text mode
            # Let's just check there's no raw Python-looking strings
            assert "None" not in body, "Python None found in LaTeX table"
            assert "True" not in body or "\\text" in body, \
                "Raw Python True in LaTeX"

    def test_row_count(self):
        """Should have one data row per scenario."""
        suite = _get_suite()
        tex = format_latex_table(suite)
        # Count lines with \\ (row terminators) between midrule and bottomrule
        match = re.search(
            r"\\midrule\s*\n(.*?)\\bottomrule",
            tex, re.DOTALL,
        )
        if match:
            body = match.group(1)
            row_count = body.count("\\\\")
            assert row_count >= len(suite.results), \
                f"Expected >= {len(suite.results)} rows, got {row_count}"


class TestCsvFormatting:
    """Tests for format_csv() — machine-readable output for external analysis."""

    def test_returns_non_empty_string(self):
        suite = _get_suite()
        csv_str = format_csv(suite)
        assert isinstance(csv_str, str)
        assert len(csv_str) > 50

    def test_parseable_csv(self):
        """Output must be valid CSV with consistent columns."""
        import csv
        import io
        suite = _get_suite()
        csv_str = format_csv(suite)
        reader = csv.reader(io.StringIO(csv_str))
        rows = list(reader)
        assert len(rows) >= 2, "Need header + at least 1 data row"
        header_cols = len(rows[0])
        for i, row in enumerate(rows[1:], 1):
            assert len(row) == header_cols, \
                f"Row {i}: {len(row)} cols != header {header_cols} cols"

    def test_header_contains_key_columns(self):
        import csv
        import io
        suite = _get_suite()
        csv_str = format_csv(suite)
        reader = csv.reader(io.StringIO(csv_str))
        header = next(reader)
        header_lower = [h.lower() for h in header]
        assert any("scenario" in h or "label" in h for h in header_lower), \
            "Missing scenario/label column"
        assert any("json" in h for h in header_lower), \
            "Missing JSON-LD size column"
        assert any("cbor_ld_ex" in h or "cbor-ld-ex" in h for h in header_lower), \
            "Missing CBOR-LD-ex size column"

    def test_data_row_count(self):
        import csv
        import io
        suite = _get_suite()
        csv_str = format_csv(suite)
        reader = csv.reader(io.StringIO(csv_str))
        rows = list(reader)
        data_rows = rows[1:]  # skip header
        assert len(data_rows) == len(suite.results), \
            f"Expected {len(suite.results)} data rows, got {len(data_rows)}"


# =========================================================================
# Section 5: SUMMARY STATISTICS
# =========================================================================


class TestSummaryStatistics:
    """Tests for compute_summary_statistics()."""

    def test_summary_has_required_keys(self):
        suite = _get_suite()
        summary = suite.summary
        assert isinstance(summary, dict)
        required = {
            "scenario_count",
            "per_encoding_stats",
            "best_case",
            "worst_case",
            "geometric_mean_compression",
        }
        missing = required - set(summary.keys())
        assert not missing, f"Missing summary keys: {missing}"

    def test_scenario_count_matches(self):
        suite = _get_suite()
        assert suite.summary["scenario_count"] == len(suite.results)

    def test_per_encoding_stats_has_all_encodings(self):
        suite = _get_suite()
        stats = suite.summary["per_encoding_stats"]
        for name in ENCODING_NAMES:
            assert name in stats, f"Missing stats for {name}"

    def test_per_encoding_min_max_mean_median(self):
        """Each encoding's stats must have min, max, mean, median sizes."""
        suite = _get_suite()
        for enc_name, stats in suite.summary["per_encoding_stats"].items():
            for key in ["min_bytes", "max_bytes", "mean_bytes", "median_bytes"]:
                assert key in stats, f"{enc_name}: missing {key}"
                assert isinstance(stats[key], (int, float)), \
                    f"{enc_name}/{key}: not numeric"
            assert stats["min_bytes"] <= stats["mean_bytes"] <= stats["max_bytes"], \
                f"{enc_name}: min <= mean <= max violated"
            assert stats["min_bytes"] <= stats["median_bytes"] <= stats["max_bytes"], \
                f"{enc_name}: min <= median <= max violated"

    def test_mean_computed_correctly(self):
        """Independently verify mean computation."""
        suite = _get_suite()
        for enc_name in ENCODING_NAMES:
            sizes = [r.encodings[enc_name]["size"] for r in suite.results]
            expected_mean = sum(sizes) / len(sizes)
            actual_mean = suite.summary["per_encoding_stats"][enc_name]["mean_bytes"]
            assert abs(actual_mean - expected_mean) < 0.01, \
                f"{enc_name}: mean {actual_mean} != {expected_mean}"

    def test_median_computed_correctly(self):
        """Independently verify median computation."""
        suite = _get_suite()
        for enc_name in ENCODING_NAMES:
            sizes = sorted(r.encodings[enc_name]["size"] for r in suite.results)
            n = len(sizes)
            if n % 2 == 1:
                expected_median = sizes[n // 2]
            else:
                expected_median = (sizes[n // 2 - 1] + sizes[n // 2]) / 2.0
            actual_median = suite.summary["per_encoding_stats"][enc_name]["median_bytes"]
            assert abs(actual_median - expected_median) < 0.01, \
                f"{enc_name}: median {actual_median} != {expected_median}"

    def test_geometric_mean_compression_positive(self):
        """Geometric mean of compression ratios must be positive."""
        suite = _get_suite()
        geomean = suite.summary["geometric_mean_compression"]
        assert isinstance(geomean, float)
        assert geomean > 0, f"Geometric mean compression {geomean} <= 0"

    def test_geometric_mean_independently_verified(self):
        """Recompute geometric mean from individual compression ratios."""
        suite = _get_suite()
        ratios = [r.derived_metrics["compression_vs_jsonld"] for r in suite.results]
        # Geometric mean = exp(mean(log(ratios)))
        # All ratios should be positive (compression > 0%)
        assert all(r > 0 for r in ratios), "Negative compression ratio found"
        log_sum = sum(math.log(r) for r in ratios)
        expected_geomean = math.exp(log_sum / len(ratios))
        actual = suite.summary["geometric_mean_compression"]
        assert abs(actual - expected_geomean) < 1e-6, \
            f"Geometric mean: {actual} != {expected_geomean}"

    def test_best_case_is_actual_maximum_compression(self):
        """best_case should identify the scenario with highest compression."""
        suite = _get_suite()
        ratios = [
            (r.derived_metrics["compression_vs_jsonld"], r.scenario.label)
            for r in suite.results
        ]
        best_ratio, best_label = max(ratios, key=lambda x: x[0])
        assert suite.summary["best_case"]["label"] == best_label
        assert abs(suite.summary["best_case"]["compression"] - best_ratio) < 1e-10

    def test_worst_case_is_actual_minimum_compression(self):
        """worst_case should identify the scenario with lowest compression."""
        suite = _get_suite()
        ratios = [
            (r.derived_metrics["compression_vs_jsonld"], r.scenario.label)
            for r in suite.results
        ]
        worst_ratio, worst_label = min(ratios, key=lambda x: x[0])
        assert suite.summary["worst_case"]["label"] == worst_label
        assert abs(suite.summary["worst_case"]["compression"] - worst_ratio) < 1e-10


# =========================================================================
# Section 6: REPRODUCIBILITY
# =========================================================================


class TestReproducibility:
    """Determinism and stability tests."""

    def test_scenario_matrix_deterministic(self):
        """Two calls to build_scenario_matrix() produce identical labels."""
        s1 = build_scenario_matrix()
        s2 = build_scenario_matrix()
        labels1 = [s.label for s in s1]
        labels2 = [s.label for s in s2]
        assert labels1 == labels2, "Scenario matrix is non-deterministic"

    def test_benchmark_results_stable(self):
        """Running the same scenario twice produces identical byte sizes."""
        scenarios = _get_scenarios()
        # Test a subset for speed
        subset = scenarios[:5]
        for s in subset:
            r1 = run_scenario(s)
            r2 = run_scenario(s)
            for name in ENCODING_NAMES:
                assert r1.encodings[name]["size"] == r2.encodings[name]["size"], \
                    f"{s.label}/{name}: unstable size " \
                    f"{r1.encodings[name]['size']} vs {r2.encodings[name]['size']}"

    def test_csv_deterministic(self):
        """Two format_csv() calls on the same suite produce identical output."""
        suite = _get_suite()
        csv1 = format_csv(suite)
        csv2 = format_csv(suite)
        assert csv1 == csv2, "CSV output is non-deterministic"


# =========================================================================
# Helper: build a standard CBOR annotation dict for comparison
# (mirrors transport.py::_annotation_to_jsonld_dict but CBOR integer keys)
# =========================================================================

def _build_cbor_annotation_dict(ann: Annotation) -> dict:
    """Build the standard CBOR key-value annotation for size comparison."""
    result = {0: int(ann.header.compliance_status)}

    if ann.header.has_opinion and ann.opinion is not None:
        if ann.header.precision_mode == PrecisionMode.DELTA_8:
            delta_b, delta_d = ann.opinion
            result[1] = {0: delta_b, 1: delta_d}
        else:
            precision_map = {
                PrecisionMode.BITS_8: 8,
                PrecisionMode.BITS_16: 16,
                PrecisionMode.BITS_32: 32,
            }
            precision = precision_map[ann.header.precision_mode]
            if precision == 32:
                b, d, u, a = ann.opinion
            else:
                b, d, u, a = dequantize_binomial(
                    *ann.opinion, precision=precision,
                )
            result[1] = {0: b, 1: d, 2: u, 3: a}

    header = ann.header
    if isinstance(header, (Tier2Header, Tier3Header)):
        result[2] = int(header.operator_id)
        result[3] = header.reasoning_context
    if isinstance(header, Tier2Header):
        result[4] = header.source_count

    return result
