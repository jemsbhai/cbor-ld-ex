# CBOR-LD-ex Implementation Plan

**Version:** 1.0  
**Date:** 2026-03-12  
**Depends on:** `spec/FORMAL_MODEL.md` v0.1.0-draft  
**Target:** IETF 125 Hackathon demo (March 14–15 2026)  
**Methodology:** Strict TDD — tests first, red phase confirmed, implementation written, green phase confirmed, then commit.

---

## 1. Project Structure

```
cborldex/
├── spec/
│   ├── FORMAL_MODEL.md          # Formal specification (ground truth)
│   └── SESSION_LOG.md           # Session continuity
├── src/
│   └── cbor_ld_ex/
│       ├── __init__.py
│       ├── opinions.py           # Phase 1: Quantization codec
│       ├── headers.py            # Phase 2: Tier-dependent header codec
│       ├── annotations.py        # Phase 3: Annotation assembly
│       ├── temporal.py           # Phase 4: Decay, triggers, delta encoding
│       ├── security.py           # Phase 5: Byzantine fusion, chain integrity
│       ├── codec.py              # Phase 6: Full CBOR-LD-ex encode/decode
│       └── transport.py          # Phase 7: CoAP transport layer
├── tests/
│   ├── __init__.py
│   ├── test_opinions.py          # Phase 1 tests
│   ├── test_headers.py           # Phase 2 tests
│   ├── test_annotations.py       # Phase 3 tests
│   ├── test_temporal.py          # Phase 4 tests
│   ├── test_security.py          # Phase 5 tests
│   ├── test_codec.py             # Phase 6 tests
│   ├── test_transport.py         # Phase 7 tests
│   └── test_axioms.py            # Cross-cutting: Axiom 1/2/3 verification
├── demo/
│   ├── benchmark.py              # Three-encoding benchmark harness
│   ├── tier1_sim.py              # Simulated Tier 1 constrained device
│   ├── tier2_gateway.py          # Simulated Tier 2 edge gateway
│   ├── tier3_dashboard.py        # Tier 3 display / results collector
│   └── run_demo.py               # End-to-end demo orchestrator
├── pyproject.toml
├── README.md
└── IMPLEMENTATION_STATUS.md      # Tracks phase completion
```

---

## 2. Dependencies

```toml
[project]
name = "cbor-ld-ex"
requires-python = ">=3.10"
dependencies = [
    "cbor2>=5.6.0",          # CBOR encoding/decoding (RFC 8949)
    "jsonld-ex>=0.9.0",      # Parent library — Opinion, fusion, compliance algebra
]

[project.optional-dependencies]
transport = [
    "aiocoap>=0.4.7",        # CoAP protocol (RFC 7252)
]
dev = [
    "pytest>=8.0",
    "pytest-cov>=5.0",
    "hypothesis>=6.100",      # Property-based testing for quantization
]
```

**Environment:** Use `poetry` with `--system-site-packages` venv. Run tests via `poetry run python -m pytest`.

---

## 3. Implementation Phases

Each phase follows strict TDD:
1. Write tests (red phase)
2. Muntaser runs `poetry run python -m pytest tests/test_<module>.py` → confirms failures
3. Write implementation
4. Muntaser runs full suite → confirms green
5. Review, then commit

### Phase 1: Quantization Codec (`opinions.py`, `test_opinions.py`)

**Priority: CRITICAL — everything else depends on this.**

**Tests to write first:**

