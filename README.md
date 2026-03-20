# CBOR-LD-ex

**Compact Binary Linked Data with Semantic Reasoning for Constrained IoT Networks**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)

CBOR-LD-ex extends [CBOR-LD](https://json-ld.github.io/cbor-ld-spec/) with bit-packed [Subjective Logic](https://en.wikipedia.org/wiki/Subjective_logic) primitives — compliance status, opinion tuples, operator provenance, and tiered reasoning metadata — enabling edge IoT devices to exchange semantically-rich compliance annotations at a fraction of the cost of JSON-LD.

Built on [jsonld-ex](https://pypi.org/project/jsonld-ex/) and its compliance algebra (Syed et al. 2026).

## Key Properties

- **5-byte semantic annotations** on constrained devices (vs ~280 bytes in JSON-LD)
- **Tiered encoding** adapts to device capability: 1-byte headers on MCUs, 4-byte+ on gateways/cloud
- **Three formal axioms** — backward compatibility, algebraic closure, quantization correctness
- **Constrained quantization** preserves `b + d + u = 1` exactly in the compact representation
- **8 theorems proven** bounding quantization error, operator propagation, and constraint preservation

## Installation

```bash
pip install cbor-ld-ex
```

Or with [Poetry](https://python-poetry.org/):

```bash
poetry add cbor-ld-ex
```

### Optional: CoAP transport

```bash
pip install cbor-ld-ex[transport]
```

## Quick Start

```python
from cbor_ld_ex.opinions import quantize_binomial, dequantize_binomial
from cbor_ld_ex.headers import (
    Tier1Header, ComplianceStatus, PrecisionMode
)
from cbor_ld_ex.annotations import Annotation, encode_annotation, wrap_cbor_tag

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

# Encode: 5 bytes total (1 header + 4 opinion)
wire_bytes = encode_annotation(ann)
assert len(wire_bytes) == 5

# Wrap in CBOR tag for interoperability
tagged = wrap_cbor_tag(wire_bytes)
```

## Architecture

```
Tier 1 (Constrained)     Tier 2 (Edge Gateway)      Tier 3 (Cloud)
┌─────────────────┐      ┌─────────────────────┐    ┌──────────────────────┐
│ 1-byte header   │      │ 4-byte header        │    │ 4-byte + extensions  │
│ 4-byte opinion  │─────>│ Fused opinion         │──>│ Provenance chain     │
│ = 5 bytes total │      │ Operator provenance   │    │ Full audit trail     │
│                 │      │ Source count           │    │ Byzantine metadata   │
└─────────────────┘      └─────────────────────┘    └──────────────────────┘
     ~85% smaller              Fusion + filtering         Full reasoning
     than JSON-LD              at the edge                 reconstruction
```

## Formal Guarantees

| Axiom | Property | Guarantee |
|-------|----------|-----------|
| **Axiom 1** | Backward Compatibility | Strip annotations → valid CBOR-LD → valid JSON-LD |
| **Axiom 2** | Algebraic Closure | Every operator produces valid annotations |
| **Axiom 3** | Quantization Correctness | `b̂ + d̂ + û = 2ⁿ − 1` exactly |

| Precision | Max error (b,d) | Max error (u) | Opinion bytes |
|-----------|----------------|---------------|---------------|
| 8-bit     | ~0.002         | ~0.004        | 4             |
| 16-bit    | ~0.000008      | ~0.000015     | 8             |
| 32-bit    | IEEE 754       | IEEE 754      | 16            |

## Development

```bash
git clone https://github.com/jemsbhai/cbor-ld-ex.git
cd cbor-ld-ex
poetry install
poetry run python -m pytest tests/ -v
```

### TDD Methodology

This project follows strict test-driven development. Every module has comprehensive tests including property-based testing via [Hypothesis](https://hypothesis.readthedocs.io/).

## Project Structure

```
cborldex/
├── src/cbor_ld_ex/
│   ├── opinions.py       # Quantization codec (Theorems 1–3)
│   ├── headers.py        # Tier-dependent header codec (§5)
│   └── annotations.py    # Annotation assembly + CBOR tagging (§5.3)
├── tests/
│   ├── test_opinions.py  # 38 tests incl. Hypothesis property tests
│   ├── test_headers.py   # Tier 1/2/3 header roundtrips
│   └── test_annotations.py  # Assembly + Axiom 1 verification
├── spec/
│   ├── FORMAL_MODEL.md   # Formal specification v0.1.0-draft
│   └── IMPLEMENTATION_PLAN.md
├── pyproject.toml
└── LICENSE
```

## References

- Syed, M., Silaghi, M., Abujar, S., and Alssadi, R. (2026). *A Compliance Algebra: Modeling Regulatory Uncertainty with Subjective Logic.* Working paper.
- Jøsang, A. (2016). *Subjective Logic: A Formalism for Reasoning Under Uncertainty.* Springer.
- Bormann, C. and Hoffman, P. (2020). [RFC 8949: CBOR.](https://www.rfc-editor.org/rfc/rfc8949) IETF.
- Shelby, Z. et al. (2014). [RFC 7252: CoAP.](https://www.rfc-editor.org/rfc/rfc7252) IETF.
- [CBOR-LD Specification](https://json-ld.github.io/cbor-ld-spec/) (W3C Community Group Draft).
- [JSON-LD 1.1](https://www.w3.org/TR/json-ld11/) (W3C Recommendation).

## License

MIT — see [LICENSE](LICENSE).
