"""Read-only repository documentation navigation contract (stdlib only)."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
LINK = re.compile(r"\[[^\]\n]+\]\(([^)\s]+\.md)\)")


class DocumentationNavigationTests(unittest.TestCase):
    def test_exactly_one_navigation_link_per_markdown_page(self):
        index = DOCS / "README.md"
        text = index.read_text(encoding="utf-8")
        paths = LINK.findall(text)
        actual = {p.name for p in DOCS.glob("*.md") if p.name != index.name}
        self.assertTrue(actual)
        self.assertEqual(len(paths), len(set(paths)), "Duplicate document link")
        self.assertEqual(set(paths), actual, "Document index missing/extra pages")
        for item in paths:
            with self.subTest(item=item):
                self.assertEqual(Path(item).name, item, "Unsafe relative link")
                self.assertTrue((DOCS / item).is_file())
                self.assertFalse((DOCS / item).is_symlink())

    def test_root_readme_links_to_local_index(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertEqual(
            readme.count("](docs/README.md)"), 1,
            "Root README must point to the local docs index exactly once")

    def test_editorial_f2_planning_document_is_valid_utf8_and_readable(self):
        # Regression for issue #113: source was irreversibly corrupted.
        target = DOCS / "ocfilo-f2.md"
        content = target.read_bytes().decode("utf-8", errors="strict")
        self.assertNotIn("\ufffd", content)
        self.assertFalse([ch for ch in content if ord(ch) < 32 and ch not in
                          "\n\r\t"], "Unexpected control character in F2 planning")
        self.assertIn("Editoryal yeniden yazım", content)
        self.assertIn("orijinalin birebir restorasyonu", content)
        self.assertIn("ocfilo_f2_plan.py", content)
        self.assertIn("execution_authorized=false", content)
        self.assertIn("F2 yol haritası Issue #10", content)

    def test_navigation_files_have_no_replacement_characters(self):
        for path in (ROOT / "README.md", DOCS / "README.md"):
            with self.subTest(path=path.name):
                self.assertNotIn("\ufffd", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
