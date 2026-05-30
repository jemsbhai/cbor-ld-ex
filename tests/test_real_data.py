"""
Tests for real-data experiment harness — Paper #1 core experiments.

TDD red phase: cbor_ld_ex_benchmark.real_data does not exist yet.
All tests will fail at import time.

Organized in eight sections:

1. SYNTHETIC FIXTURES — small records mimicking each dataset schema.
   These are realistic values drawn from the actual datasets so that
   wire sizes, field counts, and value distributions are representative.

2. RECORD MAPPER TESTS — raw CSV row \u2192 JSON-LD document + Annotation
   + ContextRegistry. Validates structure, required fields, type
   correctness.

3. BASELINE ENCODER TESTS — each of 9 formats produces non-empty bytes
   of the correct type. No format crashes on any dataset.

4. SEMANTIC PARITY — all self-describing formats encode the same sensor
   field values. Schema-rigid formats encode the same raw values.
   Formats that omit @context/typing are noted, not penalised.

5. ROUND-TRIP — decode(encode(record)) recovers the original field
   values within floating-point tolerance for all formats.

6. SIZE ORDERING — the headline paper claim for the JSON-LD family:
   CBOR-LD-ex < our CBOR-LD < jsonld-ex CBOR-LD < JSON-LD.
   Schema-rigid formats (Protobuf, FlatBuffers, MessagePack) are NOT
   required to be larger — the paper reports them honestly.

7. FRAME-FIT — correct frame-fit classification against all five
   MTU thresholds: 802.15.4 (127B), BLE (247B), LoRaWAN SF7 (242B),
   SF10 (115B), SF12 (51B).

8. DETERMINISM — same record \u2192 identical bytes on repeated encoding.
   Required for reproducible experiments.

Design principles:
  - Scientific claims become universal assertions.
  - Synthetic fixtures use realistic values, not toy data.
  - Parametrize across datasets and formats — one test \u00d7 N scenarios.
  - Every number traceable to first principles.
"""

import json
import math

import cbor2
import pytest

# =====================================================================
# Module under test — does NOT exist yet (TDD red phase).
# All tests fail at import time until real_data.py is created.
# =====================================================================

from cbor_ld_ex_benchmark.real_data import (
    # Dataset column schemas (tuples of column names)
    INTEL_LAB_COLUMNS,
    UCI_AQ_COLUMNS,
    CICIOT_FEATURE_COLUMNS,
    SWAT_A8_SENSOR_COLUMNS,
    # Record mappers: raw dict -> (jsonld_doc, Annotation, ContextRegistry)
    map_intel_lab_record,
    map_uci_aq_record,
    map_ciciot_record,
    map_swat_a8_record,
    # Encoders: (record_or_doc, ...) -> bytes
    encode_as_json_ld,
    encode_as_senml_json,
    encode_as_senml_cbor,
    encode_as_cbor_ld,
    encode_as_cbor_ld_ex,
    encode_as_jsonldex_cbor_ld,
    encode_as_protobuf,
    encode_as_flatbuffers,
    encode_as_msgpack,
    # Decoders: bytes -> field values (for round-trip)
    decode_json_ld,
    decode_senml_json,
    decode_senml_cbor,
    decode_cbor_ld,
    decode_cbor_ld_ex,
    decode_protobuf,
    decode_flatbuffers,
    decode_msgpack,
    # Frame-fit analysis
    MTU_CONSTANTS,
    frame_fit,
    # All-formats runner
    encode_record_all_formats,
)

# Existing package — used for independent verification
from cbor_ld_ex.annotations import Annotation
from cbor_ld_ex.codec import ContextRegistry


# =====================================================================
# 1. SYNTHETIC FIXTURES
#
# Realistic records drawn from actual dataset values. Each fixture
# mirrors the column schema and value ranges of its dataset so that
# wire sizes and encoding behavior are representative.
# =====================================================================

# Intel Lab: 54 Mica2Dot motes, 31-second cadence.
# Values from actual record (moteid=1, 2004-03-31).
SYNTHETIC_INTEL_LAB = {
    "timestamp": "2004-03-31 03:38:15.757551",
    "epoch": 1080618727.0,
    "moteid": 1,
    "temperature": 122.153,
    "humidity": -3.91901,
    "light": 11.04,
    "voltage": 2.03397,
}

