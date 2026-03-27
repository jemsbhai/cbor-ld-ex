# CBOR-LD-ex: Formal Data Model Specification

**Version:** 0.4.2-draft  
**Date:** 2026-03-27  
**Authors:** Muntaser Syed  
**Status:** Working Draft — Fifth review round fixes applied (v0.4.1 → v0.4.2)  
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

*Proof of (c):* We need `b̂ + d̂ ≤ 2ⁿ − 1`. Since `b + d ≤ 1` (because `u ≥ 0`), we have `b̂ ≤ round(b(2ⁿ − 1))` and `d̂ ≤ round(d(2ⁿ − 1))`. In the worst case, both round up by 0.5/(2ⁿ − 1), giving `b̂ + d̂ ≤ (b + d)(2ⁿ − 1) + 1 ≤ 2ⁿ − 1 + 1 = 2ⁿ`. However, we need `b̂ + d̂ ≤ 2ⁿ − 1`. This fails only when `b + d = 1` (i.e., `u = 0`) and both round up, which requires `b(2ⁿ − 1)` and `d(2ⁿ − 1)` to both have fractional part exactly 0.5. For `n ≥ 2`, this is a measure-zero edge case that the encoder MUST handle via symmetric clamping (see Remark below): decrement exactly one of b̂ or d̂ by 1, chosen by fractional-part comparison. ∎

**Remark (Symmetric Clamping Rule).** The encoder MUST enforce `û ≥ 0` by checking `b̂ + d̂ ≤ 2ⁿ − 1` after rounding. If violated, exactly one of b̂ or d̂ MUST be decremented by 1. The choice of which to decrement is determined by **fractional-part comparison**:

```
frac_b = b × (2ⁿ − 1) − floor(b × (2ⁿ − 1))
frac_d = d × (2ⁿ − 1) − floor(d × (2ⁿ − 1))
```

The component whose pre-rounding value had the **larger fractional part** (i.e., rounded up by more) is decremented. If `frac_b > frac_d`, decrement b̂. If `frac_d > frac_b`, decrement d̂.

**Tiebreaker (frac_b = frac_d):** This occurs only when b = d exactly (since b + d = 1 implies both have the same distance to their nearest quantization point). In this case, the clamping direction is determined by the **least significant bit of â**: if `â & 1 == 0`, decrement d̂; if `â & 1 == 1`, decrement b̂. This produces a deterministic, stateless alternation that washes out to zero net bias across opinions with varying base rates.

**Properties:** (a) The rule is **deterministic**: given (b, d, u, a, n), the output is unique. (b) The rule is **symmetric**: swapping b and d swaps the clamping target. Neither component has structural priority. (c) The clamping edge case triggers ONLY when b + d = 1 (u = 0) and both b·(2ⁿ−1) and d·(2ⁿ−1) have fractional part ≥ 0.5 — a measure-zero set in practice. (d) The tiebreaker uses â (already available) rather than an external timestamp, keeping the quantizer stateless and context-free.

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

The wire format transmits **3 values only** (b̂, d̂, â) for modes 00–10. The uncertainty component û is NEVER transmitted — it carries zero bits of Shannon information because û = (2ⁿ−1) − b̂ − d̂. The decoder derives it. Mode 11 (delta) transmits **2 values** (Δb̂, Δd̂) — the base rate â is unchanged from the previous message.

| Precision Mode | Code | Bits/value | Max error (b,d) | Max error (u) | Wire bytes |
|---|---|---|---|---|---|
| 8-bit full | 00 | 8 | ≈ 0.00196 | ≈ 0.00392 | 3 (b̂, d̂, â) |
| 16-bit full | 01 | 16 | ≈ 0.0000076 | ≈ 0.0000153 | 6 |
| 32-bit float | 10 | 32 | IEEE 754 | IEEE 754 | 12 |
| 8-bit delta | 11 | 8 | ≈ 0.00392 | ≈ 0.00784 | **2** (Δb̂, Δd̂) |

Delta mode error bounds are 2× full mode because the delta accumulates rounding error from both the original quantization and the delta quantization. Receivers MUST verify reconstructed values remain valid (§7.6).

### 4.4 Constrained Multinomial Quantization

`[GAP-2 RESOLVED]`

**Definition 11 (Constrained Multinomial Quantization).** Given a multinomial opinion `ω_X = (b⃗, u, a⃗)` over domain `X = {x₁, ..., xₖ}` with `∑ᵢ bᵢ + u = 1`, the constrained quantization `Q̃_n(ω_X)` is computed via integer simplex projection:

