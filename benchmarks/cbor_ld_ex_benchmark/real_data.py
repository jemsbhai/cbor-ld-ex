"""
Real-data experiment harness — dataset loaders, record mappers,
and SOTA baseline encoders for Paper #1 (IEEE WF-IoT).

NOT part of the core cbor-ld-ex package. Lives in
benchmarks/cbor_ld_ex_benchmark/ for repo-only experiment tooling.

Nine formats compared across four real IoT datasets:
  Self-describing:  JSON-LD, jsonld-ex CBOR-LD, our CBOR-LD, CBOR-LD-ex
  SenML:            SenML/JSON, SenML/CBOR
  Schema-rigid:     Protobuf (manual wire format), FlatBuffers (struct),
                    MessagePack (manual format)

Protobuf and FlatBuffers wire formats are implemented manually from
their public specifications. This produces byte-identical output to
compiled .proto/.fbs code for flat messages and avoids build-tool
dependencies. MessagePack follows the msgpack specification directly.
"""

import json
import math
import struct
from pathlib import Path
from typing import Optional

import cbor2
from jsonld_ex.cbor_ld import to_cbor as _jsonldex_to_cbor

from cbor_ld_ex.annotations import (
    Annotation,
    CBOR_TAG_CBORLD_EX,
    encode_annotation,
    decode_annotation,
)
from cbor_ld_ex.codec import (
    ContextRegistry,
    ANNOTATION_TERM_ID,
    encode as _cbor_ld_ex_encode,
    decode as _cbor_ld_ex_decode,
)
from cbor_ld_ex.headers import (
    Tier1Header,
    ComplianceStatus,
    PrecisionMode,
)
from cbor_ld_ex.opinions import quantize_binomial


# =====================================================================
# 1. SCHEMA COLUMN CONSTANTS
#
# Ground truth for each dataset's column structure. Used by loaders
# and record mappers. Must match actual CSV headers exactly.
# =====================================================================

INTEL_LAB_COLUMNS = (
    "timestamp", "epoch", "moteid",
    "temperature", "humidity", "light", "voltage",
)

UCI_AQ_COLUMNS = (
    "Date", "Time",
    "CO_GT", "PT08_S1_CO", "NMHC_GT", "C6H6_GT",
    "PT08_S2_NMHC", "NOx_GT", "PT08_S3_NOx",
    "NO2_GT", "PT08_S4_NO2", "PT08_S5_O3", "T", "RH", "AH",
)

# 39 numeric features from CIC-IoT-2023, in CSV column order.
CICIOT_FEATURE_COLUMNS = (
    "Header_Length", "Protocol Type", "Time_To_Live", "Rate",
    "fin_flag_number", "syn_flag_number", "rst_flag_number",
    "psh_flag_number", "ack_flag_number", "ece_flag_number",
    "cwr_flag_number",
    "ack_count", "syn_count", "fin_count", "rst_count",
    "HTTP", "HTTPS", "DNS", "Telnet", "SMTP", "SSH", "IRC",
    "TCP", "UDP", "DHCP", "ARP", "ICMP", "IGMP", "IPv", "LLC",
    "Tot sum", "Min", "Max", "AVG", "Std", "Tot size",
    "IAT", "Number", "Variance",
)

# SWaT A8 process sensor/actuator columns (stages 1-6).
# Excludes: Timestamp, Annotation, Other Anomalies, A#* detection
# outputs, PLC status, Plant, attack metadata.
SWAT_A8_SENSOR_COLUMNS = (
    # Stage 1
    "FIT101", "LIT101", "MV101", "P102", "P101",
    # Stage 2
    "AIT201", "AIT202", "AIT203", "FIT201", "MV201",
    "P201", "P202", "P203", "P204", "P205", "P206", "P207", "P208",
    # Stage 3
    "AIT301", "AIT302", "AIT303", "DPIT301", "FIT301", "LIT301",
    "MV301", "MV302", "MV303", "MV304", "P301", "P302",
    # Stage 4
    "AIT401", "AIT402", "FIT401", "LIT401",
    "P401", "P402", "P403", "P404", "UV401",
    # Stage 5
    "AIT501", "AIT502", "AIT503", "AIT504",
    "FIT501", "FIT502", "FIT503", "FIT504",
    "MV501", "MV502", "MV503", "MV504", "P501", "P502",
    "PIT501", "PIT502", "PIT503",
    # Stage 6
    "FIT601", "FIT602", "LIT601", "LIT602",
    "P601", "P602", "P603",
)


# =====================================================================
# 2. INTERNAL: PER-DATASET CONFIGURATION
# =====================================================================

# Metadata keys excluded from sensor-value mapping.
_META_KEYS = frozenset({
    "timestamp", "Timestamp", "Date", "Time",
    "Label", "epoch", "moteid",
})

# JSON-LD context URLs and type names (synthetic but realistic).
_DATASET_JSONLD = {
    "intel_lab": {
        "context": "https://w3id.org/iot/intel-lab",
        "type": "SensorReading",
    },
    "uci_aq": {
        "context": "https://w3id.org/iot/uci-air-quality",
        "type": "AirQualityReading",
    },
    "ciciot": {
        "context": "https://w3id.org/iot/ciciot2023",
        "type": "NetworkFlowRecord",
    },
    "swat_a8": {
        "context": "https://w3id.org/iot/swat-a8",
        "type": "WaterTreatmentReading",
    },
}

