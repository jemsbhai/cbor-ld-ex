"""
Stream decoder tests: stateful delta opinion reconstruction.

Tests are derived from FORMAL_MODEL.md §7.6:
  - Keyframe-first mandate
  - Delta reconstruction via apply_delta()
  - Constraint violation → NACK signal
  - I-frame / P-frame analogy
  - StreamResult preserves wire truth alongside reconstructed state

All tests target: src/cbor_ld_ex/stream.py
"""

import pytest

from cbor_ld_ex.annotations import Annotation
from cbor_ld_ex.headers import (
    Tier1Header,
    Tier2Header,
    ComplianceStatus,
    OperatorId,
    PrecisionMode,
)
from cbor_ld_ex.stream import (
    DeltaStreamDecoder,
    StreamResult,
    DeltaWithoutBaselineError,
    DeltaConstraintError,
)


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------

def _full_ann(b_q=200, d_q=30, u_q=25, a_q=128, precision=PrecisionMode.BITS_8):
    """Build a Tier 1 full-opinion annotation."""
    return Annotation(
        header=Tier1Header(
            compliance_status=ComplianceStatus.COMPLIANT,
            delegation_flag=False,
            has_opinion=True,
            precision_mode=precision,
        ),
        opinion=(b_q, d_q, u_q, a_q),
    )


def _delta_ann(delta_b=5, delta_d=-3):
    """Build a Tier 1 delta annotation."""
    return Annotation(
        header=Tier1Header(
            compliance_status=ComplianceStatus.COMPLIANT,
            delegation_flag=False,
            has_opinion=True,
            precision_mode=PrecisionMode.DELTA_8,
        ),
        opinion=(delta_b, delta_d),
    )


# -----------------------------------------------------------------------
# 1. StreamResult structure
# -----------------------------------------------------------------------

class TestStreamResult:
    """StreamResult preserves both wire truth and reconstructed state."""

    def test_full_opinion_result(self):
        """Full opinion: wire_annotation unchanged, reconstructed = opinion."""
        decoder = DeltaStreamDecoder()
        ann = _full_ann(200, 30, 25, 128)
        result = decoder.process(ann)

        assert isinstance(result, StreamResult)
        assert result.wire_annotation is ann
        assert result.reconstructed == (200, 30, 25, 128)
        assert result.was_delta is False

    def test_delta_result_preserves_wire(self):
        """Delta: wire_annotation has 2-tuple, reconstructed has 4-tuple."""
        decoder = DeltaStreamDecoder()
        decoder.process(_full_ann(200, 30, 25, 128))

        delta = _delta_ann(5, -3)
        result = decoder.process(delta)

        assert result.wire_annotation is delta
        assert result.wire_annotation.opinion == (5, -3)  # wire truth
        assert len(result.reconstructed) == 4  # full opinion
        assert result.was_delta is True


# -----------------------------------------------------------------------
# 2. Keyframe-first mandate (§7.6)
# -----------------------------------------------------------------------

class TestKeyframeFirstMandate:
    """§7.6: First message MUST be a full opinion (I-frame)."""

    def test_delta_without_baseline_raises(self):
        """Delta before any full opinion → DeltaWithoutBaselineError."""
        decoder = DeltaStreamDecoder()
        with pytest.raises(DeltaWithoutBaselineError):
            decoder.process(_delta_ann(5, -3))

    def test_full_opinion_then_delta_succeeds(self):
        """Full opinion establishes baseline → delta succeeds."""
        decoder = DeltaStreamDecoder()
        decoder.process(_full_ann(200, 30, 25, 128))
        result = decoder.process(_delta_ann(5, -3))
        assert result.reconstructed == (205, 27, 23, 128)

    def test_no_opinion_does_not_establish_baseline(self):
        """Header-only annotation (has_opinion=False) is not a keyframe."""
        decoder = DeltaStreamDecoder()
        no_opinion = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=False,
                precision_mode=PrecisionMode.BITS_8,
            ),
        )
        result = decoder.process(no_opinion)
        assert result.reconstructed is None

        # Delta still fails — no baseline established
        with pytest.raises(DeltaWithoutBaselineError):
            decoder.process(_delta_ann(5, -3))


# -----------------------------------------------------------------------
# 3. Delta reconstruction
# -----------------------------------------------------------------------

class TestDeltaReconstruction:
    """Receiver-side reconstruction per §7.6."""

    def test_basic_reconstruction(self):
        """b̂_new = b̂_prev + Δb̂, d̂_new = d̂_prev + Δd̂, û derived."""
        decoder = DeltaStreamDecoder()
        decoder.process(_full_ann(200, 30, 25, 128))
        result = decoder.process(_delta_ann(10, -5))

        b, d, u, a = result.reconstructed
        assert b == 210
        assert d == 25
        assert u == 255 - 210 - 25  # = 20, derived
        assert a == 128  # unchanged

    def test_zero_delta(self):
        """Delta (0, 0) = no change."""
        decoder = DeltaStreamDecoder()
        decoder.process(_full_ann(200, 30, 25, 128))
        result = decoder.process(_delta_ann(0, 0))
        assert result.reconstructed == (200, 30, 25, 128)

    def test_negative_deltas(self):
        """Negative deltas decrease belief/disbelief, increase uncertainty."""
        decoder = DeltaStreamDecoder()
        decoder.process(_full_ann(200, 30, 25, 128))
        result = decoder.process(_delta_ann(-50, -10))

        b, d, u, a = result.reconstructed
        assert b == 150
        assert d == 20
        assert u == 255 - 150 - 20  # = 85
        assert a == 128

    def test_sequential_deltas_accumulate(self):
        """Multiple deltas accumulate correctly (P-frame chain)."""
        decoder = DeltaStreamDecoder()
        decoder.process(_full_ann(100, 100, 55, 128))

        # Delta 1: +10, -5
        r1 = decoder.process(_delta_ann(10, -5))
        assert r1.reconstructed == (110, 95, 50, 128)

        # Delta 2: -20, +10 (applied to r1's result, not original)
        r2 = decoder.process(_delta_ann(-20, 10))
        assert r2.reconstructed == (90, 105, 60, 128)

        # Delta 3: +5, +5
        r3 = decoder.process(_delta_ann(5, 5))
        assert r3.reconstructed == (95, 110, 50, 128)

    def test_base_rate_unchanged_across_deltas(self):
        """â is never modified by delta — unchanged from keyframe."""
        decoder = DeltaStreamDecoder()
        decoder.process(_full_ann(100, 100, 55, 200))
        r = decoder.process(_delta_ann(10, -10))
        assert r.reconstructed[3] == 200  # â unchanged


