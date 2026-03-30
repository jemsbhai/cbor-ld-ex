"""
Phase 5 tests: Security primitives for CBOR-LD-ex.

Tests are derived from SECTION9_SECURITY.md and FORMAL_MODEL.md §9:
  - Annotation digest: truncated SHA-256 (64 bits / 8 bytes)
  - Byzantine fusion metadata: bit-packed (4 bytes)
  - Provenance chain entries: bit-packed (16 bytes each), chained digests
  - Chain verification: tamper detection, truncation detection

Security primitives are orthogonal to Axioms 1–3:
  - They don't alter opinions (Axiom 3 unaffected)
  - They don't alter the CBOR-LD data (Axiom 1 unaffected)
  - They don't participate in the opinion algebra (Axiom 2 unaffected)

Depends on:
  - Phase 1: opinions.py (quantization for provenance entries)
  - Phase 2: headers.py (tier/operator enums for entries)
  - Phase 3: annotations.py (encode_annotation for digest input)
  - jsonld-ex: byzantine_fuse (cross-validation of cohesion scores)
"""

import hashlib
import math
import struct

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from cbor_ld_ex.annotations import (
    Annotation,
    encode_annotation,
)
from cbor_ld_ex.headers import (
    Tier1Header,
    Tier2Header,
    ComplianceStatus,
    OperatorId,
    PrecisionMode,
)
from cbor_ld_ex.opinions import quantize_binomial

from cbor_ld_ex.security import (
    # Annotation digest
    compute_annotation_digest,
    verify_annotation_digest,
    DIGEST_SIZE_BYTES,
    # Byzantine metadata
    ByzantineMetadata,
    STRATEGY_MOST_CONFLICTING,
    STRATEGY_LEAST_TRUSTED,
    STRATEGY_COMBINED,
    encode_byzantine_metadata,
    decode_byzantine_metadata,
    # Provenance entries
    ProvenanceEntry,
    PROVENANCE_ENTRY_SIZE,
    CHAIN_ORIGIN_SENTINEL,
    encode_provenance_entry,
    decode_provenance_entry,
    # Chain operations
    encode_provenance_chain,
    decode_provenance_chain,
    verify_provenance_chain,
    compute_entry_digest,
    # Audit-grade (24-byte) entries
    AUDIT_DIGEST_SIZE_BYTES,
    AUDIT_ENTRY_SIZE,
    AUDIT_CHAIN_ORIGIN_SENTINEL,
)

# Reference for cross-validation
from jsonld_ex.confidence_algebra import Opinion
from jsonld_ex.confidence_byzantine import (
    byzantine_fuse,
    ByzantineConfig,
    cohesion_score as jex_cohesion_score,
)


# =========================================================================
# 1. Annotation Digest — truncated SHA-256 (8 bytes)
# =========================================================================

class TestAnnotationDigest:
    """Truncated SHA-256 of the annotation byte string."""

    def test_digest_is_8_bytes(self):
        """Digest is exactly 8 bytes (64 bits)."""
        ann = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.BITS_8,
            ),
            opinion=quantize_binomial(0.85, 0.05, 0.10, 0.50),
        )
        ann_bytes = encode_annotation(ann)
        digest = compute_annotation_digest(ann_bytes)
        assert len(digest) == DIGEST_SIZE_BYTES
        assert DIGEST_SIZE_BYTES == 8

    def test_digest_matches_manual_sha256(self):
        """Digest matches manually computed truncated SHA-256."""
        ann = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.BITS_8,
            ),
            opinion=quantize_binomial(0.85, 0.05, 0.10, 0.50),
        )
        ann_bytes = encode_annotation(ann)

        expected = hashlib.sha256(ann_bytes).digest()[:8]
        actual = compute_annotation_digest(ann_bytes)
        assert actual == expected

    def test_verify_correct_digest(self):
        """Verification succeeds for correct digest."""
        data = b"\x04\xd9\x0d\x80"
        digest = compute_annotation_digest(data)
        assert verify_annotation_digest(data, digest) is True

    def test_verify_wrong_digest(self):
        """Verification fails for incorrect digest."""
        data = b"\x04\xd9\x0d\x80"
        wrong = b"\x00" * 8
        assert verify_annotation_digest(data, wrong) is False

    def test_digest_changes_with_input(self):
        """Different annotations produce different digests."""
        ann1_bytes = encode_annotation(Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.BITS_8,
            ),
            opinion=quantize_binomial(0.85, 0.05, 0.10, 0.50),
        ))
        ann2_bytes = encode_annotation(Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.NON_COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.BITS_8,
            ),
            opinion=quantize_binomial(0.10, 0.80, 0.10, 0.50),
        ))
        assert compute_annotation_digest(ann1_bytes) != compute_annotation_digest(ann2_bytes)

    def test_digest_deterministic(self):
        """Same input always produces same digest."""
        data = b"\x04\xd9\x0d\x80"
        assert compute_annotation_digest(data) == compute_annotation_digest(data)

    def test_digest_on_empty_bytes(self):
        """Digest of empty bytes is valid (edge case — header-only annotation)."""
        digest = compute_annotation_digest(b"")
        assert len(digest) == 8
        # SHA-256 of empty string is well-defined
        assert digest == hashlib.sha256(b"").digest()[:8]


