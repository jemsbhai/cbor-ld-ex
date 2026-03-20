## 9. Security Model

`[GAP-7 RESOLVED]`

CBOR-LD-ex operates in adversarial environments where constrained devices may be compromised, gateways may be misconfigured, and communication links may be intercepted. The security model defines the threat landscape, countermeasures at each tier, and the interaction between CBOR-LD-ex annotations and the underlying transport security (DTLS, OSCORE).

*Reference: jsonld-ex `confidence_byzantine.py`, `security.py`; CoAP security (RFC 7252 §9), OSCORE (RFC 8613).*

### 9.1 Threat Model

**Definition 22 (Device Trust Classes).** CBOR-LD-ex defines three trust classes for devices in the network:

| Class | Assumption | Example |
|---|---|---|
| **Honest** | Device correctly implements the protocol and reports accurate opinions | Factory-calibrated sensor with secure firmware |
| **Honest-but-constrained** | Device is honest but may produce inaccurate opinions due to hardware limitations, calibration drift, or environmental interference | Low-cost consumer sensor, aging equipment |
| **Byzantine** | Device may produce arbitrarily incorrect opinions, including strategically crafted false compliance assertions | Compromised device, malicious actor, firmware-injected backdoor |

The protocol assumes **honest-but-constrained** as the default for Tier 1 devices and provides mechanisms to detect and mitigate Byzantine behavior at Tier 2 and above.

**Definition 23 (Threat Categories).** The following threats are in scope:

| ID | Threat | Target | Impact |
|---|---|---|---|
| T1 | **Opinion forgery** | Tier 1 → Tier 2 link | Fabricated compliance assertions enter the reasoning pipeline |
| T2 | **Opinion manipulation** | Tier 2 gateway | Compromised gateway alters Tier 1 opinions before fusion |
| T3 | **Provenance chain tampering** | Tier 2 → Tier 3 link | An intermediate node modifies or truncates the provenance chain to hide its reasoning steps |
| T4 | **Replay attack** | Any link | Stale but previously-valid opinions are replayed to influence current compliance evaluations |
| T5 | **Selective forwarding** | Tier 2 gateway | Gateway selectively drops non-compliant readings to inflate compliance status |
| T6 | **Context substitution** | Any tier | Attacker substitutes the compliance context to change the meaning of assertions |
| T7 | **Quantization exploitation** | Tier 1 encoding | Attacker crafts opinion values that exploit rounding to bias derived components in a chosen direction |

**Out of scope:** Physical device compromise (tamper-proof hardware is an orthogonal concern), denial of service at the network layer, and side-channel attacks on the reasoning engine.

### 9.2 Transport-Layer Security

CBOR-LD-ex does NOT define its own cryptographic primitives. It relies on the transport layer for confidentiality and integrity of the complete message (data + annotation).

**CoAP + DTLS (RFC 7252 §9):** For unicast CoAP communication, DTLS 1.2 (or 1.3) provides:
- Mutual authentication between devices
- Encryption of the full CoAP payload (including CBOR-LD-ex annotations)
- Integrity protection against modification in transit

**CoAP + OSCORE (RFC 8613):** For constrained environments or multicast, OSCORE provides end-to-end security at the application layer:
- Protects the CoAP payload and select options
- Works through intermediaries (proxies, gateways) without requiring them to decrypt
- Lighter than DTLS for very constrained devices

**CBOR-LD-ex requirement:** When OSCORE is used, the CBOR-LD-ex annotation block MUST be within the OSCORE-protected payload. The annotation is part of the message semantics and MUST NOT be transmitted as an unprotected CoAP option.

**Interaction with Axiom 1:** Transport security operates on the complete CBOR message. A DTLS or OSCORE layer sees the CBOR-LD-ex message as an opaque byte string. Stripping the annotation (Axiom 1) happens at the application layer, above the security layer — the transport protects the full message regardless of whether the application understands the annotation.

### 9.3 Annotation Integrity

Transport security protects messages in transit but does not prevent a compromised intermediary from modifying annotations between decryption and re-encryption (T2, T3). Annotation integrity provides an additional defence layer.

**Definition 24 (Annotation Digest).** An annotation digest is a compact hash of the bit-packed annotation block, computed as:

```
digest = truncate(SHA-256(annotation_bytes), 64 bits)
```

The 64-bit truncated SHA-256 provides collision resistance of ~2³² (birthday bound), which is appropriate for IoT message integrity but not for high-security applications. Tier 3 systems requiring stronger integrity SHOULD use full SHA-256 (32 bytes) or SHA-384.