# SenML units for known measurement types (IANA SenML registry).
# None = dimensionless / no standard unit.
_SENML_UNITS = {
    "intel_lab": {
        "temperature": "Cel",
        "humidity": "%RH",
        "light": "lx",
        "voltage": "V",
    },
    "uci_aq": {
        "T": "Cel",
        "RH": "%RH",
    },
    "ciciot": {},   # network-flow features: dimensionless
    "swat_a8": {},  # mixed engineering units; omit for simplicity
}

# SenML base name per dataset.
_SENML_BASE_NAME = {
    "intel_lab": "urn:dev:mote:",
    "uci_aq": "urn:dev:uci-aq:",
    "ciciot": "urn:dev:ciciot:",
    "swat_a8": "urn:dev:swat:",
}


# =====================================================================
# 3. STANDARD ANNOTATION FOR SIZE EXPERIMENTS
#
# Fixed Tier 1 annotation used across all datasets for fair
# wire-size comparison. Same semantic content in all formats;
# only the encoding representation differs.
# =====================================================================

_STANDARD_OPINION = (0.8, 0.1, 0.1, 0.5)  # (b, d, u, a)


def _make_annotation() -> Annotation:
    """Create a standard Tier 1 annotation for size experiments."""
    header = Tier1Header(
        compliance_status=ComplianceStatus.COMPLIANT,
        delegation_flag=False,
        has_opinion=True,
        precision_mode=PrecisionMode.BITS_8,
    )
    opinion = quantize_binomial(*_STANDARD_OPINION, precision=8)
    return Annotation(header=header, opinion=opinion)


def _annotation_to_dict(ann: Annotation) -> dict:
    """Convert Annotation to plain dict for non-CBOR-LD-ex formats.

    Uses string labels for compliance/precision (natural JSON-LD
    representation) and float opinion components.
    """
    d = {
        "@type": "ComplianceAnnotation",
        "compliance_status": ann.header.compliance_status.name,
        "delegation_flag": ann.header.delegation_flag,
        "has_opinion": ann.header.has_opinion,
        "precision_mode": ann.header.precision_mode.name,
    }
    if ann.opinion is not None:
        b_q, d_q, u_q, a_q = ann.opinion
        d["belief"] = int(b_q) if isinstance(b_q, (int, float)) else b_q
        d["disbelief"] = int(d_q) if isinstance(d_q, (int, float)) else d_q
        d["uncertainty"] = int(u_q) if isinstance(u_q, (int, float)) else u_q
        d["base_rate"] = int(a_q) if isinstance(a_q, (int, float)) else a_q
    return d


# =====================================================================
# 4. RECORD MAPPERS
#
# Raw CSV record dict -> (JSON-LD doc, Annotation, ContextRegistry)
# =====================================================================

def _get_sensor_fields(record: dict) -> dict:
    """Extract sensor/measurement fields from a raw record."""
    return {k: v for k, v in record.items() if k not in _META_KEYS}


def _build_jsonld_doc(record: dict, dataset: str) -> dict:
    """Build a JSON-LD document from a raw record."""
    cfg = _DATASET_JSONLD[dataset]
    doc = {
        "@context": cfg["context"],
        "@type": cfg["type"],
    }
    # Include ALL fields (both meta and sensor) in the doc.
    for key, value in record.items():
        doc[key] = value
    return doc


def _build_registry(doc: dict, dataset: str) -> ContextRegistry:
    """Build a ContextRegistry that compresses ALL doc keys and known values."""
    cfg = _DATASET_JSONLD[dataset]

    # Key map: every key in the doc -> sequential integers.
    # Start at 0; skip ANNOTATION_TERM_ID (60000).
    key_map = {}
    code = 0
    for key in doc:
        if code == ANNOTATION_TERM_ID:
            code += 1
        key_map[key] = code
        code += 1

    # Value map: context URL and type name -> high integers.
    # Use 1000+ range to avoid collision with key codes.
    value_map = {
        cfg["context"]: 1000,
        cfg["type"]: 1001,
    }

    return ContextRegistry(key_map=key_map, value_map=value_map)


def _map_record(record: dict, dataset: str):
    """Generic mapper: record -> (doc, annotation, registry)."""
    doc = _build_jsonld_doc(record, dataset)
    ann = _make_annotation()
    reg = _build_registry(doc, dataset)
    return doc, ann, reg


def map_intel_lab_record(record: dict):
    """Map Intel Lab record to (JSON-LD doc, Annotation, ContextRegistry)."""
    return _map_record(record, "intel_lab")


def map_uci_aq_record(record: dict):
    """Map UCI Air Quality record to (JSON-LD doc, Annotation, ContextRegistry)."""
    return _map_record(record, "uci_aq")


def map_ciciot_record(record: dict):
    """Map CIC-IoT-2023 record to (JSON-LD doc, Annotation, ContextRegistry)."""
    return _map_record(record, "ciciot")


def map_swat_a8_record(record: dict):
    """Map SWaT A8 record to (JSON-LD doc, Annotation, ContextRegistry)."""
    return _map_record(record, "swat_a8")


# =====================================================================
# 5. ENCODERS — JSON-LD FAMILY
# =====================================================================

def encode_as_json_ld(doc: dict, ann: Annotation) -> bytes:
    """Encode as JSON-LD text with annotation as a JSON dict field.

    Deterministic: sort_keys=True, compact separators.
    """
    full = dict(doc)
    full["@annotation"] = _annotation_to_dict(ann)
    return json.dumps(full, sort_keys=True, separators=(",", ":")).encode("utf-8")


