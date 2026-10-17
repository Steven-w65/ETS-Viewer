"""Parse the five collector schemas observed in the local ETS bank.

Only explicit answer fields and stored std/ref values are answers. Speech
evaluator fields, prompts, transcripts and scoring keywords never are.
"""

from dataclasses import dataclass
from functools import cached_property
from html.parser import HTMLParser
import json
from pathlib import Path
import re


KIND_LABELS = {
    "collector.choose": "Multiple choice",
    "collector.dialogue": "Dialogue",
    "collector.picture": "Picture response",
    "collector.word": "Word / sentence",
    "collector.read": "Reading",
}


class UnsupportedFormat(ValueError):
    """A readable document has no supported, observed question schema."""


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.chunks = []

    def handle_data(self, data):
        self.chunks.append(data)

    def handle_starttag(self, tag, attrs):
        if tag in {"br", "p", "div", "li", "tr"}:
            self.chunks.append("\n")

    def handle_endtag(self, tag):
        self.handle_starttag(tag, [])


def plain_text(value: str) -> str:
    parser = _PlainText()
    parser.feed(value)
    return " ".join("".join(parser.chunks).split())


def _text(value) -> str:
    if value is None:
        return ""
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        raise ValueError("Expected a text field, found " + type(value).__name__)
    return str(value).strip()


def _prompt(value) -> str:
    # ets_thN is a template substitution marker in the actual content files.
    return re.sub(r"^\s*ets_th\d+\s*", "", _text(value)).strip()


def _objects(value, field: str) -> list[dict]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(x, dict) for x in value):
        raise ValueError(f"{field} must be an array of objects")
    return value


@dataclass(frozen=True)
class Option:
    label: str
    text: str


@dataclass(frozen=True)
class StoredAnswer:
    text: str
    field: str


@dataclass(frozen=True)
class SubQuestion:
    number: str
    prompt: str
    context: str = ""
    options: tuple[Option, ...] = ()
    answers: tuple[StoredAnswer, ...] = ()
    explanation: str = ""
    images: tuple[str, ...] = ()
    translation: str = ""


@dataclass(frozen=True)
class Question:
    id: str
    kind: str
    source: Path
    parts: tuple[SubQuestion, ...]
    context: str = ""
    instructions: str = ""
    topic: str = ""
    images: tuple[str, ...] = ()
    explanation: str = ""
    key_points: str = ""

    @cached_property
    def title(self) -> str:
        text = self.topic or next((p.prompt or p.context for p in self.parts if p.prompt or p.context), self.context)
        return plain_text(text) or KIND_LABELS[self.kind]

    @cached_property
    def search_text(self) -> str:
        chunks = [self.id, self.title, str(self.source), self.context, self.instructions,
                  self.explanation, self.key_points, KIND_LABELS[self.kind]]
        for part in self.parts:
            chunks.extend((f"{self.id}/{part.number}", part.prompt, part.context,
                           part.explanation, part.translation))
            chunks.extend(option.text for option in part.options)
            chunks.extend(answer.text for answer in part.answers)
        return plain_text("\n".join(chunks)).casefold()


def _images(info: dict, *keys: str) -> tuple[str, ...]:
    return tuple(text for key in keys if (text := _text(info.get(key))))


def _part_id(info: dict, key: str, seen: set[str], field: str) -> str:
    number = _text(info.get(key))
    if not number or number in seen:
        raise ValueError(f"{field}.{key} must identify a distinct sub-question")
    seen.add(number)
    return number


def _reference_answers(info: dict, field: str) -> tuple[StoredAnswer, ...]:
    answers = []
    explicit = _text(info.get("answer"))
    if explicit:
        answers.append(StoredAnswer(explicit, field + ".answer"))
    for key in ("std", "ref"):
        for index, entry in enumerate(_objects(info.get(key), field + "." + key)):
            text = _text(entry.get("value"))
            if plain_text(text):
                answers.append(StoredAnswer(text, f"{field}.{key}[{index}].value"))
    return tuple(answers)


def metadata_answer_map(metadata) -> dict[tuple[str, str], StoredAnswer]:
    """Validate the observed explicit answer table, keyed by both IDs."""
    result = {}
    if not isinstance(metadata, list):
        return result
    for entry in _objects(metadata, "metadata"):
        if entry.get("code_id") != "answer" or "code_json_array" not in entry:
            continue
        rows = json.loads(entry["code_json_array"])
        for index, row in enumerate(_objects(rows, "code_json_array")):
            stid, number, answer = (_text(row.get(key)) for key in ("stid", "xth", "answer"))
            if stid and number and answer:
                key = (stid, number)
                if key in result and result[key].text != answer:
                    raise ValueError(f"Conflicting metadata answers for {stid}/{number}")
                result[key] = StoredAnswer(answer, f"info.json:answer[{index}].answer")
    return result


