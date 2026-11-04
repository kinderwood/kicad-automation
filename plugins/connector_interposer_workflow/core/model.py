"""Immutable, GUI-independent workflow records and validation diagnostics."""
from dataclasses import dataclass, field
import re

HEADERS = ("Signal", "NetClass", "Interposer", "Interposer Pin", "Connector", "Connector Pin", "Layer")
FIELDS = ("signal", "netclass", "interposer", "interposer_pin", "connector", "connector_pin", "layer")

@dataclass(frozen=True)
class Issue:
    code: str
    message: str
    source_row: int | None = None
    field: str | None = None
    severity: str = "error"

@dataclass(frozen=True)
class SignalRecord:
    signal: str
    netclass: str
    interposer: str
    interposer_pin: str
    connector: str
    connector_pin: str
    layer: str
    source_row: int = field(default=0, compare=False)

    @property
    def key(self):
        return f"{self.connector}-{self.connector_pin}"

    def electrical_dict(self):
        return {name: getattr(self, name) for name in FIELDS}


def validate_records(records, pad_universes=None):
    """Validate full-snapshot identity; unused approved pads are deliberately GND."""
    issues, seen = [], {name: {} for name in ("connector", "signal", "destination")}
    for r in records:
        for name in FIELDS:
            if not getattr(r, name).strip():
                issues.append(Issue("required", f"{name} is required", r.source_row, name))
        if not re.fullmatch(r"[1-9][0-9]*", r.connector_pin):
            issues.append(Issue("connector_pin", "Connector Pin must be a positive integer", r.source_row, "connector_pin"))
        for name, key in (("connector", r.key), ("signal", r.signal), ("destination", (r.interposer, r.interposer_pin))):
            if key in seen[name]:
                issues.append(Issue("duplicate_"+name, f"Duplicate {name} {key}; first seen on row {seen[name][key]}", r.source_row))
            else:
                seen[name][key] = r.source_row
        if pad_universes is not None:
            if r.interposer not in pad_universes:
                issues.append(Issue("interposer_missing", f"No approved interposer selected for {r.interposer}", r.source_row))
            elif r.interposer_pin not in pad_universes[r.interposer]:
                issues.append(Issue("pad_missing", f"{r.interposer} has no electrical pad {r.interposer_pin}", r.source_row, "interposer_pin"))
    return tuple(issues)
