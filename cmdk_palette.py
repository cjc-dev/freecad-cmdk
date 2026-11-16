"""Cmd+K command palette for FreeCAD."""

import json
import os
import re
import time

import FreeCAD
import FreeCADGui as Gui
from PySide import QtCore, QtGui, QtWidgets

Qt = QtCore.Qt

PARAMS = "User parameter:BaseApp/Preferences/Mod/CmdK"
SHORTCUTS = "User parameter:BaseApp/Preferences/Shortcut"
COMMAND = "CmdK_Open"
DEFAULT_SHORTCUT = "Ctrl+K"
MAX_RECENT = 6
MAX_MOST_USED = 5
MAX_RESULTS = 60
# Rows whose labels are file names; never written to the usage file.
PRIVATE_COMMANDS = ("Std_RecentFiles", "Std_RecentMacros")
USAGE_LIMITS = {"queries": 500, "misses": 200, "pairs": 1000}
DEFAULT_WIDTH = 640
DEFAULT_ROWS = 10
MIN_TEXT = 14

# Fallback when the active theme's colours cannot be read.
DARK = dict(bg="#09090b", border="#27272a", fg="#fafafa", muted="#a1a1aa", accent="#27272a", dim=120)

CATEGORY_NAMES = {"Std": "FreeCAD", "TechDraw": "TechDraw", "OpenSCAD": "OpenSCAD"}

_palette = None
_shortcut = None
_watch = None


class Usage:
    """Local command-usage statistics. Nothing here leaves the machine."""

    def __init__(self):
        self.path = os.path.join(FreeCAD.getUserAppDataDir(), "CmdK", "usage.json")
        self.previous = None
        self.data = self._empty()
        try:
            with open(self.path, encoding="utf-8") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict) and loaded.get("version") == 1:
                self.data.update(loaded)
        except (OSError, ValueError):
            pass

    @staticmethod
    def _empty():
        return {"version": 1, "commands": {}, "queries": {}, "misses": {}, "pairs": {}}

    @staticmethod
    def enabled():
        return FreeCAD.ParamGet(PARAMS).GetBool("RecordUsage", True)

    def record_run(self, row, query, seconds):
        if not self.enabled() or row["id"].startswith("own:") or row["name"] in PRIVATE_COMMANDS:
            return
        key = row["id"]
        entry = self.data["commands"].setdefault(key, {"count": 0, "seconds": 0.0, "keys": 0})
        entry.update(
            label=row["label"], count=entry["count"] + 1, last=int(time.time()),
            seconds=round(entry["seconds"] + seconds, 2), keys=entry["keys"] + len(query),
        )
        typed = query.strip().lower()
        if typed:
            picks = self.data["queries"].setdefault(typed, {})
            picks[key] = picks.get(key, 0) + 1
        if self.previous:
            pair = "%s > %s" % (self.previous, key)
            self.data["pairs"][pair] = self.data["pairs"].get(pair, 0) + 1
        self.previous = key
        self.save()

    def record_miss(self, query):
        typed = query.strip().lower()
        if typed and self.enabled():
            self.data["misses"][typed] = self.data["misses"].get(typed, 0) + 1
            self.save()

    def count(self, key):
        return self.data["commands"].get(key, {}).get("count", 0)

    def picked(self, query):
        """Commands previously chosen for this query, or for one it is a prefix of."""
        typed, found = query.strip().lower(), set()
        if typed:
            for stored, picks in self.data["queries"].items():
                if stored.startswith(typed):
                    found.update(picks)
        return found

    def most_used(self):
        ranked = sorted(self.data["commands"].items(), key=lambda kv: -kv[1]["count"])
        return [key for key, entry in ranked if entry["count"] >= 2]

    def save(self):
        for name, limit in USAGE_LIMITS.items():
            table = self.data[name]
            if len(table) > limit:
                weight = (lambda v: sum(v.values())) if name == "queries" else (lambda v: v)
                keep = sorted(table, key=lambda k: -weight(table[k]))[:limit]
                self.data[name] = {k: table[k] for k in keep}
        self.write(self.path, self.data)

    def export(self, path):
        report = dict(self.data, exported=int(time.time()), freecad=".".join(FreeCAD.Version()[:3]))
        self.write(path, report)

    @staticmethod
    def write(path, data):
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path + ".tmp", "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=1, sort_keys=True)
            os.replace(path + ".tmp", path)
        except OSError as exc:
            FreeCAD.Console.PrintWarning("CmdK: could not write %s: %s\n" % (path, exc))

    def clear(self):
        self.data = self._empty()
        self.previous = None
        self.save()


