"""BGA interposer footprint wizard for KiCad 9 and 10.

The drawing and parameter conventions intentionally follow KiCad's bundled
``bga_wizard.py``.  Pad names retain their full-grid row and column locations,
so center pad depopulation does not renumber the surrounding balls.
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

PACKAGE_CHAMFER_PARAMETERS = {
    "top left (A1 corner)": "chamfer top left (A1)",
    "top right": "chamfer top right",
    "bottom right": "chamfer bottom right",
    "bottom left": "chamfer bottom left",
}


class BGAInterposerWizard(FootprintWizardBase.FootprintWizard):
    """Generate a BGA interposer with optional depopulation and cutouts."""

    def GetName(self):
        return "BGA Interposer"

    def GetDescription(self):
        return (
            "BGA interposer with depopulation, perimeter naming, board cutout, "
            "and guide features"
        )

    def GenerateParameterList(self):
        # AddParam declaration order is the wizard UI order. Keep the package
        # envelope and mechanical features ahead of pads and openings so a
        # drawing can be entered without transient geometry-validation errors.
        self.AddParam("Package", "width", self.uMM, 26.0, designator="X")
        self.AddParam("Package", "length", self.uMM, 26.0, designator="Y")
        self.AddParam(
            "Package",
            "chamfer size",
            self.uMM,
            1.0,
            min_value=0,
        )
        for name, _x_sign, _y_sign in FEATURE_CORNERS:
            self.AddParam(
                "Package",
                PACKAGE_CHAMFER_PARAMETERS[name],
                self.uBool,
                name == "top left (A1 corner)",
            )
        self.AddParam(
            "Package",
            "courtyard margin",
            self.uMM,
            0.25,
            min_value=0.2,
        )

        self.AddParam(
            "Guide feat placement",
            "x offset",
            self.uMM,
            11.5,
            min_value=0.01,
            hint="Positive offset magnitude from package center; corner selects the sign",
        )
        self.AddParam(
            "Guide feat placement",
            "y offset",
            self.uMM,
            11.5,
            min_value=0.01,
            hint="Positive offset magnitude from package center; corner selects the sign",
        )

        self.AddParam("Guide pin hole", "enabled", self.uBool, True)
        self.AddParam(
            "Guide pin hole",
            "diameter",
            self.uMM,
            2.5,
            min_value=0.01,
            hint="Finished round NPTH diameter",
        )
        self.AddParam(
            "Guide pin hole", "top left (A1 corner)", self.uBool, True
        )
        self.AddParam("Guide pin hole", "top right", self.uBool, False)
        self.AddParam("Guide pin hole", "bottom right", self.uBool, False)
        self.AddParam("Guide pin hole", "bottom left", self.uBool, False)
        self.AddParam(
            "Guide pin hole",
            "origin at guide pin hole",
            self.uBool,
            False,
            hint="Otherwise the footprint origin is the package center",
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
            "Pads", "pitch", self.uMM, 1.0, designator="p", min_value=0.01
        )
        self.AddParam("Pads", "size", self.uMils, 20, min_value=0.01)
        self.AddParam("Pads", "solder paste", self.uBool, True)
        self.AddParam(
            "Pads",
            "corner depop depth",
            self.uInteger,
            0,
            min_value=0,
            hint="Symmetric triangular corner removal; 4 removes 4, 3, 2, 1 pads",
        )
        self.AddParam(
            "Pads", "columns", self.uInteger, 20, designator="nx", min_value=3
        )
        self.AddParam(
            "Pads", "rows", self.uInteger, 20, designator="ny", min_value=3
        )

        self.AddParam("Perimeter naming", "enabled", self.uBool, False)
        self.AddParam("Perimeter naming", "prefix", self.uString, "GND")
        self.AddParam(
            "Perimeter naming",
            "row depth",
            self.uInteger,
            1,
            min_value=1,
            hint="Number of renamed rows at both top and bottom",
        )
        self.AddParam(
            "Perimeter naming",
            "column depth",
            self.uInteger,
            1,
            min_value=1,
            hint="Number of renamed columns at both left and right",
        )

        self.AddParam(
            "Center pad depop",
            "columns",
            self.uInteger,
            8,
            designator="ox",
            min_value=0,
            hint="Number of omitted pad columns in the centered rectangle",
        )
        self.AddParam(
            "Center pad depop",
            "rows",
            self.uInteger,
            8,
            designator="oy",
            min_value=0,
            hint="Number of omitted pad rows in the centered rectangle",
        )

        self.AddParam("Center board cutout", "enabled", self.uBool, False)
        self.AddParam(
            "Center board cutout",
            "width",
            self.uMM,
            5.0,
            designator="X",
            min_value=0.01,
        )
        self.AddParam(
            "Center board cutout",
            "length",
            self.uMM,
            5.0,
            designator="Y",
            min_value=0.01,
        )
        self.AddParam(
            "Center board cutout",
            "corner radius",
            self.uMM,
            0,
            min_value=0,
        )

        self.AddParam("Silkscreen", "corners", self.uBool, True)
        self.AddParam(
            "Silkscreen",
            "corner offset",
            self.uMM,
            0.12,
            min_value=0,
        )
        self.AddParam("Silkscreen", "pin 1 dot", self.uBool, True)
        self.AddParam(
            "Silkscreen",
            "pin 1 dot offset",
            self.uMM,
            0.7,
            min_value=0,
            hint="Radial distance outward from the top-left package vertex or chamfer midpoint",
        )
        self.AddParam(
            "Silkscreen", "pin 1 dot diameter", self.uMM, 0.4, min_value=0.01
        )
        self.AddParam("Silkscreen", "ref des", self.uBool, True)

    def CheckParameters(self):
        pads = self.parameters["Pads"]
        center_depopulation = self.parameters["Center pad depop"]
        center_cutout = self.parameters["Center board cutout"]
        perimeter_naming = self.parameters["Perimeter naming"]
        package = self.parameters["Package"]
        silkscreen = self.parameters["Silkscreen"]
        guide_hole = self.parameters["Guide pin hole"]
        guide_slots = self.parameters["Guide pin slots"]

        columns = pads["columns"]
        rows = pads["rows"]
        depopulation_columns = center_depopulation["columns"]
        depopulation_rows = center_depopulation["rows"]

        if perimeter_naming["enabled"]:
            self.CheckParam(
                "Perimeter naming",
                "row depth",
                max_value=(rows - 1) // 2,
                info="perimeter naming must leave at least one interior row",
            )
            self.CheckParam(
                "Perimeter naming",
                "column depth",
                max_value=(columns - 1) // 2,
                info="perimeter naming must leave at least one interior column",
            )

            prefix = str(perimeter_naming["prefix"])
            prefix_param = self.GetParam("Perimeter naming", "prefix")
            if not prefix.strip():
                prefix_param.AddError("prefix must not be empty")
            else:
                seen_designations = set()
                duplicate_designations = set()
                for designation in self._pad_designations().values():
                    if designation in seen_designations:
                        duplicate_designations.add(designation)
                    seen_designations.add(designation)
                if duplicate_designations:
                    prefix_param.AddError(
                        "prefix creates duplicate pad designation {}".format(
                            sorted(duplicate_designations)[0]
                        )
                    )

        self.CheckParam(
            "Pads",
            "size",
            max_value=pcbnew.ToMils(pads["pitch"]),
            info="pad diameter cannot exceed pad pitch",
        )

        chamfered_corners = {
            name: package[PACKAGE_CHAMFER_PARAMETERS[name]]
            for name, _x_sign, _y_sign in FEATURE_CORNERS
        }
        has_chamfered_corners = bool(
            package["chamfer size"] and any(chamfered_corners.values())
        )
        if has_chamfered_corners:
            edge_limits = []
            for first, second, edge_length in (
                ("top left (A1 corner)", "top right", package["width"]),
                ("bottom left", "bottom right", package["width"]),
                ("top left (A1 corner)", "bottom left", package["length"]),
                ("top right", "bottom right", package["length"]),
            ):
                chamfer_count = int(chamfered_corners[first]) + int(
                    chamfered_corners[second]
                )
                if chamfer_count:
                    edge_limits.append(edge_length / chamfer_count)
            self.CheckParam(
                "Package",
                "chamfer size",
                max_value=pcbnew.ToMM(min(edge_limits)),
                info="adjacent corner chamfers must fit on every package edge",
            )

        silk_line_width = self._line_width(pcbnew.F_SilkS)
        silk_half_width = silk_line_width / 2
        if silkscreen["corners"]:
            minimum_corner_offset = silk_half_width
            if has_chamfered_corners:
                minimum_corner_offset = max(
                    minimum_corner_offset, silk_line_width / math.sqrt(2)
                )

            half_width = package["width"] / 2
            half_length = package["length"] / 2
            margin = package["courtyard margin"]
            courtyard_half_width = (
                pcbnew.PutOnGridMM((half_width + margin) * 2, 0.1) / 2
            )
            courtyard_half_length = (
                pcbnew.PutOnGridMM((half_length + margin) * 2, 0.1) / 2
            )
            maximum_corner_offset = min(
                courtyard_half_width - half_width,
                courtyard_half_length - half_length,
            ) - silk_half_width

            if has_chamfered_corners:
                maximum_corner_offset = min(
                    maximum_corner_offset,
                    package["width"] / 4 - package["chamfer size"],
                    package["length"] / 4 - package["chamfer size"],
                )

            self.CheckParam(
                "Silkscreen",
                "corner offset",
                min_value=pcbnew.ToMM(minimum_corner_offset),
                max_value=pcbnew.ToMM(maximum_corner_offset),
                info="corner marks must clear the package and fit inside the courtyard",
            )

        if silkscreen["pin 1 dot"]:
            dot_radius = silkscreen["pin 1 dot diameter"] / 2
            minimum_dot_offset = dot_radius
            if silkscreen["corners"]:
                if (
                    package["chamfer size"]
                    and chamfered_corners["top left (A1 corner)"]
                ):
                    corner_mark_offset = silkscreen["corner offset"] / math.sqrt(2)
                else:
                    corner_mark_offset = silkscreen["corner offset"] * math.sqrt(2)
                minimum_dot_offset = max(
                    minimum_dot_offset,
                    corner_mark_offset + dot_radius + silk_half_width,
                )

            self.CheckParam(
                "Silkscreen",
                "pin 1 dot offset",
                min_value=pcbnew.ToMM(minimum_dot_offset),
                max_value=pcbnew.ToMM(
                    min(package["width"], package["length"]) / 2
                ),
                info="pin 1 dot must remain outside the package and corner mark",
            )
            self.CheckParam(
                "Silkscreen",
                "pin 1 dot diameter",
                max_value=pcbnew.ToMM(2 * silkscreen["pin 1 dot offset"]),
                info="pin 1 dot must remain outside the package outline",
            )

        self.CheckParam(
            "Pads",
            "corner depop depth",
            max_value=(min(columns, rows) - 1) // 2,
            info="at least one pad position must remain between opposing corner cuts",
        )

        # Non-empty center pad depopulation must leave at least one row and
        # column on every side. Matching parity puts its center at (0, 0).
        self.CheckParam(
            "Center pad depop", "columns", max_value=max(0, columns - 2)
        )
        self.CheckParam("Center pad depop", "rows", max_value=max(0, rows - 2))

        depopulation_columns_param = self.GetParam("Center pad depop", "columns")
        depopulation_rows_param = self.GetParam("Center pad depop", "rows")

        if (depopulation_columns == 0) != (depopulation_rows == 0):
            message = "both center depop dimensions must be zero or positive"
            depopulation_columns_param.AddError(message)
            depopulation_rows_param.AddError(message)

        if depopulation_columns and (columns - depopulation_columns) % 2:
            depopulation_columns_param.AddError(
                "outer and center depop column counts must have matching parity",
                "required for depopulation centered at the package center",
            )

        if depopulation_rows and (rows - depopulation_rows) % 2:
            depopulation_rows_param.AddError(
                "outer and center depop row counts must have matching parity",
                "required for depopulation centered at the package center",
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

        if center_cutout["enabled"]:
            maximum_radius = min(center_cutout["width"], center_cutout["length"]) / 2
            self.CheckParam(
                "Center board cutout",
                "corner radius",
                max_value=pcbnew.ToMM(maximum_radius),
                info="corner radius cannot exceed half the shorter dimension",
            )

            if center_cutout["width"] >= package["width"]:
                self.GetParam("Center board cutout", "width").AddError(
                    "must be smaller than the package width"
                )
            if center_cutout["length"] >= package["length"]:
                self.GetParam("Center board cutout", "length").AddError(
                    "must be smaller than the package length"
                )

            if center_cutout["corner radius"] <= maximum_radius:
                half_width = center_cutout["width"] / 2
                half_length = center_cutout["length"] / 2
                radius = center_cutout["corner radius"]
                for pad_name, pad_position in retained_pads:
                    distance = self._rounded_rectangle_signed_distance(
                        pad_position, half_width, half_length, radius
                    )
                    if distance < pad_radius:
                        message = "center board cutout overlaps BGA pad {}".format(
                            pad_name
                        )
                        self.GetParam("Center board cutout", "width").AddError(
                            message
                        )
                        self.GetParam("Center board cutout", "length").AddError(
                            message
                        )
                        break

        if guide_hole_position is not None:
            clearance = guide_hole["diameter"] / 2 + pad_radius
            for pad_name, pad_position in retained_pads:
                if self._distance_squared(guide_hole_position, pad_position) < clearance**2:
                    message = "guide pin hole overlaps BGA pad {}".format(pad_name)
                    self.GetParam(
                        "Guide feat placement", "x offset"
                    ).AddError(message)
                    self.GetParam(
                        "Guide feat placement", "y offset"
                    ).AddError(message)
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
        center_depopulation = self.parameters["Center pad depop"]
        perimeter_naming = self.parameters["Perimeter naming"]
        package = self.parameters["Package"]
        pin_count = sum(
            not self._pad_is_omitted(column, row)
            for row in range(pads["rows"])
            for column in range(pads["columns"])
        )

        perimeter_suffix = ""
        if perimeter_naming["enabled"]:
            perimeter_suffix = "_PerimeterR{rows}C{columns}_{prefix}".format(
                rows=perimeter_naming["row depth"],
                columns=perimeter_naming["column depth"],
                prefix=self._footprint_name_token(perimeter_naming["prefix"]),
            )

        return (
            "BGA-Interposer-{pins}_{columns}x{rows}_"
            "CenterDepopulation{depopulation_columns}x"
            "{depopulation_rows}{perimeter_suffix}_"
            "{width:g}x{length:g}mm_P{pitch:g}mm"
        ).format(
            pins=pin_count,
            columns=pads["columns"],
            rows=pads["rows"],
            depopulation_columns=center_depopulation["columns"],
            depopulation_rows=center_depopulation["rows"],
            perimeter_suffix=perimeter_suffix,
            width=pcbnew.ToMM(package["width"]),
            length=pcbnew.ToMM(package["length"]),
            pitch=pcbnew.ToMM(pads["pitch"]),
        )

    @staticmethod
    def _footprint_name_token(value):
        """Encode a user prefix as a reversible footprint-name token."""
        result = []
        for character in str(value):
            if character.isascii() and (
                character.isalnum() or character in "_+-"
            ) and character != "x":
                result.append(character)
            else:
                result.append("x{:X}x".format(ord(character)))
        return "".join(result)

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
    def _inside_center_depopulation(
        column, row, columns, rows, depopulation_columns, depopulation_rows
    ):
        if not depopulation_columns or not depopulation_rows:
            return False
        first_column = (columns - depopulation_columns) // 2
        first_row = (rows - depopulation_rows) // 2
        return (
            first_column <= column < first_column + depopulation_columns
            and first_row <= row < first_row + depopulation_rows
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
        center_depopulation = self.parameters["Center pad depop"]
        return self._inside_center_depopulation(
            column,
            row,
            pads["columns"],
            pads["rows"],
            center_depopulation["columns"],
            center_depopulation["rows"],
        ) or self._inside_corner_depopulation(
            column,
            row,
            pads["columns"],
            pads["rows"],
            pads["corner depop depth"],
        )

    def _inside_perimeter(self, column, row):
        pads = self.parameters["Pads"]
        perimeter = self.parameters["Perimeter naming"]
        return (
            row < perimeter["row depth"]
            or row >= pads["rows"] - perimeter["row depth"]
            or column < perimeter["column depth"]
            or column >= pads["columns"] - perimeter["column depth"]
        )

    def _pad_designations(self):
        """Return retained pad designations keyed by physical grid position."""
        pads = self.parameters["Pads"]
        perimeter = self.parameters["Perimeter naming"]
        designations = {}
        perimeter_number = 0

        for row in range(pads["rows"]):
            for column in range(pads["columns"]):
                if self._pad_is_omitted(column, row):
                    continue

                if perimeter["enabled"] and self._inside_perimeter(column, row):
                    perimeter_number += 1
                    designation = "{}{}".format(
                        perimeter["prefix"], perimeter_number
                    )
                else:
                    interior_row = row
                    interior_column = column
                    if perimeter["enabled"]:
                        interior_row -= perimeter["row depth"]
                        interior_column -= perimeter["column depth"]
                    designation = "{}{}".format(
                        self._row_name(interior_row), interior_column + 1
                    )

                designations[(column, row)] = designation

        return designations

    @staticmethod
    def _rounded_rectangle_signed_distance(point, half_width, half_length, radius):
        """Return distance to a centered, axis-aligned rounded rectangle."""
        x_distance = abs(point[0]) - (half_width - radius)
        y_distance = abs(point[1]) - (half_length - radius)
        outside_distance = math.hypot(max(x_distance, 0), max(y_distance, 0))
        inside_distance = min(max(x_distance, y_distance), 0)
        return outside_distance + inside_distance - radius

    def _selected_corners(self, page):
        parameters = self.parameters[page]
        return [
            name for name, _x_sign, _y_sign in FEATURE_CORNERS if parameters[name]
        ]

    def _feature_position(self, corner):
        guide_feature_placement = self.parameters["Guide feat placement"]
        for name, x_sign, y_sign in FEATURE_CORNERS:
            if name == corner:
                return (
                    x_sign * guide_feature_placement["x offset"],
                    y_sign * guide_feature_placement["y offset"],
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
        designations = self._pad_designations()
        first_x = -((pads["columns"] - 1) * pads["pitch"]) / 2
        first_y = -((pads["rows"] - 1) * pads["pitch"]) / 2
        result = []
        for row in range(pads["rows"]):
            for column in range(pads["columns"]):
                if (column, row) not in designations:
                    continue
                result.append(
                    (
                        designations[(column, row)],
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
        prototype.SetProperty(pcbnew.PAD_PROP_BGA)
        if not pads["solder paste"]:
            layers = prototype.GetLayerSet()
            layers.RemoveLayer(pcbnew.F_Paste)
            prototype.SetLayerSet(layers)
        first_x = -((columns - 1) * pitch) / 2
        first_y = -((rows - 1) * pitch) / 2
        designations = self._pad_designations()

        for row in range(rows):
            for column in range(columns):
                if (column, row) not in designations:
                    continue

                pad = prototype.Duplicate()
                pad.SetName(designations[(column, row)])
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
        pad.SetProperty(pcbnew.PAD_PROP_NONE)
        pad.SetLayerSet(self._npth_layer_set())
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
            pad.SetProperty(pcbnew.PAD_PROP_NONE)
            pad.SetLayerSet(self._npth_layer_set())
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

    @staticmethod
    def _npth_layer_set():
        """Return KiCad's standard all-copper-and-mask NPTH layer set."""
        # The pad and drill are the same size, so this creates no annular
        # copper. Official KiCad library NPTH footprints use *.Cu + *.Mask.
        layers = pcbnew.LSET(pcbnew.LSET.AllCuMask())
        layers.AddLayer(pcbnew.F_Mask)
        layers.AddLayer(pcbnew.B_Mask)
        return layers

    def _add_cutout_line(self, layer, line_width, start, end):
        line = pcbnew.PCB_SHAPE(self.module)
        line.SetShape(pcbnew.SHAPE_T_SEGMENT)
        line.SetLayer(layer)
        line.SetWidth(line_width)
        line.SetStartEnd(
            self.draw.TransformPoint(*start), self.draw.TransformPoint(*end)
        )
        self.module.Add(line)

    def _add_cutout_arc(self, layer, line_width, center, start):
        arc = pcbnew.PCB_SHAPE(self.module)
        arc.SetShape(pcbnew.SHAPE_T_ARC)
        arc.SetLayer(layer)
        arc.SetWidth(line_width)
        arc.SetCenter(self.draw.TransformPoint(*center))
        arc.SetStart(self.draw.TransformPoint(*start))
        arc.SetArcAngleAndEnd(pcbnew.EDA_ANGLE(90, pcbnew.DEGREES_T), True)
        self.module.Add(arc)

    @staticmethod
    def _supports_native_rounded_rectangle():
        return hasattr(pcbnew.PCB_SHAPE, "SetCornerRadius")

    def _add_center_board_cutout_on_layer(self, layer, line_width):
        cutout = self.parameters["Center board cutout"]
        half_width = cutout["width"] / 2
        half_length = cutout["length"] / 2
        radius = cutout["corner radius"]

        # KiCad 10 supports a radius on its native rectangle object. KiCad 9
        # uses exact line and arc primitives for the rounded form instead.
        if not radius or self._supports_native_rounded_rectangle():
            rectangle = pcbnew.PCB_SHAPE(self.module)
            rectangle.SetShape(pcbnew.SHAPE_T_RECT)
            rectangle.SetLayer(layer)
            rectangle.SetWidth(line_width)
            rectangle.SetFilled(False)
            rectangle.SetStart(self.draw.TransformPoint(-half_width, -half_length))
            rectangle.SetEnd(self.draw.TransformPoint(half_width, half_length))
            if radius:
                rectangle.SetCornerRadius(int(radius))
            self.module.Add(rectangle)
            return

        self._add_cutout_line(
            layer,
            line_width,
            (-half_width + radius, -half_length),
            (half_width - radius, -half_length),
        )
        self._add_cutout_line(
            layer,
            line_width,
            (half_width, -half_length + radius),
            (half_width, half_length - radius),
        )
        self._add_cutout_line(
            layer,
            line_width,
            (half_width - radius, half_length),
            (-half_width + radius, half_length),
        )
        self._add_cutout_line(
            layer,
            line_width,
            (-half_width, half_length - radius),
            (-half_width, -half_length + radius),
        )

        self._add_cutout_arc(
            layer,
            line_width,
            (half_width - radius, -half_length + radius),
            (half_width - radius, -half_length),
        )
        self._add_cutout_arc(
            layer,
            line_width,
            (half_width - radius, half_length - radius),
            (half_width, half_length - radius),
        )
        self._add_cutout_arc(
            layer,
            line_width,
            (-half_width + radius, half_length - radius),
            (-half_width + radius, half_length),
        )
        self._add_cutout_arc(
            layer,
            line_width,
            (-half_width + radius, -half_length + radius),
            (-half_width, -half_length + radius),
        )

    def _add_center_board_cutout(self):
        if not self.parameters["Center board cutout"]["enabled"]:
            return
        self._add_center_board_cutout_on_layer(
            pcbnew.Edge_Cuts, self._line_width(pcbnew.Edge_Cuts)
        )
        self._add_center_board_cutout_on_layer(
            pcbnew.F_Fab, self._line_width(pcbnew.F_Fab)
        )

    @staticmethod
    def _line_width(layer):
        """Return the active editor default, or KiCad's native fallback."""
        board = pcbnew.GetBoard()
        if board is None:
            board = pcbnew.BOARD()
        return board.GetDesignSettings().GetLineThickness(layer)

    def _package_outline_points(
        self, half_width, half_length, chamfer_size=None
    ):
        package = self.parameters["Package"]
        chamfer = (
            package["chamfer size"]
            if chamfer_size is None
            else chamfer_size
        )
        top_left = (
            package[PACKAGE_CHAMFER_PARAMETERS["top left (A1 corner)"]]
            and chamfer
        )
        top_right = package[PACKAGE_CHAMFER_PARAMETERS["top right"]] and chamfer
        bottom_right = (
            package[PACKAGE_CHAMFER_PARAMETERS["bottom right"]] and chamfer
        )
        bottom_left = (
            package[PACKAGE_CHAMFER_PARAMETERS["bottom left"]] and chamfer
        )

        points = [
            (-half_width + chamfer, -half_length)
            if top_left
            else (-half_width, -half_length)
        ]
        if top_right:
            points.extend(
                [
                    (half_width - chamfer, -half_length),
                    (half_width, -half_length + chamfer),
                ]
            )
        else:
            points.append((half_width, -half_length))
        if bottom_right:
            points.extend(
                [
                    (half_width, half_length - chamfer),
                    (half_width - chamfer, half_length),
                ]
            )
        else:
            points.append((half_width, half_length))
        if bottom_left:
            points.extend(
                [
                    (-half_width + chamfer, half_length),
                    (-half_width, half_length - chamfer),
                ]
            )
        else:
            points.append((-half_width, half_length))
        if top_left:
            points.append((-half_width, -half_length + chamfer))
        points.append(points[0])
        return points

    def _silkscreen_corner_points(
        self, corner, x_sign, y_sign, half_width, half_length
    ):
        silkscreen = self.parameters["Silkscreen"]
        package = self.parameters["Package"]
        corner_offset = silkscreen["corner offset"]
        outer_half_width = half_width + corner_offset
        outer_half_length = half_length + corner_offset
        horizontal_run = half_width / 2
        vertical_run = half_length / 2

        horizontal_inner = (
            x_sign * (outer_half_width - horizontal_run),
            y_sign * outer_half_length,
        )
        vertical_inner = (
            x_sign * outer_half_width,
            y_sign * (outer_half_length - vertical_run),
        )

        chamfer = (
            package["chamfer size"]
            if package[PACKAGE_CHAMFER_PARAMETERS[corner]]
            else 0
        )
        if not chamfer:
            return [
                horizontal_inner,
                (x_sign * outer_half_width, y_sign * outer_half_length),
                vertical_inner,
            ]

        tangent_offset = chamfer + corner_offset
        points = [
            horizontal_inner,
            (
                x_sign * (outer_half_width - tangent_offset),
                y_sign * outer_half_length,
            ),
            (
                x_sign * outer_half_width,
                y_sign * (outer_half_length - tangent_offset),
            ),
            vertical_inner,
        ]
        if tangent_offset > horizontal_run:
            points = points[1:]
        if tangent_offset > vertical_run:
            points = points[:-1]
        return points

    def _pin_1_dot_position(self, half_width, half_length):
        package = self.parameters["Package"]
        silkscreen = self.parameters["Silkscreen"]
        chamfer = 0
        if package[PACKAGE_CHAMFER_PARAMETERS["top left (A1 corner)"]]:
            chamfer = package["chamfer size"]

        anchor_x = -half_width + chamfer / 2
        anchor_y = -half_length + chamfer / 2
        offset_component = silkscreen["pin 1 dot offset"] / math.sqrt(2)
        return anchor_x - offset_component, anchor_y - offset_component

    def BuildThisFootprint(self):
        self._add_pads()
        self._add_guide_pin_hole()
        self._add_guide_pin_slots()

        package = self.parameters["Package"]
        half_width = package["width"] / 2
        half_length = package["length"] / 2
        origin_x, origin_y = self._origin_offset()
        self.draw.TransformTranslate(-origin_x, -origin_y)

        # Fabrication outline with independently selectable corner chamfers.
        self.draw.SetLayer(pcbnew.F_Fab)
        self.draw.SetLineThickness(self._line_width(pcbnew.F_Fab))
        self.draw.Polyline(self._package_outline_points(half_width, half_length))

        # The functional board cutout and matching fabrication drawing share
        # geometry but retain their layer-standard line widths.
        self._add_center_board_cutout()

        # Sparse silkscreen corners follow the fabrication chamfers at the
        # configured offset distance.
        silkscreen = self.parameters["Silkscreen"]
        self.draw.SetLayer(pcbnew.F_SilkS)
        self.draw.SetLineThickness(self._line_width(pcbnew.F_SilkS))
        if silkscreen["corners"]:
            for corner, x_sign, y_sign in FEATURE_CORNERS:
                self.draw.Polyline(
                    self._silkscreen_corner_points(
                        corner,
                        x_sign,
                        y_sign,
                        half_width,
                        half_length,
                    )
                )
        if silkscreen["pin 1 dot"]:
            pin_1_dot_x, pin_1_dot_y = self._pin_1_dot_position(
                half_width, half_length
            )
            self.draw.Circle(
                pin_1_dot_x,
                pin_1_dot_y,
                int(silkscreen["pin 1 dot diameter"] / 2),
                filled=True,
            )

        # Courtyard dimensions are rounded to the 0.1 mm size grid, as in
        # KiCad's wizard. Its selected chamfers are true geometric offsets of
        # the fabrication outline, preserving the effective courtyard margin
        # along both straight and diagonal edges.
        margin = package["courtyard margin"]
        self.draw.SetLayer(pcbnew.F_CrtYd)
        courtyard_width = pcbnew.PutOnGridMM((half_width + margin) * 2, 0.1)
        courtyard_length = pcbnew.PutOnGridMM((half_length + margin) * 2, 0.1)
        courtyard_half_width = courtyard_width / 2
        courtyard_half_length = courtyard_length / 2
        x_margin = courtyard_half_width - half_width
        y_margin = courtyard_half_length - half_length
        effective_margin = min(x_margin, y_margin)
        courtyard_chamfer = 0
        if package["chamfer size"]:
            courtyard_chamfer = (
                package["chamfer size"]
                + x_margin
                + y_margin
                - math.sqrt(2) * effective_margin
            )
        self.draw.SetLineThickness(self._line_width(pcbnew.F_CrtYd))
        self.draw.Polyline(
            self._package_outline_points(
                courtyard_half_width,
                courtyard_half_length,
                courtyard_chamfer,
            )
        )

        text_size = self.GetTextSize()
        text_y = half_length + text_size
        self.draw.Value(0, text_y, text_size)
        if silkscreen["ref des"]:
            self.draw.Reference(0, -text_y, text_size)
        else:
            self.module.Reference().SetVisible(False)

        extra_reference = pcbnew.PCB_TEXT(self.module)
        extra_reference.SetLayer(pcbnew.F_Fab)
        extra_reference.SetPosition(
            pcbnew.VECTOR2I(int(-origin_x), int(-origin_y))
        )
        extra_reference.SetTextSize(pcbnew.VECTOR2I(text_size, text_size))
        extra_reference.SetText("${REFERENCE}")
        self.module.Add(extra_reference)

        if hasattr(self.module, "SetLibDescription"):
            self.module.SetLibDescription(
                "BGA interposer with depopulation, perimeter naming, board "
                "cutout, and guide features"
            )
        if hasattr(self.module, "SetKeywords"):
            self.module.SetKeywords("BGA interposer land grid array")
        self.module.SetAttributes(pcbnew.FP_SMD)