**Where the digest lives:** The annotation digest is included as a CBOR tagged value alongside the annotation block:

```
Tag(60001) → byte string (8 bytes, truncated SHA-256 of the annotation)
```

A receiving tier can recompute the digest from the received annotation block and verify it matches. If it doesn't, the annotation has been tampered with after the originating tier signed it.

**Tier-specific digest policy:**

| Tier | Digest | Rationale |
|---|---|---|
| Tier 1 | Optional (SHOULD NOT for most deployments) | 8 bytes of overhead per message is significant for constrained devices. Transport security (DTLS/OSCORE) is the primary integrity mechanism. |
| Tier 2 | SHOULD | Gateway outputs aggregate multiple sources. The digest attests that the gateway's annotation block is self-consistent and was produced by a specific gateway identity. |
| Tier 3 | MUST (when provenance chain is present) | The provenance chain is the audit trail. Its integrity is non-negotiable in regulated environments. |

### 9.4 Provenance Chain Integrity

The provenance chain (§6) is the primary audit artifact. Its integrity must be protected against truncation (T3) and modification.

**Definition 25 (Chained Provenance Digest).** Each provenance entry includes a `prev_digest` field containing the annotation digest of the previous entry in the chain:

```
e_i = (tier_i, depth_i, operator_id_i, precision_mode_i, opinion_i, timestamp_i, prev_digest_i)
```

Where:
```
prev_digest_1 = 0x0000000000000000  (sentinel for the chain origin)
prev_digest_i = truncate(SHA-256(serialize(e_{i-1})), 64 bits)  for i > 1
```

This creates a hash chain. Verifying the chain at Tier 3:

```
For i = 2, ..., n:
  Recompute truncate(SHA-256(serialize(e_{i-1})), 64 bits)
  Compare with prev_digest_i in e_i
  If mismatch: chain integrity violation at entry i
```

**Theorem 8 (Provenance Chain Tamper Detection).** If any entry `e_j` in the chain is modified after construction, all subsequent entries `e_{j+1}, ..., e_n` will fail digest verification, provided the attacker cannot find a SHA-256 collision within the 64-bit truncated space.

*Proof:* Modifying `e_j` changes `SHA-256(serialize(e_j))`, which changes the expected `prev_digest_{j+1}`. The attacker would need to find a modified `e_j` that produces the same 64-bit truncated hash — requiring ~2³² work (birthday attack on 64-bit output). For higher-assurance environments, extending to full SHA-256 raises this to ~2¹²⁸. ∎

**Truncation detection:** If an intermediate node drops entries from the middle of the chain (T3), the `prev_digest` link will break at the truncation point. If entries are dropped from the beginning, the first remaining entry's `prev_digest` will not be the sentinel value — indicating a truncated chain.

**Space cost of chained digests:** 8 bytes per provenance entry. For a typical 3-hop chain (Tier 1 → Tier 2 → Tier 3), this is 24 bytes of integrity overhead across the entire chain — acceptable at Tier 3 where bandwidth is not constrained.

### 9.5 Byzantine Resilience at Tier 2

A Tier 2 gateway aggregating opinions from multiple Tier 1 devices faces the risk that some devices are Byzantine (T1). The jsonld-ex library provides Byzantine-resistant fusion with three strategies.

**Definition 26 (Byzantine-Resistant Fusion).** Given opinions `ω₁, ..., ωₙ` from `n` Tier 1 devices, Byzantine-resistant fusion:

1. Computes the pairwise conflict matrix `C[i][j] = b_i · d_j + d_i · b_j` (Jøsang 2016, §12.3.4)
2. Identifies agents whose mean discord exceeds a threshold `θ`
3. Removes the most discordant agents iteratively (never removing a majority)
4. Fuses surviving agents via cumulative fusion
5. Reports cohesion score, removal reasons, and the full conflict matrix

Three removal strategies:

| Strategy | Selection criterion | Best for |
|---|---|---|
| `most_conflicting` | Highest mean pairwise discord | Unknown trust landscape, pure data-driven |
| `least_trusted` | Lowest trust weight (discord as tiebreaker) | Known device reputation scores |
| `combined` | `discord × (1 − trust)` | Balanced: penalizes both conflict and low trust |

**Wire format implications:** Byzantine filtering happens at the gateway — it is a processing step, not a wire format feature. However, the Tier 2 header records evidence of filtering:

- `source_count`: Number of surviving sources (post-filtering), not total devices
- `operator_id`: Set to `cumulative_fusion` (0001) — the fused result
- Provenance chain (if Tier 3 is downstream): Records the filtering event including the number of removed agents and the cohesion score of survivors

