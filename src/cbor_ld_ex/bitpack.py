"""
Bit-level packing utilities for CBOR-LD-ex.

Provides BitWriter and BitReader for MSB-first bit-packed encoding,
used by temporal extensions (§7.4) and multinomial wire format (§4.4).

MSB-first within each byte, matching network byte order.
Final byte padded with zero bits.
"""


class BitWriter:
    """Accumulates bits MSB-first, pads final byte with zeros."""

    def __init__(self) -> None:
        self._bits: list[int] = []

    def write(self, value: int, width: int) -> None:
        """Write `width` bits from `value` (MSB first)."""
        for i in range(width - 1, -1, -1):
            self._bits.append((value >> i) & 1)

    def to_bytes(self) -> bytes:
        """Pad to byte boundary and return bytes."""
        if not self._bits:
            return b""
        # Pad to multiple of 8
        while len(self._bits) % 8 != 0:
            self._bits.append(0)
        result = bytearray()
        for i in range(0, len(self._bits), 8):
            byte = 0
            for j in range(8):
                byte = (byte << 1) | self._bits[i + j]
            result.append(byte)
        return bytes(result)


class BitReader:
    """Reads individual bits from a byte array, MSB-first."""

    def __init__(self, data: bytes) -> None:
        self._bits: list[int] = []
        for byte in data:
            for i in range(7, -1, -1):
                self._bits.append((byte >> i) & 1)
        self._pos = 0

    def read(self, width: int) -> int:
        """Read `width` bits and return as integer."""
        value = 0
        for _ in range(width):
            value = (value << 1) | self._bits[self._pos]
            self._pos += 1
        return value

    @property
    def remaining(self) -> int:
        """Number of unread bits."""
        return len(self._bits) - self._pos
