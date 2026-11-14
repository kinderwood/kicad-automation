"""Standalone artifact consumer rejects corrupt/unknown declarative semantics."""
import copy
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins"))
from connector_interposer_workflow.artifacts import ArtifactError, canonical, load_artifact, pin_artifact, sha256, validate_artifact
from connector_interposer_workflow import artifacts
from unittest import mock


def artifact_fixture(part_sha256="a"*64):
    artifact = {"schema": "gppo.artifact/2.0", "artifact_id": "approved-sample", "revision": 1,
                "kind": "rf-transition", "payload_schema": "gppo.rf/2.0", "recipe": {"opaque": "never execute"},
                "compatibility": {"part_id": "Approved:GPPO", "part_sha256": part_sha256,
                                  "mount": "SMD", "side": "front", "netclasses": ["RF"],
                                  "copper_layer_count": 4},
                "required_capabilities": ["native-vias", "net-copper-shapes", "layer-rule-areas",
                                          "native-tracks", "filled-vias"],
                "payload": {"units": "mm", "coordinates": "x-right-y-up-ccw",
                            "net_roles": ["signal", "ground"],
                            "anchors": {"signal": [0, 0], "exit_direction_deg": 0, "handoff": [2, 0]},
                            "layer_roles": {"mount": "F.Cu", "signal": "In2.Cu"}, "objects": [
                    {"id": "signal-via", "type": "via", "net": "signal", "at": [0, 0], "diameter": .6,
                     "drill": .3, "via_type": "microvia", "layers": ["F.Cu", "In1.Cu"], "fill": "copper"},
                    {"id": "signal-via-2", "type": "via", "net": "signal", "at": [0, 0], "diameter": .6,
                     "drill": .3, "via_type": "microvia", "layers": ["In1.Cu", "In2.Cu"], "fill": "copper"},
                    {"id": "signal-land", "type": "copper", "shape": "circle", "net": "signal",
                     "at": [0, 0], "diameter": .8, "layer": "F.Cu"},
                    {"id": "ground-disc", "type": "copper", "shape": "circle", "purpose": "ground-disc",
                     "net": "ground", "at": [0, 0], "diameter": 3, "layer": "B.Cu"},
                    {"id": "exit-trace", "type": "track", "net": "signal", "layer": "In2.Cu",
                     "width": .25, "start": [0, 0], "end": [2, 0]},
                    {"id": "exclusion", "type": "keepout", "shape": "polygon", "net": "ground",
                     "layers": ["In2.Cu"], "points": [[0, -.2], [2, -.2], [2, .2], [0, .2]], "prohibit": ["copperpour"]}]}}
    return rehash(artifact)


def rehash(artifact):
    artifact.pop("hashes", None)
    hashes = {"payload": sha256(artifact["payload"]), "recipe": sha256(artifact["recipe"]), "artifact": sha256(artifact)}
    artifact["hashes"] = hashes
    return artifact


