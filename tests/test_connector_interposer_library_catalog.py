"""Loaded KiCad library-table resolution without file-picker fallbacks."""
from pathlib import Path
import tempfile
import unittest

from plugins.connector_interposer_workflow.core.library_catalog import KiCadLibraryCatalog


class _Settings:
    path = None

    @classmethod
    def GetUserSettingsPath(cls):
        return str(cls.path)


class _Board:
    def GetProject(self):
        return object()


class FakePcbnew:
    SETTINGS_MANAGER = _Settings
    root = None
    project = None

    @classmethod
    def GetBoard(cls):
        return _Board()

    @classmethod
    def ResolveUriByEnvVars(cls, value, project):
        return str(value).replace('${ROOT}', str(cls.root)).replace('${KIPRJMOD}', str(cls.project))

class LibraryCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.settings = self.root / 'settings'
        self.project = self.root / 'project'
        self.settings.mkdir()
        self.project.mkdir()
        FakePcbnew.root = self.root
        FakePcbnew.project = self.project
        _Settings.path = self.settings

    def tearDown(self):
        self.temporary.cleanup()

    def test_nested_global_table_and_project_precedence_match_loaded_footprints(self):
        global_lib = self.root / 'global.pretty'
        project_lib = self.root / 'project.pretty'
        global_lib.mkdir()
        project_lib.mkdir()
        (global_lib / 'GlobalPart.kicad_mod').write_text('(footprint "GlobalPart")')
        (project_lib / 'ProjectPart.kicad_mod').write_text('(footprint "ProjectPart")')
        nested = self.root / 'nested-fp-lib-table'
        nested.write_text('(fp_lib_table (version 7) '
                          '(lib (name "Native") (type "KiCad") (uri "${ROOT}/global.pretty") (options "") (descr "Global")) '
                          '(lib (name "Disabled") (type "KiCad") (uri "${ROOT}/disabled.pretty") (disabled)))')
        (self.settings / 'fp-lib-table').write_text(
            '(fp_lib_table (version 7) (lib (name "Nested") (type "Table") '
            '(uri "${ROOT}/nested-fp-lib-table") (options "")))')
        (self.project / 'fp-lib-table').write_text(
            '(fp_lib_table (lib (name "Native") (type "KiCad") '
            '(uri "${KIPRJMOD}/../project.pretty") (options "") (descr "Project")))')

        catalog = KiCadLibraryCatalog(FakePcbnew, self.project, 'footprint')
        self.assertEqual([(entry.nickname, entry.scope, entry.path) for entry in catalog.libraries()],
                         [('Native', 'Project', project_lib)])
        part = catalog.parts('Native')[0]
        self.assertEqual(part.library_id, 'Native:ProjectPart')
        self.assertEqual(part.path, project_lib / 'ProjectPart.kicad_mod')

    def test_symbol_catalog_lists_enabled_native_symbols(self):
        symbols = self.root / 'Company.kicad_sym'
        symbols.write_text('(kicad_symbol_lib (version 20231120) (generator kicad_symbol_editor) '
                           '(symbol "TwoPin") (symbol "Other"))')
        (self.settings / 'sym-lib-table').write_text(
            '(sym_lib_table (lib (name "Company") (type "KiCad") '
            '(uri "${ROOT}/Company.kicad_sym") (options "") (descr "Company symbols")) '
            '(lib (name "Disabled") (type "KiCad") (uri "${ROOT}/unused.kicad_sym") (disabled)))')

        catalog = KiCadLibraryCatalog(FakePcbnew, self.project, 'symbol')
        self.assertEqual([entry.nickname for entry in catalog.libraries()], ['Company'])
        self.assertEqual({part.library_id for part in catalog.parts('Company')},
                         {'Company:TwoPin', 'Company:Other'})


if __name__ == '__main__':
    unittest.main()