```
Step 1: Independently quantize ALL k+1 components:
          v = [round(b₁·M), ..., round(bₖ·M), round(u·M)]   where M = 2ⁿ − 1

Step 2: Apply integer simplex projection (Theorem 3c) to enforce sum(v) = M
          with all components ≥ 0.

Step 3: Assign: b̂ᵢ = v[i] for i = 1..k,  û = v[k+1]
```

**Wire transmission:** Only k−1 beliefs and û are transmitted. The k-th belief `b̂ₖ` is derived by the decoder as `b̂ₖ = M − (∑ᵢ₌₁ᵏ⁻¹ b̂ᵢ) − û`. This produces the same value as the encoder's projected `b̂ₖ` because `∑ᵢ₌₁ᵏ b̂ᵢ + û = M` by construction.

**Theorem 3 (Multinomial Quantization Constraint Preservation).**

**(a)** `∑ᵢ₌₁ᵏ b̂ᵢ + û = 2ⁿ − 1` (exact).

**(b)** `Q_n⁻¹(∑ᵢ b̂ᵢ) + Q_n⁻¹(û) = 1.0` (exact upon reconstruction).

**(c)** `b̂ᵢ ≥ 0` for all `i` and `û ≥ 0`, provided `n ≥ 2`. For `k + 1` independent roundings (`k` beliefs + `u`), the sum `∑ᵢ₌₁ᵏ round(bᵢ · M) + round(u · M)` may exceed `M = 2ⁿ − 1` by up to `⌊(k+1)/2⌋` quanta. The encoder MUST apply **integer simplex projection** to restore the sum constraint while keeping all components non-negative:

```
Integer Simplex Projection for Multinomial:

Step 1: Quantize ALL k+1 components independently:
          v = [round(b₁·M), ..., round(bₖ·M), round(u·M)]

Step 2: Compute excess = sum(v) − M

Step 3: Compute the fractional parts of each pre-rounding value:
          frac[i] = (x_i · M) − floor(x_i · M)   for each component x_i

Step 4: IF excess > 0 (over-budget — need to subtract):
          Sort components by frac[i] DESCENDING (largest roundup first).
          Decrement the top `excess` components by 1, each at most once.

        IF excess < 0 (under-budget — need to add):
          Sort components by frac[i] ASCENDING (largest rounddown first).
          Increment the top |excess| components by 1, each at most once.

Step 5: Assign: b̂ᵢ = v[i] for i=1..k, û = v[k+1].
```

**Properties:**

**(i)** Each component is adjusted by at most ±1. No component is adjusted twice.

**(ii)** Components that rounded up the most are corrected first, minimizing total distortion.

**(iii)** No component goes negative: when excess > 0, we only decrement components that rounded UP (frac ≥ 0.5), meaning their quantized value is ≥ 1 (since round(x·M) ≥ 1 when x·M ≥ 0.5, which is true for any x > 0 at reasonable precision). Decrementing from ≥ 1 yields ≥ 0.

**(iv)** The algorithm terminates in O(k log k) time (dominated by the sort). No iterative loop.

**(v)** `∑ᵢ b̂ᵢ + û = M` exactly after projection (by construction: we removed/added exactly `|excess|` quanta).

This replaces the iterative decrement loop from v0.4.1, which had a flaw: fractional parts are static, so the loop always targeted the same component, potentially driving it negative for k > 2.

*Proof:* The excess is bounded by `⌊(k+1)/2⌋` (each of k+1 roundings contributes at most +0.5). The projection applies exactly |excess| corrections of ±1 to distinct components, restoring the sum. Non-negativity holds by property (iii). ∎

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

If `has_opinion = 1`: opinion payload follows per `precision_mode`. When `precision_mode = 11` (delta mode), the opinion payload is 2 bytes (Δb̂, Δd̂) instead of the standard 3 bytes (b̂, d̂, â) — see §7.6.

Typical Tier 1 message: **4 bytes** (1 header + 3 opinion at 8-bit full; û not transmitted).
Minimum Tier 1 message with opinion: **3 bytes** (1 header + 2 delta opinion at 8-bit delta).
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
  [2 bits] trust_precision_mode    (00=8-bit, 01=16-bit, 10=32-bit, 11=reserved)
  [6 bits] reserved
  [variable] trust opinion (per trust_precision_mode — independent of header precision_mode)
