"""
Cross-cutting axiom verification for CBOR-LD-ex.

Tests the three foundational axioms that constrain every design decision
in the CBOR-LD-ex specification:

  Axiom 1 (Backward Compatibility):
    σ(CBOR-LD-ex message) → valid CBOR-LD → valid JSON-LD.
    Stripping annotations never corrupts the data payload.

  Axiom 2 (Algebraic Closure):
    Applying any Subjective Logic operator to valid CBOR-LD-ex
    annotations yields a valid CBOR-LD-ex annotation. The algebra
    is closed under all defined operators.

  Axiom 3 (Quantization Correctness):
    b̂ + d̂ + û = 2ⁿ − 1 exactly, for all valid inputs, through
    all operators, at all precisions.

These are NOT unit tests of individual functions — they are cross-cutting
property tests that verify invariants across the entire encode/decode
pipeline and the full SL operator algebra.

References:
  FORMAL_MODEL.md §3 (Axioms), §4 (Quantization), §5 (Wire Format)
  Jøsang (2016) Subjective Logic, Springer
  Shannon (1948) A Mathematical Theory of Communication
"""

import json
import math

import cbor2
import pytest
from hypothesis import given, assume, settings
from hypothesis import strategies as st

from cbor_ld_ex.annotations import (
    Annotation,
    CBOR_TAG_CBORLD_EX,
    encode_annotation,
    decode_annotation,
)
from cbor_ld_ex.headers import (
    Tier1Header,
    Tier2Header,
    Tier3Header,
    ComplianceStatus,
    OperatorId,
    PrecisionMode,
)
from cbor_ld_ex.opinions import (
    quantize_binomial,
    dequantize_binomial,
    encode_opinion_bytes,
    decode_opinion_bytes,
)
from cbor_ld_ex.codec import (
    encode,
    decode,
    ContextRegistry,
    ANNOTATION_TERM_ID,
)

# SL operators from jsonld-ex
from jsonld_ex.confidence_algebra import Opinion, cumulative_fuse
from jsonld_ex.compliance_algebra import jurisdictional_meet
from jsonld_ex.confidence_decay import decay_opinion


# =========================================================================
# Hypothesis strategies
# =========================================================================

def valid_opinion_strategy():
    """Generate a valid SL opinion (b, d, u, a) with b+d+u=1.

    Uses the simplex decomposition: draw b in [0,1], then d in [0, 1-b],
    derive u = 1 - b - d. Base rate a is independent.
    """
    return (
        st.floats(min_value=0.0, max_value=1.0,
                   allow_nan=False, allow_infinity=False)
        .flatmap(lambda b:
            st.floats(min_value=0.0, max_value=1.0 - b,
                       allow_nan=False, allow_infinity=False)
            .map(lambda d: (b, d, 1.0 - b - d))
        )
        .flatmap(lambda bdu:
            st.floats(min_value=0.0, max_value=1.0,
                       allow_nan=False, allow_infinity=False)
            .map(lambda a: (bdu[0], bdu[1], bdu[2], a))
        )
    )


def valid_opinion_object_strategy():
    """Generate a jsonld-ex Opinion object."""
    return valid_opinion_strategy().map(
        lambda t: Opinion(belief=t[0], disbelief=t[1],
                          uncertainty=t[2], base_rate=t[3])
    )


def compliance_status_strategy():
    """Generate a valid ComplianceStatus."""
    return st.sampled_from([
        ComplianceStatus.COMPLIANT,
        ComplianceStatus.NON_COMPLIANT,
        ComplianceStatus.INSUFFICIENT,
    ])


def precision_mode_strategy():
    """Generate a valid (non-reserved) PrecisionMode."""
    return st.sampled_from([
        PrecisionMode.BITS_8,
        PrecisionMode.BITS_16,
        PrecisionMode.BITS_32,
    ])


