"""Approved-part schematic generation and optional native CLI round trips."""
import dataclasses
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import unittest
import uuid
import xml.etree.ElementTree as ET

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'plugins'))
from connector_interposer_workflow.core import SignalRecord, read_footprint, read_symbol
from connector_interposer_workflow.core.sexpr import loads, children, child
from connector_interposer_workflow.schematic import PartSelection, generate_schematic, merge_library_table
from connector_interposer_workflow.core.project import merge_project_settings

NATIVE_CLI = ([os.environ['KICAD_TEST_APP_RUN'], 'kicad-cli'] if os.environ.get('KICAD_TEST_APP_RUN') else ([shutil.which('kicad-cli')] if shutil.which('kicad-cli') else None))

NATIVE_PYTHON = ([os.environ['KICAD_TEST_APP_RUN'], 'python3.11'] if os.environ.get('KICAD_TEST_APP_RUN') else ([sys.executable] if importlib.util.find_spec('pcbnew') else None))

PROJECT_ID='d4fcdb6e-bd58-41fd-9225-d52b6d258ab3'


def fixture(directory, count=70):
    """Test-owned librarian artifacts; production never authors these files."""
    directory=Path(directory)
    gppo=directory/'GPPO.kicad_mod'
    gppo.write_text('(footprint "GPPO" (layer "F.Cu") (pad "1" thru_hole circle (at 0 0) (size 1 1) (drill .5) (layers "*.Cu" "*.Mask")) (pad "2" smd circle (at 0 3) (size 1 1) (layers "F.Cu" "F.Mask")))')
    interposer=directory/'Interposer.kicad_mod'
    interposer.write_text('(footprint "Interposer" (layer "F.Cu") '+''.join(f'(pad "A{i+1}" smd circle (at {i%10} {i//10}) (size .5 .5) (layers "F.Cu" "F.Mask"))' for i in range(count))+')')
    symbol=directory/'Approved.kicad_sym'
    connector_symbol='''(symbol "GPPO" (pin_names (offset 0) (hide yes)) (in_bom yes) (on_board yes)
      (property "Reference" "J" (at 0 5 0) (effects (font (size 1 1))))
      (property "Value" "GPPO" (at 0 7 0) (effects (font (size 1 1))))
      (property "ki_fp_filters" "GPPO" (at 0 0 0) (effects (font (size 1 1)) hide))
      (symbol "GPPO_1_1"
       (pin passive line (at 0 0 180) (length 2.54) (name "Signal" (effects (font (size .8 .8)))) (number "1" (effects (font (size .8 .8)))))
       (pin passive line (at 0 -5.08 180) (length 2.54) (name "Shield" (effects (font (size .8 .8)))) (number "2" (effects (font (size .8 .8)))))))'''
    interposer_symbol='''(symbol "Interposer" (pin_names (offset 0) (hide yes)) (in_bom yes) (on_board yes)
      (property "Reference" "E" (at 0 5 0) (effects (font (size 1 1))))
      (property "Value" "Interposer" (at 0 7 0) (effects (font (size 1 1))))
      (property "ki_fp_filters" "Interposer" (at 0 0 0) (effects (font (size 1 1)) hide))
      (symbol "Interposer_1_1" '''+''.join(
          f'(pin passive line (at 0 {-i*2.54:g} 180) (length 2.54) '
          f'(name "A{i+1}" (effects (font (size .8 .8)))) '
          f'(number "A{i+1}" (effects (font (size .8 .8)))))' for i in range(count)
      )+'))'
    symbol.write_text('(kicad_symbol_lib (version 20241209) (generator "test_librarian") '
                      +connector_symbol+interposer_symbol+')')
    gp=PartSelection(read_footprint(gppo,'Approved:GPPO'),read_symbol(symbol,'Approved:GPPO'),mpn='APPROVED-GPPO')
    ip=PartSelection(read_footprint(interposer,'Approved:Interposer'),
                     read_symbol(symbol,'Approved:Interposer',require_two_pin=False),mpn='APPROVED-IP')
    records=tuple(SignalRecord(f'SIG{i+1}','RF','E1',f'A{i+1}','J101',str(i+1),'L1',i+2) for i in range(min(30,count)))
    return records,{'J101':gp},{'E1':ip}


