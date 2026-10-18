"""Native offline bank navigation, reading workspace, and scan lifecycle."""
import os
from pathlib import Path

from PySide6.QtCore import QModelIndex, QSignalBlocker, QThread, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QAction, QActionGroup, QFontMetrics
from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QFileDialog, QFrame,
                               QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox,
                               QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSizePolicy,
                               QSplitter, QStackedWidget, QToolButton, QVBoxLayout, QWidget)
from core.exporter import export_filename, write_answers
from core.parser import KIND_LABELS
from core.scanner import scan_bank
from ui.icons import icon
from ui.navigation import (ASSIGNMENT_ROLE, Assignment, QuestionDelegate, QuestionFilter,
                           QuestionTree, QuestionTreeModel)
from ui.renderer import LocalTextBrowser
from ui.theme import ThemeManager, palette_for, style_sheet


def default_bank_folder() -> Path:
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
            if not self.isInterruptionRequested():
                self.failed.emit(f"{type(error).__name__}: {error}")


def label(text="", name="", parent=None):
    widget = QLabel(text, parent)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    if name:
        widget.setObjectName(name)
    return widget


class MainWindow(QMainWindow):
    def __init__(self, settings=None):
        super().__init__()
        self._worker = self._pending_folder = self._result = None
        self._closing = self._scanning = False
        self._scan_error = ""
        self._current_paper: Assignment | None = None
        self.navigation_level = "papers"
        self.global_query = self.paper_query = ""
        self._paper_list_scroll = 0
        self._navigating = False
        self._manual_sidebar_collapsed = self._auto_sidebar_collapsed = False
        self._sidebar_revealed = False
        self._updating_sidebar = False
        self._sidebar_width = 312
        self.overview_totals = (0, 0, 0)
        self.theme_manager = ThemeManager(self, settings=settings)
        self.setWindowTitle("ETS Viewer")
        metrics = QFontMetrics(self.font())
        self._sidebar_min = max(240, metrics.horizontalAdvance("0") * 26 + 32)
        self._reading_min = max(360, metrics.horizontalAdvance("0") * 39 + 64)
        self._responsive_hysteresis = metrics.height() * 4
        self._rail_width = max(48, metrics.height() * 2 + 12)
        self.setMinimumSize(max(520, self._rail_width + self._reading_min + 28), 480)

        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        header = QFrame()
        header.setObjectName("header")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(14, 10, 20, 10)
        header_layout.setSpacing(12)
        self.sidebar_toggle = self._tool("sidebarToggle", "Collapse sidebar", self._toggle_sidebar)
        header_layout.addWidget(self.sidebar_toggle)
        header_layout.addWidget(label("ETS Viewer", "appTitle"))
        header_layout.addStretch()
        self.settings_button = QToolButton()
        self.settings_button.setObjectName("settingsButton")
        self.settings_button.setText("Settings")
        self.settings_button.setAccessibleName("Settings")
        self.settings_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.settings_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.settings_button)
        menu.addAction("Appearance").setEnabled(False)
        group = QActionGroup(menu)
        group.setExclusive(True)
        self.appearance_actions = {}
        for mode in ("System", "Light", "Dark"):
            action = QAction(mode, menu)
            action.setCheckable(True)
            action.setChecked(mode == self.theme_manager.mode)
            group.addAction(action)
            menu.addAction(action)
            action.triggered.connect(lambda checked, mode=mode: self.theme_manager.set_mode(mode))
            self.appearance_actions[mode] = action
        self.settings_button.setMenu(menu)
        header_layout.addWidget(self.settings_button)
        central_layout.addWidget(header)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setObjectName("contentSplitter")
        self.splitter.setHandleWidth(6)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setOpaqueResize(True)
        self.sidebar = QStackedWidget()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setMinimumWidth(self._sidebar_min)
        self.sidebar.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self._build_sidebar()
        self.splitter.addWidget(self.sidebar)
        right = QFrame()
        right.setObjectName("readingWorkspace")
        right.setMinimumWidth(0)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)
        self._build_reading_header(right_layout)
        self.workspace = QStackedWidget()
        self.workspace.setMinimumSize(0, 0)
        self._build_overview()
        self.browser = LocalTextBrowser()
        self.browser.setAccessibleName("Question and stored reference answers")
        self.browser.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.browser.matches_changed.connect(self._match_changed)
        self.browser.active_part_changed.connect(self._active_part_changed)
        self.workspace.addWidget(self.browser)
        self._build_empty_state()
        right_layout.addWidget(self.workspace, 1)
        self.splitter.addWidget(right)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([self._sidebar_width, 860])
        self.splitter.splitterMoved.connect(self._splitter_moved)
        central_layout.addWidget(self.splitter, 1)
        self.setCentralWidget(central)
        self._build_status()
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(100)
        self._search_timer.timeout.connect(self._apply_filter)
        self.search.textChanged.connect(lambda: self._search_timer.start())
        # Resizing never reselects the question. Text reflows natively and the
        # browser updates image formats while preserving its document state.
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(60)
        self._resize_timer.timeout.connect(self._layout_finished)
        self.toast = label("", "toast", self)
        self.toast.hide()
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.setInterval(3500)
        self._toast_timer.timeout.connect(self.toast.hide)
        self.theme_manager.theme_changed.connect(self._apply_theme)
        self._apply_theme(self.theme_manager.colors)
        self._message("Welcome to ETS Viewer", "Select a local ETS question bank to start browsing questions and answers.", "Select Question Bank")
        self.resize(1220, 820)

    def _tool(self, name, tooltip, callback):
        button = QToolButton()
        button.setObjectName(name)
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        button.setFixedSize(32, 32)
        button.clicked.connect(callback)
        return button

    def _build_sidebar(self):
        expanded = QWidget()
        layout = QVBoxLayout(expanded)
        layout.setContentsMargins(12, 16, 12, 14)
        layout.setSpacing(12)
        self.back_button = QPushButton("All Papers")
        self.back_button.setObjectName("backButton")
        self.back_button.clicked.connect(self._back_to_papers)
        self.back_button.hide()
        layout.addWidget(self.back_button, alignment=Qt.AlignmentFlag.AlignLeft)
        titles = QHBoxLayout()
        self.navigation_title = label("All Papers", "panelTitle")
        self.navigation_title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.count_label = label("0", "countBadge")
        titles.addWidget(self.navigation_title, 1)
        titles.addWidget(self.count_label)
        layout.addLayout(titles)
        self.search = QLineEdit()
        self.search.setObjectName("search")
        self.search.setPlaceholderText("Search all papers…")
        self.search.setAccessibleName("Search all papers")
        self.search.setClearButtonEnabled(True)
        layout.addWidget(self.search)
        self.model = QuestionTreeModel(self)
        self.proxy = QuestionFilter(self)
        self.proxy.setSourceModel(self.model)
        self.question_tree = QuestionTree()
        self.question_tree.setAccessibleName("Papers")
        self.question_tree.setModel(self.proxy)
        self.question_tree.selectionModel().currentChanged.connect(self._show_current)
        layout.addWidget(self.question_tree, 1)
        self.sidebar_empty = QWidget()
        empty_layout = QVBoxLayout(self.sidebar_empty)
        empty_layout.setContentsMargins(12, 20, 12, 20)
        self.sidebar_empty_title = label("No matching papers", "panelTitle")
        self.sidebar_empty_description = label("Try another ID or keyword.", "subtitle")
        self.sidebar_empty_description.setWordWrap(True)
        empty_layout.addWidget(self.sidebar_empty_title)
        empty_layout.addWidget(self.sidebar_empty_description)
        self.clear_search_button = QPushButton("Clear Search")
        self.clear_search_button.clicked.connect(self._clear_search)
        empty_layout.addWidget(self.clear_search_button, alignment=Qt.AlignmentFlag.AlignLeft)
        empty_layout.addStretch()
        self.sidebar_empty.hide()
        layout.addWidget(self.sidebar_empty, 1)
        self.folder_label = label("Local question bank", "folderPath")
        self.folder_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.folder_label)
        self.select_button = QPushButton("Select Question Bank")
        self.select_button.setObjectName("selectFolder")
        self.select_button.clicked.connect(self._choose_folder)
        layout.addWidget(self.select_button)
        self.sidebar.addWidget(expanded)
        rail = QWidget()
        rail.setObjectName("sidebarRail")
        rail_layout = QVBoxLayout(rail)
        rail_layout.setContentsMargins(6, 14, 6, 14)
        self.rail_expand = self._tool("sidebarToggle", "Expand sidebar", self._toggle_sidebar)
        self.rail_all = self._tool("backButton", "All Papers", self._back_to_papers)
        self.rail_change = self._tool("selectFolder", "Change Bank", self._choose_folder)
        rail_layout.addWidget(self.rail_expand, alignment=Qt.AlignmentFlag.AlignHCenter)
        rail_layout.addWidget(self.rail_all, alignment=Qt.AlignmentFlag.AlignHCenter)
        rail_layout.addStretch()
        rail_layout.addWidget(self.rail_change, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.sidebar.addWidget(rail)

    def _build_reading_header(self, layout):
        self.detail_header = QFrame()
        self.detail_header.setObjectName("detailHeader")
        header = QVBoxLayout(self.detail_header)
        header.setContentsMargins(24, 18, 24, 14)
        header.setSpacing(8)
        heading = QHBoxLayout()
        self.detail_title = label("", "workspaceTitle")
        self.detail_title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        heading.addWidget(self.detail_title, 1)
        self.export_button = QPushButton("Export Paper")
        self.export_button.setObjectName("exportAnswers")
        self.export_button.setToolTip("Exports the entire paper, including questions hidden by search, using the first stored answer for each part.")
        self.export_button.clicked.connect(self._export_answers)
        heading.addWidget(self.export_button)
        header.addLayout(heading)
        metadata = QHBoxLayout()
        self.source_label = label("", "sourcePath")
        self.source_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        metadata.addWidget(self.source_label, 1)
        self.paper_date_label = label("", "paperDate")
        metadata.addWidget(self.paper_date_label)
        header.addLayout(metadata)
        layout.addWidget(self.detail_header)
        self.detail_header.hide()
        self.export_button.hide()
        self.match_toolbar = QWidget()
        match_layout = QHBoxLayout(self.match_toolbar)
        match_layout.setContentsMargins(24, 5, 24, 5)
        self.match_label = label("", "matchLabel")
        self.match_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        match_layout.addWidget(self.match_label, 1)
        self.previous_match_button = self._tool("navButton", "Previous match in this question group", lambda: self.browser.navigate_match(-1))
        self.next_match_button = self._tool("navButton", "Next match in this question group", lambda: self.browser.navigate_match(1))
        match_layout.addWidget(self.previous_match_button)
        match_layout.addWidget(self.next_match_button)
        layout.addWidget(self.match_toolbar)
        self.match_toolbar.hide()
        self.part_nav = QScrollArea()
        self.part_nav.setObjectName("partNavigation")
        self.part_nav.setFrameShape(QFrame.Shape.NoFrame)
        self.part_nav.setWidgetResizable(True)
        self.part_nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.part_nav.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.part_nav.setFixedHeight(52)
        self.part_nav.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.part_nav_content = QWidget()
        self.part_nav_content.setObjectName("readingNavigation")
        self.part_nav_layout = QHBoxLayout(self.part_nav_content)
        self.part_nav_layout.setContentsMargins(24, 4, 24, 4)
        self.part_nav_layout.setSpacing(6)
        self.part_nav.setWidget(self.part_nav_content)
        self.part_buttons = []
        layout.addWidget(self.part_nav)
        self.part_nav.hide()

    def _build_overview(self):
        self.overview_page = QWidget()
        outer = QVBoxLayout(self.overview_page)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.addStretch(1)
        content = QWidget()
        content.setMaximumWidth(820)
        content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(16)
        body.addWidget(label("LOCAL LIBRARY", "eyebrow"))
        body.addWidget(label("Your Question Bank", "workspaceTitle"))
        description = label("Your original questions and stored reference answers, in one quiet workspace.", "subtitle")
        description.setWordWrap(True)
        body.addWidget(description)
        stats = QHBoxLayout()
        stats.setSpacing(12)
        self.stat_values = []
        for name in ("Papers", "Question Groups", "Questions"):
            card = QFrame()
            card.setObjectName("statCard")
            column = QVBoxLayout(card)
            column.setContentsMargins(18, 18, 18, 18)
            value = label("0", "statValue")
            title = label(name, "statLabel")
            title.setWordWrap(True)
            column.addWidget(value)
            column.addWidget(title)
            self.stat_values.append(value)
            stats.addWidget(card, 1)
        body.addLayout(stats)
        self.overview_path = label("", "sourcePath")
        self.overview_path.setWordWrap(True)
        body.addWidget(self.overview_path)
        guidance = label("Open a paper from the sidebar to start reading. Search IDs, prompts, passages or stored answers across your bank.\n\nQuestions stay beside their own references. Export Paper saves the entire paper with its first stored answers.", "subtitle")
        guidance.setWordWrap(True)
        body.addWidget(guidance)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(content, 20)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(2)
        self.workspace.addWidget(self.overview_page)

    def _build_empty_state(self):
        self.empty_page = QWidget()
        layout = QVBoxLayout(self.empty_page)
        layout.setContentsMargins(28, 20, 28, 20)
        layout.addStretch()
        self.empty_icon = QLabel()
        layout.addWidget(self.empty_icon, alignment=Qt.AlignmentFlag.AlignCenter)
        self.empty_title = label("", "emptyTitle")
        self.empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_title.setWordWrap(True)
        layout.addWidget(self.empty_title)
        self.empty_description = label("", "emptyDescription")
        self.empty_description.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_description.setWordWrap(True)
        layout.addWidget(self.empty_description)
        self.empty_action = QPushButton()
        self.empty_action.clicked.connect(self._empty_action_clicked)
        layout.addWidget(self.empty_action, alignment=Qt.AlignmentFlag.AlignCenter)
        self.error_details_button = QPushButton("View Error Details")
        self.error_details_button.clicked.connect(self._show_report)
        self.error_details_button.hide()
        layout.addWidget(self.error_details_button, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addStretch(2)
        self.workspace.addWidget(self.empty_page)

    def _build_status(self):
        self.statusBar().setSizeGripEnabled(False)
        self.status_label = label("Select a question bank")
        self.status_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.statusBar().addWidget(self.status_label, 1)
        self.report_button = QPushButton("View details")
        self.report_button.setObjectName("scanNotes")
        self.report_button.clicked.connect(self._show_report)
        self.report_button.hide()
        self.statusBar().addPermanentWidget(self.report_button)
        self.progress = QProgressBar()
        self.progress.setFixedSize(110, 6)
        self.progress.setTextVisible(False)
        self.progress.hide()
        self.statusBar().addPermanentWidget(self.progress)
        self.totals_label = label("")
        self.statusBar().addPermanentWidget(self.totals_label)

    @property
    def sidebar_collapsed(self):
        return self._manual_sidebar_collapsed or (self._auto_sidebar_collapsed and not self._sidebar_revealed)

    def _apply_theme(self, colors):
        QApplication.instance().setPalette(palette_for(colors))
        self.setStyleSheet(style_sheet(colors))
        self.question_tree.set_theme(colors)
        self.browser.set_theme(colors)
        for mode, action in self.appearance_actions.items():
            action.setChecked(mode == self.theme_manager.mode)
        for button, name in ((self.sidebar_toggle, "sidebar"), (self.rail_expand, "sidebar"),
                             (self.rail_all, "folder"), (self.rail_change, "folder"),
                             (self.select_button, "folder"), (self.back_button, "back"),
                             (self.settings_button, "settings"), (self.export_button, "download"),
                             (self.previous_match_button, "up"), (self.next_match_button, "down")):
            button.setIcon(icon(name, colors["text_secondary"]))
        self.empty_icon.setPixmap(icon("folder", colors["text_muted"], 30).pixmap(30, 30))

    def _message(self, title, text="", action="Change Bank", clear_search=False, error=False):
        self.browser.clear_question()
        self.browser.clear()
        self.part_nav.hide()
        self.match_toolbar.hide()
        self.empty_title.setText(title)
        self.empty_description.setText(text)
        self.empty_action.setText(action)
        self.empty_action.setVisible(bool(action))
        self._empty_clears_search = clear_search
        self.error_details_button.setVisible(error)
        self.workspace.setCurrentWidget(self.empty_page)
        if self._current_paper is None:
            self.detail_header.hide()
            self.export_button.hide()

    def _empty_action_clicked(self):
        if self._empty_clears_search:
            self._clear_search()
        else:
            self._choose_folder()

    def _clear_search(self):
        self.search.clear()
        self._apply_filter()

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
        self._navigating = True
        self._result = None
        self._scan_error = ""
        self._scanning = True
        self._current_paper = None
        self.navigation_level = "papers"
        self.global_query = self.paper_query = ""
        self._paper_list_scroll = 0
        self._search_timer.stop()
        with QSignalBlocker(self.search):
            self.search.clear()
        self.proxy.set_query("")
        self.model.set_questions(())
        self.question_tree.setRootIndex(QModelIndex())
        self._set_navigation_header()
        self.count_label.setText("0")
        self.question_tree.show()
        self.sidebar_empty.hide()
        self.folder_label.setText(folder.name)
        self.folder_label.setToolTip(str(folder))
        self.report_button.hide()
        self.totals_label.clear()
        self.browser.root = None
        self.progress.setRange(0, 0)
        self.progress.show()
        self.status_label.setText("Scanning local question bank…")
        self._message("Opening your question bank", "Reading local files. You can change banks while scanning.", "Change Bank")
        self._navigating = False
        worker = BankLoader(folder, self)
        self._worker = worker
        worker.loaded.connect(self._accept_result)
        worker.failed.connect(self._load_failed)
        worker.progress.connect(self._update_progress)
        worker.finished.connect(self._loader_finished)
        worker.start()

    def _stale_signal(self):
        sender = self.sender()
        return (self._closing or self._pending_folder is not None or
                (sender is not None and sender is not self._worker))

    @Slot(int, int)
    def _update_progress(self, current, total):
        if self._stale_signal():
            return
        if total <= 0:
            self.progress.setRange(0, 0)
            self.status_label.setText("Scanning local question bank…")
        else:
            self.progress.setRange(0, total)
            self.progress.setValue(current)
            self.status_label.setText(f"Scanning local question bank… {min(100, int(current * 100 / total))}%")

    @Slot(object)
    def _accept_result(self, result):
        if self._stale_signal() or result.cancelled:
            return
        self._result = result
        self.browser.root = result.root
        self.folder_label.setText(result.root.name)
        self.folder_label.setToolTip(str(result.root))
        self.select_button.setText("Change Bank")
        self._navigating = True
        self.model.set_questions(result.questions, result.folder_modified)
        self._navigating = False
        self.overview_totals = (len(self.model.assignments), len(result.questions), sum(len(q.parts) for q in result.questions))
        for value, total in zip(self.stat_values, self.overview_totals):
            value.setText(str(total))
        self.overview_path.setText(f"{result.root.name}  ·  Stored locally on this computer")
        self.overview_path.setToolTip(str(result.root))
        self.report_button.setVisible(bool(result.issues))
        self.report_button.setToolTip("Show source paths and explanations for scan notes")
        self._apply_filter()
        self._ready_status()

    def _ready_status(self):
        if self._scan_error:
            self.status_label.setText("Scan failed · Folder could not be read")
        elif self._result is not None:
            count = len(self._result.issues)
            self.status_label.setText(f"Loaded with {count} scan notes" if count else "Ready")
            papers, groups, _ = self.overview_totals
            self.totals_label.setText(f"{papers} papers · {groups} groups")
            if not self._result.questions:
                self.status_label.setText(f"No supported papers · {count} scan notes" if count else "No supported papers")

    @Slot(str)
    def _load_failed(self, error):
        if self._stale_signal():
            return
        self._scan_error = error
        self.report_button.show()
        self.report_button.setToolTip("View the actual loading error")
        self._ready_status()
        self._message("Could not load question bank", error, error=True)

    @Slot()
    def _loader_finished(self):
        if self.sender() is not self._worker:
            return
        worker, self._worker = self._worker, None
        worker.deleteLater()
        self._scanning = False
        self.progress.hide()
        if self._closing:
            QTimer.singleShot(0, self.close)
        elif self._pending_folder is not None:
            folder, self._pending_folder = self._pending_folder, None
            self.load_folder(folder)

    def _set_navigation_header(self):
        paper = self._current_paper
        self.navigation_title.setText(paper.folder.name if paper else "All Papers")
        self.navigation_title.setToolTip(paper.date_tooltip if paper else "All papers in this question bank")
        self.back_button.setVisible(paper is not None)
        self.search.setPlaceholderText("Search this paper…" if paper else "Search all papers…")
        self.search.setAccessibleName("Search this paper" if paper else "Search all papers")
        self.question_tree.setAccessibleName("Questions in this paper" if paper else "Papers")

    def _paper_source_index(self):
        if self._current_paper:
            for row, assignment in enumerate(self.model.assignments):
                if assignment.folder == self._current_paper.folder:
                    return self.model.index(row, 0)
        return QModelIndex()

    def _enter_paper(self, assignment):
        if self._result is None:
            return
        self._search_timer.stop()
        self.global_query = self.search.text()
        self._paper_list_scroll = self.question_tree.verticalScrollBar().value()
        self.paper_query = self.global_query
        self._current_paper = assignment
        self.navigation_level = "questions"
        self._set_navigation_header()
        self._apply_filter()

    @Slot()
    def _back_to_papers(self):
        if self._current_paper is None:
            return
        self._search_timer.stop()
        self._navigating = True
        self._current_paper = None
        self.navigation_level = "papers"
        with QSignalBlocker(self.search):
            self.search.setText(self.global_query)
        self.question_tree.setRootIndex(QModelIndex())
        self.question_tree.setCurrentIndex(QModelIndex())
        self.question_tree.clearSelection()
        self._navigating = False
        self._set_navigation_header()
        self._apply_filter()
        self.question_tree.verticalScrollBar().setValue(self._paper_list_scroll)

    @Slot()
    def _apply_filter(self):
        if self._navigating:
            return
        self._search_timer.stop()
        query, previous = self.search.text(), self.browser._question
        self._navigating = True
        self.proxy.set_query(query)
        paper = self._current_paper
        if paper is None:
            self.global_query = query
            self.question_tree.setRootIndex(QModelIndex())
            self.question_tree.setCurrentIndex(QModelIndex())
            self.question_tree.clearSelection()
            count = self.proxy.rowCount()
        else:
            self.paper_query = query
            root = self.proxy.mapFromSource(self._paper_source_index())
            count = self.proxy.rowCount(root) if root.isValid() else 0
            self.question_tree.setRootIndex(root)
            current = QModelIndex()
            for row in range(count):
                index = self.proxy.index(row, 0, root)
                if index.data(Qt.ItemDataRole.UserRole) is previous:
                    current = index
                    break
            if not current.isValid() and count:
                current = self.proxy.index(0, 0, root)
            self.question_tree.setCurrentIndex(current)
            if not count:
                self.question_tree.clearSelection()
        self.count_label.setText(str(count))
        self.question_tree.setVisible(count > 0 or not query)
        self.sidebar_empty.setVisible(count == 0 and bool(query))
        self.sidebar_empty_title.setText("No matching questions" if paper else "No matching papers")
        self._navigating = False
        if self._result is None:
            return
        if paper is None:
            self._show_overview()
        elif count:
            self._show_current()
        else:
            self._paper_header(None)
            self._message("No matching questions", "Try another ID or keyword within this paper.", "Clear Search", clear_search=True)

    def _show_overview(self):
        self.browser.clear_question()
        self.browser.clear()
        self.detail_header.hide()
        self.export_button.hide()
        self.part_nav.hide()
        self.match_toolbar.hide()
        if self._result.questions:
            self.workspace.setCurrentWidget(self.overview_page)
        else:
            self._message("No Exams Found", "No supported ETS exams were found in the selected folder.", "Change Bank")

    def _paper_header(self, question):
        paper = self._current_paper
        self.detail_title.setText(f"Question {question.id}" if question else paper.folder.name)
        self.source_label.setText(f"{paper.folder.name}  ·  {KIND_LABELS[question.kind]}" if question else paper.summary)
        self.source_label.setToolTip(str(question.source) if question else str(paper.folder))
        self.paper_date_label.setText(paper.date_label)
        self.paper_date_label.setToolTip(paper.date_tooltip)
        self.paper_date_label.show()
        self.export_button.show()
        self.detail_header.show()

    @Slot()
    def _show_current(self, *_):
        if self._navigating or self._result is None:
            return
        index = self.question_tree.currentIndex()
        if not index.isValid():
            return
        question = index.data(Qt.ItemDataRole.UserRole)
        if question is None:
            assignment = index.data(ASSIGNMENT_ROLE)
            if assignment is not None:
                self._enter_paper(assignment)
            return
        if self._current_paper is None:
            self._current_paper = index.parent().data(ASSIGNMENT_ROLE)
            self.navigation_level = "questions"
            self._set_navigation_header()
            self.question_tree.setRootIndex(index.parent())
        changed = self.browser._question is not question
        self._paper_header(question)
        self.workspace.setCurrentWidget(self.browser)
        self.browser.show_question(question, self._result.root, image_width=820, query=self.paper_query)
        if changed:
            self._build_part_buttons(question)
        self.part_nav.setVisible(len(question.parts) > 1)
        self._match_changed(self.browser.current_match, self.browser.match_count)

    def _build_part_buttons(self, question):
        while self.part_nav_layout.count():
            item = self.part_nav_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.part_buttons = []
        for index, part in enumerate(question.parts):
            button = QPushButton(f"Q{part.number or index + 1}")
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.setChecked(index == 0)
            button.setToolTip(f"Scroll to question {part.number or index + 1}")
            button.clicked.connect(lambda checked, index=index: self.browser.scroll_to_part(index))
            self.part_nav_layout.addWidget(button)
            self.part_buttons.append(button)
        self.part_nav_layout.addStretch()

    @Slot(int)
    def _active_part_changed(self, index):
        for number, button in enumerate(self.part_buttons):
            button.setChecked(number == index)
        if 0 <= index < len(self.part_buttons):
            self.part_nav.ensureWidgetVisible(self.part_buttons[index], 12, 0)

    @Slot(int, int)
    def _match_changed(self, current, total):
        active = self._current_paper is not None and bool(self.paper_query.strip()) and self.browser._question is not None
        self.match_toolbar.setVisible(active)
        self.match_label.setText(f"Search   {current} / {total}" if total else "Matched source path or paper metadata")
        self.previous_match_button.setEnabled(total > 0)
        self.next_match_button.setEnabled(total > 0)

    def _toggle_sidebar(self):
        if self.sidebar_collapsed:
            self._manual_sidebar_collapsed = False
            # An explicit reveal gives narrow-window users access to search
            # and the question list. Native text reflow uses the remaining
            # width until the next window-width change reevaluates the rail.
            self._sidebar_revealed = self._auto_sidebar_collapsed
        else:
            self._sidebar_width = self.splitter.sizes()[0]
            self._manual_sidebar_collapsed = True
            self._sidebar_revealed = False
        self._sync_sidebar()

    def _splitter_moved(self, *_):
        if not self.sidebar_collapsed and not self._updating_sidebar:
            self._sidebar_width = self.splitter.sizes()[0]
        self._resize_timer.start()

    def _sync_sidebar(self):
        self._updating_sidebar = True
        collapsed = self.sidebar_collapsed
        self.sidebar.setCurrentIndex(1 if collapsed else 0)
        if collapsed:
            self.sidebar.setFixedWidth(self._rail_width)
            self.splitter.setSizes([self._rail_width, max(1, self.splitter.width() - self._rail_width - 6)])
        else:
            self.sidebar.setMaximumWidth(16777215)
            self.sidebar.setMinimumWidth(self._sidebar_min)
            self.splitter.setSizes([self._sidebar_width, max(1, self.splitter.width() - self._sidebar_width - 6)])
        self.sidebar_toggle.setToolTip("Expand sidebar" if collapsed else "Collapse sidebar")
        self.sidebar_toggle.setAccessibleName(self.sidebar_toggle.toolTip())
        self._updating_sidebar = False

    def _responsive_sidebar(self):
        # QMainWindow receives resizeEvent before its central layout has
        # resized the splitter. The content rect is already the new width.
        available = self.contentsRect().width()
        threshold = max(self._sidebar_min, self._sidebar_width) + self._reading_min + self.splitter.handleWidth()
        previous = self._auto_sidebar_collapsed
        if self._auto_sidebar_collapsed:
            if available >= threshold + self._responsive_hysteresis:
                self._auto_sidebar_collapsed = False
        elif available < threshold:
            if not self._manual_sidebar_collapsed:
                self._sidebar_width = max(self._sidebar_width, self.splitter.sizes()[0])
            self._auto_sidebar_collapsed = True
        if (previous != self._auto_sidebar_collapsed or
                self.sidebar.currentIndex() != (1 if self.sidebar_collapsed else 0)):
            self._sync_sidebar()

    def _layout_finished(self):
        self._responsive_sidebar()
        self._position_toast()

    @Slot()
    def _export_answers(self):
        assignment, result = self._current_paper, self._result
        if assignment is None or result is None:
            return
        dialog = QFileDialog(self, "Export paper answers")
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        dialog.setFileMode(QFileDialog.FileMode.AnyFile)
        dialog.setNameFilter("Text files (*.txt)")
        dialog.setDefaultSuffix("txt")
        dialog.setDirectory(str(default_bank_folder()))
        dialog.selectFile(export_filename(assignment.folder.name))
        try:
            if dialog.exec() != QFileDialog.DialogCode.Accepted or not dialog.selectedFiles():
                return
            destination = Path(dialog.selectedFiles()[0])
        finally:
            dialog.deleteLater()
        try:
            write_answers(destination, assignment.folder.name, assignment.questions, bank_root=result.root)
        except (OSError, UnicodeError) as error:
            self._export_message("Export failed", QMessageBox.Icon.Critical,
                                 "Could not export the paper's answers.", f"{destination}\n\n{error}")
        else:
            self.toast.setText("Paper exported successfully")
            self.toast.setToolTip(str(destination))
            self.toast.adjustSize()
            self._position_toast()
            self.toast.show()
            self.toast.raise_()
            self._toast_timer.start()

    def _position_toast(self):
        if hasattr(self, "toast"):
            self.toast.move(max(12, self.width() - self.toast.width() - 24),
                            self.height() - self.statusBar().height() - self.toast.height() - 16)

    def _export_message(self, title, icon_type, text, detail):
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setIcon(icon_type)
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(text)
        box.setInformativeText(detail)
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.setWindowModality(Qt.WindowModality.WindowModal)
        box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        box.open()

    @Slot()
    def _show_report(self):
        if self._result is None and not self._scan_error:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Loading error details" if self._scan_error else "Folder scan report")
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.addWidget(label("Could not load this bank" if self._scan_error else "Scan notes", "panelTitle"))
        if self._scan_error:
            summary = "The scanner could not read the selected folder."
            details = f"{self.folder_label.toolTip()}\n\n{self._scan_error}"
        else:
            summary = f"{len(self._result.questions)} question groups loaded · {len(self._result.issues)} scan notes"
            details = "\n\n".join(f"{issue.path}\n{issue.message}" for issue in self._result.issues)
        caption = label(summary, "subtitle")
        caption.setWordWrap(True)
        layout.addWidget(caption)
        self.report_text = QPlainTextEdit()
        self.report_text.setReadOnly(True)
        self.report_text.setPlainText(details)
        self.report_text.setAccessibleName("Scan source paths and explanations")
        layout.addWidget(self.report_text, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.resize(min(680, max(400, self.width() - 64)), min(480, max(280, self.height() - 80)))
        dialog.open()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_resize_timer"):
            if event.size().width() != event.oldSize().width():
                self._sidebar_revealed = False
            self._responsive_sidebar()
            self._resize_timer.start()

    def closeEvent(self, event):
        self._closing = True
        self._pending_folder = None
        self._search_timer.stop()
        self._resize_timer.stop()
        self._toast_timer.stop()
        if self._worker is not None and self._worker.isRunning():
            self._worker.requestInterruption()
            self.status_label.setText("Finishing local scan…")
            event.ignore()
        else:
            event.accept()
