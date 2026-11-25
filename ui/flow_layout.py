"""Horizontally centered wrapping flow layout for category keycap buttons."""

from __future__ import annotations

from typing import List, Tuple

from PyQt6.QtCore import QPoint, QRect, QSize, Qt
from PyQt6.QtWidgets import QLayout, QLayoutItem, QSizePolicy, QWidget


class CenteredFlowLayout(QLayout):
    """QLayout that wraps items across rows and horizontally centers each row."""

    def __init__(
        self,
        parent: QWidget | None = None,
        margin: int = 0,
        h_spacing: int = 8,
        v_spacing: int = 8,
        centered: bool = True,
    ) -> None:
        super().__init__(parent)
        self._items: List[QLayoutItem] = []
        self._h_space = h_spacing
        self._v_space = v_spacing
        self._centered = centered
        self.setContentsMargins(margin, margin, margin, margin)

    def __del__(self) -> None:
        while self._items:
            item = self.takeAt(0)
            if item is not None:
                del item

    def addItem(self, item: QLayoutItem) -> None:
        self._items.append(item)

    def horizontalSpacing(self) -> int:
        return self._h_space

    def verticalSpacing(self) -> int:
        return self._v_space

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> QLayoutItem | None:
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientation:
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(
            margins.left() + margins.right(),
            margins.top() + margins.bottom(),
        )
        return size

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        left, top, right, bottom = self.getContentsMargins()
        effective = rect.adjusted(left, top, -right, -bottom)
        max_width = max(1, effective.width())
        h_space = self.horizontalSpacing()
        v_space = self.verticalSpacing()

        rows: List[Tuple[List[Tuple[QLayoutItem, QSize]], int, int]] = []
        current_row: List[Tuple[QLayoutItem, QSize]] = []
        row_width = 0
        row_height = 0

        for item in self._items:
            widget = item.widget()
            if widget is not None and widget.isHidden():
                continue
            item_size = item.sizeHint()
            needed = item_size.width() if not current_row else h_space + item_size.width()
            if current_row and row_width + needed > max_width:
                rows.append((current_row, row_width, row_height))
                current_row = [(item, item_size)]
                row_width = item_size.width()
                row_height = item_size.height()
            else:
                current_row.append((item, item_size))
                row_width += needed
                row_height = max(row_height, item_size.height())

        if current_row:
            rows.append((current_row, row_width, row_height))

        y = effective.y()
        for row_idx, (row_items, r_width, r_height) in enumerate(rows):
            if self._centered:
                x = effective.x() + max(0, (effective.width() - r_width) // 2)
            else:
                x = effective.x()
            if not test_only:
                for item, item_size in row_items:
                    y_offset = (r_height - item_size.height()) // 2
                    item.setGeometry(QRect(QPoint(x, y + y_offset), item_size))
                    x += item_size.width() + h_space
            y += r_height
            if row_idx < len(rows) - 1:
                y += v_space

        return (y - rect.y()) + bottom
