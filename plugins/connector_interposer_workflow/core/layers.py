"""Persistable class-by-layer policy; additions default allowed without resets."""
from dataclasses import dataclass, field
from .model import Issue

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