```

The trust opinion's precision is specified by its own `trust_precision_mode` field, NOT inherited from the header's `precision_mode`. This allows a gateway to carry a high-precision primary opinion (e.g., 16-bit fused result) alongside a lower-precision trust weight (e.g., 8-bit). The 1-byte overhead for the precision+reserved field is negligible at Tier 3.

**Extension Block Ordering (Mandatory).** When multiple extension blocks are present in a Tier 3 annotation, they MUST appear in the following strict order within the annotation byte string:

```
[header: 4 bytes]
[opinion: per precision_mode]
[1. extended_context: 4 bytes, if has_extended_context = 1]
[2. provenance_chain: variable, if has_provenance_chain = 1]
[3. trust_info: variable, if has_trust_info = 1]
[4. temporal_block: remaining bytes, if present — see §7.4]
```

This ordering is NOT negotiable. Implementations MUST serialize and parse blocks in this exact sequence. Blocks 1–3 each contain their own length indicators (extended_context is fixed at 4 bytes; provenance_chain has a chain_length byte; trust_info has an agent_id_length byte). The temporal block (§7.4) uses the "remaining bytes" detection mechanism and therefore MUST be the final block. Future extension block types MUST be assigned a position BEFORE the temporal block and MUST carry their own length indicators.

**Tier 3 Byte 3 (reserved/flags) allocation:**

```
Bit  Width  Field
───────────────────────────
0    1      has_extended_digest        (0 = 64-bit digest, 1 = 128-bit; see §9.4)
1    7      reserved
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

**Consequence for Axiom 3:** Quantized decay applies the factor to quantized components. Given `b̂, d̂` in the quantized domain, the encoder computes `b̂' = round(λ · Q_n⁻¹(b̂) · (2ⁿ−1))`, `d̂' = round(λ · Q_n⁻¹(d̂) · (2ⁿ−1))`. Since both roundings can independently round up, `b̂' + d̂'` may exceed `2ⁿ−1` — the same edge case as Theorem 1(c). The **Symmetric Clamping Rule (§4.2) MUST be applied** to `b̂'` and `d̂'` before deriving `û' = (2ⁿ−1) − b̂' − d̂'`. With clamping applied, the SL constraint is preserved by construction. This applies equally to the expiry trigger (§7.5, Definition 19), whose outputs `b' = round(γ · b̂)` and `d' = round(d̂ + (1−γ) · b̂)` are also subject to rounding overshoot.

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

The temporal annotation `τ(w, λ, triggers)` from Definition 6 is encoded as a **bit-packed extension block** appended after `[header][opinion]` in the annotation byte string. The extension block is NOT a separate CBOR structure — it occupies the remaining bytes of the annotation payload.

**Detection mechanism:** Extensions are detected by **remaining bytes** after all preceding content has been parsed. For Tier 1 and Tier 2, this is after the header and opinion. For Tier 3, this is after the header, opinion, and any preceding extension blocks (extended_context → provenance_chain → trust_info, per §5.1 Extension Block Ordering). The temporal block, when present, is always the **final block** in the annotation byte string. The opinion size is deterministic from `precision_mode` (3/6/12/2 bytes for 8-bit/16-bit/32-bit/delta modes respectively). This design achieves **zero overhead when extensions are absent** — no flags byte is needed.

**Bit-packed layout (MSB-first):**

The extension block is written using a BitWriter that packs fields at the bit level, padding to a byte boundary at the end with zero bits.

```
Bit  Width  Field
───────────────────────────
0    1      has_temporal            (0 = no temporal metadata, 1 = temporal follows)
1    1      has_triggers            (0 = no triggers, 1 = triggers follow)

IF has_temporal = 1:
  2    2      decay_function_id    (Table 3: 00=exponential, 01=linear, 10=step)
  4    8      half_life_encoded    (log-scale, see below)

IF has_triggers = 1:
  N    3      trigger_count        (1–7 triggers; 0 reserved)
  For each trigger:
    N    2      trigger_type       (Table 4)
    IF trigger_type ∈ {expiry, review_due}:
      N    8    trigger_parameter  (Q8 payload: γ for expiry, acceleration for review)
    IF trigger_type ∈ {reg_change, withdrawal}:
      (no payload — 0 bits)

Pad to byte boundary with zero bits.
```

**Log-scale half-life encoding (8 bits, Definition 22).**

The half-life parameter `τ` is encoded using a logarithmic scale:

```
seconds = 2^(value × 25 / 255)
```

This maps the 8-bit value `[0, 255]` to a range of approximately 1 second to 388 days, with each quantization step representing approximately 7% change — perceptually uniform on a log scale. Shannon efficiency is 100% (`log₂(256) / 8 = 1.0`): every bit carries one bit of information.

| Encoded value | Half-life | Use case |
|---|---|---|
| 0 | ~1 second | Real-time sensor freshness |
| 50 | ~53 seconds | Sub-minute IoT polling |
| 122 | ~1 hour | Standard gateway aggregation |
| 184 | ~1 day | Daily compliance reporting |
| 255 | ~388 days | Long-term regulatory retention |

