# Experimental Logbook — CBOR-LD-ex Paper #1 (IEEE WF-IoT)

---

## EXP-000: Wire-Size Experiment (9 formats × 4 datasets)

**Date:** 2026-05-30
**Researcher:** Muntaser Syed
**Type:** computational
**Status:** completed

### Hypothesis
CBOR-LD-ex produces the smallest wire representation among self-describing
formats for real IoT sensor records, due to context compression (integer keys)
and bit-packed annotation encoding.

### Independent Variables
- Encoding format: JSON-LD, jsonld-ex CBOR-LD, CBOR-LD, CBOR-LD-ex,
  SenML/JSON, SenML/CBOR, Protobuf, FlatBuffers, MessagePack
- Dataset: Intel Lab, UCI Air Quality, CIC-IoT-2023, SWaT A8

### Dependent Variables / Metrics
- Wire size (bytes) per record per format
- Compression ratio vs JSON-LD baseline (geometric mean)
- Frame-fit percentage per MTU threshold

### Control Conditions
- Same record content encoded in all formats
- Same annotation (Tier 1, COMPLIANT, 8-bit opinion) across JSON-LD family
- Non-canonical cbor2 serialization for fair CBOR-LD vs CBOR-LD-ex comparison

### Results
- Compression ratio vs JSON-LD: 0.242 (Intel Lab), 0.324 (UCI AQ),
  0.486 (CIC-IoT), 0.572 (SWaT A8)
- Frame-fit 802.15.4 (127B): CBOR-LD-ex 100% on Intel Lab, 0% for all
  other self-describing formats
- Full tables: papers/cborld-ex-main/tables/wire_sizes.{md,csv}

### Observations
- jsonld-ex CBOR-LD is NOT always smaller than JSON-LD. CBOR float64 (9B)
  exceeds JSON's "0.0" (3B) on flag-heavy datasets (CIC-IoT, SWaT).
- SenML/JSON is larger than JSON-LD for many-field datasets — per-measurement
  record overhead (repeated "n", "v", "u" keys) dominates.
- canonical=True in cbor2 gave CBOR-LD unfair advantage via float16 encoding;
  fixed to non-canonical for fair comparison.

### Artifacts
- Script: benchmarks/run_wire_size_experiment.py
- Output: papers/cborld-ex-main/tables/wire_sizes.{md,csv}
- Tests: tests/test_real_data.py (1175 tests including harness + runner)

---

## EXP-001: Software Decode Throughput (9 formats × 4 datasets)

**Date:** 2026-05-30
**Researcher:** Muntaser Syed
**Type:** computational
**Status:** planned

### Hypothesis
CBOR-LD-ex decode throughput is competitive with other CBOR-based formats
despite the additional annotation decoding step. JSON-LD (text parsing) is
the slowest. Protobuf is the fastest (minimal parsing overhead). CBOR-based
formats (CBOR-LD, CBOR-LD-ex, SenML/CBOR) form a middle tier.

### Independent Variables
- Encoding format (9 formats)
- Dataset (4 datasets — determines record complexity)

### Dependent Variables / Metrics
- Decode throughput: records/second (mean ± std over multiple runs)
- Decode latency: microseconds/record

### Control Conditions
- Same pre-encoded wire bytes (encode once, decode N times)
- Same hardware (user's laptop: 64GB RAM, 4090 GPU — CPU-bound test)
- Same Python interpreter (poetry run)
- Warmup iterations excluded from measurement
- timeit with sufficient repetitions for stable measurement

### Protocol
1. For each dataset, take 100 representative records
2. Pre-encode each record in all 9 formats (store wire bytes)
3. For each format: timeit decode of all 100 records × 100 iterations
4. Compute mean ± std decode time per record
5. Report as records/second and μs/record

### Environment
- Hardware: Windows laptop, 64GB RAM, RTX 4090 (CPU-bound)
- Software: Python 3.x, Poetry, cbor2, jsonld-ex
- Git commit: [to be filled]
- Config: inline in script (no separate config file)

### Results
[To be filled after execution]

### Artifacts
- Script: benchmarks/run_decode_throughput.py
- Output: papers/cborld-ex-main/tables/decode_throughput.{md,csv}

---

## EXP-002: Temporal/Delta Streaming Overhead (Intel Lab)

**Date:** 2026-05-30
**Researcher:** Muntaser Syed
**Type:** computational
**Status:** planned

### Hypothesis
CBOR-LD-ex delta mode (PrecisionMode.DELTA_8) reduces per-reading wire cost
for successive readings from the same sensor, because only the opinion delta
(2 bytes) is transmitted instead of the full opinion (3 bytes). Over a stream
of N readings from the same mote at 31-second cadence, cumulative savings are
proportional to N.

### Independent Variables
- Encoding mode: full opinion (BITS_8) vs delta (DELTA_8)
- Stream length: N successive readings from same mote

### Dependent Variables / Metrics
- Per-reading wire size (bytes) in full vs delta mode
- Cumulative wire savings over N readings
- Overhead of first full reading (baseline cost)

### Control Conditions
- Same mote, consecutive timestamps (Intel Lab, 31s cadence)
- Same context registry and data fields
- Delta is opinion-only; data fields always fully encoded

### Protocol
1. Load Intel Lab data, filter to a single mote (e.g., moteid=1)
2. Sort by epoch, take first 100 consecutive readings
3. Encode reading 1 with full Tier 1 annotation (BITS_8)
4. Encode readings 2–100 with delta annotation (DELTA_8)
5. Compare total wire cost: all-full vs first-full + rest-delta
6. Report per-reading savings and cumulative savings

### Results
[To be filled after execution]

---

## EXP-003: End-to-End Tier 1→2→3 Simulation

**Date:** 2026-05-30
**Researcher:** Muntaser Syed
**Type:** computational
**Status:** planned

### Hypothesis
The CBOR-LD-ex tiered architecture (constrained→edge→cloud) produces
increasing wire sizes at each tier due to richer headers and metadata,
but each tier's encoding remains more compact than the equivalent
JSON-LD representation at that tier.

### Independent Variables
- Tier level: Tier 1 (constrained, 1B header), Tier 2 (edge, 4B header),
  Tier 3 (cloud, 4B + extensions)
- Dataset: Intel Lab (representative constrained-device scenario)

### Dependent Variables / Metrics
- Wire size per tier
- Header overhead per tier
- Compression ratio vs JSON-LD equivalent at each tier

### Control Conditions
- Same sensor data at all tiers
- Tier 2 adds: operator_id, reasoning_context, source_count
- Tier 3 adds: provenance chain, extended context

### Protocol
1. Take 100 Intel Lab records
2. Encode each at Tier 1 (constrained device reading)
3. Simulate edge aggregation: Tier 2 header with cumulative fusion operator
4. Simulate cloud ingest: Tier 3 header with provenance chain
5. Compare wire sizes across tiers
6. Compare each tier vs JSON-LD equivalent

### Results
[To be filled after execution]
