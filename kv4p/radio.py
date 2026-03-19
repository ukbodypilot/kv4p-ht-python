"""High-level KV4P HT radio interface."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

import serial

from .protocol import (
    DELIMITER,
    DeviceCommand,
    HostCommand,
    GroupConfig,
    FiltersConfig,
    Packet,
    PacketParser,
    VersionInfo,
    build_packet,
)

log = logging.getLogger(__name__)

# Callbacks
AudioCallback = Callable[[bytes], None]       # Opus-encoded RX audio
SmeterCallback = Callable[[int], None]         # RSSI 0-255
PttCallback = Callable[[bool], None]           # Physical PTT state


class KV4PRadio:
    """
    Headless driver for the KV4P HT radio over USB serial.

    Usage::

        radio = KV4PRadio("/dev/ttyUSB0")
        radio.open()
        radio.tune(GroupConfig(tx_freq=146.520, rx_freq=146.520))
        radio.start_rx()
        # ... audio arrives via on_rx_audio callback ...
        radio.close()
    """

    def __init__(self, port: str, baudrate: int = 115200, timeout: float = 0.05):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout

        self._ser: Optional[serial.Serial] = None
        self._parser = PacketParser()
        self._reader_thread: Optional[threading.Thread] = None
        self._running = False

        # State
        self.version: Optional[VersionInfo] = None
        self._handshake_event = threading.Event()
        self._version_event = threading.Event()
        self._tx_window = 0
        self._tx_window_lock = threading.Condition()

        # User callbacks
        self.on_rx_audio: Optional[AudioCallback] = None
        self.on_smeter: Optional[SmeterCallback] = None
        self.on_phys_ptt: Optional[PttCallback] = None

    # ── Connection lifecycle ────────────────────────────────────────

    def open(self, handshake_timeout: float = 5.0) -> VersionInfo:
        """Open serial port, perform handshake, return version info."""
        self._ser = serial.Serial(
            port=self.port,
            baudrate=self.baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=self.timeout,
        )

        # Drain stale data from any prior session
        self._ser.reset_input_buffer()
        self._ser.reset_output_buffer()
        self._parser = PacketParser()

        self._running = True
        self._reader_thread = threading.Thread(
            target=self._reader_loop, daemon=True, name="kv4p-reader"
        )
        self._reader_thread.start()

        # Try to catch HELLO (only sent on fresh boot).
        # If device is already running from a prior session, HELLO won't come.
        log.info("Waiting for device HELLO...")
        got_hello = self._handshake_event.wait(timeout=handshake_timeout)

        if not got_hello:
            log.info("No HELLO — device may already be running, proceeding.")

        # Send STOP to ensure clean state, then CONFIG to get VERSION
        self._send(HostCommand.STOP)
        time.sleep(0.2)
        # Drain any leftover audio that was in flight
        self._ser.reset_input_buffer()
        self._parser = PacketParser()

        self._version_event.clear()
        self._send(HostCommand.CONFIG, bytes([1]))

        if not self._version_event.wait(timeout=5.0):
            log.warning("No VERSION response — device may be running older firmware")
        else:
            log.info("Connected: fw=%d, rf=%s, caps=%s",
                      self.version.firmware_version,
                      self.version.rf_module_type.name,
                      self.version.capability_flags)

        return self.version

    def close(self) -> None:
        """Stop radio and close serial port."""
        self._running = False
        if self._ser and self._ser.is_open:
            try:
                self._send(HostCommand.STOP)
            except Exception:
                pass
            self._ser.close()
        if self._reader_thread:
            self._reader_thread.join(timeout=2.0)
        log.info("Connection closed.")

    # ── Radio control ───────────────────────────────────────────────

    def tune(self, group: GroupConfig) -> None:
        """Set TX/RX frequency, CTCSS, squelch, and bandwidth."""
        self._send(HostCommand.GROUP, group.pack())
        log.info("Tuned to TX=%.4f RX=%.4f MHz", group.tx_freq, group.rx_freq)

    def set_filters(self, filters: FiltersConfig) -> None:
        """Set audio filters (pre-emphasis, highpass, lowpass)."""
        self._send(HostCommand.FILTERS, filters.pack())

    def set_power(self, high: bool) -> None:
        """Set transmit power level (high or low)."""
        self._send(HostCommand.HL_POWER, bytes([int(high)]))

    def enable_smeter(self, enabled: bool = True) -> None:
        """Enable or disable periodic RSSI/S-meter reports."""
        self._send(HostCommand.RSSI_ENABLE, bytes([int(enabled)]))

    def start_rx(self) -> None:
        """Enter receive mode. RX audio delivered via on_rx_audio callback."""
        # Sending GROUP again implicitly starts RX on the device
        log.info("Entering RX mode.")

    def stop(self) -> None:
        """Return radio to idle/stopped mode."""
        self._send(HostCommand.STOP)
        log.info("Radio stopped.")

    # ── Transmit ────────────────────────────────────────────────────

    def ptt_on(self) -> None:
        """Key the transmitter (PTT down)."""
        self._send(HostCommand.PTT_DOWN)
        log.info("PTT ON")

    def ptt_off(self) -> None:
        """Unkey the transmitter (PTT up)."""
        self._send(HostCommand.PTT_UP)
        log.info("PTT OFF")

    def send_audio(self, opus_data: bytes) -> None:
        """Send a single Opus-encoded TX audio frame."""
        self._send(HostCommand.TX_AUDIO, opus_data)

    def transmit_frames(self, frames: list[bytes], frame_ms: float = 40.0) -> int:
        """Send a sequence of Opus frames with correct pacing.

        Uses wall-clock alignment so encode/send overhead doesn't
        accumulate as drift. Returns the number of frames sent.
        """
        sent = 0
        t0 = time.monotonic()
        for i, frame in enumerate(frames):
            self._send(HostCommand.TX_AUDIO, frame)
            sent += 1
            # Sleep until the next frame boundary
            target = t0 + (i + 1) * (frame_ms / 1000.0)
            now = time.monotonic()
            if target > now:
                time.sleep(target - now)
        return sent

    # ── Internal: serial I/O ────────────────────────────────────────

    def _send(self, cmd: HostCommand, payload: bytes = b"") -> None:
        """Build and transmit a framed packet."""
        if not self._ser or not self._ser.is_open:
            raise ConnectionError("Serial port not open")
        pkt = build_packet(cmd, payload)
        self._ser.write(pkt)
        self._ser.flush()

    def _reader_loop(self) -> None:
        """Background thread: read serial data and dispatch packets."""
        while self._running:
            try:
                if not self._ser or not self._ser.is_open:
                    break
                data = self._ser.read(4096)
                if not data:
                    continue
                for pkt in self._parser.feed(data):
                    self._dispatch(pkt)
            except (serial.SerialException, TypeError, OSError) as e:
                if self._running:
                    log.error("Serial error: %s", e)
                break
            except Exception:
                log.exception("Reader thread error")

    def _dispatch(self, pkt: Packet) -> None:
        """Route an incoming packet to the appropriate handler."""
        try:
            cmd = DeviceCommand(pkt.command)
        except ValueError:
            log.debug("Unknown device command 0x%02x (%dB)",
                      pkt.command, len(pkt.payload))
            return

        if cmd == DeviceCommand.HELLO:
            log.debug("Received HELLO")
            self._handshake_event.set()

        elif cmd == DeviceCommand.VERSION:
            try:
                self.version = VersionInfo.unpack(pkt.payload)
                self._version_event.set()
            except Exception:
                log.exception("Failed to parse VERSION")

        elif cmd == DeviceCommand.RX_AUDIO:
            if self.on_rx_audio:
                self.on_rx_audio(pkt.payload)

        elif cmd == DeviceCommand.SMETER_REPORT:
            if pkt.payload and self.on_smeter:
                self.on_smeter(pkt.payload[0])

        elif cmd == DeviceCommand.WINDOW_UPDATE:
            if len(pkt.payload) >= 2:
                import struct
                with self._tx_window_lock:
                    self._tx_window = struct.unpack("<H", pkt.payload[:2])[0]
                    self._tx_window_lock.notify_all()

        elif cmd == DeviceCommand.PHYS_PTT_DOWN:
            if self.on_phys_ptt:
                self.on_phys_ptt(True)

        elif cmd == DeviceCommand.PHYS_PTT_UP:
            if self.on_phys_ptt:
                self.on_phys_ptt(False)

        else:
            log.debug("Unhandled: %s", pkt)

    # ── Context manager ─────────────────────────────────────────────

    def __enter__(self) -> KV4PRadio:
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()
