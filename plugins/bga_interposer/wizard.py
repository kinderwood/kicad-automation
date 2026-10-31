"""BGA interposer footprint wizard for KiCad 9 and 10.

The drawing and parameter conventions intentionally follow KiCad's bundled
``bga_wizard.py``.  Pad names retain their full-grid row and column locations,
so omitting the center opening does not renumber the surrounding balls.
"""

from __future__ import division

import math

import pcbnew

import FootprintWizardBase
import PadArray as PA


BGA_ROW_ALPHABET = "ABCDEFGHJKLMNPRTUVWY"

FEATURE_CORNERS = (
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
        return "BGA interposer with depopulation and optional guide pin features"

    def GenerateParameterList(self):
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

        self.AddParam(
            "Mounting layout",
            "x distance",
            self.uMM,
            11.5,
            min_value=0.01,
            hint="Positive distance from package center; corner selects the sign",
        )
        self.AddParam(
            "Mounting layout",
            "y distance",
            self.uMM,
            11.5,
            min_value=0.01,
            hint="Positive distance from package center; corner selects the sign",
        )

        self.AddParam("Guide pin hole", "enabled", self.uBool, True)
        self.AddParam(
            "Guide pin hole", "top left (A1 corner)", self.uBool, True
        )
        self.AddParam("Guide pin hole", "top right", self.uBool, False)
        self.AddParam("Guide pin hole", "bottom right", self.uBool, False)
        self.AddParam("Guide pin hole", "bottom left", self.uBool, False)
        self.AddParam(
            "Guide pin hole",
            "diameter",
            self.uMM,
            2.5,
            min_value=0.01,
            hint="Finished round NPTH diameter",
        )
        self.AddParam(
            "Guide pin hole",
            "origin at guide pin hole",
            self.uBool,
            False,
            hint="Otherwise the footprint origin is the package center",
        )

        for name, _x_sign, _y_sign in FEATURE_CORNERS:
            self.AddParam("Guide pin slots", name, self.uBool, False)
            self.AddParam(
                "Guide pin slots",
                "{} rotation".format(name),
                self.uDegrees,
                0,
                hint="Counterclockwise from a horizontal long axis",
            )
        self.AddParam(
            "Guide pin slots",
            "width",
            self.uMM,
            2.5,
            min_value=0.01,
            hint="Finished NPTH slot width",
        )
        self.AddParam(
            "Guide pin slots",
            "length",
            self.uMM,
            3.0,
            min_value=0.01,
            hint="Overall finished NPTH slot length",
        )
        self.AddParam("Pads", "pitch", self.uMM, 1.0, designator="p", min_value=0.01)
        self.AddParam("Pads", "size", self.uMM, 0.5, min_value=0.01)
        self.AddParam("Pads", "columns", self.uInteger, 20, designator="nx", min_value=3)
        self.AddParam("Pads", "rows", self.uInteger, 20, designator="ny", min_value=3)
        self.AddParam(
            "Pads",
            "corner depopulation depth",
            self.uInteger,
            0,
            min_value=0,
            hint="Symmetric triangular corner removal; 4 removes 4, 3, 2, 1 pads",
        )

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

    def CheckParameters(self):
        pads = self.parameters["Pads"]
        opening = self.parameters["Center opening"]
        mounting_layout = self.parameters["Mounting layout"]
        guide_hole = self.parameters["Guide pin hole"]
        guide_slots = self.parameters["Guide pin slots"]

        columns = pads["columns"]
        rows = pads["rows"]
        opening_columns = opening["columns"]
        opening_rows = opening["rows"]

        self.CheckParam(
            "Pads",
            "corner depopulation depth",
            max_value=(min(columns, rows) - 1) // 2,
            info="at least one pad position must remain between opposing corner cuts",
        )

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

        guide_hole_corners = self._selected_corners("Guide pin hole")
        slot_corners = self._selected_corners("Guide pin slots")
        guide_hole_corner = None

        if guide_hole["enabled"]:
            if len(guide_hole_corners) != 1:
                message = "select exactly one corner when the guide pin hole is enabled"
                for name, _x_sign, _y_sign in FEATURE_CORNERS:
                    self.GetParam("Guide pin hole", name).AddError(message)
            else:
                guide_hole_corner = guide_hole_corners[0]
        elif guide_hole["origin at guide pin hole"]:
            self.GetParam(
                "Guide pin hole", "origin at guide pin hole"
            ).AddError("requires the guide pin hole to be enabled")

        if guide_hole_corner in slot_corners:
            message = "a guide pin hole and slot cannot occupy the same corner"
            self.GetParam("Guide pin hole", guide_hole_corner).AddError(message)
            self.GetParam("Guide pin slots", guide_hole_corner).AddError(message)

        if slot_corners:
            self.CheckParam(
                "Guide pin slots",
                "length",
                min_value=pcbnew.ToMM(guide_slots["width"]),
                info="slot length must be at least its width",
            )

        feature_half_width = 0
        feature_half_length = 0
        guide_hole_position = None
        if guide_hole_corner is not None:
            guide_hole_position = self._feature_position(guide_hole_corner)
            hole_radius = guide_hole["diameter"] / 2
            feature_half_width = abs(guide_hole_position[0]) + hole_radius
            feature_half_length = abs(guide_hole_position[1]) + hole_radius

        slot_segments = {}
        if guide_slots["length"] >= guide_slots["width"]:
            half_segment = (guide_slots["length"] - guide_slots["width"]) / 2
            slot_radius = guide_slots["width"] / 2
            for corner in slot_corners:
                rotation = math.radians(self._slot_rotation(corner))
                x_extent = abs(math.cos(rotation)) * half_segment + slot_radius
                y_extent = abs(math.sin(rotation)) * half_segment + slot_radius
                position = self._feature_position(corner)
                slot_segments[corner] = self._slot_segment(corner, position)
                feature_half_width = max(
                    feature_half_width, abs(position[0]) + x_extent
                )
                feature_half_length = max(
                    feature_half_length, abs(position[1]) + y_extent
                )

        if feature_half_width:
            self.CheckParam(
                "Package",
                "width",
                min_value=pcbnew.ToMM(2 * feature_half_width),
                info="guide pin features must remain inside the package",
            )
        if feature_half_length:
            self.CheckParam(
                "Package",
                "length",
                min_value=pcbnew.ToMM(2 * feature_half_length),
                info="guide pin features must remain inside the package",
            )

        pad_radius = pads["size"] / 2
        retained_pads = self._retained_pad_centers()

        if guide_hole_position is not None:
            clearance = guide_hole["diameter"] / 2 + pad_radius
            for pad_name, pad_position in retained_pads:
                if self._distance_squared(guide_hole_position, pad_position) < clearance**2:
                    message = "guide pin hole overlaps BGA pad {}".format(pad_name)
                    self.GetParam("Mounting layout", "x distance").AddError(message)
                    self.GetParam("Mounting layout", "y distance").AddError(message)
                    self.GetParam("Guide pin hole", "diameter").AddError(message)
                    break

        if slot_segments:
            slot_clearance = guide_slots["width"] / 2 + pad_radius
            for corner, segment in slot_segments.items():
                for pad_name, pad_position in retained_pads:
                    distance_squared = self._point_segment_distance_squared(
                        pad_position, segment
                    )
                    if distance_squared < slot_clearance**2:
                        message = "guide pin slot at {} overlaps BGA pad {}".format(
                            corner, pad_name
                        )
                        self.GetParam("Guide pin slots", corner).AddError(message)
                        break

        if guide_hole_position is not None:
            guide_slot_clearance = (
                guide_hole["diameter"] + guide_slots["width"]
            ) / 2
            for corner, segment in slot_segments.items():
                if self._point_segment_distance_squared(
                    guide_hole_position, segment
                ) < guide_slot_clearance**2:
                    message = "guide pin hole overlaps the slot at {}".format(corner)
                    self.GetParam("Guide pin hole", guide_hole_corner).AddError(message)
                    self.GetParam("Guide pin slots", corner).AddError(message)

        slot_items = list(slot_segments.items())
        for index, (first_corner, first_segment) in enumerate(slot_items):
            for second_corner, second_segment in slot_items[index + 1 :]:
                if self._segments_distance_squared(
                    first_segment, second_segment
                ) < guide_slots["width"] ** 2:
                    message = "guide pin slots at {} and {} overlap".format(
                        first_corner, second_corner
                    )
                    self.GetParam("Guide pin slots", first_corner).AddError(message)
                    self.GetParam("Guide pin slots", second_corner).AddError(message)

    def GetValue(self):
        pads = self.parameters["Pads"]
        opening = self.parameters["Center opening"]
        package = self.parameters["Package"]
        pin_count = sum(
            not self._pad_is_omitted(column, row)
            for row in range(pads["rows"])
            for column in range(pads["columns"])
        )

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

    @staticmethod
    def _inside_corner_depopulation(column, row, columns, rows, depth):
        if not depth:
            return False
        distance_from_horizontal_edge = min(column, columns - 1 - column)
        distance_from_vertical_edge = min(row, rows - 1 - row)
        return distance_from_horizontal_edge + distance_from_vertical_edge < depth

    def _pad_is_omitted(self, column, row):
        pads = self.parameters["Pads"]
        opening = self.parameters["Center opening"]
        return self._inside_opening(
            column,
            row,
            pads["columns"],
            pads["rows"],
            opening["columns"],
            opening["rows"],
        ) or self._inside_corner_depopulation(
            column,
            row,
            pads["columns"],
            pads["rows"],
            pads["corner depopulation depth"],
        )

    def _selected_corners(self, page):
        parameters = self.parameters[page]
        return [
            name for name, _x_sign, _y_sign in FEATURE_CORNERS if parameters[name]
        ]

    def _feature_position(self, corner):
        mounting_layout = self.parameters["Mounting layout"]
        for name, x_sign, y_sign in FEATURE_CORNERS:
            if name == corner:
                return (
                    x_sign * mounting_layout["x distance"],
                    y_sign * mounting_layout["y distance"],
                )
        raise ValueError("unknown feature corner: {}".format(corner))

    def _guide_pin_hole_position(self):
        corners = self._selected_corners("Guide pin hole")
        return self._feature_position(corners[0]) if corners else (0, 0)

    def _slot_rotation(self, corner):
        return self.parameters["Guide pin slots"]["{} rotation".format(corner)]

    def _slot_segment(self, corner, position):
        slots = self.parameters["Guide pin slots"]
        half_segment = (slots["length"] - slots["width"]) / 2
        rotation = math.radians(self._slot_rotation(corner))
        delta_x = math.cos(rotation) * half_segment
        delta_y = math.sin(rotation) * half_segment
        return (
            (position[0] - delta_x, position[1] - delta_y),
            (position[0] + delta_x, position[1] + delta_y),
        )

    @staticmethod
    def _distance_squared(first, second):
        return (first[0] - second[0]) ** 2 + (first[1] - second[1]) ** 2

    @classmethod
    def _point_segment_distance_squared(cls, point, segment):
        start, end = segment
        segment_x = end[0] - start[0]
        segment_y = end[1] - start[1]
        segment_length_squared = segment_x**2 + segment_y**2
        if not segment_length_squared:
            return cls._distance_squared(point, start)
        projection = (
            (point[0] - start[0]) * segment_x
            + (point[1] - start[1]) * segment_y
        ) / segment_length_squared
        projection = max(0, min(1, projection))
        closest = (
            start[0] + projection * segment_x,
            start[1] + projection * segment_y,
        )
        return cls._distance_squared(point, closest)

    @staticmethod
    def _cross_product(first, second, third):
        return (second[0] - first[0]) * (third[1] - first[1]) - (
            second[1] - first[1]
        ) * (third[0] - first[0])

    @classmethod
    def _point_on_segment(cls, point, segment):
        start, end = segment
        return (
            cls._cross_product(start, end, point) == 0
            and min(start[0], end[0]) <= point[0] <= max(start[0], end[0])
            and min(start[1], end[1]) <= point[1] <= max(start[1], end[1])
        )

    @classmethod
    def _segments_intersect(cls, first, second):
        first_start, first_end = first
        second_start, second_end = second
        first_start_side = cls._cross_product(first_start, first_end, second_start)
        first_end_side = cls._cross_product(first_start, first_end, second_end)
        second_start_side = cls._cross_product(second_start, second_end, first_start)
        second_end_side = cls._cross_product(second_start, second_end, first_end)
        if (
            first_start_side * first_end_side < 0
            and second_start_side * second_end_side < 0
        ):
            return True
        return (
            cls._point_on_segment(second_start, first)
            or cls._point_on_segment(second_end, first)
            or cls._point_on_segment(first_start, second)
            or cls._point_on_segment(first_end, second)
        )

    @classmethod
    def _segments_distance_squared(cls, first, second):
        if cls._segments_intersect(first, second):
            return 0
        return min(
            cls._point_segment_distance_squared(first[0], second),
            cls._point_segment_distance_squared(first[1], second),
            cls._point_segment_distance_squared(second[0], first),
            cls._point_segment_distance_squared(second[1], first),
        )

    def _retained_pad_centers(self):
        pads = self.parameters["Pads"]
        first_x = -((pads["columns"] - 1) * pads["pitch"]) / 2
        first_y = -((pads["rows"] - 1) * pads["pitch"]) / 2
        result = []
        for row in range(pads["rows"]):
            for column in range(pads["columns"]):
                if self._pad_is_omitted(column, row):
                    continue
                result.append(
                    (
                        "{}{}".format(self._row_name(row), column + 1),
                        (
                            first_x + column * pads["pitch"],
                            first_y + row * pads["pitch"],
                        ),
                    )
                )
        return result

    def _origin_offset(self):
        guide_hole = self.parameters["Guide pin hole"]
        if guide_hole["enabled"] and guide_hole["origin at guide pin hole"]:
            return self._guide_pin_hole_position()
        return 0, 0

    def _add_pads(self):
        pads = self.parameters["Pads"]
        columns = pads["columns"]
        rows = pads["rows"]
        pitch = pads["pitch"]
        origin_x, origin_y = self._origin_offset()

        prototype = PA.PadMaker(self.module).SMTRoundPad(pads["size"])
        first_x = -((columns - 1) * pitch) / 2
        first_y = -((rows - 1) * pitch) / 2

        for row in range(rows):
            for column in range(columns):
                if self._pad_is_omitted(column, row):
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

    def _add_guide_pin_hole(self):
        guide_hole = self.parameters["Guide pin hole"]
        if not guide_hole["enabled"]:
            return

        hole_x, hole_y = self._guide_pin_hole_position()
        origin_x, origin_y = self._origin_offset()
        pad = PA.PadMaker(self.module).NPTHRoundPad(guide_hole["diameter"])
        pad.SetPosition(
            pcbnew.VECTOR2I(int(hole_x - origin_x), int(hole_y - origin_y))
        )
        self.module.Add(pad)

    def _add_guide_pin_slots(self):
        slots = self.parameters["Guide pin slots"]
        origin_x, origin_y = self._origin_offset()
        for corner in self._selected_corners("Guide pin slots"):
            slot_x, slot_y = self._feature_position(corner)
            pad = pcbnew.PAD(self.module)
            pad.SetSize(
                pcbnew.VECTOR2I(int(slots["length"]), int(slots["width"]))
            )
            pad.SetShape(pcbnew.PAD_SHAPE_OVAL)
            pad.SetAttribute(pcbnew.PAD_ATTRIB_NPTH)
            pad.SetLayerSet(pad.UnplatedHoleMask())
            pad.SetDrillShape(pcbnew.PAD_DRILL_SHAPE_OBLONG)
            pad.SetDrillSize(
                pcbnew.VECTOR2I(int(slots["length"]), int(slots["width"]))
            )
            pad.SetOrientation(
                pcbnew.EDA_ANGLE(self._slot_rotation(corner), pcbnew.DEGREES_T)
            )
            pad.SetPosition(
                pcbnew.VECTOR2I(int(slot_x - origin_x), int(slot_y - origin_y))
            )
            self.module.Add(pad)

    def BuildThisFootprint(self):
        self._add_pads()
        self._add_guide_pin_hole()
        self._add_guide_pin_slots()

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
