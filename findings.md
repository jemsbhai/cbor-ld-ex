# Findings

## Curated Summary

### Wire-Size Compression

CBOR-LD-ex achieves the highest compression among self-describing formats
across both synthetic and real-world IoT datasets.

**Synthetic benchmark (EXP-001):** 38 scenarios across 4 document profiles
(minimal temperature through aggregate fleet), tiers 1–2, precisions 8/16/32.
Geometric mean compression: 79.8% vs JSON-LD. Bit efficiency: 75.7–96.8%
depending on precision mode (93.0% at 8-bit Tier 1, 95.5% at 8-bit Tier 2).

**Real-data benchmark (EXP-006):** 9 formats × 4 datasets (Intel Lab, UCI AQ,
CIC-IoT-2023, SWaT A8). CBOR-LD-ex compression ratio vs JSON-LD: 0.242
(Intel Lab), 0.324 (UCI AQ), 0.486 (CIC-IoT), 0.572 (SWaT A8).

**Frame-fit** is the killer practical result: CBOR-LD-ex is the ONLY
self-describing format that fits 802.15.4 (127B) and LoRaWAN SF10 (115B)
frames for Intel Lab records (100% fit rate). No other semantic format
achieves this.

Schema-rigid Protobuf is 18–51% of JSON-LD — smaller in raw bytes but
carries no on-wire semantics. The paper leads with the value triangle:
compact + semantically interoperable + reasoning-carrying.

### Quantization Correctness (EXP-002)

Constrained binomial quantization preserves b̂ + d̂ + û = 2^n − 1 exactly
(Theorem 1) at all precisions (8/16/32-bit). Symmetric clamping eliminates
belief bias (v0.4.0+). 968 tests confirm all invariants including delta
mode, multinomial extension, and _NORM_MAX_LOOKUP Rust-canonical values.

### Batch Compression (EXP-003)

**Honest negative finding:** Independent per-component quantization beats
RHT + Lloyd-Max on MSE at equal bit-width. RHT's value is compression
(fewer wire bits via coordinated encoding), NOT distortion improvement.
ρ ≈ 4.2 (spec §11.7). The paper frames this as "compression at
constraint-exact reconstruction" — not as distortion improvement.

Lloyd-Max MSE < uniform MSE at all bit-widths. Shannon R-D bound holds.

### Provenance (EXP-004)

Provenance chain adds bounded overhead per entry. CRC-8 integrity
verification correct. Chain tamper detection verified.

### Tier Pipeline (EXP-005)

Full Tier 1→2→3 pipeline (sensors → edge → cloud) preserves opinion
validity, quantization constraints, and provenance integrity. Byzantine
filtering successfully removes outlier sensor. Deterministic via seed.
MQTT and CoAP transport adapters produce identical payloads.

### Honest Limitations

- jsonld-ex CBOR-LD is NOT universally smaller than JSON-LD: CBOR float64
  (9B) exceeds JSON's compact number representation ("0.0" = 3B) for
  flag-heavy datasets (EXP-006).
- SenML/JSON is larger than JSON-LD for many-field datasets due to
  per-measurement record overhead.
- CIC-IoT (39 fields) and SWaT (63 fields) records exceed all constrained
  single-frame MTUs in every format — no protocol can fit these in one frame.
- README's "37× smaller" claim is annotation-only; full-message geometric
  mean is ~5× (79.8% compression). Paper must lead with full-message.
- Real SF12 (51B) feasibility: smallest CBOR-LD-ex message is 55B — exceeds
  SF12. Honest claim is per-record fit fraction, not universal fit.
- Decode throughput: Protobuf/FlatBuffers/MessagePack numbers are from
  manual Python decoders — compiled C implementations would be significantly
  faster. Paper must note "Python reference implementation; host-labeled."

### Decode Throughput (EXP-007)

CBOR-LD-ex decode latency is 3.9–11.5 μs/record (259K–87K records/sec),
competitive with JSON-LD (2.5–9.6 μs) despite additional annotation
bit-unpacking. The overhead vs plain CBOR-LD is ~1 μs — negligible for
IoT duty cycles where radio TX time dominates.

