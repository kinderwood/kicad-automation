"""Integration tests executed with KiCad's bundled Python interpreter."""

import math
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


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

    @staticmethod
    def shapes_on_layer(wizard_or_footprint, layer):
        footprint = getattr(wizard_or_footprint, "module", wizard_or_footprint)
        return [
            item
            for item in footprint.GraphicalItems()
            if isinstance(item, pcbnew.PCB_SHAPE) and item.GetLayer() == layer
        ]

    def assert_mask_only_npth(self, pad):
        self.assertFalse(pad.IsOnLayer(pcbnew.F_Cu))
        self.assertFalse(pad.IsOnLayer(pcbnew.B_Cu))
        self.assertTrue(pad.IsOnLayer(pcbnew.F_Mask))
        self.assertTrue(pad.IsOnLayer(pcbnew.B_Mask))

    def test_default_20_by_20_with_8_by_8_center_depopulation(self):
        wizard = self.make_wizard()
        self.assertFalse(wizard.AnyErrors(), wizard.GetBuildMessages())
        pads = self.pads_by_name(wizard)

        self.assertEqual(336, len(pads))
        self.assertEqual(
            "BGA-Interposer-336_20x20_CenterDepopulation8x8_26x26mm_P1mm",
            wizard.GetValue(),
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
        self.assert_mask_only_npth(holes[0])
        self.assertEqual(337, len(list(wizard.module.Pads())))
        self.assertEqual("mils", wizard.GetParam("Pads", "size").units)
        self.assertEqual(
            [("Pads", "size")],
            [
                (parameter.page, parameter.name)
                for parameter in wizard.params
                if parameter.units == pcbnew.uMils
            ],
        )
        self.assertEqual(pcbnew.FromMils(20), pads["A1"].GetSize().x)
        self.assertTrue(wizard.parameters["Guide pin hole"]["enabled"])
        self.assertTrue(
            wizard.parameters["Guide pin hole"]["top left (A1 corner)"]
        )
        center_cutout = wizard.parameters["Center board cutout"]
        self.assertFalse(center_cutout["enabled"])
        self.assertEqual(pcbnew.FromMM(5), center_cutout["width"])
        self.assertEqual(pcbnew.FromMM(5), center_cutout["length"])
        self.assertEqual(0, center_cutout["corner radius"])
        self.assertEqual([], self.shapes_on_layer(wizard, pcbnew.Edge_Cuts))

    def test_parameter_pages_follow_drawing_entry_order(self):
        wizard = BGAInterposerWizard()
        self.assertEqual(
            [
                "Package",
                "Guide feat placement",
                "Guide pin hole",
                "Guide pin slots",
                "Pads",
                "Center pad depop",
                "Center board cutout",
                "Silkscreen",
            ],
            [
                wizard.GetParameterPageName(index)
                for index in range(wizard.GetNumParameterPages())
            ],
        )
        expected_parameters = {
            "Package": [
                "width",
                "length",
                "chamfer size",
                "chamfer top left (A1)",
                "chamfer top right",
                "chamfer bottom right",
                "chamfer bottom left",
                "courtyard margin",
            ],
            "Guide feat placement": ["x offset", "y offset"],
            "Guide pin hole": [
                "enabled",
                "diameter",
                "top left (A1 corner)",
                "top right",
                "bottom right",
                "bottom left",
                "origin at guide pin hole",
            ],
            "Guide pin slots": [
                "width",
                "length",
                "top left (A1 corner)",
                "top left (A1 corner) rotation",
                "top right",
                "top right rotation",
                "bottom right",
                "bottom right rotation",
                "bottom left",
                "bottom left rotation",
            ],
            "Pads": ["pitch", "size", "corner depop depth", "columns", "rows"],
            "Center pad depop": ["columns", "rows"],
            "Center board cutout": [
                "enabled",
                "width",
                "length",
                "corner radius",
            ],
            "Silkscreen": [
                "corners",
                "corner offset",
                "pin 1 dot",
                "pin 1 dot offset",
                "pin 1 dot diameter",
                "ref des",
            ],
        }
        for page, names in expected_parameters.items():
            self.assertEqual(
                names,
                [
                    parameter.name
                    for parameter in wizard.params
                    if parameter.page == page
                ],
            )
        self.assertIsNotNone(wizard.GetParam("Pads", "corner depop depth"))
        self.assertIsNone(wizard.GetParam("Pads", "corner depopulation depth"))
        self.assertIsNone(wizard.GetParam("Package", "margin"))

    def test_silkscreen_defaults_match_existing_style(self):
        wizard = self.make_wizard()
        self.assertFalse(wizard.AnyErrors(), wizard.GetBuildMessages())
        package = wizard.parameters["Package"]
        silkscreen = wizard.parameters["Silkscreen"]
        self.assertEqual(pcbnew.FromMM(1), package["chamfer size"])
        self.assertTrue(package["chamfer top left (A1)"])
        self.assertFalse(package["chamfer top right"])
        self.assertFalse(package["chamfer bottom right"])
        self.assertFalse(package["chamfer bottom left"])
        self.assertTrue(silkscreen["corners"])
        self.assertEqual(pcbnew.FromMM(0.12), silkscreen["corner offset"])
        self.assertTrue(silkscreen["pin 1 dot"])
        self.assertEqual(pcbnew.FromMM(0.7), silkscreen["pin 1 dot offset"])
        self.assertEqual(pcbnew.FromMM(0.4), silkscreen["pin 1 dot diameter"])
        self.assertTrue(silkscreen["ref des"])
        self.assertEqual(5, len(self.shapes_on_layer(wizard, pcbnew.F_Fab)))
        self.assertEqual(5, len(self.shapes_on_layer(wizard, pcbnew.F_CrtYd)))

        silk_shapes = self.shapes_on_layer(wizard, pcbnew.F_SilkS)
        self.assertEqual(10, len(silk_shapes))
        self.assertEqual(
            1,
            sum(shape.GetShape() == pcbnew.SHAPE_T_CIRCLE for shape in silk_shapes),
        )
        pin_1_dot = next(
            shape
            for shape in silk_shapes
            if shape.GetShape() == pcbnew.SHAPE_T_CIRCLE
        )
        expected_center = pcbnew.FromMM(-12.5 - 0.7 / math.sqrt(2))
        self.assertAlmostEqual(expected_center, pin_1_dot.GetCenter().x, delta=1)
        self.assertAlmostEqual(expected_center, pin_1_dot.GetCenter().y, delta=1)
        self.assertEqual(pcbnew.FromMM(0.4), pin_1_dot.GetBoundingBox().GetWidth())

    def test_pin_1_dot_geometry_is_configurable(self):
        chamfered = self.make_wizard(
            Silkscreen__pin_1_dot_offset=0,
            Silkscreen__pin_1_dot_diameter=1,
        )
        self.assertFalse(chamfered.AnyErrors(), chamfered.GetBuildMessages())
        chamfered_dot = next(
            shape
            for shape in self.shapes_on_layer(chamfered, pcbnew.F_SilkS)
            if shape.GetShape() == pcbnew.SHAPE_T_CIRCLE
        )
        self.assertEqual(pcbnew.FromMM(-12.5), chamfered_dot.GetCenter().x)
        self.assertEqual(pcbnew.FromMM(-12.5), chamfered_dot.GetCenter().y)
        self.assertEqual(
            pcbnew.FromMM(1), chamfered_dot.GetBoundingBox().GetWidth()
        )

        square_corner = self.make_wizard(
            **{
                "Package__chamfer_top_left_(A1)": False,
                "Silkscreen__pin_1_dot_offset": 0,
            }
        )
        self.assertFalse(
            square_corner.AnyErrors(), square_corner.GetBuildMessages()
        )
        square_corner_dot = next(
            shape
            for shape in self.shapes_on_layer(square_corner, pcbnew.F_SilkS)
            if shape.GetShape() == pcbnew.SHAPE_T_CIRCLE
        )
        self.assertEqual(pcbnew.FromMM(-13), square_corner_dot.GetCenter().x)
        self.assertEqual(pcbnew.FromMM(-13), square_corner_dot.GetCenter().y)

    def test_graphic_line_widths_follow_active_editor_defaults(self):
        widths = {
            pcbnew.F_SilkS: pcbnew.FromMM(0.11),
            pcbnew.F_Fab: pcbnew.FromMM(0.13),
            pcbnew.F_CrtYd: pcbnew.FromMM(0.07),
            pcbnew.Edge_Cuts: pcbnew.FromMM(0.09),
        }
        settings = mock.Mock()
        settings.GetLineThickness.side_effect = widths.__getitem__
        board = mock.Mock()
        board.GetDesignSettings.return_value = settings

        with mock.patch.object(pcbnew, "GetBoard", return_value=board):
            wizard = self.make_wizard(Center_board_cutout__enabled=True)

        self.assertFalse(wizard.AnyErrors(), wizard.GetBuildMessages())
        for layer in (pcbnew.F_Fab, pcbnew.F_CrtYd, pcbnew.Edge_Cuts):
            shapes = self.shapes_on_layer(wizard, layer)
            self.assertTrue(shapes)
            self.assertTrue(
                all(shape.GetWidth() == widths[layer] for shape in shapes)
            )

        silk_lines = [
            shape
            for shape in self.shapes_on_layer(wizard, pcbnew.F_SilkS)
            if shape.GetShape() != pcbnew.SHAPE_T_CIRCLE
        ]
        self.assertTrue(silk_lines)
        self.assertTrue(
            all(shape.GetWidth() == widths[pcbnew.F_SilkS] for shape in silk_lines)
        )

    def test_all_or_no_package_corners_can_be_chamfered(self):
        all_chamfered = self.make_wizard(
            Package__chamfer_top_right=True,
            Package__chamfer_bottom_right=True,
            Package__chamfer_bottom_left=True,
        )
        self.assertFalse(
            all_chamfered.AnyErrors(), all_chamfered.GetBuildMessages()
        )
        self.assertEqual(
            8, len(self.shapes_on_layer(all_chamfered, pcbnew.F_Fab))
        )
        self.assertEqual(
            8, len(self.shapes_on_layer(all_chamfered, pcbnew.F_CrtYd))
        )
        self.assertEqual(
            13, len(self.shapes_on_layer(all_chamfered, pcbnew.F_SilkS))
        )

        no_chamfers = self.make_wizard(
            **{"Package__chamfer_top_left_(A1)": False}
        )
        self.assertFalse(no_chamfers.AnyErrors(), no_chamfers.GetBuildMessages())
        self.assertEqual(4, len(self.shapes_on_layer(no_chamfers, pcbnew.F_Fab)))
        self.assertEqual(
            4, len(self.shapes_on_layer(no_chamfers, pcbnew.F_CrtYd))
        )
        self.assertEqual(9, len(self.shapes_on_layer(no_chamfers, pcbnew.F_SilkS)))

    def test_package_chamfers_must_fit_on_each_edge(self):
        wizard = self.make_wizard(
            Package__chamfer_top_right=True,
            Package__chamfer_size=14,
        )
        self.assertTrue(wizard.AnyErrors())
        self.assertIn("fit on every package edge", wizard.GetBuildMessages())

    def test_silkscreen_corners_and_pin_1_dot_toggle_independently(self):
        corners_off = self.make_wizard(Silkscreen__corners=False)
        self.assertFalse(corners_off.AnyErrors(), corners_off.GetBuildMessages())
        silk_shapes = self.shapes_on_layer(corners_off, pcbnew.F_SilkS)
        self.assertEqual(1, len(silk_shapes))
        self.assertEqual(pcbnew.SHAPE_T_CIRCLE, silk_shapes[0].GetShape())

        dot_off = self.make_wizard(Silkscreen__pin_1_dot=False)
        self.assertFalse(dot_off.AnyErrors(), dot_off.GetBuildMessages())
        silk_shapes = self.shapes_on_layer(dot_off, pcbnew.F_SilkS)
        self.assertEqual(9, len(silk_shapes))
        self.assertFalse(
            any(shape.GetShape() == pcbnew.SHAPE_T_CIRCLE for shape in silk_shapes)
        )

        both_off = self.make_wizard(
            Silkscreen__corners=False,
            Silkscreen__pin_1_dot=False,
        )
        self.assertFalse(both_off.AnyErrors(), both_off.GetBuildMessages())
        self.assertEqual([], self.shapes_on_layer(both_off, pcbnew.F_SilkS))

    def test_silkscreen_can_be_completely_disabled(self):
        wizard = self.make_wizard(
            Silkscreen__corners=False,
            Silkscreen__pin_1_dot=False,
            Silkscreen__ref_des=False,
        )
        self.assertFalse(wizard.AnyErrors(), wizard.GetBuildMessages())
        self.assertEqual([], self.shapes_on_layer(wizard, pcbnew.F_SilkS))
        self.assertFalse(wizard.module.Reference().IsVisible())

    def test_courtyard_chamfers_preserve_clearance(self):
        wizard = self.make_wizard()
        self.assertFalse(wizard.AnyErrors(), wizard.GetBuildMessages())
        diagonal = [
            shape
            for shape in self.shapes_on_layer(wizard, pcbnew.F_CrtYd)
            if abs(shape.GetEnd().x - shape.GetStart().x)
            == abs(shape.GetEnd().y - shape.GetStart().y)
        ]
        self.assertEqual(1, len(diagonal))

        package_edge_sum = -pcbnew.FromMM(26 - 1)
        courtyard_edge_sum = diagonal[0].GetStart().x + diagonal[0].GetStart().y
        clearance = abs(courtyard_edge_sum - package_edge_sum) / math.sqrt(2)
        self.assertAlmostEqual(pcbnew.FromMM(0.25), clearance, delta=1)

    def test_silkscreen_corner_offset_is_configurable(self):
        wizard = self.make_wizard(Silkscreen__corner_offset=1)
        self.assertFalse(wizard.AnyErrors(), wizard.GetBuildMessages())
        segments = [
            shape
            for shape in self.shapes_on_layer(wizard, pcbnew.F_SilkS)
            if shape.GetShape() == pcbnew.SHAPE_T_SEGMENT
        ]
        points = [
            point
            for segment in segments
            for point in (segment.GetStart(), segment.GetEnd())
        ]
        self.assertEqual(pcbnew.FromMM(-14), min(point.x for point in points))
        self.assertEqual(pcbnew.FromMM(14), max(point.x for point in points))
        self.assertEqual(pcbnew.FromMM(-14), min(point.y for point in points))
        self.assertEqual(pcbnew.FromMM(14), max(point.y for point in points))

    def test_pad_size_cannot_exceed_pitch(self):
        near_tangent = self.make_wizard(Pads__size=39)
        self.assertFalse(near_tangent.AnyErrors(), near_tangent.GetBuildMessages())

        tangent = self.make_wizard(Pads__size=pcbnew.ToMils(pcbnew.FromMM(1)))
        self.assertFalse(tangent.AnyErrors(), tangent.GetBuildMessages())

        overlapping = self.make_wizard(Pads__size=40)
        self.assertTrue(overlapping.AnyErrors())
        self.assertIn("diameter cannot exceed", overlapping.GetBuildMessages())

    def test_symmetric_corner_depopulation_depth_four(self):
        wizard = self.make_wizard(Pads__corner_depop_depth=4)
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
            "BGA-Interposer-296_20x20_CenterDepopulation8x8_26x26mm_P1mm",
            wizard.GetValue(),
        )

    def test_corner_depopulation_depth_is_bounded(self):
        wizard = self.make_wizard(Pads__corner_depop_depth=10)
        self.assertTrue(wizard.AnyErrors())
        self.assertIn("opposing corner cuts", wizard.GetBuildMessages())

    def test_corner_depopulation_can_clear_guide_pin_hole(self):
        wizard = self.make_wizard(
            Pads__corner_depop_depth=3,
            Guide_feat_placement__x_offset=9.5,
            Guide_feat_placement__y_offset=9.5,
            Guide_pin_hole__enabled=True,
            **{"Guide_pin_hole__top_left_(A1_corner)": True},
        )
        self.assertFalse(wizard.AnyErrors(), wizard.GetBuildMessages())
        self.assertNotIn("A1", self.pads_by_name(wizard))

    def test_rectangular_array_and_center_depopulation(self):
        wizard = self.make_wizard(
            Pads__columns=18,
            Pads__rows=14,
            Center_pad_depop__columns=6,
            Center_pad_depop__rows=4,
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
            values = {
                "Guide_pin_hole__enabled": True,
                "Guide_pin_hole__top_left_(A1_corner)": False,
            }
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
        wizard = self.make_wizard(
            Guide_pin_hole__enabled=True,
            Guide_pin_hole__origin_at_guide_pin_hole=True,
            **{"Guide_pin_hole__top_left_(A1_corner)": True},
        )
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
        wizard = self.make_wizard(
            Guide_pin_hole__enabled=True,
            Guide_pin_hole__top_right=True,
            **{"Guide_pin_hole__top_left_(A1_corner)": True},
        )
        self.assertTrue(wizard.AnyErrors())
        self.assertIn("select exactly one corner", wizard.GetBuildMessages())

    def test_guide_pin_hole_must_fit_inside_package(self):
        wizard = self.make_wizard(
            Package__width=25,
            Guide_pin_hole__enabled=True,
            **{"Guide_pin_hole__top_left_(A1_corner)": True},
        )
        self.assertTrue(wizard.AnyErrors())
        self.assertIn("guide pin features", wizard.GetBuildMessages())

    def test_guide_pin_hole_must_not_overlap_bga_pad(self):
        wizard = self.make_wizard(
            Guide_feat_placement__x_offset=9.5,
            Guide_feat_placement__y_offset=9.5,
            Guide_pin_hole__enabled=True,
            **{"Guide_pin_hole__top_left_(A1_corner)": True},
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
                self.assert_mask_only_npth(slots[0])

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
            Guide_pin_hole__enabled=True,
            **{
                "Guide_pin_hole__top_left_(A1_corner)": True,
                "Guide_pin_slots__top_left_(A1_corner)": True,
            },
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
            Guide_feat_placement__x_offset=9.5,
            Guide_feat_placement__y_offset=9.5,
        )
        self.assertTrue(wizard.AnyErrors())
        self.assertIn("guide pin slot at top right overlaps BGA pad", wizard.GetBuildMessages())

    def test_guide_pin_slots_must_not_overlap_each_other(self):
        wizard = self.make_wizard(
            Guide_pin_hole__enabled=False,
            Guide_pin_slots__top_right=True,
            Guide_pin_slots__bottom_right=True,
            Guide_feat_placement__x_offset=0.5,
            Guide_feat_placement__y_offset=0.5,
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
            Guide_pin_hole__enabled=True,
            Guide_pin_hole__origin_at_guide_pin_hole=True,
            Guide_pin_slots__top_right=True,
            **{"Guide_pin_hole__top_left_(A1_corner)": True},
        )
        self.assertFalse(wizard.AnyErrors(), wizard.GetBuildMessages())
        self.assertEqual(
            pcbnew.VECTOR2I(pcbnew.FromMM(23), 0),
            self.guide_pin_slots(wizard)[0].GetPosition(),
        )

    def test_center_board_cutout_is_one_rectangle_on_each_layer(self):
        wizard = self.make_wizard(Center_board_cutout__enabled=True)
        self.assertFalse(wizard.AnyErrors(), wizard.GetBuildMessages())

        edge_shapes = self.shapes_on_layer(wizard, pcbnew.Edge_Cuts)
        self.assertEqual(1, len(edge_shapes))
        self.assertEqual(pcbnew.SHAPE_T_RECT, edge_shapes[0].GetShape())
        self.assertEqual(pcbnew.FromMM(0.05), edge_shapes[0].GetWidth())
        self.assertEqual(
            pcbnew.VECTOR2I(pcbnew.FromMM(-2.5), pcbnew.FromMM(-2.5)),
            edge_shapes[0].GetStart(),
        )
        self.assertEqual(
            pcbnew.VECTOR2I(pcbnew.FromMM(2.5), pcbnew.FromMM(2.5)),
            edge_shapes[0].GetEnd(),
        )

        fab_rectangles = [
            shape
            for shape in self.shapes_on_layer(wizard, pcbnew.F_Fab)
            if shape.GetShape() == pcbnew.SHAPE_T_RECT
        ]
        self.assertEqual(1, len(fab_rectangles))
        self.assertEqual(pcbnew.FromMM(0.1), fab_rectangles[0].GetWidth())
        self.assertEqual(edge_shapes[0].GetStart(), fab_rectangles[0].GetStart())
        self.assertEqual(edge_shapes[0].GetEnd(), fab_rectangles[0].GetEnd())

    def test_center_board_cutout_supports_corner_radius(self):
        wizard = self.make_wizard(
            Center_board_cutout__enabled=True,
            Center_board_cutout__width=6,
            Center_board_cutout__length=4,
            Center_board_cutout__corner_radius=1,
        )
        self.assertFalse(wizard.AnyErrors(), wizard.GetBuildMessages())
        edge_shapes = self.shapes_on_layer(wizard, pcbnew.Edge_Cuts)
        fab_shapes = self.shapes_on_layer(wizard, pcbnew.F_Fab)

        if hasattr(pcbnew.PCB_SHAPE, "SetCornerRadius"):
            self.assertEqual(1, len(edge_shapes))
            self.assertEqual(pcbnew.SHAPE_T_RECT, edge_shapes[0].GetShape())
            self.assertEqual(pcbnew.FromMM(1), edge_shapes[0].GetCornerRadius())
            fab_rounded_rectangles = [
                shape
                for shape in fab_shapes
                if shape.GetShape() == pcbnew.SHAPE_T_RECT
            ]
            self.assertEqual(1, len(fab_rounded_rectangles))
            self.assertEqual(
                pcbnew.FromMM(1), fab_rounded_rectangles[0].GetCornerRadius()
            )
        else:
            self.assertEqual(8, len(edge_shapes))
            self.assertEqual(
                4,
                sum(shape.GetShape() == pcbnew.SHAPE_T_SEGMENT for shape in edge_shapes),
            )
            self.assertEqual(
                4,
                sum(shape.GetShape() == pcbnew.SHAPE_T_ARC for shape in edge_shapes),
            )
            self.assertEqual(
                4,
                sum(shape.GetShape() == pcbnew.SHAPE_T_ARC for shape in fab_shapes),
            )

    def test_center_board_cutout_line_and_arc_fallback(self):
        with mock.patch.object(
            BGAInterposerWizard,
            "_supports_native_rounded_rectangle",
            return_value=False,
        ):
            wizard = self.make_wizard(
                Center_board_cutout__enabled=True,
                Center_board_cutout__width=6,
                Center_board_cutout__length=4,
                Center_board_cutout__corner_radius=1,
            )
        self.assertFalse(wizard.AnyErrors(), wizard.GetBuildMessages())
        edge_shapes = self.shapes_on_layer(wizard, pcbnew.Edge_Cuts)
        self.assertEqual(8, len(edge_shapes))
        self.assertEqual(
            4,
            sum(shape.GetShape() == pcbnew.SHAPE_T_SEGMENT for shape in edge_shapes),
        )
        self.assertEqual(
            4,
            sum(shape.GetShape() == pcbnew.SHAPE_T_ARC for shape in edge_shapes),
        )
        self.assertEqual(
            4,
            sum(
                shape.GetShape() == pcbnew.SHAPE_T_ARC
                for shape in self.shapes_on_layer(wizard, pcbnew.F_Fab)
            ),
        )

    def test_center_board_cutout_must_fit_inside_package(self):
        wizard = self.make_wizard(
            Center_board_cutout__enabled=True,
            Center_board_cutout__width=26,
        )
        self.assertTrue(wizard.AnyErrors())
        self.assertIn("smaller than the package width", wizard.GetBuildMessages())

    def test_center_board_cutout_radius_is_bounded(self):
        wizard = self.make_wizard(
            Center_board_cutout__enabled=True,
            Center_board_cutout__width=6,
            Center_board_cutout__length=4,
            Center_board_cutout__corner_radius=2.1,
        )
        self.assertTrue(wizard.AnyErrors())
        self.assertIn("half the shorter dimension", wizard.GetBuildMessages())

    def test_center_board_cutout_must_not_overlap_retained_pad(self):
        wizard = self.make_wizard(
            Center_board_cutout__enabled=True,
            Center_pad_depop__columns=0,
            Center_pad_depop__rows=0,
        )
        self.assertTrue(wizard.AnyErrors())
        self.assertIn("center board cutout overlaps BGA pad", wizard.GetBuildMessages())

    def test_guide_pin_hole_origin_translates_center_board_cutout(self):
        wizard = self.make_wizard(
            Center_board_cutout__enabled=True,
            Guide_pin_hole__enabled=True,
            Guide_pin_hole__origin_at_guide_pin_hole=True,
            **{"Guide_pin_hole__top_left_(A1_corner)": True},
        )
        self.assertFalse(wizard.AnyErrors(), wizard.GetBuildMessages())
        rectangle = self.shapes_on_layer(wizard, pcbnew.Edge_Cuts)[0]
        self.assertEqual(
            pcbnew.VECTOR2I(pcbnew.FromMM(9), pcbnew.FromMM(9)),
            rectangle.GetStart(),
        )
        self.assertEqual(
            pcbnew.VECTOR2I(pcbnew.FromMM(14), pcbnew.FromMM(14)),
            rectangle.GetEnd(),
        )

    def test_center_board_cutout_round_trips_through_kicad_format(self):
        wizard = self.make_wizard(
            Center_board_cutout__enabled=True,
            Center_board_cutout__corner_radius=1,
        )
        self.assertFalse(wizard.AnyErrors(), wizard.GetBuildMessages())
        with tempfile.TemporaryDirectory(suffix=".pretty") as library:
            plugin = pcbnew.PCB_IO_KICAD_SEXPR()
            plugin.FootprintSave(library, wizard.module)
            loaded = plugin.FootprintLoad(library, wizard.GetValue())
            self.assertIsNotNone(loaded)
            self.assertEqual(
                len(self.shapes_on_layer(wizard, pcbnew.Edge_Cuts)),
                len(self.shapes_on_layer(loaded, pcbnew.Edge_Cuts)),
            )

    def test_parity_mismatch_is_rejected(self):
        wizard = self.make_wizard(Center_pad_depop__columns=7)
        self.assertTrue(wizard.AnyErrors())
        self.assertIn("matching parity", wizard.GetBuildMessages())

    def test_one_zero_center_depopulation_dimension_is_rejected(self):
        wizard = self.make_wizard(Center_pad_depop__rows=0)
        self.assertTrue(wizard.AnyErrors())
        self.assertIn("both center depop dimensions", wizard.GetBuildMessages())


if __name__ == "__main__":
    unittest.main()
