import pytest

from src.color import linear_to_srgb, srgb_to_linear


def test_srgb_linear_round_trip():
    for value in (0.0, 0.1, 0.5, 0.75, 1.0):
        assert linear_to_srgb(srgb_to_linear(value)) == pytest.approx(value)


def test_known_reference_values():
    # 0.5 gamma sRGB is approximately 0.214 in scene-linear.
    assert srgb_to_linear(0.5) == pytest.approx(0.2140, abs=1e-3)
    assert linear_to_srgb(0.2140) == pytest.approx(0.5, abs=1e-3)
