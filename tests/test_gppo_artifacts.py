"""Standalone artifact consumer rejects corrupt/unknown declarative semantics."""
import copy
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins"))
from gppo_interposer_workflow.artifacts import ArtifactError, canonical, pin_artifact, sha256, validate_artifact


def artifact_fixture(part_sha256="a"*64):
    artifact = {"schema": "gppo.artifact/1.0", "artifact_id": "approved-sample", "revision": 1,
                "kind": "rf-transition", "payload_schema": "gppo.rf/1.0", "recipe": {"opaque": "never execute"},
                "compatibility": {"part_id": "Approved:GPPO", "part_sha256": part_sha256,
                                  "mount": "SMD", "side": "front", "netclasses": ["RF"],
                                  "stackup": ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]},
                "required_capabilities": ["native-vias", "net-copper-shapes", "layer-rule-areas"],
                "payload": {"units": "mm", "coordinates": "x-right-y-up-ccw",
                            "net_roles": ["signal", "ground"], "anchors": {"signal": [0, 0], "exit_direction_deg": 0},
                            "layer_roles": {"mount": "F.Cu", "signal": "In2.Cu"}, "objects": [
                    {"id": "signal-via", "type": "via", "net": "signal", "at": [0, 0], "diameter": .6,
                     "drill": .3, "via_type": "through", "layers": ["F.Cu", "B.Cu"]},
                    {"id": "signal-land", "type": "copper", "shape": "circle", "net": "signal",
                     "at": [0, 0], "diameter": .8, "layer": "F.Cu"},
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

    def test_integrity_protects_metadata_and_payload(self):
        for field in ("artifact_id", "recipe", "payload"):
            artifact = artifact_fixture()
            artifact[field] = "tampered"
            with self.subTest(field=field), self.assertRaises(ArtifactError):
                validate_artifact(artifact)

    def test_unsupported_capability_type_and_microvia_span(self):
        for change in ("capability", "track", "microvia"):
            artifact = artifact_fixture()
            if change == "capability":
                artifact["required_capabilities"].append("unknown")
            elif change == "track":
                artifact["payload"]["objects"][0]["type"] = "track"
            else:
                artifact["payload"]["objects"][0]["via_type"] = "microvia"
            rehash(artifact)
            with self.subTest(change=change), self.assertRaises(ArtifactError):
                validate_artifact(artifact, layers=["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"])

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
                      "diameter": .6, "drill": .3, "via_type": "through", "layers": ["F.Cu", "B.Cu"]}
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