# =========================================================================
# 2. Byzantine Metadata — bit-packed (4 bytes)
#
# [8 bits]  original_source_count
# [8 bits]  removed_count
# [8 bits]  cohesion_q (Q8: 0=0.0, 255=1.0)
# [2 bits]  removal_strategy
# Pad to 4 bytes.
# =========================================================================

class TestByzantineMetadata:
    """Bit-packed Byzantine fusion metadata."""

    def test_metadata_is_4_bytes(self):
        """Byzantine metadata encodes to exactly 4 bytes."""
        meta = ByzantineMetadata(
            original_count=10,
            removed_count=2,
            cohesion_q=217,
            strategy=STRATEGY_MOST_CONFLICTING,
        )
        data = encode_byzantine_metadata(meta)
        assert len(data) == 4

    def test_metadata_bit_layout(self):
        """Verify exact bit positions.

        original=10(0x0A), removed=2(0x02), cohesion=217(0xD9),
        strategy=00(most_conflicting), pad=000000

        bytes: 0x0A 0x02 0xD9 0x00
        """
        meta = ByzantineMetadata(
            original_count=10,
            removed_count=2,
            cohesion_q=217,
            strategy=STRATEGY_MOST_CONFLICTING,
        )
        data = encode_byzantine_metadata(meta)
        assert data == bytes([0x0A, 0x02, 0xD9, 0x00])

    def test_metadata_least_trusted_strategy(self):
        """Strategy=01 (least_trusted) in bits 24-25.

        cohesion=128(0x80), strategy=01
        byte 3: 01 000000 = 0x40
        """
        meta = ByzantineMetadata(
            original_count=5,
            removed_count=1,
            cohesion_q=128,
            strategy=STRATEGY_LEAST_TRUSTED,
        )
        data = encode_byzantine_metadata(meta)
        assert data[3] == 0x40

    def test_metadata_combined_strategy(self):
        """Strategy=10 (combined) in bits 24-25.

        byte 3: 10 000000 = 0x80
        """
        meta = ByzantineMetadata(
            original_count=8,
            removed_count=3,
            cohesion_q=200,
            strategy=STRATEGY_COMBINED,
        )
        data = encode_byzantine_metadata(meta)
        assert data[3] == 0x80

    def test_metadata_roundtrip(self):
        """Encode → decode round-trip."""
        original = ByzantineMetadata(
            original_count=20,
            removed_count=4,
            cohesion_q=190,
            strategy=STRATEGY_COMBINED,
        )
        data = encode_byzantine_metadata(original)
        recovered = decode_byzantine_metadata(data)
        assert recovered.original_count == 20
        assert recovered.removed_count == 4
        assert recovered.cohesion_q == 190
        assert recovered.strategy == STRATEGY_COMBINED

    @given(
        original=st.integers(min_value=0, max_value=255),
        removed=st.integers(min_value=0, max_value=255),
        cohesion=st.integers(min_value=0, max_value=255),
        strategy=st.integers(min_value=0, max_value=2),
    )
    @settings(max_examples=500, deadline=None)
    def test_metadata_roundtrip_property(self, original, removed, cohesion, strategy):
        """Property: any valid metadata round-trips exactly."""
        meta = ByzantineMetadata(
            original_count=original,
            removed_count=removed,
            cohesion_q=cohesion,
            strategy=strategy,
        )
        data = encode_byzantine_metadata(meta)
        recovered = decode_byzantine_metadata(data)
        assert recovered.original_count == original
        assert recovered.removed_count == removed
        assert recovered.cohesion_q == cohesion
        assert recovered.strategy == strategy

    def test_metadata_zero_removals(self):
        """Zero removals (all sources survived) encodes correctly."""
        meta = ByzantineMetadata(
            original_count=5,
            removed_count=0,
            cohesion_q=255,
            strategy=STRATEGY_MOST_CONFLICTING,
        )
        data = encode_byzantine_metadata(meta)
        recovered = decode_byzantine_metadata(data)
        assert recovered.removed_count == 0
        assert recovered.cohesion_q == 255

    def test_metadata_from_byzantine_fuse(self):
        """Byzantine metadata from an actual jsonld-ex byzantine_fuse result.

        Cross-validates that our metadata can faithfully represent the
        output of a real Byzantine filtering operation.
        """
        honest = [
            Opinion(0.8, 0.1, 0.1, 0.5),
            Opinion(0.7, 0.1, 0.2, 0.5),
            Opinion(0.75, 0.15, 0.10, 0.5),
        ]
        rogue = [Opinion(0.0, 0.9, 0.1, 0.5)]

        report = byzantine_fuse(honest + rogue)

        # Build metadata from report
        cohesion_q = round(report.cohesion_score * 255)
        meta = ByzantineMetadata(
            original_count=4,
            removed_count=len(report.removed),
            cohesion_q=cohesion_q,
            strategy=STRATEGY_MOST_CONFLICTING,
        )

        data = encode_byzantine_metadata(meta)
        recovered = decode_byzantine_metadata(data)

        assert recovered.original_count == 4
        assert recovered.removed_count >= 1  # rogue should be removed
        # Cohesion round-trips within Q8 tolerance
        assert abs(recovered.cohesion_q / 255.0 - report.cohesion_score) <= 1.0 / 255


