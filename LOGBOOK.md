# Experimental Logbook — CBOR-LD-ex Paper #1 (IEEE WF-IoT)

---

## EXP-001: Synthetic 38-Scenario Benchmark (6-Way Comparison)

**Date:** pre-2026-05-30 (accumulated across multiple sessions)
**Researcher:** Muntaser Syed
**Type:** computational
**Status:** completed

### Hypothesis
CBOR-LD-ex produces smaller wire representations than JSON-LD, jsonld-ex
CBOR-LD, and standard CBOR-LD (with and without annotation) across a
systematic matrix of document sizes, annotation tiers, and precision modes.

### Independent Variables
- Document profile: minimal_temperature, environmental_monitor,
  industrial_machine, aggregate_fleet (4 profiles)
- Annotation tier: Tier 1 (1B), Tier 2 (4B)
- Precision mode: 8-bit, 16-bit, 32-bit
- Compliance status: COMPLIANT, NON_COMPLIANT, INSUFFICIENT
- Extensions: none, temporal, temporal+trigger

### Dependent Variables / Metrics
- Wire size (bytes) per encoding: JSON-LD, jsonld-ex CBOR-LD, our CBOR-LD
  (data only), jsonld-ex + annotation, our CBOR-LD + annotation, CBOR-LD-ex
- Compression ratio vs JSON-LD (%)
- Bit efficiency: Shannon information bits / wire bits (%)

### Results
- 38 scenarios evaluated
- Geometric mean compression vs JSON-LD: 79.8%
- Smallest CBOR-LD-ex message: 55 bytes (minimal_temperature/t1-8bit)
- Bit efficiency: 75.7%–96.8% depending on precision mode
- Tier 1 8-bit: 93.0% bit efficiency
- Tier 2 8-bit: 95.5% bit efficiency

### Artifacts
- Output: demo/output/benchmark.{md,csv,tex}
- Code: benchmarks/cbor_ld_ex_benchmark/__init__.py
- Tests: tests/test_benchmark.py (scenario, metric, invariant, formatting sections)

---

## EXP-002: SL Quantization Correctness + Bit-Efficiency

**Date:** pre-2026-05-30
**Researcher:** Muntaser Syed
**Type:** computational
**Status:** completed

### Hypothesis
Constrained binomial quantization (Definition 10) preserves the simplex
constraint b̂ + d̂ + û = 2^n - 1 exactly (Theorem 1) at all precisions,
and the symmetric clamping rule eliminates belief bias.

### Results
- 968 tests passing (zero failures) covering:
  - Binomial quantization round-trip at 8/16/32-bit
  - Multinomial quantization constraint preservation (Theorem 3)
  - Symmetric clamping tiebreaker (â LSB rule)
  - Delta mode (DELTA_8) encode/decode
  - _validate_bits() enforcement (b ∈ {2..8})
  - _NORM_MAX_LOOKUP: 15 Rust-canonical hex-pinned float32 values
  - Wire format self-description via seed MSB mode flag

### Artifacts
- Code: src/cbor_ld_ex/opinions.py, headers.py, annotations.py, codec.py
- Tests: tests/test_opinions.py, test_headers.py, test_annotations.py,
  test_codec.py, test_axioms.py

---

## EXP-003: Batch Compression RD + Ablation

**Date:** pre-2026-05-30
**Researcher:** Muntaser Syed
**Type:** computational
**Status:** completed

### Hypothesis
Batch opinion encoding via RHT (Random Hadamard Transform) + Lloyd-Max
quantization achieves better compression than individual per-opinion
encoding at equal or better distortion.

### Results
- **Honest negative finding (enshrined):** Independent per-component
  quantization beats RHT + Lloyd-Max on MSE at equal bit-width.
  RHT's value is compression (fewer wire bits via coordinated encoding),
  NOT distortion improvement.