```
test_quantize_binomial_roundtrip_8bit
    Given ω = (0.85, 0.05, 0.10, 0.50), Q_8 → (217, 13, 25, 128)
    Reconstruct → verify b+d+u = 1.0 exactly

test_quantize_binomial_constraint_preservation
    Property test (Hypothesis): for any valid (b,d,u,a),
    Q_n(b) + Q_n(d) + Q_n(u_derived) == 2^n - 1

test_quantize_binomial_clamping_edge_case
    b=0.5, d=0.5, u=0.0 at 8-bit → verify û ≥ 0 after clamping

test_quantize_binomial_error_bounds
    Verify |x - Q_n⁻¹(Q_n(x))| ≤ 1/(2(2^n - 1)) for all components

test_quantize_multinomial_roundtrip
    k=4, beliefs=(0.6, 0.2, 0.1, 0.0), u=0.1, base_rates=(0.25,0.25,0.25,0.25)
    Verify sum = 1.0 exactly after reconstruction

test_quantize_multinomial_clamping
    Edge case where derived k-th belief would go negative

test_quantize_precision_modes
    Verify 8-bit, 16-bit, 32-bit all produce correct sizes
    Verify 32-bit mode uses IEEE 754 floats directly

test_quantize_vacuous_opinion
    ω_V = (0, 0, 1, 0.5) → verify clean encoding

test_quantize_extreme_opinions
    (1.0, 0.0, 0.0, 0.5), (0.0, 1.0, 0.0, 0.5), (0.0, 0.0, 1.0, 0.5)
```

**Implementation:**

```python
# opinions.py — core functions
def quantize_binomial(b, d, u, a, precision=8) -> tuple[int, int, int, int]
def dequantize_binomial(b_q, d_q, u_q, a_q, precision=8) -> tuple[float, float, float, float]
def quantize_multinomial(beliefs, u, base_rates, precision=8) -> tuple[list[int], int, list[int]]
def dequantize_multinomial(beliefs_q, u_q, base_rates_q, precision=8) -> tuple[list[float], float, list[float]]
def encode_opinion_bytes(b_q, d_q, u_q, a_q, precision=8) -> bytes
def decode_opinion_bytes(data: bytes, precision=8) -> tuple[int, int, int, int]
```

**Estimated effort:** 2–3 hours (tests + implementation).

---

### Phase 2: Tier-Dependent Headers (`headers.py`, `test_headers.py`)

**Tests to write first:**

```
test_tier1_header_encode_decode_roundtrip
    compliance=compliant, delegation=False, has_opinion=True, precision=8bit
    Verify exactly 1 byte

test_tier1_header_all_status_values
    compliant, non_compliant, insufficient → verify 2-bit encoding

test_tier2_header_encode_decode_roundtrip
    All fields populated → verify exactly 4 bytes
    Verify operator_id, reasoning_context, context_version, sub_tier_depth, source_count

test_tier3_header_encode_decode_roundtrip
    Fixed header + variable extensions based on flags

test_tier3_header_with_provenance_chain_flag
    has_provenance_chain=True → verify extension block present

test_tier_discriminator
    Parse raw bytes, verify origin_tier field determines header layout

test_header_reserved_tier
    origin_tier=11 → parser skips without error
```

**Implementation:**

```python
# headers.py
@dataclass
class Tier1Header: ...     # 1 byte
@dataclass
class Tier2Header: ...     # 4 bytes
@dataclass
class Tier3Header: ...     # 4 bytes + extensions

def encode_header(header: Tier1Header | Tier2Header | Tier3Header) -> bytes
def decode_header(data: bytes) -> Tier1Header | Tier2Header | Tier3Header
```

**Estimated effort:** 2–3 hours.

---

### Phase 3: Annotation Assembly (`annotations.py`, `test_annotations.py`)

**Tests to write first:**

```
test_tier1_annotation_full_message
    Header + opinion → verify total 5 bytes
    Roundtrip encode/decode

test_tier1_annotation_no_opinion
    Header only → verify total 1 byte

test_tier2_annotation_with_binomial
    Header + binomial opinion → verify total 8 bytes (4 header + 4 opinion)

test_tier2_annotation_with_multinomial
    Header + multinomial opinion (k=4, 8-bit) → verify total 4 + 10 = 14 bytes

test_annotation_to_cbor_tagged
    Wrap annotation in Tag(60000) → verify valid CBOR

test_axiom1_stripping
    CBOR-LD-ex message → strip Tag(60000) → verify valid CBOR-LD remains

test_annotation_type_roundtrip
    Each annotation variant from Definition 6 → encode → decode → equality
```