# =========================================================================
# 3. Provenance Entry — bit-packed (16 bytes)
#
# Byte 0:   [origin_tier:2][operator_id:4][precision_mode:2]
# Bytes 1-3: opinion (b̂, d̂, â) — 3 × 8-bit
# Bytes 4-7: timestamp (uint32, seconds since epoch)
# Bytes 8-15: prev_digest (64 bits, chained SHA-256)
#
# Total: 128 bits = 16 bytes. Zero waste.
# =========================================================================

class TestProvenanceEntry:
    """Bit-packed provenance chain entries."""

    def test_entry_is_16_bytes(self):
        """Each provenance entry is exactly 16 bytes."""
        entry = ProvenanceEntry(
            origin_tier=0,  # constrained
            operator_id=0,  # none
            precision_mode=0,  # 8-bit
            b_q=217, d_q=13, a_q=128,
            timestamp=1710230400,
            prev_digest=CHAIN_ORIGIN_SENTINEL,
        )
        data = encode_provenance_entry(entry)
        assert len(data) == PROVENANCE_ENTRY_SIZE
        assert PROVENANCE_ENTRY_SIZE == 16

    def test_entry_byte0_bit_layout(self):
        """Verify byte 0: [origin_tier:2][operator_id:4][precision_mode:2].

        origin_tier=01(edge), operator_id=0100(jurisdictional_meet),
        precision_mode=00(8-bit)

        Byte 0: 01 0100 00 = 0x50
        """
        entry = ProvenanceEntry(
            origin_tier=1,  # edge
            operator_id=4,  # jurisdictional_meet
            precision_mode=0,  # 8-bit
            b_q=200, d_q=30, a_q=128,
            timestamp=1710230400,
            prev_digest=CHAIN_ORIGIN_SENTINEL,
        )
        data = encode_provenance_entry(entry)
        assert data[0] == 0x50

    def test_entry_opinion_bytes(self):
        """Bytes 1-3 contain the opinion (b̂, d̂, â)."""
        entry = ProvenanceEntry(
            origin_tier=0,
            operator_id=0,
            precision_mode=0,
            b_q=217, d_q=13, a_q=128,
            timestamp=0,
            prev_digest=CHAIN_ORIGIN_SENTINEL,
        )
        data = encode_provenance_entry(entry)
        assert data[1] == 217  # b̂
        assert data[2] == 13   # d̂
        assert data[3] == 128  # â

    def test_entry_timestamp(self):
        """Bytes 4-7 contain the timestamp as uint32 big-endian."""
        ts = 1710230400  # 2024-03-12T00:00:00Z
        entry = ProvenanceEntry(
            origin_tier=0, operator_id=0, precision_mode=0,
            b_q=0, d_q=0, a_q=0,
            timestamp=ts,
            prev_digest=CHAIN_ORIGIN_SENTINEL,
        )
        data = encode_provenance_entry(entry)
        recovered_ts = struct.unpack(">I", data[4:8])[0]
        assert recovered_ts == ts

    def test_entry_prev_digest(self):
        """Bytes 8-15 contain the previous entry's digest."""
        prev = b"\xDE\xAD\xBE\xEF\xCA\xFE\xBA\xBE"
        entry = ProvenanceEntry(
            origin_tier=0, operator_id=0, precision_mode=0,
            b_q=0, d_q=0, a_q=0,
            timestamp=0,
            prev_digest=prev,
        )
        data = encode_provenance_entry(entry)
        assert data[8:16] == prev

    def test_entry_chain_origin_sentinel(self):
        """Chain origin uses the sentinel (8 zero bytes)."""
        assert CHAIN_ORIGIN_SENTINEL == b"\x00" * 8
        entry = ProvenanceEntry(
            origin_tier=0, operator_id=0, precision_mode=0,
            b_q=217, d_q=13, a_q=128,
            timestamp=1710230400,
            prev_digest=CHAIN_ORIGIN_SENTINEL,
        )
        data = encode_provenance_entry(entry)
        assert data[8:16] == b"\x00" * 8

    def test_entry_roundtrip(self):
        """Encode → decode round-trip."""
        original = ProvenanceEntry(
            origin_tier=1,
            operator_id=4,
            precision_mode=0,
            b_q=200, d_q=30, a_q=128,
            timestamp=1710230400,
            prev_digest=b"\x01\x02\x03\x04\x05\x06\x07\x08",
        )
        data = encode_provenance_entry(original)
        recovered = decode_provenance_entry(data)

        assert recovered.origin_tier == 1
        assert recovered.operator_id == 4
        assert recovered.precision_mode == 0
        assert recovered.b_q == 200
        assert recovered.d_q == 30
        assert recovered.a_q == 128
        assert recovered.timestamp == 1710230400
        assert recovered.prev_digest == b"\x01\x02\x03\x04\x05\x06\x07\x08"

    @given(
        origin_tier=st.integers(min_value=0, max_value=2),
        operator_id=st.integers(min_value=0, max_value=12),
        precision_mode=st.integers(min_value=0, max_value=2),
        b_q=st.integers(min_value=0, max_value=255),
        d_q=st.integers(min_value=0, max_value=255),
        a_q=st.integers(min_value=0, max_value=255),
        timestamp=st.integers(min_value=0, max_value=2**32 - 1),
        prev_digest=st.binary(min_size=8, max_size=8),
    )
    @settings(max_examples=500, deadline=None)
    def test_entry_roundtrip_property(
        self, origin_tier, operator_id, precision_mode,
        b_q, d_q, a_q, timestamp, prev_digest,
    ):
        """Property: any valid entry round-trips exactly."""
        original = ProvenanceEntry(
            origin_tier=origin_tier,
            operator_id=operator_id,
            precision_mode=precision_mode,
            b_q=b_q, d_q=d_q, a_q=a_q,
            timestamp=timestamp,
            prev_digest=prev_digest,
        )
        data = encode_provenance_entry(original)
        assert len(data) == 16
        recovered = decode_provenance_entry(data)
        assert recovered.origin_tier == origin_tier
        assert recovered.operator_id == operator_id
        assert recovered.precision_mode == precision_mode
        assert recovered.b_q == b_q
        assert recovered.d_q == d_q
        assert recovered.a_q == a_q
        assert recovered.timestamp == timestamp
        assert recovered.prev_digest == prev_digest


