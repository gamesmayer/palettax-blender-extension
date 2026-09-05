import struct

import pytest

from src.ase import _cmyk_to_rgb, _gray_to_rgb, _lab_to_rgb, parse_ase


def _pack_name(name):
    encoded = name.encode("utf-16-be")
    name_len = len(encoded) // 2 + 1  # includes the null terminator
    return struct.pack(">H", name_len) + encoded + b"\x00\x00"


def _color_block(name, tag, values, color_type=0):
    data = _pack_name(name)
    data += tag.encode("ascii")
    data += struct.pack(f">{len(values)}f", *values)
    data += struct.pack(">H", color_type)
    return struct.pack(">HI", 0x0001, len(data)) + data


def _group_start_block(name):
    data = _pack_name(name)
    return struct.pack(">HI", 0xC001, len(data)) + data


def _group_end_block():
    return struct.pack(">HI", 0xC002, 0)


def _build_ase_file(blocks):
    header = b"ASEF" + struct.pack(">HH", 1, 0) + struct.pack(">I", len(blocks))
    return header + b"".join(blocks)


def test_parse_ase_groups_and_names(tmp_path):
    blocks = [
        _color_block("Red", "RGB ", (1.0, 0.0, 0.0)),
        _group_start_block("Warm"),
        _color_block("Orange", "CMYK", (0.0, 0.5, 1.0, 0.0)),
        _color_block("Gray1", "Gray", (0.5,)),
        _group_end_block(),
    ]
    filepath = tmp_path / "fixture.ase"
    filepath.write_bytes(_build_ase_file(blocks))

    colors, group_order = parse_ase(str(filepath))

    assert group_order == ["Warm"]
    assert len(colors) == 3

    assert colors[0].name == "Red"
    assert colors[0].group == ""
    assert colors[0].rgb == pytest.approx((1.0, 0.0, 0.0))

    assert colors[1].name == "Orange"
    assert colors[1].group == "Warm"
    assert colors[1].rgb == pytest.approx((1.0, 0.5, 0.0))

    assert colors[2].name == "Gray1"
    assert colors[2].group == "Warm"
    assert colors[2].rgb == pytest.approx((0.5, 0.5, 0.5))


def test_parse_ase_rejects_bad_signature(tmp_path):
    filepath = tmp_path / "bad.ase"
    filepath.write_bytes(b"NOPE" + struct.pack(">HHI", 1, 0, 0))

    with pytest.raises(ValueError):
        parse_ase(str(filepath))


def test_cmyk_to_rgb():
    assert _cmyk_to_rgb(0.0, 0.0, 0.0, 0.0) == pytest.approx((1.0, 1.0, 1.0))
    assert _cmyk_to_rgb(0.0, 0.0, 0.0, 1.0) == pytest.approx((0.0, 0.0, 0.0))
    assert _cmyk_to_rgb(1.0, 0.0, 0.0, 0.0) == pytest.approx((0.0, 1.0, 1.0))


def test_gray_to_rgb():
    assert _gray_to_rgb(0.25) == pytest.approx((0.25, 0.25, 0.25))


def test_lab_to_rgb_white_and_black():
    assert _lab_to_rgb(100.0, 0.0, 0.0) == pytest.approx((1.0, 1.0, 1.0), abs=1e-2)
    assert _lab_to_rgb(0.0, 0.0, 0.0) == pytest.approx((0.0, 0.0, 0.0), abs=1e-2)