# UCI Air Quality: hourly readings from multisensor device.
# Values from actual record (2004-10-03 18:00).
SYNTHETIC_UCI_AQ = {
    "Date": "10/03/2004",
    "Time": "18.00.00",
    "CO_GT": 2.6,
    "PT08_S1_CO": 1360.0,
    "NMHC_GT": 150.0,
    "C6H6_GT": 11.9,
    "PT08_S2_NMHC": 1046.0,
    "NOx_GT": 166.0,
    "PT08_S3_NOx": 1056.0,
    "NO2_GT": 113.0,
    "PT08_S4_NO2": 1692.0,
    "T": 13.6,
    "RH": 48.9,
    "AH": 0.7578,
}

# CIC-IoT-2023: 39 network-flow features + label.
# Values from representative benign flow in Merged01.csv.
SYNTHETIC_CICIOT = {
    "Header_Length": 54.0,
    "Protocol Type": 6.0,
    "Time_To_Live": 64.0,
    "Rate": 12.5,
    "fin_flag_number": 1.0,
    "syn_flag_number": 1.0,
    "rst_flag_number": 0.0,
    "psh_flag_number": 1.0,
    "ack_flag_number": 1.0,
    "ece_flag_number": 0.0,
    "cwr_flag_number": 0.0,
    "ack_count": 5.0,
    "syn_count": 1.0,
    "fin_count": 1.0,
    "rst_count": 0.0,
    "HTTP": 0.0,
    "HTTPS": 1.0,
    "DNS": 0.0,
    "Telnet": 0.0,
    "SMTP": 0.0,
    "SSH": 0.0,
    "IRC": 0.0,
    "TCP": 1.0,
    "UDP": 0.0,
    "DHCP": 0.0,
    "ARP": 0.0,
    "ICMP": 0.0,
    "IGMP": 0.0,
    "IPv": 1.0,
    "LLC": 0.0,
    "Tot sum": 1523.0,
    "Min": 54.0,
    "Max": 1460.0,
    "AVG": 380.75,
    "Std": 612.33,
    "Tot size": 4569.0,
    "IAT": 0.032,
    "Number": 12.0,
    "Variance": 374950.0,
    "Label": "BENIGN",
}

# SWaT A8: 6-stage water treatment plant, 1-second cadence.
# Values from actual record (2021-06-22 09:37:05).
# Only process sensor/actuator columns — attack metadata excluded.
SYNTHETIC_SWAT_A8 = {
    "Timestamp": "22/06/2021 09:37:05 AM",
    # Stage 1
    "FIT101": 0.0,
    "LIT101": 822.463,
    "MV101": 1.0,
    "P102": 1.0,
    "P101": 1.0,
    # Stage 2
    "AIT201": 33.485,
    "AIT202": 5.6548,
    "AIT203": 358.267,
    "FIT201": 0.0,
    "MV201": 1.0,
    "P201": 1.0,
    "P202": 1.0,
    "P203": 1.0,
    "P204": 1.0,
    "P205": 1.0,
    "P206": 1.0,
    "P207": 0.0,
    "P208": 0.0,
    # Stage 3
    "AIT301": 5.8225,
    "AIT302": 307.856,
    "AIT303": 12.901,
    "DPIT301": 0.04162,
    "FIT301": 0.000512,
    "LIT301": 898.728,
    "MV301": 1.0,
    "MV302": 2.0,
    "MV303": 1.0,
    "MV304": 1.0,
    "P301": 1.0,
    "P302": 1.0,
    # Stage 4
    "AIT401": 0.0,
    "AIT402": 0.0,
    "FIT401": 0.000768,
    "LIT401": 595.309,
    "P401": 1.0,
    "P402": 1.0,
    "P403": 1.0,
    "P404": 1.0,
    "UV401": 1.0,
    # Stage 5
    "AIT501": 6.7174,
    "AIT502": 298.462,
    "AIT503": 120.995,
    "AIT504": 4.8449,
    "FIT501": 0.002564,
    "FIT502": 0.001921,
    "FIT503": 0.002048,
    "FIT504": 0.0,
    "MV501": 1.0,
    "MV502": 1.0,
    "MV503": 1.0,
    "MV504": 1.0,
    "P501": 1.0,
    "P502": 1.0,
    "PIT501": 20.187,
    "PIT502": 6.664,
    "PIT503": 16.406,
    # Stage 6
    "FIT601": 0.0,
    "FIT602": 0.0,
    "LIT601": 707.287,
    "LIT602": 604.428,
    "P601": 1.0,
    "P602": 1.0,
    "P603": 1.0,
}

# Lookup for parametrized tests.
SYNTHETIC_RECORDS = {
    "intel_lab": SYNTHETIC_INTEL_LAB,
    "uci_aq": SYNTHETIC_UCI_AQ,
    "ciciot": SYNTHETIC_CICIOT,
    "swat_a8": SYNTHETIC_SWAT_A8,
}