# =========================================================================
# 4. Provenance Chain — chained digests, verification
# =========================================================================

class TestProvenanceChain:
    """Provenance chain encoding, chained digests, tamper detection."""

    def _make_chain_entries(self):
        """Build a valid 3-entry chain: Tier 1 → Tier 2 → Tier 3."""
        # Entry 1: Tier 1 raw observation
        e1 = ProvenanceEntry(
            origin_tier=0,
            operator_id=0,  # none (raw observation)
            precision_mode=0,
            b_q=217, d_q=13, a_q=128,
            timestamp=1710230400,
            prev_digest=CHAIN_ORIGIN_SENTINEL,
        )

        # Entry 2: Tier 2 fused (references e1)
        e1_bytes = encode_provenance_entry(e1)
        e1_digest = compute_entry_digest(e1_bytes)
        e2 = ProvenanceEntry(
            origin_tier=1,
            operator_id=1,  # cumulative_fusion
            precision_mode=0,
            b_q=200, d_q=25, a_q=128,
            timestamp=1710230460,
            prev_digest=e1_digest,
        )

        # Entry 3: Tier 3 evaluated (references e2)
        e2_bytes = encode_provenance_entry(e2)
        e2_digest = compute_entry_digest(e2_bytes)
        e3 = ProvenanceEntry(
            origin_tier=2,
            operator_id=4,  # jurisdictional_meet
            precision_mode=0,
            b_q=180, d_q=40, a_q=128,
            timestamp=1710230520,
            prev_digest=e2_digest,
        )

        return [e1, e2, e3]

    def test_chain_encode_decode_roundtrip(self):
        """Encode chain → decode → entries match."""
        entries = self._make_chain_entries()
        data = encode_provenance_chain(entries)
        assert len(data) == 3 * PROVENANCE_ENTRY_SIZE  # 48 bytes

        recovered = decode_provenance_chain(data, count=3)
        assert len(recovered) == 3
        for i in range(3):
            assert recovered[i].b_q == entries[i].b_q
            assert recovered[i].timestamp == entries[i].timestamp
            assert recovered[i].prev_digest == entries[i].prev_digest

    def test_chain_verification_succeeds(self):
        """Valid chain passes verification."""
        entries = self._make_chain_entries()
        data = encode_provenance_chain(entries)
        chain = decode_provenance_chain(data, count=3)
        is_valid, error_index = verify_provenance_chain(chain)
        assert is_valid is True
        assert error_index == -1

    def test_chain_tamper_entry1_detected(self):
        """Modifying entry 1 causes verification failure at entry 2.

        Entry 2's prev_digest was computed over the original entry 1.
        After tampering, the recomputed digest of entry 1 won't match.
        """
        entries = self._make_chain_entries()

        # Tamper: change entry 1's belief
        entries[0] = ProvenanceEntry(
            origin_tier=entries[0].origin_tier,
            operator_id=entries[0].operator_id,
            precision_mode=entries[0].precision_mode,
            b_q=0,  # TAMPERED: was 217
            d_q=entries[0].d_q,
            a_q=entries[0].a_q,
            timestamp=entries[0].timestamp,
            prev_digest=entries[0].prev_digest,
        )

        is_valid, error_index = verify_provenance_chain(entries)
        assert is_valid is False
        assert error_index == 1  # Failure detected at entry 2

    def test_chain_tamper_entry2_detected(self):
        """Modifying entry 2 causes verification failure at entry 3."""
        entries = self._make_chain_entries()

        # Tamper: change entry 2's operator_id
        entries[1] = ProvenanceEntry(
            origin_tier=entries[1].origin_tier,
            operator_id=7,  # TAMPERED: was 1 (cumulative_fusion)
            precision_mode=entries[1].precision_mode,
            b_q=entries[1].b_q,
            d_q=entries[1].d_q,
            a_q=entries[1].a_q,
            timestamp=entries[1].timestamp,
            prev_digest=entries[1].prev_digest,
        )

        is_valid, error_index = verify_provenance_chain(entries)
        assert is_valid is False
        assert error_index == 2  # Failure at entry 3

    def test_chain_truncation_detected(self):
        """Removing the first entry from a chain is detectable.

        If entry 1 is dropped, entry 2's prev_digest won't be the
        sentinel — indicating the chain was truncated from the start.
        """
        entries = self._make_chain_entries()
        truncated = entries[1:]  # Drop entry 1

        # Entry 2 (now index 0) has a non-sentinel prev_digest
        assert truncated[0].prev_digest != CHAIN_ORIGIN_SENTINEL

        # Verification should fail: first entry must have sentinel
        is_valid, error_index = verify_provenance_chain(truncated)
        assert is_valid is False
        assert error_index == 0  # First entry lacks sentinel

    def test_single_entry_chain_valid(self):
        """A single-entry chain (origin only) is valid."""
        entry = ProvenanceEntry(
            origin_tier=0, operator_id=0, precision_mode=0,
            b_q=217, d_q=13, a_q=128,
            timestamp=1710230400,
            prev_digest=CHAIN_ORIGIN_SENTINEL,
        )
        is_valid, error_index = verify_provenance_chain([entry])
        assert is_valid is True
        assert error_index == -1

    def test_chain_entry_digest_is_deterministic(self):
        """Same entry always produces the same digest."""
        entry = ProvenanceEntry(
            origin_tier=0, operator_id=0, precision_mode=0,
            b_q=217, d_q=13, a_q=128,
            timestamp=1710230400,
            prev_digest=CHAIN_ORIGIN_SENTINEL,
        )
        d1 = compute_entry_digest(encode_provenance_entry(entry))
        d2 = compute_entry_digest(encode_provenance_entry(entry))
        assert d1 == d2
        assert len(d1) == 8


