"""Render source text and local prompt images without a web engine."""

from html import escape
from html.parser import HTMLParser
from pathlib import Path
import re
from urllib.parse import urlsplit

from PySide6.QtCore import Qt, QUrl, Slot
from PySide6.QtGui import QImage, QImageReader, QTextCursor, QTextDocument
from PySide6.QtWidgets import QPushButton, QTextBrowser

from core.parser import Question, plain_text


_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}


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
        if size.isValid() and size.width() * size.height() <= 40_000_000:
            display_width = min(max(width, 100), size.width())
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

    def handle_starttag(self, tag, attrs):
        if tag in self.blocked:
            self.suppressed += 1
            return
        if self.suppressed:
            return
        if tag == "img":
            reference = dict(attrs).get("src")
            if reference:
                self.output.append(_image_html(reference, self.source, self.root, self.width))
        elif tag in self.tags:
            safe_attrs = ""
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


def safe_fragment(value: str, source: Path, root: Path, image_width=640) -> str:
    renderer = _SafeMarkup(source, root, image_width)
    renderer.feed(value)
    renderer.close()
    return "".join(renderer.output)


_CSS = """
body { color: #e3e9f3; font-family: 'Segoe UI', 'Microsoft YaHei UI', sans-serif; font-size: 10.5pt; }
h1 { color: #f3f6fc; font-size: 13pt; font-weight: 600; margin-top: 0; margin-bottom: 16px; }
h2 { color: #c3d2f5; font-size: 11pt; margin-top: 16px; margin-bottom: 8px; }
h3 { color: #a8b9d2; font-size: 10pt; font-weight: 600; margin-top: 10px; margin-bottom: 6px; }
p { line-height: 135%; margin-top: 0; margin-bottom: 6px; }
.muted { color: #97a7be; font-size: 9.5pt; }
.section-label { font-size: 9.5pt; font-weight: 600; margin-bottom: 8px; }
.variant { margin-top: 10px; }
.variant-label { color: #8bc9b5; font-size: 9.5pt; font-weight: 600; }
.correct { color: #a0ebcd; font-weight: 600; font-size: 10.5pt; }
.missing { color: #f0ca89; font-size: 10.5pt; }
.toggle { margin-top: 8px; margin-bottom: 4px; font-size: 9pt; }
.spacer { font-size: 2px; margin-top: 0; margin-bottom: 8px; }
a { color: #a9c7ff; text-decoration: none; font-weight: 600; }
"""


def _block(label: str, body: str, tone="question") -> str:
    background, accent = {
        "question": ("#202a3a", "#c1d0ef"),
        "context": ("#1a2c42", "#9bbff0"),
        "answer": ("#18382f", "#9ae2c4"),
        "explanation": ("#332c22", "#e8c492"),
    }[tone]
    heading = (f'<p class="section-label"><font color="{accent}">{escape(label)}</font></p>'
               if label else "")
    return (f'<table width="100%" cellspacing="0" cellpadding="12"><tr>'
            f'<td bgcolor="{background}">{heading}{body}</td>'
            '</tr></table><p class="spacer">&nbsp;</p>')


def render_question(question: Question, root: Path, image_width=640, expanded_answers=()) -> str:
    """Render original content, previewing the first reference in each part.

    Additional stored variants are revealed by a local answer-control link.
    No source fields are interpreted as executable links or generated answers.
    """
    def fragment(value):
        return safe_fragment(value, question.source, root, image_width)

    def images(references):
        return "".join(_image_html(ref, question.source, root, image_width) for ref in references)

    def answer_text(answer):
        # Reference fields commonly end with </br>; avoid an empty last line.
        return re.sub(r"(?:<br>\s*)+$", "", fragment(answer.text).strip())

    html = [f'<html><head><style>{_CSS}</style></head><body>',
            f'<h1>Question {escape(question.id)}</h1>']
    if question.topic:
        html.append(f"<h2>{fragment(question.topic)}</h2>")
    if question.instructions:
        html.append(_block("Instructions", fragment(question.instructions)))
    if question.context:
        html.append(_block("Context", fragment(question.context), "context"))
    html.append(images(question.images))
    for index, part in enumerate(question.parts):
        if len(question.parts) > 1:
            html.append(f"<h2>Question {escape(part.number or str(index + 1))}</h2>")
        if part.prompt:
            html.append(_block("", fragment(part.prompt)))
        html.append(images(part.images))
        if part.context:
            # Passage questions store the actual question in xt_value when
            # xt_nr is only a template marker; do not label it as a dialogue.
            label = "Transcript" if part.prompt else ""
            html.append(_block(label, fragment(part.context), "context" if part.prompt else "question"))
        if part.translation:
            html.append(_block("Translation", fragment(part.translation), "context"))
        if part.options:
            html.append("<h3>Options</h3><table width='100%' cellspacing='3' cellpadding='8'>")
            labels = {plain_text(a.text).casefold() for a in part.answers}
            for option in part.options:
                correct = option.label.casefold() in labels
                background = "#204136" if correct else "#202a3a"
                color = "#96e8cb" if correct else "#d3ddec"
                html.append(f'<tr><td width="24" bgcolor="{background}"><font color="{color}">'
                            f'<b>{escape(option.label)}</b></font></td>'
                            f'<td bgcolor="{background}">{fragment(option.text)}</td></tr>')
            html.append('</table><p class="spacer">&nbsp;</p>')
        if not part.answers:
            html.append(_block("", '<p class="missing">Answer not found</p>', "explanation"))
        elif part.options:
            answers = []
            for answer in part.answers:
                answers.append(f'<p class="correct">{answer_text(answer)}</p>')
            html.append(_block("Answer", "".join(answers), "answer"))
        else:
            count = len(part.answers)
            expanded = index in expanded_answers
            body = [answer_text(part.answers[0])]
            if count > 1:
                label = "Show fewer answers" if expanded else f"Show all {count} answers"
                body.append(f'<p class="toggle"><a href="ets-answer:{index}">{label}</a></p>')
            if expanded:
                for number, answer in enumerate(part.answers[1:], start=2):
                    body.append(f'<div class="variant"><span class="variant-label">{number}.</span> {answer_text(answer)}</div>')
            label = "Answers" if expanded else "Answer"
            html.append(_block(label, "".join(body), "answer"))
        if part.explanation:
            html.append(_block("Explanation", fragment(part.explanation), "explanation"))
    if question.key_points:
        html.append(_block("Key points", fragment(question.key_points), "explanation"))
    if question.explanation:
        html.append(_block("Explanation", fragment(question.explanation), "explanation"))
    html.append('</body></html>')
    return "".join(html)


