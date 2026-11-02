"""Read XLSX and CSV without optional runtime packages; preserve every source row."""
import csv
from decimal import Decimal, InvalidOperation
import posixpath
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
import xml.etree.ElementTree as ET
from .model import FIELDS, HEADERS, Issue, SignalRecord, validate_records

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
MAX_XML_BYTES = 64 * 1024 * 1024
MAX_ROWS = 1_048_576
MAX_COLUMNS = 16_384

@dataclass(frozen=True)
class Worksheet:
    name: str
    rows: tuple

@dataclass(frozen=True)
class Workbook:
    sheets: tuple

    def sheet(self, name):
        return next(s for s in self.sheets if s.name == name)

@dataclass(frozen=True)
class ParsedRow:
    source_row: int
    values: tuple
    ignored: bool
    record: SignalRecord | None
    issues: tuple

@dataclass(frozen=True)
class ParsedTable:
    worksheet: str
    header_row: int
    mapping: dict
    rows: tuple
    records: tuple
    issues: tuple
    mapping_complete: bool
    ignored_rows: tuple

    @property
    def included_count(self):
        return sum(not row.ignored for row in self.rows)

    @property
    def ignored_count(self):
        return sum(row.ignored for row in self.rows)

    @property
    def valid(self):
        return self.mapping_complete and not any(i.severity == "error" for i in self.issues)

    @property
    def view_data_enabled(self):
        return self.mapping_complete and not any(i.code == "ignored_range" for i in self.issues)


