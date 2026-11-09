"""Guarded multi-file updates and persistent project recovery journals.

Prepared live-project journals may be published after in-memory PCB
reconciliation. Restoring a journal always requires the project to be closed.
"""
import hashlib
import json
import os
import tempfile
from pathlib import Path


class RecoveryError(ValueError):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_hash(path):
    path = Path(path)
    return digest(path.read_bytes()) if path.is_file() else None


def _write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".connector-interposer-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _safe(root, name):
    relative = Path(name)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise RecoveryError("Unsafe project-relative path")
    target = root / relative
    if any(p.is_symlink() for p in [target, *target.parents] if p != root.parent):
        raise RecoveryError("Symlinks are not permitted in guarded project files")
    if root not in target.resolve().parents:
        raise RecoveryError("Project path escapes root")
    return target


def require_closed(root, closed_project):
    root = Path(root).resolve()
    if not closed_project:
        raise RecoveryError("This operation requires an explicitly closed project")
    if list(root.glob("*.lck")) or list(root.glob("~*.lck")):
        raise RecoveryError("KiCad lock file present; close the project first")
    return root


def require_schematic_closed(root):
    """Allow the active PCB lock, but reject any open schematic editor."""
    root = Path(root).resolve()
    locks = [path for path in root.glob('*.lck')
             if '.kicad_sch' in path.name]
    locks += [path for path in root.glob('~*.lck')
              if '.kicad_sch' in path.name]
    if locks:
        raise RecoveryError('Close the Schematic Editor before applying Workflow changes')
    return root


def prepare_files(project_root, files, expected, *, closed_project=False,
                  live_project=False, journal_dir=None):
    """Durably stage every before/after image without changing project files."""
    root = (Path(project_root).resolve() if live_project else
            require_closed(project_root, closed_project))
    if set(files) != set(expected):
        raise RecoveryError("Expected hashes must cover exactly the file set")
    targets = {name: _safe(root, name) for name in files}
    if any(file_hash(path) != expected[name] for name, path in targets.items()):
        raise RecoveryError("Project changed after preview")
    changed = {n: (bytes(b) if b is not None else None) for n, b in files.items()
               if (digest(bytes(b)) if b is not None else None) != expected[n]}
    if not changed:
        return {"status": "NOOP", "journal": None, "changed": []}
    directory = Path(journal_dir) if journal_dir else root / ".connector-interposer-recovery"
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="transaction-", dir=str(directory)))
    entries = {}
    for index, (name, content) in enumerate(sorted(changed.items())):
        backup = str(index) + ".before"
        if expected[name] is not None:
            _write(run / backup, targets[name].read_bytes())
        staged = str(index) + ".after"
        if content is not None:
            _write(run / staged, content)
        entries[name] = {
            "before": expected[name],
            "after": digest(content) if content is not None else None,
            "backup": backup, "staged": staged,
        }
    journal = {
        "schema": "connector-interposer.recovery/2.0", "root": str(root),
        "mode": "live" if live_project else "closed-copy",
        "status": "PREPARED", "files": entries,
    }
    location = run / "journal.json"
    _write(location, json.dumps(journal, sort_keys=True, indent=2).encode())
    return {"status": "PREPARED", "journal": str(location),
            "changed": sorted(changed)}


def commit_prepared(journal_path, *, live_project=False, boundary=None):
    """Publish one prepared journal after repeating its all-file preflight."""
    location = Path(journal_path).resolve()
    journal = json.loads(location.read_text())
    if journal.get('schema') != 'connector-interposer.recovery/2.0':
        raise RecoveryError('Unsupported recovery journal')
    if journal.get('status') != 'PREPARED':
        raise RecoveryError('Recovery journal is not prepared for application')
    if (journal.get('mode') == 'live') != bool(live_project):
        raise RecoveryError('Recovery journal publication mode does not match its preparation')
    root = (Path(journal['root']).resolve() if live_project else
            require_closed(journal['root'], True))
    entries = journal['files']
    targets = {name: _safe(root, name) for name in entries}
    for name, entry in entries.items():
        staged = _safe(location.parent, entry['staged'])
        if entry['after'] is not None and file_hash(staged) != entry['after']:
            raise RecoveryError('Missing or damaged staged file; nothing applied')
        if file_hash(targets[name]) != entry['before']:
            raise RecoveryError('Project changed during preparation; nothing applied')
    for index, name in enumerate(sorted(entries), 1):
        entry = entries[name]
        if entry['after'] is None:
            targets[name].unlink()
        else:
            _write(targets[name], _safe(location.parent, entry['staged']).read_bytes())
        if boundary:
            boundary(index)
    journal['status'] = 'APPLIED'
    _write(location, json.dumps(journal, sort_keys=True, indent=2).encode())
    return {'status': 'APPLIED', 'journal': str(location),
            'changed': sorted(entries)}


def apply_files(project_root, files, expected, *, closed_project=False,
                journal_dir=None, boundary=None):
    """Replace reviewed files with all-file preflight and resumable rollback.

    ``files`` maps project-relative paths to exact bytes (None deletes an owned
    obsolete file). ``expected`` records
    the reviewed current SHA-256 (None for a new file). A boundary callback is
    useful for progress and interruption testing; exceptions leave the journal.
    """
    transaction = prepare_files(
        project_root, files, expected, closed_project=closed_project,
        journal_dir=journal_dir,
    )
    if transaction['status'] == 'NOOP':
        return transaction
    if boundary:
        boundary(0)
    return commit_prepared(
        transaction['journal'], live_project=False, boundary=boundary,
    )


def recover(journal_path, *, closed_project=False, boundary=None):
    """Restore pre-state only if every file and every backup passes preflight."""
    location = Path(journal_path).resolve()
    journal = json.loads(location.read_text())
    if journal.get("schema") != "connector-interposer.recovery/2.0":
        raise RecoveryError("Unsupported recovery journal")
    root = require_closed(journal["root"], closed_project)
    entries = journal["files"]
    targets = {name: _safe(root, name) for name in entries}
    backups = {}
    for name, entry in entries.items():
        backup = _safe(location.parent, entry["backup"])
        if entry["before"] is not None:
            if file_hash(backup) != entry["before"]:
                raise RecoveryError("Missing or damaged backup; no files restored")
            backups[name] = backup.read_bytes()
        if file_hash(targets[name]) not in (entry["before"], entry["after"]):
            raise RecoveryError("Later project edit conflicts with recovery; no files restored")
    for index, name in enumerate(sorted(entries), 1):
        entry = entries[name]
        if file_hash(targets[name]) != entry["before"]:
            if entry["before"] is None:
                targets[name].unlink()
            else:
                _write(targets[name], backups[name])
        if boundary:
            boundary(index)
    journal["status"] = "RESTORED"
    _write(location, json.dumps(journal, sort_keys=True, indent=2).encode())
    return {"status": "RESTORED", "files": sorted(entries)}
