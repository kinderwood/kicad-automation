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
            "signal_pad": "1", "shield_pads": ["2"], "signal_at": [0, 0],
            "signal_shape": "circle", "signal_land": 0.45, "signal_drill": None,
            "shield_geometry": [{"at": [0, 0], "radius": 1.5875, "inner_radius": 0.635}]}


def pth_part():
    return {**smd_part(), "mount": "PTH", "signal_land": 0.508, "signal_drill": 0.381}


def layer_names(count):
    return ["F.Cu", *[f"In{i}.Cu" for i in range(1, count - 1)], "B.Cu"]


def resize(recipe, count, signal_layer):
    """Give a recipe another copper layer count, with no tuned copper."""
    layers = layer_names(count)
    params = recipe["parameters"]
    part = recipe["dependencies"]["base_part"]
    params["stackup"] = layers
    params["signal_layer"] = signal_layer
    params["tuned_signal_copper"] = {layer: None for layer in layers}
    order = layers if part["side"] == "front" else layers[::-1]
    covered = layers if part["mount"] == "PTH" else order[:order.index(signal_layer) + 1]
    params["antipads"] = {layer: 1.1 for layer in covered}
    return layers


def rehash(artifact):
    artifact.pop("hashes", None)
    artifact["hashes"] = {"payload": rf.digest(artifact["payload"]),
                          "recipe": rf.digest(artifact["recipe"]),
                          "artifact": rf.digest(artifact)}
    return artifact


def objects(payload, **match):
    return [o for o in payload["objects"] if all(o.get(k) == v for k, v in match.items())]


