# Findings

## Curated Summary

### Wire-Size Compression (EXP-000)

CBOR-LD-ex achieves 24–57% of JSON-LD wire size across four real IoT datasets
(Intel Lab, UCI Air Quality, CIC-IoT-2023, SWaT A8), making it the most compact
self-describing format tested. Schema-rigid Protobuf is 18–51% of JSON-LD —
smaller in raw bytes but carries no on-wire semantics.

Frame-fit: CBOR-LD-ex is the only self-describing format that fits 802.15.4
(127B) and LoRaWAN SF10 (115B) frames for Intel Lab records (100% fit rate).
No other semantic format achieves this.

jsonld-ex CBOR-LD is NOT universally smaller than JSON-LD: CBOR float64 (9B)
exceeds JSON's compact number representation ("0.0" = 3B) on flag-heavy datasets.

---

## Raw Findings Log

### 2026-05-30 -- EXP-000: Wire-size experiment (9 formats × 4 datasets)

**Key result:** CBOR-LD-ex compression ratio vs JSON-LD: 0.242 (Intel Lab),
0.324 (UCI AQ), 0.486 (CIC-IoT), 0.572 (SWaT A8).

**Details:** See `papers/cborld-ex-main/tables/wire_sizes.md` for full tables.
10,000-record samples per dataset (full run for UCI AQ at 827 clean records).
Protobuf beats all formats on raw size but lacks on-wire semantics.
SenML/JSON is larger than JSON-LD for many-field datasets due to per-measurement
record overhead. CIC-IoT (39 fields) and SWaT (63 fields) records exceed all
constrained single-frame MTUs in every format.