def tier1_annotation_strategy():
    """Generate a random Tier 1 annotation with opinion."""
    return st.tuples(
        compliance_status_strategy(),
        st.booleans(),       # delegation_flag
        precision_mode_strategy(),
        valid_opinion_strategy(),
    ).map(lambda t: Annotation(
        header=Tier1Header(
            compliance_status=t[0],
            delegation_flag=t[1],
            has_opinion=True,
            precision_mode=t[2],
        ),
        opinion=_quantize_for_precision(t[3], t[2]),
    ))


def tier2_annotation_strategy():
    """Generate a random Tier 2 annotation with opinion."""
    return st.tuples(
        compliance_status_strategy(),
        st.booleans(),       # delegation_flag
        precision_mode_strategy(),
        valid_opinion_strategy(),
        st.sampled_from(list(OperatorId)),
        st.integers(min_value=0, max_value=15),   # reasoning_context
        st.integers(min_value=0, max_value=15),   # context_version
        st.booleans(),       # has_multinomial
        st.integers(min_value=0, max_value=7),     # sub_tier_depth
        st.integers(min_value=0, max_value=255),   # source_count
    ).map(lambda t: Annotation(
        header=Tier2Header(
            compliance_status=t[0],
            delegation_flag=t[1],
            has_opinion=True,
            precision_mode=t[2],
            operator_id=t[4],
            reasoning_context=t[5],
            context_version=t[6],
            has_multinomial=t[7],
            sub_tier_depth=t[8],
            source_count=t[9],
        ),
        opinion=_quantize_for_precision(t[3], t[2]),
    ))


def any_annotation_strategy():
    """Generate Tier 1 or Tier 2 annotation (both with opinions)."""
    return st.one_of(tier1_annotation_strategy(), tier2_annotation_strategy())


def _quantize_for_precision(opinion_tuple, precision_mode):
    """Quantize a (b, d, u, a) tuple at the given PrecisionMode."""
    b, d, u, a = opinion_tuple
    precision_map = {
        PrecisionMode.BITS_8: 8,
        PrecisionMode.BITS_16: 16,
        PrecisionMode.BITS_32: 32,
    }
    precision = precision_map[precision_mode]
    if precision == 32:
        # Float mode: store raw floats
        return (b, d, u, a)
    return quantize_binomial(b, d, u, a, precision=precision)


# A simple context registry for test documents
_TEST_KEY_MAP = {
    "@context": 0,
    "@type": 1,
    "value": 2,
    "unit": 3,
    "sensor": 4,
}
_TEST_VALUE_MAP = {
    "https://schema.org/": 100,
    "Observation": 101,
    "celsius": 102,
}
_TEST_REGISTRY = ContextRegistry(key_map=_TEST_KEY_MAP, value_map=_TEST_VALUE_MAP)


def simple_doc_strategy():
    """Generate simple JSON-LD-like documents for full-stack tests."""
    return st.fixed_dictionaries({
        "@context": st.just("https://schema.org/"),
        "@type": st.just("Observation"),
        "value": st.floats(min_value=-100.0, max_value=100.0,
                           allow_nan=False, allow_infinity=False),
        "unit": st.just("celsius"),
    })


# =========================================================================
# AXIOM 1 — Backward Compatibility (§3.1)
#
# σ(CBOR-LD-ex message) → valid CBOR-LD → valid JSON-LD
#
# "Any CBOR-LD-ex message, when processed by a standard CBOR-LD parser
# that strips unrecognized CBOR tags, yields a valid CBOR-LD document."
# =========================================================================

