"""Native wx librarian authoring UI, independent of project Workflow."""

from copy import deepcopy
import json
import math
from pathlib import Path

import pcbnew
import wx

from .model import (RFError, capability_report, clone_artifact, default_recipe,
                    generate_payload, load_artifact, make_artifact,
                    publish_bundle, revise_artifact)
from .native import read_base_part


class GeometryPreview(wx.Panel):
    def __init__(self, parent):
        super().__init__(parent, size=(460, 360))
        self.payload = None
        self.layer = "In2.Cu"
        self.SetMinSize((320, 260))
        self.Bind(wx.EVT_PAINT, self.paint)

    def paint(self, event):
        dc = wx.PaintDC(self)
        dc.SetBackground(wx.Brush(wx.Colour(250, 250, 250)))
        dc.Clear()
        if not self.payload:
            dc.DrawText("Validate a recipe to preview native geometry", 10, 12)
            return
        objects = self.payload["objects"]
        points = [o["at"] for o in objects if "at" in o]
        extent = max([3.0] + [max(abs(v) for v in p) + 1 for p in points])
        width, height = self.GetClientSize()
        scale = min(width, height) / (2 * extent + 1)
        radians = math.radians(self.payload.get("default_clock_deg", 0))
        cosine, sine = math.cos(radians), math.sin(radians)
        def xy(point):
            x, y = point
            return wx.Point(round(width / 2 + (x * cosine - y * sine) * scale),
                            round(height / 2 - (x * sine + y * cosine) * scale))
        dc.SetTextForeground(wx.Colour(30, 30, 30))
        dc.DrawText(f"{self.layer} · clock {self.payload.get('default_clock_deg', 0):g}° · mm", 10, 10)
        layers = self.payload.get("stackup", [])
        # Keepouts first, followed by copper and drilled vias.
        for obj in sorted(objects, key=lambda item: item["type"] != "keepout"):
            if obj["type"] == "keepout":
                if self.layer not in obj["layers"]:
                    continue
                dc.SetPen(wx.Pen(wx.Colour(180, 135, 40), 1))
                dc.SetBrush(wx.Brush(wx.Colour(255, 239, 190), wx.BRUSHSTYLE_CROSSDIAG_HATCH))
                dc.DrawPolygon([xy(p) for p in obj["points"]])
            else:
                if obj["type"] == "copper" and self.layer != obj["layer"]:
                    continue
                if obj["type"] == "via" and layers and self.layer not in layers[layers.index(obj["layers"][0]):layers.index(obj["layers"][1]) + 1]:
                    continue
                color = wx.Colour(185, 55, 55) if obj["net"] == "signal" else wx.Colour(40, 130, 95)
                dc.SetPen(wx.Pen(color, 1))
                dc.SetBrush(wx.Brush(color))
                dc.DrawCircle(xy(obj["at"]), max(1, round(obj["diameter"] * scale / 2)))
                if obj["type"] == "via":
                    dc.SetBrush(wx.Brush(wx.Colour(250, 250, 250)))
                    dc.DrawCircle(xy(obj["at"]), max(1, round(obj["drill"] * scale / 2)))
        dc.SetPen(wx.Pen(wx.Colour(40, 70, 155), 2))
        dc.DrawLine(xy([0, 0]), xy([extent * 0.85, 0]))
        dc.DrawLine(xy([extent * 0.85, 0]), xy([extent * 0.72, 0.15]))
        dc.DrawLine(xy([extent * 0.85, 0]), xy([extent * 0.72, -0.15]))