# =========================================================================
# 5. Integration — Byzantine filtering through the full pipeline
# =========================================================================

class TestSecurityIntegration:
    """Security primitives used in realistic Tier 2/3 scenarios."""

    def test_tier2_byzantine_fuse_and_encode(self):
        """Full pipeline: opinions → byzantine_fuse → quantize → annotate
        with byzantine metadata → encode → decode → verify metadata."""
        honest = [
            Opinion(0.8, 0.1, 0.1, 0.5),
            Opinion(0.7, 0.15, 0.15, 0.5),
            Opinion(0.75, 0.1, 0.15, 0.5),
            Opinion(0.85, 0.05, 0.10, 0.5),
        ]
        rogue = [Opinion(0.0, 0.9, 0.1, 0.5)]

        report = byzantine_fuse(honest + rogue)
        fused = report.fused

        # Quantize the fused result
        b_q, d_q, u_q, a_q = quantize_binomial(
            fused.belief, fused.disbelief, fused.uncertainty,
            fused.base_rate, precision=8,
        )
        # Axiom 3
        assert b_q + d_q + u_q == 255

        # Build Byzantine metadata
        cohesion_q = round(report.cohesion_score * 255)
        meta = ByzantineMetadata(
            original_count=5,
            removed_count=len(report.removed),
            cohesion_q=cohesion_q,
            strategy=STRATEGY_MOST_CONFLICTING,
        )

        # Encode and decode metadata
        meta_bytes = encode_byzantine_metadata(meta)
        recovered_meta = decode_byzantine_metadata(meta_bytes)

        assert recovered_meta.original_count == 5
        assert recovered_meta.removed_count >= 1
        assert abs(recovered_meta.cohesion_q / 255.0 - report.cohesion_score) <= 1.0 / 255

    def test_provenance_chain_with_annotation_digests(self):
        """Build a provenance chain where each entry's prev_digest
        chains to the annotation digest, not just the entry digest.

        This tests the full Tier 1 → Tier 2 → Tier 3 audit trail.
        """
        # Tier 1: raw observation
        ann1 = Annotation(
            header=Tier1Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.BITS_8,
            ),
            opinion=quantize_binomial(0.85, 0.05, 0.10, 0.50),
        )
        ann1_bytes = encode_annotation(ann1)
        ann1_digest = compute_annotation_digest(ann1_bytes)

        e1 = ProvenanceEntry(
            origin_tier=0, operator_id=0, precision_mode=0,
            b_q=ann1.opinion[0], d_q=ann1.opinion[1], a_q=ann1.opinion[3],
            timestamp=1710230400,
            prev_digest=CHAIN_ORIGIN_SENTINEL,
        )

        # Tier 2: fused result
        e1_digest = compute_entry_digest(encode_provenance_entry(e1))
        e2 = ProvenanceEntry(
            origin_tier=1, operator_id=1, precision_mode=0,
            b_q=200, d_q=25, a_q=128,
            timestamp=1710230460,
            prev_digest=e1_digest,
        )

        chain = [e1, e2]
        is_valid, _ = verify_provenance_chain(chain)
        assert is_valid

        # Annotation digest is independent of chain digests
        assert verify_annotation_digest(ann1_bytes, ann1_digest)

    def test_axiom3_unaffected_by_security(self):
        """Security primitives don't touch opinion values.

        Byzantine metadata and digests are metadata about annotations,
        not modifications to opinions. Axiom 3 is orthogonal.
        """
        opinion = quantize_binomial(0.70, 0.20, 0.10, 0.50, precision=8)
        b_q, d_q, u_q, a_q = opinion
        assert b_q + d_q + u_q == 255

        # Build annotation
        ann = Annotation(
            header=Tier2Header(
                compliance_status=ComplianceStatus.COMPLIANT,
                delegation_flag=False,
                has_opinion=True,
                precision_mode=PrecisionMode.BITS_8,
                operator_id=OperatorId.CUMULATIVE_FUSION,
                reasoning_context=0,
                context_version=1,
                has_multinomial=False,
                sub_tier_depth=0,
                source_count=5,
            ),
            opinion=opinion,
        )

        # Compute digest — does NOT alter the annotation
        ann_bytes = encode_annotation(ann)
        digest = compute_annotation_digest(ann_bytes)
        assert len(digest) == 8

        # Encode Byzantine metadata — does NOT alter the opinion
        meta = ByzantineMetadata(
            original_count=5, removed_count=1,
            cohesion_q=217, strategy=STRATEGY_MOST_CONFLICTING,
        )
        meta_bytes = encode_byzantine_metadata(meta)
        assert len(meta_bytes) == 4

        # Build provenance entry — opinion values pass through unchanged
        entry = ProvenanceEntry(
            origin_tier=1, operator_id=1, precision_mode=0,
            b_q=b_q, d_q=d_q, a_q=a_q,
            timestamp=1710230400,
            prev_digest=CHAIN_ORIGIN_SENTINEL,
        )
        entry_data = encode_provenance_entry(entry)
        recovered = decode_provenance_entry(entry_data)

        # Axiom 3: opinion values in entry are unchanged
        assert recovered.b_q == b_q
        assert recovered.d_q == d_q
        assert recovered.a_q == a_q
        # û can be derived
        assert b_q + d_q + (255 - b_q - d_q) == 255


