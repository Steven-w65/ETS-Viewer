"""Render source text and local prompt images without a web engine."""

from html import escape
from html.parser import HTMLParser
from pathlib import Path
import re
from urllib.parse import urlsplit

from PySide6.QtCore import QPoint, QPointF, QSize, Qt, QUrl, Signal, Slot
from PySide6.QtGui import (QColor, QFont, QImage, QImageReader, QPainter, QPalette, QPen,
                          QTextBlockFormat, QTextCharFormat, QTextCursor, QTextDocument,
                          QTextOption, QTextTable)
from PySide6.QtWidgets import QPushButton, QTextBrowser, QTextEdit

from core.parser import Question
from ui.icons import icon


_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
_CARD_RADIUS = 10
_CARD_PADDING = 16
_BODY_POINT_SIZE = 10.5
_BODY_LINE_SPACING = 150
_LABEL_COLUMN_WIDTH = 32
_READING_WIDTH = 820
_ANSWER_CONTROL_HEIGHT = 24
# Semantic token names, shared by native painting and generated rich text.
_CARD_STYLES = {
    "question": ("surface", "text_secondary", "border"),
    "context": ("surface", "text_secondary", "border"),
    "answer": ("answer_background", "answer_accent", "answer_border"),
    "additional": ("surface", "text_secondary", "border"),
    "missing": ("surface", "text_secondary", "border"),
    "explanation": ("surface", "text_secondary", "border"),
}


def local_image(reference: str, source: Path, root: Path) -> Path | None:
    """Resolve an original image reference strictly within the chosen bank."""
    try:
        url = urlsplit(reference)
        if reference.startswith(("\\\\", "//")) or (url.scheme == "file" and url.netloc):
            return None
        if url.scheme == "file":
            decoded = QUrl(reference).toLocalFile()
            # file:////host/... and encoded backslashes can have no URL host
            # yet decode to a UNC/device path. Check before touching the disk.
            if decoded.replace("\\", "/").startswith("//"):
                return None
            path = Path(decoded)
        elif url.scheme and not Path(reference).is_absolute():
            return None
        else:
            path = source.parent / reference
            if not path.exists() and not Path(reference).is_absolute():
                # In the inspected bank, JSON references omit material/.
                # Resolve only within this question; common/ has an unrelated
                # content.jpg and must never be used as an image fallback.
                path = source.parent / "material" / reference
        path = path.resolve()
        if (not path.is_relative_to(root.resolve()) or not path.is_file()
                or path.suffix.lower() not in _IMAGE_EXTENSIONS
                or path.stat().st_size > 16 * 1024 * 1024):
            return None
        return path
    except (OSError, ValueError):
        return None


def _image_html(reference: str, source: Path, root: Path, width: int) -> str:
    path = local_image(reference, source, root)
    if path:
        reader = QImageReader(str(path))
        size = reader.size()
        # A valid header can still have truncated/corrupt pixel data. Decode
        # only after the existing limits pass, before emitting an image tag.
        if (size.isValid() and size.width() * size.height() <= 40_000_000
                and not reader.read().isNull()):
            display_width = min(max(width, 1), size.width())
            height = max(1, round(size.height() * display_width / size.width()))
            return (f'<p><img src="{escape(path.as_uri(), quote=True)}" '
                    f'width="{display_width}" height="{height}"></p>')
    return f'<p class="muted">Image unavailable: {escape(reference)}</p>'


