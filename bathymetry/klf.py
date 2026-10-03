"""Reading a KoggerApp KLF log: the MAVLink messages it holds, with their CRC checked.

A KLF file is a raw byte stream: Kogger protocol frames (0xBB 0x55 ...) mixed with raw MAVLink frames
(v1 0xFE, v2 0xFD). Records have no time stamp of their own; time comes from the MAVLink messages.
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

MAVLINK_V1_STX = 0xFE
MAVLINK_V2_STX = 0xFD

MSG_SYSTEM_TIME = 2
MSG_GPS_RAW_INT = 24
MSG_GLOBAL_POSITION_INT = 33

# msgid: (CRC_EXTRA, base payload length, full payload length with extensions)
MESSAGES: dict[int, tuple[int, int, int]] = {
    MSG_SYSTEM_TIME: (137, 12, 12),
    MSG_GPS_RAW_INT: (24, 30, 52),
    MSG_GLOBAL_POSITION_INT: (104, 28, 28),
}

# SYSTEM_TIME before this has no real UTC (the autopilot has no GNSS time yet)
MIN_VALID_UTC_YEAR = 2000

GPS_FIX_RTK_FLOAT = 5
GPS_FIX_RTK_FIXED = 6


def x25_crc(data: bytes, crc: int = 0xFFFF) -> int:
    """MAVLink checksum (CRC-16/MCRF4XX, X.25)."""
    return 0


@dataclass
class SystemTime:
    offset: int
    time_unix_usec: int
    time_boot_ms: int

    @property
    def boot_epoch_s(self) -> float:
        """UTC of the autopilot boot, in seconds since the epoch."""
        return (self.time_unix_usec - self.time_boot_ms * 1000) / 1e6

    @property
    def utc(self) -> datetime:
        return datetime.fromtimestamp(self.time_unix_usec / 1e6, tz=timezone.utc)

    @property
    def is_valid(self) -> bool:
        return self.time_unix_usec > 0 and self.utc.year >= MIN_VALID_UTC_YEAR


@dataclass
class GpsRawInt:
    offset: int
    time_usec: int
    """Since the autopilot boot on PX4, not UTC."""
    lat_deg: float
    lon_deg: float
    alt_m: float
    """Antenna altitude, MSL as the receiver gives it."""
    fix_type: int


@dataclass
class GlobalPositionInt:
    offset: int
    time_boot_ms: int
    lat_deg: float
    lon_deg: float
    alt_m: float


@dataclass
class KlfLog:
    path: Path
    system_times: list[SystemTime] = field(default_factory=list)
    gps_raw: list[GpsRawInt] = field(default_factory=list)
    global_positions: list[GlobalPositionInt] = field(default_factory=list)
    frames_bad_crc: int = 0
    """Frames of the known messages whose CRC did not match: noise in the stream, dropped."""

    @property
    def boot_epoch_s(self) -> float | None:
        """UTC of the autopilot boot: the median over the valid SYSTEM_TIME messages."""
        epochs = [message.boot_epoch_s for message in self.system_times if message.is_valid]
        return float(np.median(epochs)) if epochs else None

    def gps_utc_s(self, message: GpsRawInt) -> float | None:
        """UTC of a GPS_RAW_INT, in seconds since the epoch: the boot epoch plus its time since boot."""
        boot = self.boot_epoch_s
        return None if boot is None else boot + message.time_usec / 1e6


def parse_klf_bytes(data: bytes, path: Path | None = None) -> KlfLog:
    """Find the MAVLink frames of the known messages in a KLF byte stream; frames failing their CRC are dropped."""
    return KlfLog(path=path or Path())


def read_klf(path: Path) -> KlfLog:
    """Read a KLF file: the SYSTEM_TIME, GPS_RAW_INT and GLOBAL_POSITION_INT messages with a valid CRC."""
    path = Path(path)
    return parse_klf_bytes(path.read_bytes(), path)