class RFRecipeTests(unittest.TestCase):
    def recipe(self):
        return rf.default_recipe(smd_part())

    def pth_recipe(self):
        return rf.default_recipe(pth_part())

    def test_cross_zero_endpoints_and_full_circle_exact_count(self):
        self.assertEqual(rf.arc_angles(310, 50, 5), [310, 335, 0, 25, 50])
        self.assertEqual(rf.arc_angles(0, 360, 4), [0, 90, 180, 270])
        self.assertEqual(rf.arc_angles(15, -345, 4), [15, 105, 195, 285])
        for start, end, count in [(0, 0, 4), (0, 720, 4), (0, 10, 1), (0, 10, True)]:
            with self.assertRaises(rf.RFError):
                rf.arc_angles(start, end, count)

    def test_midpoints_partial_arc_has_one_fewer_and_full_circle_keeps_count(self):
        self.assertEqual(rf.arc_midpoints(40, 320, 9), [57.5 + 35 * i for i in range(8)])
        self.assertEqual(rf.arc_midpoints(310, 50, 5), [322.5, 347.5, 12.5, 37.5])
        self.assertEqual(rf.arc_midpoints(0, 360, 4), [45, 135, 225, 315])
        self.assertEqual(rf.arc_midpoints(0, 360, 1), [180])
        with self.assertRaises(rf.RFError):
            rf.arc_midpoints(0, 10, 1)

    def test_deterministic_exact_saved_payload_and_roles(self):
        recipe = self.recipe()
        a = rf.make_artifact(recipe, "test-rf")
        b = rf.make_artifact(recipe, "test-rf")
        self.assertEqual(rf.canonical(a), rf.canonical(b))
        self.assertEqual(len(objects(a["payload"], type="via", net="ground")), 9)
        self.assertEqual({o["type"] for o in a["payload"]["objects"]}, {"via", "copper", "keepout", "track"})
        self.assertEqual(a["payload"]["layer_roles"], {"mount": "F.Cu", "signal": "In2.Cu"})
        self.assertEqual(a["hashes"]["payload"], hashlib.sha256(rf.canonical(a["payload"])).hexdigest())
        self.assertEqual((a["schema"], a["payload_schema"], a["recipe"]["schema"]),
                         ("gppo.artifact/2.0", "gppo.rf/2.0", "gppo.rf-recipe/2.0"))
        self.assertEqual(a["required_capabilities"],
                         ["adjacent-microvia-stacks", "filled-vias", "layer-rule-areas",
                          "native-tracks", "native-vias", "net-copper-shapes"])
        rf.validate_artifact(a, reopen=True)

    def test_every_via_is_copper_plated_shut_and_it_is_not_a_setting(self):
        for recipe in (self.recipe(), self.pth_recipe()):
            vias = objects(rf.generate_payload(recipe), type="via")
            self.assertTrue(vias)
            self.assertEqual({via["fill"] for via in vias}, {"copper"})
        for name in ("signal_via", "ground_via"):
            for field, value in (("treatment", "untreated"), ("fill", "copper"), ("fill", "none")):
                recipe = self.recipe()
                recipe["parameters"][name][field] = value
                with self.subTest(name=name, field=field), self.assertRaisesRegex(rf.RFError, "unsupported fields"):
                    rf.generate_payload(recipe)
        artifact = rf.make_artifact(self.recipe(), "fill")
        for change in ({"fill": "epoxy"}, {"fill": None}):
            bad = deepcopy(artifact)
            objects(bad["payload"], type="via")[0].update(change)
            with self.assertRaisesRegex(rf.RFError, "plated shut"):
                rf.validate_artifact(rehash(bad))

    def test_only_through_and_one_layer_microvia_exist(self):
        recipe = self.recipe()
        recipe["parameters"]["ground_via"]["via_type"] = "blind_buried"
        with self.assertRaisesRegex(rf.RFError, "through or microvia"):
            rf.generate_payload(recipe)
        layers = layer_names(4)
        via = {"layers": ["F.Cu", "In2.Cu"], "diameter": 0.45, "drill": 0.2, "fill": "copper"}
        for via_type, message in (("blind_buried", "only through and one-layer microvia"),
                                  ("microvia", "adjacent"), ("through", "entire board")):
            with self.subTest(via_type=via_type), self.assertRaisesRegex(rf.RFError, message):
                rf.via_layers({**via, "via_type": via_type}, layers)
        artifact = rf.make_artifact(self.recipe(), "types")
        objects(artifact["payload"], type="via", net="ground")[0]["via_type"] = "blind_buried"
        with self.assertRaisesRegex(rf.RFError, "only through and one-layer microvia"):
            rf.validate_artifact(rehash(artifact))

    def test_tangency_allowed_and_true_intrusion_blocked(self):
        corridor = [0, 3, 0.4]
        self.assertFalse(rf.circle_intrudes_corridor([2, 0.7], 0.3, corridor))
        self.assertTrue(rf.circle_intrudes_corridor([2, 0.699], 0.3, corridor))
        self.assertFalse(rf.circle_intrudes_corridor([3.3, 0], 0.3, corridor))
        recipe = self.recipe()
        recipe["parameters"]["ground_ring"].update(start_deg=310, end_deg=50, count=5)
        with self.assertRaisesRegex(rf.RFError, "intrudes"):
            rf.generate_payload(recipe)

    def test_exit_trace_handoff_and_corridor_stop_at_the_ring_radius(self):
        for recipe in (self.recipe(), self.pth_recipe()):
            recipe["parameters"]["ground_ring"]["radius"] = 2.25
            payload = rf.generate_payload(recipe)
            self.assertEqual(objects(payload, type="track"), [
                {"id": "exit-trace", "type": "track", "net": "signal", "layer": "In2.Cu",
                 "width": 0.25, "start": [0, 0], "end": [2.25, 0]}])
            self.assertEqual(payload["anchors"],
                             {"signal": [0, 0], "exit_direction_deg": 0, "handoff": [2.25, 0]})
            corridor = objects(payload, purpose="exit-corridor")[0]
            self.assertEqual(corridor["layers"], ["In2.Cu"])
            # Half-width is trace_width / 2 + ground_pour_gap; it ends at the trace end.
            self.assertEqual(corridor["points"],
                             [[0, -0.325], [2.25, -0.325], [2.25, 0.325], [0, 0.325]])

    def test_ground_land_may_be_tangent_to_the_corridor_but_not_inside(self):
        # Half-width 0.325 plus land radius 0.3: a land centred 0.625 from the
        # centreline touches the corridor edge.
        angle = math.degrees(math.asin(0.625 / 2.0))
        recipe = self.recipe()
        recipe["parameters"]["ground_ring"].update(start_deg=angle + 1e-7, end_deg=180, count=3)
        rf.generate_payload(recipe)
        recipe["parameters"]["ground_ring"]["start_deg"] = angle - 0.01
        with self.assertRaisesRegex(rf.RFError, "ground-0: ground pad/barrel/copper intrudes"):
            rf.generate_payload(recipe)
        # A land beyond the trace end is outside the corridor even on the axis:
        # the corridor stops at the ring radius.
        self.assertFalse(rf.circle_intrudes_corridor([2.3, 0], 0.3, [0, 2.0, 0.325]))
        self.assertTrue(rf.circle_intrudes_corridor([2.29, 0], 0.3, [0, 2.0, 0.325]))

    def test_smd_signal_stack_is_derived_from_mount_to_exit_at_any_depth(self):
        recipe = self.recipe()
        layers = resize(recipe, 8, "In5.Cu")
        payload = rf.generate_payload(recipe)
        signal = objects(payload, type="via", net="signal")
        self.assertEqual([via["layers"] for via in signal],
                         [[a, b] for a, b in zip(layers[:5], layers[1:6])])
        self.assertEqual({(via["via_type"], tuple(via["at"])) for via in signal}, {("microvia", (0, 0))})
        recipe["parameters"]["signal_via"]["spans"] = [["F.Cu", "In1.Cu"]]
        with self.assertRaisesRegex(rf.RFError, "unsupported fields"):
            rf.generate_payload(recipe)
        recipe = self.recipe()
        recipe["parameters"]["signal_layer"] = "F.Cu"
        with self.assertRaisesRegex(rf.RFError, "different routing layer"):
            rf.generate_payload(recipe)

    def test_back_mount_stack_runs_upward_from_the_back_layer(self):
        recipe = rf.default_recipe({**smd_part(), "side": "back"})
        resize(recipe, 6, "In3.Cu")
        recipe["parameters"]["ground_via"].update(via_type="microvia", construction="stacked",
                                                  stop_layer="In2.Cu")
        payload = rf.generate_payload(recipe)
        self.assertEqual(payload["layer_roles"], {"mount": "B.Cu", "signal": "In3.Cu"})
        self.assertEqual([via["layers"] for via in objects(payload, type="via", net="signal")],
                         [["In4.Cu", "B.Cu"], ["In3.Cu", "In4.Cu"]])
        self.assertEqual({o["layers"][0] for o in objects(payload, purpose="antipad")},
                         {"B.Cu", "In4.Cu", "In3.Cu"})
        # Directly below the exit, further from the back mount side.
        self.assertEqual(objects(payload, purpose="ground-disc")[0]["layer"], "In2.Cu")
        self.assertEqual({tuple(via["layers"]) for via in objects(payload, type="via", net="ground")},
                         {("In4.Cu", "B.Cu"), ("In3.Cu", "In4.Cu"), ("In2.Cu", "In3.Cu")})

    def test_straight_l3_m6_escape_omits_crossed_l3_segments(self):
        layers = ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]
        # M5…M1 are crossed by the same +X corridor. Only L1–L2
        # segments remain; L2–L3 and L3–L4 must be explicitly absent.
        retained = [{"id": f"M{i}-L1-L2", "type": "via", "net": "ground",
                     "at": [i, 0], "diameter": 0.254, "drill": 0.127,
                     "via_type": "microvia", "layers": ["F.Cu", "In1.Cu"], "fill": "copper"}
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

    def test_microvia_ring_stopped_above_the_exit_layer_may_cross_the_corridor(self):
        recipe = self.recipe()
        params = recipe["parameters"]
        params["ground_ring"].update(start_deg=310, end_deg=50, count=5)
        params["ground_via"].update(via_type="microvia", construction="stacked", stop_layer="In1.Cu")
        payload = rf.generate_payload(recipe)
        ground = objects(payload, type="via", net="ground")
        self.assertEqual(len(ground), 5)
        self.assertEqual({tuple(via["layers"]) for via in ground}, {("F.Cu", "In1.Cu")})
        params["ground_via"]["stop_layer"] = "In2.Cu"
        with self.assertRaisesRegex(rf.RFError, "intrudes"):
            rf.generate_payload(recipe)

    def test_stacked_microvia_ring_runs_the_whole_board_or_stops_at_a_layer(self):
        recipe = self.recipe()
        layers = resize(recipe, 6, "In2.Cu")
        ground = recipe["parameters"]["ground_via"]
        ground.update(via_type="microvia", construction="stacked", stop_layer=None)
        whole = objects(rf.generate_payload(recipe), type="via", net="ground")
        self.assertEqual(len(whole), 9 * 5)
        self.assertEqual({tuple(via["layers"]) for via in whole}, set(zip(layers, layers[1:])))
        # Every layer pair sits at the same nine positions.
        self.assertEqual(len({tuple(via["at"]) for via in whole}), 9)
        ground["stop_layer"] = "In3.Cu"
        stopped = objects(rf.generate_payload(recipe), type="via", net="ground")
        self.assertEqual({tuple(via["layers"]) for via in stopped}, set(zip(layers[:3], layers[1:4])))
        for stop, message in (("F.Cu", "below the mount layer"), ("In9.Cu", "below the mount layer")):
            ground["stop_layer"] = stop
            with self.assertRaisesRegex(rf.RFError, message):
                rf.generate_payload(recipe)
        ground.update(stop_layer=None, construction="spiral")
        with self.assertRaisesRegex(rf.RFError, "stacked or staggered"):
            rf.generate_payload(recipe)
        ground.update(via_type="through", construction="stacked")
        with self.assertRaisesRegex(rf.RFError, "must be null"):
            rf.generate_payload(recipe)

    def test_staggered_ring_alternates_pairs_between_pattern_and_midpoints(self):
        recipe = self.recipe()
        layers = resize(recipe, 6, "In2.Cu")
        ground = recipe["parameters"]["ground_via"]
        ground.update(via_type="microvia", diameter=0.3, drill=0.1,
                      construction="staggered", stop_layer=None)
        payload = rf.generate_payload(recipe)
        vias = objects(payload, type="via", net="ground")
        entered = rf.arc_angles(40, 320, 9)
        middle = rf.arc_midpoints(40, 320, 9)

        def angle(via):
            return round(math.degrees(math.atan2(via["at"][1], via["at"][0])) % 360, 6)
        for depth, pair in enumerate(zip(layers, layers[1:])):
            here = [via for via in vias if tuple(via["layers"]) == pair]
            # The entered pattern applies to the pair at the mount layer; a
            # partial arc of nine has eight midpoints.
            expected = middle if depth % 2 else entered
            self.assertEqual(sorted(angle(via) for via in here), sorted(expected), pair)
            self.assertEqual({round(math.hypot(*via["at"]), 9) for via in here}, {2.0})
        self.assertEqual(len(vias), 9 * 3 + 8 * 2)
        self.assertEqual(len({via["id"] for via in vias}), len(vias))
        # A full circle keeps the count at the midpoints.
        recipe["parameters"]["ground_ring"].update(start_deg=20, end_deg=380, count=5)
        ground["stop_layer"] = "In2.Cu"
        full = objects(rf.generate_payload(recipe), type="via", net="ground")
        self.assertEqual(len(full), 5 + 5)
        self.assertEqual(sorted(angle(via) for via in full if via["layers"] == ["In1.Cu", "In2.Cu"]),
                         [56, 128, 200, 272, 344])
        # A midpoint land is held out of the exit corridor like any other.
        recipe["parameters"]["ground_ring"].update(start_deg=30, end_deg=390, count=6)
        with self.assertRaisesRegex(rf.RFError, "ground-m5-1: ground pad/barrel/copper intrudes"):
            rf.generate_payload(recipe)

    def test_ground_lands_may_be_tangent_never_overlapping_on_every_shared_layer(self):
        # Two positions 90° apart on radius 2: neighbours are 2·2·sin(45°) apart.
        chord = 2 * 2.0 * math.sin(math.radians(45))
        recipe = self.recipe()
        ground = recipe["parameters"]["ground_via"]
        recipe["parameters"]["ground_ring"].update(start_deg=90, end_deg=270, count=3)
        ground.update(diameter=chord - 1e-10, drill=0.3)
        rf.generate_payload(recipe)  # Tangent lands pass.
        ground["diameter"] = chord + 1e-6
        with self.assertRaisesRegex(rf.RFError, "ground via lands overlap"):
            rf.generate_payload(recipe)
        # Staggered: each set alone is clear, but on the shared layer the
        # midpoint lands fall between the entered ones, 45° apart.
        recipe = self.recipe()
        resize(recipe, 6, "In2.Cu")
        ground = recipe["parameters"]["ground_via"]
        recipe["parameters"]["ground_ring"].update(start_deg=90, end_deg=270, count=3)
        half = 2 * 2.0 * math.sin(math.radians(22.5))
        ground.update(via_type="microvia", drill=0.1, construction="stacked",
                      stop_layer="In1.Cu", diameter=half + 0.01)
        rf.generate_payload(recipe)  # One pair: only the entered positions exist.
        ground.update(construction="staggered")
        rf.generate_payload(recipe)  # Still one pair, so no midpoints yet.
        ground["stop_layer"] = "In2.Cu"
        with self.assertRaisesRegex(rf.RFError, "In1.Cu: ground via lands overlap"):
            rf.generate_payload(recipe)
        ground["construction"] = "stacked"
        rf.generate_payload(recipe)
        ground.update(construction="staggered", diameter=half - 1e-10)
        rf.generate_payload(recipe)  # Tangent on the shared layer passes.
        # An overlay enlarges the lands on its layer and is tested there.
        ground["diameter"] = 0.3
        recipe["parameters"]["tuned_ground_copper"] = {"In1.Cu": half + 0.01}
        with self.assertRaisesRegex(rf.RFError, "In1.Cu: ground via lands overlap"):
            rf.generate_payload(recipe)
        recipe["parameters"]["tuned_ground_copper"] = {"In1.Cu": 0.5}
        payload = rf.generate_payload(recipe)
        # Both sets carry the overlay on the layer they share.
        self.assertEqual(len(objects(payload, purpose="ground-overlay", layer="In1.Cu")), 3 + 2)
        recipe["parameters"]["tuned_ground_copper"] = {"In4.Cu": 0.5}
        with self.assertRaisesRegex(rf.RFError, "requires a ground via land"):
            rf.generate_payload(recipe)

    def test_omission_never_shrinks_base_or_via(self):
        recipe = self.recipe()
        recipe["parameters"]["tuned_signal_copper"] = {layer: None for layer in recipe["parameters"]["stackup"]}
        payload = rf.generate_payload(recipe)
        self.assertFalse(objects(payload, purpose="signal-overlay"))
        recipe["parameters"]["tuned_signal_copper"]["In1.Cu"] = 0.3
        with self.assertRaisesRegex(rf.RFError, "cannot shrink"):
            rf.generate_payload(recipe)

    def test_smd_antipads_run_from_mount_layer_to_exit_layer_only(self):
        recipe = self.recipe()
        payload = rf.generate_payload(recipe)
        self.assertEqual([o["layers"] for o in objects(payload, purpose="antipad")],
                         [["F.Cu"], ["In1.Cu"], ["In2.Cu"]])
        recipe["parameters"]["antipads"]["B.Cu"] = 1.1
        with self.assertRaisesRegex(rf.RFError, "exactly these layers: F.Cu, In1.Cu, In2.Cu"):
            rf.generate_payload(recipe)
        recipe = self.recipe()
        del recipe["parameters"]["antipads"]["In1.Cu"]
        with self.assertRaisesRegex(rf.RFError, "exactly these layers"):
            rf.generate_payload(recipe)
        recipe = self.recipe()
        recipe["parameters"]["antipad_span"] = ["F.Cu", "B.Cu"]
        with self.assertRaisesRegex(rf.RFError, "unsupported fields"):
            rf.generate_payload(recipe)
        # No signal copper where there is no antipad.
        recipe = self.recipe()
        recipe["parameters"]["tuned_signal_copper"]["B.Cu"] = 0.6
        with self.assertRaisesRegex(rf.RFError, "beyond the exit layer"):
            rf.generate_payload(recipe)

    def test_antipad_dimensions_and_gap(self):
        recipe = self.recipe()
        recipe["parameters"]["antipads"]["In2.Cu"] = 0.7
        with self.assertRaisesRegex(rf.RFError, "ground-pour gap"):
            rf.generate_payload(recipe)
        recipe = self.recipe()
        recipe["parameters"]["antipads"]["F.Cu"] = 3.5
        with self.assertRaisesRegex(rf.RFError, "intrudes on F.Cu antipad"):
            rf.generate_payload(recipe)
        for radius in [0.1, 1, 100]:
            points = rf._circle_points([0, 0], radius)
            self.assertLessEqual(max(math.hypot(*point) for point in points) - radius, rf.TOLERANCE_MM + 1e-9)
            # Circumscribed edges remain outside the exact circular antipad.
            for a, b in zip(points, points[1:] + points[:1]):
                cross = abs(a[0] * b[1] - a[1] * b[0])
                self.assertGreaterEqual(cross / math.dist(a, b), radius - 1e-9)

    def test_smd_ground_disc_sits_directly_below_the_exit_layer(self):
        recipe = self.recipe()
        payload = rf.generate_payload(recipe)
        self.assertEqual(objects(payload, purpose="ground-disc"), [
            {"id": "ground-disc", "type": "copper", "shape": "circle", "purpose": "ground-disc",
             "net": "ground", "at": [0, 0], "layer": "B.Cu", "diameter": 3.175}])
        # It is solid ground: no antipad opens that layer.
        self.assertFalse([o for o in objects(payload, purpose="antipad") if "B.Cu" in o["layers"]])
        resize(recipe, 6, "In1.Cu")
        self.assertEqual(objects(rf.generate_payload(recipe), purpose="ground-disc")[0]["layer"], "In2.Cu")
        # The exit on the far outer layer has no layer below it: blocked.
        recipe = self.recipe()
        resize(recipe, 4, "B.Cu")
        with self.assertRaisesRegex(rf.RFError, "no layer below it for the ground disc"):
            rf.generate_payload(recipe)
        for shields in ([], [{"at": [3, 0], "radius": 0.5}],
                        [{"at": [0.05, 0], "radius": 1.5875, "inner_radius": 0.635}],
                        [{"at": [0, 0], "radius": 1.5875, "inner_radius": 0.635}] * 2):
            recipe = self.recipe()
            recipe["dependencies"]["base_part"]["shield_geometry"] = shields
            with self.subTest(shields=shields), self.assertRaisesRegex(rf.RFError, "ground disc needs one annular"):
                rf.generate_payload(recipe)
        # A through-hole pin has no ground disc: its barrel passes every layer.
        self.assertFalse(objects(rf.generate_payload(self.pth_recipe()), purpose="ground-disc"))

    def test_unsupported_and_incomplete_recipe_blocks(self):
        cases = []
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
        recipe = self.recipe()
        del recipe["parameters"]["ground_via"]["construction"]
        cases.append(recipe)
        recipe = self.recipe()
        recipe["schema"] = "gppo.rf-recipe/1.0"
        cases.append(recipe)
        recipe = self.recipe()
        recipe["dependencies"]["base_part"]["signal_drill"] = 0.2
        cases.append(recipe)
        for recipe in cases:
            with self.assertRaises(rf.RFError):
                rf.generate_payload(recipe)

    def test_pth_lands_only_on_outer_and_exit_layers_and_antipads_on_every_layer(self):
        recipe = self.pth_recipe()
        layers = resize(recipe, 6, "In2.Cu")
        params = recipe["parameters"]
        self.assertIsNone(params["signal_via"])
        payload = rf.generate_payload(recipe)
        self.assertFalse(objects(payload, type="via", net="signal"))
        self.assertEqual([o["layers"] for o in objects(payload, purpose="antipad")], [[l] for l in layers])
        del params["antipads"]["In4.Cu"]
        with self.assertRaisesRegex(rf.RFError, "every layer for a through-hole pin"):
            rf.generate_payload(recipe)
        # Gap 0.2. Land layers (outers and the exit) must clear the 0.508 land;
        # other inner layers carry the bare 0.381 barrel only.
        for layer, passes, blocks in (("F.Cu", 0.908, 0.907), ("B.Cu", 0.908, 0.907),
                                      ("In2.Cu", 0.908, 0.907), ("In1.Cu", 0.781, 0.780),
                                      ("In3.Cu", 0.781, 0.780), ("In4.Cu", 0.781, 0.780)):
            params["antipads"] = {name: 1.1 for name in layers}
            params["antipads"][layer] = passes
            rf.generate_payload(recipe)
            params["antipads"][layer] = blocks
            with self.subTest(layer=layer), self.assertRaisesRegex(rf.RFError, f"{layer}: antipad must include"):
                rf.generate_payload(recipe)
        params["antipads"] = {name: 1.1 for name in layers}
        # Overlay copper: never smaller than the land where one exists; on a
        # bare-barrel layer it is a tuning disc that must exceed the hole.
        params["tuned_signal_copper"]["In2.Cu"] = 0.5
        with self.assertRaisesRegex(rf.RFError, "In2.Cu: tuned copper cannot shrink"):
            rf.generate_payload(recipe)
        params["tuned_signal_copper"]["In2.Cu"] = 0.508
        self.assertFalse(objects(rf.generate_payload(recipe), purpose="signal-overlay"))
        params["tuned_signal_copper"]["In2.Cu"] = None
        params["tuned_signal_copper"]["In3.Cu"] = 0.381
        with self.assertRaisesRegex(rf.RFError, "In3.Cu: tuned copper must exceed the plated signal hole"):
            rf.generate_payload(recipe)
        params["tuned_signal_copper"]["In3.Cu"] = 0.45
        self.assertEqual([o["id"] for o in objects(rf.generate_payload(recipe), purpose="signal-overlay")],
                         ["signal-copper-In3.Cu"])
        params["signal_via"] = {"diameter": 0.45, "drill": 0.2}
        with self.assertRaisesRegex(rf.RFError, "signal_via must be null"):
            rf.generate_payload(recipe)

    def test_annular_ring_under_five_mil_is_a_warning_not_a_block(self):
        recipe = self.recipe()
        review = rf.review_recipe(recipe)
        self.assertEqual(review["payload"], rf.generate_payload(recipe))
        # Default signal microvia: (0.45 − 0.2) / 2 = 0.125 mm, under 0.127 mm.
        self.assertEqual(review["warnings"], [
            {"code": "annular-ring", "subject": "signal via", "value_mm": 0.125, "limit_mm": 0.127,
             "message": "signal via: annular ring 0.1250 mm is under 0.127 mm (5 mil)"}])
        self.assertEqual(rf.recipe_warnings(recipe), review["warnings"])
        # Exactly 5 mil passes without a warning.
        recipe["parameters"]["signal_via"]["diameter"] = 0.454
        recipe["parameters"]["tuned_signal_copper"]["In1.Cu"] = 0.55
        self.assertEqual(rf.recipe_warnings(recipe), [])
        recipe["parameters"]["ground_via"].update(diameter=0.5, drill=0.3)
        self.assertEqual([w["subject"] for w in rf.recipe_warnings(recipe)], ["ground via"])
        # Footprint lands are covered too: 0.508 land on a 0.381 hole is 0.0635 mm.
        recipe = self.pth_recipe()
        warnings = rf.recipe_warnings(recipe)
        self.assertEqual([(w["subject"], w["value_mm"]) for w in warnings], [("footprint signal land", 0.0635)])
        recipe["parameters"]["tuned_signal_copper"]["In1.Cu"] = 0.5
        self.assertEqual([w["subject"] for w in rf.recipe_warnings(recipe)],
                         ["footprint signal land", "signal copper In1.Cu"])
        recipe["dependencies"]["base_part"]["signal_land"] = 0.635
        recipe["parameters"]["tuned_signal_copper"] = {l: None for l in recipe["parameters"]["stackup"]}
        recipe["parameters"]["antipads"] = {l: 1.2 for l in recipe["parameters"]["stackup"]}
        self.assertEqual(rf.recipe_warnings(recipe), [])
        # Warnings never change the payload or block publication; blocks still raise.
        rf.make_artifact(self.recipe(), "warned")
        recipe = self.recipe()
        recipe["parameters"]["signal_via"]["drill"] = 0.45
        with self.assertRaisesRegex(rf.RFError, "via diameter must exceed drill"):
            rf.recipe_warnings(recipe)

    def test_stackup_identity_is_the_copper_layer_count(self):
        part = smd_part()
        # One inspected part serves any layer count: nothing in it names a stackup.
        self.assertNotIn("inspection_stackup", part)
        for count in (4, 6, 12):
            recipe = rf.default_recipe(part)
            resize(recipe, count, "In2.Cu")
            artifact = rf.make_artifact(recipe, f"layers-{count}")
            self.assertEqual(artifact["compatibility"]["copper_layer_count"], count)
            self.assertNotIn("stackup", artifact["compatibility"])
            rf.validate_artifact(artifact, reopen=True)
        recipe = rf.default_recipe(part)
        recipe["parameters"]["stackup"] = ["top", "In1.Cu", "In2.Cu", "bottom"]
        with self.assertRaisesRegex(rf.RFError, "actual KiCad copper layers"):
            rf.generate_payload(recipe)
        self.assertEqual(rf.copper_layer_names(2), ["F.Cu", "B.Cu"])
        for count in (3, 0, 34, "4", True):
            with self.subTest(count=count), self.assertRaises(rf.RFError):
                rf.copper_layer_names(count)
        artifact["compatibility"]["copper_layer_count"] = 4
        with self.assertRaises(rf.RFError):
            rf.validate_artifact(rehash(artifact))

    def test_signal_enlargement_cannot_short_existing_base_shield_on_mount_layer(self):
        part = pth_part()
        part["shield_geometry"] = [{"at": [0, 0], "radius": 1.1, "inner_radius": 0.6}]
        recipe = rf.default_recipe(part)
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
        # The ground pad is mount-side copper only: other layers are unaffected.
        recipe["parameters"]["tuned_signal_copper"].update({"F.Cu": None, "B.Cu": 1.5})
        recipe["parameters"]["antipads"]["B.Cu"] = 1.9
        rf.generate_payload(recipe)
        # A mount-layer exit cannot cross the ground pad.
        recipe = rf.default_recipe(part)
        recipe["parameters"]["signal_layer"] = "F.Cu"
        recipe["parameters"]["tuned_signal_copper"]["In2.Cu"] = None
        with self.assertRaisesRegex(rf.RFError, "shield land intrudes"):
            rf.generate_payload(recipe)

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
        newer["schema"] = "gppo.artifact/2.1"
        rf.validate_artifact(rehash(newer))
        with self.assertRaisesRegex(rf.RFError, "different generator"):
            rf.validate_artifact(newer, reopen=True)
        unsupported = deepcopy(a)
        unsupported["payload"]["objects"].append({"id": "extra", "type": "pad", "net": "signal"})
        with self.assertRaisesRegex(rf.RFError, "object type"):
            rf.validate_artifact(rehash(unsupported))
        older = deepcopy(a)
        older["schema"] = "gppo.artifact/1.0"
        with self.assertRaisesRegex(rf.RFError, "unsupported artifact envelope"):
            rf.validate_artifact(rehash(older))
        for change in ("second", "moved", "layer", "handoff"):
            bad = deepcopy(a)
            trace = objects(bad["payload"], type="track")[0]
            if change == "second":
                bad["payload"]["objects"].append({**trace, "id": "route"})
            elif change == "moved":
                trace["end"] = [1.0, 0]
            elif change == "layer":
                trace["layer"] = "In1.Cu"
            else:
                del bad["payload"]["anchors"]["handoff"]
            with self.subTest(change=change), self.assertRaisesRegex(rf.RFError, "exit trace|anchors"):
                rf.validate_artifact(rehash(bad))

    def test_runtime_capability_range(self):
        for version in ["10.0.0", "10.0.4", "10.0.8"]:
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
            # Pin 1 signal, pin 2 ground and front mounting are the defaults.
            self.assertEqual((part["signal_pad"], part["shield_pads"], part["side"]), ("1", ["2"], "front"))
            # One land diameter and the hole; nothing names a board stackup.
            self.assertEqual((part["signal_land"], part["signal_drill"]), (0.508, 0.381))
            self.assertNotIn("inspection_stackup", part)
            self.assertEqual(part["shield_geometry"][0]["validation"], "exact-single-circle-annulus")
            self.assertAlmostEqual(part["shield_geometry"][0]["inner_radius"], 0.635)
            self.assertAlmostEqual(part["shield_geometry"][0]["radius"], 1.5875)
            recipe = rf.default_recipe(part)
            self.assertIsNone(recipe["parameters"]["signal_via"])
            artifact = rf.make_artifact(recipe, "native-read")
            rf.publish_bundle(Path(temporary) / "RF", artifact)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(part["sha256"], hashlib.sha256(before).hexdigest())
            # The same inspection serves a board with another layer count.
            expanded = deepcopy(recipe)
            resize(expanded, 6, "In3.Cu")
            payload = rf.generate_payload(expanded)
            self.assertEqual(len(objects(payload, purpose="antipad")), 6)

    def test_native_through_hole_pad_must_remove_unused_layers_and_keep_ends(self):
        from gppo_rf_transition.native import read_base_part
        shield = """(pad "2" smd custom (at 1.11125 0) (size 0.2 0.2)
                (layers "F.Cu" "F.Mask" "F.Paste")
                (options (clearance outline) (anchor circle))
                (primitives (gr_circle (center -1.11125 0) (end 0 0)
                  (width 0.9525) (fill no))))"""
        signal = """(pad "1" thru_hole circle (at 0 0) (size 0.508 0.508)
                (drill 0.381) (layers "*.Cu" "*.Mask") %s)"""
        cases = {
            "keep-all": (signal % "", "remove unused layers and keep start and end"),
            "remove-all": (signal % "(remove_unused_layers yes) (keep_end_layers no)",
                           "remove unused layers and keep start and end"),
            "custom-padstack": (
                """(pad "1" thru_hole circle (at 0 0) (size 0.508 0.508)
                (drill 0.381) (layers "*.Cu" "*.Mask")
                (remove_unused_layers yes) (keep_end_layers yes)
                (padstack (mode front_inner_back)
                  (layer "Inner" (shape circle) (size 0.45 0.45))
                  (layer "B.Cu" (shape circle) (size 0.508 0.508))))""",
                "ordinary padstack"),
            "ground-through-hole": (
                signal % "(remove_unused_layers yes) (keep_end_layers yes)",
                "ground pad must be SMD copper on the selected mount side"),
        }
        with tempfile.TemporaryDirectory() as temporary:
            library = Path(temporary) / "Approved.pretty"
            library.mkdir()
            for name, (pad, message) in cases.items():
                ground = shield if name != "ground-through-hole" else (
                    '(pad "2" thru_hole circle (at 2 0) (size 1 1) (drill 0.5) (layers "*.Cu" "*.Mask"))')
                path = library / f"{name}.kicad_mod"
                path.write_text(f'''(footprint "{name}" (version 20241229) (generator "pcbnew")
                  (layer "F.Cu") {pad} {ground})''')
                with self.subTest(name=name), self.assertRaisesRegex(rf.RFError, message):
                    read_base_part(path, approved=True)

    def test_native_smd_source_and_mount_side(self):
        from gppo_rf_transition.native import read_base_part
        with tempfile.TemporaryDirectory() as temporary:
            library = Path(temporary) / "Approved.pretty"
            library.mkdir()
            path = library / "GPPO_SMD.kicad_mod"
            path.write_text("""(footprint "GPPO_SMD" (version 20241229) (generator "pcbnew")
              (layer "F.Cu")
              (pad "1" smd circle (at 0 0) (size 0.45 0.45) (layers "F.Cu" "F.Mask" "F.Paste"))
              (pad "2" smd custom (at 1.11125 0) (size 0.2 0.2)
                (layers "F.Cu" "F.Mask" "F.Paste")
                (options (clearance outline) (anchor circle))
                (primitives (gr_circle (center -1.11125 0) (end 0 0)
                  (width 0.9525) (fill no)))))""")
            part = read_base_part(path, approved=True)
            self.assertEqual((part["mount"], part["signal_land"], part["signal_drill"]), ("SMD", 0.45, None))
            payload = rf.generate_payload(rf.default_recipe(part))
            # The ground disc takes the footprint ground pad's outer diameter.
            self.assertAlmostEqual(objects(payload, purpose="ground-disc")[0]["diameter"], 3.175)
            # A front-drawn source is not mirrored: back mounting needs its own.
            with self.assertRaisesRegex(rf.RFError, "selected mount side"):
                read_base_part(path, side="back", approved=True)

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
            # A warning is shown in the report and does not block publication.
            self.assertIn("1 warnings", dialog.report.GetValue())
            self.assertIn("WARNING · signal via: annular ring", dialog.report.GetValue())
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
