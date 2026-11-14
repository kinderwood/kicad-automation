#!/usr/bin/env python3
"""Regenerate the coupled GPPO A/B/C mechanical, electrical and RF fixtures.

Run with either supported KiCad AppRun so pcbnew writes native board files.
The checked-in project files and approved library sources are read-only inputs.
The RF transition library is authored with the repository's GPPO RF Transition
Designer from the experimental connector footprint.
"""
from __future__ import annotations

import csv
import gc
import hashlib
import io
import json
import math
from pathlib import Path
import shutil
import sys
import tempfile
import uuid
import zipfile

import pcbnew as p


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parents[1] / "plugins"))

from gppo_rf_transition.model import default_recipe, make_artifact, publish_bundle
from gppo_rf_transition.native import read_base_part

INPUTS = ROOT / "inputs"
BOARD_MIL = 4375.0
BOARD_MM = BOARD_MIL * .0254
BOARD_CENTER = BOARD_MM / 2
PITCH_MM = 150 * .0254
INTERPOSER_POSE = (BOARD_CENTER - 13, BOARD_CENTER - 13)
RF_COURTYARD_RADIUS_MM = 1.8629
EDGE_MARGIN_MM = 12.7
OUTER_RF_X = EDGE_MARGIN_MM + RF_COURTYARD_RADIUS_MM
INNER_RF_X = OUTER_RF_X + PITCH_MM
DC_PAD1 = (BOARD_CENTER - 5.54, BOARD_MM - EDGE_MARGIN_MM - 6.34)
HEADERS = (
    "Signal", "NetClass", "Interposer", "Interposer Pin",
    "Connector", "Connector Pin", "Layer",
)
PAD_ROWS = (
    "A", "B", "C", "D", "E", "F", "G", "H", "J", "K",
    "L", "M", "N", "P", "R", "T", "U", "V", "W", "Y",
    "AA", "AB", "AC", "AD", "AE", "AF", "AG", "AH", "AJ", "AK",
)
SIGNAL_ROWS = PAD_ROWS[1:28:2]
LIBRARY = ROOT / "library/Example.pretty"
RF_FOOTPRINT = "GPPO_B012_L13_01_EXPERIMENTAL"
RF_LIBRARY = ROOT / "library/rf"
# Architecture worksheet layer alias to the literal exit layer of its transition.
RF_EXIT_LAYERS = {"L2": "In1.Cu", "L3": "In2.Cu"}
DC_FOOTPRINT = "DSub_9_Pins_Vertical"
DC_ROW_LENGTHS = (5, 4)
# Drawing annotation, authored in mils like the DXF it lands in.
GANG_LABEL_HEIGHT_MM = 70 * .0254
PIN_LABEL_HEIGHT_MM = 50 * .0254
LABEL_GAP_MM = 50 * .0254
# KiCad's DXF importer derives the character width from the text height.
TEXT_WIDTH_RATIO = .9
NAMESPACE = uuid.UUID("c6f41829-41b5-56bd-a56f-10d0940a4ff0")


def uid(key: str) -> str:
    return str(uuid.uuid5(NAMESPACE, key))


def number(value: float) -> str:
    value = 0.0 if abs(value) < 5e-12 else value
    return f"{value:.12g}"