**Implementation:**

```python
# annotations.py
@dataclass
class Annotation:
    header: Tier1Header | Tier2Header | Tier3Header
    opinion: Optional[tuple]      # quantized opinion
    multinomial: Optional[tuple]   # quantized multinomial
    temporal: Optional[TemporalBlock]
    provenance: Optional[list]

CBOR_TAG_CBORLD_EX = 60000

def encode_annotation(ann: Annotation) -> bytes
def decode_annotation(data: bytes) -> Annotation
def wrap_cbor_tag(annotation_bytes: bytes) -> bytes   # Tag(60000, ...)
def strip_cbor_tag(tagged: bytes) -> bytes
```

**Estimated effort:** 2 hours.

---

### Phase 4: Temporal Extensions (`temporal.py`, `test_temporal.py`)

**Tests to write first:**

```
test_decay_exponential_quantized
    Apply exponential decay to quantized opinion → verify SL constraint preserved

test_decay_linear_quantized
    Apply linear decay → verify reaches zero at 2*half_life

test_decay_step_quantized
    Apply step decay → verify binary transition

test_temporal_extension_encode_decode
    decay_fn=exponential, half_life=3600s, window, 2 triggers
    Roundtrip

test_expiry_trigger_transfers_belief_to_disbelief
    γ=0 (hard expiry) → verify all b transfers to d

test_review_trigger_accelerates_decay
    Verify half_life reduction by acceleration factor

test_delta_encoding_roundtrip
    Previous opinion + delta → reconstructed opinion → verify constraint

test_delta_encoding_signed_values
    Negative Δb, positive Δd → verify correct reconstruction

test_delta_encoding_overflow_rejection
    Delta that would cause b_new < 0 → verify rejection/error
```

**Implementation:**

```python
# temporal.py
@dataclass
class TemporalBlock: ...

def encode_temporal(block: TemporalBlock) -> bytes
def decode_temporal(data: bytes) -> TemporalBlock
def apply_quantized_decay(b_q, d_q, precision, decay_factor) -> tuple[int, int, int]
def encode_delta(prev_b_q, prev_d_q, new_b_q, new_d_q) -> bytes
def apply_delta(prev_b_q, prev_d_q, delta_bytes, precision) -> tuple[int, int, int]
```

**Estimated effort:** 2 hours.

---

### Phase 5: Security (`security.py`, `test_security.py`)

**Tests to write first:**

```
test_provenance_mac_computation
    Chain of 3 entries → compute HMAC → verify chaining property

test_provenance_mac_tamper_detection
    Modify entry 1 → verify entry 2+ MAC verification fails

test_byzantine_metadata_encode_decode
    original=10, removed=2, cohesion=0.85, strategy=most_conflicting
    Verify 4-byte encoding, roundtrip

test_mac_truncation_64bit
    Full HMAC-SHA256 → truncate to 64 bits → verify correct

test_mac_truncation_128bit
    Full HMAC-SHA256 → truncate to 128 bits → verify correct
```

**Implementation:**

```python
# security.py
def compute_chain_mac(entries: list[bytes], key: bytes, truncate=64) -> bytes
def verify_chain_mac(entries: list[bytes], key: bytes, expected_mac: bytes) -> bool
def encode_byzantine_metadata(original, removed, cohesion, strategy) -> bytes
def decode_byzantine_metadata(data: bytes) -> dict
```

**Estimated effort:** 1.5 hours.

---

### Phase 6: Full Codec (`codec.py`, `test_codec.py`)

**Tests to write first:**

