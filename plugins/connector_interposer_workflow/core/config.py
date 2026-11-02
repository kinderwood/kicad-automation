"""Versioned project configuration, kept separate from approved library sources."""
from dataclasses import asdict, dataclass, field
import uuid
from .identities import canonical_json

CONFIG_VERSION = 1

@dataclass
class ProjectConfig:
    project_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    schema_version: int = CONFIG_VERSION
    workbook: dict = field(default_factory=dict)
    connector_symbol: dict = field(default_factory=dict)
    mounting_holes: list = field(default_factory=list)
    presentation: str = 'labels'
    output_dir: str = ''
    layer_mapping: dict = field(default_factory=dict)
    layer_rules: dict = field(default_factory=dict)
    drafts: dict = field(default_factory=dict)
    gangs: dict = field(default_factory=dict)
    interposers: dict = field(default_factory=dict)
    mechanical: dict = field(default_factory=dict)
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
            if name not in ('project_id', 'schema_version', 'mounting_holes', 'presentation', 'output_dir') and not isinstance(getattr(result, name), dict):
                raise ValueError(f'Project field {name} must be an object')
        if not isinstance(result.mounting_holes, list) or not all(isinstance(hole, dict) for hole in result.mounting_holes):
            raise ValueError('Mounting hole selections must be a list of objects')
        if result.presentation != 'labels' or not isinstance(result.output_dir, str):
            raise ValueError('Unsupported presentation or invalid output directory')
        return result