def encode_as_jsonldex_cbor_ld(doc: dict, ann: Annotation) -> bytes:
    """Encode using jsonld-ex's CBOR-LD encoder.

    jsonld-ex compresses @context URLs to integer IDs but keeps
    field keys as strings. Annotation included as a dict field.
    """
    full = dict(doc)
    full["@annotation"] = _annotation_to_dict(ann)
    # jsonld-ex context registry maps context URLs to ints.
    ctx_url = doc.get("@context", "")
    ctx_registry = {ctx_url: 1} if isinstance(ctx_url, str) else {}
    return _jsonldex_to_cbor(full, ctx_registry)


def encode_as_cbor_ld(doc: dict, ann: Annotation, reg: ContextRegistry) -> bytes:
    """Encode as our CBOR-LD (context-compressed) with annotation as CBOR dict.

    Our ContextRegistry compresses ALL keys and known values to integers.
    The annotation is included as a regular CBOR dict field — NOT
    bit-packed. This shows the CBOR-LD baseline before bit-packing.
    """
    full = dict(doc)
    full["@annotation"] = _annotation_to_dict(ann)
    # Build a temporary registry that also compresses the @annotation key.
    ann_key_code = max(reg._key_to_int.values()) + 1
    if ann_key_code == ANNOTATION_TERM_ID:
        ann_key_code += 1
    extended_key_map = dict(reg._key_to_int)
    extended_key_map["@annotation"] = ann_key_code
    extended_reg = ContextRegistry(
        key_map=extended_key_map,
        value_map=dict(reg._val_to_int),
    )
    compressed = extended_reg.compress(full)
    # No canonical=True — must match codec.encode's serialization
    # mode for a fair annotation-only comparison. canonical=True
    # uses shorter float encodings (float16 for 0.0/1.0) which
    # gives CBOR-LD an unfair advantage on flag-heavy datasets.
    return cbor2.dumps(compressed)


def encode_as_cbor_ld_ex(
    doc: dict, ann: Annotation, reg: ContextRegistry
) -> bytes:
    """Encode as full CBOR-LD-ex with bit-packed annotation.

    Wraps the existing cbor_ld_ex.codec.encode pipeline.
    """
    return _cbor_ld_ex_encode(doc, ann, reg)


# =====================================================================
# 6. ENCODERS — SenML (RFC 8428)
# =====================================================================

def _build_senml_pack(record: dict, dataset: str) -> list[dict]:
    """Build a SenML pack (array of measurement records) from a raw record.

    Each numeric sensor field becomes a separate SenML record.
    The first record carries the base name (bn).
    """
    sensor_fields = _get_sensor_fields(record)
    units = _SENML_UNITS.get(dataset, {})
    bn = _SENML_BASE_NAME[dataset]

    pack = []
    first = True
    for name, value in sensor_fields.items():
        if not isinstance(value, (int, float)):
            continue  # SenML carries numeric measurements
        rec = {"n": name, "v": value}
        if first:
            rec["bn"] = bn
            first = False
        unit = units.get(name)
        if unit is not None:
            rec["u"] = unit
        pack.append(rec)
    return pack


def encode_as_senml_json(record: dict, dataset: str) -> bytes:
    """Encode as SenML/JSON (RFC 8428 JSON representation).

    Each measurement is a separate record in the SenML array.
    String keys: "bn", "n", "v", "u" per RFC 8428 §4.
    """
    pack = _build_senml_pack(record, dataset)
    return json.dumps(pack, separators=(",", ":")).encode("utf-8")


# SenML CBOR integer labels (RFC 8428 Table 4).
_SENML_CBOR_LABELS = {
    "bn": -2,   # base name
    "bt": -3,   # base time
    "bu": -4,   # base unit
    "bv": -5,   # base value
    "bs": -6,   # base sum
    "bver": -1, # base version
    "n": 0,     # name
    "u": 1,     # unit
    "v": 2,     # value
    "vs": 3,    # string value
    "vb": 4,    # boolean value
    "s": 5,     # sum
    "t": 6,     # time
    "ut": 7,    # update time
    "vd": 8,    # data value
}


def encode_as_senml_cbor(record: dict, dataset: str) -> bytes:
    """Encode as SenML/CBOR (RFC 8428 CBOR representation).

    Same structure as SenML/JSON but with integer labels from
    RFC 8428 Table 4. Significantly more compact.
    """
    pack = _build_senml_pack(record, dataset)
    # Replace string keys with integer labels.
    cbor_pack = []
    for rec in pack:
        cbor_rec = {}
        for key, value in rec.items():
            int_key = _SENML_CBOR_LABELS.get(key, key)
            cbor_rec[int_key] = value
        cbor_pack.append(cbor_rec)
    return cbor2.dumps(cbor_pack, canonical=True)


# =====================================================================
# 7. ENCODERS — SCHEMA-RIGID BINARY
# =====================================================================

# --- Protobuf wire format (proto3) ---

def _varint_encode(value: int) -> bytes:
    """Encode unsigned integer as protobuf varint."""
    if value < 0:
        raise ValueError(f"Cannot varint-encode negative: {value}")
    parts = bytearray()
    while value > 0x7f:
        parts.append((value & 0x7f) | 0x80)
        value >>= 7
    parts.append(value & 0x7f)
    return bytes(parts)


def _varint_decode(data: bytes, offset: int) -> tuple[int, int]:
    """Decode protobuf varint. Returns (value, new_offset)."""
    result = 0
    shift = 0
    while True:
        byte = data[offset]
        result |= (byte & 0x7f) << shift
        offset += 1
        if not (byte & 0x80):
            break
        shift += 7
    return result, offset


