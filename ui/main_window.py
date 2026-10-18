"""One window: select a bank, search its questions, read stored answers."""

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import (QModelIndex, QPointF, QRect, QRectF, QSize,
                            QSortFilterProxyModel, QThread, Qt, QTimer, Signal, Slot)
from PySide6.QtGui import QColor, QCursor, QFont, QPainter, QPen, QPolygonF, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (QFileDialog, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QMainWindow, QMessageBox,
                               QProgressBar, QPushButton, QSizePolicy, QSplitter,
                               QStyledItemDelegate, QStyleOptionViewItem,
                               QTreeView, QVBoxLayout, QWidget)

from core.exporter import export_filename, write_answers
from core.parser import KIND_LABELS, Question
from core.scanner import scan_bank, task_folder
from ui.renderer import LocalTextBrowser


def default_bank_folder() -> Path:
    """Resolve the current user's roaming ETS folder without a fixed username."""
    roaming = os.environ.get("APPDATA")
    return (Path(roaming) if roaming else Path.home() / "AppData" / "Roaming") / "ETS"


class BankLoader(QThread):
    loaded = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int)

    def __init__(self, folder: Path, parent=None):
        super().__init__(parent)
        self.folder = folder

    def run(self):
        try:
            result = scan_bank(self.folder, self.progress.emit, self.isInterruptionRequested)
            if not result.cancelled and not self.isInterruptionRequested():
                self.loaded.emit(result)
        except Exception as error:
            # Deliver failures to the GUI instead of letting a worker crash it.
            if not self.isInterruptionRequested():
                self.failed.emit(f"{type(error).__name__}: {error}")


ASSIGNMENT_ROLE = int(Qt.ItemDataRole.UserRole) + 1


@dataclass(frozen=True)
class Assignment:
    folder: Path
    questions: tuple[Question, ...]
    modified: datetime | None = None

    @property
    def label(self):
        # The inspected cache has real container IDs but no descriptive titles.
        return f"Assignment / exam {self.folder.name}"

    @property
    def summary(self):
        units = sum(len(question.parts) for question in self.questions)
        return f"{len(self.questions)} question groups · {units} questions"

    @property
    def date_label(self):
        return f"Folder modified: {self.modified:%Y-%m-%d}" if self.modified else "Date unavailable"

    @property
    def date_tooltip(self):
        timestamp = (f"Folder modified: {self.modified:%Y-%m-%d %H:%M:%S} (local time)"
                     if self.modified else "Date unavailable")
        return f"{self.folder}\n{timestamp}"


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
            folder = task_folder(question.source)
            # Group by the entire source directory, never by a reused ID.
            groups.setdefault(folder, []).append(question)
        modified = folder_modified or {}
        assignments = (Assignment(folder, tuple(items), modified.get(folder))
                       for folder, items in groups.items())
        self.assignments = tuple(sorted(
            assignments,
            key=lambda assignment: (assignment.modified is not None,
                                    assignment.modified or datetime.min),
            reverse=True,
        ))
        for assignment in self.assignments:
            parent = QStandardItem(f"{assignment.label}\n{assignment.summary}\n{assignment.date_label}")
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
        self.setRecursiveFilteringEnabled(True)
        self.setAutoAcceptChildRows(True)

    def set_query(self, text):
        self.terms = tuple(text.casefold().split())
        # The setter invalidates the filter without deprecated Qt APIs.
        self.setFilterFixedString(text)

    def filterAcceptsRow(self, row, parent):
        index = self.sourceModel().index(row, 0, parent)
        question = index.data(Qt.ItemDataRole.UserRole)
        if question is not None:
            text = question.search_text
        else:
            assignment = index.data(ASSIGNMENT_ROLE)
            text = f"{assignment.label} {assignment.folder}".casefold()
        return all(term in text for term in self.terms)


