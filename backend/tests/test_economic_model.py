from __future__ import annotations

import math
import unittest

from kelly_watcher.engine.economic_model import (
    clip_confidence,
    expected_return_to_confidence,
    inverse_return_target,
    transform_return_target,
)


class EconomicModelTest(unittest.TestCase):
    def test_return_target_transform_round_trips(self) -> None:
        values = [-1.0, -0.25, 0.0, 0.4, 1.75]
        restored = [inverse_return_target(transform_return_target(value)) for value in values]

        for original, recovered in zip(values, restored):
            self.assertAlmostEqual(original, recovered, places=6)

    def test_confidence_helpers_preserve_nonfinite_values_as_invalid(self) -> None:
        self.assertTrue(math.isnan(clip_confidence(float("inf"))))
        self.assertTrue(math.isnan(expected_return_to_confidence(float("inf"), 0.50)))
        self.assertTrue(math.isnan(expected_return_to_confidence(0.10, float("nan"))))
        self.assertAlmostEqual(expected_return_to_confidence(2.0, 0.80), 0.9999, places=6)


if __name__ == "__main__":
    unittest.main()