def _mix(a, b, t):
    return QtGui.QColor(
        round(a.red() + (b.red() - a.red()) * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue() + (b.blue() - a.blue()) * t),
    )


def theme_tokens(mw):
    """Colours taken from the active FreeCAD theme.

    Hidden probe widgets are polished by the application stylesheet, which
    writes the theme's colours into their palettes: the menu gives the popup
    surface, the list gives text and selection colours.
    """
    try:
        role = QtGui.QPalette
        menu, lst = QtWidgets.QMenu(mw), QtWidgets.QListWidget(mw)
        for w in (menu, lst):
            w.setAttribute(Qt.WA_DontShowOnScreen, True)
            w.ensurePolished()
        menu.resize(60, 40)
        bg = menu.palette().color(role.Base)
        edge = menu.grab().toImage().pixelColor(0, 20)
        fg = lst.palette().color(role.Text)
        highlight = lst.palette().color(role.Highlight)
        for w in (menu, lst):
            w.deleteLater()
        if abs(fg.lightness() - bg.lightness()) < 60:
            return DARK
        dark = bg.lightness() < 128
        usable = edge.alpha() == 255 and edge != bg and not (dark and edge.lightness() < bg.lightness())
        border = edge if usable else _mix(bg, fg, 0.14)
        return dict(
            bg=bg.name(), fg=fg.name(), border=border.name(),
            muted=_mix(fg, bg, 0.42).name(), accent=_mix(bg, highlight, 0.5).name(),
            dim=120 if bg.lightness() < 128 else 70,
        )
    except Exception:
        return DARK


def metrics():
    """Sizes in pixels, scaled from the text size: FreeCAD's own font size
    unless the user picked one in the palette settings."""
    params = FreeCAD.ParamGet(PARAMS)
    text = params.GetInt("TextSize", 0)
    if text <= 0:
        app = QtWidgets.QApplication.instance()
        text = max(MIN_TEXT, QtGui.QFontInfo(app.font()).pixelSize())
    text = min(max(text, 10), 32)
    k = text / 14.0
    row = int(round(34 * k))
    rows = min(max(params.GetInt("Rows", DEFAULT_ROWS), 4), 30)
    return dict(text=text, small=int(round(12 * k)), row=row, head=int(round(30 * k)),
                input=int(round(46 * k)), icon=int(round(16 * k)),
                list=int((rows - 0.3) * row) + 8,
                width=min(max(params.GetInt("Width", DEFAULT_WIDTH), 360), 1600))


def _clean(text):
    text = re.sub(r"&(.)", r"\1", text or "")
    text = re.sub(r"^\d+\s+", "", text)
    return text.replace("…", "").replace("...", "").strip()


def _category(name):
    prefix = name.split("_", 1)[0]
    if prefix in CATEGORY_NAMES:
        return CATEGORY_NAMES[prefix]
    if prefix.isupper():
        return prefix
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", prefix)


def _workbench_icon(wb):
    icon = getattr(wb, "Icon", "")
    if not isinstance(icon, str) or not icon:
        return None
    if os.path.exists(icon):
        return QtGui.QIcon(icon)
    try:
        found = Gui.getIcon(icon)
        return None if found is None or found.isNull() else found
    except Exception:
        return None


def _shortcut_text(action):
    seq = action.shortcut()
    return seq.toString(QtGui.QKeySequence.NativeText) if not seq.isEmpty() else ""


