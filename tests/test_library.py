import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sniplab import library  # noqa: E402


class LibraryTests(unittest.TestCase):
    def test_new_path_is_unique(self):
        with tempfile.TemporaryDirectory() as d:
            now = datetime(2026, 1, 2, 3, 4, 5)
            p1 = library.new_capture_path(Path(d), now=now)
            p1.write_bytes(b"x")
            p2 = library.new_capture_path(Path(d), now=now)
            self.assertNotEqual(p1, p2)
            self.assertEqual(p1.name, "SnipLab_2026-01-02_03-04-05.png")
            self.assertEqual(p2.name, "SnipLab_2026-01-02_03-04-05_1.png")

    def test_folder_created(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "a" / "b"
            library.new_capture_path(target)
            self.assertTrue(target.is_dir())

    def test_list_newest_first_and_filters(self):
        with tempfile.TemporaryDirectory() as d:
            old, new, other = Path(d, "old.png"), Path(d, "new.jpg"), Path(d, "notes.txt")
            for p in (old, new, other):
                p.write_bytes(b"x")
            os.utime(old, (1000, 1000))
            os.utime(new, (2000, 2000))
            self.assertEqual(library.list_captures(Path(d)), [new, old])

    def test_list_missing_folder(self):
        self.assertEqual(library.list_captures(Path("/definitely/not/here")), [])

    def test_delete(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d, "a.png")
            p.write_bytes(b"x")
            self.assertTrue(library.delete_capture(p))
            self.assertFalse(library.delete_capture(p))


if __name__ == "__main__":
    unittest.main()