class _SafeMarkup(HTMLParser):
    tags = {"p", "div", "br", "b", "strong", "i", "em", "u", "s", "sub", "sup",
            "ul", "ol", "li", "table", "thead", "tbody", "tr", "td", "th", "blockquote"}
    blocked = {"script", "style", "iframe", "object", "head"}

    def __init__(self, source: Path, root: Path, width: int):
        super().__init__(convert_charrefs=True)
        self.source, self.root, self.width = source, root, width
        self.output = []
        self.suppressed = 0
        self._images = {}
        self._has_tables = False

    def handle_starttag(self, tag, attrs):
        if tag in self.blocked:
            self.suppressed += 1
            return
        if self.suppressed:
            return
        if tag == "img":
            reference = dict(attrs).get("src")
            if reference:
                html = _image_html(reference, self.source, self.root, self.width)
                if html.startswith('<p><img '):
                    self._images[len(self.output)] = reference
                self.output.append(html)
        elif tag in self.tags:
            safe_attrs = ""
            self._has_tables |= tag == "table"
            if tag in {"td", "th"}:
                for key, value in attrs:
                    if key in {"rowspan", "colspan"} and value and value.isdigit() and 1 <= int(value) <= 100:
                        safe_attrs += f' {key}="{value}"'
            self.output.append(f"<{tag}{safe_attrs}>")

    def handle_endtag(self, tag):
        if tag in self.blocked:
            self.suppressed = max(0, self.suppressed - 1)
        elif not self.suppressed and tag in self.tags:
            # The bank uses </br> extensively in its reference responses.
            self.output.append("<br>" if tag == "br" else f"</{tag}>")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in {"br", "img"}:
            self.handle_endtag(tag)

    def handle_data(self, data):
        if not self.suppressed:
            self.output.append(escape(data).replace("\n", "<br>"))

    def html(self):
        if not self._images or not self._has_tables:
            return "".join(self.output)
        # Ask Qt to interpret the already-sanitized table structure, including
        # implicit cells and row/column spans. Tiny internal placeholders keep
        # image minimum widths out of this structural pass, and carry no file
        # paths or remote resource references.
        probe = QTextDocument()
        probe.setHtml("".join(
            f'<p><img src="ets-image-sizing:{index}" width="1" height="1"></p>'
            if index in self._images else html
            for index, html in enumerate(self.output)))
        block = probe.begin()
        while block.isValid():
            iterator = block.begin()
            while not iterator.atEnd():
                fragment = iterator.fragment()
                fmt = fragment.charFormat()
                if fmt.isImageFormat():
                    index = int(fmt.toImageFormat().name().partition(":")[2])
                    cursor = QTextCursor(probe)
                    cursor.setPosition(fragment.position())
                    table = cursor.currentTable()
                    ancestors = []
                    while isinstance(table, QTextTable):
                        ancestors.append(table)
                        table = table.parentFrame()
                    width = self.width
                    for table in reversed(ancestors):
                        cell = table.cellAt(fragment.position())
                        table_format = table.format()
                        # Allocate one share per original column. A spanned
                        # cell receives all of its shares; nested tables divide
                        # that containing-cell budget again. Reserve Qt's native
                        # border, cell spacing and padding before sizing images.
                        content = (width - 2 * table_format.border()
                                   - (table.columns() + 1) * table_format.cellSpacing())
                        width = max(1, content * cell.columnSpan() / table.columns()
                                    - 2 * table_format.cellPadding())
                    self.output[index] = _image_html(self._images[index], self.source, self.root,
                                                     max(1, int(width)))
                iterator += 1
            block = block.next()
        return "".join(self.output)


def safe_fragment(value: str, source: Path, root: Path, image_width=640) -> str:
    renderer = _SafeMarkup(source, root, image_width)
    renderer.feed(value)
    renderer.close()
    return renderer.html()


_CSS = """
body {{ color: {text_primary}; font-family: 'Segoe UI', 'Microsoft YaHei UI', sans-serif; font-size: 10.5pt; }}
h1 {{ color: {text_primary}; font-size: 13pt; font-weight: 600; margin-top: 0; margin-bottom: 16px; }}
h2 {{ color: {text_primary}; font-size: 11pt; font-weight: 600; margin-top: 20px; margin-bottom: 10px; }}
h3 {{ color: {text_secondary}; font-size: 9.5pt; font-weight: 600; margin-top: 12px; margin-bottom: 8px; }}
p {{ margin-top: 0; margin-bottom: 6px; }}
.muted {{ color: {text_muted}; font-size: 9.5pt; }}
.section-label {{ font-size: 9.5pt; font-weight: 600; margin-bottom: 10px; }}
.question-label {{ font-size: 9pt; font-weight: 600; margin-bottom: 8px; }}
.question-section {{ color: {text_secondary}; font-size: 9.5pt; font-weight: 600; margin-top: 12px; margin-bottom: 6px; }}
.toggle {{ margin-top: 8px; margin-bottom: 4px; font-size: 9pt; }}
.spacer {{ font-size: 2px; margin-top: 0; margin-bottom: 12px; }}
a {{ color: {text_secondary}; text-decoration: none; font-weight: 600; }}
"""


def _theme_colors(colors=None):
    if colors is not None:
        return dict(colors)
    # Standalone callers use the same tokens as the application's theme.
    from ui.theme import LIGHT_COLORS
    return dict(LIGHT_COLORS)


def _source(identifier: str, body: str) -> str:
    """Give original material a native document range without hidden text.

    An unpainted table supplies an exact range even for nested source tables,
    images and text spanning several blocks. Only its containing card is
    painted. Generated labels and collapse controls stay outside the range.
    """
    return ('<table width="100%" cellspacing="0" cellpadding="0"><tr><td>'
            f'<a name="ets-source:{identifier}"></a>{body}</td></tr></table>')


def _source_value(identifier: str, value: str) -> str:
    """Mark an original inline value without including its generated label."""
    length = len(value.encode("utf-16-le")) // 2
    return f'<a name="ets-value:{identifier}:{length}">{escape(value)}</a>'


def _option_source(identifier: str, label: str, body: str, colors) -> str:
    """Keep each option searchable while Qt supplies a fixed label column."""
    return ('<table width="100%" cellspacing="0" cellpadding="0" border="0"><tr>'
            f'<td width="{_LABEL_COLUMN_WIDTH}" valign="top">'
            f'<a name="ets-source:{identifier}"></a>'
            f'<font color="{colors["text_secondary"]}"><b>{escape(label)}.</b></font></td>'
            f'<td valign="top">{body}</td></tr></table>')