class LocalTextBrowser(QTextBrowser):
    """Local bank content with native controls over generated answer links."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.root: Path | None = None
        self._question: Question | None = None
        self._image_width = 640
        self._has_images = False
        self._expanded_answers = set()
        self._answer_buttons: dict[str, QPushButton] = {}
        self._answer_positions: dict[str, int] = {}
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.document().setDocumentMargin(24)
        self.anchorClicked.connect(self._toggle_answers)
        self.verticalScrollBar().valueChanged.connect(self._position_answer_buttons)

    def clear_question(self):
        self._question = None
        self._has_images = False
        self._expanded_answers.clear()
        for button in self._answer_buttons.values():
            button.hide()
            button.deleteLater()
        self._answer_buttons.clear()
        self._answer_positions.clear()

    def show_question(self, question: Question, root: Path, image_width=640):
        changed = self._question is not question or self.root != root
        rescale_images = self._has_images and self._image_width != image_width
        if changed:
            self._expanded_answers.clear()
        self._question, self.root, self._image_width = question, root, image_width
        # QTextBrowser already reflows text during live pane resizing. Only
        # images have explicit dimensions that require updated markup.
        if changed or rescale_images:
            self._render(reset_scroll=changed)

    def _render(self, reset_scroll=False):
        position = 0 if reset_scroll else self.verticalScrollBar().value()
        # setHtml rebuilds the document and emits scroll changes mid-update.
        # Old anchor positions are invalid until the new layout is ready.
        self._answer_positions.clear()
        html = render_question(self._question, self.root, self._image_width, self._expanded_answers)
        self._has_images = '<img ' in html
        self.setHtml(html)
        self.verticalScrollBar().setValue(position)
        self._sync_answer_buttons()

    def _sync_answer_buttons(self):
        """Present generated answer links as native, keyboard-accessible buttons."""
        positions = {}
        block = self.document().begin()
        while block.isValid():
            iterator = block.begin()
            while not iterator.atEnd():
                fragment = iterator.fragment()
                href = fragment.charFormat().anchorHref()
                if href.startswith("ets-answer:"):
                    positions[href] = fragment.position()
                    label = fragment.text()
                    button = self._answer_buttons.get(href)
                    if button is None:
                        button = QPushButton(self.viewport())
                        button.setObjectName("answerToggle")
                        button.setCursor(Qt.CursorShape.PointingHandCursor)
                        button.setFixedHeight(24)
                        button.setProperty("answerTarget", href)
                        button.clicked.connect(self._answer_button_clicked)
                        self._answer_buttons[href] = button
                    expanded = int(QUrl(href).path()) in self._expanded_answers
                    button.setText(f"{label}  {'▴' if expanded else '▾'}")
                    button.setAccessibleName(label)
                    button.setToolTip(label)
                    button.ensurePolished()
                iterator += 1
            block = block.next()
        for href in self._answer_buttons.keys() - positions.keys():
            button = self._answer_buttons.pop(href)
            button.hide()
            button.deleteLater()
        self._answer_positions = positions
        self._position_answer_buttons()

    @Slot()
    def _answer_button_clicked(self):
        self._toggle_answers(QUrl(self.sender().property("answerTarget")))

    def _position_answer_buttons(self, *_):
        for href, position in self._answer_positions.items():
            cursor = QTextCursor(self.document())
            cursor.setPosition(position)
            anchor = self.cursorRect(cursor)
            button = self._answer_buttons[href]
            width = min(button.sizeHint().width(), max(0, self.viewport().width() - anchor.left() - 24))
            button.setGeometry(anchor.left() - 1, anchor.center().y() - button.height() // 2, width, button.height())
            # Let the viewport clip offscreen controls. Hiding the focused
            # button makes QTextBrowser focus another link and scroll to it.
            button.show()
            button.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_answer_positions"):
            self._position_answer_buttons()

    @Slot(QUrl)
    def _toggle_answers(self, url):
        if (self._question is None or self.root is None or url.scheme() != "ets-answer"
                or not url.path().isdigit() or url.hasQuery() or url.hasFragment()):
            return
        index = int(url.path())
        if not 0 <= index < len(self._question.parts) or len(self._question.parts[index].answers) < 2:
            return
        if index in self._expanded_answers:
            self._expanded_answers.remove(index)
        else:
            self._expanded_answers.add(index)
        self._render()

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
