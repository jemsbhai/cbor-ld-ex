# Syndrome-Based Tamper Localization for Compact IoT Provenance Chains

**Version:** 0.1.0-draft
**Date:** 2026-03-24
**Authors:** Muntaser Syed
**Status:** Verified — Pre-publication Draft
**Parent Project:** CBOR-LD-ex (Compact Binary Linked Data with Semantic Reasoning)

---

## Abstract

We present a novel integrity mechanism for IoT provenance chains that separates tamper *detection* from tamper *localization*, achieving 52% compression over conventional chained-hash approaches while preserving single-entry tamper localization. The mechanism uses GF(2⁸) syndrome computation (inspired by Reed-Solomon error locating) to encode positional information in O(1) fixed overhead, replacing the O(n) per-entry digest overhead of chained SHA-256. Detection is handled by a single chain-level SHA-256 digest (64-bit truncated), while localization is handled by 2t syndrome bytes where t is the configurable number of simultaneously localizable tampered entries. The approach has been exhaustively verified across all 32,640 possible (chain_length, tamper_position) pairs for chain lengths 1–255.

---

## 1. Motivation

### 1.1 The Problem

IoT provenance chains record the processing history of sensor data as it traverses a tiered architecture (sensor → gateway → cloud). Each entry in the chain records what operation was performed, by which tier, with what opinion (confidence), and when. The chain serves as an audit trail for compliance verification.

The standard integrity mechanism is **chained hashing**: each entry carries a truncated hash of the previous entry, forming a hash chain. This provides both tamper detection and tamper localization — if entry j is modified, the hash stored in entry j+1 becomes invalid, pinpointing the tampering.

**The cost problem:** Chained hashing conflates detection and localization into a single mechanism, paying O(n) overhead for both. For a chain of n entries with 64-bit truncated SHA-256 digests:

- Per-entry data payload: 6 bytes (48 bits)
- Per-entry digest: 8 bytes (64 bits)
- **Digest overhead: 57% of every entry**

For constrained IoT systems where provenance chains may contain 10–100 entries, this overhead is significant — particularly at Tier 3 (cloud) systems that aggregate chains from thousands of devices.

### 1.2 The Insight

Detection and localization serve different purposes and have different information-theoretic requirements:

- **Detection** (was anything modified?): Requires O(1) information — a single digest over the entire chain. Binary answer.
- **Localization** (which entry was modified?): Requires O(log n) information — enough to identify one of n positions. For n ≤ 255: 8 bits minimum.

Chained hashing provides O(n) information (one digest per entry) for a task that requires at most O(log n). The excess is structural waste inherent to the chained-hash design.

### 1.3 The Approach

We decouple detection from localization:

1. **Detection:** A single chain-level `truncate(SHA-256(chain_bytes), 64 bits)` digest. 8 bytes fixed, regardless of chain length. Provides 2⁶⁴ second-preimage resistance.

2. **Localization:** GF(2⁸) syndrome bytes computed over CRC-8 fingerprints of each entry, position-weighted by powers of a primitive element. 2t bytes for t-entry localization capability. Fixed overhead, independent of chain length.

---

## 2. Mathematical Foundation

### 2.1 Galois Field GF(2⁸)

**Definition 1 (Field construction).** GF(2⁸) is constructed as GF(2)[x] / p(x), where:

```
p(x) = x⁸ + x⁴ + x³ + x + 1    (0x11B, the AES/Rijndael polynomial)
```

This is an irreducible polynomial over GF(2), producing a field of 256 elements (0x00–0xFF). Addition is bitwise XOR. Multiplication is polynomial multiplication modulo p(x).

**Rationale for polynomial choice:** 0x11B is the most widely implemented GF(2⁸) polynomial, used in AES, libsodium, and hardware crypto accelerators. Existing constant-time implementations and lookup tables are universally available. No advantage to non-standard polynomials.

**Definition 2 (Primitive element).** α = 0x03 is a primitive element of GF(2⁸) mod 0x11B.

**Verified property:** {α⁰, α¹, ..., α²⁵⁴} = GF(2⁸) \ {0}. That is, α generates all 255 non-zero elements. α²⁵⁵ = α⁰ = 1.