The encoder computes: `value = round(log₂(seconds) × 255 / 25)`, clamped to `[0, 255]`.

**Tier 1 temporal encoding (minimal):**

Tier 1 devices do not encode temporal metadata in the annotation header. Timestamps are carried in the CBOR-LD data payload (e.g., as `observedAt` fields) and are available to the receiving gateway for temporal fusion. This is a deliberate design choice: every byte of header on a constrained device is a byte not available for sensor data. However, Tier 1 annotations MAY carry extension blocks — extensions are tier-independent since they are detected by remaining bytes.

**Extension block size examples:**

| Configuration | Bits | Bytes (padded) |
|---|---|---|
| Temporal only (no triggers) | 2 + 2 + 8 = 12 | 2 |
| Temporal + 1 expiry trigger | 12 + 3 + 2 + 8 = 25 | 4 |
| Temporal + 2 triggers (expiry + withdrawal) | 12 + 3 + (2+8) + (2+0) = 27 | 4 |
| Triggers only (no temporal) | 2 + 3 + trigger_bits | varies |
| No extensions | 0 | 0 |

**Minimum temporal overhead:** 2 bytes (temporal metadata, no triggers).
**Zero cost when absent:** 0 bytes — the annotation byte string simply ends after the opinion.

### 7.5 Compliance Trigger Types

Three discrete trigger types cause state changes that continuous decay cannot model. These map directly to Syed et al. (2026) §8.2.

**Table 4: Trigger type identifiers (2-bit field).**

| Code | Trigger | Semantic | Wire payload | Effect on opinion |
|---|---|---|---|---|
| 00 | expiry | Hard/soft deadline (Art. 5(1)(e)) | 8 bits (γ Q8) | Transfers `b → d` by factor `(1−γ)` |
| 01 | review_due | Missed mandatory review (Art. 35(11), 45(3)) | 8 bits (accel. Q8) | Accelerates decay rate |
| 10 | regulatory_change | Legal framework change | 0 bits | Signals opinion replacement |
| 11 | withdrawal | Consent withdrawal (Art. 7(3)) | 0 bits | Signals full opinion invalidation |

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

**Definition 22 (Delta Opinion).** When the compliance status is unchanged from the previous message, a Tier 1 device MAY send a **delta opinion** by setting `precision_mode = 11` (delta mode) in the header. This signals to the parser that the opinion payload is 2 bytes (Δb̂, Δd̂) rather than the standard 3 bytes (b̂, d̂, â). The base rate â is unchanged from the previous message and is NOT retransmitted. The delta encoding transmits:

```
[1 byte]  Δb̂ (signed 8-bit: change in quantized belief, range −128 to +127)
[1 byte]  Δd̂ (signed 8-bit: change in quantized disbelief)
```

The receiver reconstructs: `b̂_new = b̂_prev + Δb̂`, `d̂_new = d̂_prev + Δd̂`, `û_new = (2ⁿ−1) − b̂_new − d̂_new`.

**Encoder range check (mandatory fallback):** Before encoding a delta opinion, the encoder MUST verify that both deltas fit in a signed 8-bit integer: `|Δb̂| ≤ 127` AND `|Δd̂| ≤ 127`. If either delta exceeds this range (e.g., a step-change from b̂ = 220 to b̂ = 50 produces Δb̂ = −170, which overflows int8), the encoder MUST abandon delta mode for that message and transmit a full opinion using `precision_mode = 00`. This fallback is transparent to the receiver — it simply sees a full-mode message and updates its state accordingly. Delta mode resumes on the next message if the deltas are within range.

**Constraint:** The receiver MUST verify `b̂_new, d̂_new ≥ 0` and `b̂_new + d̂_new ≤ 2ⁿ−1`. If violated, the delta is invalid and the receiver MUST request a full opinion retransmission (via CoAP RST or application-level NACK).

**Savings:** Delta encoding reduces the opinion payload from 3 bytes (full) to 2 bytes (delta) when changes are small. For a sensor reporting every 5 seconds with slowly-changing conditions, this halves the annotation bandwidth.

**Keyframe-first mandate:** A Tier 1 device MUST NOT transmit a delta opinion (`precision_mode = 11`) unless it has previously transmitted at least one full opinion (`precision_mode = 00`, `01`, or `10`) within the current session or connection. The first message after device boot, power cycle, or network join MUST be a full opinion to establish the baseline state (b̂_prev, d̂_prev, â) at the receiver. A receiver that receives a delta opinion without a prior baseline MUST discard it and request a full opinion retransmission.

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

**Definition 27 (Standard Provenance Entry — 16 bytes).** For Class 1/2 threat environments (Tier 1 → Tier 2 pipelines, benign failures, Byzantine faults). Each entry is a **fixed 16-byte (128-bit) structure with zero waste**:

