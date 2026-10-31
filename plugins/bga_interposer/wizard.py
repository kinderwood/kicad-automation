"""BGA interposer footprint wizard for KiCad 10.

The drawing and parameter conventions intentionally follow KiCad's bundled
``bga_wizard.py``.  Pad names retain their full-grid row and column locations,
so omitting the center opening does not renumber the surrounding balls.
"""

from __future__ import division

import pcbnew

import FootprintWizardBase
import PadArray as PA


BGA_ROW_ALPHABET = "ABCDEFGHJKLMNPRTUVWY"

MOUNTING_CORNERS = (
    ("top left (A1 corner)", -1, -1),
    ("top right", 1, -1),
    ("bottom right", 1, 1),
    ("bottom left", -1, 1),
)


class BGAInterposerWizard(FootprintWizardBase.FootprintWizard):
    """Generate a BGA interposer with a center opening and mounting feature."""

    def GetName(self):
        return "BGA Interposer"

    def GetDescription(self):
        return "BGA interposer with a centered opening and optional corner NPTH"

    def GenerateParameterList(self):
        self.AddParam("Pads", "pitch", self.uMM, 1.0, designator="p", min_value=0.01)
        self.AddParam("Pads", "size", self.uMM, 0.5, min_value=0.01)
        self.AddParam("Pads", "columns", self.uInteger, 20, designator="nx", min_value=3)
        self.AddParam("Pads", "rows", self.uInteger, 20, designator="ny", min_value=3)

        self.AddParam(
            "Center opening",
            "columns",
            self.uInteger,
            8,
            designator="ox",
            min_value=0,
            hint="Number of omitted pad columns in the centered rectangle",
        )
        self.AddParam(
            "Center opening",
            "rows",
            self.uInteger,
            8,
            designator="oy",
            min_value=0,
            hint="Number of omitted pad rows in the centered rectangle",
        )

        self.AddParam("Package", "width", self.uMM, 26.0, designator="X")
        self.AddParam("Package", "length", self.uMM, 26.0, designator="Y")
        self.AddParam(
            "Package",
            "margin",
            self.uMM,
            0.25,
            min_value=0.2,
            hint="Courtyard margin",
        )

        self.AddParam("Mounting features", "enable round hole", self.uBool, True)
        self.AddParam(
            "Mounting features", "top left (A1 corner)", self.uBool, True
        )
        self.AddParam("Mounting features", "top right", self.uBool, False)
        self.AddParam("Mounting features", "bottom right", self.uBool, False)
        self.AddParam("Mounting features", "bottom left", self.uBool, False)
        self.AddParam(
            "Mounting features",
            "x distance",
            self.uMM,
            11.5,
            min_value=0.01,
            hint="Positive distance from package center; corner selects the sign",
        )
        self.AddParam(
            "Mounting features",
            "y distance",
            self.uMM,
            11.5,
            min_value=0.01,
            hint="Positive distance from package center; corner selects the sign",
        )
        self.AddParam(
            "Mounting features",
            "hole diameter",
            self.uMM,
            2.5,
            min_value=0.01,
            hint="Finished NPTH diameter",
        )
        self.AddParam(
            "Mounting features",
            "origin at round hole",
            self.uBool,
            False,
            hint="Otherwise the footprint origin is the package center",
        )

    def CheckParameters(self):
        pads = self.parameters["Pads"]
        opening = self.parameters["Center opening"]
        mounting = self.parameters["Mounting features"]

        columns = pads["columns"]
        rows = pads["rows"]
        opening_columns = opening["columns"]
        opening_rows = opening["rows"]

        # A non-empty opening must leave at least one row/column on every
        # side. Matching parity is what puts its geometric center at (0, 0).
        self.CheckParam("Center opening", "columns", max_value=max(0, columns - 2))
        self.CheckParam("Center opening", "rows", max_value=max(0, rows - 2))

        opening_columns_param = self.GetParam("Center opening", "columns")
        opening_rows_param = self.GetParam("Center opening", "rows")

        if (opening_columns == 0) != (opening_rows == 0):
            message = "both opening dimensions must be zero, or both must be positive"
            opening_columns_param.AddError(message)
            opening_rows_param.AddError(message)

        if opening_columns and (columns - opening_columns) % 2:
            opening_columns_param.AddError(
                "outer and opening column counts must have matching parity",
                "required for an opening centered at the origin",
            )

        if opening_rows and (rows - opening_rows) % 2:
            opening_rows_param.AddError(
                "outer and opening row counts must have matching parity",
                "required for an opening centered at the origin",
            )

        minimum_width = pcbnew.ToMM(pads["pitch"] * columns)
        minimum_length = pcbnew.ToMM(pads["pitch"] * rows)
        self.CheckParam(
            "Package",
            "width",
            min_value=minimum_width,
            info="package must extend beyond the outer pad centers",
        )
        self.CheckParam(
            "Package",
            "length",
            min_value=minimum_length,
            info="package must extend beyond the outer pad centers",
        )

        if not mounting["enable round hole"]:
            if mounting["origin at round hole"]:
                self.GetParam(
                    "Mounting features", "origin at round hole"
                ).AddError("requires the round hole to be enabled")
            return

        selected_corners = [
            name for name, _x_sign, _y_sign in MOUNTING_CORNERS if mounting[name]
        ]
        if len(selected_corners) != 1:
            message = "select exactly one corner when the round hole is enabled"
            for name, _x_sign, _y_sign in MOUNTING_CORNERS:
                self.GetParam("Mounting features", name).AddError(message)
            return

        hole_radius = mounting["hole diameter"] / 2
        required_width = pcbnew.ToMM(2 * (mounting["x distance"] + hole_radius))
        required_length = pcbnew.ToMM(2 * (mounting["y distance"] + hole_radius))
        self.CheckParam(
            "Package",
            "width",
            min_value=required_width,
            info="the complete mounting hole must remain inside the package",
        )
        self.CheckParam(
            "Package",
            "length",
            min_value=required_length,
            info="the complete mounting hole must remain inside the package",
        )

        hole_x, hole_y = self._mounting_hole_position()
        pad_radius = pads["size"] / 2
        minimum_center_distance_squared = (hole_radius + pad_radius) ** 2
        first_x = -((columns - 1) * pads["pitch"]) / 2
        first_y = -((rows - 1) * pads["pitch"]) / 2

        for row in range(rows):
            for column in range(columns):
                if self._inside_opening(
                    column,
                    row,
                    columns,
                    rows,
                    opening_columns,
                    opening_rows,
                ):
                    continue
                pad_x = first_x + column * pads["pitch"]
                pad_y = first_y + row * pads["pitch"]
                center_distance_squared = (pad_x - hole_x) ** 2 + (
                    pad_y - hole_y
                ) ** 2
                if center_distance_squared < minimum_center_distance_squared:
                    message = "round hole overlaps BGA pad {}{}".format(
                        self._row_name(row), column + 1
                    )
                    self.GetParam("Mounting features", "x distance").AddError(message)
                    self.GetParam("Mounting features", "y distance").AddError(message)
                    self.GetParam("Mounting features", "hole diameter").AddError(message)
                    return

    def GetValue(self):
        pads = self.parameters["Pads"]
        opening = self.parameters["Center opening"]
        package = self.parameters["Package"]
        pin_count = pads["rows"] * pads["columns"] - opening["rows"] * opening["columns"]

        return (
            "BGA-Interposer-{pins}_{columns}x{rows}_Opening{opening_columns}x{opening_rows}_"
            "{width:g}x{length:g}mm_P{pitch:g}mm"
        ).format(
            pins=pin_count,
            columns=pads["columns"],
            rows=pads["rows"],
            opening_columns=opening["columns"],
            opening_rows=opening["rows"],
            width=pcbnew.ToMM(package["width"]),
            length=pcbnew.ToMM(package["length"]),
            pitch=pcbnew.ToMM(pads["pitch"]),
        )

    @staticmethod
    def _row_name(row_index):
        """Return KiCad/JEDEC-style BGA row letters, omitting I, O, Q, S, X, Z."""
        # Match PadArray.AlphaNameFromNumber, including multi-letter rows.
        number = row_index + 1
        result = ""
        while number > 0:
            number, remainder = divmod(number - 1, len(BGA_ROW_ALPHABET))
            result = BGA_ROW_ALPHABET[remainder] + result
        return result

    @staticmethod
    def _inside_opening(column, row, columns, rows, opening_columns, opening_rows):
        if not opening_columns or not opening_rows:
            return False
        first_column = (columns - opening_columns) // 2
        first_row = (rows - opening_rows) // 2
        return (
            first_column <= column < first_column + opening_columns
            and first_row <= row < first_row + opening_rows
        )

    def _mounting_hole_position(self):
        mounting = self.parameters["Mounting features"]
        for name, x_sign, y_sign in MOUNTING_CORNERS:
            if mounting[name]:
                return (
                    x_sign * mounting["x distance"],
                    y_sign * mounting["y distance"],
                )
        return 0, 0

    def _origin_offset(self):
        mounting = self.parameters["Mounting features"]
        if mounting["enable round hole"] and mounting["origin at round hole"]:
            return self._mounting_hole_position()
        return 0, 0

    def _add_pads(self):
        pads = self.parameters["Pads"]
        opening = self.parameters["Center opening"]
        columns = pads["columns"]
        rows = pads["rows"]
        pitch = pads["pitch"]
        origin_x, origin_y = self._origin_offset()

        prototype = PA.PadMaker(self.module).SMTRoundPad(pads["size"])
        first_x = -((columns - 1) * pitch) / 2
        first_y = -((rows - 1) * pitch) / 2

        for row in range(rows):
            for column in range(columns):
                if self._inside_opening(
                    column,
                    row,
                    columns,
                    rows,
                    opening["columns"],
                    opening["rows"],
                ):
                    continue

                pad = prototype.Duplicate()
                pad.SetName("{}{}".format(self._row_name(row), column + 1))
                pad.SetPosition(
                    pcbnew.VECTOR2I(
                        int(first_x + column * pitch - origin_x),
                        int(first_y + row * pitch - origin_y),
                    )
                )
                self.module.Add(pad)

    def _add_mounting_hole(self):
        mounting = self.parameters["Mounting features"]
        if not mounting["enable round hole"]:
            return

        hole_x, hole_y = self._mounting_hole_position()
        origin_x, origin_y = self._origin_offset()
        pad = PA.PadMaker(self.module).NPTHRoundPad(mounting["hole diameter"])
        pad.SetPosition(
            pcbnew.VECTOR2I(int(hole_x - origin_x), int(hole_y - origin_y))
        )
        self.module.Add(pad)

    def BuildThisFootprint(self):
        self._add_pads()
        self._add_mounting_hole()

        package = self.parameters["Package"]
        half_width = package["width"] / 2
        half_length = package["length"] / 2
        origin_x, origin_y = self._origin_offset()
        self.draw.TransformTranslate(-origin_x, -origin_y)

        # Fabrication outline, including the pin-1 bevel used by KiCad's BGA wizard.
        self.draw.SetLayer(pcbnew.F_Fab)
        bevel = min(half_width, pcbnew.FromMM(1))
        self.draw.SetLineThickness(pcbnew.FromMM(0.1))
        self.draw.BoxWithDiagonalAtCorner(
            0, 0, half_width * 2, half_length * 2, bevel
        )

        # Sparse silkscreen corners keep clear of the outer balls.
        self.draw.SetLayer(pcbnew.F_SilkS)
        self.draw.SetLineThickness(pcbnew.FromMM(0.12))
        offset = self.draw.GetLineThickness()
        line_x = 0.5 * half_width
        line_y = 0.5 * half_length
        edge = [
            [half_width + offset - line_x, -half_length - offset],
            [half_width + offset, -half_length - offset],
            [half_width + offset, -half_length - offset + line_y],
        ]
        self.draw.Polyline(edge)
        self.draw.Polyline(edge, mirrorY=0)
        self.draw.Polyline(edge, mirrorX=0, mirrorY=0)

        pin1_bevel = bevel + offset
        pin1 = [
            [-half_width - offset + line_x, -half_length - offset],
            [-half_width - offset + pin1_bevel, -half_length - offset],
            [-half_width - offset, -half_length - offset + pin1_bevel],
            [-half_width - offset, -half_length - offset + line_y],
        ]
        if pin1_bevel > line_x:
            pin1 = pin1[1:]
        if pin1_bevel > line_y:
            pin1 = pin1[:-1]
        self.draw.Polyline(pin1)
        self.draw.Circle(-half_width, -half_length, pcbnew.FromMM(0.2), filled=True)

        # Courtyard rounded to the 0.1 mm size grid, as in KiCad's wizard.
        margin = package["margin"]
        self.draw.SetLayer(pcbnew.F_CrtYd)
        courtyard_width = pcbnew.PutOnGridMM((half_width + margin) * 2, 0.1)
        courtyard_length = pcbnew.PutOnGridMM((half_length + margin) * 2, 0.1)
        self.draw.SetLineThickness(pcbnew.FromMM(0.05))
        self.draw.Box(0, 0, courtyard_width, courtyard_length)

        text_size = self.GetTextSize()
        text_y = half_length + text_size
        self.draw.Value(0, text_y, text_size)
        self.draw.Reference(0, -text_y, text_size)

        extra_reference = pcbnew.PCB_TEXT(self.module)
        extra_reference.SetLayer(pcbnew.F_Fab)
        extra_reference.SetPosition(
            pcbnew.VECTOR2I(int(-origin_x), int(-origin_y))
        )
        extra_reference.SetTextSize(pcbnew.VECTOR2I(text_size, text_size))
        extra_reference.SetText("${REFERENCE}")
        self.module.Add(extra_reference)

        self.module.SetAttributes(pcbnew.FP_SMD)