- ρ ≈ 4.2 (spec §11.7 reports honestly)
- Lloyd-Max MSE < uniform MSE at all bit-widths (validated)
- Shannon R-D bound D_G(b) < all achieved codebook MSE (validated)
- ρ increases with bits (approaching asymptote)

### Artifacts
- Code: src/cbor_ld_ex/batch.py (~40KB)
- Tests: tests/test_batch.py (batch compression, distortion, ablation,
  constraint, RD curve sections)
- Benchmark: benchmarks/cbor_ld_ex_benchmark/__init__.py
  (run_batch_compression_analysis, run_batch_distortion_analysis,
   run_batch_ablation_analysis, run_batch_constraint_analysis,
   run_batch_rd_curve)

---

## EXP-004: Provenance/Integrity Overhead

**Date:** pre-2026-05-30
**Researcher:** Muntaser Syed
**Type:** computational
**Status:** completed

### Hypothesis
Provenance chain encoding in CBOR-LD-ex adds bounded overhead per chain
entry, and integrity verification (CRC-8 digest) is correct.

### Results
- Provenance entry: fixed PROVENANCE_ENTRY_SIZE bytes per entry
- Audit entry: fixed AUDIT_ENTRY_SIZE bytes per entry
- Chain integrity verified via compute_entry_digest
- Benchmark: run_provenance_analysis in benchmark module

### Artifacts
- Code: src/cbor_ld_ex/security.py (~17KB)
- Tests: tests/test_security.py
- Benchmark: benchmarks/cbor_ld_ex_benchmark/__init__.py
  (build_provenance_configs, run_provenance_analysis)

---

## EXP-005: Tier 1→2→3 Simulation Pipeline

**Date:** pre-2026-05-30
**Researcher:** Muntaser Syed
**Type:** computational
**Status:** completed

### Hypothesis
The full CBOR-LD-ex pipeline (constrained sensors → edge gateway → cloud)
preserves opinion validity, quantization constraints, and provenance
integrity across all tiers, with deterministic results via fixed seed.

### Results
- Pipeline: N sensors → temporal decay → Byzantine filter → cumulative
  fusion → provenance chain → audit summary
- One deliberate outlier sensor successfully filtered
- All encoding/decoding hops verified via real CBOR-LD-ex codec
- MQTT and CoAP transport adapters produce identical payloads
- Deterministic via seed — identical results on every run

### Artifacts
- Code: benchmarks/cbor_ld_ex_benchmark/simulation.py (~20KB)
- Tests: tests/test_simulation.py (6 sections: sensor gen, gateway,
  cloud, end-to-end, transport, scientific invariants)

---

## EXP-006: Wire-Size Experiment (9 Formats × 4 Real Datasets)

**Date:** 2026-05-30
**Researcher:** Muntaser Syed
**Type:** computational
**Status:** completed

### Hypothesis
CBOR-LD-ex produces the smallest wire representation among self-describing
formats for real IoT sensor records, due to context compression (integer
keys) and bit-packed annotation encoding.

### Independent Variables
- Encoding format: JSON-LD, jsonld-ex CBOR-LD, CBOR-LD, CBOR-LD-ex,
  SenML/JSON, SenML/CBOR, Protobuf, FlatBuffers, MessagePack
- Dataset: Intel Lab (10K sample of 2.3M), UCI Air Quality (827 clean),
  CIC-IoT-2023 (10K sample of 712K), SWaT A8 (10K sample)

### Dependent Variables / Metrics
- Wire size (bytes) per record per format (mean ± std)
- Compression ratio vs JSON-LD baseline (geometric mean)
- Frame-fit percentage per MTU threshold

### Control Conditions
- Same record content encoded in all formats
- Same annotation (Tier 1, COMPLIANT, 8-bit) across JSON-LD family
- Non-canonical cbor2 serialization for fair comparison

### Results
- Compression ratio vs JSON-LD:
  Intel Lab: 0.242, UCI AQ: 0.324, CIC-IoT: 0.486, SWaT: 0.572