class QuestionTree(QTreeView):
    """Paint each row once, including its indentation and disclosure arrow."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)

    def drawRow(self, painter, option, index):
        selected = self.selectionModel().isSelected(index)
        hovered = (self.viewport().underMouse()
                   and self.indexAt(self.viewport().mapFromGlobal(QCursor.pos())) == index)
        assignment = index.data(ASSIGNMENT_ROLE) is not None
        if selected:
            background = "#304b73" if hovered else "#293f61"
            border = "#90b3ff" if self.hasFocus() else "#6685b3" if hovered else "#526e9b"
        elif hovered:
            background, border = "#223149", "#3b5272"
        elif assignment:
            background, border = "#1b2637", "#2d3d55"
        else:
            background = None
        if background:
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(QColor(border), 1))
            painter.setBrush(QColor(background))
            bounds = QRectF(3.5, option.rect.top() + 3.5, self.viewport().width() - 7, option.rect.height() - 7)
            painter.drawRoundedRect(bounds, 10, 10)
            painter.restore()
        # Render this single-column tree directly. Native drawRow calculates
        # selection again and paints the indentation as a separate surface.
        content = QStyleOptionViewItem(option)
        content.rect = self.visualRect(index)
        self.drawBranches(painter, QRect(0, option.rect.top(), content.rect.left(), option.rect.height()), index)
        self.itemDelegateForIndex(index).paint(painter, content, index)

    def drawBranches(self, painter, rect, index):
        if not index.model().hasChildren(index):
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        selected = self.selectionModel().isSelected(index)
        painter.setPen(QPen(QColor("#c7d6ff" if selected else "#91a4c4"), 1.5,
                            Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        x, y = rect.right() - self.indentation() / 2 + 1, rect.center().y()
        points = ((x - 3, y - 1.5), (x, y + 1.5), (x + 3, y - 1.5)) if self.isExpanded(index) else (
            (x - 1.5, y - 3), (x + 1.5, y), (x - 1.5, y + 3))
        painter.drawPolyline(QPolygonF([QPointF(*point) for point in points]))
        painter.restore()


class QuestionDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(240, 76 if index.data(ASSIGNMENT_ROLE) is not None else 72)

    def paint(self, painter, option, index):
        painter.save()
        question = index.data(Qt.ItemDataRole.UserRole)
        assignment = index.data(ASSIGNMENT_ROLE)
        rect = option.rect.adjusted(12, 10, -14, -10)
        selected = option.widget.selectionModel().isSelected(index)
        font = QFont(option.font)
        font.setPointSizeF(9)
        font.setBold(False)
        painter.setFont(font)
        if assignment:
            kind = f"{assignment.modified:%Y-%m-%d}" if assignment.modified else "Date unavailable"
        else:
            kind = KIND_LABELS[question.kind]
        # Leave room for fractional font metrics on high-DPI Windows displays.
        kind_width = min(painter.fontMetrics().horizontalAdvance(kind) + 4, max(0, rect.width() - 74))
        kind = painter.fontMetrics().elidedText(kind, Qt.TextElideMode.ElideRight, kind_width)
        painter.setPen(QColor("#b8cbef" if selected else "#8f9fb9"))
        painter.drawText(rect.adjusted(rect.width() - kind_width, 0, 0, -26),
                         Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, kind)
        font.setPointSizeF(10.5)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#edf3ff" if selected else "#e0e8f6"))
        heading = assignment.folder.name if assignment else question.id
        heading = painter.fontMetrics().elidedText(heading, Qt.TextElideMode.ElideRight, rect.width() - kind_width - 12)
        painter.drawText(rect.adjusted(0, 0, -kind_width - 12, -26),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, heading)
        font.setPointSizeF(9.5)
        font.setBold(False)
        painter.setFont(font)
        painter.setPen(QColor("#cedbf1" if selected else "#a4b2c9"))
        title = assignment.summary.replace("question groups", "groups") if assignment else question.title
        title = painter.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight, rect.width())
        painter.drawText(rect.adjusted(0, 26, 0, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title)
        painter.restore()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self._worker = None
        self._pending_folder = None
        self._closing = False
        self._result = None
        self.setWindowTitle("ETS-Viewer")
        self.setMinimumSize(850, 580)
        self.setStyleSheet((Path(__file__).with_name("style.qss")).read_text(encoding="utf-8"))

        central = QWidget()
        central.setObjectName("central")
        layout = QVBoxLayout(central)
        layout.setContentsMargins(22, 22, 22, 10)
        layout.setSpacing(12)
        toolbar = QHBoxLayout()
        toolbar.setSpacing(14)
        self.select_button = QPushButton("Select Folder")
        self.select_button.setObjectName("selectFolder")
        self.select_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.select_button.clicked.connect(self._choose_folder)
        self.search = QLineEdit()
        self.search.setObjectName("search")
        self.search.setAccessibleName("Search assignment, question ID or keywords")
        self.search.setPlaceholderText("Search ID or text…")
        self.search.setClearButtonEnabled(True)
        toolbar.addWidget(self.select_button)
        toolbar.addWidget(self.search, 1)
        layout.addLayout(toolbar)

        path_row = QHBoxLayout()
        path_row.setSpacing(10)
        path_caption = QLabel("LOCAL BANK")
        path_caption.setObjectName("eyebrow")
        path_row.addWidget(path_caption)
        path_caption.hide()
        self.folder_label = QLabel("No folder selected")
        self.folder_label.setObjectName("folderPath")
        self.folder_label.setMinimumHeight(22)
        self.folder_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.folder_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        path_row.addWidget(self.folder_label, 1)
        layout.addLayout(path_row)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter = splitter
        splitter.setObjectName("contentSplitter")
        splitter.setHandleWidth(8)
        splitter.setOpaqueResize(True)
        splitter.setChildrenCollapsible(False)
        left = QFrame()
        left.setObjectName("panel")
        left.setMinimumWidth(300)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(12, 16, 12, 12)
        left_layout.setSpacing(8)
        list_header = QHBoxLayout()
        list_header.setContentsMargins(6, 0, 6, 8)
        title = QLabel("Test papers")
        title.setObjectName("panelTitle")
        subtitle = QLabel("Assignments / exams")
        subtitle.setObjectName("panelSubtitle")
        list_titles = QVBoxLayout()
        list_titles.setSpacing(4)
        list_titles.addWidget(title)
        list_titles.addWidget(subtitle)
        subtitle.hide()
        self.count_label = QLabel("0")
        self.count_label.setObjectName("countBadge")
        self.count_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.count_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        list_header.addLayout(list_titles)
        list_header.addStretch()
        list_header.addWidget(self.count_label)
        left_layout.addLayout(list_header)
        self.question_tree = QuestionTree()
        self.question_tree.setAccessibleName("Assignments and exams")
        self.question_tree.setHeaderHidden(True)
        self.question_tree.setIndentation(18)
        self.question_tree.setItemDelegate(QuestionDelegate(self.question_tree))
        self.question_tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.question_tree.setVerticalScrollMode(QTreeView.ScrollMode.ScrollPerPixel)
        self.model = QuestionTreeModel(self)
        self.proxy = QuestionFilter(self)
        self.proxy.setSourceModel(self.model)
        self.question_tree.setModel(self.proxy)
        self.question_tree.selectionModel().currentChanged.connect(self._show_current)
        left_layout.addWidget(self.question_tree, 1)

        right = QFrame()
        right.setObjectName("panel")
        right.setMinimumWidth(400)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)
        detail_header = QFrame()
        detail_header.setObjectName("detailHeader")
        detail_header_layout = QVBoxLayout(detail_header)
        detail_header_layout.setContentsMargins(22, 16, 22, 14)
        detail_header_layout.setSpacing(5)
        detail_title = QLabel("Answers")
        detail_title.setObjectName("panelTitle")
        detail_title.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        detail_heading = QHBoxLayout()
        detail_heading.setSpacing(12)
        detail_heading.addWidget(detail_title)
        detail_heading.addStretch()
        self.paper_date_label = QLabel()
        self.paper_date_label.setObjectName("paperDate")
        self.paper_date_label.setFixedHeight(28)
        self.paper_date_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.paper_date_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.paper_date_label.hide()
        detail_heading.addWidget(self.paper_date_label)
        detail_header_layout.addLayout(detail_heading)
        self.source_label = QLabel("")
        self.source_label.setObjectName("sourcePath")
        self.source_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.source_label.setTextFormat(Qt.TextFormat.PlainText)
        self.export_button = QPushButton("Export answers")
        self.export_button.setObjectName("exportAnswers")
        self.export_button.setFixedHeight(28)
        self.export_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.export_button.setToolTip("Export this entire paper with the first stored answer for each question")
        self.export_button.clicked.connect(self._export_answers)
        self.export_button.hide()
        detail_heading.addWidget(self.export_button)
        detail_header_layout.addWidget(self.source_label)
        right_layout.addWidget(detail_header)
        self.browser = LocalTextBrowser()
        self.browser.setAccessibleName("Question and stored answers")
        self.browser.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        right_layout.addWidget(self.browser, 1)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([356, 820])
        layout.addWidget(splitter, 1)
        self.setCentralWidget(central)

        self.status_label = QLabel("0 questions loaded")
        self.status_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.statusBar().setSizeGripEnabled(False)
        self.statusBar().addWidget(self.status_label, 1)
        self.report_button = QPushButton()
        self.report_button.setObjectName("scanNotes")
        self.report_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.report_button.clicked.connect(self._show_report)
        self.report_button.hide()
        self.statusBar().addPermanentWidget(self.report_button)
        self.progress = QProgressBar()
        self.progress.setFixedSize(130, 6)
        self.progress.setTextVisible(False)
        self.progress.hide()
        self.statusBar().addPermanentWidget(self.progress)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(100)
        self._search_timer.timeout.connect(self._apply_filter)
        self.search.textChanged.connect(lambda: self._search_timer.start())
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(100)
        self._resize_timer.timeout.connect(self._show_current)
        splitter.splitterMoved.connect(lambda *_: self._resize_timer.start())
        self._message("Select a folder")
        self.resize(1220, 820)

    def _message(self, title, text=""):
        from html import escape
        self.paper_date_label.hide()
        self.export_button.hide()
        self.browser.clear_question()
        self.browser.setHtml(
            '<html><body style="color:#e3e9f3;font-family:Segoe UI;font-size:10pt;">'
            f'<h2 style="color:#c1cff0;font-size:12pt;font-weight:600;">{escape(title)}</h2>'
            f'<p style="color:#97a6bd;">{escape(text)}</p></body></html>'
        )

    @Slot()
    def _choose_folder(self):
        default = self._result.root if self._result else default_bank_folder()
        selected = QFileDialog.getExistingDirectory(self, "Select ETS question bank folder", str(default))
        if selected:
            self.load_folder(Path(selected))

    def load_folder(self, folder: Path):
        if self._closing:
            return
        folder = Path(folder).expanduser()
        if self._worker is not None:
            self._pending_folder = folder
            self._worker.requestInterruption()
            return
        self._result = None
        self.model.set_questions(())
        self.count_label.setText("0")
        self.folder_label.setText(str(folder))
        self.folder_label.setToolTip(str(folder))
        self.source_label.setText("Reading local question files…")
        self.browser.root = None
        self.report_button.hide()
        self.progress.setRange(0, 0)
        self.progress.show()
        self.status_label.setText("Scanning and parsing local files…")
        self._message("Loading…")
        worker = BankLoader(folder, self)
        self._worker = worker
        worker.loaded.connect(self._accept_result)
        worker.failed.connect(self._load_failed)
        worker.progress.connect(self._update_progress)
        worker.finished.connect(self._loader_finished)
        worker.start()

    @Slot(int, int)
    def _update_progress(self, current, total):
        if not self._closing and self._pending_folder is None:
            self.progress.setRange(0, max(total, 1))
            self.progress.setValue(current)

    @Slot(object)
    def _accept_result(self, result):
        if self._closing or self._pending_folder is not None:
            return
        self._result = result
        self.browser.root = result.root
        self.folder_label.setText(str(result.root))
        self.model.set_questions(result.questions, result.folder_modified)
        self.report_button.setText(f"{len(result.issues)} scan notes")
        self.report_button.setVisible(bool(result.issues))
        self.report_button.setToolTip("Unreadable, unsupported, or unlinked files")
        self._apply_filter()

    @Slot(str)
    def _load_failed(self, error):
        if self._closing or self._pending_folder is not None:
            return
        self.status_label.setText("0 questions loaded · Folder could not be read")
        self.source_label.setText("Folder could not be read")
        self._message("Could not load folder", error)

    @Slot()
    def _loader_finished(self):
        worker = self._worker
        self._worker = None
        if worker:
            worker.deleteLater()
        self.progress.hide()
        if self._closing:
            QTimer.singleShot(0, self.close)
        elif self._pending_folder is not None:
            folder, self._pending_folder = self._pending_folder, None
            self.load_folder(folder)
        elif self._result is not None and not self._result.questions:
            self._show_no_exams()

    def _show_no_exams(self):
        self.status_label.setText("0 questions loaded · Scan stopped: no exams found")
        self.source_label.setText("No exams found")
        self.source_label.setToolTip("")
        self._message("No exams found", "Select a folder containing supported ETS question bank files.")
        box = QMessageBox(self)
        box.setWindowTitle("No exams found")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText("No supported ETS exams were found in the selected folder.")
        box.setInformativeText(f"Folder: {self._result.root}\n\nScanning has stopped. Select another question bank folder.")
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.setWindowModality(Qt.WindowModality.WindowModal)
        box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        box.open()

    @Slot()
    def _apply_filter(self):
        self.proxy.set_query(self.search.text())
        visible_assignments = self.proxy.rowCount()
        self.count_label.setText(str(visible_assignments))
        if self._result is None:
            return
        visible = sum(self.proxy.rowCount(self.proxy.index(row, 0)) for row in range(visible_assignments))
        total = len(self._result.questions)
        assignments = len(self.model.assignments)
        units = sum(len(q.parts) for q in self._result.questions)
        if visible == total:
            self.status_label.setText(f"{assignments} papers · {total} groups · {units} questions")
        else:
            self.status_label.setText(f"{visible_assignments}/{assignments} papers · {visible}/{total} groups · {units} questions")
        if visible and self.proxy.terms:
            self.question_tree.expandAll()
            current = self.question_tree.currentIndex()
            if not current.isValid() or current.data(Qt.ItemDataRole.UserRole) is None:
                self.question_tree.setCurrentIndex(self.proxy.index(0, 0, self.proxy.index(0, 0)))
            self.question_tree.scrollTo(self.question_tree.currentIndex())
            self._show_current()
        elif visible:
            self.question_tree.collapseAll()
            self.question_tree.setCurrentIndex(QModelIndex())
            self.question_tree.clearSelection()
            self.source_label.setText("")
            self.source_label.setToolTip("")
            self._message("Select a paper")
        else:
            self.source_label.setText("")
            if total:
                self._message("No matching questions")
            else:
                self._message("No supported questions found", "Select a folder containing ETS question files. Scan notes describe any unsupported or unreadable data.")

    @Slot()
    def _show_current(self, *_):
        index = self.question_tree.currentIndex()
        if not index.isValid() or self._result is None:
            return
        question = index.data(Qt.ItemDataRole.UserRole)
        assignment = (index if question is None else index.parent()).data(ASSIGNMENT_ROLE)
        if question is None:
            try:
                source = assignment.folder.relative_to(self._result.root).as_posix()
            except ValueError:
                source = assignment.folder.name
            if source == ".":
                source = assignment.folder.name
            self.source_label.setText(source)
            self.source_label.setToolTip(str(assignment.folder))
            self._message(assignment.label, assignment.summary)
        else:
            source = question.source.relative_to(self._result.root).as_posix()
            self.source_label.setText(source)
            self.source_label.setToolTip(str(question.source))
            width = max(200, self.browser.viewport().width() - 60)
            self.browser.show_question(question, self._result.root, image_width=width)
        self.paper_date_label.setText(assignment.date_label)
        self.paper_date_label.setToolTip(assignment.date_tooltip)
        self.paper_date_label.show()
        self.export_button.show()

    @Slot()
    def _export_answers(self):
        index = self.question_tree.currentIndex()
        result = self._result
        if not index.isValid() or result is None:
            return
        question = index.data(Qt.ItemDataRole.UserRole)
        assignment = (index if question is None else index.parent()).data(ASSIGNMENT_ROLE)
        if assignment is None:
            return

        dialog = QFileDialog(self, "Export paper answers")
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        dialog.setFileMode(QFileDialog.FileMode.AnyFile)
        dialog.setNameFilter("Text files (*.txt)")
        dialog.setDefaultSuffix("txt")
        dialog.setDirectory(str(default_bank_folder()))
        dialog.selectFile(export_filename(assignment.folder.name))
        try:
            if dialog.exec() != QFileDialog.DialogCode.Accepted:
                return
            selected = dialog.selectedFiles()
            if not selected:
                return
            destination = Path(selected[0])
        finally:
            dialog.deleteLater()

        try:
            # Use the original assignment, never the filtered tree rows or
            # the browser's current reference-answer expansion state.
            write_answers(destination, assignment.folder.name, assignment.questions, bank_root=result.root)
        except (OSError, UnicodeError) as error:
            self._export_message("Export failed", QMessageBox.Icon.Critical,
                                 "Could not export the paper's answers.", f"{destination}\n\n{error}")
        else:
            self._export_message("Export complete", QMessageBox.Icon.Information,
                                 "The paper's answers were exported successfully.",
                                 f"Task: {assignment.folder.name}\n{destination}")

    def _export_message(self, title, icon, text, detail):
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setIcon(icon)
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(text)
        box.setInformativeText(detail)
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.setWindowModality(Qt.WindowModality.WindowModal)
        box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        box.open()

    @Slot()
    def _show_report(self):
        if self._result is None:
            return
        box = QMessageBox(self)
        box.setWindowTitle("Folder scan report")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(f"Loaded {len(self._result.questions)} question groups. {len(self._result.issues)} scan notes.")
        box.setInformativeText("Only answers explicitly linked to supported local questions are displayed. Expand details for file-specific limitations.")
        lines = []
        for issue in self._result.issues:
            try:
                path = issue.path.relative_to(self._result.root)
            except ValueError:
                path = issue.path
            lines.append(f"{path}\n{issue.message}\n")
        box.setDetailedText("\n".join(lines))
        box.exec()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_resize_timer"):
            self._resize_timer.start()

    def closeEvent(self, event):
        self._closing = True
        self._pending_folder = None
        self._search_timer.stop()
        self._resize_timer.stop()
        if self._worker is not None and self._worker.isRunning():
            self._worker.requestInterruption()
            self.status_label.setText("Finishing local scan…")
            event.ignore()
        else:
            event.accept()