def collect_items():
    """Every command that currently has a QAction, plus group members and workbenches.

    Commands without an action are skipped: their enabled state is unknown and
    Command.isActive() can segfault on them (e.g. Std_Expression).
    """
    items, seen = {}, set()
    for name in Gui.listCommands():
        if name in ("Std_Workbench", COMMAND):
            continue
        try:
            cmd = Gui.Command.get(name)
            actions = [a for a in (cmd.getAction() or []) if a is not None and not a.isSeparator()]
            info = cmd.getInfo()
        except Exception:
            continue
        if not actions:
            continue
        cat = _category(name)
        tip = info.get("toolTip", "")
        if len(actions) == 1:
            label = _clean(info.get("menuText") or actions[0].text())
            if label:
                items["cmd:" + name] = dict(
                    id="cmd:" + name, label=label, cat=cat, name=name, tip=tip,
                    action=actions[0], run=("action", actions[0]),
                )
                seen.add(label.lower())
            continue
        for i, act in enumerate(actions):
            label = _clean(act.text())
            if not label:
                continue
            key = "grp:%s:%d" % (name, i)
            items[key] = dict(
                id=key, label=label, cat=cat, name=name, tip=_clean(act.toolTip()) or tip,
                action=act, run=("group", name, i), member=True,
            )
    # Group members that duplicate a standalone command add nothing.
    for key in [k for k, it in items.items() if it.get("member") and it["label"].lower() in seen]:
        del items[key]

    try:
        active = Gui.activeWorkbench().__class__.__name__
    except Exception:
        active = ""
    for key, wb in Gui.listWorkbenches().items():
        label = getattr(wb, "MenuText", "") or key
        if key in ("NoneWorkbench", active) or not label:
            continue
        items["wb:" + key] = dict(
            id="wb:" + key, label=label, cat="Workbench", name=key, tip="", action=None,
            icon=_workbench_icon(wb), run=("workbench", key),
        )
    return items


def toolbar_groups(mw):
    """Visible main-window toolbars, in order, as (title, [command names])."""
    groups = []
    for tb in mw.findChildren(QtWidgets.QToolBar):
        if tb.parent() is not mw or not tb.isVisible():
            continue
        names = [a.data() for a in tb.actions() if isinstance(a.data(), str) and a.data() != "Separator"]
        if names:
            groups.append((_clean(tb.windowTitle()) or tb.objectName(), names))
    return groups


def score(query, item):
    label = item["label"].lower()
    names = "%s %s" % (item["cat"].lower(), item["name"].lower())
    total = 0
    for tok in query.lower().split():
        # Tooltips are long, so short fragments match them by accident.
        other = names + " " + item["tip"].lower() if len(tok) >= 4 else names
        if label.startswith(tok):
            s = 100
        elif re.search(r"(^|[\s\-_/(])" + re.escape(tok), label):
            s = 80
        elif tok in label:
            s = 60
        elif len(tok) > 1 and _initials(label).startswith(tok):
            s = 50
        elif tok in other:
            s = 25
        else:
            return None
        total += s
    return total - len(label) * 0.2


def _initials(label):
    return "".join(w[0] for w in re.split(r"[\s\-_/(]+", label) if w)