- Frame-fit 802.15.4 (127B): CBOR-LD-ex 100% on Intel Lab, 0% all others
- Protobuf smallest overall: 0.179 (Intel Lab) — no on-wire semantics

### Observations
- jsonld-ex CBOR-LD NOT always < JSON-LD: CBOR float64 > JSON "0.0"
- SenML/JSON > JSON-LD for many-field datasets (per-record overhead)
- canonical=True gave CBOR-LD unfair advantage; fixed to non-canonical

### Artifacts
- Script: benchmarks/run_wire_size_experiment.py
- Output: papers/cborld-ex-main/tables/wire_sizes.{md,csv}
- Harness: benchmarks/cbor_ld_ex_benchmark/real_data.py
- Tests: tests/test_real_data.py (1175 tests)

---

## EXP-007: Software Decode Throughput (9 Formats × 4 Datasets)

**Date:** 2026-05-30
**Researcher:** Muntaser Syed
**Type:** computational
**Status:** planned

### Hypothesis
CBOR-LD-ex decode throughput is competitive with other CBOR-based formats
despite the additional annotation decoding step. JSON-LD (text parsing)
is the slowest. Protobuf is the fastest (minimal parsing overhead).

### Independent Variables
- Encoding format (9 formats)
- Dataset (4 datasets — determines record complexity)

### Dependent Variables / Metrics
- Decode throughput: records/second (mean ± std over multiple runs)
- Decode latency: microseconds/record

### Control Conditions
- Pre-encoded wire bytes (encode once, decode N times)
- Same hardware, same Python interpreter
- Warmup iterations excluded from measurement
- timeit with sufficient repetitions for stable measurement

### Protocol
1. For each dataset, take 100 representative records
2. Pre-encode each record in all 9 formats (store wire bytes)
3. For each format: timeit decode of 100 records × 100 iterations
4. Compute mean ± std decode time per record
5. Report as records/second and μs/record

### Results
- CBOR-LD-ex decode: 3.9 (Intel Lab) to 11.5 (SWaT) μs/record
  (259K to 87K records/sec)
- JSON-LD (json.loads): 2.5 to 9.6 μs — CPython text parsing is
  well-optimized; only 1.6× faster than CBOR-LD-ex
- jsonld-ex (cbor2.loads, no decompression): fastest at 2.0–6.1 μs
- Our manual Protobuf/MessagePack decoders are slowest (Python loops):
  30–33 μs for SWaT. Compiled C decoders would be much faster.
- All formats scale linearly with field count (SWaT 63 fields ≈ 4×
  Intel Lab 4 fields ≈ 4× latency)
- Std consistently < 1 μs across all formats — stable measurements

### Observations
- CBOR-LD-ex overhead vs plain CBOR-LD is ~1 μs (annotation bit-unpack)
  — negligible for IoT duty cycles (seconds between readings)
- Protobuf/FlatBuffers/MessagePack slowness is an artifact of our
  manual Python decoders — compiled implementations would be faster.
  Paper must note: "Python reference implementation; host-labeled."
- Decode is NOT the bottleneck for constrained IoT — radio TX time
  dominates (e.g., LoRaWAN SF12 airtime ~1.8s for 51B)

### Artifacts
- Script: benchmarks/run_decode_throughput.py
- Output: papers/cborld-ex-main/tables/decode_throughput.{md,csv}

---

## EXP-008: Temporal/Delta Streaming Overhead (Intel Lab)

**Date:** 2026-05-30
**Researcher:** Muntaser Syed
**Type:** computational
**Status:** planned

### Hypothesis
CBOR-LD-ex delta mode (PrecisionMode.DELTA_8) reduces per-reading wire
cost for successive readings from the same sensor. Over N readings from
the same mote at 31-second cadence, cumulative savings are proportional
to N.

### Independent Variables
- Encoding mode: full opinion (BITS_8) vs delta (DELTA_8)
- Stream length: N successive readings from same mote

