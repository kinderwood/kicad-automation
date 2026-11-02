"""Filesystem recovery invariants, including interrupted apply and rollback."""
import json
import sys
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins"))
from gppo_interposer_workflow.recovery import RecoveryError, apply_files, file_hash, recover


class RecoveryTests(unittest.TestCase):
    def test_real_process_exit_journal_recovery(self):
        script = """
import os,sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from gppo_interposer_workflow.recovery import apply_files,file_hash
root=Path(sys.argv[2]); boundary=int(sys.argv[3])
files={name: ('after '+name).encode() for name in ('board.kicad_pcb','board.kicad_pro','instances.json')}
expected={name:file_hash(root/name) for name in files}
def stop(index):
    if index==boundary: os._exit(77)
apply_files(root,files,expected,closed_project=True,boundary=stop)
"""
        plugin_dir = str(Path(__file__).resolve().parents[1]/"plugins")
        for boundary in range(4):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                before = {name: ("before "+name).encode() for name in ("board.kicad_pcb", "board.kicad_pro", "instances.json")}
                for name, data in before.items():
                    (root/name).write_bytes(data)
                child = subprocess.run([sys.executable,"-c",script,plugin_dir,directory,str(boundary)],
                                       env=dict(os.environ, GPPO_WORKFLOW_WORKER="1"), capture_output=True)
                self.assertEqual(77, child.returncode, child.stderr.decode())
                journal = next(root.glob(".gppo-recovery/*/journal.json"))
                recover(journal, closed_project=True)
                self.assertEqual(before, {name: (root/name).read_bytes() for name in before})

    def test_all_boundaries_and_interrupted_restore(self):
        for stop in range(4):
            with self.subTest(boundary=stop), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                files = {"board.kicad_pcb": b"after board", "board.kicad_pro": b"after pro", "registry.json": b"after ledger"}
                for name in files:
                    (root/name).write_bytes(b"before " + name.encode())
                expected = {name: file_hash(root/name) for name in files}
                def crash(index):
                    if index == stop:
                        raise RuntimeError("interrupted")
                with self.assertRaises(RuntimeError):
                    apply_files(root, files, expected, closed_project=True, boundary=crash)
                journal = next(root.glob(".gppo-recovery/*/journal.json"))
                with self.assertRaises(RuntimeError):
                    recover(journal, closed_project=True, boundary=lambda index: (_ for _ in ()).throw(RuntimeError()) if index == 1 else None)
                self.assertEqual("RESTORED", recover(journal, closed_project=True)["status"])
                self.assertEqual(expected, {name: file_hash(root/name) for name in files})

    def test_conflict_and_bad_backup_preflight_before_any_restore(self):
        for mode in ("later-edit", "corrupt-backup", "missing-backup"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root/"a").write_bytes(b"a0")
                (root/"b").write_bytes(b"b0")
                result = apply_files(root, {"a": b"a1", "b": b"b1"}, {"a": file_hash(root/"a"), "b": file_hash(root/"b")}, closed_project=True)
                journal = Path(result["journal"])
                if mode == "later-edit":
                    (root/"b").write_bytes(b"designer")
                else:
                    backup = journal.parent / json.loads(journal.read_text())["files"]["b"]["backup"]
                    backup.unlink() if mode == "missing-backup" else backup.write_bytes(b"damage")
                before = {name: (root/name).read_bytes() for name in ("a", "b")}
                with self.assertRaises(RecoveryError):
                    recover(journal, closed_project=True)
                self.assertEqual(before, {name: (root/name).read_bytes() for name in before})

    def test_new_file_restore_and_noop_mtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/"old").write_bytes(b"unchanged")
            mtime = (root/"old").stat().st_mtime_ns
            noop = apply_files(root, {"old": b"unchanged"}, {"old": file_hash(root/"old")}, closed_project=True)
            self.assertEqual("NOOP", noop["status"])
            self.assertEqual(mtime, (root/"old").stat().st_mtime_ns)
            result = apply_files(root, {"new": b"created"}, {"new": None}, closed_project=True)
            recover(result["journal"], closed_project=True)
            self.assertFalse((root/"new").exists())

    def test_lock_and_escape_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(RecoveryError):
                apply_files(root, {"../escape": b"x"}, {"../escape": None}, closed_project=True)
            (root/"~board.kicad_pcb.lck").write_text("lock")
            with self.assertRaises(RecoveryError):
                apply_files(root, {"board": b"x"}, {"board": None}, closed_project=True)

    def test_owned_obsolete_file_deletion_can_be_restored(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root/"obsolete.kicad_sch"
            path.write_bytes(b"owned obsolete sheet")
            result = apply_files(root, {path.name: None}, {path.name: file_hash(path)}, closed_project=True)
            self.assertFalse(path.exists())
            recover(result["journal"], closed_project=True)
            self.assertEqual(b"owned obsolete sheet", path.read_bytes())
