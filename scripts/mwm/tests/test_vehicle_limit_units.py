#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from vehicle_limit_units import (  # noqa: E402
    classify_tag_value,
    metres_to_cm,
    parse_length_metres,
    parse_weight_tonnes,
    tonnes_to_10kg,
)


class VehicleLimitUnitsTest(unittest.TestCase):
    def test_metres(self) -> None:
        self.assertAlmostEqual(parse_length_metres("2.7") or 0, 2.7)
        self.assertAlmostEqual(parse_length_metres("2.7 m") or 0, 2.7)
        self.assertAlmostEqual(parse_length_metres("12.40") or 0, 12.4)
        self.assertIsNone(parse_length_metres("default"))
        self.assertIsNone(parse_length_metres("none"))
        self.assertIsNone(parse_length_metres("unsigned"))
        self.assertIsNone(parse_length_metres("3.5 @ (Mo-Fr)"))

    def test_feet_inches(self) -> None:
        v = parse_length_metres("13'6\"")
        self.assertIsNotNone(v)
        assert v is not None
        self.assertAlmostEqual(v, 13 * 0.3048 + 6 * 0.0254, places=4)

    def test_weight(self) -> None:
        self.assertAlmostEqual(parse_weight_tonnes("7.5") or 0, 7.5)
        self.assertAlmostEqual(parse_weight_tonnes("7.5 t") or 0, 7.5)
        self.assertAlmostEqual(parse_weight_tonnes("7500 kg") or 0, 7.5)
        self.assertIsNone(parse_weight_tonnes("default"))
        self.assertEqual(tonnes_to_10kg(7.5), 750)
        self.assertEqual(metres_to_cm(2.7), 270)

    def test_classify_no_limit_specials(self) -> None:
        for v in ("none", "default", "unsigned", "no-limit", "nolimit", "no", "unlimited"):
            self.assertEqual(classify_tag_value(v), "non_numeric_no_limit", v)
            self.assertIsNone(parse_length_metres(v))
            self.assertIsNone(parse_weight_tonnes(v))

    def test_classify_below_default_unknown(self) -> None:
        self.assertEqual(classify_tag_value("below_default"), "unknown_value")
        self.assertIsNone(parse_length_metres("below_default"))

    def test_classify_numeric(self) -> None:
        self.assertEqual(classify_tag_value("3.5"), "numeric")
        self.assertEqual(classify_tag_value("3,5"), "numeric")
        self.assertEqual(classify_tag_value("4.0 m"), "numeric")
        self.assertEqual(classify_tag_value("11'6\""), "numeric")
        self.assertEqual(classify_tag_value("7.5 t"), "numeric")


if __name__ == "__main__":
    unittest.main()