DATASET_NAMES = list(SYNTHETIC_RECORDS.keys())

# Mapper dispatch — dataset name -> mapper function.
MAPPERS = {
    "intel_lab": map_intel_lab_record,
    "uci_aq": map_uci_aq_record,
    "ciciot": map_ciciot_record,
    "swat_a8": map_swat_a8_record,
}

# Format classification.
# JSON-LD family: all carry @context, semantic keys, and annotation.
JSONLD_FAMILY_FORMATS = [
    "json_ld",
    "jsonldex_cbor_ld",
    "cbor_ld",
    "cbor_ld_ex",
]

# SenML: self-describing (named fields) but own vocabulary, no annotation.
SENML_FORMATS = ["senml_json", "senml_cbor"]

# Schema-rigid: no on-wire metadata.
SCHEMA_RIGID_FORMATS = ["protobuf", "flatbuffers", "msgpack"]

ALL_FORMATS = JSONLD_FAMILY_FORMATS + SENML_FORMATS + SCHEMA_RIGID_FORMATS


# =====================================================================
# 2. RECORD MAPPER TESTS
# =====================================================================

class TestRecordMappers:
    """Each mapper produces (jsonld_doc, Annotation, ContextRegistry)."""

    @pytest.mark.parametrize("dataset", DATASET_NAMES)
    def test_mapper_returns_triple(self, dataset):
        """Mapper returns a 3-tuple."""
        record = SYNTHETIC_RECORDS[dataset]
        result = MAPPERS[dataset](record)
        assert isinstance(result, tuple), f"{dataset}: expected tuple"
        assert len(result) == 3, f"{dataset}: expected 3-tuple, got {len(result)}"

    @pytest.mark.parametrize("dataset", DATASET_NAMES)
    def test_mapper_jsonld_doc_is_dict(self, dataset):
        """First element is a dict (JSON-LD document)."""
        doc, _, _ = MAPPERS[dataset](SYNTHETIC_RECORDS[dataset])
        assert isinstance(doc, dict)

    @pytest.mark.parametrize("dataset", DATASET_NAMES)
    def test_mapper_jsonld_has_context(self, dataset):
        """JSON-LD document has @context."""
        doc, _, _ = MAPPERS[dataset](SYNTHETIC_RECORDS[dataset])
        assert "@context" in doc, f"{dataset}: missing @context"

    @pytest.mark.parametrize("dataset", DATASET_NAMES)
    def test_mapper_jsonld_has_type(self, dataset):
        """JSON-LD document has @type."""
        doc, _, _ = MAPPERS[dataset](SYNTHETIC_RECORDS[dataset])
        assert "@type" in doc, f"{dataset}: missing @type"

    @pytest.mark.parametrize("dataset", DATASET_NAMES)
    def test_mapper_annotation_type(self, dataset):
        """Second element is an Annotation."""
        _, ann, _ = MAPPERS[dataset](SYNTHETIC_RECORDS[dataset])
        assert isinstance(ann, Annotation), (
            f"{dataset}: expected Annotation, got {type(ann).__name__}"
        )

    @pytest.mark.parametrize("dataset", DATASET_NAMES)
    def test_mapper_registry_type(self, dataset):
        """Third element is a ContextRegistry."""
        _, _, reg = MAPPERS[dataset](SYNTHETIC_RECORDS[dataset])
        assert isinstance(reg, ContextRegistry), (
            f"{dataset}: expected ContextRegistry, got {type(reg).__name__}"
        )

    @pytest.mark.parametrize("dataset", DATASET_NAMES)
    def test_mapper_registry_compresses_all_keys(self, dataset):
        """Registry maps every JSON-LD key (except @context/@type) to int."""
        doc, _, reg = MAPPERS[dataset](SYNTHETIC_RECORDS[dataset])
        compressed = reg.compress(doc)
        for key in compressed:
            assert isinstance(key, int), (
                f"{dataset}: key {key!r} not compressed to int"
            )

    @pytest.mark.parametrize("dataset", DATASET_NAMES)
    def test_mapper_sensor_values_preserved(self, dataset):
        """JSON-LD doc contains the original sensor values."""
        record = SYNTHETIC_RECORDS[dataset]
        doc, _, _ = MAPPERS[dataset](record)
        # Exclude metadata keys and non-sensor fields.
        meta_keys = {"@context", "@type", "@id", "timestamp", "Timestamp",
                     "Date", "Time", "Label", "epoch", "moteid"}
        for key, value in record.items():
            if key in meta_keys:
                continue
            # The doc key may differ (e.g., underscored) but value must match.
            # Check that the value appears somewhere in the doc values.
            doc_values = list(doc.values())
            assert value in doc_values, (
                f"{dataset}: sensor value {key}={value} not found in doc"
            )


