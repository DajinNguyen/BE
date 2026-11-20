"""올린 사진에서 위치(GPS)·촬영 기기 같은 메타데이터를 지우고, 파일 구조가 진짜 사진인지 확인한다.

외부 라이브러리 없이 jpg·png·webp 의 구조(마커·청크)를 직접 읽는다.
- 이미지 데이터는 건드리지 않고 그대로 옮긴다 (다시 압축하지 않아서 화질 손실이 없다).
- 메타데이터 칸(EXIF·XMP·텍스트·썸네일 등)과, 사진이 끝난 뒤에 덧붙은 데이터는 버린다.
  (사진 뒤에 스크립트 같은 다른 내용을 붙인 파일이 그대로 저장되는 걸 막는다)
- 구조가 맞지 않으면 `ValueError`. 부르는 쪽이 "사진을 읽을 수 없어요"로 알린다.
"""

import struct
import zlib

# ------------------------------------------------------------------ JPEG

_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
_ICC = b"ICC_PROFILE\x00"


def _keep_jpeg_segment(marker: int, payload: bytes) -> bool:
    if marker == 0xFE:  # 설명글(COM)
        return False
    if marker == 0xE2:  # 색 프로필(ICC)만 남긴다 (색이 달라 보이지 않게). 나머지(MPF 등)는 버린다
        return payload.startswith(_ICC)
    if 0xE0 <= marker <= 0xEF:
        # APP0(JFIF)와 APP14(Adobe 색 변환)만 남긴다. APP1(EXIF·XMP)·APP13(IPTC) 등은 버린다
        return marker in (0xE0, 0xEE)
    return True


def _clean_jpeg(data: bytes) -> bytes:
    n = len(data)
    if data[:2] != b"\xff\xd8":
        raise ValueError("not a jpeg")
    out = [b"\xff\xd8"]
    i = 2
    seen_sof = seen_scan = False
    while True:
        while i < n and data[i] == 0xFF and i + 1 < n and data[i + 1] == 0xFF:
            i += 1  # 마커 앞 채움 바이트
        if i + 1 >= n or data[i] != 0xFF:
            raise ValueError("jpeg marker expected")
        marker = data[i + 1]
        i += 2
        if marker == 0xD9:  # EOI: 여기서 끝. 뒤에 덧붙은 것은 버린다
            if not seen_scan:
                raise ValueError("jpeg without image data")
            out.append(b"\xff\xd9")
            return b"".join(out)
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:  # 길이 없는 마커
            out.append(bytes([0xFF, marker]))
            continue
        if i + 2 > n:
            raise ValueError("truncated jpeg")
        length = int.from_bytes(data[i : i + 2], "big")
        if length < 2 or i + length > n:
            raise ValueError("bad jpeg segment")
        payload = data[i + 2 : i + length]
        if _keep_jpeg_segment(marker, payload):
            out.append(bytes([0xFF, marker]) + data[i : i + length])
        seen_sof = seen_sof or marker in _SOF
        i += length
        if marker == 0xDA:  # 스캔 시작: 다음 (RST 가 아닌) 마커까지가 이미지 데이터
            if not seen_sof:
                raise ValueError("jpeg scan before frame header")
            seen_scan = True
            j = i
            while j + 1 < n:
                nxt = data[j + 1]
                if data[j] == 0xFF and nxt not in (0x00, 0xFF) and not 0xD0 <= nxt <= 0xD7:
                    break
                j += 1
            else:
                raise ValueError("jpeg without end marker")
            out.append(data[i:j])
            i = j


# ------------------------------------------------------------------ PNG

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# 메타데이터 청크: EXIF, 글(tEXt·zTXt·iTXt), 수정 시각
_PNG_DROP = {b"eXIf", b"tEXt", b"zTXt", b"iTXt", b"tIME"}


def _clean_png(data: bytes) -> bytes:
    n = len(data)
    out = [_PNG_SIGNATURE]
    pos = 8
    first = True
    seen_idat = False
    while pos + 12 <= n:
        length = int.from_bytes(data[pos : pos + 4], "big")
        kind = data[pos + 4 : pos + 8]
        end = pos + 12 + length
        if length > 0x7FFFFFFF or end > n:
            raise ValueError("bad png chunk")
        if first and kind != b"IHDR":
            raise ValueError("png must start with IHDR")
        first = False
        if zlib.crc32(data[pos + 4 : pos + 8 + length]) != int.from_bytes(
            data[end - 4 : end], "big"
        ):
            raise ValueError("png chunk checksum mismatch")
        seen_idat = seen_idat or kind == b"IDAT"
        if kind not in _PNG_DROP:
            out.append(data[pos:end])
        pos = end
        if kind == b"IEND":
            if not seen_idat:
                raise ValueError("png without image data")
            return b"".join(out)  # IEND 뒤에 덧붙은 것은 버린다
    raise ValueError("png without IEND")


# ------------------------------------------------------------------ WebP

_WEBP_DROP = {b"EXIF", b"XMP "}
_VP8X_EXIF, _VP8X_XMP = 0x08, 0x04
_WEBP_IMAGE = {b"VP8 ", b"VP8L", b"VP8X"}


def _clean_webp(data: bytes) -> bytes:
    if data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise ValueError("not a webp")
    total = int.from_bytes(data[4:8], "little") + 8
    if total < 12 or total > len(data):
        raise ValueError("bad webp size")
    chunks: list[tuple[bytes, bytes]] = []
    pos = 12
    while pos + 8 <= total:
        tag = data[pos : pos + 4]
        size = int.from_bytes(data[pos + 4 : pos + 8], "little")
        end = pos + 8 + size
        if end > total:
            raise ValueError("bad webp chunk")
        if tag not in _WEBP_DROP:
            payload = data[pos + 8 : end]
            if tag == b"VP8X" and payload:
                payload = bytes([payload[0] & ~(_VP8X_EXIF | _VP8X_XMP)]) + payload[1:]
            chunks.append((tag, payload))
        pos = end + (size & 1)
    if not any(tag in _WEBP_IMAGE for tag, _ in chunks):
        raise ValueError("webp without image data")
    body = b"WEBP"
    for tag, payload in chunks:
        body += (
            tag + struct.pack("<I", len(payload)) + payload + (b"\x00" if len(payload) & 1 else b"")
        )
    return b"RIFF" + struct.pack("<I", len(body)) + body


# ------------------------------------------------------------------

_CLEANERS = {"jpg": _clean_jpeg, "png": _clean_png, "webp": _clean_webp}


def clean(kind: str, data: bytes) -> bytes:
    """메타데이터를 뺀 사진 데이터. 구조가 맞지 않으면 ValueError."""
    try:
        return _CLEANERS[kind](data)
    except (ValueError, struct.error, IndexError) as e:
        raise ValueError(f"invalid {kind}: {e}") from e