All formats scale linearly with field count. Decode is NOT the bottleneck
for constrained IoT applications.

### Temporal/Delta Streaming (EXP-008)

Delta mode (DELTA_8) saves 1 byte per reading (3B → 2B opinion payload),
a 1.0% total stream saving over 100 consecutive Intel Lab readings from
mote 1. Data fields (~100B) dominate wire cost; the opinion component
is only 3B. Zero fallbacks to full mode — adjacent readings produce
slowly-varying opinions via sliding-window Beta mapping.

Honest framing: delta is a micro-optimization for long-running streams,
not a headline compression feature. Over 10K readings (5.5 hours at
31s cadence), savings are ~10KB — meaningful for energy-constrained
LoRaWAN devices.

### Tier Pipeline on Real Data (EXP-009)

Full Tier 1→2→3 pipeline on real Intel Lab data (5 motes, 10 readings
each). Compression ratios vs JSON-LD: Tier 1 = 0.243, Tier 2 = 0.235
(improves due to header amortization), Tier 3 = 0.344 (provenance chain
adds 96B fixed cost). Mote 3’s out-of-threshold readings produce an
inverted opinion (b=0.083, d=0.750), correctly weighted as minority
dissent by cumulative fusion (fused b=0.750, d=0.211, u=0.039).

---

## Raw Findings Log

### 2026-05-30 -- EXP-009: Tier 1→2→3 on real Intel Lab data

**Key result:** Tier 1 = 0.243, Tier 2 = 0.235, Tier 3 = 0.344 vs JSON-LD.
Provenance chain: 96B (6 entries × 16B). Mote 3 dissent correctly fused.

### 2026-05-30 -- EXP-008: Temporal/delta streaming (Intel Lab mote 1)

**Key result:** Delta saves 1B/reading (1.0% total). 99B over 100 readings.
Zero fallbacks. Data fields dominate; opinion is 3B of ~100B message.

### 2026-05-30 -- EXP-007: Decode throughput (9 formats × 4 datasets)

**Key result:** CBOR-LD-ex decode: 3.9–11.5 μs/record (259K–87K rec/s).
JSON-LD: 2.5–9.6 μs (only 1.6× faster). Overhead vs CBOR-LD: ~1 μs.

**Details:** 100 records × 100 iterations × 5 repeats, 3 warmup excluded.
All formats scale linearly with field count. Manual Python Protobuf/
MsgPack decoders are slowest (30–33 μs for SWaT 63-field records).
Compiled C would be faster. See `papers/cborld-ex-main/tables/decode_throughput.md`.

### 2026-05-30 -- EXP-006: Wire-size experiment (9 formats × 4 real datasets)

**Key result:** CBOR-LD-ex compression ratio vs JSON-LD: 0.242 (Intel Lab),
0.324 (UCI AQ), 0.486 (CIC-IoT), 0.572 (SWaT A8).

**Details:** See `papers/cborld-ex-main/tables/wire_sizes.md`. 10K-record
samples per dataset (full for UCI AQ at 827 clean records). Protobuf beats
all on raw size (0.179–0.511) but lacks on-wire semantics.

Frame-fit: CBOR-LD-ex 100% at 802.15.4 and LoRaWAN SF10 for Intel Lab.
No other self-describing format fits. CIC-IoT and SWaT: 0% fit for all
formats at all MTUs (too many fields per record).

SenML/JSON > JSON-LD for many-field datasets (CIC-IoT ratio 1.114).

### Pre-2026-05-30 -- EXP-001: 38-scenario synthetic benchmark

**Key result:** Geometric mean compression vs JSON-LD: 79.8%. Bit efficiency
93.0% (Tier 1 8-bit), 95.5% (Tier 2 8-bit). Smallest message: 55B.

### Pre-2026-05-30 -- EXP-003: Batch RD + ablation

**Key result:** Independent quantization beats RHT+LM on MSE. ρ ≈ 4.2.
RHT value is compression, not distortion. Enshrined as honest invariant.

### Pre-2026-05-30 -- EXP-005: Tier 1→2→3 simulation

**Key result:** Full pipeline verified. Byzantine filter removes outlier.
Provenance chain intact. Transport-agnostic (MQTT = CoAP payloads).
