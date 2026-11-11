"""Terminal rendering: widths, alignment, fallbacks."""

from __future__ import annotations

import unittest

from mendel import render


class WidthTests(unittest.TestCase):
    def test_ascii_and_cjk(self):
        self.assertEqual(render.display_width("abc"), 3)
        self.assertEqual(render.display_width("中文"), 4)
        self.assertEqual(render.display_width("a中b"), 4)

    def test_combining_marks_are_free(self):
        self.assertEqual(render.display_width("e\u0301"), 1)

    def test_pad(self):
        self.assertEqual(render.pad("中", 4), "中  ")
        self.assertEqual(render.pad("中", 4, "right"), "  中")
        self.assertEqual(render.pad("x", 3, "center"), " x ")

    def test_pad_never_truncates(self):
        self.assertEqual(render.pad("abcdef", 2), "abcdef")


class TableTests(unittest.TestCase):
    def test_columns_line_up_with_cjk(self):
        text = render.table(["name", "值"], [["a", "1"], ["bb", "22"]])
        lines = text.splitlines()
        # Header, separator, two rows.  First column is padded to width 4,
        # then two spaces, so the second column starts at character 6.
        self.assertEqual(lines[0][6:], "值")
        self.assertEqual(lines[2][6:], "1")
        self.assertEqual(lines[3][6:], "22")

    def test_no_trailing_spaces(self):
        text = render.table(
            ["a", "long header"],
            [["x", "y"], ["long value", "z"]],
        )
        for line in text.splitlines():
            self.assertEqual(line, line.rstrip(), repr(line))

    def test_right_alignment(self):
        text = render.table(["n", "v"], [["a", "1"], ["b", "100"]], aligns=["left", "right"])
        lines = text.splitlines()
        self.assertTrue(lines[2].endswith("  1"))
        self.assertTrue(lines[3].endswith("100"))

    def test_row_width_mismatch_is_rejected(self):
        with self.assertRaises(ValueError):
            render.table(["a", "b"], [["only one"]])

    def test_indent(self):
        text = render.table(["a"], [["1"]], indent="  ")
        self.assertTrue(all(line.startswith("  ") for line in text.splitlines()))


class NumberTests(unittest.TestCase):
    def test_number(self):
        self.assertEqual(render.number(1234567), "1,234,567")
        self.assertEqual(render.number(0), "0")

    def test_seconds_thresholds(self):
        self.assertEqual(render.seconds(0.0004), "400 µs")
        self.assertEqual(render.seconds(0.5), "500.0 ms")
        self.assertEqual(render.seconds(2.5), "2.50 s")

    def test_bar(self):
        self.assertEqual(render.bar(0, 10, width=5), "·····")
        self.assertEqual(render.bar(10, 10, width=5), "█████")
        self.assertEqual(render.bar(5, 10, width=4), "██··")
        self.assertEqual(render.bar(1, 0), "")


class TextTests(unittest.TestCase):
    def test_heading_underline_matches_display_width(self):
        text = render.heading("中文")
        lines = text.splitlines()
        self.assertEqual(render.display_width(lines[1]), 4)

    def test_wrap_keeps_words_and_indent(self):
        lines = render.wrap("alpha beta gamma delta", width=14, indent="> ")
        self.assertTrue(all(line.startswith("> ") for line in lines))
        self.assertEqual(" ".join(line[2:] for line in lines), "alpha beta gamma delta")
        self.assertTrue(all(render.display_width(line) <= 14 for line in lines))

    def test_glyph_fallback(self):
        original = render._UNICODE_OK
        try:
            render._UNICODE_OK = False
            self.assertEqual(render.glyph("─│├└"), "-|++")
            self.assertEqual(render.glyph("✓"), "ok")
        finally:
            render._UNICODE_OK = original

    def test_terminal_width_is_positive(self):
        self.assertGreater(render.terminal_width(), 0)


if __name__ == "__main__":
    unittest.main()
