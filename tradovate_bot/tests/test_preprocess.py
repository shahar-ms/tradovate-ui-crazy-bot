import numpy as np

from app.capture.preprocess import RECIPES, make_variants


def _synthetic_bgr():
    img = np.full((40, 120, 3), 240, dtype=np.uint8)
    img[10:30, 10:110] = 30  # dark "text" band
    return img


def test_all_recipes_return_numpy():
    img = _synthetic_bgr()
    for name, fn in RECIPES.items():
        out = fn(img)
        assert isinstance(out, np.ndarray), name
        assert out.ndim == 2, name  # should be grayscale/binary


def test_make_variants_respects_whitelist():
    img = _synthetic_bgr()
    variants = make_variants(img, ["gray_only", "nope_does_not_exist", "otsu_threshold"])
    assert set(variants.keys()) == {"gray_only", "otsu_threshold"}


def test_binary_is_two_values():
    img = _synthetic_bgr()
    b = RECIPES["binary_threshold"](img)
    unique = set(np.unique(b).tolist())
    assert unique.issubset({0, 255})
