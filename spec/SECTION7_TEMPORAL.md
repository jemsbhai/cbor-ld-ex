## 7. Temporal Model

`[GAP-9 RESOLVED]`

IoT data is inherently temporal. Compliance is often time-bounded ("temperature must remain below X for Y minutes"), evidence ages, and regulatory triggers fire at discrete moments. The temporal model addresses five distinct concerns: timestamp encoding, opinion decay on the wire, temporal fusion semantics, compliance trigger encoding, and time-series delta compression.

*Reference: jsonld-ex `confidence_decay.py`, `confidence_temporal_fusion.py`, `sl_network/temporal.py`; Syed et al. (2026) §8.*

### 7.1 Timestamp Encoding

Timestamps appear in three protocol contexts: observation time (when a sensor reading was taken), processing time (when a gateway fused or evaluated), and provenance chain entries (§6).

**Definition 14 (Protocol Timestamp).** A protocol timestamp is an unsigned integer representing seconds since the Unix epoch (1970-01-01T00:00:00Z), truncated to the precision specified by the tier:

| Tier | Timestamp width | Resolution | Range |
|---|---|---|---|
| Tier 1 | 32 bits | 1 second | Until 2106 |
| Tier 2 | 32 bits | 1 second | Until 2106 |
| Tier 3 | 48 bits | 1 second | Until year 8.9 million |

Tier 1 and Tier 2 use 32-bit timestamps (4 bytes). This is sufficient for all practical IoT deployments through 2106. Tier 3 uses 48 bits for archival provenance where long-term audit trails are required.

**Relative timestamps.** Within a time-series batch (§7.5), timestamps after the first are encoded as **deltas** from the preceding timestamp, using a variable-width encoding:

```
Delta ≤ 255 seconds:     1 byte  (covers ~4 minute intervals)
Delta ≤ 65535 seconds:   2 bytes (covers ~18 hour intervals)
Delta > 65535 seconds:   4 bytes (full 32-bit delta)
```

The delta width is signaled by the high bits of the first byte:

```
0xxxxxxx              → 7-bit delta (0–127 seconds)
10xxxxxx xxxxxxxx     → 14-bit delta (0–16383 seconds, ~4.5 hours)
11xxxxxx xxxxxxxx xxxxxxxx xxxxxxxx → 30-bit delta (0–1073741823 seconds, ~34 years)
```

This variable-length encoding exploits the fact that IoT sensors typically report at regular, short intervals. A sensor reporting every 5 seconds uses 1-byte deltas — saving 3 bytes per reading compared to full timestamps.

### 7.2 Opinion Decay on the Wire

Opinion decay models the epistemic reality that evidence ages — old observations carry less certainty than fresh ones. The jsonld-ex library implements three decay functions:

**Definition 15 (Decay Function).** A decay function `λ : ℝ≥0 × ℝ>0 → [0,1]` maps (elapsed time, half-life) to a decay factor. Three built-in functions are defined:

| Code | Function | Formula | Properties |
|---|---|---|---|
| 00 | Exponential | `λ(t, τ) = 2^(−t/τ)` | Smooth, never reaches zero, standard SL aging |
| 01 | Linear | `λ(t, τ) = max(0, 1 − t/(2τ))` | Hard zero at `t = 2τ`, simpler analysis |
| 10 | Step | `λ(t, τ) = 1 if t < τ else 0` | Binary freshness, TTL-style |
| 11 | Reserved | — | Future: custom/negotiated functions |

The decay function code is 2 bits, encoded within the temporal annotation block (§7.4).

**Definition 16 (Decayed Opinion).** Given opinion `ω = (b, d, u, a)` and decay factor `λ ∈ [0, 1]`:

```
decay(ω, λ) = (λb, λd, 1 − λb − λd, a)
```

**Theorem 6 (Decay Preserves Validity).**

**(a)** `λb + λd + (1 − λb − λd) = 1` (trivially).

