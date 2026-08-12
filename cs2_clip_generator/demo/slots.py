"""Recover CS2 spectator slots straight from the demo container.

Why this module exists
---------------------
To put the camera on a player, CS2 needs ``spec_player <slot>``. CS:GO also
accepted ``spec_player_by_accountid <accountid>``, but that command is gone in
Source 2, so the *slot* is unavoidable — and no Python demo parser exposes it.

The slot is, however, sitting right there in the demo: the ``userinfo`` string
table holds one ``CMsgPlayerInfo`` per connected player, and

    slot = (CMsgPlayerInfo.userid & 0xff) + 1

(The raw ``userid`` is ``0xFF00 | slot0`` in practice; masking with ``0xff`` is
exactly what demoinfocs-golang and demoparser2 do internally.)

So this module reads the demo container itself. It only needs three things:
varint decoding, Snappy decompression, and enough protobuf awareness to walk
two nested messages. No generated protobuf code, no schema, no extra service.

Container layout (Source 2 / ``PBDEMS2``)::

    "PBDEMS2\\0" | int32 fileinfo_offset | int32 spawngroups_offset
    repeat: varint kind | varint tick | varint size | payload[size]

``kind`` has ``0x40`` set when the payload is Snappy-compressed. We only care
about ``DEM_StringTables`` (6) and ``DEM_FullPacket`` (13), which carry string
table snapshots. Everything else is skipped without being decoded, so a 2 GB
demo costs a few hundred milliseconds and no meaningful memory.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass

from ..core.logger import get_logger

log = get_logger("parser")

DEM_IS_COMPRESSED = 0x40
DEM_STRING_TABLES = 6
DEM_FULL_PACKET = 13
DEM_STOP = 0

CS2_MAGIC = b"PBDEMS2\x00"

#: Protobuf field numbers we rely on (stable Valve message definitions).
_FIELD_TABLES = 1  # CDemoStringTables.tables
_FIELD_TABLE_NAME = 1  # table_t.table_name
_FIELD_TABLE_ITEMS = 2  # table_t.items
_FIELD_ITEM_DATA = 2  # items_t.data
_FIELD_FULLPACKET_STRING_TABLE = 1  # CDemoFullPacket.string_table
_FIELD_PLAYERINFO_NAME = 1  # CMsgPlayerInfo.name        (string)
_FIELD_PLAYERINFO_XUID = 2  # CMsgPlayerInfo.xuid        (fixed64)
_FIELD_PLAYERINFO_USERID = 3  # CMsgPlayerInfo.userid     (int32)
_FIELD_PLAYERINFO_FAKEPLAYER = 5  # CMsgPlayerInfo.fakeplayer (bool)
_FIELD_PLAYERINFO_ISHLTV = 6  # CMsgPlayerInfo.ishltv     (bool)


@dataclass
class SlotInfo:
    steamid: str
    name: str
    user_id: int
    is_bot: bool = False
    is_hltv: bool = False

    @property
    def slot(self) -> int:
        """The value CS2 expects in ``spec_player``."""
        return (self.user_id & 0xFF) + 1


class _ProtobufError(ValueError):
    pass


def _read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = 0
    shift = 0
    length = len(buf)
    while True:
        if pos >= length or shift > 63:
            raise _ProtobufError("truncated varint")
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7


def _iter_fields(buf: bytes) -> Iterator[tuple[int, int, object]]:
    """Yield ``(field_number, wire_type, value)`` for a protobuf message.

    Length-delimited values come back as ``bytes``; varints and fixed widths as
    ``int``. Unknown fields are skipped, which is what makes this robust across
    game updates.
    """
    pos = 0
    length = len(buf)
    while pos < length:
        key, pos = _read_varint(buf, pos)
        field_number, wire_type = key >> 3, key & 0x07
        if wire_type == 0:
            value, pos = _read_varint(buf, pos)
        elif wire_type == 2:
            size, pos = _read_varint(buf, pos)
            if size < 0 or pos + size > length:
                raise _ProtobufError("truncated length-delimited field")
            value = buf[pos : pos + size]
            pos += size
        elif wire_type == 5:
            value = int.from_bytes(buf[pos : pos + 4], "little")
            pos += 4
        elif wire_type == 1:
            value = int.from_bytes(buf[pos : pos + 8], "little")
            pos += 8
        elif wire_type in (3, 4):  # deprecated groups — nothing we read uses them
            raise _ProtobufError("unsupported group wire type")
        else:
            raise _ProtobufError(f"unknown wire type {wire_type}")
        yield field_number, wire_type, value


def _snappy_decompress(payload: bytes) -> bytes | None:
    """Snappy raw decompression via cramjam, python-snappy or snappy."""
    try:
        import cramjam  # type: ignore

        return bytes(cramjam.snappy.decompress_raw(payload))
    except Exception:
        pass
    try:
        import snappy  # type: ignore

        return snappy.decompress(payload)
    except Exception:
        return None


def iter_demo_frames(path: str | os.PathLike[str], kinds: set[int] | None = None) -> Iterator[tuple[int, int, bytes]]:
    """Stream ``(kind, tick, payload)`` frames of a CS2 demo.

    Payloads of frames whose ``kind`` is not in ``kinds`` are skipped without
    decompression, which keeps this cheap on large demos.
    """
    with open(path, "rb") as handle:
        magic = handle.read(8)
        if magic != CS2_MAGIC:
            raise ValueError(f"not a CS2 demo (magic={magic!r})")
        handle.read(8)  # fileinfo + spawngroups offsets
        buffer = b""
        while True:
            # Read enough for three varints (max 5 bytes each) plus a margin.
            if len(buffer) < 16:
                buffer += handle.read(1 << 16)
            if not buffer:
                return
            try:
                kind, pos = _read_varint(buffer, 0)
                tick, pos = _read_varint(buffer, pos)
                size, pos = _read_varint(buffer, pos)
            except _ProtobufError:
                return
            buffer = buffer[pos:]
            while len(buffer) < size:
                chunk = handle.read(max(size - len(buffer), 1 << 16))
                if not chunk:
                    return
                buffer += chunk
            payload = buffer[:size]
            buffer = buffer[size:]

            base_kind = kind & ~DEM_IS_COMPRESSED
            if base_kind == DEM_STOP:
                return
            if kinds is not None and base_kind not in kinds:
                continue
            if kind & DEM_IS_COMPRESSED:
                decompressed = _snappy_decompress(payload)
                if decompressed is None:
                    log.debug("snappy unavailable or payload corrupt; stopping slot scan")
                    return
                payload = decompressed
            yield base_kind, tick, payload


def _parse_player_info(blob: bytes) -> SlotInfo | None:
    name = ""
    xuid = 0
    user_id: int | None = None
    is_bot = False
    is_hltv = False
    try:
        for field_number, wire_type, value in _iter_fields(blob):
            if field_number == _FIELD_PLAYERINFO_NAME and wire_type == 2:
                name = bytes(value).decode("utf-8", "replace")
            elif field_number == _FIELD_PLAYERINFO_XUID and wire_type == 1:
                xuid = int(value)
            elif field_number == _FIELD_PLAYERINFO_USERID and wire_type == 0:
                user_id = int(value)
            elif field_number == _FIELD_PLAYERINFO_FAKEPLAYER:
                is_bot = bool(value)
            elif field_number == _FIELD_PLAYERINFO_ISHLTV:
                is_hltv = bool(value)
    except _ProtobufError:
        return None
    if user_id is None:
        return None
    return SlotInfo(steamid=str(xuid), name=name, user_id=user_id, is_bot=is_bot, is_hltv=is_hltv)


def _collect_from_string_tables(payload: bytes, found: dict[str, SlotInfo]) -> None:
    try:
        for field_number, wire_type, value in _iter_fields(payload):
            if field_number != _FIELD_TABLES or wire_type != 2:
                continue
            table_name = ""
            items: list[bytes] = []
            for sub_number, sub_wire, sub_value in _iter_fields(bytes(value)):
                if sub_number == _FIELD_TABLE_NAME and sub_wire == 2:
                    table_name = bytes(sub_value).decode("utf-8", "replace")
                elif sub_number == _FIELD_TABLE_ITEMS and sub_wire == 2:
                    items.append(bytes(sub_value))
            if table_name != "userinfo":
                continue
            for item in items:
                for item_number, item_wire, item_value in _iter_fields(item):
                    if item_number != _FIELD_ITEM_DATA or item_wire != 2:
                        continue
                    info = _parse_player_info(bytes(item_value))
                    if info and info.steamid not in ("0", "") and not info.is_hltv:
                        found[info.steamid] = info
    except _ProtobufError:
        return


def read_player_slots(path: str | os.PathLike[str]) -> dict[str, SlotInfo]:
    """Map ``steamid -> SlotInfo`` for every human player in the demo.

    Returns an empty mapping (never raises) when the demo cannot be walked, so
    callers can fall back to a heuristic without special-casing errors.
    """
    found: dict[str, SlotInfo] = {}
    try:
        for kind, _tick, payload in iter_demo_frames(path, kinds={DEM_STRING_TABLES, DEM_FULL_PACKET}):
            if kind == DEM_STRING_TABLES:
                _collect_from_string_tables(payload, found)
            elif kind == DEM_FULL_PACKET:
                try:
                    for field_number, wire_type, value in _iter_fields(payload):
                        if field_number == _FIELD_FULLPACKET_STRING_TABLE and wire_type == 2:
                            _collect_from_string_tables(bytes(value), found)
                except _ProtobufError:
                    continue
            # Slots never change mid-match; once every seat is taken we can stop.
            if len(found) >= 10:
                break
    except (OSError, ValueError) as exc:
        log.debug("slot extraction failed for %s: %s", path, exc)
        return {}
    log.debug("slot extraction found %d players in %s", len(found), os.path.basename(str(path)))
    return found


def slots_by_steamid(path: str | os.PathLike[str]) -> dict[str, int]:
    return {steamid: info.slot for steamid, info in read_player_slots(path).items()}
