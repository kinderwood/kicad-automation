"""Conservative electrical-type inference and connector classification."""
import re


VALID_TYPES = ("RF", "DC")


def reference_type(reference):
    """Return the project naming-convention type, or None when ambiguous."""
    value = str(reference).strip()
    if re.fullmatch(r"J[0-9]", value, re.IGNORECASE):
        return "DC"
    if re.fullmatch(r"J[0-9]{3}", value, re.IGNORECASE):
        return "RF"
    return None


def suggest_netclass_types(records):
    """Infer only unanimous types; report contradictory evidence separately."""
    evidence = {}
    for record in records:
        values = evidence.setdefault(record.netclass, set())
        if record.netclass.strip().casefold() == "dc":
            values.add("DC")
        inferred = reference_type(record.connector)
        if inferred:
            values.add(inferred)
    suggestions = {name: next(iter(values)) for name, values in evidence.items()
                   if len(values) == 1}
    conflicts = {name: tuple(sorted(values)) for name, values in evidence.items()
                 if len(values) > 1}
    unresolved = tuple(sorted(name for name, values in evidence.items() if not values))
    return suggestions, conflicts, unresolved


def connector_type(records, netclass_types, reference):
    """Resolve one connector only when every attached class has one valid type."""
    values = {netclass_types.get(record.netclass) for record in records
              if record.connector == reference}
    if None in values or not values or not values <= set(VALID_TYPES) or len(values) != 1:
        return None
    return next(iter(values))
