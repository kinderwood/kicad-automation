"""Linear-dimension parsing with millimetres as the workflow storage unit."""
import math
import re


DIMENSION_UNITS = ("mm", "mil", "inch", "um")
UNIT_SCALE_MM = {
    "mm": 1.0,
    "mil": 0.0254,
    "inch": 25.4,
    "um": 0.001,
}

_UNIT_ALIASES = {
    "mm": "mm",
    "millimeter": "mm",
    "millimeters": "mm",
    "millimetre": "mm",
    "millimetres": "mm",
    "mil": "mil",
    "mils": "mil",
    "thou": "mil",
    "inch": "inch",
    "inches": "inch",
    "in": "inch",
    '"': "inch",
    "um": "um",
    "µm": "um",
    "μm": "um",
    "micron": "um",
    "microns": "um",
}

_DIMENSION = re.compile(
    r"^\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*([^\d\s].*?)?\s*$"
)


def normalize_unit(unit):
    """Return the public unit name for a supported spelling."""
    normalized = str(unit).strip().casefold().replace("μ", "µ")
    try:
        return _UNIT_ALIASES[normalized]
    except KeyError as error:
        raise ValueError("Units must be mm, mil, inch or um") from error


def parse_dimension(text, default_unit="mm"):
    """Parse a finite linear dimension and return (millimetres, unit, number text)."""
    match = _DIMENSION.fullmatch(str(text))
    if not match:
        raise ValueError("Enter a number, optionally followed by mm, mil, inch or um")
    number_text, suffix = match.groups()
    value = float(number_text)
    if not math.isfinite(value):
        raise ValueError("Dimension must be finite")
    unit = normalize_unit(suffix if suffix else default_unit)
    return value * UNIT_SCALE_MM[unit], unit, number_text


def dimension_text(value_mm, unit="mm"):
    """Format a millimetre value in the requested display unit."""
    unit = normalize_unit(unit)
    value = float(value_mm) / UNIT_SCALE_MM[unit]
    if not math.isfinite(value):
        raise ValueError("Dimension must be finite")
    return f"{value:.12g}"