def _block(label: str, body: str, tone="question", identifier="", colors=None) -> str:
    colors = _theme_colors(colors)
    accent = colors[_CARD_STYLES[tone][1]]
    label_class = "section-label"
    if tone == "question":
        label, label_class, accent = "QUESTION", "question-label", colors["text_muted"]
    # Use real headings: Qt can drop empty anchors before image-only cells.
    heading = (f'<p class="{label_class}"><a name="ets-card:{tone}:{identifier}">'
               f'<font color="{accent}">{escape(label)}</font></a></p>')
    return (f'<table width="100%" cellspacing="0" cellpadding="{_CARD_PADDING}"><tr>'
            f'<td>{heading}{body}</td></tr></table><p class="spacer">&nbsp;</p>')


def render_question(question: Question, root: Path, image_width=640, expanded_answers=(),
                    shared_expanded=False, colors=None) -> str:
    """Render stored material with independent question and reference cards."""
    colors = _theme_colors(colors)

    def fragment(value):
        return safe_fragment(value, question.source, root, image_width)

    def source(identifier, value):
        return _source(identifier, fragment(value))

    def images(identifier, references):
        return "".join(_source(f"{identifier}:{index}",
                               _image_html(ref, question.source, root, image_width))
                       for index, ref in enumerate(references))

    def answer(identifier, stored):
        value = re.sub(r"(?:<br>\s*)+$", "", fragment(stored.text).strip())
        return _source(identifier, value)

    def card(label, body, tone, identifier):
        return _block(label, body, tone, identifier, colors)

    html = [f'<html><head><style>{_CSS.format(**colors)}</style></head><body>',
            f'<h1>Question {_source_value("group:id", question.id)}</h1>']
    if question.topic:
        html.append(source("group:topic", question.topic))
    if question.instructions:
        html.append(card("Instructions", source("group:instructions", question.instructions),
                         "context", "instructions"))
    if question.context or question.images:
        types = []
        if question.context:
            types.append("Reading Passage" if question.kind == "collector.choose" else "Transcript")
        if question.images:
            types.append("Images")
        body = [f'<p class="muted">{escape(" · ".join(types))}</p>',
                '<p class="toggle"><a href="ets-shared:0">'
                f'{"Hide shared material" if shared_expanded else "Show shared material"}</a></p>']
        if shared_expanded:
            if question.context:
                body.append(source("shared:context", question.context))
            body.append(images("shared:image", question.images))
        html.append(card("Shared Material", "".join(body), "context", "shared"))
    for index, part in enumerate(question.parts):
        number = _source_value(f"part:{index}:number", part.number) if part.number else str(index + 1)
        html.append(f'<h2><a name="ets-part:{index}"></a>Question '
                    f'<font color="{colors["text_secondary"]}">{number}</font></h2>')
        body = []
        if part.prompt:
            body.append(source(f"part:{index}:prompt", part.prompt))
        if part.context:
            if part.prompt:
                body.append('<h3 class="question-section">Transcript</h3>')
            body.append(source(f"part:{index}:context", part.context))
        body.append(images(f"part:{index}:image", part.images))
        if part.options:
            body.append('<h3 class="question-section">Options</h3>')
            for number, option in enumerate(part.options):
                body.append(_option_source(f"part:{index}:option:{number}", option.label,
                                           safe_fragment(option.text, question.source, root,
                                                         image_width - _LABEL_COLUMN_WIDTH), colors))
        html.append(card("Question", "".join(body), "question", f"part:{index}:question"))
        if not part.answers:
            html.append(card("Reference Answer",
                             '<p>Answer not found</p><p class="muted">'
                             'No reference answer is available in the original question bank.</p>',
                             "missing", f"part:{index}:missing"))
        else:
            html.append(card("Reference Answer", answer(f"part:{index}:answer:0", part.answers[0]),
                             "answer", f"part:{index}:primary"))
            remaining = len(part.answers) - 1
            if remaining:
                expanded = index in expanded_answers
                label = ("Show fewer answers" if expanded else
                         f"Show {remaining} more {'answer' if remaining == 1 else 'answers'}")
                extra = [f'<p class="toggle"><a href="ets-answer:{index}">{label}</a></p>']
                if expanded:
                    for number, stored in enumerate(part.answers[1:], start=1):
                        if number > 1:
                            extra.append(f'<hr color="{colors["border"]}">')
                        extra.append(f'<h3>Answer {number + 1}</h3>')
                        extra.append(answer(f"part:{index}:answer:{number}", stored))
                    extra.append(f'<p class="toggle"><a href="ets-answer:{index}#bottom">'
                                 'Show fewer answers</a></p>')
                html.append(card("Additional Reference Answers", "".join(extra), "additional",
                                 f"part:{index}:additional"))
        if part.translation:
            html.append(card("Translation", source(f"part:{index}:translation", part.translation),
                             "context", f"part:{index}:translation"))
        if part.explanation:
            html.append(card("Explanation", source(f"part:{index}:explanation", part.explanation),
                             "explanation", f"part:{index}:explanation"))
    if question.key_points:
        html.append(card("Key points", source("group:key-points", question.key_points),
                         "explanation", "key-points"))
    if question.explanation:
        html.append(card("Explanation", source("group:explanation", question.explanation),
                         "explanation", "group-explanation"))
    html.append("</body></html>")
    return "".join(html)


