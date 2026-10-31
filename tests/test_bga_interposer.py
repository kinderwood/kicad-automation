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
    def npth_pads(wizard_or_footprint):
        footprint = getattr(wizard_or_footprint, "module", wizard_or_footprint)
        return [
            pad
            for pad in footprint.Pads()
            if pad.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH
        ]

    @classmethod
    def guide_pin_holes(cls, wizard):
        return [
            pad
            for pad in cls.npth_pads(wizard)
            if pad.GetDrillShape() == pcbnew.PAD_DRILL_SHAPE_CIRCLE
        ]

    @classmethod
    def guide_pin_slots(cls, wizard):
        return [
            pad
            for pad in cls.npth_pads(wizard)
            if pad.GetDrillShape() == pcbnew.PAD_DRILL_SHAPE_OBLONG
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

        holes = self.guide_pin_holes(wizard)
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
            [
                "Package",
                "Mounting layout",
                "Guide pin hole",
                "Guide pin slots",
                "Pads",
                "Center opening",
            ],
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

    def test_corner_depopulation_can_clear_guide_pin_hole(self):
        wizard = self.make_wizard(
            Pads__corner_depopulation_depth=3,
            Mounting_layout__x_distance=9.5,
            Mounting_layout__y_distance=9.5,
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
            Guide_pin_hole__enabled=False,
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

    def test_each_guide_pin_hole_corner(self):
        expected_positions = {
            "top_left_(A1_corner)": (-11.5, -11.5),
            "top_right": (11.5, -11.5),
            "bottom_right": (11.5, 11.5),
            "bottom_left": (-11.5, 11.5),
        }
        for selected, expected in expected_positions.items():
            values = {"Guide_pin_hole__top_left_(A1_corner)": False}
            values["Guide_pin_hole__" + selected] = True
            with self.subTest(corner=selected):
                wizard = self.make_wizard(**values)
                self.assertFalse(wizard.AnyErrors(), wizard.GetBuildMessages())
                hole = self.guide_pin_holes(wizard)[0]
                self.assertEqual(
                    pcbnew.VECTOR2I(
                        pcbnew.FromMM(expected[0]), pcbnew.FromMM(expected[1])
                    ),
                    hole.GetPosition(),
                )

    def test_guide_pin_hole_can_be_the_footprint_origin(self):
        wizard = self.make_wizard(Guide_pin_hole__origin_at_guide_pin_hole=True)
        self.assertFalse(wizard.AnyErrors(), wizard.GetBuildMessages())
        self.assertEqual(
            pcbnew.VECTOR2I(0, 0), self.guide_pin_holes(wizard)[0].GetPosition()
        )
        self.assertEqual(
            pcbnew.VECTOR2I(pcbnew.FromMM(2), pcbnew.FromMM(2)),
            self.pads_by_name(wizard)["A1"].GetPosition(),
        )

    def test_guide_pin_hole_can_be_disabled(self):
        wizard = self.make_wizard(
            Guide_pin_hole__enabled=False,
            Package__width=20,
            Package__length=20,
        )
        self.assertFalse(wizard.AnyErrors(), wizard.GetBuildMessages())
        self.assertEqual([], self.guide_pin_holes(wizard))
        self.assertEqual(336, len(list(wizard.module.Pads())))

    def test_multiple_guide_pin_hole_corners_are_rejected(self):
        wizard = self.make_wizard(Guide_pin_hole__top_right=True)
        self.assertTrue(wizard.AnyErrors())
        self.assertIn("select exactly one corner", wizard.GetBuildMessages())

    def test_guide_pin_hole_must_fit_inside_package(self):
        wizard = self.make_wizard(Package__width=25)
        self.assertTrue(wizard.AnyErrors())
        self.assertIn("guide pin features", wizard.GetBuildMessages())

    def test_guide_pin_hole_must_not_overlap_bga_pad(self):
        wizard = self.make_wizard(
            Mounting_layout__x_distance=9.5,
            Mounting_layout__y_distance=9.5,
        )
        self.assertTrue(wizard.AnyErrors())
        self.assertIn("overlaps BGA pad A1", wizard.GetBuildMessages())

    def test_guide_pin_hole_origin_requires_enabled_hole(self):
        wizard = self.make_wizard(
            Guide_pin_hole__enabled=False,
            Guide_pin_hole__origin_at_guide_pin_hole=True,
        )
        self.assertTrue(wizard.AnyErrors())
        self.assertIn("requires the guide pin hole", wizard.GetBuildMessages())

    def test_guide_pin_slots_are_optional_in_each_corner(self):
        expected_positions = {
            "top_left_(A1_corner)": (-11.5, -11.5),
            "top_right": (11.5, -11.5),
            "bottom_right": (11.5, 11.5),
            "bottom_left": (-11.5, 11.5),
        }
        for selected, expected in expected_positions.items():
            with self.subTest(corner=selected):
                wizard = self.make_wizard(
                    **{
                        "Guide_pin_hole__enabled": False,
                        "Guide_pin_slots__" + selected: True,
                    }
                )
                self.assertFalse(wizard.AnyErrors(), wizard.GetBuildMessages())
                slots = self.guide_pin_slots(wizard)
                self.assertEqual(1, len(slots))
                self.assertEqual(
                    pcbnew.VECTOR2I(
                        pcbnew.FromMM(expected[0]), pcbnew.FromMM(expected[1])
                    ),
                    slots[0].GetPosition(),
                )
                self.assertEqual(
                    pcbnew.VECTOR2I(pcbnew.FromMM(3), pcbnew.FromMM(2.5)),
                    slots[0].GetDrillSize(),
                )
                self.assertEqual(pcbnew.PAD_SHAPE_OVAL, slots[0].GetShape())

    def test_guide_pin_slots_have_independent_rotations(self):
        wizard = self.make_wizard(
            Guide_pin_hole__enabled=False,
            Guide_pin_slots__top_right=True,
            Guide_pin_slots__top_right_rotation=90,
            Guide_pin_slots__bottom_right=True,
            Guide_pin_slots__bottom_right_rotation=30,
        )
        self.assertFalse(wizard.AnyErrors(), wizard.GetBuildMessages())
        rotations_by_y = {
            pcbnew.ToMM(slot.GetPosition().y): slot.GetOrientationDegrees()
            for slot in self.guide_pin_slots(wizard)
        }
        self.assertEqual({-11.5: 90, 11.5: 30}, rotations_by_y)

    def test_guide_pin_slot_round_trips_through_kicad_format(self):
        wizard = self.make_wizard(
            Guide_pin_slots__top_right=True,
            Guide_pin_slots__top_right_rotation=37,
        )
        self.assertFalse(wizard.AnyErrors(), wizard.GetBuildMessages())
        with tempfile.TemporaryDirectory(suffix=".pretty") as library:
            plugin = pcbnew.PCB_IO_KICAD_SEXPR()
            plugin.FootprintSave(library, wizard.module)
            loaded = plugin.FootprintLoad(library, wizard.GetValue())
            self.assertIsNotNone(loaded)
            slots = self.guide_pin_slots(loaded)
            self.assertEqual(1, len(slots))
            self.assertEqual(37, slots[0].GetOrientationDegrees())

    def test_guide_pin_slot_cannot_share_hole_corner(self):
        wizard = self.make_wizard(
            **{"Guide_pin_slots__top_left_(A1_corner)": True}
        )
        self.assertTrue(wizard.AnyErrors())
        self.assertIn("cannot occupy the same corner", wizard.GetBuildMessages())

    def test_guide_pin_slot_length_must_cover_width(self):
        wizard = self.make_wizard(
            Guide_pin_slots__top_right=True,
            Guide_pin_slots__length=2,
        )
        self.assertTrue(wizard.AnyErrors())
        self.assertIn("at least its width", wizard.GetBuildMessages())

    def test_inactive_guide_pin_slot_dimensions_do_not_block_preview(self):
        wizard = self.make_wizard(Guide_pin_slots__length=2)
        self.assertFalse(wizard.AnyErrors(), wizard.GetBuildMessages())

    def test_guide_pin_slot_must_fit_inside_package(self):
        wizard = self.make_wizard(
            Guide_pin_slots__top_right=True,
            Guide_pin_slots__length=4,
        )
        self.assertTrue(wizard.AnyErrors())
        self.assertIn("guide pin features", wizard.GetBuildMessages())

    def test_guide_pin_slot_must_not_overlap_bga_pad(self):
        wizard = self.make_wizard(
            Guide_pin_hole__enabled=False,
            Guide_pin_slots__top_right=True,
            Mounting_layout__x_distance=9.5,
            Mounting_layout__y_distance=9.5,
        )
        self.assertTrue(wizard.AnyErrors())
        self.assertIn("guide pin slot at top right overlaps BGA pad", wizard.GetBuildMessages())

    def test_guide_pin_slots_must_not_overlap_each_other(self):
        wizard = self.make_wizard(
            Guide_pin_hole__enabled=False,
            Guide_pin_slots__top_right=True,
            Guide_pin_slots__bottom_right=True,
            Mounting_layout__x_distance=0.5,
            Mounting_layout__y_distance=0.5,
        )
        self.assertTrue(wizard.AnyErrors())
        self.assertIn("guide pin slots at", wizard.GetBuildMessages())

    def test_crossing_guide_pin_slots_must_not_overlap(self):
        self.assertEqual(
            0,
            BGAInterposerWizard._segments_distance_squared(
                ((-2, -2), (2, 2)), ((-2, 2), (2, -2))
            ),
        )
        self.assertGreater(
            BGAInterposerWizard._segments_distance_squared(
                ((-2, 0), (2, 0)), ((-2, 2), (2, 2))
            ),
            0,
        )

    def test_guide_pin_hole_origin_translates_slots(self):
        wizard = self.make_wizard(
            Guide_pin_hole__origin_at_guide_pin_hole=True,
            Guide_pin_slots__top_right=True,
        )
        self.assertFalse(wizard.AnyErrors(), wizard.GetBuildMessages())
        self.assertEqual(
            pcbnew.VECTOR2I(pcbnew.FromMM(23), 0),
            self.guide_pin_slots(wizard)[0].GetPosition(),
        )

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
