"""올린 사진에서 위치(GPS)·촬영 기기 같은 메타데이터를 지우고, 파일 구조가 진짜 사진인지 확인한다.

외부 라이브러리 없이 jpg·png·webp 의 구조(마커·청크)를 직접 읽는다.
- 이미지 데이터는 건드리지 않고 그대로 옮긴다 (다시 압축하지 않아서 화질 손실이 없다).
- 메타데이터 칸(EXIF·XMP·텍스트·썸네일 등)과, 사진이 끝난 뒤에 덧붙은 데이터는 버린다.
  (사진 뒤에 스크립트 같은 다른 내용을 붙인 파일이 그대로 저장되는 걸 막는다)
- 구조가 맞지 않으면 `ValueError`. 부르는 쪽이 "사진을 읽을 수 없어요"로 알린다.
"""

import struct
import zlib

# ------------------------------------------------------------------ 회전 정보(Orientation)
# 휴대폰 사진은 화면에 보이는 방향이 아니라 센서 방향 그대로 저장하고, "이만큼 돌려서 보여라"는 값을 EXIF 의
# Orientation 에 적어 둔다. EXIF 를 통째로 지우면 세로로 찍은 사진이 옆으로 누워 보인다. 그래서 위치·기기 같은 정보는
# 모두 지우되, 이 값(2~8)만 담은 아주 작은 EXIF 를 다시 만들어 남긴다.

_ORIENTATION_TAG = 0x0112


def _orientation(tiff: bytes) -> int | None:
    """TIFF 구조(EXIF 본문)에서 Orientation 값 (2~8). 없거나 1(회전 없음)이거나 구조가 이상하면 None."""
    try:
        if tiff[:2] == b"II":
            order = "little"
        elif tiff[:2] == b"MM":
            order = "big"
        else:
            return None
        if int.from_bytes(tiff[2:4], order) != 42:
            return None
        ifd = int.from_bytes(tiff[4:8], order)
        count = int.from_bytes(tiff[ifd : ifd + 2], order)
        for i in range(min(count, 64)):
            entry = ifd + 2 + i * 12
            tag = int.from_bytes(tiff[entry : entry + 2], order)
            if tag != _ORIENTATION_TAG:
                continue
            kind = int.from_bytes(tiff[entry + 2 : entry + 4], order)
            if kind != 3 or int.from_bytes(tiff[entry + 4 : entry + 8], order) != 1:
                return None
            value = int.from_bytes(tiff[entry + 8 : entry + 10], order)
            return value if 2 <= value <= 8 else None
    except (IndexError, ValueError):
        return None
    return None


def _orientation_tiff(value: int) -> bytes:
    """Orientation 하나만 든 TIFF 구조 (빅 엔디안, 26바이트)."""
    return (
        b"MM\x00\x2a\x00\x00\x00\x08"  # 머리: IFD 는 8바이트 뒤
        + b"\x00\x01"  # 항목 1개
        + struct.pack(">HHIHxx", _ORIENTATION_TAG, 3, 1, value)  # 태그, 형식(SHORT), 개수, 값
        + b"\x00\x00\x00\x00"  # 다음 IFD 없음
    )


# ------------------------------------------------------------------ JPEG

_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
_ICC = b"ICC_PROFILE\x00"


_EXIF_HEADER = b"Exif\x00\x00"


def _jpeg_segment_payload(marker: int, payload: bytes) -> bytes | None:
    """남길 마커 칸의 내용. 버릴 칸은 None. EXIF 는 회전 값만 담은 새 EXIF 로 바꾼다."""
    if marker == 0xE1 and payload.startswith(_EXIF_HEADER):
        value = _orientation(payload[len(_EXIF_HEADER) :])
        return _EXIF_HEADER + _orientation_tiff(value) if value else None
    return payload if _keep_jpeg_segment(marker, payload) else None


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
        kept = _jpeg_segment_payload(marker, payload)
        if kept is not None:
            out.append(bytes([0xFF, marker]) + struct.pack(">H", len(kept) + 2) + kept)
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
# 메타데이터 청크: EXIF(회전 값만 남긴다), 글(tEXt·zTXt·iTXt), 수정 시각
_PNG_DROP = {b"eXIf", b"tEXt", b"zTXt", b"iTXt", b"tIME"}


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


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
        if kind == b"eXIf":
            value = _orientation(data[pos + 8 : pos + 8 + length])
            if value:
                out.append(_png_chunk(b"eXIf", _orientation_tiff(value)))
        elif kind not in _PNG_DROP:
            out.append(data[pos:end])
        pos = end
        if kind == b"IEND":
            if not seen_idat:
                raise ValueError("png without image data")
            return b"".join(out)  # IEND 뒤에 덧붙은 것은 버린다
    raise ValueError("png without IEND")


# ------------------------------------------------------------------ WebP

_WEBP_DROP = {b"XMP "}  # EXIF 는 회전 값만 남기고 다시 쓴다
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
        payload = data[pos + 8 : end]
        if tag == b"EXIF":
            tiff = payload[len(_EXIF_HEADER) :] if payload.startswith(_EXIF_HEADER) else payload
            value = _orientation(tiff)
            if value:
                chunks.append((tag, _orientation_tiff(value)))
        elif tag not in _WEBP_DROP:
            chunks.append((tag, payload))
        pos = end + (size & 1)
    has_exif = any(tag == b"EXIF" for tag, _ in chunks)
    chunks = [
        (
            (
                tag,
                bytes([(payload[0] & ~(_VP8X_EXIF | _VP8X_XMP)) | (_VP8X_EXIF if has_exif else 0)])
                + payload[1:],
            )
            if tag == b"VP8X" and payload
            else (tag, payload)
        )
        for tag, payload in chunks
    ]
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