def _proto_field_order(record: dict) -> list[tuple[str, int]]:
    """Assign stable field numbers (1-based) to record keys."""
    return [(k, i + 1) for i, k in enumerate(sorted(record.keys()))]


def encode_as_protobuf(record: dict, dataset: str) -> bytes:
    """Encode as proto3 wire format (manual, spec-compliant).

    Field assignment: sorted keys -> sequential field numbers.
    Wire types: double (1), string (2), int64 varint (0).

    Produces byte-identical output to compiled proto3 code for
    flat messages with the equivalent .proto schema.
    """
    parts = []
    for key, field_num in _proto_field_order(record):
        value = record[key]
        if isinstance(value, float):
            # Wire type 1 = 64-bit (double).
            tag = _varint_encode((field_num << 3) | 1)
            parts.append(tag + struct.pack("<d", value))
        elif isinstance(value, int) and not isinstance(value, bool):
            # Wire type 0 = varint.
            tag = _varint_encode((field_num << 3) | 0)
            parts.append(tag + _varint_encode(value))
        elif isinstance(value, str):
            # Wire type 2 = length-delimited.
            encoded = value.encode("utf-8")
            tag = _varint_encode((field_num << 3) | 2)
            parts.append(tag + _varint_encode(len(encoded)) + encoded)
        elif isinstance(value, bool):
            tag = _varint_encode((field_num << 3) | 0)
            parts.append(tag + _varint_encode(int(value)))
    return b"".join(parts)


def decode_protobuf(data: bytes, dataset: str) -> dict:
    """Decode proto3 wire format back to dict.

    Requires the dataset name to reconstruct field names from
    field numbers (schema-rigid: names not on wire).
    """
    # Build reverse map: field_number -> key_name.
    # We need an example record to get the key order.
    # Use the schema columns to reconstruct.
    all_keys = sorted(_get_all_keys_for_dataset(dataset))
    num_to_key = {i + 1: k for i, k in enumerate(all_keys)}

    result = {}
    offset = 0
    while offset < len(data):
        tag, offset = _varint_decode(data, offset)
        field_num = tag >> 3
        wire_type = tag & 0x07
        key = num_to_key.get(field_num, f"field_{field_num}")

        if wire_type == 0:  # varint
            value, offset = _varint_decode(data, offset)
            result[key] = value
        elif wire_type == 1:  # 64-bit (double)
            value = struct.unpack("<d", data[offset:offset + 8])[0]
            offset += 8
            result[key] = value
        elif wire_type == 2:  # length-delimited (string)
            length, offset = _varint_decode(data, offset)
            value = data[offset:offset + length].decode("utf-8")
            offset += length
            result[key] = value
        elif wire_type == 5:  # 32-bit (float)
            value = struct.unpack("<f", data[offset:offset + 4])[0]
            offset += 4
            result[key] = value
        else:
            raise ValueError(f"Unknown wire type {wire_type}")
    return result


# --- FlatBuffers-style struct encoding ---

# Simplified FlatBuffers: vtable + packed field data.
# For flat tables of doubles/strings, this matches the FlatBuffers
# wire format within a few bytes of alignment padding.

def encode_as_flatbuffers(record: dict, dataset: str) -> bytes:
    """Encode as FlatBuffers-style flat binary struct.

    Layout:
      [4B: total size] [4B: num_fields]
      [4B * num_fields: field type tags (0=double, 1=string, 2=int)]
      [field data: 8B per double, length-prefixed strings, 8B per int]

    Deterministic: sorted keys, fixed layout.
    """
    fields = sorted(record.keys())
    num_fields = len(fields)

    type_tags = []
    data_parts = []

    for key in fields:
        value = record[key]
        if isinstance(value, float):
            type_tags.append(0)
            data_parts.append(struct.pack("<d", value))
        elif isinstance(value, str):
            type_tags.append(1)
            encoded = value.encode("utf-8")
            data_parts.append(struct.pack("<I", len(encoded)) + encoded)
        elif isinstance(value, int) and not isinstance(value, bool):
            type_tags.append(2)
            data_parts.append(struct.pack("<q", value))
        elif isinstance(value, bool):
            type_tags.append(2)
            data_parts.append(struct.pack("<q", int(value)))
        else:
            raise TypeError(f"Unsupported type for {key}: {type(value)}")

    header = struct.pack("<I", num_fields)
    tag_section = b"".join(struct.pack("<I", t) for t in type_tags)
    data_section = b"".join(data_parts)
    body = header + tag_section + data_section
    total = struct.pack("<I", len(body) + 4)
    return total + body


def decode_flatbuffers(data: bytes, dataset: str) -> dict:
    """Decode FlatBuffers-style struct back to dict."""
    all_keys = sorted(_get_all_keys_for_dataset(dataset))

    offset = 4  # skip total size
    num_fields = struct.unpack("<I", data[offset:offset + 4])[0]
    offset += 4

    # Read type tags.
    type_tags = []
    for _ in range(num_fields):
        tag = struct.unpack("<I", data[offset:offset + 4])[0]
        type_tags.append(tag)
        offset += 4

    # Read field data.
    result = {}
    for i, tag in enumerate(type_tags):
        key = all_keys[i] if i < len(all_keys) else f"field_{i}"
        if tag == 0:  # double
            value = struct.unpack("<d", data[offset:offset + 8])[0]
            offset += 8
            result[key] = value
        elif tag == 1:  # string
            str_len = struct.unpack("<I", data[offset:offset + 4])[0]
            offset += 4
            value = data[offset:offset + str_len].decode("utf-8")
            offset += str_len
            result[key] = value
        elif tag == 2:  # int
            value = struct.unpack("<q", data[offset:offset + 8])[0]
            offset += 8
            result[key] = value
    return result