### Dependent Variables / Metrics
- Per-reading wire size (bytes) in full vs delta mode
- Cumulative wire savings over N readings

### Protocol
1. Load Intel Lab data, filter to moteid=1, sort by epoch
2. Take first 100 consecutive readings
3. Encode reading 1 with full annotation (BITS_8)
4. Encode readings 2–100 with delta annotation (DELTA_8)
5. Compare total wire cost: all-full vs first-full + rest-delta

### Results
- Opinion payload: BITS_8 = 3 bytes, DELTA_8 = 2 bytes (1B saving)
- Per-reading saving: 1.0 byte (1.0% of total message)
- Stream saving over 100 readings: 99 bytes (1.0%)
- Zero fallbacks to full mode (opinion deltas fit int8 range)
- Data fields dominate wire cost (~100B per reading); opinion is
  only 3B of that. Delta saves 1B of the 3B opinion component.

### Observations
- Delta savings are modest (1.0%) because data fields are the
  dominant wire cost, not the opinion. The honest framing: delta
  mode is a micro-optimization for long-running streams, not a
  headline compression feature.
- Zero fallback confirms that adjacent readings from the same
  mote produce slowly-varying opinions (sliding-window Beta mapping
  changes by at most 1 evidence count per reading).
- Over 10,000 readings (5.5 hours at 31s cadence), savings would
  be ~10KB — meaningful for energy-constrained LoRaWAN devices
  where every byte costs radio airtime.

### Artifacts
- Script: benchmarks/run_temporal_delta.py
- Output: papers/cborld-ex-main/tables/temporal_delta.md

---

## EXP-009: End-to-End Tier 1→2→3 on Real Data

**Date:** 2026-05-30
**Researcher:** Muntaser Syed
**Type:** computational
**Status:** planned

### Hypothesis
The CBOR-LD-ex tiered architecture produces increasing wire sizes at
each tier (richer headers/metadata), but each tier's encoding remains
more compact than the equivalent JSON-LD representation.

### Independent Variables
- Tier level: Tier 1 (1B header), Tier 2 (4B header), Tier 3 (4B + ext)
- Dataset: Intel Lab slice

### Dependent Variables / Metrics
- Wire size per tier
- Header overhead per tier
- Compression ratio vs JSON-LD equivalent at each tier

### Protocol
1. Take 100 Intel Lab records
2. Encode each at Tier 1 (constrained device reading)
3. Simulate edge: Tier 2 header with cumulative fusion operator
4. Simulate cloud: Tier 3 header with provenance chain
5. Compare wire sizes across tiers and vs JSON-LD

### Results
- Tier 1: 101B CBOR-LD-ex vs 416B JSON-LD = 0.243 ratio
- Tier 2: 106B vs 451B = 0.235 ratio (header amortized over same data)
- Tier 3: 202B vs 587B = 0.344 ratio (96B provenance chain added)
- Annotation sizes: Tier 1 = 4B, Tier 2 = 9B, Tier 3 = 9B
- Provenance: 16B per entry, 6 entries (5 motes + 1 fusion) = 96B
- Fused opinion: b=0.750, d=0.211, u=0.039 (mote 3 dissent visible)

### Observations
- Mote 3 has inverted opinion (b=0.083, d=0.750) — its temperature
  readings are outside the compliance threshold. Cumulative fusion
  correctly weights this as minority dissent.
- Compression ratio improves from Tier 1 to Tier 2 (0.243 → 0.235)
  because the richer Tier 2 header (4B vs 1B) is offset by the JSON-LD
  equivalent growing proportionally more (annotation dict overhead).
- Tier 3 ratio increases (0.344) because provenance chain is a fixed
  cost that doesn't compress via context compression.

### Artifacts
- Script: benchmarks/run_tier_simulation.py
- Output: papers/cborld-ex-main/tables/tier_simulation.md