def parse_document(data, source: Path, metadata=None) -> Question:
    """Extract one question group without altering the input or its files.

    The observed choice metadata wrapper is the only supported wrapper. Its
    answer table joins on both group ID and sub-question ID, never row order.
    """
    if isinstance(data, list):
        metadata = data
        wrapper = next((x for x in _objects(data, "metadata")
                        if x.get("code_id") == "content_choose"), None)
        if wrapper is None or "code_json_obj" not in wrapper:
            raise UnsupportedFormat("Metadata does not contain a complete supported question")
        data = {"structure_type": "collector.choose", "info": json.loads(wrapper["code_json_obj"])}
    if not isinstance(data, dict):
        raise UnsupportedFormat("No supported ETS collector schema")
    kind = data.get("structure_type")
    if kind not in KIND_LABELS:
        raise UnsupportedFormat(f"Unsupported question format: {kind or 'no collector schema'}")
    info = data.get("info")
    if not isinstance(info, dict):
        raise ValueError("info must be an object")
    stid = _text(info.get("stid"))
    if not stid:
        raise ValueError("Question has no stid; no ID mapping can be established")
    parts = []
    seen = set()
    common = dict(id=stid, kind=kind, source=Path(source))
    if kind == "collector.choose":
        answer_map = metadata_answer_map(metadata)
        for index, item in enumerate(_objects(info.get("xtlist"), "info.xtlist")):
            field = f"info.xtlist[{index}]"
            number = _part_id(item, "xt_xh", seen, field)
            prompt = _prompt(item.get("xt_nr"))
            context = _text(item.get("xt_value"))
            images = _images(item, "xt_pic")
            if not (prompt or context or images or _text(info.get("st_nr"))):
                raise ValueError(f"{field} has no question content")
            answer = _text(item.get("answer"))
            answers = (StoredAnswer(answer, field + ".answer"),) if answer else ()
            if not answers and (stid, number) in answer_map:
                answers = (answer_map[(stid, number)],)
            options = tuple(Option(_text(o.get("xx_mc")), _text(o.get("xx_nr")))
                            for o in _objects(item.get("xxlist"), field + ".xxlist"))
            parts.append(SubQuestion(number, prompt, context, options, answers,
                                     _text(item.get("xt_analy")), images))
        if not parts:
            raise ValueError("Choice group has no sub-questions")
        return Question(**common, parts=tuple(parts), context=_text(info.get("st_nr")),
                        instructions=_text(info.get("st_sm")), images=_images(info, "st_pic"))
    if kind == "collector.dialogue":
        for index, item in enumerate(_objects(info.get("question"), "info.question")):
            field = f"info.question[{index}]"
            number = _part_id(item, "xh", seen, field)
            prompt, images = _prompt(item.get("ask")), _images(item, "askimg")
            if not (prompt or images):
                raise ValueError(f"{field} has no question content")
            parts.append(SubQuestion(number, prompt,
                                     answers=_reference_answers(item, field),
                                     explanation=_text(item.get("analyze")),
                                     images=images))
        if not parts:
            raise ValueError("Dialogue group has no sub-questions")
        return Question(**common, parts=tuple(parts), context=_text(info.get("value")),
                        images=_images(info, "image"))
    if kind == "collector.picture":
        if not any(_text(info.get(key)) for key in ("value", "topic", "image")):
            raise ValueError("Picture record has no question content")
        part = SubQuestion("", _prompt(info.get("value")), answers=_reference_answers(info, "info"))
        return Question(**common, parts=(part,), topic=_text(info.get("topic")),
                        images=_images(info, "image"), explanation=_text(info.get("analyze")),
                        key_points=_text(info.get("keypoint")))
    # These observed word/reading records contain prompts, not reference answers.
    if not (_prompt(info.get("value")) or _images(info, "image")):
        raise ValueError("Word/reading record has no question content")
    part = SubQuestion(_text(info.get("xh")), _prompt(info.get("value")),
                       explanation=_text(info.get("analyze")),
                       translation=_text(info.get("translate")))
    return Question(**common, parts=(part,), images=_images(info, "image"))
