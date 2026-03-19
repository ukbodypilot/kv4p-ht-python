"""KV4P HT Python driver — headless control over USB serial."""

from .radio import KV4PRadio
from .protocol import (
    DELIMITER,
    HostCommand,
    DeviceCommand,
    RfModuleType,
    CapabilityFlags,
    GroupConfig,
    FiltersConfig,
    VersionInfo,
)
from .audio import (
    DCOffsetRemover,
    VolumeRamp,
    ClickRemover,
    OpusEncoder,
    OpusDecoder,
)

__version__ = "0.1.0"
__all__ = [
    "KV4PRadio",
    "DELIMITER",
    "HostCommand",
    "DeviceCommand",
    "RfModuleType",
    "CapabilityFlags",
    "GroupConfig",
    "FiltersConfig",
    "VersionInfo",
    "DCOffsetRemover",
    "VolumeRamp",
    "OpusEncoder",
    "OpusDecoder",
]
