"""Deterministic interposer microvia size eligibility and stack topology."""
from dataclasses import dataclass
from functools import lru_cache
import math


@dataclass(frozen=True)
class MicroviaPreset:
    diameter: float
    drill: float
    eligible: bool = True
    reason: str = ""


def _positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def minimum_pitch(points):
    """Return the minimum distance between physical electrical pad centres."""
    points = tuple(tuple(float(v) for v in point) for point in points)
    if any(len(point) != 2 or not all(math.isfinite(v) for v in point) for point in points):
        raise ValueError("Interposer pad centres must be finite XY pairs")
    return _minimum_pitch(points)


@lru_cache(maxsize=8)
def _minimum_pitch(points):
    # Sweep in X order: a later point cannot be the nearest neighbour once its
    # X distance alone reaches the best separation found so far.
    ordered = sorted(points)
    best = math.inf
    count = len(ordered)
    for index, first in enumerate(ordered):
        for other in range(index + 1, count):
            second = ordered[other]
            if second[0] - first[0] >= best:
                break
            distance = math.dist(first, second)
            if distance < best:
                best = distance
    return best


def check_preset(diameter, drill, *, min_diameter=0, min_drill=0,
                 min_clearance=0, pad_centres=()):
    """Return ``(eligible, reason)`` for one anonymous KiCad size pair."""
    if not _positive(diameter) or not _positive(drill):
        return False, "diameter and drill must be positive"
    if drill >= diameter:
        return False, "laser drill must be smaller than the copper diameter"
    if diameter + 1e-12 < min_diameter:
        return False, "copper diameter is below the board microvia minimum"
    if drill + 1e-12 < min_drill:
        return False, "laser drill is below the board microvia minimum"
    pitch = minimum_pitch(pad_centres)
    if pitch - diameter + 1e-12 < min_clearance:
        return False, "does not maintain the board minimum copper spacing at this interposer pitch"
    return True, ""


def evaluate_presets(presets, *, min_diameter=0, min_drill=0,
                     min_clearance=0, pad_centres=()):
    """Deduplicate and classify Board Setup diameter/drill pairs."""
    result = []
    seen = set()
    for diameter, drill in presets:
        pair = (round(float(diameter), 9), round(float(drill), 9))
        if pair in seen or pair == (0.0, 0.0):
            continue
        seen.add(pair)
        eligible, reason = check_preset(
            *pair, min_diameter=min_diameter, min_drill=min_drill,
            min_clearance=min_clearance, pad_centres=pad_centres,
        )
        result.append(MicroviaPreset(*pair, eligible, reason))
    return tuple(result)


def stack_plan(copper_layers, side, signal_layer=None):
    """Return ``(role, top_layer, bottom_layer)`` adjacent physical spans.

    ``signal_layer=None`` produces a full-depth ground stack. Signal stacks run
    from the mount side to the routing layer, omit the next span, and continue
    as ground through any remaining layers.
    """
    layers = tuple(copper_layers)
    if len(layers) < 2 or len(set(layers)) != len(layers) or any(not layer for layer in layers):
        raise ValueError("Microvias require at least two distinct copper layers")
    if side not in ("F", "B"):
        raise ValueError("Interposer mounting side must be Front or Back")
    ordered = layers if side == "F" else tuple(reversed(layers))
    physical_index = {layer: index for index, layer in enumerate(layers)}

    def physical_pair(first, second):
        return ((first, second) if physical_index[first] < physical_index[second]
                else (second, first))

    if signal_layer is None:
        return tuple(("ground", *physical_pair(first, second))
                     for first, second in zip(ordered, ordered[1:]))
    if signal_layer not in ordered:
        raise ValueError("Signal routing layer is absent from the copper stack")
    route = ordered.index(signal_layer)
    if route in (0, len(ordered) - 1):
        raise ValueError("Interposer signals cannot route on an outside copper layer")
    result = [("signal", *physical_pair(ordered[index], ordered[index + 1]))
              for index in range(route)]
    result.extend(("ground", *physical_pair(ordered[index], ordered[index + 1]))
                  for index in range(route + 1, len(ordered) - 1))
    return tuple(result)