def rf_positions(variant: str) -> list[dict]:
    if variant == "A":
        first_y = BOARD_CENTER - 6.5 * PITCH_MM
        coordinates = {
            "J101": [(INNER_RF_X, first_y + index * PITCH_MM) for index in range(14)],
            "J102": [(BOARD_MM - INNER_RF_X, first_y + index * PITCH_MM) for index in range(14)],
        }
    else:
        first_y = BOARD_CENTER - 3 * PITCH_MM
        stagger = PITCH_MM / 2 if variant == "C" else 0
        coordinates = {
            "J101": (
                [(INNER_RF_X, first_y + index * PITCH_MM) for index in range(7)] +
                [(OUTER_RF_X, first_y + stagger + index * PITCH_MM) for index in range(7)]
            ),
            "J102": (
                [(BOARD_MM - OUTER_RF_X, first_y + index * PITCH_MM) for index in range(7)] +
                [(BOARD_MM - INNER_RF_X, first_y + stagger + index * PITCH_MM) for index in range(7)]
            ),
        }
    result = []
    for gang, points in coordinates.items():
        for pin, (x, y) in enumerate(points, 1):
            reference = f"{gang}-{pin}"
            result.append({
                "reference": reference,
                "pin": pin,
                "gang": gang,
                "x_mm": round(x, 9),
                "y_mm": round(y, 9),
                "uuid": uid("circle:" + reference),
                "orientation_degrees": 0 if gang == "J101" else 180,
            })
    return result


def dc_positions() -> list[dict]:
    offsets = (
        (0, 0), (2.77, 0), (5.54, 0), (8.31, 0), (11.08, 0),
        (1.385, 2.84), (4.155, 2.84), (6.925, 2.84), (9.695, 2.84),
    )
    result = []
    for pin, (dx, dy) in enumerate(offsets, 1):
        reference = f"J1-{pin}"
        result.append({
            "reference": reference,
            "pin": pin,
            "gang": "J1",
            "x_mm": round(DC_PAD1[0] + dx, 9),
            "y_mm": round(DC_PAD1[1] + dy, 9),
            "uuid": uid("circle:" + reference),
            "orientation_degrees": 0,
        })
    return result


def row_lengths(variant: str, gang: str) -> tuple[int, ...]:
    if gang == "J1":
        return DC_ROW_LENGTHS
    return (14,) if variant == "A" else (7, 7)


def pth_radius(footprint_name: str) -> float:
    """Return the pad 1 finished-hole radius of a library connector."""
    footprint = p.FootprintLoad(str(LIBRARY), footprint_name)
    pad = next(pad for pad in footprint.Pads() if pad.GetNumber() == "1")
    return p.ToMM(pad.GetDrillSize().x) / 2


def hole_radii() -> dict[str, float]:
    rf = pth_radius(RF_FOOTPRINT)
    return {"J101": rf, "J102": rf, "J1": pth_radius(DC_FOOTPRINT)}


def text_extent(text: str, height: float) -> tuple[float, float]:
    return TEXT_WIDTH_RATIO * height * len(text), height


def drawing_labels(variant: str, positions: list[dict],
                   radii: dict[str, float]) -> list[dict]:
    """Label each gang once and each row at its first and last pin.

    Pin numbers continue the row axis outward from the end holes. The gang
    identifier is centered over the holes on the board-top side of everything
    the gang draws, whatever the gang rotation. Every label is anchored at its
    center so placement does not depend on the viewer's font metrics.
    """
    gangs: dict[str, list[dict]] = {}
    for row in positions:
        gangs.setdefault(row["gang"], []).append(row)
    labels = []
    for gang, rows in gangs.items():
        radius = radii[gang]
        hole_x = [row["x_mm"] for row in rows]
        top = min(row["y_mm"] for row in rows) - radius
        start = 0
        for length in row_lengths(variant, gang):
            first, last = rows[start], rows[start + length - 1]
            start += length
            span = math.dist((first["x_mm"], first["y_mm"]), (last["x_mm"], last["y_mm"]))
            axis = ((last["x_mm"] - first["x_mm"]) / span,
                    (last["y_mm"] - first["y_mm"]) / span)
            for row, direction in ((first, -1), (last, 1)):
                text = str(row["pin"])
                width, height = text_extent(text, PIN_LABEL_HEIGHT_MM)
                reach = direction * (
                    LABEL_GAP_MM + (abs(axis[0]) * width + abs(axis[1]) * height) / 2
                )
                center = (row["x_mm"] + axis[0] * reach, row["y_mm"] + axis[1] * reach)
                top = min(top, center[1] - height / 2)
                labels.append({
                    "key": f"text:{gang}:pin:{text}", "text": text,
                    "center": center, "height": PIN_LABEL_HEIGHT_MM,
                })
        labels.append({
            "key": "text:" + gang, "text": gang,
            "center": ((min(hole_x) + max(hole_x)) / 2,
                       top - LABEL_GAP_MM - GANG_LABEL_HEIGHT_MM / 2),
            "height": GANG_LABEL_HEIGHT_MM,
        })
    return labels