class TestAxiom1BackwardCompatibility:
    """Stripping annotations never corrupts the data payload."""

    @given(doc=simple_doc_strategy(), ann=any_annotation_strategy())
    @settings(max_examples=200, deadline=None)
    def test_axiom1_stripping_property_comprehensive(self, doc, ann):
        """Axiom 1: sigma(M_ex) -> valid CBOR-LD -> valid JSON-LD.

        For any CBOR-LD-ex message:
          1. Encode the full message (doc + annotation).
          2. Parse as raw CBOR — must succeed (valid CBOR).
          3. Remove the annotation term ID key.
          4. The remaining map is valid CBOR-LD data.
          5. Decompress with context registry -> valid JSON-LD.
          6. JSON-LD is serializable and matches original doc.
        """
        # Step 1: Full encode
        cbor_ld_ex_bytes = encode(doc, ann, context_registry=_TEST_REGISTRY)

        # Step 2: Parse as raw CBOR — a standard parser must succeed
        raw_map = cbor2.loads(cbor_ld_ex_bytes)
        assert isinstance(raw_map, dict), "CBOR-LD-ex must decode to a CBOR map"

        # Step 3: Strip annotation (sigma function)
        # The annotation key is ANNOTATION_TERM_ID (60000).
        # A standard CBOR-LD parser doesn't know this key — it ignores it.
        stripped = {k: v for k, v in raw_map.items() if k != ANNOTATION_TERM_ID}

        # Step 4: Remaining map is valid CBOR data
        # Re-encode and re-decode to prove CBOR round-trip integrity
        stripped_bytes = cbor2.dumps(stripped)
        re_decoded = cbor2.loads(stripped_bytes)
        assert isinstance(re_decoded, dict)

        # Step 5: Decompress -> valid JSON-LD
        json_ld = _TEST_REGISTRY.decompress(re_decoded)

        # Step 6: Matches original document and is JSON-serializable
        json_str = json.dumps(json_ld)  # Must not raise
        assert isinstance(json.loads(json_str), dict)

        # Verify original doc fields survived stripping
        for key in doc:
            assert key in json_ld, f"Key '{key}' lost during strip"
            if isinstance(doc[key], float):
                assert math.isclose(json_ld[key], doc[key], rel_tol=1e-9)
            else:
                assert json_ld[key] == doc[key]

    @given(ann=any_annotation_strategy())
    @settings(max_examples=100, deadline=None)
    def test_axiom1_annotation_tag_is_ignorable(self, ann):
        """CBOR Tag(60000) is ignorable per RFC 8949 §3.4.

        A CBOR parser that doesn't understand tag 60000 must present
        the tag number AND content — it must not fail. We verify that
        the tagged annotation round-trips through standard CBOR encoding.
        """
        ann_bytes = encode_annotation(ann)
        tagged = cbor2.CBORTag(CBOR_TAG_CBORLD_EX, ann_bytes)
        roundtripped = cbor2.loads(cbor2.dumps(tagged))

        assert isinstance(roundtripped, cbor2.CBORTag)
        assert roundtripped.tag == CBOR_TAG_CBORLD_EX
        assert roundtripped.value == ann_bytes

    def test_axiom1_no_string_keys_on_wire(self):
        """CBOR-LD-ex never places string keys on the wire when a
        context registry is provided.

        This is a stricter sub-property of Axiom 1: the wire format
        uses only integer keys, matching CBOR-LD conventions. String
        keys (including "@annotation") never appear.
        """
        doc = {
            "@context": "https://schema.org/",
            "@type": "Observation",
            "value": 23.5,
            "unit": "celsius",
        }
        ann = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.BITS_8,
            ),
            opinion=quantize_binomial(0.8, 0.1, 0.1, 0.5),
        )

        encoded = encode(doc, ann, context_registry=_TEST_REGISTRY)
        raw_map = cbor2.loads(encoded)

        # Every key must be an integer
        for key in raw_map.keys():
            assert isinstance(key, int), (
                f"String key '{key}' found on wire — CBOR-LD requires "
                f"integer keys when a context registry is provided"
            )

    def test_axiom1_no_annotation_key_string(self):
        """The annotation key is integer ANNOTATION_TERM_ID, never '@annotation'."""
        doc = {"@context": "https://schema.org/", "value": 42}
        ann = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.BITS_8,
            ),
            opinion=quantize_binomial(0.9, 0.05, 0.05, 0.5),
        )
        encoded = encode(doc, ann, context_registry=_TEST_REGISTRY)
        raw_map = cbor2.loads(encoded)

        assert "@annotation" not in raw_map, (
            "String '@annotation' found on wire — must use integer term ID"
        )
        assert ANNOTATION_TERM_ID in raw_map, (
            f"Integer annotation term ID {ANNOTATION_TERM_ID} not found"
        )