class RFDesignerDialog(wx.Dialog):
    """Edit a complete declarative recipe; immutable file publication only."""

    def __init__(self, parent=None):
        super().__init__(parent, title="GPPO RF Transition Designer — Librarian", size=(1180, 820),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.artifact = None
        self.recipe = default_recipe()
        self.draft_mode = "new"
        self.validated = None
        self.base_path = None
        root = wx.BoxSizer(wx.VERTICAL)
        self.SetSizer(root)
        root.Add(wx.StaticText(self, label="Author reusable RF artifacts from approved central parts. Source footprints stay read-only."), 0, wx.ALL, 10)
        tools = wx.BoxSizer(wx.HORIZONTAL)
        for label, handler in [("Open artifact…", self.open_artifact),
                               ("Select approved GPPO…", self.select_part),
                               ("Clone variant", self.clone), ("New revision", self.revision)]:
            button = wx.Button(self, label=label)
            button.Bind(wx.EVT_BUTTON, handler)
            tools.Add(button, 0, wx.RIGHT, 6)
        root.Add(tools, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        self.identity = wx.StaticText(self, label="New unsaved artifact · revision 1")
        root.Add(self.identity, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        splitter = wx.BoxSizer(wx.HORIZONTAL)
        left = wx.BoxSizer(wx.VERTICAL)
        left.Add(wx.StaticText(self, label="Complete recipe (JSON; millimetres, local +X exit, +Y up, angles CCW)"), 0, wx.BOTTOM, 5)
        self.editor = wx.TextCtrl(self, value=json.dumps(self.recipe, indent=2),
                                  style=wx.TE_MULTILINE | wx.TE_DONTWRAP)
        self.editor.SetFont(wx.Font(wx.FontInfo(10).Family(wx.FONTFAMILY_TELETYPE)))
        self.editor.Bind(wx.EVT_TEXT, self.invalidate)
        left.Add(self.editor, 1, wx.EXPAND)
        splitter.Add(left, 3, wx.EXPAND | wx.RIGHT, 10)
        right = wx.BoxSizer(wx.VERTICAL)
        self.layer = wx.Choice(self, choices=self.recipe["parameters"]["stackup"])
        self.layer.SetStringSelection("In2.Cu")
        self.layer.Bind(wx.EVT_CHOICE, self.change_layer)
        right.Add(self.layer, 0, wx.EXPAND | wx.BOTTOM, 5)
        self.preview = GeometryPreview(self)
        right.Add(self.preview, 1, wx.EXPAND)
        self.report = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY)
        right.Add(self.report, 1, wx.EXPAND | wx.TOP, 8)
        splitter.Add(right, 2, wx.EXPAND)
        root.Add(splitter, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)
        actions = wx.BoxSizer(wx.HORIZONTAL)
        validate = wx.Button(self, label="Validate / Preview")
        validate.Bind(wx.EVT_BUTTON, self.validate)
        actions.Add(validate, 0, wx.RIGHT, 6)
        self.save = wx.Button(self, label="Publish immutable library bundle…")
        self.save.Bind(wx.EVT_BUTTON, self.publish)
        self.save.Enable(False)
        actions.Add(self.save, 0, wx.RIGHT, 6)
        close = wx.Button(self, wx.ID_CLOSE, "Close")
        close.Bind(wx.EVT_BUTTON, lambda event: self.EndModal(wx.ID_CLOSE))
        actions.AddStretchSpacer()
        actions.Add(close)
        root.Add(actions, 0, wx.EXPAND | wx.ALL, 10)
        self.show_capabilities()
        self.CentreOnParent()

    def show_capabilities(self):
        self.report.SetValue(json.dumps(capability_report(pcbnew.Version()), indent=2))

    def error(self, error):
        self.save.Enable(False)
        self.validated = None
        self.report.SetValue(f"BLOCKED\n{error}\n\n" + json.dumps(capability_report(pcbnew.Version()), indent=2))

    def invalidate(self, event=None):
        self.validated = None
        if hasattr(self, "save"):
            self.save.Enable(False)
        if hasattr(self, "preview"):
            self.preview.payload = None
            self.preview.Refresh()
        if event:
            event.Skip()

    def set_recipe(self, recipe):
        self.recipe = deepcopy(recipe)
        self.editor.SetValue(json.dumps(self.recipe, indent=2))

    def open_artifact(self, event):
        with wx.FileDialog(self, "Reopen generation", wildcard="RF artifact (*.gppo-rf.json)|*.gppo-rf.json|JSON (*.json)|*.json",
                           style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as dialog:
            if dialog.ShowModal() != wx.ID_OK:
                return
            try:
                artifact = load_artifact(dialog.GetPath(), reopen=True)
                self.artifact = artifact
                self.base_path = None  # Portable saved pins do not depend on a prior selection.
                self.draft_mode = "existing"
                self.set_recipe(artifact["recipe"])
                self.identity.SetLabel(f"{artifact['artifact_id']} · pinned revision {artifact['revision']} · clone or create revision to change")
                self.validate(None)
            except (RFError, OSError, ValueError) as error:
                self.error(error)

    def select_part(self, event):
        with wx.FileDialog(self, "Select approved read-only GPPO", wildcard="KiCad footprint (*.kicad_mod)|*.kicad_mod",
                           style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as dialog:
            if dialog.ShowModal() != wx.ID_OK:
                return
            path = dialog.GetPath()
        with wx.TextEntryDialog(self, "Signal pad; comma-separated shield pads; mount side (front/back)",
                                "GPPO pad roles", "1;2;front") as roles:
            if roles.ShowModal() != wx.ID_OK:
                return
            try:
                signal, shields, side = roles.GetValue().split(";")
                recipe = json.loads(self.editor.GetValue())
                part = read_base_part(path, signal.strip(), [s.strip() for s in shields.split(",")],
                                      recipe["parameters"]["stackup"], side.strip(), approved=True)
                recipe["dependencies"]["base_part"] = part
                if part["mount"] == "PTH":
                    recipe["parameters"]["signal_via"] = None
                self.base_path = path
                self.set_recipe(recipe)
            except (RFError, ValueError, KeyError, TypeError) as error:
                self.error(error)

    def clone(self, event):
        if not self.artifact:
            return self.error("Open a saved artifact before cloning")
        self.draft_mode = "clone"
        self.identity.SetLabel(f"Clone of {self.artifact['artifact_id']} r{self.artifact['revision']} · new identity on publication")
        self.invalidate()

    def revision(self, event):
        if not self.artifact:
            return self.error("Open a saved artifact before creating a revision")
        self.draft_mode = "revision"
        self.identity.SetLabel(f"{self.artifact['artifact_id']} · new immutable revision {self.artifact['revision'] + 1}")
        self.invalidate()

    def change_layer(self, event):
        self.preview.layer = self.layer.GetStringSelection()
        self.preview.Refresh()

    def validate(self, event):
        try:
            if not capability_report(pcbnew.Version())["supported"]:
                raise RFError("unsupported KiCad runtime")
            recipe = json.loads(self.editor.GetValue())
            payload = generate_payload(recipe)
            self.layer.SetItems(recipe["parameters"]["stackup"])
            self.layer.SetStringSelection(recipe["parameters"]["signal_layer"])
            self.preview.layer = self.layer.GetStringSelection()
            self.preview.payload = {**payload, "stackup": recipe["parameters"]["stackup"]}
            self.preview.Refresh()
            if self.draft_mode == "existing":
                if recipe != self.artifact["recipe"]:
                    raise RFError("saved revisions are immutable; choose Clone variant or New revision")
                candidate = self.artifact
            elif self.draft_mode == "revision":
                candidate = revise_artifact(self.artifact, recipe)
            elif self.draft_mode == "clone":
                candidate = clone_artifact(self.artifact, recipe)
            else:
                candidate = make_artifact(recipe)
            self.validated = (self.editor.GetValue(), candidate)
            self.save.Enable(True)
            self.report.SetValue(f"VALIDATED · {len(payload['objects'])} board objects\nNo signal traces generated.\nPublication preserves source footprints.\n\n" +
                                 json.dumps(capability_report(pcbnew.Version()), indent=2))
        except (RFError, ValueError, KeyError, TypeError) as error:
            self.error(error)

    def publish(self, event):
        if not self.validated or self.validated[0] != self.editor.GetValue():
            return self.error("preview is stale; validate the current recipe")
        with wx.DirDialog(self, "Publish to librarian-managed RF library directory") as dialog:
            if dialog.ShowModal() != wx.ID_OK:
                return
            try:
                text, artifact = self.validated
                if self.base_path:
                    import hashlib
                    expected = artifact["recipe"]["dependencies"]["base_part"]["sha256"]
                    if hashlib.sha256(Path(self.base_path).read_bytes()).hexdigest() != expected:
                        raise RFError("approved base source changed; select and validate again")
                path = publish_bundle(dialog.GetPath(), artifact)
                self.artifact = artifact
                self.draft_mode = "existing"
                self.identity.SetLabel(f"{artifact['artifact_id']} · immutable revision {artifact['revision']}")
                self.report.SetValue(f"Published {path}\n\nExact payload, recipe and dependency pin preserved.\nSource part unchanged. Native geometry needs downstream project validation.")
            except (RFError, OSError, ValueError) as error:
                self.error(error)


class GPPORFTransitionDesignerPlugin(pcbnew.ActionPlugin):
    def defaults(self):
        self.name = "GPPO RF Transition Designer"
        self.category = "Library authoring"
        self.description = "Author and publish immutable GPPO RF transition recipes and board geometry"
        self.show_toolbar_button = False

    def Run(self):
        parent = next((window for window in wx.GetTopLevelWindows() if 'PCB Editor' in window.GetTitle()), None)
        dialog = RFDesignerDialog(parent)
        try:
            dialog.ShowModal()
        finally:
            dialog.Destroy()