# -----------------------------------------------------------------------
# 4. Keyframe resets baseline
# -----------------------------------------------------------------------

class TestKeyframeReset:
    """Full opinion (I-frame) resets baseline at any point."""

    def test_full_opinion_resets_baseline(self):
        """New full opinion after deltas resets the baseline."""
        decoder = DeltaStreamDecoder()
        decoder.process(_full_ann(100, 100, 55, 128))
        decoder.process(_delta_ann(10, -5))

        # New keyframe with completely different opinion
        decoder.process(_full_ann(50, 50, 155, 64))

        # Next delta applies to the NEW baseline
        r = decoder.process(_delta_ann(5, 5))
        assert r.reconstructed == (55, 55, 145, 64)

    def test_16bit_keyframe_then_delta(self):
        """16-bit full opinion establishes baseline for 8-bit deltas.

        §7.6: delta is always 8-bit signed. The baseline precision
        determines the max_val for û derivation.
        """
        decoder = DeltaStreamDecoder(precision=16)
        decoder.process(_full_ann(50000, 10000, 5535, 32768,
                                  precision=PrecisionMode.BITS_16))
        r = decoder.process(_delta_ann(100, -50))

        b, d, u, a = r.reconstructed
        assert b == 50100
        assert d == 9950
        assert u == 65535 - 50100 - 9950  # = 5485
        assert a == 32768


# -----------------------------------------------------------------------
# 5. Constraint violation → NACK
# -----------------------------------------------------------------------

class TestConstraintViolation:
    """§7.6: receiver MUST verify reconstructed opinion validity."""

    def test_negative_belief_raises(self):
        """Delta producing b̂ < 0 → DeltaConstraintError."""
        decoder = DeltaStreamDecoder()
        decoder.process(_full_ann(10, 100, 145, 128))

        with pytest.raises(DeltaConstraintError):
            decoder.process(_delta_ann(-20, 0))  # b̂ = 10 - 20 = -10

    def test_negative_disbelief_raises(self):
        """Delta producing d̂ < 0 → DeltaConstraintError."""
        decoder = DeltaStreamDecoder()
        decoder.process(_full_ann(100, 5, 150, 128))

        with pytest.raises(DeltaConstraintError):
            decoder.process(_delta_ann(0, -10))  # d̂ = 5 - 10 = -5

    def test_sum_exceeds_max_val_raises(self):
        """Delta producing b̂ + d̂ > max_val → DeltaConstraintError."""
        decoder = DeltaStreamDecoder()
        decoder.process(_full_ann(200, 50, 5, 128))

        with pytest.raises(DeltaConstraintError):
            decoder.process(_delta_ann(5, 5))  # b̂+d̂ = 205+55 = 260 > 255

    def test_constraint_error_clears_baseline(self):
        """After constraint violation, baseline is cleared — §7.6 NACK.

        Receiver desynchronized: must wait for next full opinion.
        """
        decoder = DeltaStreamDecoder()
        decoder.process(_full_ann(10, 100, 145, 128))

        with pytest.raises(DeltaConstraintError):
            decoder.process(_delta_ann(-20, 0))

        # Baseline cleared — next delta also fails
        with pytest.raises(DeltaWithoutBaselineError):
            decoder.process(_delta_ann(1, 1))


# -----------------------------------------------------------------------
# 6. Pass-through for non-opinion annotations
# -----------------------------------------------------------------------

class TestPassThrough:
    """Non-opinion annotations pass through without affecting state."""

    def test_header_only_passthrough(self):
        """has_opinion=False annotation returns None reconstructed."""
        decoder = DeltaStreamDecoder()
        no_opinion = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.NON_COMPLIANT,
                delegation_flag=True,
                has_opinion=False,
                precision_mode=PrecisionMode.BITS_8,
            ),
        )
        result = decoder.process(no_opinion)
        assert result.wire_annotation is no_opinion
        assert result.reconstructed is None
        assert result.was_delta is False

    def test_header_only_does_not_clear_baseline(self):
        """Non-opinion annotation between deltas doesn't break chain."""
        decoder = DeltaStreamDecoder()
        decoder.process(_full_ann(100, 100, 55, 128))

        no_opinion = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.NON_COMPLIANT,
                delegation_flag=False,
                has_opinion=False,
                precision_mode=PrecisionMode.BITS_8,
            ),
        )
        decoder.process(no_opinion)

        # Delta still works — baseline intact
        r = decoder.process(_delta_ann(5, -5))
        assert r.reconstructed == (105, 95, 55, 128)