# =====================================================================
# 3. BASELINE ENCODER TESTS
# =====================================================================

class TestEncoders:
    """Each encoder produces non-empty bytes."""

    @pytest.mark.parametrize("dataset", DATASET_NAMES)
    def test_encode_json_ld(self, dataset):
        """JSON-LD encoder produces bytes."""
        doc, ann, reg = MAPPERS[dataset](SYNTHETIC_RECORDS[dataset])
        result = encode_as_json_ld(doc, ann)
        assert isinstance(result, bytes)
        assert len(result) > 0

    @pytest.mark.parametrize("dataset", DATASET_NAMES)
    def test_encode_senml_json(self, dataset):
        """SenML/JSON encoder produces bytes."""
        record = SYNTHETIC_RECORDS[dataset]
        result = encode_as_senml_json(record, dataset)
        assert isinstance(result, bytes)
        assert len(result) > 0

    @pytest.mark.parametrize("dataset", DATASET_NAMES)
    def test_encode_senml_cbor(self, dataset):
        """SenML/CBOR encoder produces bytes."""
        record = SYNTHETIC_RECORDS[dataset]
        result = encode_as_senml_cbor(record, dataset)
        assert isinstance(result, bytes)
        assert len(result) > 0

    @pytest.mark.parametrize("dataset", DATASET_NAMES)
    def test_encode_cbor_ld(self, dataset):
        """Our CBOR-LD encoder produces bytes."""
        doc, ann, reg = MAPPERS[dataset](SYNTHETIC_RECORDS[dataset])
        result = encode_as_cbor_ld(doc, ann, reg)
        assert isinstance(result, bytes)
        assert len(result) > 0

    @pytest.mark.parametrize("dataset", DATASET_NAMES)
    def test_encode_cbor_ld_ex(self, dataset):
        """CBOR-LD-ex encoder produces bytes."""
        doc, ann, reg = MAPPERS[dataset](SYNTHETIC_RECORDS[dataset])
        result = encode_as_cbor_ld_ex(doc, ann, reg)
        assert isinstance(result, bytes)
        assert len(result) > 0

    @pytest.mark.parametrize("dataset", DATASET_NAMES)
    def test_encode_jsonldex_cbor_ld(self, dataset):
        """jsonld-ex CBOR-LD encoder produces bytes."""
        doc, ann, reg = MAPPERS[dataset](SYNTHETIC_RECORDS[dataset])
        result = encode_as_jsonldex_cbor_ld(doc, ann)
        assert isinstance(result, bytes)
        assert len(result) > 0

    @pytest.mark.parametrize("dataset", DATASET_NAMES)
    def test_encode_protobuf(self, dataset):
        """Protobuf encoder produces bytes."""
        record = SYNTHETIC_RECORDS[dataset]
        result = encode_as_protobuf(record, dataset)
        assert isinstance(result, bytes)
        assert len(result) > 0

    @pytest.mark.parametrize("dataset", DATASET_NAMES)
    def test_encode_flatbuffers(self, dataset):
        """FlatBuffers encoder produces bytes."""
        record = SYNTHETIC_RECORDS[dataset]
        result = encode_as_flatbuffers(record, dataset)
        assert isinstance(result, bytes)
        assert len(result) > 0

    @pytest.mark.parametrize("dataset", DATASET_NAMES)
    def test_encode_msgpack(self, dataset):
        """MessagePack encoder produces bytes."""
        record = SYNTHETIC_RECORDS[dataset]
        result = encode_as_msgpack(record, dataset)
        assert isinstance(result, bytes)
        assert len(result) > 0


# =====================================================================
# 4. SEMANTIC PARITY
#
# All formats that encode sensor fields must encode the SAME values.
# Self-describing formats carry metadata; schema-rigid formats do not.
# The invariant is on the sensor/measurement data, not the metadata.
# =====================================================================

