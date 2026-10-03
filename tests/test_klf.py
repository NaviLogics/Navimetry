"""Reading the KoggerApp KLF log: MAVLink frames with their CRC checked, SYSTEM_TIME for UTC."""
from __future__ import annotations

import struct
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from bathymetry.klf import (
    MSG_GPS_RAW_INT,
    MSG_SYSTEM_TIME,
    parse_klf_bytes,
    read_klf,
    x25_crc,
)

PILOT_KLF = Path("sample_data") / "pilot_2026-10-01" / "00038_2026.10.01_12.12.18.klf"

CRC_EXTRA = {MSG_SYSTEM_TIME: 137, MSG_GPS_RAW_INT: 24}


def frame_v1(msgid: int, payload: bytes, crc_extra: int | None = None) -> bytes:
    body = bytes([len(payload), 0, 1, 1, msgid]) + payload
    crc = x25_crc(bytes([CRC_EXTRA[msgid] if crc_extra is None else crc_extra]), x25_crc(body))
    return b"\xfe" + body + struct.pack("<H", crc)


def frame_v2(msgid: int, payload: bytes, crc_extra: int) -> bytes:
    body = bytes([len(payload), 0, 0, 0, 1, 1]) + msgid.to_bytes(3, "little") + payload
    crc = x25_crc(bytes([crc_extra]), x25_crc(body))
    return b"\xfd" + body + struct.pack("<H", crc)


def gps_raw_payload(time_usec: int, alt_mm: int, fix_type: int) -> bytes:
    return struct.pack("<QiiiHHHHBB", time_usec, 559345784, 373812133, alt_mm, 100, 100, 0, 0, fix_type, 30)


@pytest.fixture(scope="module")
def pilot():
    return read_klf(PILOT_KLF)


def test_x25_crc_check_value() -> None:
    # CRC-16/MCRF4XX of "123456789"
    assert x25_crc(b"123456789") == 0x6F91


def test_a_frame_is_read_only_with_its_crc_right() -> None:
    payload = struct.pack("<QI", 1790845947987000, 310767)
    good = frame_v1(MSG_SYSTEM_TIME, payload)
    bad = good[:-1] + bytes([good[-1] ^ 0xFF])

    log = parse_klf_bytes(b"\xbb\x55\x00\x01" + bad + b"\x00" + good + b"\xfe\x00")

    assert len(log.system_times) == 1
    assert log.system_times[0].time_boot_ms == 310767
    assert log.frames_bad_crc == 1


def test_mavlink_v2_truncated_payload_is_padded_and_msgids_over_255_are_skipped() -> None:
    # v2 drops the trailing zero bytes: fix_type 6 and satellites 30 are kept, the extensions are zeros
    payload = gps_raw_payload(5_000_000, 183_645, 6)
    v2 = frame_v2(MSG_GPS_RAW_INT, payload.rstrip(b"\0"), 24)
    unknown = frame_v2(300, b"\x01\x02\x03", 0)

    log = parse_klf_bytes(unknown + v2)

    assert len(log.gps_raw) == 1
    message = log.gps_raw[0]
    assert message.alt_m == pytest.approx(183.645)
    assert message.fix_type == 6
    assert message.time_usec == 5_000_000


def test_utc_of_a_record_is_the_boot_epoch_plus_its_time_since_boot() -> None:
    # PX4 GPS_RAW_INT.time_usec counts from boot; SYSTEM_TIME gives the boot epoch
    system_time = frame_v1(MSG_SYSTEM_TIME, struct.pack("<QI", 1790845947987000, 310767))
    gps = frame_v1(MSG_GPS_RAW_INT, gps_raw_payload(320_767_000, 183_645, 6))

    log = parse_klf_bytes(system_time + gps)

    assert log.boot_epoch_s == pytest.approx(1790845637.220)
    assert log.gps_utc_s(log.gps_raw[0]) == pytest.approx(1790845957.987)


def test_pilot_first_valid_system_time(pilot) -> None:
    first = next(message for message in pilot.system_times if message.is_valid)
    expected = datetime(2026, 10, 1, 9, 12, 27, 987000, tzinfo=timezone.utc)
    assert abs(first.utc.timestamp() - expected.timestamp()) < 0.001
    assert first.time_boot_ms == 310767
    assert pilot.boot_epoch_s == pytest.approx(1790845637.22, abs=0.01)
    assert datetime.fromtimestamp(pilot.boot_epoch_s, tz=timezone.utc).strftime("%H:%M:%S") == "09:07:17"


def test_pilot_gps_raw_int(pilot) -> None:
    altitudes = np.array([message.alt_m for message in pilot.gps_raw])
    # The task expects about 2296 (counted without CRC by another script); the log holds 2362 frames with a
    # valid CRC at 5 Hz without gaps, plus one with a bad CRC and an altitude of 910 316 m
    assert len(pilot.gps_raw) == 2362
    assert float(np.median(altitudes)) == pytest.approx(183.645, abs=0.0005)
    assert altitudes.min() >= 100 and altitudes.max() <= 300
    assert Counter(message.fix_type for message in pilot.gps_raw) == {6: 2357, 5: 5}
    gaps = np.diff([message.time_usec for message in pilot.gps_raw]) / 1e6
    assert gaps.min() > 0 and gaps.max() < 0.25


def test_pilot_global_position_int_is_read_but_is_not_the_antenna(pilot) -> None:
    altitudes = [message.alt_m for message in pilot.global_positions]
    assert float(np.median(altitudes)) == pytest.approx(183.453, abs=0.0005)