def netlist_rows(variant: str) -> list[tuple]:
    rows = []
    for gang_index, gang in enumerate(("J101", "J102")):
        signal_start = gang_index * 7 + 1
        interposer_columns = (3, 6) if gang == "J101" else (28, 25)
        for pin, row_name in enumerate(SIGNAL_ROWS, 1):
            pair_index = (pin - 1) // 2
            clock_number = signal_start + pair_index
            odd = pin % 2 == 1
            if variant == "A":
                netclass = "CLOCK" if odd else "DATA"
            else:
                netclass = "DATA" if odd else "CLOCK"
            signal = f"{netclass}{clock_number}"
            column = interposer_columns[0 if odd else 1]
            rows.append((
                signal, netclass, "E1", f"{row_name}{column}",
                gang, pin, "L2" if odd else "L3",
            ))
    if variant == "C":
        rows.extend(
            (f"DC{pin}", "DC", "E1", f"AK{pin + 10}", "J1", pin, "L2")
            for pin in range(1, 10)
        )
    return rows


def write_csv(path: Path, rows: list[tuple]) -> None:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(HEADERS)
    writer.writerows(rows)
    path.write_text(stream.getvalue(), encoding="utf-8")


def sheet_xml(rows: list[tuple]) -> bytes:
    all_rows = [HEADERS, *rows]
    columns = "ABCDEFG"
    xml_rows = []
    for row_number, row in enumerate(all_rows, 1):
        cells = []
        for column, value in zip(columns, row):
            reference = f"{column}{row_number}"
            if isinstance(value, int):
                cells.append(f'<c r="{reference}" t="n"><v>{value}</v></c>')
            else:
                escaped = (str(value).replace("&", "&amp;").replace("<", "&lt;")
                           .replace(">", "&gt;"))
                cells.append(
                    f'<c r="{reference}" t="inlineStr"><is><t>{escaped}</t></is></c>'
                )
        xml_rows.append(f'<row r="{row_number}">{"".join(cells)}</row>')
    columns_xml = "".join(
        f'<col width="20" customWidth="1" min="{index}" max="{index}"/>'
        for index in range(1, 8)
    )
    content = (
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetPr><outlinePr summaryBelow="1" summaryRight="1"/><pageSetUpPr/></sheetPr>'
        f'<dimension ref="A1:G{len(all_rows)}"/>'
        '<sheetViews><sheetView workbookViewId="0"><selection activeCell="A1" '
        'sqref="A1"/></sheetView></sheetViews>'
        '<sheetFormatPr baseColWidth="8" defaultRowHeight="15"/>'
        f'<cols>{columns_xml}</cols><sheetData>{"".join(xml_rows)}</sheetData>'
        '<pageMargins left="0.75" right="0.75" top="1" bottom="1" '
        'header="0.5" footer="0.5"/></worksheet>'
    )
    return content.encode("utf-8")


