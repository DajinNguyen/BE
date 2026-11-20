import pytest

from app.services import image_clean
from tests.images import (
    GPS_EXIF,
    jpeg_segment,
    make_jpeg,
    make_png,
    make_webp,
    png_chunk,
)

HTML = b"<html><script>alert(1)</script></html>"


def test_jpeg_drops_exif_and_comments_but_keeps_image_and_color_profile():
    icc = jpeg_segment(0xE2, b"ICC_PROFILE\x00" + b"\x01" * 20)
    mpf = jpeg_segment(0xE2, b"MPF\x00" + b"\x01" * 20)
    data = make_jpeg(
        jpeg_segment(0xE1, GPS_EXIF),
        jpeg_segment(0xED, b"IPTC"),
        jpeg_segment(0xFE, b"hi"),
        icc,
        mpf,
    )
    cleaned = image_clean.clean("jpg", data)
    assert b"GPSLatitude" not in cleaned and b"IPTC" not in cleaned and b"MPF" not in cleaned
    assert icc in cleaned  # 색 프로필은 남긴다 (색이 달라 보이지 않게)
    # 이미지 데이터(DQT·SOF·DHT·SOS·데이터)는 그대로
    assert cleaned == make_jpeg(icc)
    assert image_clean.clean("jpg", cleaned) == cleaned  # 두 번 걸러도 같다


def test_jpeg_drops_data_appended_after_the_image():
    cleaned = image_clean.clean("jpg", make_jpeg(trailing=HTML))
    assert HTML not in cleaned and cleaned.endswith(b"\xff\xd9")


@pytest.mark.parametrize(
    "data",
    [
        b"\xff\xd8\xff",  # 머리만 있는 가짜
        b"\xff\xd8" + b"0" * 50,
        make_jpeg()[:-2],  # 끝(EOI) 없음
        make_jpeg()[:20],  # 중간에 잘림
    ],
)
def test_broken_jpeg_is_rejected(data):
    with pytest.raises(ValueError):
        image_clean.clean("jpg", data)


def test_png_drops_text_and_exif_chunks_and_trailing_data():
    data = make_png(
        png_chunk(b"eXIf", GPS_EXIF),
        png_chunk(b"tEXt", b"Comment\x00secret"),
        png_chunk(b"tIME", b"\x07\xe9\x0b\x14\x0a\x00\x00"),
        png_chunk(b"gAMA", b"\x00\x01\x86\xa0"),
        trailing=HTML,
    )
    cleaned = image_clean.clean("png", data)
    assert b"GPSLatitude" not in cleaned and b"secret" not in cleaned and HTML not in cleaned
    assert cleaned == make_png(
        png_chunk(b"gAMA", b"\x00\x01\x86\xa0")
    )  # 그림에 필요한 청크는 남는다


@pytest.mark.parametrize(
    "data",
    [
        make_png()[:-5],  # IEND 잘림
        make_png().replace(b"IDAT", b"IDAX"),  # 체크섬이 맞지 않음
        b"\x89PNG\r\n\x1a\n" + b"0" * 100,  # 머리만 있는 가짜
    ],
)
def test_broken_png_is_rejected(data):
    with pytest.raises(ValueError):
        image_clean.clean("png", data)


def test_webp_drops_exif_chunk_and_clears_its_flag():
    data = make_webp(exif=GPS_EXIF)
    assert b"GPSLatitude" in data
    cleaned = image_clean.clean("webp", data)
    assert b"GPSLatitude" not in cleaned and b"EXIF" not in cleaned
    assert cleaned == make_webp()  # VP8X 의 EXIF 표시도 꺼진다
    assert int.from_bytes(cleaned[4:8], "little") == len(cleaned) - 8  # RIFF 크기가 맞다


@pytest.mark.parametrize("data", [b"RIFF\x04\x00\x00\x00WEBP", b"RIFF" + b"\xff" * 4 + b"WEBP"])
def test_broken_webp_is_rejected(data):
    with pytest.raises(ValueError):
        image_clean.clean("webp", data)
