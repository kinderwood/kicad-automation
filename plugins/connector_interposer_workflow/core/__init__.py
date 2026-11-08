"""Pure Python Connector to Interposer Workflow domain models; importing never loads pcbnew or wx."""
from .model import HEADERS, FIELDS, Issue, SignalRecord, validate_records
from .workbook import Workbook, Worksheet, ParsedRow, ParsedTable, read_workbook, parse_sheet
from .layers import LayerRules, suggest_layer_mappings, validate_layers
from .geometry import GangDefinition, validate_mechanical_population
from .connector_types import (VALID_TYPES, connector_type, reference_type,
                              suggest_netclass_types)
from .identities import stable_uuid, diff_snapshots, digest, canonical_json
from .libraries import (ApprovedFootprint, ApprovedSymbol, footprint_matches_filters,
                        read_footprint, read_symbol)
from .config import ProjectConfig
from .units import DIMENSION_UNITS, dimension_text, normalize_unit, parse_dimension
from .microvias import (MicroviaPreset, check_preset, evaluate_presets,
                        minimum_pitch, stack_plan)
