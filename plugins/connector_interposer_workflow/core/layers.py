"""Persistable class-by-layer policy; additions default allowed without resets."""
from dataclasses import dataclass, field
import re
from .model import Issue


def _single_number(name):
    runs = re.findall(r'\d+', str(name))
    return int(runs[0]) if len(runs) == 1 else None


def suggest_layer_mappings(netlist_layers, copper_layers):
    """Return unambiguous numeric matches between netlist and board layers.

    KiCad's default inner-layer names are zero-offset from the physical copper
    layer number: In1.Cu is physical layer 2. Custom names use their single
    embedded number directly.
    """
    copper_layers = tuple(copper_layers)
    by_number = {}
    for layer in copper_layers:
        folded = str(layer).casefold()
        if folded == 'f.cu':
            number = 1
        elif folded == 'b.cu':
            number = len(copper_layers)
        else:
            default_inner = re.fullmatch(r'in(\d+)\.cu', folded)
            number = int(default_inner.group(1)) + 1 if default_inner else _single_number(layer)
        if number is not None:
            by_number.setdefault(number, []).append(layer)

    suggestions, unresolved = {}, {}
    for source in netlist_layers:
        number = _single_number(source)
        candidates = by_number.get(number, ()) if number is not None else ()
        if len(candidates) == 1:
            suggestions[source] = candidates[0]
        else:
            unresolved[source] = tuple(candidates)
    return suggestions, unresolved

@dataclass
class LayerRules:
    permissions: dict = field(default_factory=dict)

    def synchronize(self, netclasses, copper_layers):
        added = []
        for netclass in sorted(set(netclasses)):
            row = self.permissions.setdefault(netclass, {})
            for layer in copper_layers:
                if layer not in row:
                    row[layer] = True
                    added.append((netclass, layer))
        return tuple(added)

    def allowed(self, netclass, layer):
        return self.permissions.get(netclass, {}).get(layer, True)

    def to_dict(self):
        return {c: dict(layers) for c, layers in self.permissions.items()}


def validate_layers(records, layer_mapping, copper_layers, rules=None):
    issues = []
    rules = rules or LayerRules()
    for r in records:
        layer = layer_mapping.get(r.layer)
        if layer is None:
            issues.append(Issue("layer_unmapped", f"Map architecture layer {r.layer}", r.source_row, "layer"))
        elif layer not in copper_layers:
            issues.append(Issue("layer_missing", f"Mapped layer {layer} is absent from the board copper stackup", r.source_row, "layer"))
        elif not rules.allowed(r.netclass, layer):
            issues.append(Issue("layer_disallowed", f"Layer rule rejects {r.netclass} on {layer}", r.source_row, "layer"))
    return tuple(issues)
