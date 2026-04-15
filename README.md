# CBOR-LD-ex

**Compact Binary Linked Data with Semantic Reasoning for Constrained IoT Networks**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![PyPI version](https://img.shields.io/pypi/v/cbor-ld-ex.svg)](https://pypi.org/project/cbor-ld-ex/)

CBOR-LD-ex extends [CBOR-LD](https://json-ld.github.io/cbor-ld-spec/) with bit-packed [Subjective Logic](https://en.wikipedia.org/wiki/Subjective_logic) primitives — compliance status, opinion tuples, operator provenance, temporal decay, and security metadata — enabling edge IoT devices to exchange semantically-rich compliance annotations at a fraction of the cost of JSON-LD.

Built on [jsonld-ex](https://pypi.org/project/jsonld-ex/) and its compliance algebra (Syed et al. 2026).

## Key Properties

- **4-byte semantic annotations** on constrained devices (1-byte header + 3-byte opinion)
- **37× smaller** than JSON-LD, **>10× smaller** than standard CBOR-LD for the same semantic content
- **93% bit efficiency** — almost every wire bit carries Shannon information
- **Tiered encoding** adapts to device capability: 1-byte headers on MCUs, 4-byte+ on gateways/cloud
- **Temporal extensions** — bit-packed decay metadata, log-scale half-life (1s to 388 days in 8 bits), compliance triggers
- **Security primitives** — annotation digests, Byzantine fusion metadata, chained provenance (16 bytes per entry, zero waste)
- **Transport-agnostic** — identical payloads over MQTT and CoAP
- **Three formal axioms** — backward compatibility, algebraic closure, quantization correctness
- **820 tests** including exhaustive 8-bit verification (32,896 pairs), Hypothesis property tests, and batch compression verification

## 6-Way Encoding Benchmark

CBOR-LD-ex is not just smaller — it carries **more semantic information** in **fewer bytes** than any alternative encoding.

| # | Encoding | Payload size | Annotation overhead | Semantic fields |
|---|---|---|---|---|
| 1 | JSON-LD (raw text) | ~280 bytes | ~148 bytes | data + verbose annotation |
| 2 | jsonld-ex CBOR-LD (context-only compression) | ~85 bytes | 0 | data only |
| 3 | Our CBOR-LD (full key+value compression) | ~22 bytes | 0 | data only |
| 4 | jsonld-ex CBOR-LD + annotation | ~210 bytes | ~125 bytes | data + annotation as JSON |
| 5 | Our CBOR-LD + standard CBOR annotation | ~70 bytes | ~49 bytes | data + CBOR k/v annotation |
| 6 | **CBOR-LD-ex (bit-packed)** | **~30 bytes** | **4 bytes** | **data + compliance + opinion + provenance** |

**Key findings:**
- CBOR-LD-ex annotation: **4 bytes** vs CBOR-LD's **49 bytes** for the same semantic content (>10× smaller)
- Our `ContextRegistry` (full key+value compression) beats jsonld-ex's context-only compression
- CBOR-LD-ex is the **only** encoding that fits compliance + opinion in a single 802.15.4 frame (127 bytes)
- Annotation bit efficiency: **93%** (Shannon information / wire bits)

Run `cbor_ld_ex.transport.full_benchmark()` to reproduce these numbers for your own documents.

## Installation

```bash
pip install cbor-ld-ex
```

Or with [Poetry](https://python-poetry.org/):

```bash
poetry add cbor-ld-ex
```

## Quick Start

```python
from cbor_ld_ex.opinions import quantize_binomial
from cbor_ld_ex.headers import Tier1Header, ComplianceStatus, PrecisionMode
from cbor_ld_ex.annotations import Annotation, encode_annotation
from cbor_ld_ex.codec import encode, decode, ContextRegistry

# Quantize an opinion: 85% belief, 5% disbelief, 10% uncertainty
b_q, d_q, u_q, a_q = quantize_binomial(0.85, 0.05, 0.10, 0.50, precision=8)
# (217, 13, 25, 128) — SL constraint preserved exactly: 217 + 13 + 25 = 255

# Build a Tier 1 annotation (constrained device)
header = Tier1Header(
    compliance_status=ComplianceStatus.COMPLIANT,
    delegation_flag=False,
    has_opinion=True,
    precision_mode=PrecisionMode.BITS_8,
)
ann = Annotation(header=header, opinion=(b_q, d_q, u_q, a_q))

# Encode annotation: 4 bytes total (1 header + 3 opinion; û not on wire)
wire_bytes = encode_annotation(ann)
assert len(wire_bytes) == 4

# Full CBOR-LD-ex message with context compression
registry = ContextRegistry(
    key_map={"@context": 0, "@type": 1, "value": 2, "unit": 3},
    value_map={"https://schema.org/": 100, "Observation": 101, "celsius": 102},
)
doc = {
    "@context": "https://schema.org/",
    "@type": "Observation",
    "value": 22.5,
    "unit": "celsius",
}
cbor_ld_ex_bytes = encode(doc, ann, context_registry=registry)

# Decode round-trip
recovered_doc, recovered_ann = decode(cbor_ld_ex_bytes, context_registry=registry)
assert recovered_doc["value"] == 22.5
assert recovered_ann.opinion[:3] == (217, 13, 25)  # b̂, d̂, û (û derived)
```

### Transport (MQTT / CoAP)

```python
from cbor_ld_ex.transport import (
    to_mqtt_payload, from_mqtt_payload, derive_topic, derive_qos,
    to_coap_payload, from_coap_payload,
)

# MQTT — same CBOR-LD-ex payload + protocol metadata
payload = to_mqtt_payload(doc, ann, context_registry=registry)
topic = derive_topic(doc, ann)         # "cbor-ld-ex/Observation/temp-042/compliant"
qos = derive_qos(doc, ann)            # 2 (high confidence → exactly-once)

# CoAP — identical payload, different transport
coap_payload = to_coap_payload(doc, ann, context_registry=registry)
assert coap_payload == payload         # Transport-agnostic encoding
```

### Temporal Decay

```python
from cbor_ld_ex.temporal import (
    TemporalBlock, ExtensionBlock, encode_half_life, decode_half_life,
    compute_decay_factor, apply_decay_quantized, DECAY_EXPONENTIAL,
)

# Encode a 1-hour half-life in 8 bits (log-scale, ~7% granularity)
encoded = encode_half_life(3600.0)
decoded = decode_half_life(encoded)  # ≈ 3600 seconds

# Apply decay to a quantized opinion (dequantize → decay → re-quantize)
factor = compute_decay_factor(DECAY_EXPONENTIAL, half_life=3600.0, elapsed=3600.0)
# factor ≈ 0.5 (one half-life elapsed)
b2, d2, u2, a2 = apply_decay_quantized(*ann.opinion, factor, precision=8)
assert b2 + d2 + u2 == 255  # Axiom 3 preserved through decay
```

### Security

```python
from cbor_ld_ex.security import (
    compute_annotation_digest, verify_annotation_digest,
    ProvenanceEntry, CHAIN_ORIGIN_SENTINEL,
    encode_provenance_entry, verify_provenance_chain, compute_entry_digest,
)

# Annotation digest (truncated SHA-256, 8 bytes)
digest = compute_annotation_digest(wire_bytes)
assert verify_annotation_digest(wire_bytes, digest)

# Provenance chain entry (16 bytes, 128 bits, zero waste)
entry = ProvenanceEntry(
    origin_tier=0, operator_id=0, precision_mode=0,
    b_q=217, d_q=13, a_q=128,
    timestamp=1710230400,
    prev_digest=CHAIN_ORIGIN_SENTINEL,
)
entry_bytes = encode_provenance_entry(entry)
assert len(entry_bytes) == 16  # Every bit carries information
```

### Batch Compression (§4.8)

```python
from cbor_ld_ex.batch import encode_batch, decode_batch

# 32 opinions from edge sensors
opinions = [
    (0.7, 0.1, 0.2, 0.5),  # (belief, disbelief, uncertainty, base_rate)
    (0.3, 0.4, 0.3, 0.5),
    # ... 30 more opinions
] * 16  # 32 total

# Encode: RHT + Lloyd-Max quantization at 3 bits/coordinate
wire = encode_batch(opinions, bits=3, quantizer='lloyd_max')
# Wire format: seed_mode(4) + norm_q(2) + packed_coords
# MSB of seed_mode = 1 (Lloyd-Max mode flag, self-describing)

# Decode: auto-detects quantizer mode from wire
recovered = decode_batch(wire, n_opinions=32, bits=3)
# Each opinion satisfies b+d+u=1 exactly (simplex projection)
# and a ∈ [0,1] (base rate clamping)

# ~50% smaller than individual 8-bit encoding for N=32
print(f"Wire: {len(wire)} bytes vs individual: {32*3} bytes")
```

## Architecture

```
Tier 1 (Constrained)     Tier 2 (Edge Gateway)      Tier 3 (Cloud)
┌─────────────────┐      ┌─────────────────────┐    ┌──────────────────────┐
│ 1-byte header   │      │ 4-byte header        │    │ 4-byte + extensions  │
│ 3-byte opinion  │─────>│ Fused opinion         │──>│ Provenance chain     │
│ = 4 bytes total │ MQTT │ Operator provenance   │    │ Full audit trail     │
│                 │ CoAP │ Byzantine filtering   │    │ Chained digests      │
└─────────────────┘      └─────────────────────┘    └──────────────────────┘
     ~85% smaller              Temporal decay             Full reasoning
     than JSON-LD              + spatial fusion            reconstruction
```

## Formal Guarantees

| Axiom | Property | Guarantee |
|-------|----------|-----------|
| **Axiom 1** | Backward Compatibility | Strip annotations → valid CBOR-LD → valid JSON-LD |
| **Axiom 2** | Algebraic Closure | Every SL operator produces valid annotations |
| **Axiom 3** | Quantization Correctness | `b̂ + d̂ + û = 2ⁿ − 1` exactly |

All three axioms are verified by cross-cutting property tests including exhaustive enumeration of all 32,896 valid 8-bit opinion pairs, and Hypothesis property tests through all operators (fusion, meet, decay).

| Precision | Max error (b,d) | Max error (u) | Wire bytes |
|-----------|----------------|---------------|------------|
| 8-bit     | ~0.002         | ~0.004        | 3          |
| 16-bit    | ~0.000008      | ~0.000015     | 6          |
| 32-bit    | IEEE 754       | IEEE 754      | 12         |

## API Overview

| Module | Purpose |
|--------|---------|
| `opinions.py` | Constrained quantization codec (Theorems 1–3) |
| `headers.py` | Tier-dependent header encoding/decoding (§5) |
| `annotations.py` | Annotation assembly + CBOR Tag(60000) + extensions |
| `temporal.py` | Bit-packed decay, log-scale half-life, expiry/review triggers |
| `security.py` | Annotation digests, Byzantine metadata, provenance chains |
| `codec.py` | Full encode/decode pipeline, `ContextRegistry`, Shannon bit analysis |
| `batch.py` | Batch compression — RHT + Lloyd-Max quantization (§4.8) |
| `transport.py` | MQTT + CoAP adapters, 6-way `full_benchmark()` engine |

## Development

```bash
git clone https://github.com/jemsbhai/cbor-ld-ex.git
cd cbor-ld-ex
poetry install
poetry run python -m pytest tests/ -v
```

### TDD Methodology

This project follows strict test-driven development. Every module has comprehensive tests including property-based testing via [Hypothesis](https://hypothesis.readthedocs.io/). Tests are never weakened to pass — the code or design is fixed instead.

## Project Structure

```
cborldex/
├── src/cbor_ld_ex/
│   ├── opinions.py       # Quantization codec (Theorems 1–3)
│   ├── headers.py        # Tier-dependent header codec (§5)
│   ├── annotations.py    # Annotation assembly + CBOR tagging + extensions
│   ├── temporal.py       # Temporal extensions — decay, triggers, BitWriter/BitReader
│   ├── security.py       # Digests, Byzantine metadata, provenance chains
│   ├── codec.py          # Full codec, ContextRegistry, bit-level analysis
│   ├── batch.py          # Batch compression — RHT, Lloyd-Max, Shannon analysis
│   └── transport.py      # MQTT + CoAP adapters, 6-way benchmark engine
├── tests/
│   ├── test_opinions.py      # 38 tests — quantization, Hypothesis properties
│   ├── test_headers.py       # 28 tests — Tier 1/2/3 header roundtrips
│   ├── test_annotations.py   # 15 tests — assembly, CBOR tag, wire format
│   ├── test_temporal.py      # 65 tests — bit-packed extensions, decay, triggers
│   ├── test_security.py      # 33 tests — digests, Byzantine, provenance chains
│   ├── test_codec.py         # 46 tests — full pipeline, payload comparison
│   ├── test_axioms.py        # 19 tests — cross-cutting axiom verification
│   ├── test_batch.py         # 253 tests — RHT, Lloyd-Max, batch pipeline, Shannon
│   └── test_transport.py     # 24 tests — MQTT, CoAP, 6-way benchmark
├── spec/
│   ├── FORMAL_MODEL.md       # Formal specification v0.4.5-draft
│   └── IMPLEMENTATION_PLAN.md
├── pyproject.toml
└── LICENSE
```

**820 tests total**, all passing.

## References

- Syed, M., Silaghi, M., Abujar, S., and Alssadi, R. (2026). *A Compliance Algebra: Modeling Regulatory Uncertainty with Subjective Logic.* Working paper.
- Jøsang, A. (2016). *Subjective Logic: A Formalism for Reasoning Under Uncertainty.* Springer.
- Shannon, C.E. (1948). *A Mathematical Theory of Communication.* Bell System Technical Journal.
- Bormann, C. and Hoffman, P. (2020). [RFC 8949: CBOR.](https://www.rfc-editor.org/rfc/rfc8949) IETF.
- Shelby, Z. et al. (2014). [RFC 7252: CoAP.](https://www.rfc-editor.org/rfc/rfc7252) IETF.
- Selander, G. et al. (2019). [RFC 8613: OSCORE.](https://www.rfc-editor.org/rfc/rfc8613) IETF.
- [CBOR-LD Specification](https://json-ld.github.io/cbor-ld-spec/) (W3C Community Group Draft).
- [JSON-LD 1.1](https://www.w3.org/TR/json-ld11/) (W3C Recommendation).

- Han, S. et al. (2025). [TurboQuant: Online Vector Quantization.](https://arxiv.org/abs/2504.19874) ICLR 2026.
- Han, S. et al. (2025). [PolarQuant: Polar Coordinate Quantization.](https://arxiv.org/abs/2502.02617) AISTATS 2026.
- Lloyd, S. (1982). Least squares quantization in PCM. *IEEE Trans. Information Theory.*
- Max, J. (1960). Quantizing for minimum distortion. *IEEE Trans. Information Theory.*
- Ailon, N. and Chazelle, B. (2009). The fast Johnson-Lindenstrauss transform. *SIAM J. Computing.*
- Duchi, J. et al. (2008). Efficient projections onto the l1-ball. *ICML 2008.*

## License

MIT — see [LICENSE](LICENSE).
