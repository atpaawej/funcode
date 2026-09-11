"""Unit tests for ui/complete.py (stdlib unittest, no terminal needed)."""
import unittest

from funcode.ui.complete import exact_command, match_commands


def _names(items, frag=""):
    return match_commands(items, frag)


class MatchTest(unittest.TestCase):
    def setUp(self):
        self.items = [
            ("compact", "builtin"), ("context", "builtin"), ("commit", "project:skills"),
            ("review", "project:commands"), ("providers", "builtin"),
            ("clear", "builtin"), ("ctx", "builtin"), ("resume", "builtin"),
        ]

    def test_empty_frag_returns_all_sorted(self):
        self.assertEqual(_names(self.items),
                         sorted(n for n, _ in self.items))

    def test_startswith_beats_substring_beats_fuzzy(self):
        # 'co': compact/context/commit start with it; providers contains it? no.
        # 'rov' is substring of providers; 'pew' would be fuzzy-only.
        got = _names(self.items, "co")
        self.assertEqual(got[:3], ["commit", "compact", "context"])

    def test_case_insensitive(self):
        self.assertEqual(_names(self.items, "CO")[:3], ["commit", "compact", "context"])

    def test_substring(self):
        got = _names(self.items, "rovi")
        self.assertEqual(got, ["providers"])

    def test_fuzzy_subsequence(self):
        got = _names(self.items, "rvw")
        self.assertEqual(got, ["review"])

    def test_no_match(self):
        self.assertEqual(_names(self.items, "zzz"), [])

    def test_duplicates_first_wins(self):
        items = [("tools", "builtin"), ("tools", "project:commands")]
        self.assertEqual(_names(items), ["tools"])

    def test_exact_typed_name_matches_itself(self):
        got = _names(self.items, "clear")
        self.assertEqual(got[0], "clear")


class ExactTest(unittest.TestCase):
    NAMES = {"clear", "compact", "review"}

    def test_exact(self):
        self.assertEqual(exact_command("/clear", self.NAMES), "clear")

    def test_exact_case_insensitive(self):
        self.assertEqual(exact_command("/CLEAR", self.NAMES), "clear")

    def test_with_args_is_not_exact(self):
        self.assertIsNone(exact_command("/clear foo", self.NAMES))

    def test_unknown_is_none(self):
        self.assertIsNone(exact_command("/nope", self.NAMES))

    def test_not_slash_is_none(self):
        self.assertIsNone(exact_command("hello", self.NAMES))
        self.assertIsNone(exact_command("", self.NAMES))

    def test_surrounding_whitespace_ok(self):
        self.assertEqual(exact_command("  /review  ", self.NAMES), "review")


class MenuCompleterTest(unittest.TestCase):
    """Drive the real prompt_toolkit completer with fake Documents."""

    def setUp(self):
        from prompt_toolkit.document import Document
        from funcode.ui.rich_cli import build_menu_completer
        self.Document = Document
        items = [("compact", "builtin"), ("context", "builtin"),
                 ("commit", "project:skills"), ("review", "project:commands"),
                 ("clear", "builtin")]
        meta = {n: (f"desc {n}", src) for n, src in items}
        self.comp = build_menu_completer(
            items, meta, ["src/main.py", "README.md", "pyproject.toml"])
        self.assertIsNotNone(self.comp)

    def _texts(self, text):
        doc = self.Document(text=text, cursor_position=len(text))
        return [c.text for c in self.comp.get_completions(doc, None)]

    def test_bare_slash_lists_all(self):
        got = self._texts("/")
        self.assertEqual(got, ["/clear", "/commit", "/compact", "/context", "/review"])

    def test_typing_filters(self):
        self.assertEqual(self._texts("/co"), ["/commit", "/compact", "/context"])

    def test_fuzzy(self):
        self.assertEqual(self._texts("/rvw"), ["/review"])

    def test_meta_shows_source(self):
        doc = self.Document(text="/co", cursor_position=3)
        metas = [c.display_meta_text
                 for c in self.comp.get_completions(doc, None)]
        self.assertTrue(all(" · " in m for m in metas))
        self.assertIn("project:skills", metas[0])

    def test_args_close_menu(self):
        self.assertEqual(self._texts("/review auth"), [])

    def test_plain_text_no_menu(self):
        self.assertEqual(self._texts("hello"), [])

    def test_at_files(self):
        got = self._texts("see @mai")
        self.assertIn("src/main.py", got)

    def test_unknown_slash_empty(self):
        self.assertEqual(self._texts("/zzz"), [])


if __name__ == "__main__":
    unittest.main()
