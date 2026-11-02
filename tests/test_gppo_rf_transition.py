"""Independent librarian RF authoring and native source-read integration tests."""

from copy import deepcopy
import ast
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins"))

from gppo_rf_transition import model as rf

try:
    import pcbnew
except ModuleNotFoundError:
    pcbnew = None


def smd_part():
    return {"part_id": "Approved:GPPO", "sha256": "a" * 64,
            "approval": "librarian-approved", "mount": "SMD", "side": "front",
            "inspection_stackup": ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"],
            "signal_pad": "1", "shield_pads": ["2"], "signal_at": [0, 0],
            "signal_shape": "circle", "minimum_signal_copper": {"F.Cu": 0.45},
            "shield_geometry": []}


def rehash(artifact):
    artifact.pop("hashes", None)
    artifact["hashes"] = {"payload": rf.digest(artifact["payload"]),
                          "recipe": rf.digest(artifact["recipe"]),
                          "artifact": rf.digest(artifact)}
    return artifact


class RFRecipeTests(unittest.TestCase):
    def recipe(self):
        return rf.default_recipe(smd_part())

    def test_cross_zero_endpoints_and_full_circle_exact_count(self):
        self.assertEqual(rf.arc_angles(310, 50, 5), [310, 335, 0, 25, 50])
        self.assertEqual(rf.arc_angles(0, 360, 4), [0, 90, 180, 270])
        self.assertEqual(rf.arc_angles(15, -345, 4), [15, 105, 195, 285])
        for start, end, count in [(0, 0, 4), (0, 720, 4), (0, 10, 1), (0, 10, True)]:
            with self.assertRaises(rf.RFError):
                rf.arc_angles(start, end, count)

    def test_deterministic_exact_saved_payload_roles_and_no_tracks(self):
        recipe = self.recipe()
        a = rf.make_artifact(recipe, "test-rf")
        b = rf.make_artifact(recipe, "test-rf")
        self.assertEqual(rf.canonical(a), rf.canonical(b))
        self.assertEqual(len([o for o in a["payload"]["objects"] if o["type"] == "via" and o["net"] == "ground"]), 9)
        self.assertEqual({o["type"] for o in a["payload"]["objects"]}, {"via", "copper", "keepout"})
        self.assertEqual(a["payload"]["layer_roles"]["signal"], "In2.Cu")
        self.assertEqual(a["hashes"]["payload"], hashlib.sha256(rf.canonical(a["payload"])).hexdigest())
        rf.validate_artifact(a, reopen=True)

    def test_tangency_allowed_and_true_intrusion_blocked(self):
        corridor = [0, 3, 0.4]
        self.assertFalse(rf.circle_intrudes_corridor([2, 0.7], 0.3, corridor))
        self.assertTrue(rf.circle_intrudes_corridor([2, 0.699], 0.3, corridor))
        self.assertFalse(rf.circle_intrudes_corridor([3.3, 0], 0.3, corridor))
        recipe = self.recipe()
        recipe["parameters"]["ground_ring"].update(start_deg=310, end_deg=50, count=5)
        with self.assertRaisesRegex(rf.RFError, "intrudes"):
            rf.generate_payload(recipe)

    def test_arbitrary_depth_signal_microvia_stack(self):
        recipe = self.recipe()
        layers = ["F.Cu", *[f"In{i}.Cu" for i in range(1, 7)], "B.Cu"]
        params = recipe["parameters"]
        params["stackup"] = layers
        recipe["dependencies"]["base_part"]["inspection_stackup"] = layers
        params["signal_layer"] = "In5.Cu"
        params["signal_via"]["spans"] = [[a, b] for a, b in zip(layers[:5], layers[1:6])]
        params["tuned_signal_copper"] = {layer: None for layer in layers}
        params["antipads"] = {layer: 1.1 for layer in layers}
        objects = rf.generate_payload(recipe)["objects"]
        signal = [o for o in objects if o["net"] == "signal" and o["type"] == "via"]
        self.assertEqual(len(signal), 5)
        self.assertEqual(signal[-1]["layers"], ["In4.Cu", "In5.Cu"])
        params["signal_via"]["spans"][2] = ["In2.Cu", "In4.Cu"]
        with self.assertRaisesRegex(rf.RFError, "adjacent"):
            rf.generate_payload(recipe)

    def test_straight_l3_m6_escape_omits_crossed_l3_segments(self):
        layers = ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]
        # M5…M1 are crossed by the same +X corridor. Only L1–L2
        # segments remain; L2–L3 and L3–L4 must be explicitly absent.
        retained = [{"id": f"M{i}-L1-L2", "type": "via", "net": "ground",
                     "at": [i, 0], "diameter": 0.254, "drill": 0.127,
                     "via_type": "microvia", "layers": ["F.Cu", "In1.Cu"]}
                    for i in range(1, 6)]
        rf.validate_ground_corridor(retained, layers, "In2.Cu", [0, 6, 0.5])
        for span in [["In1.Cu", "In2.Cu"], ["In2.Cu", "B.Cu"]]:
            forbidden = {**retained[0], "layers": span}
            with self.assertRaisesRegex(rf.RFError, "intrudes"):
                rf.validate_ground_corridor(retained + [forbidden], layers, "In2.Cu", [0, 6, 0.5])
        overlay = {"id": "unexpected-L3-overlay", "type": "copper", "net": "ground",
                   "at": [1, 0], "diameter": 0.3, "layer": "In2.Cu"}
        with self.assertRaisesRegex(rf.RFError, "intrudes"):
            rf.validate_ground_corridor(retained + [overlay], layers, "In2.Cu", [0, 6, 0.5])

    def test_split_ground_stack_may_pass_above_routing_layer(self):
        recipe = self.recipe()
        params = recipe["parameters"]
        params["ground_ring"].update(start_deg=310, end_deg=50, count=5)
        params["ground_via"].update(via_type="microvia", spans=[["F.Cu", "In1.Cu"]])
        payload = rf.generate_payload(recipe)
        self.assertEqual(len([o for o in payload["objects"] if o["type"] == "via" and o["net"] == "ground"]), 5)
        params["ground_via"]["spans"].append(["In1.Cu", "In2.Cu"])
        with self.assertRaisesRegex(rf.RFError, "intrudes"):
            rf.generate_payload(recipe)

    def test_omission_never_shrinks_base_or_via(self):
        recipe = self.recipe()
        recipe["parameters"]["tuned_signal_copper"] = {layer: None for layer in recipe["parameters"]["stackup"]}
        payload = rf.generate_payload(recipe)
        self.assertFalse(any(o["type"] == "copper" for o in payload["objects"]))
        recipe["parameters"]["tuned_signal_copper"]["In1.Cu"] = 0.3
        with self.assertRaisesRegex(rf.RFError, "cannot shrink"):
            rf.generate_payload(recipe)

    def test_antipad_span_dimensions_and_gap(self):
        recipe = self.recipe()
        recipe["parameters"]["antipads"]["In2.Cu"] = 0.7
        with self.assertRaisesRegex(rf.RFError, "ground-pour gap"):
            rf.generate_payload(recipe)
        recipe = self.recipe()
        del recipe["parameters"]["antipads"]["B.Cu"]
        with self.assertRaisesRegex(rf.RFError, "required layer span"):
            rf.generate_payload(recipe)
        for radius in [0.1, 1, 100]:
            points = rf._circle_points([0, 0], radius)
            self.assertLessEqual(max(math.hypot(*point) for point in points) - radius, rf.TOLERANCE_MM + 1e-9)
            # Circumscribed edges remain outside the exact circular antipad.
            for a, b in zip(points, points[1:] + points[:1]):
                cross = abs(a[0] * b[1] - a[1] * b[0])
                self.assertGreaterEqual(cross / math.dist(a, b), radius - 1e-9)

    def test_unsupported_and_incomplete_recipe_blocks(self):
        cases = []
        recipe = self.recipe()
        recipe["parameters"]["ground_via"]["treatment"] = "filled"
        cases.append(recipe)
        recipe = self.recipe()
        recipe["parameters"]["embedded_python"] = "do_something()"
        cases.append(recipe)
        recipe = self.recipe()
        recipe["parameters"]["ground_ring"]["radius"] = float("nan")
        cases.append(recipe)
        cases.append(rf.default_recipe())
        recipe = self.recipe()
        del recipe["parameters"]["trace_width"]
        cases.append(recipe)
        for recipe in cases:
            with self.assertRaises(rf.RFError):
                rf.generate_payload(recipe)

    def test_pth_has_no_duplicate_signal_via_and_preserves_minimum_lands(self):
        part = smd_part()
        part["mount"] = "PTH"
        part["minimum_signal_copper"] = {layer: 0.508 for layer in self.recipe()["parameters"]["stackup"]}
        recipe = rf.default_recipe(part)
        recipe["parameters"]["signal_via"] = None
        payload = rf.generate_payload(recipe)
        self.assertFalse(any(o["type"] == "via" and o["net"] == "signal" for o in payload["objects"]))
        self.assertEqual(recipe["dependencies"]["base_part"]["minimum_signal_copper"]["B.Cu"], 0.508)

    def test_signal_enlargement_cannot_short_existing_base_shield_on_any_layer(self):
        part = smd_part()
        part["mount"] = "PTH"
        part["minimum_signal_copper"] = {layer: 0.508 for layer in self.recipe()["parameters"]["stackup"]}
        part["shield_geometry"] = [{"at": [0, 0], "radius": 1.1,
                                    "inner_radius": 0.6, "layers": ["F.Cu"]}]
        recipe = rf.default_recipe(part)
        recipe["parameters"]["signal_via"] = None
        rf.generate_payload(recipe)  # Minimum signal + gap fits inside annular bore.
        recipe["parameters"]["tuned_signal_copper"]["F.Cu"] = 1.5
        recipe["parameters"]["antipads"]["F.Cu"] = 1.9
        with self.assertRaisesRegex(rf.RFError, "base shield copper"):
            rf.make_artifact(recipe)
        # Tangency at the required ground clearance is accepted.
        recipe["parameters"]["tuned_signal_copper"]["F.Cu"] = 0.8
        rf.generate_payload(recipe)
        recipe["parameters"]["tuned_signal_copper"]["F.Cu"] = 0.801
        with self.assertRaisesRegex(rf.RFError, "base shield copper"):
            rf.generate_payload(recipe)
        self.assertEqual(recipe["dependencies"]["base_part"], part)

    def test_recipe_reopen_clone_revision_and_immutable_publication(self):
        a = rf.make_artifact(self.recipe(), "library-rf")
        old_bytes = rf.canonical(a)
        changed = deepcopy(a["recipe"])
        changed["parameters"]["ground_ring"]["radius"] = 2.2
        revision = rf.revise_artifact(a, changed)
        clone = rf.clone_artifact(a, changed, "new-variant")
        self.assertEqual(revision["artifact_id"], a["artifact_id"])
        self.assertEqual(revision["revision"], 2)
        self.assertEqual(clone["revision"], 1)
        self.assertEqual(clone["provenance"]["parent"], rf.pin(a))
        self.assertEqual(rf.canonical(a), old_bytes)
        with tempfile.TemporaryDirectory() as temporary:
            path = rf.publish_bundle(temporary, a)
            mtime = path.stat().st_mtime_ns
            self.assertEqual(rf.load_artifact(path, reopen=True), a)
            self.assertEqual(rf.publish_bundle(temporary, a), path)
            self.assertEqual(path.stat().st_mtime_ns, mtime)
            rf.publish_bundle(temporary, revision)
            catalog = json.loads((Path(temporary) / "manifest.json").read_text())
            self.assertEqual(len(catalog["artifacts"]), 2)
            different = rf.make_artifact(changed, "library-rf")
            with self.assertRaisesRegex(rf.RFError, "immutable"):
                rf.publish_bundle(temporary, different)
            self.assertEqual(path.read_bytes(), old_bytes + b"\n")

    def test_integrity_capability_and_opaque_recipe_compatibility(self):
        a = rf.make_artifact(self.recipe(), "test")
        for field in ["payload", "recipe", "compatibility"]:
            bad = deepcopy(a)
            bad[field]["tampered"] = True
            with self.assertRaisesRegex(rf.RFError, "integrity"):
                rf.validate_artifact(bad)
        bad = deepcopy(a)
        bad["required_capabilities"].append("execute-embedded-code")
        with self.assertRaisesRegex(rf.RFError, "capability"):
            rf.validate_artifact(rehash(bad))
        newer = deepcopy(a)
        newer["recipe"]["generator"]["version"] = "99.0.0"
        newer["schema"] = "gppo.artifact/1.1"
        rf.validate_artifact(rehash(newer))
        with self.assertRaisesRegex(rf.RFError, "different generator"):
            rf.validate_artifact(newer, reopen=True)
        unsupported = deepcopy(a)
        unsupported["payload"]["objects"].append({"id": "route", "type": "track", "net": "signal"})
        with self.assertRaisesRegex(rf.RFError, "object type"):
            rf.validate_artifact(rehash(unsupported))

    def test_runtime_capability_range(self):
        for version in ["9.0.0", "9.0.9.1", "10.0.0", "10.0.8"]:
            self.assertTrue(rf.capability_report(version)["supported"])
        for version in ["8.0.9", "10.0.9", "11.0.0", "unknown"]:
            self.assertFalse(rf.capability_report(version)["supported"])


