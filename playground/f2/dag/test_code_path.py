#!/usr/bin/env python3
"""Safety checks for POSIX workspace-relative paths.

The module is intentionally self-contained:

* standard library only (``unittest``);
* it never reads, writes or lists files;
* it never spawns subprocesses or shell commands;
* it never imports anything from ``playground``.

Public API
----------
``is_safe_relative_path(value) -> bool``
    Return ``True`` only when ``value`` is a safe workspace-relative path.
    All of the following rules must hold:

    1. ``value`` is a :class:`str` (``bytes``, ``os.PathLike`` and every
       other type are rejected);
    2. ``value`` is not empty;
    3. ``value`` contains no NUL character (``"\\x00"``);
    4. ``value`` contains no backslash (``"\\"``);
    5. ``value`` is relative - it does not start with ``"/"``;
    6. ``value`` starts with the literal prefix ``"playground/"``;
    7. every ``"/"``-separated segment is non-empty (no ``"//"``, no trailing
       ``"/"``);
    8. no segment is ``"."`` or ``".."``;
    9. no segment is hidden, i.e. no segment starts with ``"."``;
    10. the last segment contains a dot, so the path names a file rather than
        a directory.

Every ASCII C0 (U+0000 through U+001F) and DEL (U+007F) control
character is also rejected, including LF, CR, TAB and ESC. These
characters are unsafe in filenames that may enter logs or receipts.

Run the test suite with::

    python3 -m unittest discover -s playground/f2/dag -p test_code_path.py
"""

import unittest

__all__ = ["is_safe_relative_path", "IsSafeRelativePathTests"]

#: Workspace root that every accepted path has to live under.
WORKSPACE_PREFIX = "playground/"

#: Characters that are never allowed inside a workspace path.
NUL = "\x00"
BACKSLASH = "\\"

#: A segment starting with this character is hidden (dotfile).
HIDDEN_PREFIX = "."

#: Segment names that would escape or collapse the path.
RELATIVE_SEGMENTS = (".", "..")


def is_safe_relative_path(value):
    """Return ``True`` when ``value`` is a safe workspace-relative path.

    See the module docstring for the full list of rules.  The function is
    total: it never raises, it simply returns ``False`` for anything that is
    not an acceptable path.
    """
    # 1. Strings only.
    if not isinstance(value, str):
        return False

    # 2. Non-empty.
    if not value:
        return False

    # 3./4. No NUL bytes, no backslashes.
    if NUL in value or BACKSLASH in value:
        return False

    # Also reject all C0/DEL control characters (not just NUL); disjoint
    # ownership checking already applies this boundary.
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        return False

    # 5. Relative paths only.
    if value.startswith("/"):
        return False

    # 6. Must live inside the workspace.
    if not value.startswith(WORKSPACE_PREFIX):
        return False

    # 7. Split on the POSIX separator; every segment has to be non-empty.
    segments = value.split("/")
    for segment in segments:
        if not segment:
            return False

        # 8. No "." / ".." traversal segments.
        if segment in RELATIVE_SEGMENTS:
            return False

        # 9. No hidden components.
        if segment.startswith(HIDDEN_PREFIX):
            return False

    # 10. Filenames only: the last segment must carry a dot.
    if "." not in segments[-1]:
        return False

    return True


class IsSafeRelativePathTests(unittest.TestCase):
    """Unit tests for :func:`is_safe_relative_path`."""

    def test_accepts_workspace_file_paths(self):
        """Ordinary workspace file paths are accepted."""
        for value in (
            "playground/a.txt",
            "playground/README.md",
            "playground/f2/dag/test_code_path.py",
            "playground/fleet/receipt-fixture.json",
            "playground/dir/sub.dir/file.name.ext",
            "playground/name.with.dots.txt",
            "playground/file_with spaces.txt",
            "playground/f2/dag/_private.py",
        ):
            with self.subTest(value=value):
                self.assertTrue(is_safe_relative_path(value))

    def test_rejects_absolute_paths(self):
        """Absolute paths never qualify."""
        for value in (
            "/playground/f2/dag/test_code_path.py",
            "/etc/passwd",
            "//playground/a.txt",
            "/",
        ):
            with self.subTest(value=value):
                self.assertFalse(is_safe_relative_path(value))

    def test_rejects_backslash_and_nul(self):
        """Backslashes and NUL characters are rejected."""
        for value in (
            "playground\\a.txt",
            "playground/dir\\file.txt",
            "playground/a.txt\x00",
            "\x00playground/a.txt",
            "playground/a.txt\x00.png",
        ):
            with self.subTest(value=value):
                self.assertFalse(is_safe_relative_path(value))

    def test_rejects_ascii_c0_and_del_controls(self):
        """No control character may occur in a workspace path."""
        for code in (*range(0x20), 0x7F):
            with self.subTest(code=code):
                unsafe = "playground/f2/dag/name" + chr(code) + ".txt"
                self.assertFalse(is_safe_relative_path(unsafe))

    def test_rejects_non_string_values(self):
        """Only ``str`` instances are accepted."""
        for value in (
            None,
            b"playground/a.txt",
            bytearray(b"playground/a.txt"),
            42,
            3.5,
            True,
            ["playground/a.txt"],
            {"path": "playground/a.txt"},
            ("playground/a.txt",),
        ):
            with self.subTest(value=value):
                self.assertFalse(is_safe_relative_path(value))

    def test_rejects_empty_string(self):
        """The empty string is not a path."""
        self.assertFalse(is_safe_relative_path(""))

    def test_rejects_paths_without_workspace_prefix(self):
        """The ``playground/`` prefix is mandatory and case sensitive."""
        for value in (
            "a.txt",
            "src/a.txt",
            "playground",
            "Playground/a.txt",
            "PLAYGROUND/a.txt",
            " playground/a.txt",
            "myplayground/a.txt",
            "../playground/a.txt",
        ):
            with self.subTest(value=value):
                self.assertFalse(is_safe_relative_path(value))

    def test_rejects_empty_dot_and_dotdot_segments(self):
        """Empty, ``"."`` and ``".."`` segments are rejected."""
        for value in (
            "playground//a.txt",
            "playground///a.txt",
            "playground/./a.txt",
            "playground/../a.txt",
            "playground/dir/./file.txt",
            "playground/dir/../file.txt",
            "playground/dir/..",
            "playground/a.txt/",
            "playground/",
        ):
            with self.subTest(value=value):
                self.assertFalse(is_safe_relative_path(value))

    def test_rejects_hidden_components(self):
        """Hidden (dot-prefixed) components are rejected."""
        for value in (
            "playground/.hidden",
            "playground/.hidden.txt",
            "playground/.git/config",
            "playground/dir/.secret/file.txt",
            "playground/file.txt/.",
        ):
            with self.subTest(value=value):
                self.assertFalse(is_safe_relative_path(value))

    def test_rejects_paths_without_filename(self):
        """The last segment must look like a file name, not a directory."""
        for value in (
            "playground/dir",
            "playground/f2",
            "playground/dir/",
        ):
            with self.subTest(value=value):
                self.assertFalse(is_safe_relative_path(value))

    def test_is_deterministic(self):
        """Repeated calls return the same answer."""
        value = "playground/f2/dag/test_code_path.py"
        results = {is_safe_relative_path(value) for _ in range(5)}
        self.assertEqual(results, {True})


if __name__ == "__main__":
    unittest.main()
