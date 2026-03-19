# kv4p-ht-python

Python driver for headless control of the [KV4P HT](https://kv4p.com) ham radio over USB serial.

Lets you control the KV4P HT from a Linux box (Raspberry Pi, etc.) without the Android app.

## Install

```bash
pip install -e .

# With Opus audio support:
pip install -e ".[audio]"
```

## Quick start

```python
from kv4p import KV4PRadio, GroupConfig

radio = KV4PRadio("/dev/ttyUSB0")
radio.on_rx_audio = lambda opus: handle_audio(opus)
radio.on_smeter = lambda rssi: print(f"S-meter: {rssi}")

radio.open()
radio.tune(GroupConfig(tx_freq=146.520, rx_freq=146.520, squelch=4))
radio.enable_smeter(True)

# TX
radio.ptt_on()
radio.send_audio(opus_encoded_frame)
radio.ptt_off()

radio.close()
```

## CLI

```bash
kv4p /dev/ttyUSB0 -f 146.520 --smeter -v
```

## Protocol

Based on the [KV4P HT firmware](https://github.com/VanceVagell/kv4p-ht) serial protocol:
- 115200 baud, 8N1
- 8-byte delimiter framing (`0xDEADBEEF` x2)
- Opus narrowband audio at 48kHz mono, 40ms frames
- SA818/DRA818 radio module controlled via ESP32

## License

GPL-3.0 — same as the KV4P HT project.