class ArtifactTests(unittest.TestCase):
    def test_supported_payload_opaque_recipe_and_pin(self):
        artifact = artifact_fixture()
        self.assertEqual(artifact, validate_artifact(artifact, layers=["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]))
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory)/"library.json", Path(directory)/"project"/"pinned.json"
            source.write_bytes(canonical(artifact))
            before = source.stat().st_mtime_ns
            pin_artifact(source, target)
            stamp = target.stat().st_mtime_ns
            pin_artifact(source, target)
            self.assertEqual(stamp, target.stat().st_mtime_ns)
            self.assertEqual(before, source.stat().st_mtime_ns)

    def test_unchanged_bytes_are_validated_once_and_changed_bytes_again(self):
        layers = ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "library.json"
            source.write_bytes(canonical(artifact_fixture()))
            artifacts._VALIDATED.clear()
            with mock.patch.object(artifacts, "validate_artifact",
                                   wraps=artifacts.validate_artifact) as validate:
                first = load_artifact(source, layers=layers)
                first["artifact_id"] = "caller edits never reach the next reader"
                self.assertEqual(load_artifact(source, layers=layers)["artifact_id"],
                                 "approved-sample")
                self.assertEqual(validate.call_count, 1)
                # Other checks are a different question about the same bytes.
                with self.assertRaises(ArtifactError):
                    load_artifact(source, layers=layers, netclass="OTHER")
                with self.assertRaises(ArtifactError):
                    load_artifact(source, layers=layers, netclass="OTHER")
                self.assertEqual(validate.call_count, 2)
                tampered = artifact_fixture()
                tampered["artifact_id"] = "tampered"
                source.write_bytes(canonical(tampered))
                with self.assertRaises(ArtifactError):
                    load_artifact(source, layers=layers)
                self.assertEqual(validate.call_count, 3)

    def test_integrity_protects_metadata_and_payload(self):
        for field in ("artifact_id", "recipe", "payload"):
            artifact = artifact_fixture()
            artifact[field] = "tampered"
            with self.subTest(field=field), self.assertRaises(ArtifactError):
                validate_artifact(artifact)

    def test_unsupported_capability_type_and_microvia_span(self):
        for change in ("capability", "primitive", "microvia", "blind", "unfilled",
                       "treatment", "old-envelope", "old-payload"):
            artifact = artifact_fixture()
            via = artifact["payload"]["objects"][0]
            if change == "capability":
                artifact["required_capabilities"].append("unknown")
            elif change == "primitive":
                via["type"] = "pad"
            elif change == "microvia":
                via["layers"] = ["F.Cu", "In2.Cu"]
            elif change == "blind":
                via.update(via_type="blind_buried", layers=["F.Cu", "In2.Cu"])
            elif change == "unfilled":
                del via["fill"]
            elif change == "treatment":
                via["fill"] = "epoxy"
            elif change == "old-envelope":
                artifact["schema"] = "gppo.artifact/1.0"
            else:
                artifact["payload_schema"] = "gppo.rf/1.0"
            rehash(artifact)
            with self.subTest(change=change), self.assertRaises(ArtifactError):
                validate_artifact(artifact, layers=["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"])

    def test_exit_trace_handoff_and_ground_disc_are_required(self):
        def objects(artifact, identity):
            return next(x for x in artifact["payload"]["objects"] if x["id"] == identity)
        for change in ("no-trace", "two-traces", "wrong-layer", "ground-trace", "short-trace",
                       "offset-start", "no-handoff", "no-disc", "disc-layer", "disc-net"):
            artifact = artifact_fixture()
            payload = artifact["payload"]
            trace = objects(artifact, "exit-trace")
            disc = objects(artifact, "ground-disc")
            if change == "no-trace":
                payload["objects"].remove(trace)
            elif change == "two-traces":
                payload["objects"].append(dict(trace, id="second"))
            elif change == "wrong-layer":
                trace["layer"] = "In1.Cu"
            elif change == "ground-trace":
                trace["net"] = "ground"
            elif change == "short-trace":
                trace["end"] = [1.5, 0]
            elif change == "offset-start":
                trace["start"] = [.1, 0]
            elif change == "no-handoff":
                del payload["anchors"]["handoff"]
            elif change == "no-disc":
                payload["objects"].remove(disc)
            elif change == "disc-layer":
                disc["layer"] = "In1.Cu"
            else:
                disc["net"] = "signal"
            rehash(artifact)
            with self.subTest(change=change), self.assertRaises(ArtifactError):
                validate_artifact(artifact)
        # A through-hole pin needs no ground disc: its barrel passes every layer.
        artifact = artifact_fixture()
        artifact["compatibility"]["mount"] = "PTH"
        artifact["payload"]["objects"] = [x for x in artifact["payload"]["objects"]
                                          if x["id"] not in ("ground-disc", "signal-via", "signal-via-2")]
        validate_artifact(rehash(artifact))

    def test_stackup_identity_is_the_copper_layer_count(self):
        artifact = artifact_fixture()
        validate_artifact(artifact, layers=["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"])
        # Any four-layer board accepts a four-layer transition.
        validate_artifact(artifact, layers=["top", "gnd", "sig", "bottom"])
        for layers in (["F.Cu", "B.Cu"], ["F.Cu", "In1.Cu", "In2.Cu", "In3.Cu", "In4.Cu", "B.Cu"]):
            with self.subTest(count=len(layers)), self.assertRaisesRegex(ArtifactError, "layer count"):
                validate_artifact(artifact, layers=layers)
        for count in (3, 0, 34, "4", True):
            bad = artifact_fixture()
            bad["compatibility"]["copper_layer_count"] = count
            with self.subTest(count=count), self.assertRaises(ArtifactError):
                validate_artifact(rehash(bad))

    def test_required_compatibility_and_non_simple_polygons(self):
        for mode in ("compatibility", "roles", "crossing", "collinear"):
            artifact = artifact_fixture()
            if mode == "compatibility":
                del artifact["compatibility"]
            elif mode == "roles":
                del artifact["payload"]["layer_roles"]
            elif mode == "crossing":
                artifact["payload"]["objects"][-1]["points"] = [[0,0],[2,2],[0,2],[2,0]]
            else:
                artifact["payload"]["objects"][-1]["points"] = [[0,0],[1,0],[2,0]]
            rehash(artifact)
            with self.subTest(mode=mode), self.assertRaises(ArtifactError):
                validate_artifact(artifact)

    def test_tangency_intrusion_and_layer_specific_stack(self):
        for mode in ("tangent", "intrusion", "above-signal"):
            artifact = artifact_fixture()
            area = artifact["payload"]["objects"][-1]
            area["purpose"] = "exit-corridor"
            ground = {"id": "ground", "type": "via", "net": "ground", "at": [1, .5],
                      "diameter": .6, "drill": .3, "via_type": "through", "layers": ["F.Cu", "B.Cu"],
                      "fill": "copper"}
            if mode == "intrusion":
                ground["at"][1] -= .01
            elif mode == "above-signal":
                ground.update(at=[1, 0], via_type="microvia", layers=["F.Cu", "In1.Cu"])
            artifact["payload"]["objects"].append(ground)
            rehash(artifact)
            if mode == "intrusion":
                with self.assertRaises(ArtifactError):
                    validate_artifact(artifact)
            else:
                validate_artifact(artifact)