**Critical note:** α = 0x02 is NOT primitive for this polynomial — it has order 51 (a proper subgroup, since 51 | 255 = 3×5×17). Using 0x02 would cause localization to fail silently for chain positions outside the 51-element subgroup. This was caught during verification and corrected. The primitivity of α is a **load-bearing assumption** — the localization proof depends on the bijectivity of the power map over {0, ..., 254}.

**Verification status:** Exhaustively verified. All 255 non-zero elements generated. α²⁵⁵ = 1 confirmed. Field axioms (commutativity, associativity, identity, inverse, distributivity, zero) verified over 30,000+ random samples plus exhaustive checks for identity (256 elements), inverse (255 elements), and zero (256 elements).

### 2.2 Entry Hash Function

**Definition 3 (Entry fingerprint).** h: {6-byte entry} → GF(2⁸) is CRC-8/CCITT with generator polynomial g(x) = x⁸ + x² + x + 1 (0x07).

**Rationale for CRC-8 over alternatives:**

| Function | Collision rate (random) | Structured weakness? | Compute cost |
|---|---|---|---|
| XOR-fold (e[0]⊕...⊕e[5]) | 1/256 | **YES** — same-bit flips in 2 bytes cancel | 5 XOR |
| CRC-8/CCITT (0x07) | See §2.2.1 | Minimal — guaranteed detection properties | Table lookup |
| GF polynomial (⊕ᵢ αⁱ⊗e[i]) | 1/256 | Minimal | 5 GF muls |

XOR-fold is ruled out due to trivially constructible blind spots. CRC-8 provides guaranteed detection of all single-bit errors and strong detection of multi-bit errors, with lower computational cost than GF polynomial evaluation.

**Implementation:** 256-byte lookup table, byte-at-a-time processing. Total static storage: 256 bytes.

**Verification status:** All 48 possible single-bit flips in a 6-byte entry are detected (guaranteed by polynomial construction).

#### 2.2.1 Collision Rate Analysis (Corrected)