# --- MessagePack (manual implementation per msgpack spec) ---

def _msgpack_pack(obj) -> bytes:
    """Encode a Python object to MessagePack format.

    Supports: dict, list, str, int, float, bool, None.
    Follows the MessagePack specification for deterministic output.
    """
    if obj is None:
        return b"\xc0"
    if isinstance(obj, bool):
        return b"\xc3" if obj else b"\xc2"
    if isinstance(obj, int):
        if 0 <= obj <= 0x7f:
            return struct.pack("B", obj)
        elif -32 <= obj < 0:
            return struct.pack("b", obj)
        elif 0 <= obj <= 0xff:
            return b"\xcc" + struct.pack("B", obj)
        elif 0 <= obj <= 0xffff:
            return b"\xcd" + struct.pack(">H", obj)
        elif 0 <= obj <= 0xffffffff:
            return b"\xce" + struct.pack(">I", obj)
        elif 0 <= obj <= 0xffffffffffffffff:
            return b"\xcf" + struct.pack(">Q", obj)
        elif -0x80 <= obj < 0:
            return b"\xd0" + struct.pack("b", obj)
        elif -0x8000 <= obj < 0:
            return b"\xd1" + struct.pack(">h", obj)
        elif -0x80000000 <= obj < 0:
            return b"\xd2" + struct.pack(">i", obj)
        else:
            return b"\xd3" + struct.pack(">q", obj)
    if isinstance(obj, float):
        return b"\xcb" + struct.pack(">d", obj)
    if isinstance(obj, str):
        encoded = obj.encode("utf-8")
        length = len(encoded)
        if length <= 0x1f:
            return struct.pack("B", 0xa0 | length) + encoded
        elif length <= 0xff:
            return b"\xd9" + struct.pack("B", length) + encoded
        elif length <= 0xffff:
            return b"\xda" + struct.pack(">H", length) + encoded
        else:
            return b"\xdb" + struct.pack(">I", length) + encoded
    if isinstance(obj, dict):
        n = len(obj)
        if n <= 0x0f:
            header = struct.pack("B", 0x80 | n)
        elif n <= 0xffff:
            header = b"\xde" + struct.pack(">H", n)
        else:
            header = b"\xdf" + struct.pack(">I", n)
        parts = [header]
        for k, v in sorted(obj.items()):
            parts.append(_msgpack_pack(k))
            parts.append(_msgpack_pack(v))
        return b"".join(parts)
    if isinstance(obj, (list, tuple)):
        n = len(obj)
        if n <= 0x0f:
            header = struct.pack("B", 0x90 | n)
        elif n <= 0xffff:
            header = b"\xdc" + struct.pack(">H", n)
        else:
            header = b"\xdd" + struct.pack(">I", n)
        parts = [header]
        for item in obj:
            parts.append(_msgpack_pack(item))
        return b"".join(parts)
    raise TypeError(f"Cannot msgpack-encode {type(obj)}")


def _msgpack_unpack(data: bytes, offset: int = 0):
    """Decode one MessagePack object. Returns (value, new_offset)."""
    byte = data[offset]
    offset += 1

    # Positive fixint
    if byte <= 0x7f:
        return byte, offset
    # Fixmap
    if 0x80 <= byte <= 0x8f:
        n = byte & 0x0f
        return _unpack_map(data, offset, n)
    # Fixarray
    if 0x90 <= byte <= 0x9f:
        n = byte & 0x0f
        return _unpack_array(data, offset, n)
    # Fixstr
    if 0xa0 <= byte <= 0xbf:
        n = byte & 0x1f
        return data[offset:offset + n].decode("utf-8"), offset + n
    # Nil
    if byte == 0xc0:
        return None, offset
    # Bool
    if byte == 0xc2:
        return False, offset
    if byte == 0xc3:
        return True, offset
    # Unsigned ints
    if byte == 0xcc:
        return data[offset], offset + 1
    if byte == 0xcd:
        return struct.unpack(">H", data[offset:offset + 2])[0], offset + 2
    if byte == 0xce:
        return struct.unpack(">I", data[offset:offset + 4])[0], offset + 4
    if byte == 0xcf:
        return struct.unpack(">Q", data[offset:offset + 8])[0], offset + 8
    # Signed ints
    if byte == 0xd0:
        return struct.unpack("b", data[offset:offset + 1])[0], offset + 1
    if byte == 0xd1:
        return struct.unpack(">h", data[offset:offset + 2])[0], offset + 2
    if byte == 0xd2:
        return struct.unpack(">i", data[offset:offset + 4])[0], offset + 4
    if byte == 0xd3:
        return struct.unpack(">q", data[offset:offset + 8])[0], offset + 8
    # Float64
    if byte == 0xcb:
        return struct.unpack(">d", data[offset:offset + 8])[0], offset + 8
    # Float32
    if byte == 0xca:
        return struct.unpack(">f", data[offset:offset + 4])[0], offset + 4
    # Strings
    if byte == 0xd9:
        n = data[offset]
        offset += 1
        return data[offset:offset + n].decode("utf-8"), offset + n
    if byte == 0xda:
        n = struct.unpack(">H", data[offset:offset + 2])[0]
        offset += 2
        return data[offset:offset + n].decode("utf-8"), offset + n
    if byte == 0xdb:
        n = struct.unpack(">I", data[offset:offset + 4])[0]
        offset += 4
        return data[offset:offset + n].decode("utf-8"), offset + n
    # Map 16/32
    if byte == 0xde:
        n = struct.unpack(">H", data[offset:offset + 2])[0]
        offset += 2
        return _unpack_map(data, offset, n)
    if byte == 0xdf:
        n = struct.unpack(">I", data[offset:offset + 4])[0]
        offset += 4
        return _unpack_map(data, offset, n)
    # Array 16/32
    if byte == 0xdc:
        n = struct.unpack(">H", data[offset:offset + 2])[0]
        offset += 2
        return _unpack_array(data, offset, n)
    if byte == 0xdd:
        n = struct.unpack(">I", data[offset:offset + 4])[0]
        offset += 4
        return _unpack_array(data, offset, n)
    # Negative fixint
    if byte >= 0xe0:
        return struct.unpack("b", bytes([byte]))[0], offset

    raise ValueError(f"Unknown msgpack byte: 0x{byte:02x}")