def write_xlsx(path: Path, template: dict[str, bytes], order: list[str], rows: list[tuple]) -> None:
    values = dict(template)
    values["xl/worksheets/sheet1.xml"] = sheet_xml(rows)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in order:
            info = zipfile.ZipInfo(name, date_time=(2026, 11, 8, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, values[name])


def dxf_point(point: tuple[float, float]) -> tuple[float, float]:
    return point[0] / .0254, (BOARD_MM - point[1]) / .0254


class DxfEntities:
    def __init__(self):
        self.lines: list[str] = []
        self.handle = 0xA0000

    def _head(self, kind: str, layer: str, subclass: str) -> None:
        self.handle += 1
        self.lines.extend((
            "  0", kind, "  5", f"{self.handle:X}", "330", "17",
            "100", "AcDbEntity", "  8", layer, "100", subclass,
        ))

    def line(self, layer: str, start: tuple[float, float], end: tuple[float, float]) -> None:
        self._head("LINE", layer, "AcDbLine")
        self.lines.extend((
            " 10", number(start[0]), " 20", number(start[1]), " 30", "0",
            " 11", number(end[0]), " 21", number(end[1]), " 31", "0",
        ))

    def circle(self, layer: str, center: tuple[float, float], radius: float) -> None:
        self._head("CIRCLE", layer, "AcDbCircle")
        self.lines.extend((
            " 10", number(center[0]), " 20", number(center[1]), " 30", "0",
            " 40", number(radius),
        ))

    def text(self, layer: str, center: tuple[float, float], height: float,
             value: str) -> None:
        """Write middle-center text; group 11/21 is the governing point."""
        width, _height = text_extent(value, height)
        self._head("TEXT", layer, "AcDbText")
        self.lines.extend((
            " 10", number(center[0] - width / 2), " 20", number(center[1] - height / 2),
            " 30", "0", " 40", number(height), "  1", value, " 72", "1",
            " 11", number(center[0]), " 21", number(center[1]), " 31", "0",
            "100", "AcDbText", " 73", "2",
        ))


def replace_dxf_entities(path: Path, positions: list[dict], labels: list[dict],
                         radii: dict[str, float], include_dc: bool,
                         pad_centers: list[tuple[float, float]]) -> None:
    source = path.read_text(encoding="utf-8").splitlines()
    entity_start = next(
        index + 2 for index in range(0, len(source) - 1, 2)
        if source[index].strip() == "2" and source[index + 1].strip() == "ENTITIES"
    )
    entity_end = next(
        index for index in range(entity_start, len(source) - 1, 2)
        if source[index].strip() == "0" and source[index + 1].strip() == "ENDSEC"
    )
    dxf = DxfEntities()
    for start, end in (
        ((0, 0), (BOARD_MIL, 0)), ((BOARD_MIL, 0), (BOARD_MIL, BOARD_MIL)),
        ((BOARD_MIL, BOARD_MIL), (0, BOARD_MIL)), ((0, BOARD_MIL), (0, 0)),
    ):
        dxf.line("BOARD_OUTLINE", start, end)
    outline_min = (INTERPOSER_POSE[0] - 2, INTERPOSER_POSE[1] - 2)
    outline_max = (INTERPOSER_POSE[0] + 28, INTERPOSER_POSE[1] + 28)
    corners = (
        outline_min, (outline_max[0], outline_min[1]), outline_max,
        (outline_min[0], outline_max[1]), outline_min,
    )
    for start, end in zip(corners, corners[1:]):
        dxf.line("INTERPOSER", dxf_point(start), dxf_point(end))
    dxf.circle("INTERPOSER", dxf_point(INTERPOSER_POSE), 1.25 / .0254)
    for row in positions:
        center = dxf_point((row["x_mm"], row["y_mm"]))
        layer = "DC_CENTERS" if row["gang"] == "J1" else "GPPO_CENTERS"
        dxf.circle(layer, center, radii[row["gang"]] / .0254)
    for label in labels:
        dxf.text("MECH_LABELS", dxf_point(label["center"]), label["height"] / .0254,
                 label["text"])
    if include_dc:
        for center in pad_centers:
            dxf.circle("INTERPOSER", dxf_point(center), 6)
    path.write_text("\n".join([*source[:entity_start], *dxf.lines, *source[entity_end:]]) + "\n",
                    encoding="utf-8")


def assign_uuid(item, key: str) -> None:
    item.SetUuid(p.KIID(uid(key)))


def add_line(board, key: str, start: tuple[float, float], end: tuple[float, float]) -> None:
    item = p.PCB_SHAPE(board)
    item.SetShape(p.SHAPE_T_SEGMENT)
    item.SetStart(p.VECTOR2I(p.FromMM(start[0]), p.FromMM(start[1])))
    item.SetEnd(p.VECTOR2I(p.FromMM(end[0]), p.FromMM(end[1])))
    item.SetWidth(p.FromMM(.2))
    item.SetLayer(p.Dwgs_User)
    assign_uuid(item, key)
    board.Add(item)


def add_circle(board, key: str, center: tuple[float, float], radius: float) -> None:
    item = p.PCB_SHAPE(board)
    item.SetShape(p.SHAPE_T_CIRCLE)
    item.SetStart(p.VECTOR2I(p.FromMM(center[0]), p.FromMM(center[1])))
    item.SetEnd(p.VECTOR2I(p.FromMM(center[0] + radius), p.FromMM(center[1])))
    item.SetWidth(p.FromMM(.2))
    item.SetLayer(p.Dwgs_User)
    assign_uuid(item, key)
    board.Add(item)


def add_text(board, label: dict) -> None:
    """Add a label as PCB Editor's DXF import represents middle-center text."""
    height = label["height"]
    item = p.PCB_TEXT(board)
    item.SetText(label["text"])
    item.SetPosition(p.VECTOR2I(
        p.FromMM(label["center"][0]), p.FromMM(label["center"][1]),
    ))
    item.SetTextSize(p.VECTOR2I(
        p.FromMM(TEXT_WIDTH_RATIO * height), p.FromMM(height),
    ))
    item.SetTextThickness(p.FromMM(height / 8))
    item.SetHorizJustify(p.GR_TEXT_H_ALIGN_CENTER)
    item.SetVertJustify(p.GR_TEXT_V_ALIGN_CENTER)
    item.SetLayer(p.Dwgs_User)
    assign_uuid(item, label["key"])
    board.Add(item)


def write_board(variant: str, positions: list[dict], labels: list[dict],
                radii: dict[str, float],
                pad_centers: list[tuple[str, tuple[float, float]]]) -> None:
    board = p.BOARD()
    board.SetCopperLayerCount(4)
    outline = ((0, 0), (BOARD_MM, 0), (BOARD_MM, BOARD_MM), (0, BOARD_MM), (0, 0))
    for index, (start, end) in enumerate(zip(outline, outline[1:])):
        add_line(board, f"board-outline:{index}", start, end)
    minimum = (INTERPOSER_POSE[0] - 2, INTERPOSER_POSE[1] - 2)
    maximum = (INTERPOSER_POSE[0] + 28, INTERPOSER_POSE[1] + 28)
    assembly = (minimum, (maximum[0], minimum[1]), maximum,
                (minimum[0], maximum[1]), minimum)
    for index, (start, end) in enumerate(zip(assembly, assembly[1:])):
        add_line(board, f"interposer-outline:{index}", start, end)
    add_circle(board, "interposer-origin", INTERPOSER_POSE, 1.25)
    for row in positions:
        center = (row["x_mm"], row["y_mm"])
        add_circle(board, "circle:" + row["reference"], center, radii[row["gang"]])
    for label in labels:
        add_text(board, label)
    if variant == "C":
        for pad_number, center in pad_centers:
            add_circle(board, "interposer-pad:" + pad_number, center, .1524)
    with tempfile.TemporaryDirectory(prefix="gppo-fixture-") as temporary:
        temporary_board = Path(temporary) / f"mechanical-{variant}.kicad_pcb"
        p.SaveBoard(str(temporary_board), board)
        shutil.copy2(temporary_board, ROOT / f"mechanical-{variant}.kicad_pcb")
    del board
    gc.collect()


def interposer_pad_centers() -> list[tuple[str, tuple[float, float]]]:
    footprint = p.FootprintLoad(str(LIBRARY), "Interposer_800")
    footprint.SetPosition(p.VECTOR2I(p.FromMM(INTERPOSER_POSE[0]), p.FromMM(INTERPOSER_POSE[1])))
    return [
        (pad.GetNumber(), (p.ToMM(pad.GetPosition().x), p.ToMM(pad.GetPosition().y)))
        for pad in footprint.Pads()
        if pad.GetNumber() and pad.GetAttribute() != p.PAD_ATTRIB_NPTH
    ]


def rf_artifacts() -> dict:
    """Author the demo RF transitions, one per exit layer, deterministically.

    The footprint is through-hole, so its own barrel is the signal conductor.
    Eight ground vias leave the 180° position empty: in the rectangular
    revision B gang the connector behind sits 150 mil away on the exit axis,
    and a via there would land on its neighbour's exit trace.
    """
    part = read_base_part(LIBRARY / (RF_FOOTPRINT + ".kicad_mod"), approved=True)
    result = {}
    for architecture, copper in RF_EXIT_LAYERS.items():
        recipe = default_recipe(part)
        params = recipe["parameters"]
        params["netclasses"] = ["CLOCK", "DATA"]
        params["signal_layer"] = copper
        params["ground_ring"]["count"] = 8
        params["tuned_signal_copper"] = {
            layer: 0.65 if layer == copper else None for layer in params["stackup"]}
        result[architecture] = make_artifact(
            recipe, f"RF-{architecture}", provenance={
                "operation": "new",
                "purpose": "DEMONSTRATION FIXTURE ONLY — not manufacturing or RF qualified",
                "source": "Supplied OL-B012-L1X-01 rev B drawing; experimental central source",
            })
    return result


def write_rf_library() -> None:
    """Replace the published demo transitions and their catalog."""
    for stale in [*RF_LIBRARY.glob("*.gppo-rf.json"), RF_LIBRARY / "manifest.json"]:
        stale.unlink(missing_ok=True)
    for artifact in rf_artifacts().values():
        publish_bundle(RF_LIBRARY, artifact)


def update_provenance() -> None:
    path = ROOT / "fixture-provenance.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["source"] = (
        "Deterministic A/B/C demonstration geometry derived from the supplied "
        "B012-L13-01 connector and existing 800-pad interposer library sources."
    )
    for relative in manifest["sha256"]:
        target = ROOT / relative
        manifest["sha256"][relative] = hashlib.sha256(target.read_bytes()).hexdigest()
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    xlsx_path = INPUTS / "netlist-A.xlsx"
    with zipfile.ZipFile(xlsx_path) as archive:
        xlsx_order = archive.namelist()
        xlsx_template = {name: archive.read(name) for name in xlsx_order}
    pad_centers = interposer_pad_centers()
    pad_points = [center for _number, center in pad_centers]
    radii = hole_radii()
    for variant in "ABC":
        rows = netlist_rows(variant)
        positions = rf_positions(variant)
        if variant == "C":
            positions += dc_positions()
        write_csv(INPUTS / f"netlist-{variant}.csv", rows)
        write_xlsx(INPUTS / f"netlist-{variant}.xlsx", xlsx_template, xlsx_order, rows)
        (INPUTS / f"positions-{variant}.json").write_text(
            json.dumps(positions, indent=2) + "\n", encoding="utf-8",
        )
        labels = drawing_labels(variant, positions, radii)
        replace_dxf_entities(
            INPUTS / f"dxf-{variant}-mils.dxf", positions, labels, radii,
            include_dc=variant == "C", pad_centers=pad_points,
        )
        write_board(variant, positions, labels, radii, pad_centers)
    write_rf_library()
    update_provenance()


if __name__ == "__main__":
    main()
