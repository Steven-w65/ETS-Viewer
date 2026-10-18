"""Loaded-bank navigation models and theme-aware lightweight list rendering."""
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from PySide6.QtCore import QRectF, QSize, QSortFilterProxyModel, Qt
from PySide6.QtGui import QColor, QCursor, QFont, QPainter, QPen, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QStyledItemDelegate, QStyleOptionViewItem, QTreeView
from core.parser import KIND_LABELS, Question
from core.scanner import task_folder
from ui.icons import icon

ASSIGNMENT_ROLE = int(Qt.ItemDataRole.UserRole) + 1


@dataclass(frozen=True)
class Assignment:
    folder: Path
    questions: tuple[Question, ...]
    modified: datetime | None = None

    @property
    def label(self):
        return f"Assignment / exam {self.folder.name}"

    @property
    def summary(self):
        return f"{len(self.questions)} question groups · {sum(len(q.parts) for q in self.questions)} questions"

    @property
    def date_label(self):
        return f"{self.modified:%Y-%m-%d}" if self.modified else "Date unavailable"

    @property
    def date_tooltip(self):
        date = (f"Folder modified: {self.modified:%Y-%m-%d %H:%M:%S} (local time)"
                if self.modified else "Date unavailable")
        return f"{self.folder}\n{date}"


