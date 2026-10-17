"""Export stored question/answer pairs without changing the question bank."""

from collections.abc import Sequence
from pathlib import Path
import re

from PySide6.QtCore import QIODevice, QSaveFile

from core.parser import KIND_LABELS, Question, plain_text


_HEADER = 'ETS-Viewer answer export'
_GROUP_LINE = '=' * 72
_ANSWER_LINE = '-' * 72


def export_filename(task_name: str) -> str:
    """Keep the task name, replacing characters Windows cannot save."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', task_name).strip().rstrip(' .')
    if not name:
        name = 'ETS-answers'
    if re.fullmatch(r'CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³]', name.split('.')[0], re.IGNORECASE):
        name = '_' + name
    return name + '.txt'


def format_answers(task_name: str, questions: Sequence[Question]) -> str:
    """Use the complete paper and exactly its first stored answer per part."""
    lines = [_HEADER, f'Task: {task_name}',
             'Only the first stored answer for each question is included.', '']
    for question in questions:
        lines.extend((_GROUP_LINE, f'Question group: {question.id}', KIND_LABELS[question.kind]))
        for label, text in (('Topic', question.topic), ('Instructions', question.instructions),
                            ('Context', question.context)):
            if text:
                lines.extend(('', f'{label}:', plain_text(text)))
        for part in question.parts:
            identifier = f'{question.id}/{part.number}' if part.number else question.id
            lines.extend(('', f'Question: {identifier}'))
            if part.prompt:
                lines.append(plain_text(part.prompt))
            if part.context:
                lines.extend(('', 'Context:', plain_text(part.context)))
            if part.translation:
                lines.extend(('', 'Translation:', plain_text(part.translation)))
            if part.options:
                lines.extend(('', 'Options:'))
                lines.extend(f'{plain_text(option.label)}. {plain_text(option.text)}' for option in part.options)
            answer = plain_text(part.answers[0].text) if part.answers else 'Answer not found'
            lines.extend(('', _ANSWER_LINE, 'Answer:', answer, _ANSWER_LINE))
        lines.append('')
    return '\n'.join(lines) + '\n'


def write_answers(destination: Path, task_name: str, questions: Sequence[Question], *, bank_root: Path) -> None:
    """Write Windows-friendly UTF-8 text atomically, preserving bank originals.

    Existing exports can be replaced after the save dialog's overwrite prompt.
    Other existing files inside the scanned bank are protected.
    """
    destination = Path(destination).resolve()
    if destination.exists() and destination.is_relative_to(Path(bank_root).resolve()):
        with destination.open('rb') as original:
            prefix = original.read(128).decode('utf-8-sig', errors='replace')
        if not prefix.startswith(_HEADER + '\nTask: '):
            raise PermissionError('Choose a different filename. Original question bank files cannot be overwritten.')

    data = format_answers(task_name, questions).encode('utf-8-sig')
    output = QSaveFile(str(destination))
    # Atomic replacement prevents an interrupted or failed write from
    # truncating an earlier export. Do not fall back to writing in place.
    output.setDirectWriteFallback(False)
    if not output.open(QIODevice.OpenModeFlag.WriteOnly):
        raise OSError(output.errorString())
    try:
        if output.write(data) != len(data):
            raise OSError(output.errorString())
        if not output.commit():
            raise OSError(output.errorString())
    except Exception:
        output.cancelWriting()
        raise