@unittest.skipUnless(pcbnew, "native KiCad runtime required")
class RFNativeTests(unittest.TestCase):
    def test_registered_action_plugin_is_independent_and_read_only(self):
        import gppo_rf_transition
        self.assertIsInstance(gppo_rf_transition.PLUGIN, pcbnew.ActionPlugin)
        self.assertEqual(gppo_rf_transition.PLUGIN.name, "GPPO RF Transition Designer")
        for source in (ROOT / "plugins" / "gppo_rf_transition").glob("*.py"):
            tree = ast.parse(source.read_text())
            for node in ast.walk(tree):
                names = ([alias.name for alias in node.names] if isinstance(node, ast.Import)
                         else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
                self.assertFalse(any("connector_interposer_workflow" in name for name in names))

    def test_native_plated_signal_and_custom_annular_shield_roundtrip(self):
        from gppo_rf_transition.native import read_base_part
        with tempfile.TemporaryDirectory() as temporary:
            library = Path(temporary) / "Approved.pretty"
            library.mkdir()
            # Native library source, including the actual user-drawing minimum
            # dimensions. No source from a scratch prototype is imported.
            module = """(footprint "GPPO" (version 20241229) (generator "pcbnew")
              (layer "F.Cu")
              (pad "1" thru_hole circle (at 0 0) (size 0.508 0.508)
                (drill 0.381) (layers "*.Cu" "*.Mask")
                (remove_unused_layers yes) (keep_end_layers yes))
              (pad "2" smd custom (at 1.11125 0) (size 0.2 0.2)
                (layers "F.Cu" "F.Mask" "F.Paste")
                (options (clearance outline) (anchor circle))
                (primitives (gr_circle (center -1.11125 0) (end 0 0)
                  (width 0.9525) (fill no)))))"""
            path = library / "GPPO.kicad_mod"
            path.write_text(module)
            before = path.read_bytes()
            with self.assertRaisesRegex(rf.RFError, "approval"):
                read_base_part(path)
            part = read_base_part(path, approved=True)
            self.assertEqual(part["mount"], "PTH")
            self.assertEqual(part["minimum_signal_copper"]["In2.Cu"], 0.508)
            self.assertEqual(part["shield_geometry"][0]["validation"], "exact-single-circle-annulus")
            self.assertAlmostEqual(part["shield_geometry"][0]["inner_radius"], 0.635)
            self.assertAlmostEqual(part["shield_geometry"][0]["radius"], 1.5875)
            recipe = rf.default_recipe(part)
            recipe["parameters"]["signal_via"] = None
            artifact = rf.make_artifact(recipe, "native-read")
            rf.publish_bundle(Path(temporary) / "RF", artifact)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(part["sha256"], hashlib.sha256(before).hexdigest())
            expanded = deepcopy(recipe)
            expanded["parameters"]["stackup"] = ["F.Cu", "In1.Cu", "In2.Cu", "In3.Cu", "In4.Cu", "B.Cu"]
            with self.assertRaisesRegex(rf.RFError, "stackup changed since base-part inspection"):
                rf.generate_payload(expanded)

    @unittest.skipUnless(os.environ.get("DISPLAY"), "native wx preview requires a display")
    def test_native_dialog_validation_and_stale_publication_gate(self):
        import wx
        from gppo_rf_transition.plugin import RFDesignerDialog
        app = wx.GetApp() or wx.App(False)
        dialog = RFDesignerDialog()
        try:
            dialog.Show()
            app.Yield()
            self.assertFalse(dialog.save.IsEnabled())
            dialog.set_recipe(rf.default_recipe(smd_part()))
            dialog.validate(None)
            app.Yield()
            self.assertTrue(dialog.save.IsEnabled(), dialog.report.GetValue())
            self.assertTrue(dialog.preview.payload)
            original = dialog.validated[1]
            dialog.artifact = original
            dialog.revision(None)
            self.assertFalse(dialog.save.IsEnabled())
            dialog.validate(None)
            self.assertTrue(dialog.save.IsEnabled())
            self.assertEqual(dialog.validated[1]["revision"], 2)
            dialog.clone(None)
            dialog.validate(None)
            self.assertNotEqual(dialog.validated[1]["artifact_id"], original["artifact_id"])
            dialog.editor.SetValue("{}")
            app.Yield()
            self.assertFalse(dialog.save.IsEnabled())
            dialog.validate(None)
            self.assertIn("BLOCKED", dialog.report.GetValue())
        finally:
            dialog.Destroy()
            app.Yield()


if __name__ == "__main__":
    unittest.main()
