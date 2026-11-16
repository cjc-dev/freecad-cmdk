"""Cmd+K command palette for FreeCAD."""

import os
import re

import FreeCAD
import FreeCADGui as Gui
from PySide import QtCore, QtGui, QtWidgets

Qt = QtCore.Qt

PARAMS = "User parameter:BaseApp/Preferences/Mod/CmdK"
MAX_RECENT = 6
MAX_RESULTS = 60
ROW_H = 34
HEAD_H = 30
LIST_MAX_H = 338
PANEL_W = 640

# Fallback when the active theme's colours cannot be read.
DARK = dict(bg="#09090b", border="#27272a", fg="#fafafa", muted="#a1a1aa", accent="#27272a", dim=120)

CATEGORY_NAMES = {"Std": "FreeCAD", "TechDraw": "TechDraw", "OpenSCAD": "OpenSCAD"}

_palette = None
_shortcut = None


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
        if name == "Std_Workbench":
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
        return QtCore.QSize(10, HEAD_H if "heading" in row else ROW_H)

    def _font(self, px, medium=False):
        f = QtGui.QFont(self.p.font())
        f.setPixelSize(px)
        f.setWeight(QtGui.QFont.Medium if medium else QtGui.QFont.Normal)
        return f

    def paint(self, painter, option, index):
        row = index.data(Qt.UserRole)
        t = self.p.tokens
        painter.save()
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        r = option.rect.adjusted(6, 1, -6, -1)
        if "heading" in row:
            painter.setFont(self._font(12, True))
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

        icon_rect = QtCore.QRect(r.left() + 10, r.center().y() - 7, 16, 16)
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
        painter.setFont(self._font(12))
        fm = painter.fontMetrics()
        painter.setPen(muted)
        notes = (row.get("shortcut"), row.get("hint")) if enabled else ("Unavailable right now",)
        for text in notes:
            if text:
                w = fm.horizontalAdvance(text)
                painter.drawText(QtCore.QRect(right - w, r.top(), w, r.height()), Qt.AlignVCenter, text)
                right -= w + 14

        painter.setFont(self._font(14))
        painter.setPen(fg)
        text_rect = QtCore.QRect(r.left() + 36, r.top(), right - r.left() - 36, r.height())
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
        self.items = {}
        self.prev_focus = None
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
        self.input.setFixedHeight(46)
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
        self.empty.setFixedHeight(68)
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
        self._apply_style()
        self.items = collect_items()
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
        siblings = [c for c in self.mw.children() if isinstance(c, QtWidgets.QWidget) and not c.isWindow()]
        if siblings and siblings[-1] is not self:
            self.raise_()
        self.view.viewport().update()

    def close_palette(self):
        self.keep_fresh.stop()
        self.hide()
        if self.prev_focus is not None:
            try:
                self.prev_focus.setFocus()
            except RuntimeError:
                pass
        self.prev_focus = None

    def _apply_style(self):
        t = self.tokens
        self.panel.setStyleSheet(
            """
            #cmdkPanel { background: %(bg)s; border: 1px solid %(border)s; border-radius: 12px; }
            #cmdkRule { background: %(border)s; border: none; }
            #cmdkInput { background: transparent; border: none; border-radius: 0; padding: 0; margin: 0;
                         min-height: 46px; max-height: 46px; font-weight: normal;
                         color: %(fg)s; font-size: 14px; selection-background-color: %(muted)s;
                         selection-color: %(bg)s; }
            #cmdkEmpty { background: transparent; border: none; color: %(muted)s; font-size: 14px; }
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
            for it in items.values():
                s = score(query, it)
                if s is None:
                    continue
                if it["id"] in recent:
                    s += 12
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
            height = sum(HEAD_H if "heading" in r else ROW_H for r in rows) + 8
            self.view.setFixedHeight(min(height, LIST_MAX_H))
            self.view.scrollToTop()
            self._select(self._next_item(-1, 1))
        self._place()

    # -- geometry / painting -------------------------------------------------

    def _place(self):
        width = min(PANEL_W, self.width() - 32)
        self.panel.setFixedWidth(max(width, 240))
        self.panel.adjustSize()
        x = (self.width() - self.panel.width()) // 2
        y = max(24, min(int(self.height() * 0.16), self.height() - 46 - LIST_MAX_H - 40))
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

    def eventFilter(self, obj, event):
        if obj is self.mw:
            if event.type() == QtCore.QEvent.Resize and self.isVisible():
                self.setGeometry(self.mw.rect())
            return False
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
        self._remember(row["id"])
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
        except Exception as exc:
            FreeCAD.Console.PrintError("CmdK: could not run %s: %s\n" % (row["label"], exc))


def install():
    global _palette, _shortcut
    mw = Gui.getMainWindow()
    if mw is None or _palette is not None:
        return
    _palette = Palette(mw)
    _shortcut = QtGui.QShortcut(QtGui.QKeySequence("Ctrl+K"), mw)
    _shortcut.setContext(Qt.ApplicationShortcut)
    _shortcut.activated.connect(_palette.toggle)
    _shortcut.activatedAmbiguously.connect(_palette.toggle)
