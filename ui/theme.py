"""Shared colors and local appearance preferences for the native desktop UI."""

from pathlib import Path
import re

from PySide6.QtCore import QObject, QSettings, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QColor, QGuiApplication, QPalette


LIGHT_COLORS = {
    "background": "#FAF9F7",
    "sidebar": "#F3F2F0",
    "surface": "#FFFFFF",
    "surface_hover": "#EAE8E5",
    "surface_selected": "#E3E0DC",
    "text_primary": "#272623",
    "text_secondary": "#65625E",
    "text_muted": "#6F6B65",
    "border": "#DEDCD8",
    "border_hover": "#C6C1BA",
    "border_focus": "#96918A",
    "answer_background": "#F3F6F2",
    "answer_border": "#D5DFD2",
    "answer_accent": "#4E6E4A",
    "warning": "#8B641C",
    "error": "#A63D3D",
    "search_highlight": "#F3DF9F",
    "search_highlight_active": "#EBC46F",
    "selection_text": "#272623",
    "scrollbar": "#C8C4BE",
    "scrollbar_hover": "#AAA59D",
    "disabled_background": "#EFEEEB",
    "disabled_text": "#9A958E",
    "link": "#4F6656",
    "code_background": "#F2F1EE",
}

DARK_COLORS = {
    "background": "#0B0D10",
    "sidebar": "#0E1014",
    "workspace": "#12151A",
    "surface": "#1B1F25",
    "surface_hover": "#22272F",
    "surface_selected": "#2A2F37",
    "text_primary": "#E8EAED",
    "text_secondary": "#B2BAC6",
    "text_muted": "#8F98A6",
    "border": "#343A44",
    "border_hover": "#46505E",
    "border_focus": "#7E8998",
    "answer_background": "#19241E",
    "answer_border": "#344B3B",
    "answer_accent": "#9EAD9F",
    "warning": "#D4B475",
    "error": "#DE9995",
    "search_highlight": "#65552D",
    "search_highlight_active": "#785B20",
    "selection_text": "#E8EAED",
    "scrollbar": "#3B434F",
    "scrollbar_hover": "#535F6E",
    "disabled_background": "#181C22",
    "disabled_text": "#697382",
    "link": "#A7BAAE",
    "code_background": "#161A20",
}

_MODES = ("System", "Light", "Dark")
_SETTING = "appearance/mode"
_TOKEN = re.compile(r"@([a-z_]+)@")


def _windows_color_scheme():
    """Read the application appearance preference without keeping a registry handle."""
    try:
        import winreg
    except ImportError:
        return None
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            light, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        if light in (0, 1):
            return Qt.ColorScheme.Light if light else Qt.ColorScheme.Dark
    except OSError:
        pass
    return None


class ThemeManager(QObject):
    """Resolve System/Light/Dark colors; callers apply them to their UI.

    Qt's color-scheme signal gives immediate updates on native platforms. Windows
    is also polled once a second while following System: Fusion can otherwise
    miss changes to AppsUseLightTheme. Each read closes its registry key.
    """

    theme_changed = Signal(object)

    def __init__(self, parent=None, settings=None):
        super().__init__(parent)
        self._settings = settings if settings is not None else QSettings("ETS-Viewer", "ETS-Viewer", self)
        saved = self._settings.value(_SETTING, "System")
        self._mode = saved if saved in _MODES else "System"
        self._style_hints = QGuiApplication.styleHints() if QGuiApplication.instance() is not None else None
        self._colors = self._resolved_colors()
        self._system_timer = QTimer(self)
        self._system_timer.setInterval(1000)
        self._system_timer.timeout.connect(self._refresh_system)
        if self._style_hints is not None:
            self._style_hints.colorSchemeChanged.connect(self._on_color_scheme_changed)
        if self._mode == "System":
            self._system_timer.start()

    @property
    def mode(self):
        return self._mode

    @property
    def colors(self):
        return dict(self._colors)

    @Slot(str)
    def set_mode(self, mode):
        if mode not in _MODES:
            raise ValueError(f"Unknown appearance mode: {mode!r}")
        self._mode = mode
        self._settings.setValue(_SETTING, mode)
        if mode == "System":
            self._system_timer.start()
        else:
            self._system_timer.stop()
        self._apply_colors(self._resolved_colors())

    def _resolved_colors(self, scheme=None):
        if self._mode == "Dark":
            return dict(DARK_COLORS)
        if self._mode == "Light":
            return dict(LIGHT_COLORS)
        if scheme not in (Qt.ColorScheme.Light, Qt.ColorScheme.Dark):
            scheme = _windows_color_scheme()
        if scheme is None and self._style_hints is not None:
            scheme = self._style_hints.colorScheme()
        return dict(DARK_COLORS if scheme == Qt.ColorScheme.Dark else LIGHT_COLORS)

    def _apply_colors(self, colors):
        if colors != self._colors:
            self._colors = colors
            self.theme_changed.emit(dict(colors))

    @Slot()
    def _refresh_system(self):
        if self._mode == "System":
            self._apply_colors(self._resolved_colors())

    @Slot(Qt.ColorScheme)
    def _on_color_scheme_changed(self, scheme):
        if self._mode == "System":
            self._apply_colors(self._resolved_colors(scheme))


def style_sheet(colors):
    """Resolve the local QSS template using the same colors as custom rendering."""
    template = Path(__file__).with_name("style.qss").read_text(encoding="utf-8")
    tokens = {"workspace": colors["background"], **colors}
    return _TOKEN.sub(lambda match: tokens[match.group(1)], template)


def palette_for(colors):
    """Provide native widgets and dialogs with the corresponding semantic palette."""
    palette = QPalette()
    for role, token in (
        (QPalette.ColorRole.Window, "background"),
        (QPalette.ColorRole.WindowText, "text_primary"),
        (QPalette.ColorRole.Base, "surface"),
        (QPalette.ColorRole.AlternateBase, "sidebar"),
        (QPalette.ColorRole.Text, "text_primary"),
        (QPalette.ColorRole.Button, "surface"),
        (QPalette.ColorRole.ButtonText, "text_primary"),
        (QPalette.ColorRole.BrightText, "text_primary"),
        (QPalette.ColorRole.ToolTipBase, "surface"),
        (QPalette.ColorRole.ToolTipText, "text_primary"),
        (QPalette.ColorRole.Highlight, "surface_selected"),
        (QPalette.ColorRole.HighlightedText, "selection_text"),
        (QPalette.ColorRole.PlaceholderText, "text_muted"),
        (QPalette.ColorRole.Link, "link"),
        (QPalette.ColorRole.LinkVisited, "link"),
        (QPalette.ColorRole.Accent, "border_focus"),
        (QPalette.ColorRole.Light, "surface"),
        (QPalette.ColorRole.Midlight, "surface_hover"),
        (QPalette.ColorRole.Mid, "border"),
        (QPalette.ColorRole.Dark, "border_hover"),
        (QPalette.ColorRole.Shadow, "border_hover"),
    ):
        palette.setColor(role, QColor(colors[token]))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(colors["disabled_text"]))
    for role in (QPalette.ColorRole.Base, QPalette.ColorRole.Button):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(colors["disabled_background"]))
    return palette