class TestSemanticParity:
    """Field values must be identical across all formats after decoding."""

    @pytest.mark.parametrize("dataset", DATASET_NAMES)
    def test_jsonld_family_same_sensor_values(self, dataset):
        """All JSON-LD family formats encode identical sensor values."""
        record = SYNTHETIC_RECORDS[dataset]
        doc, ann, reg = MAPPERS[dataset](record)

        # Decode each format and extract sensor values.
        json_ld_vals = decode_json_ld(encode_as_json_ld(doc, ann))
        cbor_ld_vals = decode_cbor_ld(encode_as_cbor_ld(doc, ann, reg), reg)
        # decode_cbor_ld_ex returns (doc, annotation) — unpack.
        cbor_ld_ex_doc, _ = decode_cbor_ld_ex(
            encode_as_cbor_ld_ex(doc, ann, reg), reg
        )

        # Compare sensor fields (exclude metadata).
        meta_keys = {"@context", "@type", "@id", "@annotation"}
        for key in json_ld_vals:
            if key in meta_keys:
                continue
            assert key in cbor_ld_vals, (
                f"{dataset}: key {key!r} missing from CBOR-LD decode"
            )
            assert key in cbor_ld_ex_doc, (
                f"{dataset}: key {key!r} missing from CBOR-LD-ex decode"
            )
            if isinstance(json_ld_vals[key], float):
                assert math.isclose(
                    json_ld_vals[key], cbor_ld_vals[key], rel_tol=1e-9
                ), f"{dataset}/{key}: JSON-LD vs CBOR-LD mismatch"
                assert math.isclose(
                    json_ld_vals[key], cbor_ld_ex_doc[key], rel_tol=1e-9
                ), f"{dataset}/{key}: JSON-LD vs CBOR-LD-ex mismatch"
            else:
                assert json_ld_vals[key] == cbor_ld_vals[key]
                assert json_ld_vals[key] == cbor_ld_ex_doc[key]

    @pytest.mark.parametrize("dataset", DATASET_NAMES)
    def test_senml_encodes_same_measurements(self, dataset):
        """SenML formats encode the same measurement values as JSON-LD."""
        record = SYNTHETIC_RECORDS[dataset]
        doc, ann, reg = MAPPERS[dataset](record)

        senml_json_vals = decode_senml_json(
            encode_as_senml_json(record, dataset)
        )
        senml_cbor_vals = decode_senml_cbor(
            encode_as_senml_cbor(record, dataset)
        )

        # SenML returns list of {name: value} measurement records.
        # Verify same measurement names and values in both.
        assert len(senml_json_vals) == len(senml_cbor_vals), (
            f"{dataset}: SenML/JSON has {len(senml_json_vals)} records "
            f"but SenML/CBOR has {len(senml_cbor_vals)}"
        )
        for j_rec, c_rec in zip(senml_json_vals, senml_cbor_vals):
            assert j_rec["n"] == c_rec["n"], "SenML name mismatch"
            if isinstance(j_rec["v"], float):
                assert math.isclose(j_rec["v"], c_rec["v"], rel_tol=1e-9)
            else:
                assert j_rec["v"] == c_rec["v"]

    @pytest.mark.parametrize("dataset", DATASET_NAMES)
    def test_msgpack_encodes_same_values(self, dataset):
        """MessagePack round-trips the same raw field values."""
        record = SYNTHETIC_RECORDS[dataset]
        decoded = decode_msgpack(encode_as_msgpack(record, dataset))
        meta_keys = {"timestamp", "Timestamp", "Date", "Time", "Label",
                     "epoch", "moteid"}
        for key, value in record.items():
            if key in meta_keys:
                continue
            assert key in decoded, f"{dataset}: key {key!r} missing from msgpack"
            if isinstance(value, float):
                assert math.isclose(value, decoded[key], rel_tol=1e-9), (
                    f"{dataset}/{key}: msgpack mismatch"
                )
            else:
                assert value == decoded[key]


# =====================================================================
# 5. ROUND-TRIP
# =====================================================================

