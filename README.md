# CBOR-LD-ex

**Compact Binary Linked Data with Semantic Reasoning for Constrained IoT Networks**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![PyPI version](https://img.shields.io/pypi/v/cbor-ld-ex.svg)](https://pypi.org/project/cbor-ld-ex/)

CBOR-LD-ex extends [CBOR-LD](https://json-ld.github.io/cbor-ld-spec/) with bit-packed [Subjective Logic](https://en.wikipedia.org/wiki/Subjective_logic) primitives — compliance status, opinion tuples, operator provenance, and tiered reasoning metadata — enabling edge IoT devices to exchange semantically-rich compliance annotations at a fraction of the cost of JSON-LD.

Built on [jsonld-ex](https://pypi.org/project/jsonld-ex/) and its compliance algebra (Syed et al. 2026).

## Key Properties

- **4-byte semantic annotations** on constrained devices (1-byte header + 3-byte opinion)
- **37× smaller** than JSON-LD, **>10× smaller** than standard CBOR-LD for the same semantic content
- **93% bit efficiency** — almost every wire bit carries Shannon information
- **Tiered encoding** adapts to device capability: 1-byte headers on MCUs, 4-byte+ on gateways/cloud
- **Three formal axioms** — backward compatibility, algebraic closure, quantization correctness
- **Constrained quantization** preserves `b + d + u = 1` exactly — û is derived, never transmitted
- **146 tests** including exhaustive 8-bit verification (32,896 pairs) and Hypothesis property tests

## Compression Benchmark

| Encoding | Annotation size | Bit efficiency | vs JSON-LD |
|---|---|---|---|
| JSON-LD (verbose text) | ~148 bytes | ~2.5% | baseline |
| CBOR-LD (integer keys, best effort) | ~49 bytes | ~7.6% | 3× |
| **CBOR-LD-ex (bit-packed)** | **4 bytes** | **93.0%** | **37×** |

The key insight: the SL constraint `b + d + u = 1` means û carries zero bits of Shannon information. CBOR-LD-ex transmits only 3 values (b̂, d̂, â) and derives û on decode. Combined with bit-packed headers and integer term IDs, this achieves >10× compression over CBOR-LD for the same semantic content.

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

## Architecture

```
Tier 1 (Constrained)     Tier 2 (Edge Gateway)      Tier 3 (Cloud)
┌─────────────────┐      ┌─────────────────────┐    ┌──────────────────────┐
│ 1-byte header   │      │ 4-byte header        │    │ 4-byte + extensions  │
│ 3-byte opinion  │─────>│ Fused opinion         │──>│ Provenance chain     │
│ = 4 bytes total │      │ Operator provenance   │    │ Full audit trail     │
│                 │      │ Source count           │    │ Byzantine metadata   │
└─────────────────┘      └─────────────────────┘    └──────────────────────┘
     ~85% smaller              Fusion + filtering         Full reasoning
     than JSON-LD              at the edge                 reconstruction
```

## Formal Guarantees

| Axiom | Property | Guarantee |
|-------|----------|-----------|
| **Axiom 1** | Backward Compatibility | Strip annotations → valid CBOR-LD → valid JSON-LD |
| **Axiom 2** | Algebraic Closure | Every SL operator produces valid annotations |
| **Axiom 3** | Quantization Correctness | `b̂ + d̂ + û = 2ⁿ − 1` exactly |

All three axioms are verified by cross-cutting property tests (19 tests in `test_axioms.py`) including exhaustive enumeration of all 32,896 valid 8-bit opinion pairs.

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
| `annotations.py` | Annotation assembly + CBOR Tag(60000) wrapping |
| `codec.py` | Full encode/decode pipeline, `ContextRegistry`, `payload_comparison`, Shannon bit analysis |

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
│   ├── annotations.py    # Annotation assembly + CBOR tagging (§5.3)
│   └── codec.py          # Full codec, ContextRegistry, bit-level analysis (§11)
├── tests/
│   ├── test_opinions.py  # 38 tests — quantization roundtrips, Hypothesis properties
│   ├── test_headers.py   # 28 tests — Tier 1/2/3 header encoding/decoding
│   ├── test_annotations.py  # 15 tests — assembly, CBOR tag, wire format
│   ├── test_codec.py     # 46 tests — full pipeline, payload comparison, bit analysis
│   └── test_axioms.py    # 19 tests — cross-cutting axiom verification
├── spec/
│   ├── FORMAL_MODEL.md   # Formal specification v0.2.0-draft
│   └── IMPLEMENTATION_PLAN.md
├── pyproject.toml
└── LICENSE
```

## References

- Syed, M., Silaghi, M., Abujar, S., and Alssadi, R. (2026). *A Compliance Algebra: Modeling Regulatory Uncertainty with Subjective Logic.* Working paper.
- Jøsang, A. (2016). *Subjective Logic: A Formalism for Reasoning Under Uncertainty.* Springer.
- Shannon, C.E. (1948). *A Mathematical Theory of Communication.* Bell System Technical Journal.
- Bormann, C. and Hoffman, P. (2020). [RFC 8949: CBOR.](https://www.rfc-editor.org/rfc/rfc8949) IETF.
- Shelby, Z. et al. (2014). [RFC 7252: CoAP.](https://www.rfc-editor.org/rfc/rfc7252) IETF.
- [CBOR-LD Specification](https://json-ld.github.io/cbor-ld-spec/) (W3C Community Group Draft).
- [JSON-LD 1.1](https://www.w3.org/TR/json-ld11/) (W3C Recommendation).

## License

MIT — see [LICENSE](LICENSE).