def _text(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _xml(zf, name):
    info = zf.getinfo(name)
    if info.file_size > MAX_XML_BYTES:
        raise ValueError(f"Workbook XML exceeds limit: {name}")
    data = zf.read(info)
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ValueError("Workbook XML entities are unsupported")
    return ET.fromstring(data)


def _rich_text(node):
    if node is None:
        return ""
    return "".join((item.text or "") for item in node.findall("m:t", NS)+node.findall("m:r/m:t", NS))


def _column(ref):
    match = re.fullmatch(r"([A-Z]+)([1-9][0-9]*)", ref)
    if not match:
        raise ValueError(f"Invalid worksheet cell reference: {ref}")
    result = 0
    for letter in match[1]:
        result = result * 26 + ord(letter) - 64
    if result > MAX_COLUMNS:
        raise ValueError("Worksheet column exceeds XLSX limit")
    return result - 1, int(match[2])


def read_workbook(path):
    path = Path(path)
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as stream:
            rows = tuple(tuple(cell for cell in row) for row in csv.reader(stream))
        return Workbook((Worksheet(path.stem, rows),))
    if path.suffix.lower() != ".xlsx":
        raise ValueError("Choose an .xlsx or UTF-8 .csv file")
    with zipfile.ZipFile(path) as zf:
        root = _xml(zf, "xl/workbook.xml")
        rels = _xml(zf, "xl/_rels/workbook.xml.rels")
        targets = {}
        for rel in rels:
            if rel.get("TargetMode") == "External":
                continue
            target = rel.get("Target", "")
            target = posixpath.normpath(target.lstrip("/") if target.startswith("/") else "xl/"+target)
            if not target.startswith("xl/"):
                raise ValueError("Worksheet relationship escapes workbook")
            targets[rel.get("Id")] = target
        shared = []
        if "xl/sharedStrings.xml" in zf.namelist():
            shared = [_rich_text(si) for si in _xml(zf, "xl/sharedStrings.xml")]
        sheets = []
        for sheet in root.findall("m:sheets/m:sheet", NS):
            target = targets.get(sheet.get(f"{{{REL}}}id"))
            if not target:
                raise ValueError("Missing or external worksheet relationship")
            data = _xml(zf, target)
            rows = []
            for row in data.findall("m:sheetData/m:row", NS):
                number = int(row.get("r", str(len(rows)+1)))
                if number <= len(rows) or number > MAX_ROWS:
                    raise ValueError("Invalid worksheet row order or range")
                rows.extend([()] * (number-len(rows)))
                cells = []
                for cell in row.findall("m:c", NS):
                    index, cell_row = _column(cell.get("r", ""))
                    if cell_row != number or index < len(cells):
                        raise ValueError("Invalid or duplicate worksheet cell")
                    cells.extend([""] * (index+1-len(cells)))
                    typ = cell.get("t", "n")
                    val = cell.findtext("m:v", default="", namespaces=NS)
                    if typ == "s":
                        try:
                            index = int(val)
                            if index < 0:
                                raise ValueError("Negative shared-string index")
                            val = shared[index]
                        except (ValueError, IndexError):
                            raise ValueError("Invalid shared-string index") from None
                    elif typ == "inlineStr":
                        val = _rich_text(cell.find("m:is", NS))
                    elif typ == "e":
                        val = "#XLSX_ERROR:" + val
                    elif typ == "n" and val:
                        try:
                            number_value = Decimal(val)
                            if not number_value.is_finite():
                                val = "#XLSX_ERROR:non-finite number"
                            elif number_value == number_value.to_integral_value():
                                val = str(int(number_value))
                        except InvalidOperation:
                            val = "#XLSX_ERROR:invalid number"
                    if cell.find("m:f", NS) is not None and not val:
                        val = "#XLSX_ERROR:formula has no cached result"
                    cells[index] = val
                rows[number-1] = tuple(cells)
            sheets.append(Worksheet(sheet.get("name", ""), tuple(rows)))
        return Workbook(tuple(sheets))


def recognize_headers(values):
    result = {}
    for header in HEADERS:
        matches = [i for i, value in enumerate(values) if _text(value).casefold() == header.casefold()]
        if len(matches) == 1:
            result[header] = matches[0]
    return result


def parse_sheet(sheet, header_row=1, mapping=None, ignored_rows=()):
    if isinstance(header_row, bool) or not isinstance(header_row, int) or not 1 <= header_row <= len(sheet.rows):
        raise ValueError("Header row must identify an existing worksheet row")
    mapping = dict(recognize_headers(sheet.rows[header_row-1]) if mapping is None else mapping)
    width = max((len(row) for row in sheet.rows), default=0)
    complete = set(mapping) == set(HEADERS) and all(type(i) is int and 0 <= i < width for i in mapping.values()) and len(set(mapping.values())) == len(HEADERS)
    issues = [] if complete else [Issue("mapping", "Map all seven required fields to unique columns")]
    ignored = set(ignored_rows)
    if any(type(row) is not int or row <= header_row or row > len(sheet.rows) for row in ignored):
        issues.append(Issue("ignored_range", "Ignored rows must be data rows within the worksheet"))
    rows, records = [], []
    for number, raw in enumerate(sheet.rows[header_row:], header_row+1):
        row_issues, record = [], None
        if number not in ignored and complete:
            values = tuple(_text(raw[mapping[h]]) if mapping[h] < len(raw) else "" for h in HEADERS)
            record = SignalRecord(**dict(zip(FIELDS, values)), source_row=number)
            records.append(record)
            for name, value in zip(FIELDS, values):
                if value.startswith("#XLSX_ERROR:"):
                    row_issues.append(Issue("cell_error", value, number, name))
        rows.append(ParsedRow(number, tuple(_text(v) for v in raw), number in ignored, record, tuple(row_issues)))
        issues.extend(row_issues)
    record_issues = validate_records(records)
    issues.extend(record_issues)
    rows = tuple(ParsedRow(r.source_row, r.values, r.ignored, r.record, tuple(i for i in issues if i.source_row == r.source_row)) for r in rows)
    return ParsedTable(sheet.name, header_row, mapping, rows, tuple(records), tuple(issues), complete, tuple(sorted(ignored, key=lambda value: (type(value).__name__, str(value)))))