# =========================================================================
# 7. Audit-grade provenance entries — 24 bytes, 128-bit digest (§9.4)
# =========================================================================

class TestAuditGradeConstants:
    """Audit-grade constants per Definition 27b."""

    def test_audit_digest_size(self):
        """128-bit digest = 16 bytes."""
        assert AUDIT_DIGEST_SIZE_BYTES == 16

    def test_audit_entry_size(self):
        """24 bytes = 8 (header+opinion+timestamp) + 16 (digest)."""
        assert AUDIT_ENTRY_SIZE == 24

    def test_audit_sentinel_size(self):
        """Audit sentinel is 16 zero bytes."""
        assert AUDIT_CHAIN_ORIGIN_SENTINEL == b"\x00" * 16
        assert len(AUDIT_CHAIN_ORIGIN_SENTINEL) == 16


class TestAuditGradeEntry:
    """Audit-grade 24-byte provenance entries (Definition 27b)."""

    def test_encode_size_exactly_24_bytes(self):
        """Audit-grade entry is exactly 24 bytes."""
        entry = ProvenanceEntry(
            origin_tier=2, operator_id=1, precision_mode=0,
            b_q=200, d_q=30, a_q=128,
            timestamp=1710230400,
            prev_digest=AUDIT_CHAIN_ORIGIN_SENTINEL,
        )
        data = encode_provenance_entry(entry, audit_grade=True)
        assert len(data) == 24

    def test_encode_decode_roundtrip(self):
        """Full roundtrip: encode → decode → same entry."""
        entry = ProvenanceEntry(
            origin_tier=2, operator_id=5, precision_mode=0,
            b_q=180, d_q=40, a_q=100,
            timestamp=1710230400,
            prev_digest=AUDIT_CHAIN_ORIGIN_SENTINEL,
        )
        data = encode_provenance_entry(entry, audit_grade=True)
        recovered = decode_provenance_entry(data, audit_grade=True)

        assert recovered.origin_tier == 2
        assert recovered.operator_id == 5
        assert recovered.precision_mode == 0
        assert recovered.b_q == 180
        assert recovered.d_q == 40
        assert recovered.a_q == 100
        assert recovered.timestamp == 1710230400
        assert recovered.prev_digest == AUDIT_CHAIN_ORIGIN_SENTINEL
        assert len(recovered.prev_digest) == 16

    def test_first_8_bytes_identical_to_standard(self):
        """Bytes 0–7 are identical between standard and audit-grade.

        Only the digest portion (bytes 8+) differs in size.
        """
        entry = ProvenanceEntry(
            origin_tier=1, operator_id=7, precision_mode=0,
            b_q=200, d_q=30, a_q=128,
            timestamp=1710230400,
            prev_digest=CHAIN_ORIGIN_SENTINEL,
        )
        entry_audit = ProvenanceEntry(
            origin_tier=1, operator_id=7, precision_mode=0,
            b_q=200, d_q=30, a_q=128,
            timestamp=1710230400,
            prev_digest=AUDIT_CHAIN_ORIGIN_SENTINEL,
        )
        std_data = encode_provenance_entry(entry, audit_grade=False)
        aud_data = encode_provenance_entry(entry_audit, audit_grade=True)

        assert std_data[:8] == aud_data[:8]

    def test_audit_digest_is_128_bit_sha256(self):
        """Audit entry digest is first 16 bytes of SHA-256."""
        entry = ProvenanceEntry(
            origin_tier=2, operator_id=1, precision_mode=0,
            b_q=200, d_q=30, a_q=128,
            timestamp=1710230400,
            prev_digest=AUDIT_CHAIN_ORIGIN_SENTINEL,
        )
        entry_bytes = encode_provenance_entry(entry, audit_grade=True)
        digest = compute_entry_digest(entry_bytes, audit_grade=True)
        assert len(digest) == 16

        # Must match first 16 bytes of SHA-256
        expected = hashlib.sha256(entry_bytes).digest()[:16]
        assert digest == expected