```
test_full_encode_tier1_message
    JSON-LD doc + Tier 1 annotation → CBOR-LD-ex bytes
    Verify size matches prediction (~38-43 bytes)

test_full_decode_tier1_message
    Bytes → JSON-LD doc + Annotation
    Verify all fields recovered

test_full_encode_tier2_fused_message
    Gateway fused opinion with source_count=5, operator=jurisdictional_meet

test_full_encode_tier3_with_provenance
    Cloud message with 3-entry provenance chain

test_axiom1_full_stack
    CBOR-LD-ex → strip → valid CBOR-LD → decompress → valid JSON-LD

test_axiom2_closure_through_codec
    Encode two Tier 1 messages → decode → fuse opinions → encode as Tier 2
    Verify result is valid CBOR-LD-ex

test_axiom3_quantization_through_codec
    Full encode/decode roundtrip → verify b+d+u=1.0 exactly

test_payload_size_comparison
    Same document in JSON-LD, CBOR-LD, CBOR-LD-ex
    Verify CBOR-LD-ex < CBOR-LD < JSON-LD
```

**Implementation:**

```python
# codec.py
def encode(doc: dict, annotation: Annotation, context_registry=None) -> bytes
def decode(data: bytes, context_registry=None) -> tuple[dict, Annotation]
def payload_comparison(doc: dict, annotation: Annotation) -> dict
    # Returns {json_ld_bytes, cbor_ld_bytes, cbor_ld_ex_bytes, ratios}
```

**Estimated effort:** 2 hours.

---

### Phase 7: CoAP Transport (`transport.py`, `test_transport.py`)

**Tests to write first:**

```
test_coap_request_encoding
    CBOR-LD-ex message → CoAP PUT/POST with correct content-format

test_coap_observe_subscription
    CoAP Observe → receive stream of CBOR-LD-ex messages

test_coap_message_fits_single_packet
    Tier 1 message ≤ 127 bytes (802.15.4 MTU)

test_coap_blockwise_for_large_messages
    Tier 3 message with provenance chain → CoAP Block option
```

**Implementation:**

```python
# transport.py (uses aiocoap)
async def send_observation(uri, doc, annotation, protocol) -> None
async def observe_stream(uri, callback, protocol) -> None
```

**Estimated effort:** 2 hours.

---

### Cross-Cutting: Axiom Tests (`test_axioms.py`)

These tests verify the three axioms at the integration level:

```
test_axiom1_stripping_property_comprehensive
    Generate 100 random CBOR-LD-ex messages (Hypothesis)
    Strip annotations → verify each is valid CBOR-LD
    Decompress → verify each is valid JSON-LD

test_axiom2_closure_fusion
    Two random valid annotations → fuse → verify result is valid

test_axiom2_closure_meet
    Two random valid annotations → jurisdictional meet → verify valid

test_axiom2_closure_decay
    Random valid annotation → decay at random time → verify valid

test_axiom3_quantization_roundtrip_exhaustive
    For 8-bit: test all 256×256 possible (b̂, d̂) pairs
    Verify û ≥ 0 and sum = 255 for every pair

test_axiom3_quantization_through_operators
    Quantize → apply operator → verify constraint holds in result
```

**Estimated effort:** 2 hours.

---

## 4. Demo Pipeline (Hackathon Deliverable)

### benchmark.py

Generates N sensor readings, encodes in three formats, measures:

| Metric | How measured |
|---|---|
| Message size (bytes) | `len(encoded)` for each format |
| Encode time (μs) | `time.perf_counter_ns()` around encode |
| Decode time (μs) | `time.perf_counter_ns()` around decode |
| Messages per 802.15.4 frame | `127 // message_size` |
| Semantic completeness | Table of what each format conveys |

Output: CSV + summary table for presentation.

### Three-Node Simulation

**tier1_sim.py:** Generates temperature readings at configurable interval. Encodes as Tier 1 CBOR-LD-ex (5-byte annotation). Sends via CoAP to Tier 2 address.

**tier2_gateway.py:** Receives Tier 1 CoAP messages. Applies temporal fusion (decay older readings). Applies Byzantine filtering (if multiple Tier 1 sources). Runs compliance evaluation (jurisdictional meet). Emits Tier 2 CBOR-LD-ex with fused opinion, source_count, operator_id. Sends to Tier 3.