class Model(QtCore.QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []

    def set_rows(self, rows):
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def rowCount(self, parent=QtCore.QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index, role=Qt.DisplayRole):
        if role == Qt.UserRole:
            return self.rows[index.row()]
        if role == Qt.DisplayRole:
            row = self.rows[index.row()]
            return row.get("label") or row.get("heading")
        return None

    def flags(self, index):
        if "heading" in self.rows[index.row()]:
            return Qt.NoItemFlags
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable


class Delegate(QtWidgets.QStyledItemDelegate):
    def __init__(self, palette):
        super().__init__(palette)
        self.p = palette

    def sizeHint(self, option, index):
        row = index.data(Qt.UserRole)
        return QtCore.QSize(10, self.p.m["head"] if "heading" in row else self.p.m["row"])

    def _font(self, px, medium=False):
        f = QtGui.QFont(self.p.font())
        f.setPixelSize(px)
        f.setWeight(QtGui.QFont.Medium if medium else QtGui.QFont.Normal)
        return f

    def paint(self, painter, option, index):
        row = index.data(Qt.UserRole)
        t = self.p.tokens
        m = self.p.m
        painter.save()
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        r = option.rect.adjusted(6, 1, -6, -1)
        if "heading" in row:
            painter.setFont(self._font(m["small"], True))
            painter.setPen(QtGui.QColor(t["muted"]))
            painter.drawText(r.adjusted(8, 6, -8, 0), Qt.AlignVCenter | Qt.AlignLeft, row["heading"])
            painter.restore()
            return

        enabled = self.p.is_enabled(row)
        if option.state & QtWidgets.QStyle.State_Selected:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QtGui.QColor(t["accent"]))
            painter.drawRoundedRect(r, 6, 6)

        fg = QtGui.QColor(t["fg"] if enabled else t["muted"])
        muted = QtGui.QColor(t["muted"])
        if not enabled:
            fg.setAlpha(150)
            muted.setAlpha(120)

        icon_rect = QtCore.QRect(r.left() + 10, r.center().y() - m["icon"] // 2 + 1, m["icon"], m["icon"])
        action = row.get("action")
        icon = row.get("icon") or (action.icon() if action is not None else None)
        if icon is not None and not icon.isNull():
            mode = QtGui.QIcon.Normal if enabled else QtGui.QIcon.Disabled
            icon.paint(painter, icon_rect, Qt.AlignCenter, mode)
        else:
            painter.setPen(Qt.NoPen)
            painter.setBrush(muted)
            painter.drawEllipse(QtCore.QRectF(icon_rect.center().x() - 1.5, icon_rect.center().y() - 1.5, 4, 4))

        right = r.right() - 10
        painter.setFont(self._font(m["small"]))
        fm = painter.fontMetrics()
        painter.setPen(muted)
        notes = (row.get("shortcut"), row.get("hint")) if enabled else ("Unavailable right now",)
        for text in notes:
            if text:
                w = fm.horizontalAdvance(text)
                painter.drawText(QtCore.QRect(right - w, r.top(), w, r.height()), Qt.AlignVCenter, text)
                right -= w + 14

        painter.setFont(self._font(m["text"]))
        painter.setPen(fg)
        left = r.left() + 20 + m["icon"]
        text_rect = QtCore.QRect(left, r.top(), right - left, r.height())
        label = painter.fontMetrics().elidedText(row["label"], Qt.ElideRight, text_rect.width())
        painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, label)
        painter.restore()


class SearchGlyph(QtWidgets.QWidget):
    def __init__(self, palette):
        super().__init__(palette)
        self.p = palette
        self.setFixedSize(16, 16)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        color = QtGui.QColor(self.p.tokens["muted"])
        painter.setPen(QtGui.QPen(color, 1.5, Qt.SolidLine, Qt.RoundCap))
        painter.drawEllipse(QtCore.QRectF(2, 2, 9, 9))
        painter.drawLine(QtCore.QPointF(10, 10), QtCore.QPointF(14, 14))