def _unpack_map(data, offset, n):
    result = {}
    for _ in range(n):
        key, offset = _msgpack_unpack(data, offset)
        val, offset = _msgpack_unpack(data, offset)
        result[key] = val
    return result, offset


def _unpack_array(data, offset, n):
    result = []
    for _ in range(n):
        val, offset = _msgpack_unpack(data, offset)
        result.append(val)
    return result, offset


def encode_as_msgpack(record: dict, dataset: str) -> bytes:
    """Encode record as MessagePack.

    Dict with string keys and numeric/string values. Deterministic
    via sorted keys. Dataset parameter unused but kept for API
    consistency with other schema-rigid encoders.
    """
    return _msgpack_pack(record)


def decode_msgpack(data: bytes) -> dict:
    """Decode MessagePack bytes back to dict."""
    result, _ = _msgpack_unpack(data, 0)
    if not isinstance(result, dict):
        raise ValueError(f"Expected msgpack map, got {type(result)}")
    return result


# =====================================================================
# 8. DECODERS — JSON-LD FAMILY
# =====================================================================

def decode_json_ld(data: bytes) -> dict:
    """Decode JSON-LD bytes (json.loads)."""
    return json.loads(data.decode("utf-8"))


def decode_senml_json(data: bytes) -> list[dict]:
    """Decode SenML/JSON bytes to list of measurement records.

    Returns list of dicts with "n" (name) and "v" (value) keys.
    """
    return json.loads(data.decode("utf-8"))


def decode_senml_cbor(data: bytes) -> list[dict]:
    """Decode SenML/CBOR bytes to list of measurement records.

    Restores integer labels to string keys per RFC 8428 Table 4.
    """
    _reverse_labels = {v: k for k, v in _SENML_CBOR_LABELS.items()}
    raw = cbor2.loads(data)
    result = []
    for rec in raw:
        decoded_rec = {}
        for key, value in rec.items():
            str_key = _reverse_labels.get(key, key)
            decoded_rec[str_key] = value
        result.append(decoded_rec)
    return result


def decode_cbor_ld(data: bytes, reg: ContextRegistry) -> dict:
    """Decode our CBOR-LD bytes and decompress keys/values."""
    raw = cbor2.loads(data)
    return reg.decompress(raw)


def decode_cbor_ld_ex(
    data: bytes, reg: ContextRegistry
) -> tuple[dict, Annotation]:
    """Decode CBOR-LD-ex bytes to (doc, annotation).

    Wraps the existing cbor_ld_ex.codec.decode pipeline.
    """
    return _cbor_ld_ex_decode(data, reg)


# =====================================================================
# 9. HELPER: DATASET KEY ENUMERATION
# =====================================================================

def _get_all_keys_for_dataset(dataset: str) -> list[str]:
    """Get all record keys for a dataset (for protobuf/flatbuffers decode).

    These must be the EXACT keys present in the synthetic records,
    matching the encoder's sorted-key field assignment.
    """
    # Import the synthetic records lazily to avoid circular dependency.
    # In production, this would come from the dataset schema.
    _schema_keys = {
        "intel_lab": list(INTEL_LAB_COLUMNS),
        "uci_aq": list(UCI_AQ_COLUMNS),
        "ciciot": list(CICIOT_FEATURE_COLUMNS) + ["Label"],
        "swat_a8": ["Timestamp"] + list(SWAT_A8_SENSOR_COLUMNS),
    }
    return _schema_keys.get(dataset, [])


# =====================================================================
# 10. FRAME-FIT
# =====================================================================

MTU_CONSTANTS = {
    "802.15.4": 127,
    "BLE": 247,
    "LoRaWAN_SF7": 242,
    "LoRaWAN_SF10": 115,
    "LoRaWAN_SF12": 51,
}


def frame_fit(size_bytes: int) -> dict[str, bool]:
    """Classify whether a message fits each constrained-network frame.

    Returns dict mapping MTU name to boolean (fits / doesn't fit).
    A message fits if its size is <= the MTU threshold.
    """
    return {name: size_bytes <= mtu for name, mtu in MTU_CONSTANTS.items()}


# =====================================================================
# 11. ALL-FORMATS RUNNER
# =====================================================================

