"""테스트용 아주 작은 사진 만들기 (구조만 맞는 가짜 사진: 화면에 그리는 용도가 아니다)."""

import struct
import zlib


def png_chunk(kind: bytes, data: bytes = b"") -> bytes:
    crc = zlib.crc32(kind + data)
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)


def make_png(*extra: bytes, trailing: bytes = b"") -> bytes:
    """IHDR → (extra 청크) → IDAT → IEND. extra 는 png_chunk(...) 로 만든 청크."""
    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\xff\x00\x00")
    return (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", header)
        + b"".join(extra)
        + png_chunk(b"IDAT", idat)
        + png_chunk(b"IEND")
        + trailing
    )


def jpeg_segment(marker: int, payload: bytes) -> bytes:
    return bytes([0xFF, marker]) + struct.pack(">H", len(payload) + 2) + payload


GPS_EXIF = b"Exif\x00\x00MM\x00*GPSLatitude=37.5665,GPSLongitude=126.9780"


def make_jpeg(*extra_segments: bytes, trailing: bytes = b"") -> bytes:
    """SOI → APP0 → (extra 마커 칸) → DQT → SOF0 → DHT → SOS(+이미지 데이터) → EOI."""
    return (
        b"\xff\xd8"
        + jpeg_segment(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")
        + b"".join(extra_segments)
        + jpeg_segment(0xDB, b"\x00" + b"\x01" * 64)
        + jpeg_segment(0xC0, b"\x08\x00\x01\x00\x01\x01\x01\x11\x00")
        + jpeg_segment(0xC4, b"\x00" + b"\x00" * 16)
        + jpeg_segment(0xDA, b"\x01\x01\x00\x00\x3f\x00")
        + b"\x12\x34\xff\x00\x56"  # 이미지 데이터 (0xFF00 은 데이터 안의 0xFF)
        + b"\xff\xd9"
        + trailing
    )


def webp_chunk(tag: bytes, payload: bytes) -> bytes:
    return tag + struct.pack("<I", len(payload)) + payload + (b"\x00" if len(payload) & 1 else b"")


def make_webp(*, exif: bytes | None = None) -> bytes:
    """VP8X(+EXIF) → VP8L. exif 를 주면 VP8X 의 EXIF 표시를 켜고 EXIF 칸을 넣는다."""
    flags = 0x08 if exif is not None else 0
    vp8x = webp_chunk(b"VP8X", bytes([flags]) + b"\x00\x00\x00" + b"\x00\x00\x00" + b"\x00\x00\x00")
    body = b"WEBP" + vp8x + webp_chunk(b"VP8L", b"\x2f\x00\x00\x00\x00")
    if exif is not None:
        body += webp_chunk(b"EXIF", exif)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def exif_tiff(orientation: int | None = None, *, little: bool = False) -> bytes:
    """EXIF 본문(TIFF): 기기(Make), (있으면) 회전(Orientation), GPS 위치를 가리키는 칸이 든 것."""
    order = "<" if little else ">"
    entries = [
        (0x010F, 2, 6, b"Apple\x00"[:4])
    ]  # Make: 값이 4바이트를 넘으면 오프셋이지만 구조만 맞으면 돼요
    if orientation is not None:
        entries.append((0x0112, 3, 1, struct.pack(order + "H", orientation) + b"\x00\x00"))
    entries.append((0x8825, 4, 1, struct.pack(order + "I", 200)))  # GPS IFD 오프셋
    entries.sort()
    header = (b"II\x2a\x00" if little else b"MM\x00\x2a") + struct.pack(order + "I", 8)
    body = struct.pack(order + "H", len(entries))
    for tag, kind, count, value in entries:
        body += struct.pack(order + "HHI", tag, kind, count) + value
    body += b"\x00\x00\x00\x00"
    return header + body + b"GPSLatitude=37.5665,GPSLongitude=126.9780" + b"\x00" * 8