**(b)** `1 − λb − λd ≥ 0` since `λ ≤ 1` and `b + d ≤ 1`, so `λ(b + d) ≤ 1`.

**(c)** The belief-disbelief ratio is preserved: `b′/d′ = (λb)/(λd) = b/d`. The direction of evidence is unchanged; only its strength degrades.

**(d)** As `λ → 0`, `ω → (0, 0, 1, a)` — the vacuous opinion. Complete uncertainty is the limit of complete aging.

**(e)** The projected probability `P(ω′) = λb + a(1 − λb − λd) → a` as `λ → 0`. The opinion reverts to the base rate under complete aging.

*Proof:* All properties follow directly from the definition and `λ ∈ [0,1]`, `b + d ≤ 1`. ∎

**Decay in the quantized domain.** When opinions are stored at n-bit precision, decay MUST be applied before quantization (at the sending tier) or after reconstruction (at the receiving tier). Applying decay to quantized integer values directly would compound rounding errors. The protocol does NOT transmit pre-decayed opinions — it transmits the raw opinion plus the information needed for the receiver to decay it (timestamp + half-life + decay function code).

**Axiom 2 verification:** Decay is a unary operation on opinions producing valid opinions (Theorem 6). Algebraic closure is preserved.

### 7.3 Temporal vs. Spatial Fusion

When a Tier 2 gateway aggregates opinions, it must distinguish between two fundamentally different fusion operations:

**Spatial fusion:** Combining opinions from multiple sensors at the same time about the same proposition. "Sensor A says 22.5°C with confidence X, Sensor B says 22.7°C with confidence Y — what is the zone's compliance?" This uses cumulative fusion or averaging fusion directly.

**Temporal fusion:** Combining opinions from the same sensor across time about the same proposition. "Sensor A reported compliance at t₁, t₂, ..., tₙ — what is the current compliance status?" This requires decaying each opinion by its age before fusing.

**Definition 17 (Temporal Fusion).** Given a sequence of timestamped opinions `{(ω₁, t₁), ..., (ωₙ, tₙ)}` from the same source, a reference time `t_ref`, half-life `τ`, and decay function `λ`:

```
temporal_fuse({(ωᵢ, tᵢ)}, t_ref, τ, λ) = cumulative_fuse(decay(ω₁, λ(t_ref − t₁, τ)), ..., decay(ωₙ, λ(t_ref − tₙ, τ)))
```

Newer opinions receive higher weight because they have decayed less.

**Definition 18 (Spatiotemporal Fusion).** A Tier 2 gateway performing both spatial and temporal aggregation applies temporal fusion per-source first, then spatial fusion across sources:

```
Step 1: ω̃ₛ = temporal_fuse(readings_from_source_s)    for each source s
Step 2: ω_zone = cumulative_fuse(ω̃₁, ..., ω̃ₖ)        across k sources
```

**Wire format implication:** The Tier 2 header's `source_count` field (§5.1) records the number of spatial sources (Step 2), not the total number of temporal readings. The temporal dimension is collapsed into per-source summaries before spatial fusion.

**Weighted temporal fusion.** Different sources may have different decay rates — a calibrated laboratory sensor may have a longer half-life than a consumer-grade device. The jsonld-ex `temporal_fuse_weighted()` function supports per-source half-lives. On the wire, per-source half-lives are NOT transmitted in Tier 2 messages (they are configuration, not data). They MAY appear in Tier 3 provenance extensions for audit purposes.

### 7.4 Temporal Annotation Wire Format

The temporal annotation `τ(w, λ, triggers)` from Definition 6 (§3.4) is encoded as an optional extension block, present when a `has_temporal` flag is set.

**Tier 1 — Minimal temporal information:**

Tier 1 devices do not transmit temporal annotations. The observation timestamp is part of the CBOR-LD data payload (e.g., the `observedAt` field), not the annotation block. Decay is applied by the receiving gateway, not the sending device. This preserves the 1-byte header constraint.