```
Byte 0:     [origin_tier:2][operator_id:4][precision_mode:2]
Bytes 1-3:  b̂, d̂, â (3 × uint8 opinion — û NOT stored, derived)
Bytes 4-7:  timestamp (uint32, big-endian, seconds since Unix epoch)
Bytes 8-15: prev_digest (64-bit truncated SHA-256 of previous entry)
```

**Bit budget:** 2 + 4 + 2 + 8 + 8 + 8 + 32 + 64 = 128 bits = 16 bytes. Zero waste — every bit carries information.

**Definition 27b (Audit-Grade Provenance Entry — 24 bytes).** For Class 3 threat environments (Tier 3 cloud, public networks, regulatory audit):

```
Byte 0:     [origin_tier:2][operator_id:4][precision_mode:2]
Bytes 1-3:  b̂, d̂, â (3 × uint8 opinion — û NOT stored, derived)
Bytes 4-7:  timestamp (uint32, big-endian, seconds since Unix epoch)
Bytes 8-23: prev_digest (128-bit truncated SHA-256 of previous entry)
```

**192 bits = 24 bytes.** The additional 8 bytes provide 128-bit second pre-image resistance and birthday-bound collision resistance of 2⁶⁴ — meeting the minimum security level for modern cryptographic protocols (NIST SP 800-57).

**Format signaling:** The `has_extended_digest` flag in Tier 3 byte 3 (§5.1) selects the entry format. When `has_extended_digest = 0`: standard 16-byte entries. When `has_extended_digest = 1`: audit-grade 24-byte entries.

**Design rationale:**
- û is NOT stored — it is derived as `255 − b̂ − d̂` (Axiom 3). Storing û would waste 8 bits per entry.
- Opinion is fixed at 8-bit precision (3 bytes). This is sufficient for provenance audit purposes; the full-precision opinion is in the annotation itself.
- The `prev_digest` field creates a hash chain without requiring shared symmetric keys (unlike HMAC-based approaches). Any party with access to the chain can verify its integrity.

**Definition 28 (Chained Digest).** The digest linking entry `eᵢ` to entry `eᵢ₊₁` is:

```
Standard:    prev_digest(eᵢ₊₁) = truncate(SHA-256(serialize(eᵢ)), 64 bits)
Audit-grade: prev_digest(eᵢ₊₁) = truncate(SHA-256(serialize(eᵢ)), 128 bits)
```

Where `serialize(eᵢ)` is the wire encoding of entry `eᵢ` (16 or 24 bytes) and `truncate` takes the first 8 or 16 bytes of the SHA-256 output respectively. All entries in a single chain MUST use the same format.

**Chain origin sentinel:** The first entry in a chain uses `prev_digest` as all zero bytes (8 zero bytes for standard, 16 zero bytes for audit-grade). This sentinel allows the verifier to detect chain truncation: if the first entry does not have the sentinel, entries have been removed from the front of the chain.

**Tamper detection properties:**

1. **Modification:** Modifying entry `eⱼ` changes `serialize(eⱼ)`, which invalidates `prev_digest(eⱼ₊₁)` and all subsequent entries. The verifier detects this at entry `j+1`.

2. **Truncation (front):** Removing entries from the beginning is detected because the new first entry will not have the chain origin sentinel.

3. **Truncation (back):** Removing entries from the end is detectable only if the expected chain length is known from external context (e.g., `source_count` in the Tier 2 header).

4. **Insertion:** Inserting a forged entry between `eⱼ` and `eⱼ₊₁` requires computing `SHA-256(serialize(eⱼ))`, which is feasible (SHA-256 is not keyed). Chained digests provide tamper **evidence**, not tamper **prevention**. For tamper prevention, use transport-layer security (§9.5).

**Collision resistance:** Standard entries (16 bytes) use 64-bit truncated SHA-256, providing second pre-image resistance of 2⁶⁴ and birthday-bound collision resistance of 2³². This is sufficient for Class 1/2 threat environments (§9.1): accidental corruption, hardware faults, and Byzantine-faulty minorities. For Class 3 threat environments (active adversaries on public networks), deployments MUST use audit-grade entries (24 bytes) with 128-bit truncated SHA-256, providing second pre-image resistance of 2¹²⁸ and collision resistance of 2⁶⁴. The `has_extended_digest` flag in the Tier 3 header (§5.1) signals which format is in use.

**Space cost comparison (8-sensor pipeline, 1 fusion step = 9 entries):**