class Palette(QtWidgets.QWidget):
    """Full-window dim layer holding the centred command panel."""

    def __init__(self, mw):
        super().__init__(mw)
        self.mw = mw
        self.tokens = DARK
        self.m = metrics()
        self.items = {}
        self.prev_focus = None
        self.usage = Usage()
        self.opened_at = 0.0
        self.hide()

        self.panel = QtWidgets.QFrame(self)
        self.panel.setObjectName("cmdkPanel")
        layout = QtWidgets.QVBoxLayout(self.panel)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)

        head = QtWidgets.QHBoxLayout()
        head.setContentsMargins(15, 0, 15, 0)
        head.setSpacing(10)
        head.addWidget(SearchGlyph(self))
        self.input = QtWidgets.QLineEdit()
        self.input.setObjectName("cmdkInput")
        self.input.setPlaceholderText("Type a command or search…")
        self.input.setFrame(False)
        self.input.setAttribute(Qt.WA_MacShowFocusRect, False)
        head.addWidget(self.input)
        layout.addLayout(head)

        self.rule = QtWidgets.QFrame()
        self.rule.setObjectName("cmdkRule")
        self.rule.setFixedHeight(1)
        layout.addWidget(self.rule)

        self.model = Model(self)
        self.view = QtWidgets.QListView()
        self.view.setObjectName("cmdkList")
        self.view.setModel(self.model)
        self.view.setItemDelegate(Delegate(self))
        self.view.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.view.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        self.view.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.view.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.view.setFocusPolicy(Qt.NoFocus)
        self.view.setMouseTracking(True)
        self.view.entered.connect(self._hover)
        self.view.clicked.connect(lambda _i: self.run_current())
        layout.addWidget(self.view)

        self.empty = QtWidgets.QLabel("No results found.")
        self.empty.setObjectName("cmdkEmpty")
        self.empty.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.empty)

        # FreeCAD re-raises its overlay panels and updates command states on
        # its own timers, so stay on top and repaint while open.
        self.keep_fresh = QtCore.QTimer(self)
        self.keep_fresh.setInterval(250)
        self.keep_fresh.timeout.connect(self._freshen)

        self.input.textChanged.connect(self.refresh)
        self.input.installEventFilter(self)
        mw.installEventFilter(self)

    # -- lifecycle ---------------------------------------------------------

    def toggle(self):
        self.close_palette() if self.isVisible() else self.open_palette()

    def open_palette(self):
        self.prev_focus = QtWidgets.QApplication.focusWidget()
        self.tokens = theme_tokens(self.mw)
        self.m = metrics()
        self._apply_style()
        self.items = collect_items()
        self.items.update(self._own_items())
        self.opened_at = time.monotonic()
        self.input.blockSignals(True)
        self.input.clear()
        self.input.blockSignals(False)
        self.refresh()
        self.setGeometry(self.mw.rect())
        self.show()
        self.raise_()
        self.input.setFocus(Qt.ShortcutFocusReason)
        self.keep_fresh.start()

    def _freshen(self):
        self.raise_()
        self.view.viewport().update()

    def close_palette(self):
        if self.isVisible() and not self.model.rows:
            self.usage.record_miss(self.input.text())
        self.keep_fresh.stop()
        self.hide()
        if self.prev_focus is not None:
            try:
                self.prev_focus.setFocus()
            except RuntimeError:
                pass
        self.prev_focus = None

    def _apply_style(self):
        t = dict(self.tokens, **self.m)
        self.input.setFixedHeight(t["input"])
        self.empty.setFixedHeight(t["row"] * 2)
        self.panel.setStyleSheet(
            """
            #cmdkPanel { background: %(bg)s; border: 1px solid %(border)s; border-radius: 12px; }
            #cmdkRule { background: %(border)s; border: none; }
            #cmdkInput { background: transparent; border: none; border-radius: 0; padding: 0; margin: 0;
                         min-height: %(input)dpx; max-height: %(input)dpx; font-weight: normal;
                         color: %(fg)s; font-size: %(text)dpx; selection-background-color: %(muted)s;
                         selection-color: %(bg)s; }
            #cmdkEmpty { background: transparent; border: none; color: %(muted)s; font-size: %(text)dpx; }
            #cmdkList { background: transparent; border: none; outline: 0; padding: 4px 0; margin: 0; }
            #cmdkList::item { border: none; background: transparent; }
            """
            % t
        )
        pal = self.input.palette()
        pal.setColor(QtGui.QPalette.PlaceholderText, QtGui.QColor(t["muted"]))
        self.input.setPalette(pal)

    # -- rows --------------------------------------------------------------

    def is_enabled(self, row):
        action = row.get("action")
        try:
            return True if action is None else action.isEnabled()
        except RuntimeError:
            return False

    def _row(self, item, hint=False):
        row = dict(item)
        if item.get("action") is not None:
            row["shortcut"] = _shortcut_text(item["action"])
        if hint:
            row["hint"] = item["cat"]
        return row

    def _own_items(self):
        def own(key, label, tip, call):
            return dict(id="own:" + key, label=label, cat="Command Palette", name="CmdK_" + key,
                        tip=tip, action=None, run=("call", call))

        return {
            "own:export": own("ExportUsage", "Export Usage Stats",
                              "Save the local command usage statistics to a JSON file", self.export_usage),
            "own:settings": own("Settings", "Command Palette Settings",
                                "Change the text size, width and height of the command palette",
                                self.open_settings),
            "own:clear": own("ClearUsage", "Clear Usage Stats",
                             "Delete the local command usage statistics", self.clear_usage),
        }

    def export_usage(self):
        start = os.path.join(os.path.expanduser("~"), "cmdk-usage.json")
        path, _filter = QtWidgets.QFileDialog.getSaveFileName(self.mw, "Export usage stats", start, "JSON (*.json)")
        if path:
            self.usage.export(path)

    def clear_usage(self):
        box = QtWidgets.QMessageBox
        answer = box.question(self.mw, "Clear usage stats",
                              "Delete the command usage statistics stored on this computer?")
        if answer == box.Yes:
            self.usage.clear()

    def open_settings(self):
        params = FreeCAD.ParamGet(PARAMS)
        dialog = QtWidgets.QDialog(self.mw)
        dialog.setWindowTitle("Command Palette Settings")
        form = QtWidgets.QFormLayout(dialog)

        def spin(low, high, step, value, suffix="", auto=False):
            box = QtWidgets.QSpinBox()
            box.setRange(low, high)
            box.setSingleStep(step)
            box.setSuffix(suffix)
            if auto:
                box.setSpecialValueText("Match FreeCAD")
            box.setValue(value)
            return box

        text = spin(9, 32, 1, max(params.GetInt("TextSize", 0), 9), " px", auto=True)
        width = spin(360, 1600, 20, params.GetInt("Width", DEFAULT_WIDTH), " px")
        rows = spin(4, 30, 1, params.GetInt("Rows", DEFAULT_ROWS))
        record = QtWidgets.QCheckBox("Learn from the commands I run")
        record.setChecked(Usage.enabled())
        note = QtWidgets.QLabel(
            "Counts which commands you run and what you typed to find them, so your usual "
            "picks rank higher and appear under Most used. Stored in a file on this computer "
            "and never sent anywhere. If unticked, nothing new is counted; what was already "
            "counted stays until you run Clear Usage Stats."
        )
        note.setWordWrap(True)
        note.setEnabled(False)
        note.setMinimumWidth(380)
        form.addRow("Text size", text)
        form.addRow("Width", width)
        form.addRow("Rows shown", rows)
        form.addRow(record)
        form.addRow(note)
        box = QtWidgets.QDialogButtonBox
        buttons = box(box.Ok | box.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if (getattr(dialog, "exec_", None) or dialog.exec)():
            params.SetInt("TextSize", 0 if text.value() == 9 else text.value())
            params.SetInt("Width", width.value())
            params.SetInt("Rows", rows.value())
            params.SetBool("RecordUsage", record.isChecked())

    def _recent(self):
        raw = FreeCAD.ParamGet(PARAMS).GetString("Recent", "")
        return [k for k in raw.split("|") if k]

    def _remember(self, key):
        recent = [key] + [k for k in self._recent() if k != key]
        FreeCAD.ParamGet(PARAMS).SetString("Recent", "|".join(recent[:MAX_RECENT]))

    def build_rows(self, query):
        items = self.items
        if query.strip():
            scored = []
            recent = self._recent()
            picked = self.usage.picked(query)
            for it in items.values():
                s = score(query, it)
                if s is None:
                    continue
                if it["id"] in recent:
                    s += 12
                if it["id"] in picked:
                    s += 40
                s += min(self.usage.count(it["id"]), 20) * 1.5
                if it.get("action") is not None and not self.is_enabled(it):
                    s -= 30
                scored.append((-s, it["label"].lower(), it))
            scored.sort(key=lambda x: x[:2])
            return [self._row(it, hint=True) for _s, _l, it in scored[:MAX_RESULTS]]

        rows, used = [], set()

        def group(title, members, hint=False):
            members = [m for m in members if m["id"] not in used]
            if not members:
                return
            rows.append({"heading": title})
            for m in members:
                used.add(m["id"])
                rows.append(self._row(m, hint=hint))

        recent = [items[k] for k in self._recent() if k in items]
        if recent:
            rows.append({"heading": "Recent"})
            rows.extend(self._row(m, hint=True) for m in recent)

        shown = set(m["id"] for m in recent)
        most = [items[k] for k in self.usage.most_used() if k in items and k not in shown][:MAX_MOST_USED]
        if most:
            rows.append({"heading": "Most used"})
            rows.extend(self._row(m, hint=True) for m in most)

        for title, names in toolbar_groups(self.mw):
            members = []
            for name in names:
                if "cmd:" + name in items:
                    members.append(items["cmd:" + name])
                else:
                    prefix = "grp:%s:" % name
                    members.extend(it for k, it in items.items() if k.startswith(prefix))
            group(title, members)

        rest = sorted(
            (it for it in items.values() if it["cat"] != "Workbench"), key=lambda it: it["label"].lower()
        )
        group("More commands", rest, hint=True)
        group("Workbenches", sorted((it for it in items.values() if it["cat"] == "Workbench"),
                                    key=lambda it: it["label"].lower()))
        return rows

    def refresh(self, *_):
        rows = self.build_rows(self.input.text())
        self.model.set_rows(rows)
        has = bool(rows)
        self.view.setVisible(has)
        self.empty.setVisible(not has)
        if has:
            height = sum(self.m["head"] if "heading" in r else self.m["row"] for r in rows) + 8
            self.view.setFixedHeight(min(height, self.m["list"]))
            self.view.scrollToTop()
            self._select(self._next_item(-1, 1))
        self._place()

    # -- geometry / painting -------------------------------------------------

    def _place(self):
        width = min(self.m["width"], self.width() - 32)
        self.panel.setFixedWidth(max(width, 240))
        self.panel.adjustSize()
        x = (self.width() - self.panel.width()) // 2
        y = max(24, min(int(self.height() * 0.16), self.height() - self.m["input"] - self.m["list"] - 40))
        self.panel.move(x, y)
        self.update()

    def resizeEvent(self, event):
        self._place()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), QtGui.QColor(0, 0, 0, self.tokens["dim"]))
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        base = QtCore.QRectF(self.panel.geometry()).translated(0, 10)
        for i in range(1, 25):
            painter.setBrush(QtGui.QColor(0, 0, 0, 5))
            painter.drawRoundedRect(base.adjusted(-i, -i, i, i), 12 + i, 12 + i)

    def mousePressEvent(self, event):
        if not self.panel.geometry().contains(event.pos()):
            self.close_palette()

    # -- navigation ----------------------------------------------------------

    def _next_item(self, start, step):
        rows = self.model.rows
        i = start + step
        while 0 <= i < len(rows):
            if "heading" not in rows[i]:
                return i
            i += step
        return None

    def _select(self, row):
        if row is None:
            return
        index = self.model.index(row, 0)
        self.view.setCurrentIndex(index)
        # Keep a heading visible when its first item is selected.
        if row > 0 and "heading" in self.model.rows[row - 1]:
            self.view.scrollTo(self.model.index(row - 1, 0))
        self.view.scrollTo(index)

    def _hover(self, index):
        if "heading" not in self.model.rows[index.row()]:
            self.view.setCurrentIndex(index)

    def _move(self, step):
        current = self.view.currentIndex().row() if self.view.currentIndex().isValid() else -1
        target = current
        for _ in range(abs(step)):
            nxt = self._next_item(target, 1 if step > 0 else -1)
            if nxt is None:
                break
            target = nxt
        if target != current:
            self._select(target)

    def _is_shortcut(self, event):
        if _shortcut is None or _shortcut.key().isEmpty():
            return False
        try:
            pressed = QtGui.QKeySequence(event.keyCombination())
        except AttributeError:  # Qt5
            pressed = QtGui.QKeySequence(int(event.modifiers()) | event.key())
        return pressed == _shortcut.key()

    def eventFilter(self, obj, event):
        if obj is self.mw:
            if event.type() == QtCore.QEvent.Resize and self.isVisible():
                self.setGeometry(self.mw.rect())
            return False
        # The search box would otherwise swallow the shortcut as an editing key
        # (Ctrl+K deletes to the end of the line on Linux and Windows).
        if event.type() in (QtCore.QEvent.ShortcutOverride, QtCore.QEvent.KeyPress) and self._is_shortcut(event):
            if self.isVisible():
                self.close_palette()
            event.accept()
            return True
        if event.type() == QtCore.QEvent.KeyPress:
            key = event.key()
            if key == Qt.Key_Escape:
                self.close_palette()
            elif key in (Qt.Key_Return, Qt.Key_Enter):
                self.run_current()
            elif key == Qt.Key_Down:
                self._move(1)
            elif key == Qt.Key_Up:
                self._move(-1)
            elif key == Qt.Key_PageDown:
                self._move(8)
            elif key == Qt.Key_PageUp:
                self._move(-8)
            else:
                return False
            return True
        return False

    # -- execution -----------------------------------------------------------

    def run_current(self):
        index = self.view.currentIndex()
        if not index.isValid():
            return
        row = self.model.rows[index.row()]
        if "heading" in row or not self.is_enabled(row):
            return
        if not row["id"].startswith("own:"):
            self._remember(row["id"])
        self.usage.record_run(row, self.input.text(), time.monotonic() - self.opened_at)
        self.close_palette()
        QtCore.QTimer.singleShot(0, lambda: self._run(row))

    def _run(self, row):
        run = row["run"]
        try:
            if run[0] == "action":
                run[1].trigger()
            elif run[0] == "group":
                Gui.runCommand(run[1], run[2])
            elif run[0] == "workbench":
                Gui.activateWorkbench(run[1])
            elif run[0] == "call":
                run[1]()
        except Exception as exc:
            FreeCAD.Console.PrintError("CmdK: could not run %s: %s\n" % (row["label"], exc))