**Tier 2 — Temporal extension block (when `has_temporal` flag is set in an extended Tier 2 header):**

```
Bit  Width  Field
───────────────────────────
0    2      decay_function_code      (Definition 15)
2    16     half_life_encoded        (see below)
18   1      has_compliance_window    (0 = no window, 1 = window follows)
19   1      has_triggers             (0 = no triggers, 1 = trigger block follows)
20   4      reserved
```

**3 bytes base.** The half-life is encoded as a 16-bit unsigned integer representing seconds, giving a range of 0–65535 seconds (~18 hours). For longer half-lives, the value is scaled: values > 32768 are interpreted as `(value − 32768) × 3600` seconds, extending the range to ~9.1 years. The encoding scheme:

```
0–32768:       value in seconds (0–32768 seconds, ~9.1 hours precision)
32769–65535:   (value − 32768) × 3600 seconds (3600–117,842,400 seconds, ~3.7 years, hour precision)
```

### 7.5 Compliance Windows

A compliance window defines a time-bounded evaluation: "the proposition must hold continuously for this duration."

**Definition 19 (Compliance Window).** A compliance window is a tuple `W = (duration, min_observations, aggregation)` where:
- `duration` is the window length in seconds
- `min_observations` is the minimum number of observations required within the window for an evaluation to be non-vacuous
- `aggregation` specifies how opinions within the window are combined

Wire encoding (when `has_compliance_window = 1`):

```
Bit  Width  Field
───────────────────────────
0    32     window_duration_seconds
32   8      min_observations         (0–255; 0 means "any")
40   2      aggregation_mode         (00 = all_must_comply, 01 = majority, 10 = weighted_temporal, 11 = reserved)
42   6      reserved
```

**6 bytes.** The `aggregation_mode` determines how opinions within the window map to a compliance status:

- `all_must_comply` (00): Jurisdictional Meet across all observations in the window. A single non-compliant reading within the window makes the entire window non-compliant. Strictest mode.
- `majority` (01): Compliance status is determined by the projected probability of the fused opinion exceeding 0.5. More tolerant of transient excursions.
- `weighted_temporal` (10): Temporal fusion with decay — newer observations within the window have higher weight. The fused opinion is then evaluated for compliance.

### 7.6 Temporal Trigger Encoding

Triggers (Syed et al. 2026, §8.2) are discrete regulatory events that cause non-continuous state changes. Three trigger types are defined in the compliance algebra; each requires wire representation for Tier 3 provenance.

**Definition 20 (Trigger Encoding).** A trigger block (when `has_triggers = 1`) consists of:

```
[8 bits]  trigger_count (1–255)
For each trigger:
  [4 bits]  trigger_type
  [32 bits] trigger_timestamp (Unix epoch seconds)
  [variable] trigger-specific payload
```

Trigger types:

| Code | Type | Payload | Semantics |
|---|---|---|---|
| 0001 | expiry | `[8 bits] gamma` — residual lawfulness factor Q₈(γ) | At trigger time, lawfulness transfers to violation: `l′ = γl`, `v′ = v + (1−γ)l`. Hard expiry when γ = 0. (Definition 12, Syed et al.) |
| 0010 | review_due | `[16 bits] accelerated_half_life` — new faster decay rate | Missed mandatory review accelerates uncertainty growth. Continuous decay rate changes from τ to τ_fast. (Definition 13, Syed et al.) |
| 0011 | regulatory_change | `[16 bits] new_context_id, [16 bits] new_context_version` | Legal framework changed. Compliance opinion is replaced by a new assessment under the new context. (Definition 14, Syed et al.) |
| 0100 | withdrawal | `[8 bits] purpose_id` | Consent withdrawn for a specific purpose. Proposition replacement: compliance question changes from "was consent valid?" to "has processing ceased?" (Definition 11, Syed et al.) |
| Others | reserved | — | — |