class TestRoundTrip:
    """decode(encode(record)) must recover original field values."""

    @pytest.mark.parametrize("dataset", DATASET_NAMES)
    def test_json_ld_round_trip(self, dataset):
        """JSON-LD: json.loads(json.dumps(doc)) == doc."""
        doc, ann, reg = MAPPERS[dataset](SYNTHETIC_RECORDS[dataset])
        wire = encode_as_json_ld(doc, ann)
        recovered = decode_json_ld(wire)
        # @annotation may be encoded differently; compare sensor fields.
        meta = {"@context", "@type", "@id", "@annotation"}
        for key in doc:
            if key in meta:
                continue
            assert key in recovered, f"{dataset}: key {key!r} lost in round-trip"
            if isinstance(doc[key], float):
                assert math.isclose(doc[key], recovered[key], rel_tol=1e-9)
            else:
                assert doc[key] == recovered[key]

    @pytest.mark.parametrize("dataset", DATASET_NAMES)
    def test_cbor_ld_ex_round_trip(self, dataset):
        """CBOR-LD-ex: decode recovers doc and annotation."""
        doc, ann, reg = MAPPERS[dataset](SYNTHETIC_RECORDS[dataset])
        wire = encode_as_cbor_ld_ex(doc, ann, reg)
        recovered_doc, recovered_ann = decode_cbor_ld_ex(wire, reg)
        # Verify document fields.
        meta = {"@context", "@type", "@id", "@annotation"}
        for key in doc:
            if key in meta:
                continue
            assert key in recovered_doc, f"key {key!r} lost"
            if isinstance(doc[key], float):
                assert math.isclose(doc[key], recovered_doc[key], rel_tol=1e-9)
            else:
                assert doc[key] == recovered_doc[key]
        # Verify annotation header type recovered.
        assert type(recovered_ann.header) is type(ann.header)

    @pytest.mark.parametrize("dataset", DATASET_NAMES)
    def test_protobuf_round_trip(self, dataset):
        """Protobuf: decode(encode(record)) recovers field values."""
        record = SYNTHETIC_RECORDS[dataset]
        wire = encode_as_protobuf(record, dataset)
        recovered = decode_protobuf(wire, dataset)
        meta = {"timestamp", "Timestamp", "Date", "Time", "Label",
                "epoch", "moteid"}
        for key, value in record.items():
            if key in meta:
                continue
            assert key in recovered, f"{dataset}: key {key!r} lost"
            if isinstance(value, float):
                # Protobuf float32 may lose precision; use rel_tol=1e-5.
                assert math.isclose(value, recovered[key], rel_tol=1e-5), (
                    f"{dataset}/{key}: {value} vs {recovered[key]}"
                )
            else:
                assert value == recovered[key]

    @pytest.mark.parametrize("dataset", DATASET_NAMES)
    def test_flatbuffers_round_trip(self, dataset):
        """FlatBuffers: decode(encode(record)) recovers field values."""
        record = SYNTHETIC_RECORDS[dataset]
        wire = encode_as_flatbuffers(record, dataset)
        recovered = decode_flatbuffers(wire, dataset)
        meta = {"timestamp", "Timestamp", "Date", "Time", "Label",
                "epoch", "moteid"}
        for key, value in record.items():
            if key in meta:
                continue
            assert key in recovered, f"{dataset}: key {key!r} lost"
            if isinstance(value, float):
                assert math.isclose(value, recovered[key], rel_tol=1e-5), (
                    f"{dataset}/{key}: {value} vs {recovered[key]}"
                )
            else:
                assert value == recovered[key]

    @pytest.mark.parametrize("dataset", DATASET_NAMES)
    def test_msgpack_round_trip(self, dataset):
        """MessagePack: unpackb(packb(record)) recovers field values."""
        record = SYNTHETIC_RECORDS[dataset]
        wire = encode_as_msgpack(record, dataset)
        recovered = decode_msgpack(wire)
        for key, value in record.items():
            assert key in recovered, f"{dataset}: key {key!r} lost"
            if isinstance(value, float):
                assert math.isclose(value, recovered[key], rel_tol=1e-9)
            else:
                assert value == recovered[key]


# =====================================================================
# 6. SIZE ORDERING — THE HEADLINE PAPER CLAIM
#
# For the JSON-LD family (all carrying the SAME semantic content +
# annotation), the strict ordering is:
#
#   CBOR-LD-ex  <  our CBOR-LD  <  jsonld-ex CBOR-LD  <  JSON-LD
#
# This is the core compression claim. Schema-rigid formats may beat
# all of them on raw size — the paper reports this honestly.
# =====================================================================