| Format | Entry size | Chain cost | Threat class |
|---|---|---|---|
| Standard | 16 bytes | 144 bytes | Class 1/2 (second pre-image 2⁶⁴) |
| Audit-grade | 24 bytes | 216 bytes | Class 3 (second pre-image 2¹²⁸) |

**Chain verification algorithm:**

```
verify_chain(entries):
  if entries[0].prev_digest ≠ SENTINEL: return (false, 0)
  for i in 1..len(entries)-1:
    expected = truncate(SHA-256(serialize(entries[i-1])), 64)
    if entries[i].prev_digest ≠ expected: return (false, i)
  return (true, -1)
```

Returns `(is_valid, error_index)` where `error_index` is the first invalid entry, or `-1` if the chain is valid.

**Space cost:** A standard provenance chain with `n` entries costs exactly `16n` bytes; an audit-grade chain costs `24n` bytes. For a typical Tier 1 → Tier 2 pipeline with 8 sensors and 1 fusion step: 144 bytes (standard) or 216 bytes (audit-grade). Chains are carried at Tier 3 only — Tier 1 and Tier 2 do not transmit chains (§6.2).

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

CBOR-LD-ex is transport-agnostic: the annotation payload is identical regardless of the transport protocol. Transport-specific metadata (MQTT topics, CoAP content-format options) is derived from the annotation but is NOT part of the CBOR-LD-ex payload itself.

*Reference: jsonld-ex `mqtt.py`, `cbor_ld.py`; CBOR-LD-ex `transport.py`.*

### 10.1 Transport-Agnostic Payload Principle

**Invariant:** For any CBOR-LD-ex message `M`, the payload carried over MQTT and the payload carried over CoAP MUST be byte-identical:

```
mqtt_payload(M) == coap_payload(M) == encode(doc, annotation, registry)
```

This is verified by test for all scenarios in the benchmark suite (406 tests). The transport layer adds protocol-specific routing metadata but MUST NOT alter, wrap, or re-encode the CBOR-LD-ex payload.

### 10.2 MQTT Transport

MQTT (v3.1.1 or v5.0) is the primary transport for Tier 1 → Tier 2 communication in most IoT deployments.

**Payload:** The MQTT PUBLISH payload is the raw CBOR-LD-ex codec output — no MQTT-specific framing. The subscriber receives the exact bytes produced by `encode()`.

**Topic derivation:** MQTT topics are derived from the document metadata and annotation:

```
topic = {prefix}/{@type_local}/{@id_fragment}/{compliance_status}
```

Where:
- `prefix` defaults to `cbor-ld-ex`
- `@type_local` is the local name of the `@type` IRI (e.g., `TemperatureReading`)
- `@id_fragment` is the local part of `@id` (e.g., `temp-042` from `urn:sensor:temp-042`)
- `compliance_status` is one of `compliant`, `non_compliant`, `insufficient`

Example: `cbor-ld-ex/TemperatureReading/temp-042/compliant`

The compliance status in the topic enables topic-based filtering at the MQTT broker level — subscribers can filter by compliance state without payload inspection, which is cheaper on constrained brokers.

**QoS derivation:** MQTT QoS level is derived from the annotation’s projected probability `P(ω) = b + a·u`:

| Projected probability | QoS | Rationale |
|---|---|---|
| P(ω) ≥ 0.9 | 2 (exactly once) | High confidence → critical, must not be lost |
| 0.5 ≤ P(ω) < 0.9 | 1 (at least once) | Normal confidence → standard delivery |
| P(ω) < 0.5 | 0 (at most once) | Low confidence → best-effort, acceptable to lose |

If no opinion is present, QoS defaults to 1.

### 10.3 CoAP Transport

CoAP (RFC 7252) is the primary transport for constrained networks (802.15.4, Thread, 6LoWPAN).

**Payload:** The CoAP response payload is the raw CBOR-LD-ex codec output — identical to the MQTT payload.

**Content-Format:** CBOR-LD-ex uses Content-Format ID `60000` (experimental range, matching the CBOR tag number):

```
Content-Format: 60000
```

Standard CBOR is Content-Format `60`. CBOR-LD does not yet have an assigned Content-Format ID. The experimental ID `60000` is used for development and hackathon purposes.

**802.15.4 frame fit:** The 802.15.4 MAC layer has an MTU of 127 bytes. With a minimal CoAP NON message overhead of approximately 16 bytes, the maximum CBOR-LD-ex payload that fits in a single frame is:

```
max_payload = 127 − 16 = 111 bytes
```

All Tier 1 CBOR-LD-ex messages in the benchmark suite fit within this limit (verified by test). Tier 2 messages with large provenance chains may require CoAP blockwise transfer (RFC 7959).

### 10.4 Transport Security

CBOR-LD-ex does NOT define its own transport encryption. It relies on existing IoT security protocols:

