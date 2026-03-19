"""KV4P HT serial protocol definitions and packet framing."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum, IntFlag
from typing import Optional


# 4-byte delimiter that precedes every packet
DELIMITER = bytes([0xDE, 0xAD, 0xBE, 0xEF])

# Maximum payload size per packet
PROTO_MTU = 2048


# ── Host → Device commands ──────────────────────────────────────────

class HostCommand(IntEnum):
    PTT_DOWN = 0x01
    PTT_UP = 0x02
    GROUP = 0x03
    FILTERS = 0x04
    STOP = 0x05
    CONFIG = 0x06
    TX_AUDIO = 0x07
    HL_POWER = 0x08
    RSSI_ENABLE = 0x09


# ── Device → Host commands ──────────────────────────────────────────

class DeviceCommand(IntEnum):
    HELLO = 0x06
    RX_AUDIO = 0x07
    VERSION = 0x08
    WINDOW_UPDATE = 0x09
    PHYS_PTT_DOWN = 0x44
    PHYS_PTT_UP = 0x55
    SMETER_REPORT = 0x53


# ── Enums / flags ───────────────────────────────────────────────────

class RfModuleType(IntEnum):
    SA818_VHF = 0
    SA818_UHF = 1


class CapabilityFlags(IntFlag):
    HAS_PHYS_PTT = 1 << 0
    HAS_HL_POWER = 1 << 1
    RADIO_DETECTED = 1 << 2


# ── Data structures ─────────────────────────────────────────────────

@dataclass
class GroupConfig:
    """Radio group/channel configuration.

    Wire format (packed, 12 bytes):
        uint8_t bw        — 0 = narrow (12.5 kHz), 1 = wide (25 kHz)
        float   freq_tx   — TX frequency in MHz
        float   freq_rx   — RX frequency in MHz
        uint8_t ctcss_tx  — TX CTCSS tone code (0 = none)
        uint8_t squelch   — squelch level 0-8
        uint8_t ctcss_rx  — RX CTCSS tone code (0 = none)
    """
    tx_freq: float          # MHz, e.g. 146.520
    rx_freq: float          # MHz
    bandwidth: int = 1      # 0 = narrow (12.5 kHz), 1 = wide (25 kHz)
    ctcss_tx: int = 0       # TX CTCSS tone code (0 = none)
    squelch: int = 4        # Squelch level 0-8
    ctcss_rx: int = 0       # RX CTCSS tone code (0 = none)

    def pack(self) -> bytes:
        """Pack into 12-byte wire format matching firmware Group struct."""
        return struct.pack("<BffBBB",
                           self.bandwidth, self.tx_freq, self.rx_freq,
                           self.ctcss_tx, self.squelch, self.ctcss_rx)

    @classmethod
    def unpack(cls, data: bytes) -> GroupConfig:
        bw, tx, rx, ct_tx, sq, ct_rx = struct.unpack("<BffBBB", data[:12])
        return cls(tx_freq=tx, rx_freq=rx, bandwidth=bw,
                   ctcss_tx=ct_tx, squelch=sq, ctcss_rx=ct_rx)


@dataclass
class FiltersConfig:
    """DRA818 radio module filter settings.

    Wire format: single uint8_t bitmask.
        bit 0: pre/de-emphasis (50µs FM standard)
        bit 1: highpass (~300Hz)
        bit 2: lowpass (audio band)
    """
    pre_emphasis: bool = True
    highpass: bool = True
    lowpass: bool = True

    def pack(self) -> bytes:
        flags = 0
        if self.pre_emphasis:
            flags |= (1 << 0)
        if self.highpass:
            flags |= (1 << 1)
        if self.lowpass:
            flags |= (1 << 2)
        return bytes([flags])


@dataclass
class VersionInfo:
    """Firmware version response.

    Wire format (packed, little-endian):
        uint16_t ver              — firmware version
        char     radioModuleStatus — 'f' = found, other = not found
        uint32_t windowSize       — USB buffer / flow-control window
        uint8_t  rfModuleType     — 0 = VHF, 1 = UHF
        uint8_t  features         — capability bitmask
    Total: 9 bytes minimum (firmware may send up to 12).
    """
    firmware_version: int = 0
    radio_module_present: bool = False
    window_size: int = 0
    rf_module_type: RfModuleType = RfModuleType.SA818_VHF
    capability_flags: CapabilityFlags = CapabilityFlags(0)

    @classmethod
    def unpack(cls, data: bytes) -> VersionInfo:
        if len(data) < 9:
            raise ValueError(f"VERSION payload too short: {len(data)} bytes")
        fw, radio_char, win, rf, caps = struct.unpack_from("<HcIBB", data)
        try:
            rf_type = RfModuleType(rf)
        except ValueError:
            rf_type = RfModuleType.SA818_VHF
        return cls(
            firmware_version=fw,
            radio_module_present=(radio_char == b'f'),
            window_size=win,
            rf_module_type=rf_type,
            capability_flags=CapabilityFlags(caps),
        )


# ── Packet building / parsing ───────────────────────────────────────

def build_packet(cmd: int, payload: bytes = b"") -> bytes:
    """Build a framed serial packet: delimiter + cmd + length + payload."""
    if len(payload) > PROTO_MTU:
        raise ValueError(f"Payload {len(payload)} exceeds MTU {PROTO_MTU}")
    return DELIMITER + struct.pack("<BH", cmd, len(payload)) + payload


@dataclass
class Packet:
    """A parsed protocol packet."""
    command: int
    payload: bytes

    def __repr__(self) -> str:
        try:
            name = DeviceCommand(self.command).name
        except ValueError:
            name = f"0x{self.command:02x}"
        return f"Packet({name}, {len(self.payload)}B)"


class PacketParser:
    """Incremental parser that extracts packets from a serial byte stream."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[Packet]:
        """Feed raw bytes, return any complete packets found."""
        self._buf.extend(data)
        packets: list[Packet] = []

        while True:
            # Find delimiter
            idx = self._buf.find(DELIMITER)
            if idx == -1:
                # Keep tail bytes that could be start of delimiter
                if len(self._buf) > len(DELIMITER):
                    self._buf = self._buf[-(len(DELIMITER) - 1):]
                break

            # Discard bytes before delimiter
            if idx > 0:
                self._buf = self._buf[idx:]

            # Need delimiter(4) + cmd(1) + length(2) = 7 bytes minimum
            header_size = len(DELIMITER) + 3
            if len(self._buf) < header_size:
                break

            cmd = self._buf[len(DELIMITER)]
            param_len = struct.unpack_from("<H", self._buf, len(DELIMITER) + 1)[0]

            total = header_size + param_len
            if len(self._buf) < total:
                break

            payload = bytes(self._buf[header_size:total])
            packets.append(Packet(command=cmd, payload=payload))
            self._buf = self._buf[total:]

        return packets