class TestSizeOrdering:
    """Wire-size ordering invariants for the paper."""

    @pytest.mark.parametrize("dataset", DATASET_NAMES)
    def test_jsonld_family_ordering(self, dataset):
        """CBOR-LD-ex < our CBOR-LD < JSON-LD.

        This is the guaranteed ordering for the JSON-LD family.
        jsonld-ex CBOR-LD is NOT guaranteed to beat JSON-LD because
        CBOR float64 (9 bytes) can exceed JSON's compact number
        representation (e.g. "0.0" = 3 bytes) for flag-heavy datasets
        where keys stay as strings. The paper reports jsonld-ex
        sizes honestly without claiming a universal ordering.
        """
        doc, ann, reg = MAPPERS[dataset](SYNTHETIC_RECORDS[dataset])

        size_cbor_ld_ex = len(encode_as_cbor_ld_ex(doc, ann, reg))
        size_cbor_ld = len(encode_as_cbor_ld(doc, ann, reg))
        size_json_ld = len(encode_as_json_ld(doc, ann))

        assert size_cbor_ld_ex < size_cbor_ld, (
            f"{dataset}: CBOR-LD-ex ({size_cbor_ld_ex}B) "
            f"not smaller than CBOR-LD ({size_cbor_ld}B)"
        )
        assert size_cbor_ld < size_json_ld, (
            f"{dataset}: CBOR-LD ({size_cbor_ld}B) "
            f"not smaller than JSON-LD ({size_json_ld}B)"
        )

    @pytest.mark.parametrize("dataset", DATASET_NAMES)
    def test_senml_cbor_smaller_than_senml_json(self, dataset):
        """SenML/CBOR < SenML/JSON (integer keys vs string keys)."""
        record = SYNTHETIC_RECORDS[dataset]
        size_cbor = len(encode_as_senml_cbor(record, dataset))
        size_json = len(encode_as_senml_json(record, dataset))
        assert size_cbor < size_json, (
            f"{dataset}: SenML/CBOR ({size_cbor}B) "
            f"not smaller than SenML/JSON ({size_json}B)"
        )

    @pytest.mark.parametrize("dataset", DATASET_NAMES)
    def test_all_sizes_reported(self, dataset):
        """All 9 formats produce measurable wire sizes for every dataset."""
        record = SYNTHETIC_RECORDS[dataset]
        sizes = encode_record_all_formats(record, dataset)
        assert len(sizes) == len(ALL_FORMATS), (
            f"{dataset}: expected {len(ALL_FORMATS)} formats, "
            f"got {len(sizes)}"
        )
        for fmt, wire_bytes in sizes.items():
            assert len(wire_bytes) > 0, f"{dataset}/{fmt}: empty output"


# =====================================================================
# 7. FRAME-FIT
#
# Correct classification of wire sizes against constrained-network
# MTU thresholds. The constants must match the paper text exactly.
# =====================================================================

class TestFrameFit:
    """Frame-fit classification against real MTU constants."""

    def test_mtu_constants_values(self):
        """MTU constants match protocol specifications."""
        assert MTU_CONSTANTS["802.15.4"] == 127
        assert MTU_CONSTANTS["BLE"] == 247
        assert MTU_CONSTANTS["LoRaWAN_SF7"] == 242
        assert MTU_CONSTANTS["LoRaWAN_SF10"] == 115
        assert MTU_CONSTANTS["LoRaWAN_SF12"] == 51

    def test_mtu_constants_completeness(self):
        """All five MTU thresholds present."""
        expected = {"802.15.4", "BLE", "LoRaWAN_SF7",
                    "LoRaWAN_SF10", "LoRaWAN_SF12"}
        assert set(MTU_CONSTANTS.keys()) == expected

    def test_frame_fit_small_message(self):
        """A 50-byte message fits all frames."""
        result = frame_fit(50)
        for name, fits in result.items():
            assert fits is True, f"50B should fit {name} ({MTU_CONSTANTS[name]}B)"

    def test_frame_fit_medium_message(self):
        """A 120-byte message fits BLE and LoRaWAN SF7 but not 802.15.4
        or LoRaWAN SF10/SF12."""
        result = frame_fit(120)
        assert result["802.15.4"] is True     # 120 <= 127
        assert result["BLE"] is True          # 120 <= 247
        assert result["LoRaWAN_SF7"] is True  # 120 <= 242
        assert result["LoRaWAN_SF10"] is False  # 120 > 115
        assert result["LoRaWAN_SF12"] is False  # 120 > 51

    def test_frame_fit_large_message(self):
        """A 250-byte message fits nothing."""
        result = frame_fit(250)
        for name, fits in result.items():
            assert fits is False, f"250B should not fit {name}"

    def test_frame_fit_boundary_exact(self):
        """Exact MTU boundary: message of exactly MTU bytes fits."""
        for name, mtu in MTU_CONSTANTS.items():
            result = frame_fit(mtu)
            assert result[name] is True, (
                f"{mtu}B should fit {name} (boundary)"
            )

    def test_frame_fit_boundary_exceeded(self):
        """One byte over MTU: does not fit."""
        for name, mtu in MTU_CONSTANTS.items():
            result = frame_fit(mtu + 1)
            assert result[name] is False, (
                f"{mtu + 1}B should not fit {name}"
            )

    @pytest.mark.parametrize("dataset", DATASET_NAMES)
    def test_frame_fit_cbor_ld_ex_record(self, dataset):
        """Frame-fit of CBOR-LD-ex record is consistent with its size."""
        doc, ann, reg = MAPPERS[dataset](SYNTHETIC_RECORDS[dataset])
        wire = encode_as_cbor_ld_ex(doc, ann, reg)
        size = len(wire)
        result = frame_fit(size)
        for name, mtu in MTU_CONSTANTS.items():
            expected = size <= mtu
            assert result[name] == expected, (
                f"{dataset}: {size}B vs {name} ({mtu}B) "
                f"expected {expected}, got {result[name]}"
            )