def encode_record_all_formats(
    record: dict, dataset: str
) -> dict[str, bytes]:
    """Encode one record in all 9 formats.

    Returns dict mapping format name to wire bytes.
    """
    doc, ann, reg = _map_record(record, dataset)

    return {
        "json_ld": encode_as_json_ld(doc, ann),
        "jsonldex_cbor_ld": encode_as_jsonldex_cbor_ld(doc, ann),
        "cbor_ld": encode_as_cbor_ld(doc, ann, reg),
        "cbor_ld_ex": encode_as_cbor_ld_ex(doc, ann, reg),
        "senml_json": encode_as_senml_json(record, dataset),
        "senml_cbor": encode_as_senml_cbor(record, dataset),
        "protobuf": encode_as_protobuf(record, dataset),
        "flatbuffers": encode_as_flatbuffers(record, dataset),
        "msgpack": encode_as_msgpack(record, dataset),
    }


# =====================================================================
# 12. DATASET LOADERS
#
# Parse real dataset CSV files into list[dict] records.
# Each loader handles the specific format quirks of its dataset.
# =====================================================================

# UCI AQ: map original CSV column names to our schema names.
_UCI_AQ_COLUMN_MAP = {
    "Date": "Date",
    "Time": "Time",
    "CO(GT)": "CO_GT",
    "PT08.S1(CO)": "PT08_S1_CO",
    "NMHC(GT)": "NMHC_GT",
    "C6H6(GT)": "C6H6_GT",
    "PT08.S2(NMHC)": "PT08_S2_NMHC",
    "NOx(GT)": "NOx_GT",
    "PT08.S3(NOx)": "PT08_S3_NOx",
    "NO2(GT)": "NO2_GT",
    "PT08.S4(NO2)": "PT08_S4_NO2",
    "PT08.S5(O3)": "PT08_S5_O3",
    "T": "T",
    "RH": "RH",
    "AH": "AH",
}

# CIC-IoT: sentinel for clipping infinity values.
_INF_REPLACEMENT = 1.0e9

# SWaT A8: columns to KEEP (Timestamp + sensors). Everything else dropped.
_SWAT_KEEP_COLUMNS = frozenset({"Timestamp"} | set(SWAT_A8_SENSOR_COLUMNS))


def load_intel_lab(path) -> list[dict]:
    """Load Intel Lab sensor data.

    Format: whitespace-separated, no header row.
    Raw columns: date time epoch moteid temperature humidity light voltage
    Date and time are combined into a single 'timestamp' string.
    Rows with NaN sensor values are dropped.
    """
    path = Path(path)
    records = []
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) < 8:
                continue
            # Columns: date(0) time(1) epoch(2) moteid(3) temp(4) hum(5) light(6) volt(7)
            date_str = parts[0]
            time_str = parts[1]
            # Check for NaN in sensor columns (indices 4-7)
            sensor_strs = parts[4:8]
            if any(s.lower() == "nan" for s in sensor_strs):
                continue
            try:
                record = {
                    "timestamp": f"{date_str} {time_str}",
                    "epoch": float(parts[2]),
                    "moteid": int(parts[3]),
                    "temperature": float(parts[4]),
                    "humidity": float(parts[5]),
                    "light": float(parts[6]),
                    "voltage": float(parts[7]),
                }
                records.append(record)
            except (ValueError, IndexError):
                continue
    return records


# =====================================================================
# 13. EXPERIMENT RUNNER
# =====================================================================