class OpenCommand:
    """Registers the palette as a FreeCAD command so its shortcut can be
    changed in Tools > Customize > Keyboard like any other."""

    def GetResources(self):
        return {"MenuText": "Command Palette", "ToolTip": "Search for a command and run it",
                "Accel": DEFAULT_SHORTCUT}

    def Activated(self):
        if _palette is not None:
            _palette.toggle()

    def IsActive(self):
        return True


class ShortcutWatcher:
    def onChange(self, group, name):
        if name == COMMAND:
            apply_shortcut()


def apply_shortcut():
    keys = FreeCAD.ParamGet(SHORTCUTS).GetString(COMMAND, DEFAULT_SHORTCUT)
    if _shortcut is not None:
        _shortcut.setKey(QtGui.QKeySequence(keys))


def install():
    global _palette, _shortcut, _watch
    mw = Gui.getMainWindow()
    if mw is None or _palette is not None:
        return
    _palette = Palette(mw)
    Gui.addCommand(COMMAND, OpenCommand())
    # QShortcut lives in QtGui on Qt6 and QtWidgets on Qt5.
    shortcut_class = getattr(QtGui, "QShortcut", None) or QtWidgets.QShortcut
    _shortcut = shortcut_class(mw)
    apply_shortcut()
    group = FreeCAD.ParamGet(SHORTCUTS)
    _watch = (group, ShortcutWatcher())
    group.Attach(_watch[1])
    _shortcut.setContext(Qt.ApplicationShortcut)
    _shortcut.activated.connect(_palette.toggle)
    _shortcut.activatedAmbiguously.connect(_palette.toggle)