**Theorem 7 (Trigger Ordering).** Triggers MUST be encoded in chronological order within the trigger block (`t₁ ≤ t₂ ≤ ... ≤ tₙ`). This is a correctness requirement, not merely a convention:

**(a)** Expiry triggers are non-commutative with review-due triggers: expiry transfers `l → v` while review-due transfers `(l, d) → u`. Applying expiry then review-due produces a different state than review-due then expiry.

**(b)** Regulatory change replaces the entire opinion. Any trigger after a regulatory change operates on the new opinion, not the original.

*Proof:* Follows from the asymmetric transfer semantics of the trigger types (Syed et al. Theorem 4(e)). ∎

### 7.7 Time-Series Delta Compression

For Tier 1 devices reporting at regular intervals, transmitting a full annotation block per reading is wasteful when the compliance status hasn't changed. Time-series delta compression exploits temporal redundancy.

**Definition 21 (Time-Series Batch).** A time-series batch is a compact encoding of N consecutive readings from the same device:

```
[1 byte]    batch_header (Tier 1 header — applies to all readings in the batch)
[4 bytes]   base_timestamp (first reading, absolute)
[1 byte]    reading_count (N, 1–255)
[variable]  opinion_payload (full opinion for the first reading)
For readings 2..N:
  [variable] delta_timestamp (§7.1 variable-length encoding)
  [1 bit]    status_changed (0 = same compliance_status as previous, 1 = changed)
  [variable] If status_changed: new compliance_status (2 bits) + new opinion
             If !status_changed: delta_opinion or omitted
```

**Delta opinion encoding.** When compliance status hasn't changed between consecutive readings, the opinion often changes only slightly. Two sub-modes:

- **Omit** (most common): If opinion hasn't changed by more than the quantization step, transmit nothing. The receiver reuses the previous opinion. Signaled by `status_changed = 0` with no following data.
- **Delta**: Transmit signed deltas for `b̂` and `d̂` as 4-bit signed integers (range −8 to +7). The receiver applies: `b̂_new = b̂_prev + Δb̂`, `d̂_new = d̂_prev + Δd̂`, `û_new = (2ⁿ−1) − b̂_new − d̂_new`. Signaled by a `has_delta` bit following `status_changed = 0`.

**Space savings for typical scenarios:**

| Scenario | Per-reading cost (no batch) | Per-reading cost (batched) | Savings |
|---|---|---|---|
| Stable compliant (no change) | 5 bytes | ~1.5 bytes (delta timestamp + 1 bit) | ~70% |
| Slowly drifting opinion | 5 bytes | ~2.5 bytes (delta timestamp + 1 byte delta) | ~50% |
| Status change | 5 bytes | ~6 bytes (delta timestamp + full new annotation) | −20% (overhead for rare events) |

For a sensor reporting every 5 seconds over 5 minutes (60 readings), batched encoding reduces total annotation overhead from ~300 bytes to ~100 bytes.

### 7.8 Temporal Model and Axiom Verification

**Axiom 1 (Backward Compatibility):** Temporal annotations are encoded in CBOR-LD-ex extension blocks. Stripping the annotation tag removes all temporal metadata. The underlying CBOR-LD data (including any `observedAt` fields) is unaffected.

**Axiom 2 (Algebraic Closure):** Decay produces valid opinions (Theorem 6). Temporal fusion composes decay (valid → valid) with cumulative fusion (valid → valid). Trigger operations produce valid opinions (Syed et al. Theorem 4). All temporal operations preserve closure.

**Axiom 3 (Quantization Correctness):** Decay is applied at full precision before quantization. Temporal deltas in batch mode are applied in the quantized domain but maintain the constrained quantization invariant (`b̂ + d̂ + û = 2ⁿ − 1`) because `û` is always derived, never independently delta'd.
