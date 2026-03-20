# CBOR-LD-ex: Formal Data Model Specification

**Version:** 0.2.0-draft  
**Date:** 2026-03-20  
**Authors:** Muntaser Syed  
**Status:** Working Draft — Iterative Development  
**Parent Project:** jsonld-ex (https://pypi.org/project/jsonld-ex/)  
**Target Venue:** IETF 125 Hackathon, March 14–15 2026  

---

## Document Conventions

- **MUST**, **MUST NOT**, **SHALL**, **SHOULD**, **MAY** follow RFC 2119 semantics.
- All mathematical notation follows Jøsang (2016) unless stated otherwise.
- Section numbering is stable across revisions; new sections are appended or inserted with sub-numbering.
- `[GAP-n]` markers reference the gap analysis. When a gap is resolved, the marker is replaced with the resolution and the gap log (Appendix A) is updated.

---

## 1. Introduction and Motivation

CBOR-LD-ex (Concise Binary Object Representation for Linked Data, Extended) is a compact binary serialization format for semantically-annotated linked data, designed for constrained IoT networks where edge devices must exchange not only observations but also reasoning metadata — compliance status, epistemic confidence, and provenance — under severe bandwidth and compute constraints.

CBOR-LD-ex extends the existing CBOR-LD specification (a compact binary encoding of JSON-LD using CBOR, RFC 8949) with bit-packed semantic reasoning primitives derived from the jsonld-ex Python library and its compliance algebra (Syed et al. 2026).

### 1.1 Design Goals

1. **Sub-10-byte semantic overhead** for compliance-annotated IoT telemetry on Tier 1 (constrained) devices.
2. **Tiered encoding** that adapts header complexity to device capability, from 1-byte headers on microcontrollers to variable-length headers with full provenance on cloud infrastructure.
3. **Formal algebraic closure** ensuring that every protocol-level operation (fusion, evaluation, delegation) produces valid protocol-level outputs.
4. **Full backward compatibility** with CBOR-LD and, through CBOR-LD, with JSON-LD 1.1.

### 1.2 Relationship to Existing Specifications

| Specification | Relationship |
|---|---|
| JSON-LD 1.1 (W3C Rec) | Abstract data model origin; CBOR-LD-ex documents are valid JSON-LD when decoded |
| CBOR (RFC 8949) | Wire encoding; CBOR-LD-ex payloads are valid CBOR |
| CBOR-LD (W3C CG Draft) | Context compression mechanism; CBOR-LD-ex extends CBOR-LD with tagged annotation blocks |
| CoAP (RFC 7252) | Primary transport protocol for constrained environments |
| Subjective Logic (Jøsang 2016) | Theoretical foundation for opinion representation and composition |
| jsonld-ex (Syed et al. 2026) | Reference implementation; compliance algebra, SL network, and MQTT/CBOR-LD modules |

---

## 2. Foundational Axioms

The following three properties are **axiomatic** — they are non-negotiable invariants that every design decision in this specification MUST preserve. Any proposed extension or encoding that violates any of these axioms is invalid regardless of other merits.

### Axiom 1: Backward Compatibility (The Stripping Property)

**Every valid CBOR-LD-ex message is a valid CBOR-LD message is a valid JSON-LD document.**

Formally: Let `M` be any CBOR-LD-ex message. Define the stripping function `σ` that removes all CBOR-LD-ex annotation tags (§5) from `M`, leaving only standard CBOR-LD content. Then:

```
σ(M) is a valid CBOR-LD message
```

And applying the standard CBOR-LD-to-JSON-LD decompression `δ`:

```
δ(σ(M)) is a valid JSON-LD 1.1 document
```

**Consequence:** A parser that understands only CBOR-LD can decode any CBOR-LD-ex message by ignoring unknown CBOR tags. A parser that understands only JSON-LD can decode the fully decompressed form. Information loss under stripping is limited to the annotation `α` — the data content `(s, p, o)` is fully preserved.

**Formal requirement:** CBOR-LD-ex annotations MUST be encoded as CBOR tagged byte strings (Major Type 6 + Major Type 2) using tag numbers from the "First Come First Served" range (≥ 256) or the experimental range. Standard CBOR-LD parsers MUST be able to skip these tags without parsing failure per RFC 8949 §3.4 (tags on unrecognized tag numbers are ignored by well-behaved decoders).

### Axiom 2: Algebraic Closure (The Composition Property)

**Annotations compose through well-defined operations, and every composition produces a valid annotation.**

Formally: Let `A` be the set of all valid CBOR-LD-ex annotations (Definition 5, §3). Define the set of annotation operators `Ω = {fuse, meet, propagate, decay, delegate, withdraw, ...}`. For every operator `op ∈ Ω` with arity `k`:

```
∀ α₁, ..., αₖ ∈ A : op(α₁, ..., αₖ) ∈ A
```

**Consequence:** A Tier 2 gateway that receives Tier 1 annotations and applies any sequence of valid operations will always produce valid Tier 2 annotations. There is no operation sequence that can produce an ill-formed annotation. The protocol is closed under its own operations.

**Specific closure requirements:**

- **(C1) Opinion fusion:** Cumulative fusion of two opinions `ω₁, ω₂` produces a valid opinion `ω₃` satisfying `b₃ + d₃ + u₃ = 1`, `b₃, d₃, u₃ ≥ 0`.
- **(C2) Compliance evaluation:** Evaluating compliance over an opinion produces a valid compliance assertion `(c, ω)` where `c ∈ C` and `ω` satisfies the SL constraint.
- **(C3) Jurisdictional Meet:** `J_⊓(ω₁, ω₂)` produces a valid opinion (Theorem 1, Syed et al. 2026).
- **(C4) Compliance Propagation:** `Prop(τ, π, ωₛ)` produces a valid opinion (Theorem 2, Syed et al. 2026).
- **(C5) Temporal Decay:** `ω(t)` for any `t ≥ t₀` produces a valid opinion (Theorem 4, Syed et al. 2026).
- **(C6) Delegation:** `δ(t)` is trivially valid — it carries no opinion to constrain.
- **(C7) Quantized closure:** Closure holds in the quantized domain (§4.2).

### Axiom 3: Quantization Correctness (The Invariant Preservation Property)

**The constrained quantization function preserves Subjective Logic invariants in the compact representation.**

Formally: Let `ω = (b, d, u, a)` be a valid SL opinion with `b + d + u = 1`. Let `Q_n` be the n-bit quantization function (Definition 8, §4). Then the quantized representation `ω̂ = Q_n(ω)` satisfies:

```
Q_n(b) + Q_n(d) + Q_n(u) = 2ⁿ − 1
```

And the reconstructed opinion `Q_n⁻¹(ω̂)` satisfies:

```
Q_n⁻¹(Q_n(b)) + Q_n⁻¹(Q_n(d)) + Q_n⁻¹(Q_n(u)) = 1.0  (exactly)
```

**This is achieved by constrained quantization:** quantize `b` and `d` independently via rounding, then derive `û = (2ⁿ − 1) − b̂ − d̂`. The third component is never independently quantized.

**Consequence:** No CBOR-LD-ex parser will ever reconstruct an opinion tuple that violates the SL constraint. Downstream reasoning operations that depend on `b + d + u = 1` are safe to assume this invariant holds exactly in the quantized domain.

**Bound on quantization error:** For n-bit precision, the maximum error on any single component is bounded by:

```
|x − Q_n⁻¹(Q_n(x))| ≤ 1 / (2(2ⁿ − 1))
```

For the derived component `u`, the error is bounded by:

```
|u − Q_n⁻¹(û)| ≤ 1 / (2ⁿ − 1)
```

(The derived component accumulates rounding error from both `b` and `d`.)

---

## 3. Abstract Data Model

### 3.1 Subjective Logic Primitives

**Definition 1 (Binomial Opinion).** A binomial opinion is a tuple `ω = (b, d, u, a)` where:
- `b ∈ [0, 1]` is belief
- `d ∈ [0, 1]` is disbelief
- `u ∈ [0, 1]` is uncertainty
- `a ∈ [0, 1]` is the base rate (prior probability)
- Constraint: `b + d + u = 1`

The projected probability is `P(ω) = b + a · u`.

The vacuous opinion is `ω_V = (0, 0, 1, a)` representing complete ignorance.

*Reference: Jøsang (2016), Definition 1.1.*

**Definition 2 (Multinomial Opinion).** A multinomial opinion over domain `X = {x₁, ..., xₖ}` is a tuple `ω_X = (b⃗, u, a⃗)` where:
- `b⃗ = (b₁, ..., bₖ)` with each `bᵢ ≥ 0`
- `u ≥ 0`
- `∑ᵢ bᵢ + u = 1`
- `a⃗ = (a₁, ..., aₖ)` with `∑ᵢ aᵢ = 1`, each `aᵢ ≥ 0`

The projected probability for outcome `xᵢ` is `P(xᵢ) = bᵢ + aᵢ · u`.

`[GAP-2]` *Multinomial opinions are first-class primitives in the abstract model. Their wire encoding is specified in §4.4.*

*Reference: Jøsang (2016), Definition 2.2; jsonld-ex `MultinomialOpinion` class.*

**Definition 3 (Domain-Specific Interpretation).** The semantic interpretation of opinion components depends on the reasoning context:

| Context | `b` | `d` | `u` | `a` |
|---|---|---|---|---|
| General SL | Belief | Disbelief | Uncertainty | Base rate |
| Compliance (Syed et al.) | Lawfulness `l` | Violation `v` | Uncertainty `u` | Jurisdictional prior |
| Data quality | Accuracy | Inaccuracy | Unknown quality | Expected accuracy |
| Trust | Trust | Distrust | Uncertainty | Default trust |
| Erasure | Completeness `e` | Persistence `ē` | Unknown | Expected completeness |

`[GAP-1 RESOLVED]` *The wire format uses the general notation `(b, d, u, a)`. The `reasoning_context` field (§5) specifies which interpretation applies. Parsers MAY relabel components for domain-specific display but MUST NOT alter the algebraic semantics.*

### 3.2 Compliance Status

**Definition 4 (Compliance Status).** A compliance status is an element of the set:

```
C = { compliant, non_compliant, insufficient }
```

This three-valued logic distinguishes:
- `compliant` — evidence supports lawfulness
- `non_compliant` — evidence supports violation
- `insufficient` — evidence is inadequate for determination

Wire encoding: 2 bits (§5.1).

### 3.3 Compliance Operators

**Definition 5 (Operator Identifier).** A compliance operator identifier is an element of the set:

```
Op = { jurisdictional_meet, compliance_propagation, consent_assessment,
       temporal_decay, erasure_propagation, cumulative_fusion,
       trust_discount, deduction, withdrawal_override,
       expiry_trigger, review_trigger, regulatory_change }
```

`[GAP-3 RESOLVED]` *Each operator in Op has a unique numeric code for wire encoding (§5.2, Table 2). The provenance chain (§6) records which operator produced each annotation, not merely "subjective logic was used."*

*Reference: Syed et al. (2026), §§5–9; jsonld-ex `compliance_algebra.py`.*

### 3.4 Annotation Types

**Definition 6 (Annotation).** An annotation is a value from the following algebraic type:

```
α ::= ∅                                    — bare (no annotation)
    | ω                                    — opinion only (binomial)
    | ω_X                                  — opinion only (multinomial)
    | (c, ω)                               — compliance assertion
    | (c, ω, op)                           — compliance assertion with operator provenance
    | δ(t)                                 — delegation to tier t
    | τ(w, λ, triggers)                    — temporal annotation
    | trust(ω_T, agent_id)                 — trust relationship
    | attest(ω, agent_id)                  — attestation
    | chain([(α₁, t₁), ..., (αₙ, tₙ)])   — provenance chain
```

Where:
- `ω` is a binomial opinion (Definition 1)
- `ω_X` is a multinomial opinion (Definition 2)
- `c ∈ C` is a compliance status (Definition 4)
- `op ∈ Op` is an operator identifier (Definition 5)
- `t` is a tier class (§5.1)
- `w` is a time window specification
- `λ` is a decay function identifier
- `triggers` is a set of temporal trigger specifications
- `agent_id` is a unique agent identifier
- `tᵢ` are timestamps

`[GAP-4 PARTIAL]` *Trust and attestation annotations are included as first-class annotation types. Full trust graph encoding is deferred to §7 (Protocol Stack Integration).*

### 3.5 Annotated Assertions

**Definition 7 (Annotated Assertion).** An annotated assertion is a tuple `(s, p, o, α)` where:
- `s` is a subject (IRI or blank node)
- `p` is a predicate (IRI)
- `o` is an object (IRI, blank node, or literal)
- `α` is an annotation (Definition 6)

When `α = ∅`, the assertion degenerates to a standard RDF triple.

**Definition 8 (Annotated Graph).** An annotated graph `G` is a finite set of annotated assertions:

```
G = { (s₁, p₁, o₁, α₁), ..., (sₙ, pₙ, oₙ, αₙ) }
```

**Operations on annotated graphs** (formal definitions deferred to §8):
- **Merge:** `G₁ ∪ G₂` with annotation conflict resolution
- **Project:** `π(G)` strips all annotations, yielding a standard RDF graph
- **Filter:** `σ_C(G)` selects assertions matching compliance status `c ∈ C`

---

## 4. Quantization Theory

### 4.1 The Quantization Function

**Definition 9 (Quantization).** For precision `n` bits, the quantization function `Q_n : [0,1] → {0, 1, ..., 2ⁿ − 1}` is:

```
Q_n(x) = round(x × (2ⁿ − 1))
```

With inverse:

```
Q_n⁻¹(k) = k / (2ⁿ − 1)
```

### 4.2 Constrained Quantization for Opinions

**Definition 10 (Constrained Binomial Quantization).** Given opinion `ω = (b, d, u, a)` with `b + d + u = 1`, the constrained quantization `Q̃_n(ω)` is:

```
b̂ = Q_n(b)
d̂ = Q_n(d)
û = (2ⁿ − 1) − b̂ − d̂
â = Q_n(a)
```

The uncertainty component `û` is **derived, never independently quantized**.

**Theorem 1 (Quantization Constraint Preservation).** For any valid opinion `ω = (b, d, u, a)` with `b + d + u = 1` and `b, d, u ≥ 0`:

**(a)** `b̂ + d̂ + û = 2ⁿ − 1` (exact in the quantized domain).

**(b)** `Q_n⁻¹(b̂) + Q_n⁻¹(d̂) + Q_n⁻¹(û) = 1.0` (exact upon reconstruction).

**(c)** `û ≥ 0` provided `n ≥ 2`.

*Proof of (a):* By construction, `û = (2ⁿ − 1) − b̂ − d̂`, so `b̂ + d̂ + û = 2ⁿ − 1`. ∎

*Proof of (b):* `Q_n⁻¹(b̂) + Q_n⁻¹(d̂) + Q_n⁻¹(û) = (b̂ + d̂ + û) / (2ⁿ − 1) = (2ⁿ − 1) / (2ⁿ − 1) = 1`. ∎

*Proof of (c):* We need `b̂ + d̂ ≤ 2ⁿ − 1`. Since `b + d ≤ 1` (because `u ≥ 0`), we have `b̂ ≤ round(b(2ⁿ − 1))` and `d̂ ≤ round(d(2ⁿ − 1))`. In the worst case, both round up by 0.5/(2ⁿ − 1), giving `b̂ + d̂ ≤ (b + d)(2ⁿ − 1) + 1 ≤ 2ⁿ − 1 + 1 = 2ⁿ`. However, we need `b̂ + d̂ ≤ 2ⁿ − 1`. This fails only when `b + d = 1` (i.e., `u = 0`) and both round up, which requires `b(2ⁿ − 1)` and `d(2ⁿ − 1)` to both have fractional part exactly 0.5. For `n ≥ 2`, this is a measure-zero edge case that the encoder MUST handle by clamping: if `b̂ + d̂ > 2ⁿ − 1`, decrement `d̂` by 1. ∎

**Remark (Clamping rule).** The encoder MUST enforce `û ≥ 0` by checking `b̂ + d̂ ≤ 2ⁿ − 1` after rounding. If violated, `d̂` is decremented by 1. The choice to clamp `d̂` rather than `b̂` introduces a marginal bias toward belief over disbelief in the clamping edge case. This bias direction is documented and MAY be made configurable in future revisions.

### 4.3 Quantization Error Bounds

**Theorem 2 (Per-Component Error Bound).** For `n`-bit quantization:

**(a)** For independently quantized components (`b`, `d`, `a`):
```
|x − Q_n⁻¹(Q_n(x))| ≤ 1 / (2(2ⁿ − 1))
```

**(b)** For the derived component `u`:
```
|u − Q_n⁻¹(û)| ≤ 1 / (2ⁿ − 1)
```

*Proof of (a):* Standard rounding error: `|x − round(x(2ⁿ−1))/(2ⁿ−1)| ≤ 0.5/(2ⁿ−1) = 1/(2(2ⁿ−1))`. ∎

*Proof of (b):* `û = (2ⁿ−1) − b̂ − d̂`. The error in `û` is `|u(2ⁿ−1) − û| = |(2ⁿ−1)(b+d) − (b̂+d̂) − (2ⁿ−1)(b+d) + u(2ⁿ−1)| = |b̂+d̂ − (b+d)(2ⁿ−1)|`. Since `b̂` and `d̂` each have error at most 0.5, the combined error is at most 1, giving `|u − Q_n⁻¹(û)| ≤ 1/(2ⁿ−1)`. ∎

**Table 1: Precision characteristics by mode.**

The wire format transmits **3 values only** (b̂, d̂, â). The uncertainty component û is NEVER transmitted — it carries zero bits of Shannon information because û = (2ⁿ−1) − b̂ − d̂. The decoder derives it.

| Precision Mode | Bits/value | Max error (b,d) | Max error (u) | Wire bytes (3 values) |
|---|---|---|---|---|
| 00 (8-bit) | 8 | ≈ 0.00196 | ≈ 0.00392 | 3 |
| 01 (16-bit) | 16 | ≈ 0.0000076 | ≈ 0.0000153 | 6 |
| 10 (32-bit float) | 32 | IEEE 754 | IEEE 754 | 12 |
| 11 (reserved) | — | — | — | — |

### 4.4 Constrained Multinomial Quantization

`[GAP-2 RESOLVED]`

**Definition 11 (Constrained Multinomial Quantization).** Given a multinomial opinion `ω_X = (b⃗, u, a⃗)` over domain `X = {x₁, ..., xₖ}` with `∑ᵢ bᵢ + u = 1`, the constrained quantization `Q̃_n(ω_X)` is:

```
b̂ᵢ = Q_n(bᵢ)        for i = 1, ..., k−1
b̂ₖ = —  (not transmitted; see below)
û  = (2ⁿ − 1) − ∑ᵢ₌₁ᵏ⁻¹ b̂ᵢ − b̂ₖ
```

**Problem:** With `k` belief components plus uncertainty, we have `k + 1` values summing to 1. Independently quantizing all of them will not preserve the sum. Generalizing the binomial approach: quantize `k − 1` belief components and uncertainty independently, derive the `k`-th belief component:

```
b̂ᵢ = Q_n(bᵢ)        for i = 1, ..., k−1
û   = Q_n(u)
b̂ₖ  = (2ⁿ − 1) − (∑ᵢ₌₁ᵏ⁻¹ b̂ᵢ) − û
```

**Theorem 3 (Multinomial Quantization Constraint Preservation).**

**(a)** `∑ᵢ₌₁ᵏ b̂ᵢ + û = 2ⁿ − 1` (exact).

**(b)** `Q_n⁻¹(∑ᵢ b̂ᵢ) + Q_n⁻¹(û) = 1.0` (exact upon reconstruction).

**(c)** `b̂ₖ ≥ 0` provided individual rounding errors don't exceed `bₖ(2ⁿ − 1)`. The encoder MUST clamp: if `b̂ₖ < 0`, reduce the largest `b̂ᵢ` (for `i < k`) by 1 and recompute.

*Proof:* Analogous to Theorem 1, extended to `k + 1` components. ∎

**Wire format for multinomial opinions:**

```
[1 byte]  k — domain cardinality (1–255; 0 reserved)
[1 byte]  precision_mode (same 2-bit encoding as binomial, packed with 6 reserved bits)
[k−1 × value_width]  b̂₁, ..., b̂ₖ₋₁ (independently quantized)
[value_width]         û (independently quantized)
[(k-1) × value_width]  â₁, ..., âₖ₋₁ (independently quantized; âₖ derived via clamping)
```

The k-th belief component `b̂ₖ` is derived by the decoder: `b̂ₖ = (2ⁿ − 1) − (∑ᵢ₌₁ᵏ⁻¹ b̂ᵢ) − û`. Similarly, `âₖ = (2ⁿ − 1) − ∑ᵢ₌₁ᵏ⁻¹ âᵢ`. Neither derived component is transmitted.

**Space cost:** For 8-bit precision and `k = 4` (quaternary domain): `1 + 1 + 3 + 1 + 3 = 9 bytes`. Compare to JSON-LD encoding of the same: ~120+ bytes. The overhead scales linearly with `k`, which is acceptable since multinomial opinions are primarily used at Tier 2/3 where bandwidth is less constrained.

**Tier restriction:** Tier 1 devices with 1-byte headers SHOULD use binomial opinions only. Multinomial opinions SHOULD be used at Tier 2 and above. This is a SHOULD, not a MUST — a sufficiently capable Tier 1 device MAY transmit multinomial opinions using the Tier 2 header format.

### 4.5 Quantization Error Propagation Through Operators

`[GAP-6 RESOLVED]`

When quantized opinions are composed through compliance operators, rounding errors compound. We bound this for the critical operators.

**Theorem 4 (Jurisdictional Meet Error Propagation).** Let `ω₁ = (b₁, d₁, u₁, a₁)` and `ω₂ = (b₂, d₂, u₂, a₂)` be opinions. Let `ω̂₁, ω̂₂` be their `n`-bit quantized forms. Let `l_⊓ = b₁b₂` (exact) and `l̂_⊓ = b̂₁b̂₂/(2ⁿ−1)` (quantized product, renormalized). Then:

```
|l_⊓ − l̂_⊓| ≤ ε_b(b₁ + b₂) + ε_b²
```

where `ε_b = 1/(2(2ⁿ−1))` is the single-component quantization error bound.

*Proof:* Let `b̂ᵢ = bᵢ + εᵢ` where `|εᵢ| ≤ ε_b`. Then `b̂₁b̂₂ = b₁b₂ + b₁ε₂ + b₂ε₁ + ε₁ε₂`. The error magnitude is bounded by `|b₁||ε₂| + |b₂||ε₁| + |ε₁||ε₂| ≤ b₁ε_b + b₂ε_b + ε_b² = ε_b(b₁ + b₂) + ε_b²`. ∎

**Corollary (8-bit Meet Error).** For 8-bit quantization (`ε_b ≈ 0.00196`) and typical beliefs `b₁, b₂ ≤ 1`:

```
|l_⊓ − l̂_⊓| ≤ 0.00196 × 2 + 0.00196² ≈ 0.00392
```

This is within the precision of a single 8-bit value — the meet operation does not amplify quantization error beyond one precision step.

**Theorem 5 (Chain Propagation Error Bound).** For a derivation chain of length `n` (Compliance Propagation applied `n` times), with 8-bit quantized opinions at each step, the cumulative error on the lawfulness component is bounded by:

```
|l_exact − l_quantized| ≤ n × ε_b × l_exact^((n-1)/n) + O(ε_b²)
```

*This is a first-order approximation.* For chains of length ≤ 10 with 8-bit precision, the error remains below 0.04 — acceptable for Tier 1/2 operations. Chains exceeding length 10 SHOULD use 16-bit precision or higher.

**Practical guidance:**

| Chain length | 8-bit error bound | Recommendation |
|---|---|---|
| 1–5 | ≤ 0.02 | 8-bit sufficient |
| 6–10 | ≤ 0.04 | 8-bit acceptable; 16-bit preferred |
| 11–20 | ≤ 0.08 | 16-bit required |
| 20+ | unbounded at 8-bit | 32-bit float or promote to Tier 3 |

---

## 5. Wire Format

### 5.1 Tier-Dependent Header Profiles

The `origin_tier` field (2 bits) acts as a **format discriminator**. The parser reads these bits and immediately knows the header layout.

**Tier Class 00 — Constrained (Tier 1)**

```
Bit  Width  Field
───────────────────────────
0    2      compliance_status          (Definition 4)
2    1      delegation_flag            (0 = terminal, 1 = forward)
3    2      origin_tier                (= 00)
5    1      has_opinion                (0 = no opinion, 1 = opinion follows)
6    2      precision_mode             (Table 1)
```

**1 byte fixed header.** No context version, no operator provenance, no sub-tier. Minimum viable semantic annotation.

If `has_opinion = 1`: opinion payload follows per `precision_mode`.

Typical Tier 1 message: **4 bytes** (1 header + 3 opinion at 8-bit; û not transmitted).
Minimum Tier 1 message: **1 byte** (header only, no opinion).

**Tier Class 01 — Edge (Tier 2)**

```
Bit  Width  Field
───────────────────────────
0    2      compliance_status
2    1      delegation_flag
3    2      origin_tier                (= 01)
5    1      has_opinion
6    2      precision_mode
8    4      operator_id                (see Table 2)
12   4      reasoning_context          (see Definition 3)
16   4      context_version            (0–15)
20   1      has_multinomial            (0 = binomial opinion, 1 = multinomial)
21   3      sub_tier_depth             (0–7)
24   8      source_count               (0–255 contributing Tier 1 sources)
```

**4 byte (32-bit) fixed header.** The original draft had 36 bits (context_version: 6, sub_tier_depth: 4, plus a reserved bit). Corrected to exactly 32 bits: context_version reduced to 4 bits (0–15), sub_tier_depth to 3 bits (0–7), reserved bit removed. Full 8-bit source_count preserved as the operationally more important field.

`[GAP-3]` The `operator_id` field (4 bits, 16 values) encodes which compliance operator produced this annotation. `reasoning_context` (4 bits) specifies the interpretation mapping (Definition 3).

`[GAP-2]` The `has_multinomial` flag indicates whether the opinion payload uses the multinomial encoding (§4.4) rather than binomial.

**Tier Class 10 — Cloud (Tier 3)**

```
Bit  Width  Field
───────────────────────────
0    2      compliance_status
2    1      delegation_flag
3    2      origin_tier                (= 10)
5    1      has_opinion
6    2      precision_mode
8    4      operator_id
12   4      reasoning_context
16   1      has_extended_context
17   1      has_provenance_chain
18   1      has_multinomial
19   1      has_trust_info
20   4      sub_tier_depth
24   8      reserved/flags
```

**4 byte fixed header**, followed by variable-length extension blocks based on flag bits.

Extension blocks (when flagged):

```
If has_extended_context = 1:
  [16 bits] context_id
  [16 bits] context_version
  (4 bytes)

If has_provenance_chain = 1:
  [8 bits] chain_length
  For each entry:
    [2 bits]  entry_tier
    [2 bits]  entry_precision
    [4 bits]  entry_operator_id
    [variable] opinion at entry_precision
    [32 bits] timestamp (seconds since epoch, truncated)
  (variable length)

If has_trust_info = 1:
  [8 bits] agent_id_length
  [variable] agent_id (UTF-8, compact)
  [variable] trust opinion (per precision_mode)
```

**Tier Class 11 — Reserved**

Reserved for future use. Parsers encountering `origin_tier = 11` MUST skip the annotation block without error.

### 5.2 Operator Identifier Table

**Table 2: Operator ID assignments (4-bit field, Tier 2/3 only).**

| Code | Operator | Reference |
|---|---|---|
| 0000 | none / raw observation | — |
| 0001 | cumulative_fusion | Jøsang (2016) §12.3 |
| 0010 | trust_discount | Jøsang (2016) §10.2 |
| 0011 | deduction | Jøsang (2016) §7 |
| 0100 | jurisdictional_meet | Syed et al. §5 |
| 0101 | compliance_propagation | Syed et al. §6 |
| 0110 | consent_assessment | Syed et al. §7 |
| 0111 | temporal_decay | Syed et al. §8 |
| 1000 | erasure_propagation | Syed et al. §9 |
| 1001 | withdrawal_override | Syed et al. §7.2 |
| 1010 | expiry_trigger | Syed et al. §8.2 |
| 1011 | review_trigger | Syed et al. §8.2 |
| 1100 | regulatory_change | Syed et al. §8.2 |
| 1101 | reserved | — |
| 1110 | reserved | — |
| 1111 | extension (next byte is extended op code) | — |

Code `1111` allows future extension beyond 16 operators via a follow-on byte.

### 5.3 CBOR Tag Integration

CBOR-LD-ex annotation blocks are wrapped in CBOR tagged byte strings:

```
Tag(TBD_CBORLD_EX) → byte string (bit-packed annotation per §5.1)
```

The tag number SHALL be registered in the IANA CBOR Tags registry under the "First Come First Served" policy. For experimental/hackathon use, tag numbers in the range 65536–15309735 (unassigned) MAY be used.

**Proposed tag number for hackathon:** `60000` (experimental).

**Interoperability with standard CBOR-LD parsers:** Per RFC 8949 §3.4, a CBOR decoder encountering an unrecognized tag "can present both the tag number and the tag content to the application." A standard CBOR-LD parser will see `Tag(60000, byte_string)` and either ignore it (well-behaved) or present the raw bytes. In either case, the surrounding CBOR-LD content is unaffected — satisfying Axiom 1.

**Message structure:**

```
CBOR-LD-ex message = standard CBOR-LD document
                   + Tag(60000) → byte string (annotation block)
```

The annotation block is a **sibling** of the data content in the CBOR map, keyed by the protocol-defined integer term ID `60000` (matching the CBOR tag number). CBOR-LD maps ALL vocabulary terms to integers on the wire — string keys never appear. The string `"@annotation"` is used only in JSON-LD text representations, never on the CBOR wire. CBOR encoding cost: 3 bytes (major type 0, additional info 25, 2-byte value). Compare to `"@annotation"` as a CBOR text string: 12 bytes.

---

## 6. Provenance Model

`[GAP-4]` `[GAP-8 PARTIAL]`

The provenance chain is the mechanism by which Tier 3 (and auditors) can reconstruct the full reasoning path from raw sensor reading to compliance determination.

### 6.1 Provenance Chain Structure

**Definition 12 (Provenance Entry).** A provenance entry is a tuple:

```
e = (tier, depth, operator_id, precision_mode, opinion, timestamp)
```

**Definition 13 (Provenance Chain).** A provenance chain `Π` is an ordered sequence of provenance entries:

```
Π = [e₁, e₂, ..., eₙ]
```

where `e₁` is the originating observation and `eₙ` is the most recent processing step. Temporal ordering is strict: `t₁ < t₂ < ... < tₙ`.

### 6.2 Chain Growth

Each processing tier appends an entry to the chain:

- **Tier 1** creates chain `Π = [e₁]` (single entry: raw observation with opinion).
- **Tier 2** receives `Π`, appends `e₂` (fused/evaluated result): `Π' = Π ∥ [e₂]`.
- **Tier 3** receives `Π'`, appends `e₃`: `Π'' = Π' ∥ [e₃]`.

**Tier 1 devices do not transmit chains.** The Tier 1 message IS the first chain entry, reconstructed by the receiving gateway from the Tier 1 header and opinion payload. This preserves the 1-byte header constraint.

### 6.3 Conditional Opinion Encoding

`[GAP-8]` *Conditional opinions `(ω_{Y|X=T}, ω_{Y|X=F})` used in SL deduction are encoded as paired opinion payloads within a provenance entry when `operator_id = deduction`. The entry carries two opinion tuples instead of one, signaled by the operator code.*

---

## 7. Temporal Model

`[GAP-9 RESOLVED]`

IoT data is inherently temporal. Compliance is often time-bounded ("temperature must stay below X for Y minutes"). The temporal model addresses three concerns: how opinions age on the wire, how time-series data is encoded compactly, and how regulatory triggers cause discrete state changes.

*Reference: jsonld-ex `confidence_decay.py`, `confidence_temporal_fusion.py`, `sl_network/temporal.py`; Syed et al. (2026) §8.*

### 7.1 Opinion Decay

**Definition 14 (Decay Function).** A decay function `λ : [0, ∞) → [0, 1]` maps elapsed time to a retention factor. It MUST satisfy:
- `λ(0) = 1` (no decay at time of formation)
- `λ` is monotonically non-increasing
- `λ(t) ≥ 0` for all `t`

**Definition 15 (Decayed Opinion).** Given opinion `ω = (b, d, u, a)` and decay factor `λ(t)`:

```
ω(t) = (λ(t)·b,  λ(t)·d,  1 − λ(t)·(b + d),  a)
```

**Theorem 6 (Decay Preserves SL Constraint).**

**(a)** `b(t) + d(t) + u(t) = λb + λd + 1 − λ(b+d) = 1`. ∎

**(b)** `b(t), d(t) ≥ 0` since `λ ≥ 0` and `b, d ≥ 0`.

**(c)** `u(t) = 1 − λ(b+d) ≥ 1 − 1·1 = 0` since `λ ≤ 1` and `b + d ≤ 1`.

**(d)** The evidence ratio `b(t)/d(t) = b/d` is preserved (direction of evidence is unchanged; only magnitude decays).

**(e)** The projected probability `P(ω(t)) = λb + a(1 − λ(b+d))` converges to `a` as `λ → 0` (reverts to base rate under complete decay).

**Consequence for Axiom 2:** Decay produces a valid opinion from a valid opinion — closure holds.

**Consequence for Axiom 3:** Quantized decay applies the factor to quantized components. Given `b̂, d̂` in the quantized domain, the encoder computes `b̂' = round(λ · Q_n⁻¹(b̂) · (2ⁿ−1))`, `d̂' = round(λ · Q_n⁻¹(d̂) · (2ⁿ−1))`, and derives `û' = (2ⁿ−1) − b̂' − d̂'`. The SL constraint is preserved by construction.

### 7.2 Decay Function Registry

Three built-in decay functions are defined, matching the jsonld-ex implementation:

**Table 3: Decay function identifiers (2-bit field in temporal payload).**

| Code | Function | Formula | Behavior |
|---|---|---|---|
| 00 | exponential | `λ(t) = 2^(−t/τ)` | Smooth, never zero, standard |
| 01 | linear | `λ(t) = max(0, 1 − t/(2τ))` | Reaches zero at `t = 2τ` |
| 10 | step | `λ(t) = 1 if t < τ, else 0` | Binary freshness (TTL) |
| 11 | reserved | — | Future: custom/negotiated |

Where `τ` is the half-life parameter (the time at which `λ = 0.5` for exponential decay, or the time at which `λ = 0.5` for linear decay).

### 7.3 Temporal vs. Spatial Fusion

Fusion operations at Tier 2 gateways fall into two semantically distinct categories:

**Definition 16 (Spatial Fusion).** Combining opinions from `n` different sources at the same point in time about the same proposition. Example: 5 temperature sensors reporting simultaneously. Uses standard SL cumulative fusion.

**Definition 17 (Temporal Fusion).** Combining opinions from the same source (or aggregated source) at different points in time. Example: 1 temperature sensor reporting every 5 seconds for 5 minutes. Requires decay-then-fuse: each opinion is first decayed by its age relative to a reference time, then fused.

The distinction matters for the wire format because temporal fusion carries implicit time-series structure. The gateway's `operator_id` SHOULD distinguish between these: `cumulative_fusion` (0001) for spatial, `temporal_decay` (0111) for temporal. When both are applied (temporal fusion across multiple spatial sources), the provenance chain records the sequence.

**Definition 18 (Temporal Fusion Pipeline).** Given timestamped opinions `{(ω₁, t₁), ..., (ωₙ, tₙ)}`, reference time `t_ref`, half-life `τ`, and decay function `λ`:

```
Step 1: ω'ᵢ = decay(ωᵢ, elapsed = t_ref − tᵢ, τ, λ)    for each i
Step 2: ω_fused = cumulative_fuse(ω'₁, ..., ω'ₙ)
```

**Theorem 7 (Temporal Fusion Preserves SL Constraint).** Since decay preserves validity (Theorem 6) and cumulative fusion preserves validity (Axiom 2, C1), their composition preserves validity. ∎

### 7.4 Temporal Annotation Wire Format

The temporal annotation `τ(w, λ, triggers)` from Definition 6 is encoded as an optional extension block in Tier 2/3 headers.

**Tier 1 temporal encoding (minimal):**

Tier 1 devices do not encode temporal metadata in the annotation header. Timestamps are carried in the CBOR-LD data payload (e.g., as `observedAt` fields) and are available to the receiving gateway for temporal fusion. This is a deliberate design choice: every byte of header on a constrained device is a byte not available for sensor data.

**Tier 2 temporal extension block:**

```
Bit  Width  Field
───────────────────────────
0    2      decay_function_id          (Table 3)
2    1      has_half_life              (0 = use session default, 1 = explicit)
3    1      has_temporal_window        (0 = no window, 1 = window follows)
4    1      has_triggers               (0 = no triggers, 1 = trigger block follows)
5    3      reserved

If has_half_life = 1:
  [16 bits] half_life_seconds          (0–65535 seconds ≈ 18.2 hours)

If has_temporal_window = 1:
  [32 bits] window_start               (seconds since epoch, truncated)
  [16 bits] window_duration_seconds    (0–65535 seconds)

If has_triggers = 1:
  [8 bits]  trigger_count              (1–255)
  For each trigger:
    [2 bits]  trigger_type             (Table 4)
    [6 bits]  reserved/flags
    [32 bits] trigger_time             (seconds since epoch, truncated)
    [8 bits]  trigger_parameter        (γ for expiry, acceleration factor for review)
```

**Minimum temporal overhead at Tier 2:** 1 byte (header only, using session defaults).
**Typical with half-life and window:** 1 + 2 + 6 = 9 bytes.

### 7.5 Compliance Trigger Types

Three discrete trigger types cause state changes that continuous decay cannot model. These map directly to Syed et al. (2026) §8.2.

**Table 4: Trigger type identifiers (2-bit field).**

| Code | Trigger | Semantic | Effect on opinion |
|---|---|---|---|
| 00 | expiry | Hard/soft deadline (Art. 5(1)(e)) | Transfers `b → d` by factor `(1−γ)` |
| 01 | review_due | Missed mandatory review (Art. 35(11), 45(3)) | Accelerates decay rate (`λ → λ_fast`) |
| 10 | regulatory_change | Legal framework change | Replaces opinion with `ω_new` |
| 11 | reserved | — | — |

**Definition 19 (Expiry Trigger).** At trigger time `t_T`, with residual factor `γ ∈ [0,1]` (encoded as 8-bit quantized):

```
b' = γ · b(t_T)
d' = d(t_T) + (1−γ) · b(t_T)
u' = u(t_T)
```

Key property: lawfulness transfers to violation, NOT to uncertainty. An expired deadline is a known fact, not an epistemic gap. `γ = 0` is hard expiry (all lawfulness becomes violation). `γ = 1` is no effect.

**Definition 20 (Review-Due Trigger).** At trigger time `t_T`, the decay function's half-life is reduced by an acceleration factor `α` (encoded as 8-bit quantized, representing a multiplier `1/α` on the half-life). This models that a missed mandatory review accelerates uncertainty growth without immediately asserting violation.

**Definition 21 (Regulatory Change Trigger).** At trigger time `t_T`, the current opinion is replaced by a new assessment. The new opinion is encoded inline following the trigger metadata. This models discrete legal events (adequacy decision revocation, new regulation taking effect).

**Theorem 8 (Trigger Constraint Preservation).**

**(a)** Expiry: `b' + d' + u' = γb + d + (1−γ)b + u = b + d + u = 1`. ∎

**(b)** Review-due: Does not alter the opinion directly, only the decay parameters. The next decay application uses the accelerated half-life, and decay preserves validity (Theorem 6). ∎

**(c)** Regulatory change: The replacement opinion is a fresh valid opinion by assumption (it was formed through valid assessment). ∎

### 7.6 Time-Series Delta Encoding

For Tier 1 devices emitting frequent readings (e.g., every 5 seconds), full opinion encoding on every message is wasteful when the compliance status and opinion change slowly.

**Definition 22 (Delta Opinion).** When the compliance status and precision mode are unchanged from the previous message, a Tier 1 device MAY send a **delta opinion** by setting a reserved bit in the header. The delta encoding transmits:

```
[1 byte]  Δb̂ (signed 8-bit: change in quantized belief, range −128 to +127)
[1 byte]  Δd̂ (signed 8-bit: change in quantized disbelief)
```

The receiver reconstructs: `b̂_new = b̂_prev + Δb̂`, `d̂_new = d̂_prev + Δd̂`, `û_new = (2ⁿ−1) − b̂_new − d̂_new`.

**Constraint:** The receiver MUST verify `b̂_new, d̂_new ≥ 0` and `b̂_new + d̂_new ≤ 2ⁿ−1`. If violated, the delta is invalid and the receiver MUST request a full opinion retransmission (via CoAP RST or application-level NACK).

**Savings:** Delta encoding reduces the opinion payload from 3 bytes (full) to 2 bytes (delta) when changes are small. For a sensor reporting every 5 seconds with slowly-changing conditions, this halves the annotation bandwidth.

**Limitation:** Delta encoding requires stateful receivers (they must track the previous opinion). Stateless receivers or receivers that missed a message MUST fall back to full opinion encoding. The protocol handles this via periodic full-opinion "keyframes" — every Nth message carries the full opinion regardless of change.

### 7.7 Temporal Model at Each Tier

**Tier 1:** Emits timestamped observations with opinions. No temporal metadata in annotation header. Timestamps in CBOR-LD data payload. May use delta encoding for sequential readings.

**Tier 2:** Receives Tier 1 streams, applies temporal decay per source, performs temporal and/or spatial fusion, emits fused opinions with temporal extension block (§7.4). Tracks per-source half-lives (via jsonld-ex `temporal_fuse_weighted()`). Evaluates compliance triggers. The `source_count` field indicates how many sources were fused.

**Tier 3:** Receives Tier 2 summaries with full temporal metadata. Can reconstruct the temporal evolution of compliance status via the provenance chain. Stores time-series for audit. Applies its own decay and triggers based on the full regulatory graph.

---

## 8. Graph Operations

*Detailed specification deferred to next iteration. Covers:*

- Annotated graph merge with conflict resolution
- Projection to standard RDF (Axiom 1 implementation)
- Subgraph extraction for audit
- Compact graph topology encoding for Tier 3 provenance

*Reference: jsonld-ex `merge.py`.*

---

## 9. Security Model

`[GAP-7 RESOLVED]`

The tiered architecture introduces a distributed trust surface. Each tier boundary is a point where opinions can be forged, tampered with, or replayed. This section defines the threat model, the trust assumptions at each tier, and the mechanisms for ensuring annotation integrity.

*Reference: jsonld-ex `confidence_byzantine.py`, `security.py`.*

### 9.1 Threat Model

**Definition 23 (Threat Classes).** CBOR-LD-ex considers three threat classes, corresponding to increasing adversarial capability:

**Class 1 — Honest-but-constrained (benign failures).** Devices operate correctly but have limited resources. Errors arise from hardware faults, transient communication failures, or clock drift — not malicious intent. This is the baseline assumption for Tier 1 devices.

**Class 2 — Byzantine-faulty (compromised minority).** Up to `f` of `n` devices in a tier may produce arbitrary (possibly malicious) outputs. Remaining devices are honest. This models compromised sensors, firmware attacks, or supply-chain tampering. The protocol SHOULD tolerate this at Tier 2 gateways via Byzantine-resistant fusion.

**Class 3 — Active adversary (targeted attack).** An attacker controls one or more devices and can observe, modify, replay, or inject messages on the network. This is the strongest threat model and requires cryptographic protection (§9.4).

### 9.2 Trust Assumptions by Tier

**Tier 1 (Constrained):** Assumed Class 1 by default. Individual sensor readings carry opinions that reflect measurement quality, not security guarantees. A Tier 1 opinion of `(0.85, 0.05, 0.10, 0.50)` means "my sensor reading suggests compliance with this confidence" — it does NOT mean "this opinion has not been tampered with."

**Tier 2 (Edge Gateway):** Must handle Class 2 threats from Tier 1 sources. The gateway is the first line of defense: it aggregates multiple Tier 1 opinions and can detect outliers. The `source_count` field is meaningful here — a fused opinion from 10 sources with 1 outlier removed is more trustworthy than a single-source passthrough.

**Tier 3 (Cloud):** Assumed to operate in a Class 3 threat environment (public networks, untrusted intermediaries). Requires end-to-end integrity verification of the provenance chain.

### 9.3 Byzantine-Resistant Fusion

At Tier 2, when fusing opinions from multiple Tier 1 sources, the gateway SHOULD apply Byzantine-resistant fusion to detect and exclude compromised sources.

**Definition 24 (Byzantine Fusion).** Given opinions `{ω₁, ..., ωₙ}` from `n` sources, with at most `f` Byzantine-faulty sources (`f < n/2`):

```
Step 1: Compute pairwise conflict matrix C[i,j] = distance(ωᵢ, ωⱼ)
Step 2: Identify and remove up to f most-conflicting opinions
Step 3: Fuse remaining opinions via cumulative fusion
```

Three removal strategies (from jsonld-ex `confidence_byzantine.py`):

**Strategy A — Most conflicting (pure discord).** Remove opinions with highest aggregate pairwise conflict. Suitable when no external trust information is available.

**Strategy B — Least trusted.** When trust weights are available (from TrustEdge relationships), remove opinions from least-trusted sources first. Suitable when the gateway has prior trust assessments of its Tier 1 devices.

**Strategy C — Combined.** Rank by `conflict_score × (1 − trust_weight)`. Balances observed behavior with prior trust.

**Definition 25 (Conflict Distance).** The pairwise conflict between opinions `ωᵢ` and `ωⱼ` is measured by a distance metric on the opinion simplex. Supported metrics:

| Metric | Formula | Properties |
|---|---|---|
| Euclidean (L2) | `‖(bᵢ,dᵢ,uᵢ) − (bⱼ,dⱼ,uⱼ)‖₂ / √2` | Simple, normalized to [0,1] |
| Manhattan (L1) | `(|bᵢ−bⱼ| + |dᵢ−dⱼ| + |uᵢ−uⱼ|) / 2` | Robust to outliers |
| Jensen-Shannon | `JSD(P(ωᵢ) ‖ P(ωⱼ))` | Information-theoretic, symmetric |
| Hellinger | `H(P(ωᵢ), P(ωⱼ))` | Bounded [0,1], smooth |

The Euclidean metric is the default for Tier 2 gateways due to computational simplicity. Higher-tier processing MAY use information-theoretic metrics.

**Definition 26 (Group Cohesion).** After Byzantine removal, the cohesion of the remaining opinions is:

```
cohesion = 1 − mean(C[i,j]) for all remaining pairs (i,j)
```

Cohesion in `[0, 1]` where 1 = perfect agreement. The gateway MAY include the cohesion score in the Tier 2 annotation metadata as a quality indicator.

**Wire format for Byzantine metadata (Tier 2 extension, optional):**

```
[8 bits]  original_source_count    (n, before removal)
[8 bits]  removed_count            (f, sources excluded)
[8 bits]  cohesion_score           (Q_8 quantized, 0–255 → 0.0–1.0)
[2 bits]  removal_strategy         (00=most_conflicting, 01=least_trusted, 10=combined, 11=reserved)
[6 bits]  reserved
```

**4 bytes** for Byzantine metadata when present. This allows Tier 3 to assess the quality of the gateway's fusion without re-processing the raw Tier 1 data.

### 9.4 Provenance Chain Integrity

The provenance chain (§6) is the audit trail. If an attacker can modify chain entries undetected, the entire compliance determination is undermined.

**Definition 27 (Chain Authentication).** Each provenance entry `eᵢ` MAY carry a message authentication code (MAC) computed over the entry contents and all preceding entries:

```
mac_i = HMAC-SHA256(key_tier, e₁ ∥ e₂ ∥ ... ∥ eᵢ)
```

Where `key_tier` is a per-tier symmetric key established during device provisioning or session establishment.

**Chaining property:** `mac_i` depends on all previous entries. Modifying any earlier entry invalidates all subsequent MACs. This provides **tamper evidence** — a verifier can detect modification but not prevent it.

**Wire format:** When chain authentication is enabled, each provenance entry is extended with:

```
[8 bits]   mac_algorithm            (0 = HMAC-SHA256-64, 1 = HMAC-SHA256-128, 2–255 reserved)
[variable] mac_value                (64 or 128 bits, truncated HMAC)
```

Truncated HMAC (64 bits) is the default for bandwidth efficiency. 128-bit MAC is available for high-security environments.

**Tier 1 exception:** Tier 1 devices do not transmit provenance chains (§6.2). The integrity of Tier 1 messages is protected at the transport layer (DTLS or OSCORE, §9.5), not at the annotation layer.

### 9.5 Transport Layer Security Integration

CBOR-LD-ex does NOT define its own transport encryption. It relies on existing IoT security protocols:

**DTLS 1.2/1.3 (RFC 6347/9147):** Provides encrypted, authenticated transport for CoAP over UDP. Protects against Class 3 threats (eavesdropping, injection, replay) at the message level. The CBOR-LD-ex annotation block is protected as part of the CoAP payload.

**OSCORE (RFC 8613):** Object Security for Constrained RESTful Environments. Provides end-to-end security for CoAP at the application layer, surviving CoAP proxies. OSCORE protects individual CoAP options and payload, which means the CBOR-LD-ex annotation is integrity-protected even through untrusted intermediaries.

**Recommendation by tier:**

| Tier | Minimum Security | Recommended |
|---|---|---|
| Tier 1 → Tier 2 | Pre-shared key DTLS | OSCORE with group keys |
| Tier 2 → Tier 3 | DTLS 1.3 with certificates | OSCORE + provenance chain MAC |
| Tier 3 internal | TLS 1.3 | TLS 1.3 + provenance chain MAC |

### 9.6 Opinion Forgery Prevention

**Threat:** A compromised gateway (Tier 2) could forge a fused opinion that misrepresents the underlying Tier 1 data — e.g., claiming compliance when the raw sensor data indicates violation.

**Mitigation 1 — Provenance chain verification.** If the provenance chain includes Tier 1 entries with MACs computed under Tier 1 keys, the Tier 2 gateway cannot forge Tier 1 entries. Tier 3 can verify Tier 1 entries independently and check that the Tier 2 fusion is consistent with the Tier 1 inputs.

**Mitigation 2 — Redundant reporting.** Multiple Tier 2 gateways receiving the same Tier 1 data SHOULD produce consistent fused opinions. Tier 3 can cross-check independent gateway outputs.

**Mitigation 3 — Byzantine-resistant aggregation at Tier 3.** Tier 3 applies its own Byzantine fusion across multiple Tier 2 gateways, treating gateways as potentially Byzantine sources.

**Limitation (honest assessment):** If ALL gateways processing a device's data are compromised, and no independent path exists, forgery is undetectable at the protocol level. This is a fundamental limitation of any layered architecture — it is not specific to CBOR-LD-ex. Defense against this scenario requires physical security of gateway hardware, which is outside the protocol's scope.

### 9.7 Replay Protection

**Threat:** An attacker re-sends a previously valid CBOR-LD-ex message to inject stale compliance assessments.

**Mitigation:** Timestamps in provenance entries and CBOR-LD data payloads provide replay detection. Receivers MUST reject messages with timestamps outside an acceptable window (configured per deployment). DTLS and OSCORE provide their own replay protection at the transport layer via sequence numbers.

---

## 10. Protocol Stack Integration

*Detailed specification deferred to next iteration. Covers:*

- CoAP request/response mapping
- CoAP Observe for streaming telemetry
- OSCORE integration for annotation integrity
- MQTT transport (bridging to existing jsonld-ex MQTT module)
- Thread mesh networking considerations

*Reference: jsonld-ex `mqtt.py`, `cbor_ld.py`.*

---

## 11. Compression Analysis

*Detailed specification deferred to next iteration. Covers:*

- Information-theoretic bounds on annotation compression
- Comparison: JSON-LD vs CBOR-LD vs CBOR-LD-ex payload sizes
- Shannon entropy analysis of typical IoT compliance payloads
- Optimality gap analysis

---

## Appendix A: Gap Analysis Log

| Gap | Description | Status | Resolution Section |
|---|---|---|---|
| GAP-1 | Notation inconsistency (b,d,u,a) vs (l,v,u,a) | **Resolved** | §3.1, Definition 3 |
| GAP-2 | Multinomial opinion encoding | **Resolved** | §4.4, §5.1 (Tier 2 `has_multinomial` flag) |
| GAP-3 | Compliance operator identity in provenance | **Resolved** | §3.3, §5.2 (Table 2) |
| GAP-4 | Trust and attestation edge encoding | **Partial** | §3.4 (types defined), §6 (chain structure); full trust graph encoding deferred |
| GAP-5 | Graph topology encoding | **Open** | Deferred to §8 |
| GAP-6 | Quantization error through operators | **Resolved** | §4.5 (Theorems 4–5) |
| GAP-7 | Byzantine/adversarial resilience | **Resolved** | §9 (threat model, Byzantine fusion, chain integrity, transport security) |
| GAP-8 | Conditional opinion encoding | **Partial** | §6.3 (paired encoding for deduction); full CPT encoding deferred |
| GAP-9 | Temporal primitive mapping | **Resolved** | §7 (decay, triggers, delta encoding, temporal fusion, tier-specific behavior) |
| GAP-10 | Annotated graph operations | **Open** | Deferred to §8 |

---

## Appendix B: Precision Mode Quick Reference

The wire format transmits 3 values (b̂, d̂, â); û is derived by the decoder.

```
Bits  Mode   Binomial wire   Multinomial (k=4)  Use case
────────────────────────────────────────────────────────────
 8    00     3 bytes         9 bytes             Tier 1 default
16    01     6 bytes         16 bytes            Tier 2 fusion
32    10     12 bytes        30 bytes            Tier 3 / audit
 —    11     reserved        reserved            Future use
```

---

## Appendix C: Worked Example — Full Stack Encoding

**Scenario:** Temperature sensor reports 22.5°C as compliant with 85% belief.

### C.1 JSON-LD representation (~280 bytes)

```json
{
  "@context": "https://w3id.org/iot/compliance/v1",
  "@type": "TemperatureReading",
  "@id": "urn:sensor:temp-042",
  "value": 22.5,
  "unit": "Celsius",
  "observedAt": "2026-03-12T10:00:00Z",
  "@annotation": {
    "complianceStatus": "compliant",
    "opinion": {
      "belief": 0.85,
      "disbelief": 0.05,
      "uncertainty": 0.10,
      "baseRate": 0.50
    },
    "reasoningBackend": "subjective_logic"
  }
}
```

### C.2 CBOR-LD representation (~30–35 bytes)

Context-compressed integer keys, CBOR-encoded values. No annotation semantics — the opinion is just opaque data to a CBOR-LD parser.

### C.3 CBOR-LD-ex Tier 1 representation (4 bytes annotation)

```
Byte 0 (header):
  [00]  compliance_status = compliant
  [0]   delegation_flag = terminal
  [00]  origin_tier = constrained
  [1]   has_opinion = yes
  [00]  precision_mode = 8-bit

Byte 1: b̂ = Q_8(0.85) = round(0.85 × 255) = 217
Byte 2: d̂ = Q_8(0.05) = round(0.05 × 255) = 13
Byte 3: â = Q_8(0.50) = round(0.50 × 255) = 128

û is NOT transmitted. Decoder derives: û = 255 − 217 − 13 = 25
[Q_8⁻¹(25) = 0.098 ≈ 0.10 ✓]

Total annotation: 4 bytes (1 header + 3 opinion).
```

Wrapped in CBOR: `Tag(60000, <4-byte annotation>)` — approximately 7 bytes with CBOR framing.
Annotation map key: integer `60000` (3 CBOR bytes), not string `"@annotation"` (12 CBOR bytes).

**Total CBOR-LD-ex message: ~37–42 bytes** (CBOR-LD data + CBOR-LD-ex annotation).
**Compared to JSON-LD: ~280 bytes.**
**Compression ratio: ~85–87%.**

**Annotation-only comparison (same semantic content):**

| Encoding | Annotation size | Bit efficiency |
|---|---|---|
| JSON-LD text | ~148 bytes (1184 bits) | ~2.5% |
| CBOR-LD (integer keys, best effort) | ~49 bytes (392 bits) | ~7.6% |
| **CBOR-LD-ex (bit-packed)** | **4 bytes (32 bits)** | **93.0%** |

CBOR-LD-ex is >10× smaller than CBOR-LD for the same annotation content, and ~37× smaller than JSON-LD.

---

## References

- Jøsang, A. (2016). *Subjective Logic: A Formalism for Reasoning Under Uncertainty.* Springer.
- Bormann, C. and Hoffman, P. (2020). RFC 8949: Concise Binary Object Representation (CBOR). IETF.
- Shelby, Z. et al. (2014). RFC 7252: The Constrained Application Protocol (CoAP). IETF.
- Selander, G. et al. (2019). RFC 8613: Object Security for Constrained RESTful Environments (OSCORE). IETF.
- Rescorla, E. et al. (2022). RFC 9147: The Datagram Transport Layer Security (DTLS) Protocol Version 1.3. IETF.
- Longley, D. and Sporny, M. (2024). CBOR-LD. W3C Community Group Draft.
- Sporny, M. et al. (2020). JSON-LD 1.1. W3C Recommendation.
- Syed, M., Silaghi, M., Abujar, S., and Alssadi, R. (2026). A Compliance Algebra: Modeling Regulatory Uncertainty with Subjective Logic. Working paper.
- Syed, M. et al. (2026). jsonld-ex: JSON-LD 1.2 Extensions for AI/ML Data Exchange. PyPI.

---

*End of document. Next revision will address: §8 (Graph Operations), §10 (Protocol Stack Integration), §11 (Compression Analysis).*