**Threshold guidance:** The default threshold `θ = 0.15` (from jsonld-ex) means an agent is flagged when its mean conflict with the group exceeds 15% of the maximum possible conflict. For IoT compliance:
- `θ = 0.10`: Strict — removes any device with moderate disagreement. Appropriate for safety-critical environments (medical, industrial)
- `θ = 0.15`: Default — balances sensitivity with tolerance for sensor noise
- `θ = 0.25`: Permissive — only removes strongly adversarial devices. Appropriate for low-stakes environmental monitoring

### 9.6 Context Integrity

Compliance evaluation is meaningless if the compliance context itself has been substituted (T6). A device claiming "compliant" against a permissive context it chose itself provides no assurance.

**Definition 27 (Context Integrity Hash).** A context integrity hash binds a compliance evaluation to a specific, immutable version of the compliance context:

```
context_hash = truncate(SHA-256(canonical(context_document)), 64 bits)
```

Where `canonical()` produces a deterministic serialization (JSON with sorted keys, no whitespace).

The `context_version` field in Tier 2/3 headers (§5.1) identifies which context was used. The `context_hash` provides cryptographic binding — a verifier can independently retrieve the context document, compute the hash, and confirm that the claimed context version matches the actual content.

**Wire encoding:** The context hash is part of the `has_extended_context` block in Tier 3 headers:

```
If has_extended_context = 1:
  [16 bits] context_id
  [16 bits] context_version
  [64 bits] context_hash        ← new
  (12 bytes total)
```

### 9.7 Quantization Security

Quantization introduces rounding that an attacker could exploit (T7). Two specific attacks:

**Clamping bias exploitation:** The constrained quantization rule (Theorem 1(c), §4.2) clamps `d̂` when `b̂ + d̂ > 2ⁿ − 1`. An attacker crafting opinions where `b` and `d` are both near 0.5 with `u ≈ 0` can systematically trigger clamping, biasing reconstructed opinions toward higher belief.

**Mitigation:** The clamping direction is documented (§4.2). Receivers SHOULD flag opinions where `û = 0` (no uncertainty) as suspicious — genuinely certain compliance assessments are rare. The Byzantine filtering layer (§9.5) will naturally detect systematically biased opinions if they deviate from the group.

**Delta compression exploitation:** In batch mode (§7.7), 4-bit signed deltas are applied to quantized values. An attacker sending a long sequence of small positive deltas to `b̂` could gradually inflate belief without triggering discrete status changes.

**Mitigation:** Tier 2 gateways SHOULD independently evaluate compliance from raw sensor data, not rely solely on Tier 1 self-reported compliance status. The Tier 1 compliance status is a hint for routing (delegation) and prioritization, not an authoritative determination.

### 9.8 Security and Axiom Verification

**Axiom 1 (Backward Compatibility):** Security extensions (digests, context hashes, chained provenance) are encoded as additional CBOR tagged values. Stripping all CBOR-LD-ex tags removes security metadata along with annotations. The underlying CBOR-LD data is unaffected. A system that strips annotations accepts the loss of security metadata — this is by design (you lose reasoning assurance when you lose reasoning metadata).

**Axiom 2 (Algebraic Closure):** Byzantine fusion is a composition of conflict detection (produces a float), removal (produces a subset), and cumulative fusion (produces a valid opinion). The output is always a valid opinion. Annotation digests are metadata about annotations, not annotations themselves — they do not participate in the opinion algebra.

**Axiom 3 (Quantization Correctness):** Security does not alter the quantization scheme. Context hashes and provenance digests are byte-level operations on the serialized annotation, not on the opinion values. Quantization correctness is orthogonal to security.

### 9.9 Trust Bootstrapping

A complete treatment of trust bootstrapping (how devices initially obtain trust scores, how trust is revoked) is out of scope for this specification. The protocol provides the *transport mechanism* for trust-annotated opinions and Byzantine-filtered fusion. The trust management infrastructure — device provisioning, certificate authorities, trust score computation — is deployment-specific.

**Recommended patterns:**

- **Pre-shared trust:** Trust scores assigned at provisioning time and stored in gateway configuration. Simplest. Appropriate for static deployments.
- **Reputation-based:** Trust scores derived from historical conflict rates. The Tier 2 gateway maintains a running conflict history per device and adjusts trust weights accordingly. The `combined` Byzantine strategy (§9.5) naturally integrates these.
- **Certificate-based:** Devices present certificates tied to manufacturer or calibration authority. Trust score maps to certificate chain length / authority reputation. Integrates with DTLS mutual authentication.
