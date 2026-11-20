import pytest

from app.services import image_clean
from tests.images import (
    GPS_EXIF,
    exif_tiff,
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


# ---------------------------------------------------------------- 회전(Orientation) 은 남긴다


def _jpeg_with_exif(tiff: bytes) -> bytes:
    return make_jpeg(jpeg_segment(0xE1, b"Exif\x00\x00" + tiff))


def _kept_orientation(cleaned: bytes) -> int | None:
    start = cleaned.find(b"Exif\x00\x00")
    return image_clean._orientation(cleaned[start + 6 :]) if start >= 0 else None


@pytest.mark.parametrize("little", [False, True])
@pytest.mark.parametrize("orientation", [2, 3, 6, 8])
def test_jpeg_keeps_only_the_rotation_from_exif(orientation, little):
    """세로로 찍은 휴대폰 사진은 '돌려서 보여라'는 값이 EXIF 에만 있다. 지우면 옆으로 누워 보인다."""
    data = _jpeg_with_exif(exif_tiff(orientation, little=little))
    assert b"GPSLatitude" in data and b"Appl" in data
    cleaned = image_clean.clean("jpg", data)
    assert b"GPSLatitude" not in cleaned and b"Appl" not in cleaned
    assert _kept_orientation(cleaned) == orientation
    assert image_clean.clean("jpg", cleaned) == cleaned  # 두 번 걸러도 같다


def test_jpeg_without_rotation_keeps_no_exif_at_all():
    for tiff in (exif_tiff(None), exif_tiff(1)):  # 회전 없음(1)도 지운다
        cleaned = image_clean.clean("jpg", _jpeg_with_exif(tiff))
        assert b"Exif" not in cleaned and b"GPSLatitude" not in cleaned


def test_broken_exif_is_dropped_not_fatal():
    """EXIF 가 깨져 있어도 사진은 올라가고, 깨진 EXIF 는 버린다."""
    for tiff in (b"", b"II*\x00", b"MM\x00*\xff\xff\xff\xff" + b"\x00" * 20, b"XX" + b"\x00" * 30):
        cleaned = image_clean.clean("jpg", _jpeg_with_exif(tiff))
        assert b"Exif" not in cleaned and cleaned.endswith(b"\xff\xd9")


def test_png_keeps_only_the_rotation():
    data = make_png(png_chunk(b"eXIf", exif_tiff(6)))
    cleaned = image_clean.clean("png", data)
    assert b"GPSLatitude" not in cleaned and b"Appl" not in cleaned
    start = cleaned.find(b"eXIf")
    assert image_clean._orientation(cleaned[start + 4 : start + 4 + 26]) == 6


def test_webp_keeps_only_the_rotation_and_its_flag():
    data = make_webp(exif=exif_tiff(8))
    cleaned = image_clean.clean("webp", data)
    assert b"GPSLatitude" not in cleaned and b"Appl" not in cleaned
    assert b"EXIF" in cleaned
    flags = cleaned[12 + 8]  # VP8X 의 표시 바이트
    assert flags & 0x08  # EXIF 표시가 켜져 있다
    assert int.from_bytes(cleaned[4:8], "little") == len(cleaned) - 8
    assert image_clean._orientation(cleaned[cleaned.find(b"EXIF") + 8 :][:26]) == 8


def test_webp_without_rotation_clears_the_exif_flag():
    cleaned = image_clean.clean("webp", make_webp(exif=exif_tiff(None)))
    assert b"EXIF" not in cleaned and not cleaned[12 + 8] & 0x08


def test_check_images_script_reports_per_file(tmp_path, capsys):
    from app.scripts import check_images

    good = tmp_path / "phone.jpg"
    good.write_bytes(_jpeg_with_exif(exif_tiff(6)))
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"\xff\xd8\xff" + b"0" * 10)
    heic = tmp_path / "iphone.heic"
    heic.write_bytes(b"\x00\x00\x00\x18ftypheic" + b"0" * 20)

    assert check_images.main([str(good)]) == 0
    assert check_images.main([str(good), str(broken), str(heic)]) == 1
    out = capsys.readouterr().out
    assert "[통과] phone.jpg" in out and "회전 6 유지" in out and "GPS 제거" in out
    assert "[실패] broken.jpg" in out and "[실패] iphone.heic" in out and "HEIC" in out