class TestAuditGradeChain:
    """Audit-grade provenance chain: chained 128-bit digests."""

    def test_chain_encode_decode_roundtrip(self):
        """Encode → decode roundtrip for audit-grade chain."""
        e1 = ProvenanceEntry(
            origin_tier=1, operator_id=0, precision_mode=0,
            b_q=200, d_q=30, a_q=128,
            timestamp=1710230400,
            prev_digest=AUDIT_CHAIN_ORIGIN_SENTINEL,
        )
        e1_bytes = encode_provenance_entry(e1, audit_grade=True)
        e1_digest = compute_entry_digest(e1_bytes, audit_grade=True)

        e2 = ProvenanceEntry(
            origin_tier=1, operator_id=1, precision_mode=0,
            b_q=190, d_q=35, a_q=128,
            timestamp=1710230410,
            prev_digest=e1_digest,
        )

        chain_bytes = encode_provenance_chain([e1, e2], audit_grade=True)
        assert len(chain_bytes) == 48  # 2 × 24

        recovered = decode_provenance_chain(chain_bytes, count=2, audit_grade=True)
        assert len(recovered) == 2
        assert recovered[0].b_q == 200
        assert recovered[1].b_q == 190
        assert len(recovered[1].prev_digest) == 16

    def test_chain_verify_valid(self):
        """Valid audit-grade chain passes verification."""
        e1 = ProvenanceEntry(
            origin_tier=1, operator_id=0, precision_mode=0,
            b_q=200, d_q=30, a_q=128,
            timestamp=1710230400,
            prev_digest=AUDIT_CHAIN_ORIGIN_SENTINEL,
        )
        e1_bytes = encode_provenance_entry(e1, audit_grade=True)
        e1_digest = compute_entry_digest(e1_bytes, audit_grade=True)

        e2 = ProvenanceEntry(
            origin_tier=1, operator_id=1, precision_mode=0,
            b_q=190, d_q=35, a_q=128,
            timestamp=1710230410,
            prev_digest=e1_digest,
        )

        is_valid, err_idx = verify_provenance_chain([e1, e2], audit_grade=True)
        assert is_valid is True
        assert err_idx == -1

    def test_chain_verify_detects_tamper(self):
        """Modifying an audit-grade entry invalidates the chain."""
        e1 = ProvenanceEntry(
            origin_tier=1, operator_id=0, precision_mode=0,
            b_q=200, d_q=30, a_q=128,
            timestamp=1710230400,
            prev_digest=AUDIT_CHAIN_ORIGIN_SENTINEL,
        )
        e1_bytes = encode_provenance_entry(e1, audit_grade=True)
        e1_digest = compute_entry_digest(e1_bytes, audit_grade=True)

        e2 = ProvenanceEntry(
            origin_tier=1, operator_id=1, precision_mode=0,
            b_q=190, d_q=35, a_q=128,
            timestamp=1710230410,
            prev_digest=e1_digest,
        )

        # Tamper with e1
        e1.b_q = 199
        is_valid, err_idx = verify_provenance_chain([e1, e2], audit_grade=True)
        assert is_valid is False
        assert err_idx == 1

    def test_chain_verify_detects_wrong_sentinel(self):
        """Audit chain must use 16-byte sentinel, not 8-byte."""
        e1 = ProvenanceEntry(
            origin_tier=1, operator_id=0, precision_mode=0,
            b_q=200, d_q=30, a_q=128,
            timestamp=1710230400,
            prev_digest=CHAIN_ORIGIN_SENTINEL,  # wrong: 8 bytes
        )
        is_valid, err_idx = verify_provenance_chain([e1], audit_grade=True)
        assert is_valid is False
        assert err_idx == 0

    def test_standard_and_audit_chains_independent(self):
        """Standard and audit chains for same data produce different digests."""
        entry = ProvenanceEntry(
            origin_tier=1, operator_id=0, precision_mode=0,
            b_q=200, d_q=30, a_q=128,
            timestamp=1710230400,
            prev_digest=CHAIN_ORIGIN_SENTINEL,
        )
        entry_audit = ProvenanceEntry(
            origin_tier=1, operator_id=0, precision_mode=0,
            b_q=200, d_q=30, a_q=128,
            timestamp=1710230400,
            prev_digest=AUDIT_CHAIN_ORIGIN_SENTINEL,
        )

        std_bytes = encode_provenance_entry(entry, audit_grade=False)
        aud_bytes = encode_provenance_entry(entry_audit, audit_grade=True)

        std_digest = compute_entry_digest(std_bytes, audit_grade=False)
        aud_digest = compute_entry_digest(aud_bytes, audit_grade=True)

        # Different sizes
        assert len(std_digest) == 8
        assert len(aud_digest) == 16
        # Different content (different input bytes due to different sentinel size)
        assert std_digest != aud_digest[:8]