# =====================================================================
# 8. DETERMINISM
#
# The same record must produce byte-identical output every time.
# Required for reproducible experiments and for the paper's tables
# to be verifiable.
# =====================================================================

class TestDeterminism:
    """Encoding the same record twice produces identical bytes."""

    @pytest.mark.parametrize("dataset", DATASET_NAMES)
    @pytest.mark.parametrize("format_name", ALL_FORMATS)
    def test_deterministic_encoding(self, dataset, format_name):
        """Two encodes of the same record produce identical bytes."""
        record = SYNTHETIC_RECORDS[dataset]
        doc, ann, reg = MAPPERS[dataset](record)

        def _encode():
            if format_name == "json_ld":
                return encode_as_json_ld(doc, ann)
            elif format_name == "senml_json":
                return encode_as_senml_json(record, dataset)
            elif format_name == "senml_cbor":
                return encode_as_senml_cbor(record, dataset)
            elif format_name == "cbor_ld":
                return encode_as_cbor_ld(doc, ann, reg)
            elif format_name == "cbor_ld_ex":
                return encode_as_cbor_ld_ex(doc, ann, reg)
            elif format_name == "jsonldex_cbor_ld":
                return encode_as_jsonldex_cbor_ld(doc, ann)
            elif format_name == "protobuf":
                return encode_as_protobuf(record, dataset)
            elif format_name == "flatbuffers":
                return encode_as_flatbuffers(record, dataset)
            elif format_name == "msgpack":
                return encode_as_msgpack(record, dataset)
            else:
                raise ValueError(f"Unknown format: {format_name}")

        first = _encode()
        second = _encode()
        assert first == second, (
            f"{dataset}/{format_name}: non-deterministic encoding "
            f"({len(first)}B vs {len(second)}B)"
        )


# =====================================================================
# 9. SCHEMA COLUMN CONSTANTS
#
# The exported column-name tuples must match the actual dataset schemas.
# These are the ground truth for the record mappers.
# =====================================================================

class TestSchemaConstants:
    """Column-name constants match actual dataset schemas."""

    def test_intel_lab_columns(self):
        """Intel Lab has the expected sensor columns."""
        expected = {"timestamp", "epoch", "moteid",
                    "temperature", "humidity", "light", "voltage"}
        assert set(INTEL_LAB_COLUMNS) == expected

    def test_uci_aq_columns(self):
        """UCI Air Quality has the expected columns."""
        expected = {"Date", "Time", "CO_GT", "PT08_S1_CO", "NMHC_GT",
                    "C6H6_GT", "PT08_S2_NMHC", "NOx_GT", "PT08_S3_NOx",
                    "NO2_GT", "PT08_S4_NO2", "T", "RH", "AH"}
        assert set(UCI_AQ_COLUMNS) == expected

    def test_ciciot_columns_count(self):
        """CIC-IoT-2023 has exactly 39 feature columns."""
        assert len(CICIOT_FEATURE_COLUMNS) == 39

    def test_ciciot_columns_known_subset(self):
        """CIC-IoT-2023 includes known feature names."""
        known = {"Header_Length", "Protocol Type", "Rate", "Tot sum",
                 "IAT", "Number", "Variance", "HTTP", "TCP", "UDP"}
        assert known.issubset(set(CICIOT_FEATURE_COLUMNS))

    def test_swat_sensor_columns_stages(self):
        """SWaT A8 sensor columns span all 6 process stages."""
        cols = set(SWAT_A8_SENSOR_COLUMNS)
        # Stage 1
        assert "FIT101" in cols and "LIT101" in cols
        # Stage 2
        assert "AIT201" in cols and "FIT201" in cols
        # Stage 3
        assert "LIT301" in cols and "DPIT301" in cols
        # Stage 4
        assert "FIT401" in cols and "LIT401" in cols
        # Stage 5
        assert "PIT501" in cols and "AIT501" in cols
        # Stage 6
        assert "FIT601" in cols and "LIT601" in cols

    def test_swat_sensor_columns_exclude_attack_metadata(self):
        """SWaT sensor columns do NOT include attack annotation fields."""
        cols = set(SWAT_A8_SENSOR_COLUMNS)
        attack_fields = {"Annotation", "Other Anomalies", "Attack Hash",
                         "Attack Name", "Attack State", "Attack Target"}
        assert cols.isdisjoint(attack_fields), (
            f"Sensor columns include attack metadata: "
            f"{cols & attack_fields}"
        )