def _statistics(values: list[int]) -> dict:
    """Compute mean, std, min, max, median for a list of ints."""
    n = len(values)
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / n
    std = math.sqrt(variance)
    sorted_vals = sorted(values)
    if n % 2 == 1:
        median = float(sorted_vals[n // 2])
    else:
        median = (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2.0
    return {
        "sizes": values,
        "mean": mean,
        "std": std,
        "min": min(values),
        "max": max(values),
        "median": median,
    }


def run_wire_size_experiment(
    records: list[dict],
    dataset: str,
) -> dict:
    """Run the wire-size experiment on a list of records.

    Encodes each record in all 9 formats, computes per-format
    size statistics, compression ratios vs JSON-LD, and frame-fit
    counts per MTU threshold.

    Returns:
        {
            "dataset": str,
            "n_records": int,
            "per_format": {format_name: {sizes, mean, std, min, max, median}},
            "compression_ratios": {format_name: geometric_mean_ratio_vs_json_ld},
            "frame_fit": {mtu_name: {format_name: count_that_fit}},
        }
    """
    # Collect per-record sizes for each format.
    format_sizes: dict[str, list[int]] = {fmt: [] for fmt in [
        "json_ld", "jsonldex_cbor_ld", "cbor_ld", "cbor_ld_ex",
        "senml_json", "senml_cbor",
        "protobuf", "flatbuffers", "msgpack",
    ]}

    for record in records:
        encoded = encode_record_all_formats(record, dataset)
        for fmt, wire_bytes in encoded.items():
            format_sizes[fmt].append(len(wire_bytes))

    # Per-format statistics.
    per_format = {fmt: _statistics(sizes) for fmt, sizes in format_sizes.items()}

    # Compression ratios vs JSON-LD (per-record ratio, then mean).
    json_ld_sizes = format_sizes["json_ld"]
    compression_ratios = {}
    for fmt, sizes in format_sizes.items():
        if fmt == "json_ld":
            compression_ratios[fmt] = 1.0
        else:
            ratios = [s / j for s, j in zip(sizes, json_ld_sizes) if j > 0]
            if ratios:
                # Geometric mean of ratios.
                log_sum = sum(math.log(r) for r in ratios)
                compression_ratios[fmt] = math.exp(log_sum / len(ratios))
            else:
                compression_ratios[fmt] = 0.0

    # Frame-fit counts per MTU.
    frame_fit_counts: dict[str, dict[str, int]] = {
        mtu_name: {fmt: 0 for fmt in format_sizes}
        for mtu_name in MTU_CONSTANTS
    }
    for fmt, sizes in format_sizes.items():
        for size in sizes:
            fit = frame_fit(size)
            for mtu_name, fits in fit.items():
                if fits:
                    frame_fit_counts[mtu_name][fmt] += 1

    return {
        "dataset": dataset,
        "n_records": len(records),
        "per_format": per_format,
        "compression_ratios": compression_ratios,
        "frame_fit": frame_fit_counts,
    }


def load_uci_air_quality(path) -> list[dict]:
    """Load UCI Air Quality dataset.

    Format: semicolon-separated CSV with header row.
    European decimal format (commas in numbers).
    Missing values encoded as -200 — rows containing -200 are dropped.
    Column names mapped: CO(GT)->CO_GT, PT08.S1(CO)->PT08_S1_CO, etc.
    Trailing empty columns (from trailing semicolons) are ignored.
    """
    path = Path(path)
    records = []
    with open(path, "r") as f:
        # Parse header
        header_line = f.readline().strip().rstrip(";")
        raw_cols = [c.strip() for c in header_line.split(";")]
        # Map to our column names, skip unmapped trailing columns
        mapped_cols = []
        for c in raw_cols:
            if c in _UCI_AQ_COLUMN_MAP:
                mapped_cols.append((c, _UCI_AQ_COLUMN_MAP[c]))

        for line in f:
            line = line.strip()
            if not line:
                continue
            raw_vals = line.split(";")
            # Check for -200 sentinel in numeric columns
            has_missing = False
            record = {}
            for i, (raw_col, mapped_name) in enumerate(mapped_cols):
                if i >= len(raw_vals):
                    break
                val_str = raw_vals[i].strip()
                if mapped_name in ("Date", "Time"):
                    record[mapped_name] = val_str
                else:
                    # European decimal: replace comma with dot
                    val_str = val_str.replace(",", ".")
                    try:
                        val = float(val_str)
                    except ValueError:
                        has_missing = True
                        break
                    if val == -200.0:
                        has_missing = True
                        break
                    record[mapped_name] = val
            if has_missing or len(record) != len(mapped_cols):
                continue
            records.append(record)
    return records


def load_ciciot(path, max_rows: Optional[int] = None) -> list[dict]:
    """Load CIC-IoT-2023 dataset.

    Format: standard CSV with header row.
    Data hygiene (matches aiiot2026 loader):
      - inf values clipped to _INF_REPLACEMENT (1e9)
      - Rows with NaN values dropped
    """
    path = Path(path)
    records = []
    with open(path, "r") as f:
        header_line = f.readline().strip()
        cols = [c.strip() for c in header_line.split(",")]

        for line in f:
            line = line.strip()
            if not line:
                continue
            vals = line.split(",")
            if len(vals) != len(cols):
                continue
            record = {}
            has_nan = False
            for col, val_str in zip(cols, vals):
                val_str = val_str.strip()
                if col == "Label":
                    record[col] = val_str
                else:
                    try:
                        val = float(val_str)
                    except ValueError:
                        has_nan = True
                        break
                    if math.isnan(val):
                        has_nan = True
                        break
                    if math.isinf(val):
                        val = _INF_REPLACEMENT
                    record[col] = val
            if has_nan:
                continue
            records.append(record)
            if max_rows is not None and len(records) >= max_rows:
                break
    return records


def load_swat_a8(
    root, max_rows: Optional[int] = None
) -> list[dict]:
    """Load SWaT A8 dataset from session subdirectories.

    Directory structure:
      root/
        _YYYYMMDD_HHMMSS/
          YYYYMMDD_HHMMSS.csv

    Each CSV has a header row. Only Timestamp + sensor columns
    (SWAT_A8_SENSOR_COLUMNS) are kept; attack metadata is dropped.
    Sessions are loaded in sorted directory-name order.
    """
    root = Path(root)
    records = []

    # Find session directories (start with underscore)
    session_dirs = sorted(
        d for d in root.iterdir()
        if d.is_dir() and d.name.startswith("_")
    )

    for session_dir in session_dirs:
        # Find CSV file in session directory
        csv_files = list(session_dir.glob("*.csv"))
        if not csv_files:
            continue
        csv_path = csv_files[0]

        with open(csv_path, "r") as f:
            header_line = f.readline().strip()
            cols = [c.strip() for c in header_line.split(",")]
            # Determine which column indices to keep
            keep_indices = []
            keep_names = []
            for i, col in enumerate(cols):
                if col in _SWAT_KEEP_COLUMNS:
                    keep_indices.append(i)
                    keep_names.append(col)

            for line in f:
                line = line.strip()
                if not line:
                    continue
                vals = line.split(",")
                record = {}
                for idx, name in zip(keep_indices, keep_names):
                    if idx >= len(vals):
                        continue
                    val_str = vals[idx].strip()
                    if name == "Timestamp":
                        record[name] = val_str
                    else:
                        try:
                            record[name] = float(val_str)
                        except ValueError:
                            record[name] = 0.0  # empty/missing sensor
                records.append(record)
                if max_rows is not None and len(records) >= max_rows:
                    break
        if max_rows is not None and len(records) >= max_rows:
            break
    return records