**Theoretical prediction:** For uniformly random 6-byte modifications, P(h(e) = h(e')) = 1/256 ≈ 0.39%.

**Empirical measurement:** Zero collisions observed in 99,324 trials using 1–3 bit flips.

**Resolution — two collision regimes:**

The 1/256 figure applies to **arbitrary random entry replacement** (uniformly random 48-bit strings). For **small perturbations** (bit flips), CRC-8/CCITT provides much stronger guarantees:

| Modification type | Theoretical collision rate | Empirical (99K trials) |
|---|---|---|
| Single-bit flip | **0** (guaranteed by polynomial) | 0 |
| 2-bit flip | **0** for errors within Hamming distance of poly | 0 |
| 3-bit flip | Near-zero (most detected) | 0 |
| Arbitrary random replacement | 1/256 ≈ 0.39% | Not tested in original run |

**Implication for threat model:**

- **Class 1 (honest-but-faulty):** Natural errors are bit flips, sensor faults, transient corruption. CRC-8 collision probability is effectively **zero**. Syndromes provide perfect localization for this threat class.
- **Class 2/3 (Byzantine/adversarial):** An attacker crafting arbitrary replacement entries has a 1/256 probability of evading syndrome localization per entry. The chain-level SHA-256 digest still detects the tampering. Syndromes degrade gracefully: localization becomes a diagnostic tool, not a security guarantee.

This two-regime characterization is **stronger** than the original uniform 1/256 claim. The practical blind spot only exists for adversaries who are already caught by the chain digest.

### 2.3 Syndrome Computation

**Definition 4 (Syndrome vector).** Given entries e₀, e₁, ..., e_{n-1}, the syndrome vector of order t is:

```
S_k = ⊕ᵢ₌₀ⁿ⁻¹ (α^{ki} ⊗ h(eᵢ))    for k = 0, 1, ..., 2t-1
```

where ⊕ is GF(2⁸) addition (XOR), ⊗ is GF(2⁸) multiplication, and h is CRC-8.

**Expanded for t=1 (default):**

```
S₀ = h(e₀) ⊕ h(e₁) ⊕ ... ⊕ h(e_{n-1})                           (unweighted XOR)
S₁ = (α⁰⊗h(e₀)) ⊕ (α¹⊗h(e₁)) ⊕ ... ⊕ (α^{n-1}⊗h(e_{n-1}))    (position-weighted)
```

**Cost:** 2t bytes stored. O(n) computation (n CRC-8 evaluations + n×2t GF multiplications + 2tn XORs).

**Implementation:** GF multiplications use 256-byte exp table + 256-byte log table. Total static storage for GF: 512 bytes. Combined with CRC-8 table: **768 bytes total**.

### 2.4 Localization Theorem

**Theorem 1 (Single-entry tamper localization).** Let entries e₀, ..., e_{n-1} have stored syndromes (S₀, S₁). If exactly one entry eⱼ is replaced by e'ⱼ where h(eⱼ) ≠ h(e'ⱼ), then j is uniquely recoverable.

**Proof.**

The verifier recomputes syndromes (S₀', S₁') from the received chain and computes error syndromes:

```
Δ₀ = S₀' ⊕ S₀ = h(eⱼ) ⊕ h(e'ⱼ) = Δ         (non-zero by assumption)
Δ₁ = S₁' ⊕ S₁ = αʲ ⊗ Δ
```

Since Δ ≠ 0, it has a multiplicative inverse Δ⁻¹ in GF(2⁸). Therefore:

```
αʲ = Δ₁ ⊗ Δ₀⁻¹
```

Since α is primitive (verified: α = 0x03 generates all 255 non-zero elements), the discrete logarithm is unique for j ∈ {0, ..., 254}:

```
j = log_α(Δ₁ ⊗ Δ₀⁻¹)
```

The verifier recovers j via a single 256-byte log table lookup. **QED.**

**Verification status:** Exhaustively verified for ALL 32,640 (chain_length, tamper_position) pairs: chain lengths 1–255, every position within each chain. **Zero failures.**

### 2.5 Extension to t > 1

**Theorem 2 (Multi-entry tamper localization).** For t tampered entries, 2t syndrome bytes suffice for localization via Reed-Solomon decoding.

The syndrome vector generalizes to 2t components:

```
S_k = ⊕ᵢ (α^{ki} ⊗ h(eᵢ))   for k = 0, 1, ..., 2t-1
```

For t = 2, the error locator polynomial σ(x) = x² + σ₁x + σ₂ is computed from the 4 error syndromes (Δ₀, Δ₁, Δ₂, Δ₃) via:

```
denom = Δ₀⊗Δ₂ ⊕ Δ₁²
σ₁ = (Δ₀⊗Δ₃ ⊕ Δ₁⊗Δ₂) ⊗ denom⁻¹
σ₂ = (Δ₁⊗Δ₃ ⊕ Δ₂²) ⊗ denom⁻¹
```

Roots are found by Chien search (exhaustive evaluation over all 255 non-zero elements).

**Verification status:** 305 tests across chain lengths {5, 10, 20, 50, 100, 200, 255}. Zero failures, zero degenerate cases.

**Cost per additional t:** Exactly 2 bytes. Deployments choose t at session negotiation.

---

## 3. Failure Mode Analysis

### 3.1 Case 1: h-collision (h(eⱼ) = h(e'ⱼ))

- Δ₀ = 0, Δ₁ = 0. Syndromes report "clean."
- Chain digest (64-bit SHA-256) detects the tampering.
- Verifier reports: "chain invalid, cannot localize."
- **Probability:** Effectively zero for bit-flip errors (Class 1 threats). Approximately 1/256 for arbitrary entry replacement (Class 2/3 threats).
- **Graceful degradation:** Detection is unaffected. Only localization is lost.

### 3.2 Case 2: Multiple entries tampered (t' > t)

- For t=1 with 2 entries tampered: Δ₀ = Δⱼ ⊕ Δₖ, Δ₁ = (αʲ⊗Δⱼ) ⊕ (αᵏ⊗Δₖ).
- Two equations, four unknowns. System is underdetermined.
- Localization returns an incorrect index.
- Chain digest detects the tampering.
- Verifier reports: "chain invalid, localization unreliable (multi-entry tampering suspected)."
- **Verified behavior:** 70/70 multi-entry tamper pairs were detected by syndromes (Δ ≠ 0). All 70 returned wrong localization indices. Zero were invisible to syndromes.

### 3.3 Case 3: Entry insertion or deletion

- Chain length field mismatches entry count → detected before syndromes are checked.
- Syndromes not applicable — chain structure is violated.

### 3.4 Case 4: Syndrome bytes themselves tampered

- Syndromes are included in the SHA-256 input for the chain digest.
- Modifying syndromes invalidates the chain digest → detected.

### 3.5 Consistency Check (distinguishing Case 1 from Case 2)

After recovering candidate j from syndromes, the verifier can check consistency: remove entry j's contribution from the received syndromes and verify the residual is zero. If not, the localization is inconsistent → suspect multi-entry tampering. This is a computation, not an additional wire field. Zero extra cost.

---

## 4. Wire Format

### 4.1 Provenance Entry (Compact — 6 bytes, 48 bits, zero waste)

```
Bit   Width  Field                          Justification
──────────────────────────────────────────────────────────────────
0     2      origin_tier                    Which tier produced this entry (3 tiers + reserved)
2     4      operator_id                    Which operation was applied (13 operators defined)
6     2      precision_mode                 Source annotation precision (audit trail)
8     8      b̂ (quantized belief)           Opinion snapshot — belief component
16    8      d̂ (quantized disbelief)        Opinion snapshot — disbelief component
24    8      â (quantized base rate)        Opinion snapshot — base rate
32    16     time_offset                    Seconds since chain base_timestamp (max 65,535s ≈ 18.2h)
──────────────────────────────────────────────────────────────────
TOTAL: 48 bits = 6 bytes.  Zero waste. Every bit carries information.
```

**Key design decisions:**
- **û NOT stored** — derived as 255 − b̂ − d̂ (Axiom 3, SL invariant). Saves 8 bits per entry.
- **Opinion fixed at 8-bit** — sufficient for provenance audit. Full-precision opinion is in the annotation itself.
- **time_offset (16-bit)** replaces absolute timestamp (32-bit). Entries within a chain are temporally clustered (same pipeline run). 65,535 seconds covers any realistic processing latency. Saves 2 bytes per entry vs. absolute timestamps.
- **No per-entry digest** — replaced by chain-level syndromes + digest.

### 4.2 Chain Wire Format

```
Offset          Size        Field
──────────────────────────────────────────────────────────────────
0               4 bytes     base_timestamp (uint32, seconds since Unix epoch)
4               1 byte      chain_length (uint8, n, max 255)
5               6n bytes    entries (compact, §4.1)
5+6n            2t bytes    syndromes (S₀, S₁, ..., S_{2t-1})
5+6n+2t         8 bytes     chain_digest (truncate(SHA-256(all preceding bytes), 64 bits))
──────────────────────────────────────────────────────────────────
TOTAL:          13 + 6n + 2t bytes
```

**Critical:** The SHA-256 input for chain_digest includes the base_timestamp, chain_length, all entries, AND the syndrome bytes. This means the syndromes are integrity-protected by the chain digest. An attacker cannot modify both entries and syndromes without invalidating the digest.

**The value of t is not stored on the wire** — it is established at session negotiation (§4.3) and known to both sender and receiver. This avoids wasting bits on a field that is constant for the lifetime of a session.

### 4.3 Session Negotiation

The syndrome localization parameter t is configured at session setup:

```
Bit   Width  Field
──────────────────────────────────────
0     3      syndrome_t (0–7, where 0 means "no syndromes, digest only")
3     5      reserved
──────────────────────────────────────
```

1 byte in the session negotiation payload. t=0 disables syndromes (chain_digest only). t=1 is the default (2 syndrome bytes). t=7 is the maximum (14 syndrome bytes, localizes up to 7 simultaneous tampered entries).

### 4.4 Cost Comparison

For n=9 (typical Tier 1 → Tier 2 pipeline, 8 sensors + 1 fusion step):

| Scheme | Per-entry | Fixed overhead | Total (n=9) | Properties |
|---|---|---|---|---|
| §9 chained SHA-256 | 16 bytes | 0 | **144 bytes** | Detection + localization |
| Chain digest only | 6 bytes | 13 bytes | **67 bytes** | Detection only |
| **Syndrome t=1** | **6 bytes** | **15 bytes** | **69 bytes** | **Detection + localization** |
| Syndrome t=2 | 6 bytes | 17 bytes | 71 bytes | Detection + 2-entry localization |

**Key results:**
- **52.1% compression** vs. chained SHA-256 with identical single-entry localization capability.
- Localization adds only **2 bytes** over detection-only (chain digest).
- Each additional t costs exactly **2 bytes** (configurable at session negotiation).
- **Asymptotic:** O(6n + 2t + 13) vs O(16n). Gap grows with chain length.

---

## 5. Implementation Requirements

### 5.1 Static Storage

| Component | Size | Notes |
|---|---|---|
| GF(2⁸) exp table | 256 bytes | α^i for i=0..255 |
| GF(2⁸) log table | 256 bytes | Discrete log of each element |
| CRC-8 lookup table | 256 bytes | CRC-8/CCITT (poly 0x07) |
| **Total** | **768 bytes** | Fits on any microcontroller |

### 5.2 Computational Cost

| Operation | Cost | When |
|---|---|---|
| CRC-8 per entry | 6 table lookups + 6 XORs | Chain construction |
| GF mul per syndrome | 1 exp lookup + 1 log lookup + 1 addition | Chain construction |
| Syndrome computation | n × 2t GF muls + n × 2t XORs | Chain construction |
| SHA-256 | 1 invocation over chain bytes | Chain construction |
| Localization (t=1) | 1 GF mul + 1 GF inv + 1 log lookup | Verification |
| Localization (t=2) | Chien search: 255 polynomial evaluations | Verification |

All operations are constant-time when using table lookups (no branching on secret data).

### 5.3 Feature Gating (Rust Implementation)

```
security.rs — always compiled (types + encode/decode)
├── Types: ByzantineMetadata, ProvenanceEntry, RemovalStrategy, ProvenanceChain
│   → Zero deps. Available on bare no_std.
├── Encode/Decode: entry and metadata bit-packing
│   → Zero deps. Available on bare no_std.
├── GF(2⁸) + CRC-8: tables and arithmetic
│   → Zero deps. Available on bare no_std (768 bytes static).
├── Syndrome computation: compute_syndromes, localize_tamper
│   → Zero deps. Available on bare no_std.
├── Chain batch ops: encode/decode chain → Vec<u8> / Vec<ProvenanceEntry>
│   → Gated on `alloc` (heap allocation).
└── Chain digest: compute_chain_digest, verify_chain
    → Gated on `digest` feature (pulls in sha2 crate).
```

---

## 6. Verification Summary

| Property | Method | Coverage | Result |
|---|---|---|---|
| α=0x03 primitivity | Exhaustive generation | 255/255 elements | **VERIFIED** |
| GF(2⁸) field axioms | Random sampling + exhaustive | 30K+ samples | **VERIFIED** |
| CRC-8 single-bit detection | Exhaustive | 48/48 bit positions | **VERIFIED** |
| t=1 localization | **Exhaustive** | **32,640/32,640** tests | **VERIFIED** |
| t=2 localization | Sampled | 305/305 tests | **VERIFIED** |
| Multi-entry detection (t=1) | Sampled | 70/70 pairs detected | **VERIFIED** |
| Edge cases | Targeted | n=1, n=255, all-zero, all-identical, untampered | **VERIFIED** |
| h-collision regime | Empirical | 99K trials | **CHARACTERIZED** |

**Reproduction:** All results are reproducible via `verify_syndrome_localization.py` with `random.seed(42)`.

---

## 7. Relationship to Existing Work

### 7.1 What is Known

- **Reed-Solomon codes** (Reed & Solomon, 1960): Error-correcting codes over GF(2^m) using syndromes for error localization in communication channels. Well-established theory.
- **Chained hashing** for data integrity (Merkle trees, blockchain hash chains): Per-element digests linked sequentially. O(n) overhead. Widely deployed.
- **HMAC-based provenance** (various IoT security papers): Keyed hashes for authentication. Requires shared secrets. Higher overhead.

### 7.2 What May Be Novel (Pending Literature Search)

1. **Applying RS syndrome decoding to provenance chain integrity** — using the error localization machinery of coding theory for tamper localization in data integrity, not error correction in communication.
2. **The detection/localization decomposition** — formally separating the O(1) detection problem from the O(log n) localization problem, and solving each with the minimum-cost mechanism.
3. **The two-regime collision analysis** — showing that CRC-8 provides effectively zero collision probability for natural (bit-flip) errors while maintaining the theoretical 1/256 bound only for adversarial (arbitrary replacement) modifications.
4. **The configurable t parameter** — allowing deployments to choose their localization capability at session negotiation, paying exactly 2 bytes per additional t.

### 7.3 What is NOT Novel

- GF(2⁸) arithmetic, CRC-8, SHA-256, Reed-Solomon syndrome computation — all well-established.
- The individual components are standard; the contribution (if confirmed novel) is the specific composition and the cost/capability tradeoff for IoT provenance chains.

---

## 8. Limitations (Honest Assessment)

1. **Localization is not a security guarantee.** An adversary who can craft arbitrary entries has a 1/256 probability of evading syndrome localization per entry. The chain digest provides the security guarantee; syndromes are a diagnostic tool.

2. **Not applicable to entry insertion/deletion.** Syndromes assume a fixed chain length. Structural modifications (insertion, deletion, reordering) are detected by chain_length mismatch and digest failure, not by syndromes.

3. **CRC-8 is not cryptographic.** An adversary who knows the CRC-8 polynomial can construct modifications that collide. This is by design — the chain digest provides cryptographic integrity. CRC-8 is chosen for computational efficiency and natural-error detection, not adversarial resistance.

4. **GF(2⁸) limits chain length to 255.** The primitive element cycle has period 255. For longer chains, GF(2¹⁶) would be needed (65,535 max entries), but with 2-byte syndromes per component instead of 1-byte. In practice, provenance chains in IoT systems are much shorter than 255 entries.

5. **t must be known at both sender and receiver.** The value of t is not self-describing on the wire — it must be established at session negotiation. A receiver with the wrong t will misinterpret the chain footer.

---

## 9. Open Questions

1. **Literature search needed** — prior art check for the specific application of RS syndrome decoding to provenance chain integrity. See §7.2.
2. **Optimal hash function** — is CRC-8 the best choice for h()? Alternatives: truncated SHA-256 (1 byte), Fletcher-8, Adler-8. CRC-8 has the strongest guaranteed detection properties for small errors, but a formal comparison would strengthen the paper.
3. **Empirical validation** — the cost analysis uses n=9 (typical IoT pipeline). Real-world chain length distributions from deployed systems would validate the practical relevance.
4. **Formal security reduction** — can we prove that the syndrome+digest scheme is at least as secure as chained hashing under a standard cryptographic model? Intuition says yes (the chain digest provides equivalent detection), but a formal proof would be valuable.

---

## Appendix A: GF(2⁸) Reference Data

### A.1 First 20 Powers of α = 0x03

```
α⁰  = 0x01    α⁵  = 0x33    α¹⁰ = 0x72    α¹⁵ = 0x35
α¹  = 0x03    α⁶  = 0x55    α¹¹ = 0x96    α¹⁶ = 0x5F
α²  = 0x05    α⁷  = 0xFF    α¹² = 0xA1    α¹⁷ = 0xE1
α³  = 0x0F    α⁸  = 0x1A    α¹³ = 0xF8    α¹⁸ = 0x38
α⁴  = 0x11    α⁹  = 0x2E    α¹⁴ = 0x13    α¹⁹ = 0x48
```

### A.2 Verification Script

`verify_syndrome_localization.py` — standalone Python script with zero external dependencies. Reproduces all results in §6 with `random.seed(42)`.

---

## Appendix B: Comparison with Related Integrity Mechanisms

| Mechanism | Detection | Localization | Per-entry cost | Fixed cost | Crypto needed? |
|---|---|---|---|---|---|
| Chained SHA-256 | Yes | Yes (at j+1) | 8 bytes | 0 | SHA-256 |
| Merkle tree | Yes | Yes (O(log n) proof) | 0 (leaves) | 32n bytes (tree) | SHA-256 |
| Single chain digest | Yes | No | 0 | 8 bytes | SHA-256 |
| HMAC per entry | Yes | Yes | 16–32 bytes | 0 | HMAC + shared key |
| **Syndrome + digest** | **Yes** | **Yes (at j)** | **0** | **8 + 2t bytes** | **SHA-256 + GF(2⁸)** |

The syndrome approach is the only mechanism that provides localization with zero per-entry overhead.

---

*End of document.*