| Tier path | Minimum security | Recommended |
|---|---|---|
| Tier 1 → Tier 2 | Pre-shared key DTLS | OSCORE with group keys (RFC 8613) |
| Tier 2 → Tier 3 | DTLS 1.3 with certificates | OSCORE + provenance chain |
| Tier 3 internal | TLS 1.3 | TLS 1.3 + provenance chain |

OSCORE (RFC 8613) is particularly well-suited because it provides end-to-end security at the application layer, surviving CoAP proxies. The CBOR-LD-ex annotation is integrity-protected even through untrusted intermediaries.

---

## 11. Compression Analysis

This section presents the information-theoretic foundation for CBOR-LD-ex’s compression claims and summarizes the benchmark results. All numbers are independently verified by the test suite (406 tests, 38 benchmark scenarios).

*Reference: Shannon (1948); CBOR-LD-ex `codec.py::annotation_information_bits()`, `transport.py::full_benchmark()`, `benchmarks/cbor_ld_ex_benchmark/`.*

### 11.1 The û-Elision Insight

The single most important compression insight in CBOR-LD-ex is that the uncertainty component û carries **zero bits of Shannon information**.

Given Axiom 3: `b̂ + d̂ + û = 2ⁿ − 1`, the value of û is fully determined by b̂ and d̂. Transmitting û would transmit zero additional information — it is a derived value, not an independent variable.

By eliding û from the wire format, each opinion tuple saves one value width:

| Precision | With û (4 values) | Without û (3 values) | Savings |
|---|---|---|---|
| 8-bit | 4 bytes | 3 bytes | 25% |
| 16-bit | 8 bytes | 6 bytes | 25% |
| 32-bit | 16 bytes | 12 bytes | 25% |

This is not an optimization — it is information-theoretically correct. Transmitting a derived value is a protocol design error.

### 11.2 Shannon Information Content of Annotations

For each header field, the Shannon information content is `H(field) = log₂(number_of_valid_states)` bits. Fields whose state count is a power of 2 are perfectly packed; fields with non-power-of-2 state counts have unavoidable waste bits.

**Tier 1 header (1 byte = 8 wire bits):**

| Field | States | H (bits) | Wire bits | Efficiency |
|---|---|---|---|---|
| compliance_status | 3 | 1.585 | 2 | 79.2% |
| delegation_flag | 2 | 1.000 | 1 | 100% |
| origin_tier | 3 | 1.585 | 2 | 79.2% |
| has_opinion | 2 | 1.000 | 1 | 100% |
| precision_mode | 4 | 2.000 | 2 | **100%** |
| **Total header** | | **7.170** | **8** | **89.6%** |

**8-bit opinion payload (3 wire bytes = 24 wire bits):**

The opinion (b̂, d̂, â) is constrained: b̂ + d̂ ≤ 255. The number of valid (b̂, d̂) pairs is `∑ₛ₌₀²⁵⁵ (s+1) = 256 × 257 / 2 = 32,896`. The Shannon information is `log₂(32,896) ≈ 15.006` bits for (b̂, d̂) jointly, plus `log₂(256) = 8` bits for â, totaling `23.006` bits in 24 wire bits = **95.9% efficiency**.

**Tier 1 full annotation total (4 bytes = 32 wire bits):**

`7.170 + 23.006 = 30.176` Shannon bits in 32 wire bits = **94.3% bit efficiency**.

**Tier 1 delta annotation total (3 bytes = 24 wire bits):**

The delta opinion (Δb̂, Δd̂) carries `log₂(256) + log₂(256) = 16` bits of information in 16 wire bits = 100% efficiency. Combined with the header:

`7.170 + 16.000 = 23.170` Shannon bits in 24 wire bits = **96.5% bit efficiency**.

Delta mode achieves the highest annotation bit efficiency of any CBOR-LD-ex encoding — both smaller (3 bytes vs. 4 bytes) AND higher information density (96.5% vs. 94.3%) than full 8-bit mode.

### 11.3 Six-Way Benchmark

The benchmark compares six encoding strategies for the same IoT document + annotation:

1. **JSON-LD** — raw JSON text, annotation as JSON object
2. **jsonld-ex CBOR-LD** — context-only compression (context URLs → integers), no annotation
3. **Our CBOR-LD (data only)** — full key+value compression, no annotation
4. **jsonld-ex CBOR-LD + annotation** — annotation embedded as JSON inside CBOR
5. **Our CBOR-LD + CBOR annotation** — same info as CBOR-LD-ex, CBOR key-value encoding
6. **CBOR-LD-ex** — bit-packed annotation with Tag(60000)

