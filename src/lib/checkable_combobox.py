"""
CheckableComboBox - a QComboBox whose dropdown items carry checkboxes, so the
user can tick multiple entries without a separate list widget. Row 0 is a
"Select All" pseudo-item that ticks/unticks every real item at once and stays
in sync with them. The box itself is a read-only line edit showing a summary
("N apps selected") instead of a single chosen value.

Dropdown rows follow the plain-label-left / checkmark-right pattern (light
grey background on the checked row, a bold "✓" at the far right, no boxed
checkbox glyph) via TickMarkDelegate -- painted manually because the native
per-style checkbox indicator ignores QSS margin/padding tweaks on several
Windows styles, which is what caused the tick and label to visually overlap
in an earlier version of this widget.

ArrowComboBox paints its own double-chevron (⌃ over ⌄) indicator instead of
Qt's default single filled triangle, since QSS's ::down-arrow only supports
one glyph/border-triangle at a time and can't express that shape.
"""
from PySide6.QtCore import Qt, Signal, QPointF
from PySide6.QtGui import QStandardItemModel, QStandardItem, QColor, QPainter, QPen
from PySide6.QtWidgets import QComboBox, QStyledItemDelegate, QStyle

_SELECT_ALL_LABEL = "Select All"
_NAME_ROLE = Qt.UserRole
_CHECKED_ROLE = Qt.UserRole + 1

_STYLE = """
    QComboBox {
        padding: 0px 28px 0px 10px;
        border: 1px solid #ced4da;
        border-radius: 6px;
        font-size: 13px;
        background-color: #ffffff;
        color: #111827;
    }
    QComboBox:focus, QComboBox:hover {
        border: 1px solid #4338ca;
    }
    QComboBox::drop-down {
        width: 24px;
        border: none;
    }
    QComboBox::down-arrow {
        image: none;
        width: 0px;
        height: 0px;
    }
    QComboBox QLineEdit {
        color: #111827;
        border: none;
        background: transparent;
        padding: 0px;
        margin: 0px;
    }
    QComboBox QAbstractItemView {
        background-color: #ffffff;
        border: 1px solid #e5e7eb;
        outline: none;
    }
"""

_ARROW_COLOR = QColor("#6b7280")


class ArrowComboBox(QComboBox):
    """QComboBox with a hand-painted double-chevron dropdown indicator
    (matches the "expand/select" icon pattern -- a caret pointing up stacked
    directly over one pointing down) instead of Qt's single filled triangle.
    """

    def __init__(self, parent=None, height=34):
        super().__init__(parent)
        self.setFixedHeight(height)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        pen = QPen(_ARROW_COLOR)
        pen.setWidthF(1.4)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)

        half_w = 4.0
        chevron_h = 3.0
        cx = self.width() - 16
        mid_y = self.height() / 2
        gap = 1.5

        top_y = mid_y - gap
        painter.drawPolyline([
            QPointF(cx - half_w, top_y),
            QPointF(cx, top_y - chevron_h),
            QPointF(cx + half_w, top_y),
        ])

        bottom_y = mid_y + gap
        painter.drawPolyline([
            QPointF(cx - half_w, bottom_y),
            QPointF(cx, bottom_y + chevron_h),
            QPointF(cx + half_w, bottom_y),
        ])
        painter.end()


class TickMarkDelegate(QStyledItemDelegate):
    """Paints dropdown rows the way native OS pickers do (see the reference
    screenshot): plain label on the left, a checkmark on the right for
    whichever row(s) are checked, light grey background on a checked row.
    *is_checked_fn(QModelIndex) -> bool* decides which rows show the mark --
    kept generic so both a multi-check combo and a plain single-select combo
    (checked == "is this the current index") can share one delegate.
    """

    def __init__(self, is_checked_fn, parent=None):
        super().__init__(parent)
        self._is_checked_fn = is_checked_fn

    def paint(self, painter, option, index):
        painter.save()

        checked = self._is_checked_fn(index)
        hovered = bool(option.state & QStyle.State_MouseOver)
        bg = QColor("#f3f4f6") if checked else (QColor("#f9fafb") if hovered else QColor("#ffffff"))
        painter.fillRect(option.rect, bg)

        font = index.data(Qt.FontRole)
        if font:
            painter.setFont(font)

        check_width = 28
        text_rect = option.rect.adjusted(12, 0, -check_width, 0)
        painter.setPen(QColor("#111827"))
        painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, index.data(Qt.DisplayRole) or "")

        if checked:
            check_rect = option.rect.adjusted(option.rect.width() - check_width, 0, -8, 0)
            bold_font = painter.font()
            bold_font.setBold(True)
            painter.setFont(bold_font)
            painter.drawText(check_rect, Qt.AlignVCenter | Qt.AlignRight, "✓")

        painter.restore()

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        size.setHeight(max(size.height(), 32))
        return size