def _document_ranges(document):
    """Return source ranges, generated card tables and stable anchor positions."""
    anchors = {}
    block = document.begin()
    while block.isValid():
        iterator = block.begin()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            for name in fragment.charFormat().anchorNames():
                anchors[name] = fragment.position()
            iterator += 1
        block = block.next()
    cards = []
    for name, position in list(anchors.items()):
        if not name.startswith("ets-card:"):
            continue
        _, tone, identifier = name.split(":", 2)
        cursor = QTextCursor(document)
        cursor.setPosition(position)
        table = cursor.currentTable()
        if table is not None:
            table.setProperty("cardTone", tone)
            cards.append(table)
            anchors[f"ets-section:{identifier}"] = position
    sources = {}
    for name, position in anchors.items():
        if name.startswith("ets-value:"):
            identifier, length = name[10:].rsplit(":", 1)
            sources[identifier] = (position, position + int(length))
            continue
        if not name.startswith("ets-source:"):
            continue
        cursor = QTextCursor(document)
        cursor.setPosition(position)
        table = cursor.currentTable()
        if table is None:
            continue
        # Source markup may begin in a nested original table. Its unpainted
        # wrapper is the outermost table immediately under a card/root.
        while isinstance(table.parentFrame(), QTextTable) and not table.parentFrame().property("cardTone"):
            table = table.parentFrame()
        sources[name[11:]] = (table.firstPosition(), table.lastPosition())
    return sources, cards, anchors


def _folded_occurrences(text, terms):
    """Map Python Unicode casefold matches back to Qt UTF-16 positions."""
    folded, positions = [], []
    offset = 0
    for character in text:
        width = 2 if ord(character) > 0xFFFF else 1
        value = character.casefold()
        folded.append(value)
        positions.extend((offset, offset + width) for _ in value)
        offset += width
    folded = "".join(folded)
    matches = set()
    for term in terms:
        start = 0
        while (found := folded.find(term, start)) >= 0:
            end = found + len(term)
            matches.add((positions[found][0], positions[end - 1][1]))
            start = end
    return sorted(matches)


