"""Project-scoped identities and complete authoritative snapshot differences."""
import hashlib
import json
import uuid
from dataclasses import dataclass


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def stable_uuid(project_id, key):
    """The project UUID is persisted once; row order/net changes never seed IDs."""
    return str(uuid.uuid5(uuid.UUID(str(project_id)), str(key)))


@dataclass(frozen=True)
class SnapshotDiff:
    added: tuple
    changed: tuple
    removed: tuple
    unchanged: tuple


def diff_snapshots(old, new):
    def index(records):
        result = {}
        for record in records:
            if record.key in result:
                raise ValueError(f"Duplicate physical connector {record.key}")
            result[record.key] = record
        return result
    old, new = index(old), index(new)
    return SnapshotDiff(tuple(sorted(new.keys()-old.keys())), tuple(sorted(k for k in old.keys() & new.keys() if old[k] != new[k])), tuple(sorted(old.keys()-new.keys())), tuple(sorted(k for k in old.keys() & new.keys() if old[k] == new[k])))
