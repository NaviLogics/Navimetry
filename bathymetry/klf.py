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
    for byte in data:
        tmp = byte ^ (crc & 0xFF)
        tmp = (tmp ^ (tmp << 4)) & 0xFF
        crc = ((crc >> 8) ^ (tmp << 8) ^ (tmp << 3) ^ (tmp >> 4)) & 0xFFFF
    return crc


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


def _decode(msgid: int, payload: bytes, offset: int) -> SystemTime | GpsRawInt | GlobalPositionInt:
    if msgid == MSG_SYSTEM_TIME:
        time_unix_usec, time_boot_ms = struct.unpack_from("<QI", payload)
        return SystemTime(offset, time_unix_usec, time_boot_ms)
    if msgid == MSG_GPS_RAW_INT:
        time_usec, lat, lon, alt = struct.unpack_from("<Qiii", payload)
        fix_type = payload[28]
        return GpsRawInt(offset, time_usec, lat / 1e7, lon / 1e7, alt / 1000, fix_type)
    time_boot_ms, lat, lon, alt = struct.unpack_from("<Iiii", payload)
    return GlobalPositionInt(offset, time_boot_ms, lat / 1e7, lon / 1e7, alt / 1000)


def parse_klf_bytes(data: bytes, path: Path | None = None) -> KlfLog:
    """Find the MAVLink frames of the known messages in a KLF byte stream; frames failing their CRC are dropped."""
    log = KlfLog(path=path or Path())
    position = 0
    length = len(data)
    start = re.compile(b"[\xfe\xfd]")
    while True:
        match = start.search(data, position)
        if match is None:
            break
        index = match.start()
        frame = _read_frame(data, index, length)
        if frame is None:
            position = index + 1
            continue
        kind, msgid, payload, frame_end = frame
        if kind == "bad_crc":
            log.frames_bad_crc += 1
            position = index + 1
            continue
        message = _decode(msgid, payload, index)
        if isinstance(message, SystemTime):
            log.system_times.append(message)
        elif isinstance(message, GpsRawInt):
            log.gps_raw.append(message)
        else:
            log.global_positions.append(message)
        position = frame_end
    return log


def _read_frame(data: bytes, index: int, length: int):
    stx = data[index]
    if stx == MAVLINK_V1_STX:
        header = 6
        if index + header > length:
            return None
        payload_len = data[index + 1]
        msgid = data[index + 5]
        signature = 0
    else:
        header = 10
        if index + header > length:
            return None
        payload_len = data[index + 1]
        incompat = data[index + 2]
        msgid = data[index + 7] | (data[index + 8] << 8) | (data[index + 9] << 16)
        signature = 13 if incompat & 0x01 else 0
        # Messages over 255 are not ones read here
        if msgid > 255:
            return None
    known = MESSAGES.get(msgid)
    if known is None:
        return None
    crc_extra, base_len, full_len = known
    # v1 frames carry the base payload, maybe with extensions; v2 frames may truncate trailing zeros
    if stx == MAVLINK_V1_STX and not base_len <= payload_len <= full_len:
        return None
    if stx == MAVLINK_V2_STX and not 1 <= payload_len <= full_len:
        return None
    frame_end = index + header + payload_len + 2 + signature
    if frame_end > length:
        return None
    body = data[index + 1 : index + header + payload_len]
    expected = x25_crc(bytes([crc_extra]), x25_crc(body))
    received = data[index + header + payload_len] | (data[index + header + payload_len + 1] << 8)
    if expected != received:
        return ("bad_crc", msgid, b"", index + 1)
    payload = data[index + header : index + header + payload_len].ljust(full_len, b"\0")
    return ("ok", msgid, payload, frame_end)


def read_klf(path: Path) -> KlfLog:
    """Read a KLF file: the SYSTEM_TIME, GPS_RAW_INT and GLOBAL_POSITION_INT messages with a valid CRC."""
    path = Path(path)
    return parse_klf_bytes(path.read_bytes(), path)
