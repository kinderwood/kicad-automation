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
        return {str(pad.GetName()): pad for pad in wizard.module.Pads()}

    def test_default_20_by_20_with_8_by_8_opening(self):
        wizard = self.make_wizard()
        self.assertFalse(wizard.AnyErrors(), wizard.GetBuildMessages())
        pads = self.pads_by_name(wizard)

        self.assertEqual(336, len(pads))
        self.assertEqual(
            "BGA-Interposer-336_20x20_Opening8x8_20x20mm_P1mm", wizard.GetValue()
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

    def test_rectangular_array_and_opening(self):
        wizard = self.make_wizard(
            Pads__columns=18,
            Pads__rows=14,
            Center_opening__columns=6,
            Center_opening__rows=4,
            Package__width=18,
            Package__length=14,
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
            self.assertEqual(336, len(list(loaded.Pads())))

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
