"""Versioned project configuration, kept separate from approved library sources."""
from dataclasses import asdict, dataclass, field
import uuid
from .identities import canonical_json
from .geometry import DEFAULT_FEATURE_TOLERANCE_MM, feature_tolerance
from .units import DIMENSION_UNITS

CONFIG_VERSION = 9


def _mechanical_defaults():
    return {
        'layer': 'Dwgs.User',
        'feature_tolerance_mm': DEFAULT_FEATURE_TOLERANCE_MM,
        'feature_tolerance_unit': 'mil',
        'accepted': False,
        'inspected_revision': '',
        'inspected_geometry': '',
    }

@dataclass
class ProjectConfig:
    project_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    schema_version: int = CONFIG_VERSION
    workbook: dict = field(default_factory=dict)
    mounting_holes: list = field(default_factory=list)
    presentation: str = 'labels'
    layer_mapping: dict = field(default_factory=dict)
    layer_rules: dict = field(default_factory=dict)
    netclass_types: dict = field(default_factory=dict)
    drafts: dict = field(default_factory=dict)
    gangs: dict = field(default_factory=dict)
    dc_connectors: dict = field(default_factory=dict)
    interposers: dict = field(default_factory=dict)
    microvias: dict = field(default_factory=dict)
    mechanical: dict = field(default_factory=_mechanical_defaults)
    outline: dict = field(default_factory=dict)
    artifacts: dict = field(default_factory=dict)
    instances: dict = field(default_factory=dict)
    tombstones: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)

    def to_json(self):
        return canonical_json(self.to_dict()) + '\n'

    @classmethod
    def from_dict(cls, value):
        if type(value.get('schema_version')) is not int or value['schema_version'] != CONFIG_VERSION:
            raise ValueError('Unsupported Workflow project schema version')
        uuid.UUID(value['project_id'])
        known = cls.__dataclass_fields__
        if set(value)-set(known):
            raise ValueError('Unknown project configuration fields')
        result = cls(**value)
        for name in known:
            if name not in ('project_id', 'schema_version', 'mounting_holes', 'presentation') and not isinstance(getattr(result, name), dict):
                raise ValueError(f'Project field {name} must be an object')
        if not isinstance(result.mounting_holes, list) or not all(isinstance(hole, dict) for hole in result.mounting_holes):
            raise ValueError('Mounting hole selections must be a list of objects')
        if result.presentation != 'labels':
            raise ValueError('Unsupported presentation')
        try:
            layer = result.mechanical['layer']
            accepted = result.mechanical['accepted']
            feature_tolerance(result.mechanical['feature_tolerance_mm'])
            tolerance_unit = result.mechanical['feature_tolerance_unit']
            inspected_revision = result.mechanical['inspected_revision']
            inspected_geometry = result.mechanical['inspected_geometry']
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError('Mechanical settings are incomplete or invalid') from error
        if (not isinstance(layer, str) or not layer or
                type(accepted) is not bool or tolerance_unit not in DIMENSION_UNITS or
                not isinstance(inspected_revision, str) or
                not isinstance(inspected_geometry, str)):
            raise ValueError('Mechanical settings are incomplete or invalid')
        return result