**tier3_dashboard.py:** Receives Tier 2 messages. Reconstructs provenance chain. Displays compliance status, opinion evolution over time, and audit trail. Generates benchmark comparison output.

**run_demo.py:** Orchestrates all three nodes in separate async tasks. Runs for configurable duration (default 60 seconds). Produces final benchmark report.

### Presentation Slide Targets

1. **Architecture diagram** — Three-tier pipeline with byte counts at each hop
2. **Benchmark table** — JSON-LD vs CBOR-LD vs CBOR-LD-ex (size, time, semantic completeness)
3. **Live/recorded demo** — Compliance status flowing through the pipeline
4. **Novel contribution** — Bit-packed SL primitives inside CBOR, with formal guarantees

---

## 5. Phase Execution Order for Hackathon Weekend

### Friday evening (pre-hackathon prep)
- [ ] Set up project skeleton: `pyproject.toml`, directories, `__init__.py` files
- [ ] Install dependencies: `cbor2`, `jsonld-ex`, `aiocoap`, `pytest`, `hypothesis`
- [ ] Write Phase 1 tests (red phase)

### Saturday (Hackathon Day 1)
- [ ] **Morning:** Phase 1 implementation (quantization) → green
- [ ] **Morning:** Phase 2 tests + implementation (headers) → green
- [ ] **Midday:** Phase 3 tests + implementation (annotations) → green
- [ ] **Afternoon:** Phase 6 tests + implementation (full codec) → green
- [ ] **Afternoon:** Axiom tests → green
- [ ] **Evening:** benchmark.py — generate comparison numbers

### Sunday (Hackathon Day 2)
- [ ] **Morning:** Phase 4 (temporal) if time permits — nice-to-have for demo
- [ ] **Morning:** Phase 7 (CoAP transport) — minimum viable demo pipeline
- [ ] **Midday:** Demo simulation: tier1_sim → tier2_gateway → tier3_dashboard
- [ ] **By 13:30:** Hacking stops. Prepare slides.
- [ ] **By 13:30:** Upload presentation to Datatracker
- [ ] **14:00–16:00:** Present results

### Phases 4, 5 can be deferred post-hackathon
- Temporal extensions and security are formally specified and can be implemented after the demo
- The core demo needs: quantization (Phase 1), headers (Phase 2), annotations (Phase 3), codec (Phase 6)

---

## 6. Success Criteria

### Minimum viable demo (must achieve)
- [ ] CBOR-LD-ex codec encodes/decodes Tier 1 and Tier 2 messages
- [ ] Benchmark shows size comparison across three encodings
- [ ] All three axioms verified by tests
- [ ] 5-minute presentation with benchmark numbers

### Full demo (stretch goal)
- [ ] CoAP transport between simulated tiers
- [ ] Temporal decay visible in tier-to-tier pipeline
- [ ] Byzantine filtering at Tier 2
- [ ] Live dashboard showing compliance status evolution

### Post-hackathon (implementation completion)
- [ ] All 7 phases implemented and tested
- [ ] Full test coverage including property-based tests
- [ ] Published to PyPI as `cbor-ld-ex`
- [ ] Integration with jsonld-ex as optional dependency
- [ ] Paper draft for journal submission

---

## 7. Risk Mitigation

| Risk | Mitigation |
|---|---|
| Phase 1 takes longer than expected | Quantization is well-specified; Hypothesis tests may find edge cases that need fixing — budget extra time |
| CoAP setup issues | Fall back to direct function calls (no network) for demo; benchmark doesn't need transport |
| `aiocoap` compatibility on Windows | Test early Friday evening; fall back to MQTT (already in jsonld-ex) if CoAP fails |
| Context limits during implementation | Session log + continuation prompt ensures seamless handoff |
| Demo doesn't produce impressive numbers | Numbers are predicted by formal model; if reality differs, investigate and present honestly |