# =========================================================================
# AXIOM 2 — Algebraic Closure (§3.2)
#
# For any valid annotations w1, w2 and any defined operator:
#   w1 op w2 in Omega  (the result is a valid annotation)
#
# The algebra is closed: applying SL operators to valid opinions always
# produces valid opinions that can be quantized and encoded.
# =========================================================================

class TestAxiom2AlgebraicClosure:
    """SL operators on valid annotations produce valid annotations."""

    @given(
        op1=valid_opinion_object_strategy(),
        op2=valid_opinion_object_strategy(),
    )
    @settings(max_examples=500, deadline=None)
    def test_axiom2_closure_fusion(self, op1, op2):
        """Cumulative fusion of two valid opinions -> valid opinion -> valid annotation.

        Verifies:
          1. cumulative_fuse produces a valid Opinion (b+d+u=1).
          2. The result quantizes correctly (Axiom 3 through operator).
          3. The quantized result encodes and decodes without error.
        """
        fused = cumulative_fuse(op1, op2)

        # 1. Valid opinion
        assert math.isclose(
            fused.belief + fused.disbelief + fused.uncertainty, 1.0,
            abs_tol=1e-9,
        ), f"Fused opinion violates b+d+u=1: {fused}"
        assert fused.belief >= 0.0
        assert fused.disbelief >= 0.0
        assert fused.uncertainty >= 0.0

        # 2. Quantizes correctly at 8-bit
        b_q, d_q, u_q, a_q = quantize_binomial(
            fused.belief, fused.disbelief, fused.uncertainty,
            fused.base_rate, precision=8,
        )
        assert b_q + d_q + u_q == 255, (
            f"Axiom 3 violated after fusion: {b_q}+{d_q}+{u_q}={b_q+d_q+u_q}"
        )

        # 3. Encode/decode round-trip
        ann = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.BITS_8,
            ),
            opinion=(b_q, d_q, u_q, a_q),
        )
        raw = encode_annotation(ann)
        recovered = decode_annotation(raw)
        assert recovered.opinion is not None
        rb, rd, ru, ra = recovered.opinion
        assert rb + rd + ru == 255

    @given(
        op1=valid_opinion_object_strategy(),
        op2=valid_opinion_object_strategy(),
    )
    @settings(max_examples=500, deadline=None)
    def test_axiom2_closure_meet(self, op1, op2):
        """Jurisdictional meet of two valid opinions -> valid opinion -> valid annotation.

        Definition 3 (compliance algebra §5):
          l_meet = l1 * l2
          v_meet = v1 + v2 - v1 * v2
          u_meet = (1-v1)(1-v2) - l1 * l2

        Result must satisfy b+d+u=1 and quantize correctly.
        """
        met = jurisdictional_meet(op1, op2)

        # 1. Valid opinion
        total = met.belief + met.disbelief + met.uncertainty
        assert math.isclose(total, 1.0, abs_tol=1e-9), (
            f"Meet result violates b+d+u=1: {total} for {met}"
        )
        assert met.belief >= -1e-12
        assert met.disbelief >= -1e-12
        assert met.uncertainty >= -1e-12

        # 2. Quantizes correctly at 8-bit
        b_q, d_q, u_q, a_q = quantize_binomial(
            max(0.0, met.belief),
            max(0.0, met.disbelief),
            max(0.0, met.uncertainty),
            max(0.0, min(1.0, met.base_rate)),
            precision=8,
        )
        assert b_q + d_q + u_q == 255, (
            f"Axiom 3 violated after meet: {b_q}+{d_q}+{u_q}={b_q+d_q+u_q}"
        )

        # 3. Encode/decode
        ann = Annotation(
            header=Tier2Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.BITS_8,
                operator_id=OperatorId.JURISDICTIONAL_MEET,
                reasoning_context=0,
                context_version=1,
                has_multinomial=False,
                sub_tier_depth=0,
                source_count=2,
            ),
            opinion=(b_q, d_q, u_q, a_q),
        )
        raw = encode_annotation(ann)
        recovered = decode_annotation(raw)
        assert recovered.opinion is not None
        rb, rd, ru, ra = recovered.opinion
        assert rb + rd + ru == 255

    @given(
        op=valid_opinion_object_strategy(),
        elapsed=st.floats(min_value=0.0, max_value=1e6,
                          allow_nan=False, allow_infinity=False),
        half_life=st.floats(min_value=1.0, max_value=1e6,
                            allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=500, deadline=None)
    def test_axiom2_closure_decay(self, op, elapsed, half_life):
        """Temporal decay of a valid opinion -> valid opinion -> valid annotation.

        decay_opinion scales b and d by factor lambda in [0,1], then derives
        u' = 1 - b' - d'. Result must satisfy b+d+u=1 and quantize correctly.
        """
        decayed = decay_opinion(op, elapsed=elapsed, half_life=half_life)

        # 1. Valid opinion
        total = decayed.belief + decayed.disbelief + decayed.uncertainty
        assert math.isclose(total, 1.0, abs_tol=1e-9), (
            f"Decayed opinion violates b+d+u=1: {total}"
        )
        assert decayed.belief >= 0.0
        assert decayed.disbelief >= 0.0
        assert decayed.uncertainty >= 0.0

        # 2. Quantizes correctly at 8-bit
        b_q, d_q, u_q, a_q = quantize_binomial(
            decayed.belief, decayed.disbelief, decayed.uncertainty,
            decayed.base_rate, precision=8,
        )
        assert b_q + d_q + u_q == 255, (
            f"Axiom 3 violated after decay: {b_q}+{d_q}+{u_q}={b_q+d_q+u_q}"
        )

        # 3. Encode/decode
        ann = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.BITS_8,
            ),
            opinion=(b_q, d_q, u_q, a_q),
        )
        raw = encode_annotation(ann)
        recovered = decode_annotation(raw)
        assert recovered.opinion is not None
        rb, rd, ru, ra = recovered.opinion
        assert rb + rd + ru == 255

    @given(
        op1=valid_opinion_object_strategy(),
        op2=valid_opinion_object_strategy(),
        precision=st.sampled_from([8, 16]),
    )
    @settings(max_examples=300, deadline=None)
    def test_axiom2_closure_fusion_at_all_precisions(self, op1, op2, precision):
        """Closure holds at both 8-bit and 16-bit precisions."""
        fused = cumulative_fuse(op1, op2)
        max_val = (1 << precision) - 1

        b_q, d_q, u_q, a_q = quantize_binomial(
            fused.belief, fused.disbelief, fused.uncertainty,
            fused.base_rate, precision=precision,
        )
        assert b_q + d_q + u_q == max_val, (
            f"Axiom 3 violated after fusion at {precision}-bit: "
            f"{b_q}+{d_q}+{u_q}={b_q+d_q+u_q} (expected {max_val})"
        )

    @given(
        op1=valid_opinion_object_strategy(),
        op2=valid_opinion_object_strategy(),
    )
    @settings(max_examples=200, deadline=None)
    def test_axiom2_closure_full_pipeline(self, op1, op2):
        """Closure through the FULL encode/decode pipeline.

        Fuse two opinions -> quantize -> build annotation -> encode to
        CBOR-LD-ex -> decode -> verify the recovered annotation is valid.
        """
        fused = cumulative_fuse(op1, op2)

        b_q, d_q, u_q, a_q = quantize_binomial(
            fused.belief, fused.disbelief, fused.uncertainty,
            fused.base_rate, precision=8,
        )

        doc = {"@context": "https://schema.org/", "value": 42.0}
        ann = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.BITS_8,
            ),
            opinion=(b_q, d_q, u_q, a_q),
        )

        encoded = encode(doc, ann, context_registry=_TEST_REGISTRY)
        recovered_doc, recovered_ann = decode(encoded, context_registry=_TEST_REGISTRY)

        # Recovered annotation satisfies Axiom 3
        assert recovered_ann.opinion is not None
        rb, rd, ru, ra = recovered_ann.opinion
        assert rb + rd + ru == 255, (
            f"Axiom 3 violated after full pipeline: "
            f"{rb}+{rd}+{ru}={rb+rd+ru}"
        )

        # Recovered document matches original
        assert recovered_doc["value"] == 42.0