class CheckableComboBox(ArrowComboBox):
    selection_changed = Signal(list)  # emits the current list of checked item texts

    def __init__(self, items=None, placeholder="Select app(s)...", parent=None):
        super().__init__(parent, height=28)
        self._placeholder = placeholder
        self._keep_popup_open = False
        self.setStyleSheet(_STYLE)

        # Editable + read-only line edit is the standard way to show custom
        # summary text on a QComboBox instead of the selected item's text.
        self.setEditable(True)
        self.lineEdit().setReadOnly(True)
        self.lineEdit().setAlignment(Qt.AlignVCenter | Qt.AlignLeft)

        self.setModel(QStandardItemModel(self))
        self.setItemDelegate(TickMarkDelegate(self._row_is_checked, self))
        self.view().setMouseTracking(True)
        self.view().pressed.connect(self._on_item_pressed)

        self.model().appendRow(self._make_item(_SELECT_ALL_LABEL, bold=True))

        if items:
            self.add_items(items)
        else:
            self._refresh_display_text()

    def _row_is_checked(self, index):
        item = self.model().itemFromIndex(index)
        return bool(item and item.data(_CHECKED_ROLE))

    def _make_item(self, name, checked=False, bold=False):
        item = QStandardItem(name)
        item.setData(name, _NAME_ROLE)
        item.setData(checked, _CHECKED_ROLE)
        item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        if bold:
            font = item.font()
            font.setBold(True)
            item.setFont(font)
        return item

    def add_items(self, items):
        for text in items:
            self.model().appendRow(self._make_item(text))
        self._refresh_display_text()

    def _real_items(self):
        """Every row except the row-0 'Select All' pseudo-item."""
        return [self.model().item(row) for row in range(1, self.model().rowCount())]

    def _on_item_pressed(self, index):
        item = self.model().itemFromIndex(index)
        new_checked = not item.data(_CHECKED_ROLE)

        if index.row() == 0:
            # Select All: force every real item to match its new state.
            item.setData(new_checked, _CHECKED_ROLE)
            for real_item in self._real_items():
                real_item.setData(new_checked, _CHECKED_ROLE)
        else:
            item.setData(new_checked, _CHECKED_ROLE)
            # Keep "Select All" in sync: checked only when everything else is.
            all_checked = all(i.data(_CHECKED_ROLE) for i in self._real_items())
            self.model().item(0).setData(all_checked, _CHECKED_ROLE)

        self.view().viewport().update()
        self._refresh_display_text()
        self.selection_changed.emit(self.checked_items())
        # QComboBox otherwise closes the popup on every click -- checking
        # several items would mean reopening the dropdown each time.
        self._keep_popup_open = True

    def hidePopup(self):
        if self._keep_popup_open:
            self._keep_popup_open = False
            return
        super().hidePopup()

    def checked_items(self):
        return [item.data(_NAME_ROLE) for item in self._real_items() if item.data(_CHECKED_ROLE)]

    def set_checked(self, texts):
        """Pre-check the given item texts (e.g. restoring a saved selection)."""
        wanted = set(texts)
        real_items = self._real_items()
        for item in real_items:
            item.setData(item.data(_NAME_ROLE) in wanted, _CHECKED_ROLE)
        all_checked = bool(real_items) and all(i.data(_CHECKED_ROLE) for i in real_items)
        self.model().item(0).setData(all_checked, _CHECKED_ROLE)
        self.view().viewport().update()
        self._refresh_display_text()

    def _refresh_display_text(self):
        checked = self.checked_items()
        line_edit = self.lineEdit()
        if not checked:
            line_edit.setText(self._placeholder)
        elif len(checked) == len(self._real_items()):
            line_edit.setText(f"All {len(checked)} apps selected")
        elif len(checked) == 1:
            line_edit.setText(checked[0])
        else:
            line_edit.setText(f"{len(checked)} apps selected")
