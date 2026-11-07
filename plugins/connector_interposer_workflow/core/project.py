"""Merge exact owned-net assignments into native KiCad project settings."""
import copy


def merge_project_settings(existing, records, previously_owned_nets=()):
    """Return a new project dict; preserve designer rules and class dimensions.

    New net settings use schema 4; existing KiCad 10 schema 5 is retained. Every
    class entry needs an explicit priority. New classes
    intentionally have no dimensional overrides, inheriting the project's
    Default class. If no saved Default exists, KiCad retains its native factory
    Default; a dimensionless synthetic Default must not replace it.

    Only explicitly declared previously-owned exact assignments may be removed.
    Existing wildcard rules and assignments for other nets are never rewritten.
    """
    if existing is None:
        existing = {}
    if not isinstance(existing, dict):
        raise ValueError('KiCad project settings must be an object')
    result = copy.deepcopy(existing)
    settings = result.setdefault('net_settings', {})
    if not isinstance(settings, dict):
        raise ValueError('KiCad net_settings must be an object')
    meta = settings.setdefault('meta', {})
    if not isinstance(meta, dict):
        raise ValueError('KiCad net_settings metadata must be an object')
    version = meta.get('version', 4)
    if type(version) is not int or version > 5 or version < 0:
        raise ValueError('Unsupported native net_settings schema')
    classes = settings.setdefault('classes', [])
    if not isinstance(classes, list) or any(not isinstance(c, dict) or not isinstance(c.get('name'), str) for c in classes):
        raise ValueError('KiCad netclass definitions must be named objects')
    by_name = {}
    priority = 0
    for entry in classes:
        name = entry['name']
        if name in by_name:
            raise ValueError(f'Duplicate saved netclass {name}')
        by_name[name] = entry
        entry.setdefault('priority', 2147483647 if name == 'Default' else priority)
        if type(entry['priority']) is not int:
            raise ValueError('KiCad netclass priority must be an integer')
        if name != 'Default':
            priority = max(priority, entry['priority']+1)
    records = tuple(records)
    for name in sorted({record.netclass for record in records}):
        if name not in by_name and name != 'Default':
            entry = {'name':name, 'priority':priority}
            classes.append(entry)
            by_name[name] = entry
            priority += 1
    assignments = settings.get('netclass_assignments')
    if assignments is None:
        assignments = {}
    if not isinstance(assignments, dict):
        raise ValueError('KiCad exact netclass assignments must be an object')
    # Migrate pre-v4 scalar assignments without dropping unrelated saved nets.
    for name, value in assignments.items():
        if isinstance(value, str):
            assignments[name] = [value] if value else []
        elif not isinstance(value, list) or any(not isinstance(v, str) for v in value):
            raise ValueError(f'Malformed exact netclass assignment for {name}')
    for net in previously_owned_nets:
        assignments.pop(net, None)
    for record in sorted(records, key=lambda row:row.signal):
        assignments[record.signal] = [record.netclass]
    settings['netclass_assignments'] = assignments
    settings.setdefault('netclass_patterns', [])
    meta['version'] = max(4, version)
    return result
