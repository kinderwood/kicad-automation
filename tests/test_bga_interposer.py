"""Integration tests executed with KiCad's bundled Python interpreter."""

import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins"))
if os.environ.get("KICAD_WIZARD_DIR"):
    sys.path.insert(0, os.environ["KICAD_WIZARD_DIR"])

import pcbnew  # noqa: E402

from bga_interposer.wizard import BGAInterposerWizard  # noqa: E402


class BGAInterposerWizardTests(unittest.TestCase):
    def make_wizard(self, **values):
        wizard = BGAInterposerWizard()
        for key, value in values.items():
            page, name = key.split("__", 1)
            wizard.GetParam(page.replace("_", " "), name.replace("_", " ")).SetValue(value)
        wizard.BuildFootprint()
        return wizard

    @staticmethod
    def pads_by_name(wizard):
        return {
            str(pad.GetName()): pad
            for pad in wizard.module.Pads()
            if str(pad.GetName())
        }

    @staticmethod
    def mounting_holes(wizard):
        return [
            pad
            for pad in wizard.module.Pads()
            if pad.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH
        ]

    def test_default_20_by_20_with_8_by_8_opening(self):
        wizard = self.make_wizard()
        self.assertFalse(wizard.AnyErrors(), wizard.GetBuildMessages())
        pads = self.pads_by_name(wizard)

        self.assertEqual(336, len(pads))
        self.assertEqual(
            "BGA-Interposer-336_20x20_Opening8x8_26x26mm_P1mm", wizard.GetValue()
        )
        self.assertIn("A1", pads)
        self.assertIn("Y20", pads)
        self.assertNotIn("G7", pads)
        self.assertNotIn("P14", pads)
        self.assertIn("G6", pads)
        self.assertIn("R7", pads)

        positions = [pad.GetPosition() for pad in pads.values()]
        self.assertEqual(pcbnew.FromMM(-9.5), min(p.x for p in positions))
        self.assertEqual(pcbnew.FromMM(9.5), max(p.x for p in positions))
        self.assertEqual(pcbnew.FromMM(-9.5), min(p.y for p in positions))
        self.assertEqual(pcbnew.FromMM(9.5), max(p.y for p in positions))

        holes = self.mounting_holes(wizard)
        self.assertEqual(1, len(holes))
        self.assertEqual(
            pcbnew.VECTOR2I(pcbnew.FromMM(-11.5), pcbnew.FromMM(-11.5)),
            holes[0].GetPosition(),
        )
        self.assertEqual(
            pcbnew.VECTOR2I(pcbnew.FromMM(2.5), pcbnew.FromMM(2.5)),
            holes[0].GetDrillSize(),
        )
        self.assertEqual(337, len(list(wizard.module.Pads())))

    def test_parameter_pages_follow_drawing_entry_order(self):
        wizard = BGAInterposerWizard()
        self.assertEqual(
            ["Package", "Mounting features", "Pads", "Center opening"],
            [
                wizard.GetParameterPageName(index)
                for index in range(wizard.GetNumParameterPages())
            ],
        )

    def test_symmetric_corner_depopulation_depth_four(self):
        wizard = self.make_wizard(Pads__corner_depopulation_depth=4)
        self.assertFalse(wizard.AnyErrors(), wizard.GetBuildMessages())
        pads = self.pads_by_name(wizard)

        self.assertEqual(296, len(pads))
        top_left_removed = {
            "A1",
            "A2",
            "A3",
            "A4",
            "B1",
            "B2",
            "B3",
            "C1",
            "C2",
            "D1",
        }
        self.assertTrue(top_left_removed.isdisjoint(pads))
        self.assertTrue(
            {"A17", "A18", "A19", "A20", "Y1", "Y4", "Y17", "Y20"}.isdisjoint(pads)
        )
        for name in ("A5", "B4", "C3", "D2", "E1"):
            self.assertIn(name, pads)
        self.assertEqual(
            "BGA-Interposer-296_20x20_Opening8x8_26x26mm_P1mm",
            wizard.GetValue(),
        )

    def test_corner_depopulation_depth_is_bounded(self):
        wizard = self.make_wizard(Pads__corner_depopulation_depth=10)
        self.assertTrue(wizard.AnyErrors())
        self.assertIn("opposing corner cuts", wizard.GetBuildMessages())

    def test_corner_depopulation_can_clear_mounting_hole(self):
        wizard = self.make_wizard(
            Pads__corner_depopulation_depth=3,
            Mounting_features__x_distance=9.5,
            Mounting_features__y_distance=9.5,
        )
        self.assertFalse(wizard.AnyErrors(), wizard.GetBuildMessages())
        self.assertNotIn("A1", self.pads_by_name(wizard))

    def test_rectangular_array_and_opening(self):
        wizard = self.make_wizard(
            Pads__columns=18,
            Pads__rows=14,
            Center_opening__columns=6,
            Center_opening__rows=4,
            Package__width=18,
            Package__length=14,
            Mounting_features__enable_round_hole=False,
        )
        self.assertFalse(wizard.AnyErrors(), wizard.GetBuildMessages())
        self.assertEqual(228, len(list(wizard.module.Pads())))

    def test_generated_footprint_round_trips_through_kicad_format(self):
        wizard = self.make_wizard()
        with tempfile.TemporaryDirectory(suffix=".pretty") as library:
            plugin = pcbnew.PCB_IO_KICAD_SEXPR()
            plugin.FootprintSave(library, wizard.module)
            loaded = plugin.FootprintLoad(library, wizard.GetValue())
            self.assertIsNotNone(loaded)
            self.assertEqual(337, len(list(loaded.Pads())))

    def test_each_mounting_hole_corner(self):
        expected_positions = {
            "top_left_(A1_corner)": (-11.5, -11.5),
            "top_right": (11.5, -11.5),
            "bottom_right": (11.5, 11.5),
            "bottom_left": (-11.5, 11.5),
        }
        for selected, expected in expected_positions.items():
            values = {"Mounting_features__top_left_(A1_corner)": False}
            values["Mounting_features__" + selected] = True
            with self.subTest(corner=selected):
                wizard = self.make_wizard(**values)
                self.assertFalse(wizard.AnyErrors(), wizard.GetBuildMessages())
                hole = self.mounting_holes(wizard)[0]
                self.assertEqual(
                    pcbnew.VECTOR2I(
                        pcbnew.FromMM(expected[0]), pcbnew.FromMM(expected[1])
                    ),
                    hole.GetPosition(),
                )

    def test_round_hole_can_be_the_footprint_origin(self):
        wizard = self.make_wizard(Mounting_features__origin_at_round_hole=True)
        self.assertFalse(wizard.AnyErrors(), wizard.GetBuildMessages())
        self.assertEqual(
            pcbnew.VECTOR2I(0, 0), self.mounting_holes(wizard)[0].GetPosition()
        )
        self.assertEqual(
            pcbnew.VECTOR2I(pcbnew.FromMM(2), pcbnew.FromMM(2)),
            self.pads_by_name(wizard)["A1"].GetPosition(),
        )

    def test_round_hole_can_be_disabled(self):
        wizard = self.make_wizard(
            Mounting_features__enable_round_hole=False,
            Package__width=20,
            Package__length=20,
        )
        self.assertFalse(wizard.AnyErrors(), wizard.GetBuildMessages())
        self.assertEqual([], self.mounting_holes(wizard))
        self.assertEqual(336, len(list(wizard.module.Pads())))

    def test_multiple_mounting_hole_corners_are_rejected(self):
        wizard = self.make_wizard(Mounting_features__top_right=True)
        self.assertTrue(wizard.AnyErrors())
        self.assertIn("select exactly one corner", wizard.GetBuildMessages())

    def test_mounting_hole_must_fit_inside_package(self):
        wizard = self.make_wizard(Package__width=25)
        self.assertTrue(wizard.AnyErrors())
        self.assertIn("complete mounting hole", wizard.GetBuildMessages())

    def test_mounting_hole_must_not_overlap_bga_pad(self):
        wizard = self.make_wizard(
            Mounting_features__x_distance=9.5,
            Mounting_features__y_distance=9.5,
        )
        self.assertTrue(wizard.AnyErrors())
        self.assertIn("overlaps BGA pad A1", wizard.GetBuildMessages())

    def test_mounting_hole_origin_requires_enabled_hole(self):
        wizard = self.make_wizard(
            Mounting_features__enable_round_hole=False,
            Mounting_features__origin_at_round_hole=True,
        )
        self.assertTrue(wizard.AnyErrors())
        self.assertIn("requires the round hole", wizard.GetBuildMessages())

    def test_parity_mismatch_is_rejected(self):
        wizard = self.make_wizard(Center_opening__columns=7)
        self.assertTrue(wizard.AnyErrors())
        self.assertIn("matching parity", wizard.GetBuildMessages())

    def test_one_zero_opening_dimension_is_rejected(self):
        wizard = self.make_wizard(Center_opening__rows=0)
        self.assertTrue(wizard.AnyErrors())
        self.assertIn("both opening dimensions", wizard.GetBuildMessages())


if __name__ == "__main__":
    unittest.main()