class LocalTextBrowser(QTextBrowser):
    """Secure native reader with source-aware collapse and match navigation."""

    matches_changed = Signal(int, int)
    active_part_changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.root: Path | None = None
        self._question: Question | None = None
        self._requested_image_width = 640
        self._image_width = 640
        self._rendered_image_width = 640
        self._has_images = False
        self._expanded_answers = set()
        self._shared_expanded = False
        self._answer_buttons: dict[str, QPushButton] = {}
        self._answer_positions: dict[str, int] = {}
        self._shared_buttons: dict[str, QPushButton] = {}
        self._shared_positions: dict[str, int] = {}
        self._card_tables = []
        self._source_ranges = {}
        self._anchors = {}
        self._query = ""
        self._matches = []
        self._match_index = -1
        self._active_part = -1
        self._rendering = False
        self._updating_width = False
        self._colors = _theme_colors()
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.document().setDocumentMargin(24)
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.anchorClicked.connect(self._toggle_answers)
        self.anchorClicked.connect(self._toggle_shared)
        self.verticalScrollBar().valueChanged.connect(self._position_answer_buttons)
        self.verticalScrollBar().valueChanged.connect(self._track_active_part)
        self.document().documentLayout().documentSizeChanged.connect(self._layout_changed)
        self._apply_palette()

    @property
    def match_count(self):
        return len(self._matches)

    @property
    def current_match(self):
        return self._match_index + 1 if self._matches else 0

    def _apply_palette(self):
        palette = self.palette()
        workspace = QColor(self._colors.get("workspace", self._colors["background"]))
        palette.setColor(QPalette.ColorRole.Base, workspace)
        palette.setColor(QPalette.ColorRole.Window, workspace)
        palette.setColor(QPalette.ColorRole.Text, QColor(self._colors["text_primary"]))
        self.setPalette(palette)
        self.viewport().setPalette(palette)
        self.viewport().setAutoFillBackground(True)

    def set_theme(self, colors):
        colors = _theme_colors(colors)
        if colors == self._colors:
            return
        self._colors = colors
        self._apply_palette()
        if self._question is not None:
            self._render()
        else:
            self.viewport().update()

    def setHtml(self, text):
        self._card_tables.clear()
        self._source_ranges.clear()
        self._anchors.clear()
        super().setHtml(text)
        self._format_body_blocks()
        self._source_ranges, self._card_tables, self._anchors = _document_ranges(self.document())
        self._format_question_content()

    def _format_question_content(self):
        document = self.document()
        cursor = QTextCursor(document)
        for identifier, (start, end) in self._source_ranges.items():
            dialogue = identifier.startswith("part:") and identifier.endswith(":context")
            option = ":option:" in identifier
            if not (dialogue or option):
                continue
            if dialogue:
                # A <br> before a new speaker needs its own hanging indent.
                # Replacing one separator with another preserves UTF-16 source
                # offsets, plain text and meaningful continuation/blank lines.
                breaks = []
                block = document.findBlock(start)
                while block.isValid() and block.position() < end:
                    for match in re.finditer(r"\u2028(?=[ \t]*[MW][:：])", block.text()):
                        offset = len(block.text()[:match.start()].encode("utf-16-le")) // 2
                        breaks.append(block.position() + offset)
                    block = block.next()
                for position in reversed(breaks):
                    cursor.setPosition(position)
                    cursor.setPosition(position + 1, QTextCursor.MoveMode.KeepAnchor)
                    cursor.insertBlock()
            block = document.findBlock(start)
            while block.isValid() and block.position() < end:
                match = re.match(r"[ \t]*[MW][:：][ \t]*", block.text()) if dialogue else None
                if option or match:
                    cursor.setPosition(block.position())
                    fmt = block.blockFormat()
                    fmt.setTopMargin(0)
                    fmt.setBottomMargin(4)
                    if match:
                        # Widen only the existing separator glyph; source text,
                        # inline emphasis and search offsets remain unchanged.
                        body = block.position() + match.end()
                        cursor.setPosition(body)
                        body_x = self.cursorRect(cursor).left()
                        cursor.setPosition(block.position())
                        extra = _LABEL_COLUMN_WIDTH - (body_x - self.cursorRect(cursor).left())
                        cursor.setPosition(body - 1)
                        cursor.setPosition(body, QTextCursor.MoveMode.KeepAnchor)
                        spacing = QTextCharFormat()
                        if match[0][-1].isspace():
                            spacing.setFontWordSpacing(extra)
                        else:
                            spacing.setFontLetterSpacingType(QFont.SpacingType.AbsoluteSpacing)
                            spacing.setFontLetterSpacing(extra)
                        cursor.mergeCharFormat(spacing)
                        cursor.setPosition(block.position())
                        cursor.setPosition(body, QTextCursor.MoveMode.KeepAnchor)
                        speaker = QTextCharFormat()
                        speaker.setForeground(QColor(self._colors["text_secondary"]))
                        cursor.mergeCharFormat(speaker)
                        fmt.setLeftMargin(fmt.leftMargin() + _LABEL_COLUMN_WIDTH)
                        fmt.setTextIndent(-_LABEL_COLUMN_WIDTH)
                    cursor.setBlockFormat(fmt)
                block = block.next()

    def _format_body_blocks(self):
        cursor = QTextCursor(self.document())
        cursor.beginEditBlock()
        block = self.document().begin()
        while block.isValid():
            if block.text() == "\xa0" and QTextCursor(block).currentTable() is None:
                # Qt ignores CSS paragraph margins between adjacent tables.
                # Give the generated spacer a reliable native line height.
                fmt = block.blockFormat()
                fmt.setLineHeight(14, QTextBlockFormat.LineHeightTypes.FixedHeight.value)
                fmt.setTopMargin(0)
                fmt.setBottomMargin(0)
                cursor.setPosition(block.position())
                cursor.setBlockFormat(fmt)
            if block.text().strip() and not block.blockFormat().headingLevel():
                control, image, answer_heading = False, False, False
                iterator = block.begin()
                while not iterator.atEnd():
                    fmt = iterator.fragment().charFormat()
                    control |= fmt.anchorHref().startswith(("ets-answer:", "ets-shared:"))
                    image |= fmt.isImageFormat()
                    answer_heading |= any(name.startswith("ets-card:answer:") for name in fmt.anchorNames())
                    iterator += 1
                fmt = block.blockFormat()
                if answer_heading:
                    fmt.setLeftMargin(20)
                if control:
                    fmt.setLineHeight(_ANSWER_CONTROL_HEIGHT, QTextBlockFormat.LineHeightTypes.FixedHeight.value)
                elif not image:
                    fmt.setLineHeight(_BODY_LINE_SPACING, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
                else:
                    block = block.next()
                    continue
                cursor.setPosition(block.position())
                cursor.setBlockFormat(fmt)
            block = block.next()
        cursor.endEditBlock()

    def _update_reading_width(self):
        if self._updating_width:
            return
        self._updating_width = True
        try:
            available = self.width() - 2 * self.frameWidth()
            if self.verticalScrollBar().isVisible():
                available -= self.verticalScrollBar().width()
            gutter = max(0, int((available - _READING_WIDTH) / 2))
            if self.viewportMargins().left() != gutter:
                self.setViewportMargins(gutter, 0, gutter, 0)
            if self.document().textWidth() != self.viewport().width():
                self.document().setTextWidth(self.viewport().width())
            image_available = (self.viewport().width() - 2 * self.document().documentMargin()
                               - 2 * _CARD_PADDING - self.verticalScrollBar().sizeHint().width())
            self._image_width = min(self._requested_image_width, max(1, int(image_available)))
        finally:
            self._updating_width = False

    def _card_bounds(self, table):
        bounds = self.document().documentLayout().frameBoundingRect(table)
        padding = table.format().cellPadding()
        return bounds.translated(-padding, -padding)

    def paintEvent(self, event):
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setClipRect(event.rect())
        painter.translate(-self.horizontalScrollBar().value(), -self.verticalScrollBar().value())
        for table in self._card_tables:
            background, _, border = _CARD_STYLES[table.property("cardTone")]
            painter.setBrush(QColor(self._colors[background]))
            painter.setPen(QPen(QColor(self._colors[border]), 1))
            painter.drawRoundedRect(self._card_bounds(table).adjusted(0.5, 0.5, -0.5, -0.5),
                                    _CARD_RADIUS, _CARD_RADIUS)
            if table.property("cardTone") == "answer":
                header = self.cursorRect(table.firstCursorPosition())
                center = QPointF(header.left() + self.horizontalScrollBar().value() - 12,
                                 header.center().y() + self.verticalScrollBar().value())
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor(self._colors["answer_accent"]), 1.25,
                                    Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                                    Qt.PenJoinStyle.RoundJoin))
                painter.drawEllipse(center, 5.5, 5.5)
                painter.drawLine(center + QPointF(-2.5, 0), center + QPointF(-0.5, 2))
                painter.drawLine(center + QPointF(-0.5, 2), center + QPointF(2.8, -2.2))
        painter.end()
        super().paintEvent(event)

    def clear_question(self):
        self._question = None
        self._has_images = False
        self._expanded_answers.clear()
        self._shared_expanded = False
        self._query = ""
        self._matches.clear()
        self._match_index = -1
        self._active_part = -1
        self.setExtraSelections([])
        for buttons in (self._answer_buttons, self._shared_buttons):
            for button in buttons.values():
                button.hide()
                button.deleteLater()
            buttons.clear()
        self._answer_positions.clear()
        self._shared_positions.clear()
        self._source_ranges.clear()
        self._anchors.clear()
        self.matches_changed.emit(0, 0)

    def show_question(self, question: Question, root: Path, image_width=640, query=None):
        changed = self._question is not question or self.root != root
        self._requested_image_width = image_width
        self._update_reading_width()
        if changed:
            self._expanded_answers.clear()
            self._shared_expanded = False
            self._query = "" if query is None else str(query)
            self._question, self.root = question, root
            self._build_match_catalog()
            self._expand_matching_sections()
            self._render(reset_scroll=True)
            self.matches_changed.emit(self.current_match, self.match_count)
            self._track_active_part()
        else:
            if self._has_images and self._rendered_image_width != self._image_width:
                self._render()
            if query is not None:
                self.set_search(query)

    def _logical_position(self, position):
        for identifier, (start, end) in self._source_ranges.items():
            if start <= position <= end:
                return identifier, position - start
        return None, position

    def _visible_position(self, logical):
        identifier, offset = logical
        if identifier is None:
            return min(offset, self.document().characterCount() - 1)
        bounds = self._source_ranges.get(identifier)
        if bounds is None:
            return None
        return min(bounds[0] + offset, bounds[1])

    def _view_state(self):
        cursor = self.textCursor()
        top = self.cursorForPosition(QPoint(24, 24))
        return (self._logical_position(cursor.anchor()), self._logical_position(cursor.position()),
                self._logical_position(top.position()), self.cursorRect(top).top(),
                self.verticalScrollBar().value())

    def _restore_view(self, state):
        anchor, position, top, top_y, scroll = state
        start, end = self._visible_position(anchor), self._visible_position(position)
        if start is not None and end is not None:
            cursor = QTextCursor(self.document())
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            self.setTextCursor(cursor)
        target = self._visible_position(top)
        if target is not None:
            cursor = QTextCursor(self.document())
            cursor.setPosition(target)
            bar = self.verticalScrollBar()
            bar.setValue(bar.value() + self.cursorRect(cursor).top() - top_y)
        else:
            self.verticalScrollBar().setValue(scroll)

    def _render(self, reset_scroll=False):
        if self._question is None or self.root is None:
            return
        state = None if reset_scroll else self._view_state()
        self._rendering = True
        try:
            self._answer_positions.clear()
            self._shared_positions.clear()
            html = render_question(self._question, self.root, self._image_width, self._expanded_answers,
                                   self._shared_expanded, self._colors)
            self._rendered_image_width = self._image_width
            self._has_images = '<img ' in html
            self.setHtml(html)
            self._update_reading_width()
            self._sync_answer_buttons()
            if state is not None:
                self._restore_view(state)
            else:
                self.verticalScrollBar().setValue(0)
            self._update_highlights()
        finally:
            self._rendering = False
        self._position_answer_buttons()
        self._track_active_part()

    def _sync_answer_buttons(self):
        positions = {}
        shared_positions = {}
        block = self.document().begin()
        while block.isValid():
            iterator = block.begin()
            while not iterator.atEnd():
                fragment = iterator.fragment()
                href = fragment.charFormat().anchorHref()
                if href.startswith(("ets-answer:", "ets-shared:")):
                    shared = href.startswith("ets-shared:")
                    target_positions = shared_positions if shared else positions
                    target_buttons = self._shared_buttons if shared else self._answer_buttons
                    target_positions[href] = fragment.position()
                    label = fragment.text()
                    button = target_buttons.get(href)
                    if button is None:
                        button = QPushButton(self.viewport())
                        button.setObjectName("sharedToggle" if shared else "answerToggle")
                        button.setCursor(Qt.CursorShape.PointingHandCursor)
                        button.setFixedHeight(_ANSWER_CONTROL_HEIGHT)
                        button.setProperty("answerControl", href)
                        button.setProperty("answerTarget", href.split("#", 1)[0])
                        button.clicked.connect(self._answer_button_clicked)
                        target_buttons[href] = button
                    expanded = self._shared_expanded if shared else int(QUrl(href).path()) in self._expanded_answers
                    button.setText(label)
                    button.setIcon(icon("up" if expanded else "down", self._colors["text_secondary"], size=12))
                    button.setIconSize(QSize(12, 12))
                    button.setAccessibleName(label)
                    button.setToolTip(label)
                    button.setStyleSheet(
                        "QPushButton { text-align: left; border: 1px solid transparent; border-radius: 5px;"
                        f"padding: 0 5px; color: {self._colors['text_secondary']};"
                        f"background: {self._colors['surface']}; }}"
                        f"QPushButton:hover {{ background: {self._colors['surface_hover']}; }}"
                        f"QPushButton:focus {{ border-color: {self._colors['border_focus']}; }}")
                    button.ensurePolished()
                iterator += 1
            block = block.next()
        for buttons, found in ((self._answer_buttons, positions), (self._shared_buttons, shared_positions)):
            for href in buttons.keys() - found.keys():
                button = buttons.pop(href)
                if button.hasFocus():
                    replacement = buttons.get(button.property("answerTarget"))
                    if replacement is not None:
                        replacement.setFocus(Qt.FocusReason.OtherFocusReason)
                button.hide()
                button.deleteLater()
        self._answer_positions, self._shared_positions = positions, shared_positions
        self._position_answer_buttons()

    @Slot()
    def _answer_button_clicked(self):
        url = QUrl(self.sender().property("answerControl"))
        if url.scheme() == "ets-shared":
            self._toggle_shared(url)
        else:
            self._toggle_answers(url)

    def _position_answer_buttons(self, *_):
        for buttons, positions in ((self._answer_buttons, self._answer_positions),
                                   (self._shared_buttons, self._shared_positions)):
            for href, position in positions.items():
                cursor = QTextCursor(self.document())
                cursor.setPosition(position)
                anchor = self.cursorRect(cursor)
                button = buttons[href]
                width = min(button.sizeHint().width(), max(0, self.viewport().width() - anchor.left() - 24))
                button.setGeometry(anchor.left() - 1, anchor.center().y() - button.height() // 2,
                                   width, button.height())
                # Hiding an offscreen focused button can focus another link and
                # make QTextBrowser jump. Let the viewport do the clipping.
                button.show()
                button.raise_()

    def _layout_changed(self, *_):
        if not self._rendering:
            self._update_reading_width()
            self._position_answer_buttons()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not hasattr(self, "_answer_positions"):
            return
        self._update_reading_width()
        if self._has_images and self._image_width != self._rendered_image_width and not self._rendering:
            self._render()
        self._position_answer_buttons()

    def _control_position(self, href):
        position = {**self._answer_positions, **self._shared_positions}.get(href)
        if position is None:
            return None
        cursor = QTextCursor(self.document())
        cursor.setPosition(position)
        return self.cursorRect(cursor).top()

    def _keep_control_position(self, href, old_y):
        new_y = self._control_position(href)
        if new_y is not None and old_y is not None:
            bar = self.verticalScrollBar()
            bar.setValue(bar.value() + new_y - old_y)

    @Slot(QUrl)
    def _toggle_answers(self, url):
        if (self._question is None or self.root is None or url.scheme() != "ets-answer"
                or not url.path().isdigit() or url.hasQuery() or url.host()
                or (url.hasFragment() and url.fragment() != "bottom")):
            return
        index = int(url.path())
        if not 0 <= index < len(self._question.parts) or len(self._question.parts[index].answers) < 2:
            return
        bottom = url.fragment() == "bottom"
        if bottom and index not in self._expanded_answers:
            return
        href = f"ets-answer:{index}"
        old_y = self._control_position(href)
        if index in self._expanded_answers:
            self._expanded_answers.remove(index)
        else:
            self._expanded_answers.add(index)
        self._render()
        if bottom:
            cursor = QTextCursor(self.document())
            cursor.setPosition(self._answer_positions[href])
            bar = self.verticalScrollBar()
            bar.setValue(bar.value() + self.cursorRect(cursor).top() - 24)
        else:
            self._keep_control_position(href, old_y)

    @Slot(QUrl)
    def _toggle_shared(self, url):
        if (self._question is None or self.root is None or url.scheme() != "ets-shared"
                or url.path() != "0" or url.hasQuery() or url.hasFragment() or url.host()
                or not (self._question.context or self._question.images)):
            return
        old_y = self._control_position("ets-shared:0")
        self._shared_expanded = not self._shared_expanded
        self._render()
        self._keep_control_position("ets-shared:0", old_y)

    def _build_match_catalog(self):
        self._matches = []
        self._match_index = -1
        terms = tuple(dict.fromkeys(self._query.casefold().split()))
        if not terms or self._question is None or self.root is None:
            return
        canonical = QTextDocument()
        canonical.setHtml(render_question(self._question, self.root, self._image_width,
                                         range(len(self._question.parts)), True, self._colors))
        sources, _, _ = _document_ranges(canonical)
        # Qt's selectedText() omits table-cell separators. Keep those native
        # positions when mapping matches into the option label/text columns.
        raw_text = canonical.toRawText().encode("utf-16-le")
        ordered = []
        for identifier, (start, end) in sources.items():
            text = raw_text[2 * start:2 * end].decode("utf-16-le")
            for offset, finish in _folded_occurrences(text, terms):
                ordered.append((start + offset, identifier, offset, finish - offset))
        self._matches = [(identifier, offset, length) for _, identifier, offset, length in sorted(ordered)]
        if self._matches:
            self._match_index = 0

    def _expand_source(self, identifier):
        if identifier.startswith("shared:"):
            changed = not self._shared_expanded
            self._shared_expanded = True
            return changed
        match = re.fullmatch(r"part:(\d+):answer:(\d+)", identifier)
        if match and int(match[2]) > 0:
            index = int(match[1])
            changed = index not in self._expanded_answers
            self._expanded_answers.add(index)
            return changed
        return False

    def _expand_matching_sections(self):
        changed = False
        for identifier, _, _ in self._matches:
            changed |= self._expand_source(identifier)
        return changed

    def set_search(self, query):
        query = str(query or "")
        if query == self._query:
            return
        self._query = query
        self._build_match_catalog()
        if self._expand_matching_sections():
            self._render()
        else:
            self._update_highlights()
        self.matches_changed.emit(self.current_match, self.match_count)

    def _match_cursor(self, index):
        identifier, offset, length = self._matches[index]
        bounds = self._source_ranges.get(identifier)
        if bounds is None:
            return None
        cursor = QTextCursor(self.document())
        cursor.setPosition(bounds[0] + offset)
        cursor.setPosition(bounds[0] + offset + length, QTextCursor.MoveMode.KeepAnchor)
        return cursor

    def _update_highlights(self):
        selections = []
        for index in range(len(self._matches)):
            cursor = self._match_cursor(index)
            if cursor is None:
                continue
            selection = QTextEdit.ExtraSelection()
            selection.cursor = cursor
            token = "search_highlight_active" if index == self._match_index else "search_highlight"
            selection.format.setBackground(QColor(self._colors[token]))
            selection.format.setForeground(QColor(self._colors["text_primary"]))
            selections.append(selection)
        self.setExtraSelections(selections)

    def navigate_match(self, step):
        if not self._matches:
            return
        self._match_index = (self._match_index + int(step)) % len(self._matches)
        if self._expand_source(self._matches[self._match_index][0]):
            self._render()
        cursor = self._match_cursor(self._match_index)
        if cursor is not None:
            self.setTextCursor(cursor)
            self.ensureCursorVisible()
        self._update_highlights()
        self.matches_changed.emit(self.current_match, self.match_count)

    def scroll_to_part(self, index):
        if self._question is None or not 0 <= index < len(self._question.parts):
            return
        position = self._anchors.get(f"ets-part:{index}")
        if position is None:
            return
        cursor = QTextCursor(self.document())
        cursor.setPosition(position)
        bar = self.verticalScrollBar()
        bar.setValue(bar.value() + self.cursorRect(cursor).top() - 24)
        self._set_active_part(index)

    def _set_active_part(self, index):
        if index != self._active_part:
            self._active_part = index
            self.active_part_changed.emit(index)

    def _track_active_part(self, *_):
        if self._rendering or self._question is None:
            return
        active = 0
        for index in range(len(self._question.parts)):
            position = self._anchors.get(f"ets-part:{index}")
            if position is None:
                continue
            cursor = QTextCursor(self.document())
            cursor.setPosition(position)
            if self.cursorRect(cursor).top() <= 48:
                active = index
            else:
                break
        self._set_active_part(active)

    def loadResource(self, resource_type, url):
        if resource_type != QTextDocument.ResourceType.ImageResource or self.root is None or not url.isLocalFile():
            return QImage()
        path = local_image(url.toString(), self.root / "content.json", self.root)
        if path is None:
            return QImage()
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        size = reader.size()
        if not size.isValid() or size.width() * size.height() > 40_000_000:
            return QImage()
        return reader.read()
