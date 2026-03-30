"""
Stateful stream decoder for delta opinion reconstruction.

Implements the receiver side of §7.6 (Time-Series Delta Encoding):
  - Keyframe-first mandate: reject deltas without prior baseline
  - Delta reconstruction: apply_delta() to recover full opinion
  - Constraint violation: signal NACK, clear baseline
  - I-frame / P-frame analogy: full opinions reset baseline

Wire efficiency is preserved: StreamResult.wire_annotation is the
literal wire-level truth (DELTA_8 header, 2-tuple opinion, 3 bytes).
StreamResult.reconstructed is receiver-side compute — zero wire cost.
"""

from dataclasses import dataclass
from typing import Optional

from cbor_ld_ex.annotations import Annotation
from cbor_ld_ex.headers import PrecisionMode
from cbor_ld_ex.opinions import apply_delta


class DeltaWithoutBaselineError(Exception):
    """Delta received before any full opinion (§7.6 keyframe-first mandate).

    Receiver MUST silently discard and wait for a full opinion.
    """


class DeltaConstraintError(Exception):
    """Reconstructed opinion violates SL constraints (§7.6 NACK).

    Receiver MUST discard the delta and SHOULD send an application-layer
    NACK to request full opinion retransmission.
    """


@dataclass
class StreamResult:
    """Result of stateful stream decoding.

    Preserves wire-level truth alongside reconstructed opinion.

    Attributes:
        wire_annotation: The annotation exactly as received — untouched.
            For delta mode: DELTA_8 header, 2-tuple opinion, 3 wire bytes.
            Efficiency claims (§11.2, §11.4) describe THIS object.
        reconstructed: Full (b̂, d̂, û, â) opinion for downstream
            processing (fusion, analysis). None if has_opinion=False.
            This is receiver-side compute — zero wire cost.
        was_delta: True if the wire annotation was a delta opinion.
            Provenance flag for audit trail.
    """
    wire_annotation: Annotation
    reconstructed: Optional[tuple]
    was_delta: bool


class DeltaStreamDecoder:
    """Stateful decoder for §7.6 delta opinion streams.

    Tracks baseline opinion state. Processes a stream of annotations
    (mix of full opinions and deltas) and produces StreamResults with
    reconstructed full opinions.

    One decoder per source stream. For Tier 2 gateways handling N
    sensors, instantiate N decoders.

    Args:
        precision: Quantization precision of the stream (default 8).
            Determines max_val for û derivation in apply_delta().
    """

    def __init__(self, precision: int = 8) -> None:
        self._precision = precision
        self._baseline: Optional[tuple] = None  # (b̂, d̂, û, â)

    @property
    def has_baseline(self) -> bool:
        """Whether a full opinion baseline has been established."""
        return self._baseline is not None

    def process(self, annotation: Annotation) -> StreamResult:
        """Process an annotation from the stream.

        Full opinion (I-frame): stores as baseline, returns as-is.
        Delta opinion (P-frame): reconstructs via apply_delta(),
            updates baseline, returns full 4-tuple.
        No opinion: passes through, baseline unaffected.

        Args:
            annotation: The annotation to process.

        Returns:
            StreamResult with wire truth and reconstructed opinion.

        Raises:
            DeltaWithoutBaselineError: Delta before any full opinion.
            DeltaConstraintError: Reconstructed opinion invalid.
        """
        # No opinion — pass through, don't touch baseline
        if not annotation.header.has_opinion or annotation.opinion is None:
            return StreamResult(
                wire_annotation=annotation,
                reconstructed=None,
                was_delta=False,
            )

        if annotation.header.precision_mode == PrecisionMode.DELTA_8:
            return self._process_delta(annotation)
        else:
            return self._process_full(annotation)

    def _process_full(self, annotation: Annotation) -> StreamResult:
        """Process a full opinion — update baseline."""
        self._baseline = annotation.opinion
        return StreamResult(
            wire_annotation=annotation,
            reconstructed=annotation.opinion,
            was_delta=False,
        )

    def _process_delta(self, annotation: Annotation) -> StreamResult:
        """Process a delta opinion — reconstruct from baseline."""
        if self._baseline is None:
            raise DeltaWithoutBaselineError(
                "Delta opinion received without prior baseline. "
                "§7.6: first message MUST be a full opinion (keyframe)."
            )

        delta_b, delta_d = annotation.opinion

        try:
            reconstructed = apply_delta(
                self._baseline, delta_b, delta_d,
                precision=self._precision,
            )
        except ValueError as e:
            # Constraint violation — clear baseline, signal NACK
            self._baseline = None
            raise DeltaConstraintError(
                f"Delta reconstruction violated constraints. "
                f"Baseline cleared — receiver desynchronized. "
                f"§7.6: NACK required. Detail: {e}"
            ) from e

        # Update baseline for next delta in the chain
        self._baseline = reconstructed

        return StreamResult(
            wire_annotation=annotation,
            reconstructed=reconstructed,
            was_delta=True,
        )