Across 38 scenarios (4 document profiles × 12 annotation configurations, realistic pairing):

| Encoding | Min (B) | Max (B) | Mean (B) | Median (B) |
|---|---|---|---|---|
| JSON-LD | 295 | 673 | 469 | 426 |
| jsonld-ex CBOR-LD | 88 | 349 | 183 | 166 |
| Our CBOR-LD (data only) | 44 | 125 | 80 | 85 |
| jsonld-ex CBOR-LD + annotation | 245 | 553 | 368 | 327 |
| Our CBOR-LD + CBOR annotation | 92 | 179 | 131 | 133 |
| CBOR-LD-ex (bit-packed) | 55 | 148 | 95 | 99 |

### 11.4 Verified Claims

The following claims are verified as **universal invariants** over all 38 benchmark scenarios (falsification of any single scenario would retract the claim):

1. **CBOR-LD-ex < JSON-LD** for all scenarios. Geometric mean compression: 79.8%.
2. **CBOR-LD-ex < CBOR-LD + annotation** (same semantic content) for all scenarios. Bit-packing always wins over CBOR’s self-describing encoding for fixed-schema protocol metadata.
3. **Our key+value compression ≤ jsonld-ex context-only compression** for all scenarios. Full `ContextRegistry` beats context-URL-only compression.
4. **CBOR-LD-ex carries MORE semantic fields while being SMALLER** than data-only encodings.
5. **All Tier 1 payloads fit a single 802.15.4 frame** (≤ 111 bytes after CoAP overhead).
6. **Tier 1 annotation bit efficiency > 70%** for all scenarios (actual: 94.3% for 8-bit full, 96.5% for 8-bit delta).
7. **Full size ordering: CBOR-LD-ex < our+ann ≤ jex+ann < JSON-LD** for all scenarios.
8. **Shannon information ≤ wire bits** for all annotations (physical law).
9. **û never transmitted on wire** (verified by annotation size arithmetic for all scenarios).

### 11.5 Compression Ratio Summary

| Metric | Value |
|---|---|
| Geometric mean compression vs JSON-LD | 79.8% |
| Best case | 85.4% (minimal document, Tier 2, 8-bit) |
| Worst case | 72.1% (medium document, Tier 1, 32-bit) |
| Tier 1 annotation overhead (full) | 4 bytes (8-bit) to 13 bytes (32-bit) |
| Tier 1 annotation overhead (delta) | **3 bytes** |
| Annotation bit efficiency (Tier 1, 8-bit full) | **94.3%** |
| Annotation bit efficiency (Tier 1, 8-bit delta) | **96.5%** |
| Header bit efficiency | **89.6%** |
| precision_mode utilization | **100%** (4/4 states) |
| Annotation ratio: JSON-LD / CBOR-LD-ex | ~37× (full), ~93× (delta) |
| Annotation ratio: CBOR-LD / CBOR-LD-ex | ~10× |

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

The wire format transmits 3 values (b̂, d̂, â) for modes 00–10; û is derived by the decoder. Mode 11 (delta) transmits 2 values (Δb̂, Δd̂); â and û are derived from state.

```
Bits  Mode    Code  Binomial wire   Use case
──────────────────────────────────────────────────────
 8    full    00    3 bytes         Tier 1 default / keyframe
16    full    01    6 bytes         Tier 2 fusion
32    float   10    12 bytes        Tier 3 / audit
 8    delta   11    2 bytes         Time-series streaming (stateful)
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
| **CBOR-LD-ex (bit-packed, full)** | **4 bytes (32 bits)** | **94.3%** |
| **CBOR-LD-ex (bit-packed, delta)** | **3 bytes (24 bits)** | **96.5%** |

CBOR-LD-ex is >10× smaller than CBOR-LD for the same annotation content, and ~37× smaller than JSON-LD (full mode) or ~49× smaller (delta mode).

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

*End of document. v0.4.2 changes: integer simplex projection replaces broken iterative clamping for multinomial (§4.4 Theorem 3c, Definition 11), keyframe-first mandate for delta cold start (§7.6). v0.4.1 changes: delta-to-full fallback on range overflow (§7.6), trust_precision_mode field in trust_info block (§5.1), symmetric clamping mandate for temporal decay and expiry trigger outputs (§7.1). v0.4.0 changes: symmetric clamping (§4.2), delta mode via precision_mode=11 (§5.1, §7.6, Table 1, Appendix B), mandatory Tier 3 extension block ordering (§5.1), tiered provenance digest security with 128-bit audit-grade option (§9.4), updated Shannon efficiency analysis (§11.2, §11.5). Next revision will address: §8 (Graph Operations), Phase 0 TurboQuant integration (§4.6–4.9).*