class QuestionTreeModel(QStandardItemModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.questions = ()
        self.assignments = ()

    def set_questions(self, questions, folder_modified=None):
        self.clear()
        self.setColumnCount(1)
        self.questions = tuple(questions)
        groups = {}
        for question in self.questions:
            groups.setdefault(task_folder(question.source), []).append(question)
        dates = folder_modified or {}
        self.assignments = tuple(sorted(
            (Assignment(folder, tuple(items), dates.get(folder)) for folder, items in groups.items()),
            key=lambda paper: (paper.modified is not None, paper.modified or datetime.min), reverse=True))
        for assignment in self.assignments:
            parent = QStandardItem(f"{assignment.label}\n{assignment.summary}\nFolder modified: {assignment.date_label}")
            parent.setEditable(False)
            parent.setData(assignment, ASSIGNMENT_ROLE)
            parent.setToolTip(assignment.date_tooltip)
            for question in assignment.questions:
                item = QStandardItem(f"{question.id} · {KIND_LABELS[question.kind]}\n{question.title}")
                item.setEditable(False)
                item.setData(question, Qt.ItemDataRole.UserRole)
                item.setToolTip(f"{question.title}\n{question.source}")
                parent.appendRow(item)
            self.appendRow(parent)


class QuestionFilter(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.terms = ()

    def set_query(self, text):
        self.terms = tuple(text.casefold().split())
        self.setFilterFixedString(text)

    def _matches(self, question, assignment):
        text = f"{assignment.label} {assignment.folder}".casefold() + " " + question.search_text
        return all(term in text for term in self.terms)

    def filterAcceptsRow(self, row, parent):
        index = self.sourceModel().index(row, 0, parent)
        question = index.data(Qt.ItemDataRole.UserRole)
        if question is not None:
            return self._matches(question, parent.data(ASSIGNMENT_ROLE))
        assignment = index.data(ASSIGNMENT_ROLE)
        return assignment is not None and any(self._matches(q, assignment) for q in assignment.questions)


class QuestionTree(QTreeView):
    """Present only one level of the loaded hierarchical source model."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.colors = {}
        self.setMouseTracking(True)
        self.setRootIsDecorated(False)
        self.setItemsExpandable(False)
        self.setIndentation(0)
        self.setHeaderHidden(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QTreeView.ScrollMode.ScrollPerPixel)
        self.setItemDelegate(QuestionDelegate(self))

    def set_theme(self, colors):
        self.colors = colors
        self.viewport().update()

    def drawRow(self, painter, option, index):
        selected = self.selectionModel().isSelected(index)
        hovered = (self.viewport().underMouse() and
                   self.indexAt(self.viewport().mapFromGlobal(QCursor.pos())) == index)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if selected or hovered:
            background = self.colors.get("surface_selected" if selected else "surface_hover", self.palette().highlight().color().name())
            bounds = QRectF(4, option.rect.top() + 3, self.viewport().width() - 8, option.rect.height() - 6)
            painter.setBrush(QColor(background))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(bounds, 8, 8)
            if selected and self.hasFocus():
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor(self.colors.get("border_focus", background)), 1))
                painter.drawRoundedRect(bounds.adjusted(.5, .5, -.5, -.5), 8, 8)
        painter.restore()
        content = QStyleOptionViewItem(option)
        content.rect = self.visualRect(index)
        self.itemDelegateForIndex(index).paint(painter, content, index)

    def drawBranches(self, painter, rect, index):
        pass


class QuestionDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(220, option.fontMetrics.height() * (2 if index.data(ASSIGNMENT_ROLE) else 3) + 28)

    def paint(self, painter, option, index):
        painter.save()
        colors = option.widget.colors
        primary = QColor(colors.get("text_primary", option.palette.text().color().name()))
        secondary = QColor(colors.get("text_secondary", primary.name()))
        muted = QColor(colors.get("text_muted", secondary.name()))
        assignment = index.data(ASSIGNMENT_ROLE)
        question = index.data(Qt.ItemDataRole.UserRole)
        rect = option.rect.adjusted(14, 12, -14, -10)
        font = QFont(option.font)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(primary)
        line_height = painter.fontMetrics().height()
        if assignment:
            icon("folder", secondary.name(), 17).paint(painter, int(rect.x()), int(rect.y()), 17, 17)
            heading = painter.fontMetrics().elidedText(assignment.folder.name, Qt.TextElideMode.ElideRight, max(0, rect.width() - 40))
            painter.drawText(rect.adjusted(27, 0, -12, 0), Qt.AlignmentFlag.AlignTop, heading)
            font.setBold(False)
            font.setPointSizeF(max(8, font.pointSizeF() - .5))
            painter.setFont(font)
            painter.setPen(muted)
            count, total = index.model().rowCount(index), len(assignment.questions)
            detail = f"{count} / {total} groups" if count != total else f"{total} groups"
            detail += f"  ·  {assignment.date_label}"
            detail = painter.fontMetrics().elidedText(detail, Qt.TextElideMode.ElideRight, max(0, rect.width() - 27))
            painter.drawText(rect.adjusted(27, line_height + 6, 0, 0), Qt.AlignmentFlag.AlignTop, detail)
        else:
            kind_font = QFont(font)
            kind_font.setBold(False)
            kind_font.setPointSizeF(max(8, font.pointSizeF() - 1))
            painter.setFont(kind_font)
            kind = KIND_LABELS[question.kind]
            width = min(painter.fontMetrics().horizontalAdvance(kind), rect.width() // 2)
            kind = painter.fontMetrics().elidedText(kind, Qt.TextElideMode.ElideRight, width)
            painter.setPen(muted)
            painter.drawText(rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop, kind)
            painter.setFont(font)
            painter.setPen(primary)
            heading = painter.fontMetrics().elidedText(question.id, Qt.TextElideMode.ElideRight, max(0, rect.width() - width - 12))
            painter.drawText(rect, Qt.AlignmentFlag.AlignTop, heading)
            font.setBold(False)
            painter.setFont(font)
            painter.setPen(secondary)
            text = question.title
            metrics = painter.fontMetrics()
            # Fit at most two lines, without measuring every possible prefix.
            lo, hi = 0, len(text)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if metrics.horizontalAdvance(text[:mid]) <= rect.width():
                    lo = mid
                else:
                    hi = mid - 1
            first = text[:lo]
            if lo < len(text) and " " in first:
                first = first.rsplit(" ", 1)[0]
            second = metrics.elidedText(text[len(first):].lstrip(), Qt.TextElideMode.ElideRight, rect.width())
            painter.drawText(rect.adjusted(0, line_height + 5, 0, 0), Qt.AlignmentFlag.AlignTop, first)
            painter.drawText(rect.adjusted(0, line_height * 2 + 5, 0, 0), Qt.AlignmentFlag.AlignTop, second)
        painter.restore()