class SchematicTests(unittest.TestCase):
    def test_determinism_stable_identity_removal_and_bom(self):
        with tempfile.TemporaryDirectory() as tmp:
            records,gp,ip=fixture(tmp)
            before={p.name:p.read_bytes() for p in Path(tmp).iterdir()}
            bundle=generate_schematic(records,gp,ip,PROJECT_ID)
            reordered=generate_schematic(records[::-1],gp,ip,PROJECT_ID)
            self.assertEqual(bundle.files,reordered.files)
            self.assertEqual(len(bundle.bom),31)
            self.assertEqual(sum(r['Quantity'] for r in bundle.bom if r['Reference'].startswith('J')),30)
            removed=generate_schematic(records[:-1],gp,ip,PROJECT_ID)
            self.assertEqual(bundle.paths['J101-1'],removed.paths['J101-1'])
            self.assertEqual(before,{p.name:p.read_bytes() for p in Path(tmp).iterdir()})
            self.assertEqual(len([n for n in bundle.files if n.startswith('gang_')]),2)
            for text in bundle.files.values():loads(text)
            for filename in removed.files:
                if filename.startswith('gang_') and filename.endswith('_1.kicad_sch'):
                    self.assertEqual(bundle.files[filename],removed.files[filename])
            changed=generate_schematic((dataclasses.replace(records[0],signal='CHANGED'),*records[1:]),gp,ip,PROJECT_ID)
            self.assertEqual(bundle.paths,changed.paths)
            self.assertIn('CHANGED',changed.project_settings['net_settings']['netclass_assignments'])
            self.assertIn('APPROVED-GPPO',bundle.bom_csv())

    def test_unassigned_interposer_pads_ground_and_missing_library_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            records,gp,ip=fixture(tmp,4)
            bundle=generate_schematic(records[:1],gp,ip,PROJECT_ID)
            sheet=next(v for k,v in bundle.files.items() if k.startswith('interposer_'))
            node=loads(sheet)
            self.assertEqual([l[1] for l in children(node,'global_label')].count('GND'),3)
            with self.assertRaises(ValueError):generate_schematic(records,{},ip,PROJECT_ID)
            with self.assertRaises(ValueError):generate_schematic(records,{'J101':dataclasses.replace(gp['J101'],symbol=None)},ip,PROJECT_ID)
            with self.assertRaises(ValueError):generate_schematic((dataclasses.replace(records[0],interposer_pin='ZZ1'),),gp,ip,PROJECT_ID)

    def test_native_project_merges_preserve_designer_rules(self):
        record=SignalRecord('RF[*]','RF','E1','A1','J1','1','L1')
        existing={'board':{'unknown':42},'net_settings':{'classes':[{'name':'Default','priority':2147483647,'track_width':.432},{'name':'RF','priority':3,'clearance':.765}], 'netclass_patterns':[{'pattern':'DESIGNER*','netclass':'RF'}], 'netclass_assignments':{'DESIGNER':['RF'],'OLD':['RF']},'meta':{'version':4},'unknown':99}}
        result=merge_project_settings(existing,[record],previously_owned_nets=['OLD'])
        self.assertEqual(result['board'],existing['board'])
        self.assertEqual(result['net_settings']['classes'],existing['net_settings']['classes'])
        self.assertEqual(result['net_settings']['netclass_patterns'],existing['net_settings']['netclass_patterns'])
        self.assertEqual(result['net_settings']['netclass_assignments'],{'DESIGNER':['RF'],'RF[*]':['RF']})
        self.assertIn('OLD',existing['net_settings']['netclass_assignments'])
        future=json.loads(json.dumps(existing));future['net_settings']['meta']['version']=5
        self.assertEqual(merge_project_settings(future,[record],target_kicad_major=10)['net_settings']['meta']['version'],5)
        with self.assertRaises(ValueError):merge_project_settings(future,[record],target_kicad_major=9)
        table='(sym_lib_table (lib (name "Designer") (type "KiCad") (uri "/designer.kicad_sym") (options "") (descr "Mine")))'
        incoming='(sym_lib_table (lib (name "WorkflowProject") (type "KiCad") (uri "${KIPRJMOD}/WorkflowProject.kicad_sym") (options "") (descr "")))'
        merged=merge_library_table(table,incoming)
        self.assertIn('Designer',merged)
        self.assertEqual(merge_library_table(merged,incoming),merged)
        with self.assertRaises(ValueError):merge_library_table(table,incoming.replace('WorkflowProject','Designer'))

    @unittest.skipUnless(NATIVE_PYTHON,'Native netclass test needs bundled/system pcbnew Python')
    def test_native_exact_netclass_assignments_and_default_preservation(self):
        with tempfile.TemporaryDirectory() as tmp:
            board_path=str(Path(tmp)/'classes.kicad_pcb')
            create="""import pcbnew as p,sys
b=p.BOARD()
n=p.NETINFO_ITEM(b,'RF[*]');b.Add(n)
t=p.PCB_TRACK(b);t.SetStart(p.VECTOR2I(10000000,10000000));t.SetEnd(p.VECTOR2I(11000000,10000000));t.SetWidth(200000);t.SetLayer(p.F_Cu);t.SetNet(n);b.Add(t)
p.SaveBoard(sys.argv[1],b)
"""
            result=subprocess.run(NATIVE_PYTHON+['-c',create,board_path],capture_output=True,text=True,timeout=60)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            existing={'net_settings':{'meta':{'version':4},'classes':[{'name':'Default','priority':2147483647,'track_width':.432,'clearance':.2,'via_diameter':.6,'via_drill':.3,'microvia_diameter':.3,'microvia_drill':.1}]}}
            settings=merge_project_settings(existing,[SignalRecord('RF[*]','RF','E1','A1','J1','1','L1')])
            Path(board_path).with_suffix('.kicad_pro').write_text(json.dumps(settings))
            inspect="""import pcbnew as p,sys
b=p.LoadBoard(sys.argv[1])
classes={str(name):(nc.GetPriority(),nc.GetTrackWidth()) for name,nc in b.GetAllNetClasses().items()}
assert 'RF' in str(b.GetNetsByName()['RF[*]'].GetNetClassName()).split(',')
assert classes['Default'][1]==432000,classes
assert classes['RF']==(0,-1),classes
"""
            # A fresh process avoids the project cache created by SaveBoard.
            result=subprocess.run(NATIVE_PYTHON+['-c',inspect,board_path],capture_output=True,text=True,timeout=60)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    @unittest.skipUnless(NATIVE_CLI,'Native tests need kicad-cli on PATH or KICAD_TEST_APP_RUN')
    def test_native_multisheet_netlist_and_erc(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp)
            records,gp,ip=fixture(directory)
            bundle=generate_schematic(records,gp,ip,PROJECT_ID,'native')
            for name,text in bundle.files.items():(directory/name).write_text(text)
            (directory/'native.kicad_pro').write_text(json.dumps(bundle.project_settings))
            cli=NATIVE_CLI
            for args in (['sch','export','netlist','--format','kicadxml','-o',str(directory/'net.xml')],['sch','erc','--format','json','-o',str(directory/'erc.json')]):
                result=subprocess.run(cli+args+[str(directory/'native.kicad_sch')],capture_output=True,text=True,timeout=120)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            root=ET.parse(directory/'net.xml').getroot()
            components=root.findall('components/comp')
            self.assertEqual(len(components),31)
            netnodes={net.get('name'):{(n.get('ref'),n.get('pin')) for n in net.findall('node')} for net in root.findall('nets/net')}
            for record in records:
                self.assertEqual(netnodes[record.signal],{(record.key,'1'),('E1',record.interposer_pin)})
            self.assertEqual(len(netnodes['GND']),30+40)
            report=json.loads((directory/'erc.json').read_text())
            errors=[v for sheet in report['sheets'] for v in sheet['violations'] if v.get('severity')=='error']
            self.assertEqual(errors,[])
            self.assertEqual([v for sheet in report['sheets'] for v in sheet['violations']],[])


if __name__=='__main__':unittest.main()
