"""Real RF author → saved library artifact → independent native Workflow contract.

The test may import both packages; neither production package imports the other.
The demo sources derive from the supplied drawing and remain unmodified.
"""

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins"))

import pcbnew as p

from gppo_rf_transition.model import (default_recipe, load_artifact as author_load,
                                     make_artifact, publish_bundle)
from gppo_rf_transition.native import read_base_part
from connector_interposer_workflow.artifacts import (ArtifactError, load_artifact,
                                                validate_artifact)
from connector_interposer_workflow.backend import NativeBackend, board_items, uuid_of
from connector_interposer_workflow.core.workbook import parse_sheet, read_workbook

DEMO = ROOT / "examples" / "gppo_demo"
SOURCE = DEMO / "library" / "Example.pretty" / "GPPO_B012_L13_01_EXPERIMENTAL.kicad_mod"
INTERPOSER = SOURCE.with_name("Interposer_800.kicad_mod")
LAYERS = ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]
LAYER_MAP = {"L2": "In1.Cu", "L3": "In2.Cu"}


def demo_artifacts():
    """Produce the actual versioned demo library bundles deterministically.

    This is an isolated test/example fixture authoring operation, never a
    Workflow layout-designer feature. Production artifacts use this same author.
    """
    part = read_base_part(SOURCE, stackup=LAYERS, approved=True)
    result = {}
    for architecture, copper in LAYER_MAP.items():
        recipe = default_recipe(part)
        params = recipe["parameters"]
        params["netclasses"] = ["CLOCK", "DATA"]
        params["signal_layer"] = copper
        params["signal_via"] = None  # The selected source has the real PTH barrel.
        params["tuned_signal_copper"] = {layer: 0.65 if layer == copper else None for layer in LAYERS}
        result[architecture] = make_artifact(
            recipe, f"RF-{architecture}", provenance={
                "operation": "new",
                "purpose": "DEMONSTRATION FIXTURE ONLY — not manufacturing or RF qualified",
                "source": "Supplied OL-B012-L1X-01 rev B drawing; experimental central source",
            })
    return result


class ConnectorInterposerArtifactContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.artifacts = demo_artifacts()
        cls.positions = {row["reference"]: row for row in json.loads((DEMO / "inputs" / "positions-A.json").read_text())}

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.board = self.source / "contract.kicad_pcb"
        board = p.BOARD()
        board.SetCopperLayerCount(4)
        p.SaveBoard(str(self.board), board, True)
        self.board.with_suffix(".kicad_pro").write_text(json.dumps({
            "meta": {"filename": "contract.kicad_pro", "version": 3},
            "net_settings": {
                "meta": {"version": 4},
                "classes": [{"name": "Default", "priority": 2147483647},
                            {"name": "CLOCK", "priority": 0},
                            {"name": "DATA", "priority": 1}],
                "netclass_patterns": [
                    {"netclass": netclass, "pattern": f"{netclass}{index}"}
                    for netclass in ("CLOCK", "DATA") for index in range(1, 7)],
            },
        }))
        self.backend = NativeBackend(p)
        self.source_hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in [SOURCE, INTERPOSER]}

    @staticmethod
    def rows(variant):
        workbook = read_workbook(DEMO / "inputs" / f"netlist-{variant}.xlsx")
        table = parse_sheet(workbook.sheets[0])
        if not table.valid:
            raise AssertionError(table.issues)
        return table.records

    def desired(self, variant):
        rows = self.rows(variant)
        selection = {"path": str(SOURCE), "sha256": self.source_hashes[SOURCE],
                     "approved": True, "library_id": "Example:" + SOURCE.stem}
        instances = {}
        for row in rows:
            pos = self.positions[row.key]
            artifact = deepcopy(self.artifacts[row.layer])
            validate_artifact(artifact, layers=LAYERS,
                              part_sha256=selection["sha256"], netclass=row.netclass)
            instances[row.key] = {
                "gang": row.connector, "kind": "connector", "footprint": deepcopy(selection),
                "pose": [pos["x_mm"], pos["y_mm"], pos["orientation_degrees"]],
                "pads": {"1": row.signal, "2": "GND"},
                "netclass": row.netclass, "signal_layer": LAYER_MAP[row.layer],
                "rf": {"artifact": artifact,
                       "nets": {"signal": row.signal, "ground": "GND"},
                       "clock": 0 if row.connector == "J101" else 180},
            }
        fp = p.FootprintLoad(str(INTERPOSER.parent), INTERPOSER.stem)
        pads = {pad.GetNumber(): "GND" for pad in fp.Pads() if pad.GetNumber()}
        for row in rows:
            pads[row.interposer_pin] = row.signal
        instances["E1"] = {
            "gang": "E1", "kind": "interposer",
            "footprint": {"path": str(INTERPOSER), "sha256": self.source_hashes[INTERPOSER],
                          "approved": True, "library_id": "Example:" + INTERPOSER.stem},
            "pose": [94.95, 94.95, 0], "pads": pads,
        }
        return {"instances": instances}

    def assert_native_contract(self, result, desired):
        board = p.LoadBoard(result["board_path"])
        native = board_items(board)
        self.assertEqual(13, len(list(board.GetFootprints())))
        self.assertEqual(108, len(list(board.GetTracks())))
        self.assertTrue(all(isinstance(item, p.PCB_VIA) for item in board.GetTracks()))
        for key, spec in desired["instances"].items():
            registry = result["registry"]["instances"][key]
            base = native[registry["base"]]
            self.assertEqual(spec["pads"], {pad.GetNumber(): pad.GetNetname() for pad in base.Pads() if pad.GetNumber()})
            if "rf" not in spec:
                continue
            center = next(pad for pad in base.Pads() if pad.GetNumber() == "1")
            self.assertEqual(center.GetAttribute(), p.PAD_ATTRIB_PTH)
            self.assertAlmostEqual(p.ToMM(center.GetDrillSize().x), 0.381)
            artifact = spec["rf"]["artifact"]
            self.assertEqual(len(artifact["payload"]["objects"]), len(registry["objects"]))
            for obj in artifact["payload"]["objects"]:
                actual = native[registry["objects"][obj["id"]]]
                self.assertEqual(uuid_of(actual.GetParentGroup()), registry["group"])
                if obj["type"] == "via":
                    self.assertEqual(actual.GetNetname(), "GND")
                    if not hasattr(p, "VIATYPE_THROUGH"):
                        self.assertEqual(p.Version(), "10.0.0")
                    self.assertEqual(actual.GetViaType(), getattr(p, "VIATYPE_THROUGH", 4))
                    self.assertEqual([actual.TopLayer(), actual.BottomLayer()], [p.F_Cu, p.B_Cu])
                    angle = math.radians(spec["rf"]["clock"])
                    x, y = obj["at"]
                    expected = [round((spec["pose"][0] + x * math.cos(angle) - y * math.sin(angle)) * 1e6),
                                round((spec["pose"][1] - x * math.sin(angle) - y * math.cos(angle)) * 1e6)]
                    self.assertEqual([actual.GetPosition().x, actual.GetPosition().y], expected)
                    self.assertEqual(actual.GetDrillValue(), p.FromMM(obj["drill"]))
                elif obj["type"] == "copper":
                    self.assertEqual(actual.GetNetname(), spec["pads"]["1"])
                    self.assertEqual(actual.GetLayer(), board.GetLayerID(spec["signal_layer"]))
                    self.assertAlmostEqual(actual.GetRadius() / 1e6, obj["diameter"] / 2, places=6)
                else:
                    self.assertTrue(actual.GetIsRuleArea())
                    self.assertFalse(actual.GetDoNotAllowTracks())
                    self.assertFalse(actual.GetDoNotAllowVias())
                    self.assertEqual(actual.Outline().COutline(0).PointCount(), len(obj["points"]))
        for path, expected in self.source_hashes.items():
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), expected)
        return board

    def test_actual_author_artifacts_validate_save_reopen_and_consume(self):
        for architecture, artifact in self.artifacts.items():
            shipped = DEMO / "library" / "rf" / f"RF-{architecture}.r1.gppo-rf.json"
            self.assertEqual(author_load(shipped, reopen=True), artifact)
            self.assertEqual(artifact["compatibility"]["mount"], "PTH")
            self.assertEqual(artifact["compatibility"]["part_id"], "Example:" + SOURCE.stem)
            self.assertIsNone(artifact["recipe"]["parameters"]["signal_via"])
            self.assertFalse(any(item["type"] == "via" and item["net"] == "signal" for item in artifact["payload"]["objects"]))
            path = publish_bundle(self.root / "rf-library", artifact)
            self.assertEqual(author_load(path, reopen=True), artifact)
            for netclass in ["CLOCK", "DATA"]:
                self.assertEqual(load_artifact(path, layers=LAYERS, part_sha256=self.source_hashes[SOURCE], netclass=netclass), artifact)
        desired = self.desired("A")
        result = self.backend.apply(self.backend.preview(self.board, desired), self.root / "A")
        self.assert_native_contract(result, desired)

    def test_same_pins_support_mixed_class_a_to_b_and_explicit_layer_override(self):
        desired_a = self.desired("A")
        first = self.backend.apply(self.backend.preview(self.board, desired_a), self.root / "A")
        desired_b = self.desired("B")
        # A and B use the same physical keys/route layers while swapping
        # CLOCK/DATA assignments. Class never determines the routing layer.
        self.assertEqual({row.netclass for row in self.rows("A")}, {"CLOCK", "DATA"})
        self.assertEqual({desired_b["instances"][f"J101-{i}"]["signal_layer"] for i in range(1, 7)}, {"In1.Cu", "In2.Cu"})
        second = self.backend.apply(self.backend.preview(first["board_path"], desired_b), self.root / "B")
        self.assert_native_contract(second, desired_b)
        for key in desired_a["instances"]:
            self.assertEqual(first["registry"]["instances"][key]["base"], second["registry"]["instances"][key]["base"])
        prior = second["registry"]["instances"]["J101-1"]
        override = deepcopy(desired_b)
        instance = override["instances"]["J101-1"]
        instance["signal_layer"] = "In2.Cu"
        instance["rf"]["artifact"] = deepcopy(self.artifacts["L3"])
        third = self.backend.apply(self.backend.preview(second["board_path"], override), self.root / "override")
        self.assert_native_contract(third, override)
        self.assertEqual(prior["base"], third["registry"]["instances"]["J101-1"]["base"])
        output = Path(third["board_path"])
        before = (output.read_bytes(), output.stat().st_mtime_ns)
        noop = self.backend.preview(output, override)
        self.assertEqual(noop["status"], "NOOP")
        self.assertEqual(self.backend.apply(noop, self.root / "unused")["status"], "NOOP")
        self.assertEqual((output.read_bytes(), output.stat().st_mtime_ns), before)

    def test_wrong_part_hash_layer_and_class_block_before_mutation(self):
        artifact = self.artifacts["L2"]
        with self.assertRaises(ArtifactError):
            validate_artifact(artifact, layers=LAYERS, part_sha256="f" * 64)
        with self.assertRaises(ArtifactError):
            validate_artifact(artifact, layers=LAYERS, netclass="Control")
        desired = self.desired("A")
        desired["instances"]["J101-1"]["signal_layer"] = "In2.Cu"
        before = self.board.read_bytes()
        with self.assertRaisesRegex(ValueError, "signal layer"):
            self.backend.preview(self.board, desired)
        self.assertEqual(self.board.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