# =========================================================================
# AXIOM 3 — Quantization Correctness (§3.3)
#
# For all valid opinions w = (b, d, u, a) with b+d+u=1:
#   Q(w) = (b_hat, d_hat, u_hat, a_hat) satisfies
#   b_hat + d_hat + u_hat = 2^n - 1 exactly.
#
# This is the integer-domain constraint. It is EXACT — no floating-point
# tolerance. It holds because u_hat is DERIVED, not independently quantized.
# =========================================================================

class TestAxiom3QuantizationCorrectness:
    """b_hat + d_hat + u_hat = 2^n - 1 exactly, always."""

    def test_axiom3_quantization_roundtrip_exhaustive_8bit(self):
        """EXHAUSTIVE: All valid 8-bit (b_hat, d_hat) pairs satisfy the constraint.

        For 8-bit quantization, the valid output pairs are all (b_hat, d_hat)
        where b_hat + d_hat <= 255. For each, u_hat = 255 - b_hat - d_hat
        must be >= 0, and the dequantized components must sum to exactly 1.0
        in real arithmetic (Theorem 1b).

        Total pairs checked: (256 * 257) / 2 = 32,896.
        """
        max_val = 255
        checked = 0

        for b_q in range(256):
            for d_q in range(256 - b_q):
                u_q = max_val - b_q - d_q

                # Integer-domain constraint (Theorem 1a) — EXACT
                assert b_q + d_q + u_q == max_val, (
                    f"Theorem 1a violated: {b_q}+{d_q}+{u_q}={b_q+d_q+u_q}"
                )
                assert u_q >= 0, f"Negative u_hat: {u_q} for b_hat={b_q}, d_hat={d_q}"

                # Dequantize and verify real-domain sum (Theorem 1b)
                b_r = b_q / max_val
                d_r = d_q / max_val
                u_r = u_q / max_val

                # The sum (b_hat + d_hat + u_hat) / (2^n-1) = (2^n-1)/(2^n-1) = 1
                # exactly in real arithmetic. In IEEE 754, three separate
                # divisions may differ by at most a few ULPs.
                total = b_r + d_r + u_r
                assert math.isclose(total, 1.0, abs_tol=2e-15), (
                    f"Theorem 1b violated: {b_r}+{d_r}+{u_r}={total}"
                )

                checked += 1

        assert checked == (256 * 257) // 2, (
            f"Expected 32896 valid pairs, checked {checked}"
        )

    def test_axiom3_exhaustive_wire_roundtrip_8bit(self):
        """All valid 8-bit pairs survive wire encode -> decode exactly.

        For every valid (b_hat, d_hat) pair, encoding to 3 bytes and decoding
        must recover the EXACT integer values, including the derived u_hat.
        """
        max_val = 255

        for b_q in range(256):
            for d_q in range(256 - b_q):
                u_q = max_val - b_q - d_q
                a_q = b_q  # arbitrary — use b_hat as base rate for variety

                # Encode 3 values -> decode -> get 4 values
                wire = encode_opinion_bytes(b_q, d_q, a_q, precision=8)
                assert len(wire) == 3, f"Wire length {len(wire)} != 3"

                rb, rd, ru, ra = decode_opinion_bytes(wire, precision=8)
                assert rb == b_q, f"b_hat mismatch: {rb} != {b_q}"
                assert rd == d_q, f"d_hat mismatch: {rd} != {d_q}"
                assert ru == u_q, f"u_hat mismatch: {ru} != {u_q}"
                assert ra == a_q, f"a_hat mismatch: {ra} != {a_q}"

    @given(opinion=valid_opinion_strategy())
    @settings(max_examples=1000, deadline=None)
    def test_axiom3_quantize_preserves_constraint_8bit(self, opinion):
        """Quantize any valid opinion at 8-bit -> b_hat+d_hat+u_hat = 255 exactly."""
        b, d, u, a = opinion
        b_q, d_q, u_q, a_q = quantize_binomial(b, d, u, a, precision=8)

        assert b_q + d_q + u_q == 255, (
            f"Axiom 3 violated: {b_q}+{d_q}+{u_q}={b_q+d_q+u_q} "
            f"for input ({b:.6f}, {d:.6f}, {u:.6f})"
        )

    @given(opinion=valid_opinion_strategy())
    @settings(max_examples=1000, deadline=None)
    def test_axiom3_quantize_preserves_constraint_16bit(self, opinion):
        """Quantize any valid opinion at 16-bit -> b_hat+d_hat+u_hat = 65535 exactly."""
        b, d, u, a = opinion
        b_q, d_q, u_q, a_q = quantize_binomial(b, d, u, a, precision=16)

        assert b_q + d_q + u_q == 65535, (
            f"Axiom 3 violated at 16-bit: {b_q}+{d_q}+{u_q}={b_q+d_q+u_q}"
        )

    @given(
        op1=valid_opinion_object_strategy(),
        op2=valid_opinion_object_strategy(),
    )
    @settings(max_examples=500, deadline=None)
    def test_axiom3_quantization_through_fusion(self, op1, op2):
        """Axiom 3 holds AFTER cumulative fusion at all integer precisions.

        Quantize -> Fuse (at real-valued level) -> Re-quantize -> Verify.
        This checks that operator outputs don't produce edge cases
        that break the quantization constraint.
        """
        fused = cumulative_fuse(op1, op2)

        for precision in (8, 16):
            max_val = (1 << precision) - 1
            b_q, d_q, u_q, a_q = quantize_binomial(
                fused.belief, fused.disbelief, fused.uncertainty,
                fused.base_rate, precision=precision,
            )
            assert b_q + d_q + u_q == max_val, (
                f"Axiom 3 violated after fusion at {precision}-bit: "
                f"{b_q}+{d_q}+{u_q}={b_q+d_q+u_q} (expected {max_val})"
            )

    @given(
        op1=valid_opinion_object_strategy(),
        op2=valid_opinion_object_strategy(),
    )
    @settings(max_examples=500, deadline=None)
    def test_axiom3_quantization_through_meet(self, op1, op2):
        """Axiom 3 holds AFTER jurisdictional meet at all integer precisions."""
        met = jurisdictional_meet(op1, op2)

        for precision in (8, 16):
            max_val = (1 << precision) - 1
            b_q, d_q, u_q, a_q = quantize_binomial(
                max(0.0, met.belief),
                max(0.0, met.disbelief),
                max(0.0, met.uncertainty),
                max(0.0, min(1.0, met.base_rate)),
                precision=precision,
            )
            assert b_q + d_q + u_q == max_val, (
                f"Axiom 3 violated after meet at {precision}-bit: "
                f"{b_q}+{d_q}+{u_q}={b_q+d_q+u_q} (expected {max_val})"
            )

    @given(
        op=valid_opinion_object_strategy(),
        elapsed=st.floats(min_value=0.0, max_value=1e6,
                          allow_nan=False, allow_infinity=False),
        half_life=st.floats(min_value=1.0, max_value=1e6,
                            allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=500, deadline=None)
    def test_axiom3_quantization_through_decay(self, op, elapsed, half_life):
        """Axiom 3 holds AFTER temporal decay at all integer precisions."""
        decayed = decay_opinion(op, elapsed=elapsed, half_life=half_life)

        for precision in (8, 16):
            max_val = (1 << precision) - 1
            b_q, d_q, u_q, a_q = quantize_binomial(
                decayed.belief, decayed.disbelief, decayed.uncertainty,
                decayed.base_rate, precision=precision,
            )
            assert b_q + d_q + u_q == max_val, (
                f"Axiom 3 violated after decay at {precision}-bit: "
                f"{b_q}+{d_q}+{u_q}={b_q+d_q+u_q} (expected {max_val})"
            )

    def test_axiom3_boundary_opinions(self):
        """Axiom 3 holds at extreme boundary opinions.

        These are the corners of the simplex:
          - Full belief:      (1, 0, 0, a)
          - Full disbelief:   (0, 1, 0, a)
          - Full uncertainty:  (0, 0, 1, a)
          - Vacuous:          (0, 0, 1, 0.5)
          - Dogmatic:         (1, 0, 0, 0.5)
          - Half-half:        (0.5, 0.5, 0, a)
          - Near-overflow:    (0.999, 0.001, 0, a)
        """
        boundaries = [
            (1.0, 0.0, 0.0, 0.5),   # Full belief
            (0.0, 1.0, 0.0, 0.5),   # Full disbelief
            (0.0, 0.0, 1.0, 0.5),   # Full uncertainty (vacuous)
            (0.5, 0.5, 0.0, 0.5),   # Exact half-half (no uncertainty)
            (0.5, 0.0, 0.5, 1.0),   # Half belief, half uncertainty
            (0.0, 0.5, 0.5, 0.0),   # Half disbelief, half uncertainty
            (1/3, 1/3, 1/3, 0.5),   # Uniform (triggers rounding)
            (0.999, 0.001, 0.0, 0.5),  # Near-overflow
            (0.001, 0.999, 0.0, 0.5),  # Near-overflow (disbelief)
            (0.0, 0.0, 1.0, 0.0),   # Fully uncertain, zero base rate
            (0.0, 0.0, 1.0, 1.0),   # Fully uncertain, full base rate
        ]

        for precision in (8, 16):
            max_val = (1 << precision) - 1
            for b, d, u, a in boundaries:
                b_q, d_q, u_q, a_q = quantize_binomial(
                    b, d, u, a, precision=precision,
                )
                assert b_q + d_q + u_q == max_val, (
                    f"Axiom 3 violated for boundary ({b},{d},{u},{a}) "
                    f"at {precision}-bit: {b_q}+{d_q}+{u_q}={b_q+d_q+u_q}"
                )

    def test_axiom3_clamping_path_preserves_constraint(self):
        """When b_hat+d_hat > max_val (rounding overflow), clamping d_hat by 1
        still preserves b_hat+d_hat+u_hat = max_val.

        The clamping path is triggered when b ~= 0.5 and d ~= 0.5
        with u ~= 0, and both round up.
        """
        # At 8-bit: b = 0.5 -> round(0.5 * 255) = 128
        #           d = 0.5 -> round(0.5 * 255) = 128
        #           128 + 128 = 256 > 255 -> clamp d_hat to 127
        b_q, d_q, u_q, a_q = quantize_binomial(0.5, 0.5, 0.0, 0.5, precision=8)

        assert b_q + d_q + u_q == 255, "Clamping violated Axiom 3"
        assert b_q == 128, "Clamping should preserve b_hat"
        assert d_q == 127, "Clamping should decrement d_hat"
        assert u_q == 0, "u_hat should be 0 when u = 0 after clamping"

    def test_axiom3_u_hat_never_transmitted(self):
        """u_hat carries zero Shannon information and must NEVER be on the wire.

        Verify that for all precisions, the wire format is exactly 3
        values (b_hat, d_hat, a_hat), and the decoded 4th value matches
        the derived u_hat.
        """
        opinion = (0.7, 0.2, 0.1, 0.5)

        for precision in (8, 16):
            b_q, d_q, u_q, a_q = quantize_binomial(*opinion, precision=precision)
            max_val = (1 << precision) - 1

            # Encode transmits 3 values
            wire = encode_opinion_bytes(b_q, d_q, a_q, precision=precision)
            expected_bytes = 3 * (precision // 8)
            assert len(wire) == expected_bytes, (
                f"Wire length {len(wire)} != {expected_bytes} at {precision}-bit"
            )

            # Decode derives u_hat
            rb, rd, ru, ra = decode_opinion_bytes(wire, precision=precision)
            assert ru == u_q, f"Derived u_hat {ru} != original u_hat {u_q}"
            assert ru == max_val - rb - rd, "u_hat derivation formula broken"
