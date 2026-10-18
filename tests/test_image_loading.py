"""Focused local-image pipeline coverage, using synthetic ETS records."""

import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import QImage, QImageReader, QTextDocument
from PySide6.QtWidgets import QApplication

from core.parser import parse_document
from tests.bank import temporary_bank
from ui.renderer import LocalTextBrowser, local_image
from ui.theme import DARK_COLORS, LIGHT_COLORS


class ImageLoadingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.directory = self.enterContext(temporary_bank())
        self.root = Path(self.directory) / "题库 with spaces"
        self.source = self.root / "paper" / "content_synthetic" / "content.json"
        self.source.parent.mkdir(parents=True)
        self.browser = LocalTextBrowser()
        self.addCleanup(self.browser.close)
        self.browser.resize(1000, 650)
        self.browser.show()
        self.app.processEvents()

    def save_image(self, path, size=(1200, 600), color=Qt.GlobalColor.red):
        path.parent.mkdir(parents=True, exist_ok=True)
        image = QImage(*size, QImage.Format.Format_RGB32)
        image.fill(color)
        self.assertTrue(image.save(str(path)))
        return path

    def show_references(self, references):
        record = {"structure_type": "collector.dialogue", "info": {
            "stid": "synthetic", "question": [
                {"xh": str(index), "ask": f"Original prompt {index}", "askimg": reference}
                for index, reference in enumerate(references, 1)]}}
        self.source.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        question = parse_document(json.loads(self.source.read_text(encoding="utf-8")), self.source)
        self.assertEqual(tuple(part.images for part in question.parts), tuple((ref,) for ref in references))
        self.browser.show_question(question, self.root, image_width=820)
        self.app.processEvents()
        return self.image_formats()

    def image_formats(self):
        # allFormats also contains obsolete formats after an in-place reflow;
        # inspect only actual image fragments in the current document.
        images = []
        block = self.browser.document().begin()
        while block.isValid():
            iterator = block.begin()
            while not iterator.atEnd():
                fragment = iterator.fragment()
                fmt = fragment.charFormat()
                if fmt.isImageFormat():
                    images.append((fragment.position(), fmt.toImageFormat()))
                iterator += 1
            block = block.next()
        return images

    def assert_document_image(self, fmt, path, size=(1200, 600)):
        url = QUrl(fmt.name())
        self.assertTrue(url.isLocalFile())
        self.assertEqual(Path(url.toLocalFile()), path)
        resource = self.browser.document().resource(QTextDocument.ResourceType.ImageResource, url)
        self.assertIsInstance(resource, QImage)
        self.assertFalse(resource.isNull())
        self.assertEqual((resource.width(), resource.height()), size)
        # The original is a solid red image. A working-directory decoy or an
        # unrelated question's identically named image must not be displayed.
        pixel = resource.pixelColor(size[0] // 2, size[1] // 2)
        self.assertGreater(pixel.red(), 240)
        self.assertLess(pixel.green(), 10)
        self.assertLess(pixel.blue(), 10)

    def test_png_and_jpeg_beside_source_load_independently_of_working_directory(self):
        names = ("prompt.png", "prompt.jpg")
        for name in names:
            self.save_image(self.source.parent / name)
        decoy = Path(self.directory) / "working-directory"
        for name in names:
            self.save_image(decoy / name, color=Qt.GlobalColor.blue)
        previous = Path.cwd()
        try:
            os.chdir(decoy)
            formats = self.show_references(names)
        finally:
            os.chdir(previous)
        self.assertEqual(len(formats), 2)
        for index, ((position, fmt), name) in enumerate(zip(formats, names), 1):
            self.assertEqual(local_image(name, self.source, self.root), self.source.parent / name)
            self.assert_document_image(fmt, self.source.parent / name)
            prompt = self.browser.document().find(f"Original prompt {index}")
            self.assertGreater(position, prompt.selectionEnd())
            if index == 1:
                self.assertLess(position, self.browser.document().find("Original prompt 2").selectionStart())
        self.assertNotIn("Image unavailable", self.browser.toPlainText())

    def test_bare_names_load_from_question_local_material_directory(self):
        names = ("material-image.png", "material-image.jpg")
        for name in names:
            self.save_image(self.source.parent / "material" / name)
        formats = self.show_references(names)
        self.assertEqual(len(formats), 2)
        for (_, fmt), name in zip(formats, names):
            path = self.source.parent / "material" / name
            self.assertEqual(local_image(name, self.source, self.root), path)
            self.assert_document_image(fmt, path)

    def test_unicode_spaces_and_encoded_file_urls_reach_qtextdocument(self):
        paths = [self.save_image(self.source.parent / "material" / name)
                 for name in ("示意 图 100% #.png", "本地 图片 & 100%.jpg")]
        references = [paths[0].name, paths[1].as_uri()]
        formats = self.show_references(references)
        self.assertEqual(len(formats), 2)
        for (_, fmt), path in zip(formats, paths):
            reader = QImageReader(str(path))
            self.assertTrue(reader.canRead())
            self.assertFalse(reader.read().isNull())
            self.assert_document_image(fmt, path)

    def test_explicit_material_paths_accept_native_and_forward_slashes(self):
        path = self.save_image(self.source.parent / "material" / "picture.jpg")
        relative = path.relative_to(self.source.parent)
        for reference in (str(relative), relative.as_posix()):
            with self.subTest(reference=reference):
                formats = self.show_references((reference,))
                self.assertEqual(len(formats), 1)
                self.assert_document_image(formats[0][1], path)

    def test_resizing_and_themes_preserve_images_aspect_ratio_and_small_image_size(self):
        large = self.save_image(self.source.parent / "large.jpg")
        small = self.save_image(self.source.parent / "material" / "small.png", size=(64, 32))
        self.show_references((large.name, small.name))
        widths = []
        for colors, width in ((DARK_COLORS, 400), (LIGHT_COLORS, 1200), (DARK_COLORS, 450)):
            self.browser.resize(width, 650)
            self.browser.set_theme(colors)
            self.app.processEvents()
            formats = self.image_formats()
            self.assertEqual(len(formats), 2)
            self.assert_document_image(formats[0][1], large)
            self.assert_document_image(formats[1][1], small, size=(64, 32))
            widths.append(formats[0][1].width())
            for _, fmt in formats:
                self.assertLessEqual(fmt.width(), self.browser.viewport().width() - 48 - 32)
                self.assertAlmostEqual(fmt.width() / fmt.height(), 2, delta=.02)
            self.assertEqual((formats[1][1].width(), formats[1][1].height()), (64, 32))
            self.assertEqual(self.browser.horizontalScrollBar().maximum(), 0)
        self.assertGreater(widths[1], widths[0])
        self.assertLess(widths[2], widths[1])

    def test_missing_image_does_not_borrow_same_filename_from_another_question(self):
        self.save_image(self.root / "other-paper" / "content_other" / "material" / "ques2askimg.jpg")
        self.show_references(("ques2askimg.jpg",))
        self.assertIsNone(local_image("ques2askimg.jpg", self.source, self.root))
        self.assertFalse(self.image_formats())
        self.assertIn("Image unavailable: ques2askimg.jpg", self.browser.toPlainText())

    def test_invalid_and_unsupported_files_keep_the_existing_fallback(self):
        for name in ("invalid.png", "invalid.jpg", "unsupported.svg"):
            (self.source.parent / name).write_bytes(b"not an image")
        self.show_references(("invalid.png", "invalid.jpg", "unsupported.svg"))
        self.assertFalse(self.image_formats())
        for name in ("invalid.png", "invalid.jpg", "unsupported.svg"):
            self.assertIn(f"Image unavailable: {name}", self.browser.toPlainText())

    def test_remote_network_and_outside_root_images_stay_blocked(self):
        outside = self.save_image(Path(self.directory) / "outside.png")
        references = ("https://example.invalid/image.jpg", "http://example.invalid/image.png",
                      "../../../outside.png", str(outside), outside.as_uri(),
                      r"\\example.invalid\share\image.jpg", "file://example.invalid/share/image.png",
                      "file:////example.invalid/share/image.png",
                      "file:///%5C%5Cexample.invalid/share/image.png")
        self.show_references(references)
        self.assertFalse(self.image_formats())
        self.assertEqual(self.browser.toPlainText().count("Image unavailable:"), len(references))
        for reference in references:
            self.assertIsNone(local_image(reference, self.source, self.root))
            image = self.browser.loadResource(QTextDocument.ResourceType.ImageResource, QUrl(reference))
            self.assertTrue(image.isNull())

    def test_existing_file_size_and_pixel_limits_remain_enforced(self):
        oversized = self.source.parent / "oversized.jpg"
        with oversized.open("wb") as stream:
            stream.seek(16 * 1024 * 1024)
            stream.write(b"x")
        self.assertIsNone(local_image(oversized.name, self.source, self.root))
        self.show_references((oversized.name,))
        self.assertIn("Image unavailable: oversized.jpg", self.browser.toPlainText())
        valid = self.save_image(self.source.parent / "too-many-pixels.png", size=(16, 8))
        # Oversized image metadata must be rejected before allocating decoded
        # pixels. Substitute metadata only, keeping a real validated local file.
        with patch("ui.renderer.QImageReader") as reader:
            reader.return_value.size.return_value = QSize(10000, 4001)
            reader.return_value.read.side_effect = AssertionError("Oversized image must not be decoded")
            self.show_references((valid.name,))
            image = self.browser.loadResource(QTextDocument.ResourceType.ImageResource, QUrl.fromLocalFile(str(valid)))
            self.assertTrue(image.isNull())
        self.assertFalse(self.image_formats())
        self.assertIn("Image unavailable: too-many-pixels.png", self.browser.toPlainText())


if __name__ == "__main__":
    unittest.main()
