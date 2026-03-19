"""Simple CLI for testing KV4P HT radio control."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time

from .protocol import GroupConfig, FiltersConfig
from .radio import KV4PRadio


def main() -> None:
    parser = argparse.ArgumentParser(description="KV4P HT radio control")
    parser.add_argument("port", help="Serial port (e.g. /dev/ttyUSB0)")
    parser.add_argument("-f", "--freq", type=float, default=146.520,
                        help="Frequency in MHz (default: 146.520)")
    parser.add_argument("--tx-freq", type=float, default=None,
                        help="TX frequency if different from RX")
    parser.add_argument("-s", "--squelch", type=int, default=4,
                        help="Squelch level 0-8 (default: 4)")
    parser.add_argument("-c", "--ctcss", type=int, default=0,
                        help="CTCSS tone code (default: 0 = none)")
    parser.add_argument("-w", "--wide", action="store_true",
                        help="Wide bandwidth (25 kHz), default is wide")
    parser.add_argument("--narrow", action="store_true",
                        help="Narrow bandwidth (12.5 kHz)")
    parser.add_argument("--high-power", action="store_true", default=True,
                        help="High power TX (default)")
    parser.add_argument("--low-power", action="store_true",
                        help="Low power TX")
    parser.add_argument("--smeter", action="store_true",
                        help="Enable S-meter reporting")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    tx_freq = args.tx_freq if args.tx_freq else args.freq
    bandwidth = 0 if args.narrow else 1
    power_high = not args.low_power

    group = GroupConfig(
        tx_freq=tx_freq,
        rx_freq=args.freq,
        bandwidth=bandwidth,
        ctcss_tx=args.ctcss,
        squelch=args.squelch,
        ctcss_rx=args.ctcss,
    )

    radio = KV4PRadio(args.port)

    def on_smeter(rssi: int) -> None:
        bars = rssi * 9 // 255
        print(f"\rS{bars} (raw={rssi})  ", end="", flush=True)

    def on_rx_audio(opus_data: bytes) -> None:
        # In a real app, decode and play or forward to gateway
        pass

    radio.on_smeter = on_smeter
    radio.on_rx_audio = on_rx_audio

    def shutdown(sig, frame):
        print("\nShutting down...")
        radio.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        version = radio.open()
        if version:
            print(f"Firmware: v{version.firmware_version}")
            print(f"RF module: {version.rf_module_type.name}")
            print(f"Capabilities: {version.capability_flags}")

        radio.set_power(power_high)
        radio.tune(group)
        radio.set_filters(FiltersConfig())

        if args.smeter:
            radio.enable_smeter(True)

        print(f"Listening on {args.freq:.4f} MHz (squelch={args.squelch})")
        print("Press Ctrl+C to exit.")

        while True:
            time.sleep(1)

    except TimeoutError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        radio.close()


if __name__ == "__main__":
    main()
