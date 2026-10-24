#!/usr/bin/env python3
"""F2 DAG code-owner guard: are per-task file scopes mutually disjoint?

``scopes_disjoint`` answers one question: may two tasks run in parallel
without fighting over the same file?  The predicate is deliberately strict
and fail-closed -- malformed or ambiguous input returns ``False`` instead of
raising, so a scheduler can use it as a plain boolean guard.

Accepted input
--------------
* ``scopes`` is a non-empty ``dict`` mapping non-empty string task IDs to
  non-empty ``list`` values of file paths;
* every path is a non-empty POSIX *relative* path: no leading ``/``, no
  backslash, no control characters, and no empty / ``.`` / ``..`` segment
  (so ``playground//x``, ``playground/./x`` and ``playground/../x`` are all
  rejected).

Rejected -- the function returns ``False``
-----------------------------------------
* malformed containers: not a dict, empty mapping, empty task ID, a value
  that is not a non-empty list, a path that is not a string;
* unsafe paths: absolute, backslash separated, or carrying empty/``.``/``..``
  segments or control characters;
* the same path claimed twice, case-insensitively, within one task or by two
  different tasks;
* nested collisions: one owned path is a directory prefix of another one, for
  example ``playground/a`` together with ``playground/a/b`` (``playground/a``
  and ``playground/ab`` are *not* nested, they are two separate files).

Distinct, valid relative paths are accepted; the ``playground/`` prefix policy
stays with the caller, this module only guards against overlap.

Pure stdlib: no network, no filesystem, no subprocess, no repo imports.
"""
import unittest

__all__ = ["scopes_disjoint"]


def _is_valid_path(path):
    """True when ``path`` is a safe POSIX-relative file path."""
    if not isinstance(path, str) or not path:
        return False
    if "\\" in path:
        return False
    if path.startswith("/"):
        return False
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in path):
        return False
    return all(segment not in ("", ".", "..") for segment in path.split("/"))


def scopes_disjoint(scopes: dict[str, list[str]]) -> bool:
    """Return True when no two tasks in ``scopes`` may own the same file.

    A scope plan is disjoint when every path is a valid POSIX-relative path
    and no path is claimed twice (case-insensitively) nor nested inside
    another owned path.  Anything else -- including malformed containers --
    is rejected with ``False``.
    """
    if not isinstance(scopes, dict) or not scopes:
        return False

    owned = {}  # casefolded path -> original path
    for task_id, paths in scopes.items():
        if not isinstance(task_id, str) or not task_id:
            return False
        if not isinstance(paths, list) or not paths:
            return False
        for path in paths:
            if not _is_valid_path(path):
                return False
            key = path.casefold()
            if key in owned:
                # Same path twice, inside one task or claimed by two tasks.
                return False
            owned[key] = path

    # Nested collision: an owned path is a directory prefix of another one.
    for key in owned:
        parts = key.split("/")
        for depth in range(1, len(parts)):
            if "/".join(parts[:depth]) in owned:
                return False
    return True


class ScopesDisjointTests(unittest.TestCase):
    """Positive and negative cases for the code-owner scope guard."""

    # --- positive -------------------------------------------------------

    def test_distinct_paths_for_two_agents_are_disjoint(self):
        scopes = {
            "f2-code5-alpha": ["playground/f2/dag/alpha.py"],
            "f2-code5-beta": ["playground/f2/dag/beta.py"],
        }
        self.assertTrue(scopes_disjoint(scopes))

    def test_one_task_with_several_distinct_paths_is_disjoint(self):
        scopes = {"f2-code5-alpha": ["playground/f2/dag/a.py",
                                     "playground/f2/dag/b.py"]}
        self.assertTrue(scopes_disjoint(scopes))

    def test_shared_directory_is_not_a_nested_collision(self):
        scopes = {"alpha": ["playground/f2/dag/a.py"],
                  "beta": ["playground/f2/dag/b.py"]}
        self.assertTrue(scopes_disjoint(scopes))

    def test_similar_names_are_not_nested(self):
        scopes = {"alpha": ["playground/a"], "beta": ["playground/ab"]}
        self.assertTrue(scopes_disjoint(scopes))

    def test_returns_a_real_boolean(self):
        scopes = {"alpha": ["playground/f2/dag/alpha.py"]}
        self.assertIs(scopes_disjoint(scopes), True)
        self.assertIs(scopes_disjoint({"alpha": ["playground/../x"]}), False)

    # --- cross-agent collision ------------------------------------------

    def test_cross_agent_same_path_collides(self):
        scopes = {
            "alpha": ["playground/f2/dag/shared.py"],
            "beta": ["playground/f2/dag/shared.py"],
        }
        self.assertFalse(scopes_disjoint(scopes))

    def test_casefold_duplicate_across_agents_collides(self):
        scopes = {
            "alpha": ["playground/f2/dag/Alpha.py"],
            "beta": ["playground/f2/dag/alpha.py"],
        }
        self.assertFalse(scopes_disjoint(scopes))

    def test_casefold_duplicate_inside_one_agent_collides(self):
        scopes = {"alpha": ["playground/f2/dag/Alpha.py",
                            "playground/f2/dag/alpha.py"]}
        self.assertFalse(scopes_disjoint(scopes))

    def test_nested_collision_across_agents_collides(self):
        scopes = {
            "alpha": ["playground/a"],
            "beta": ["playground/a/b"],
        }
        self.assertFalse(scopes_disjoint(scopes))

    def test_nested_collision_inside_one_agent_collides(self):
        scopes = {"alpha": ["playground/a", "playground/a/b"]}
        self.assertFalse(scopes_disjoint(scopes))

    # --- traversal and unsafe paths -------------------------------------

    def test_dotdot_traversal_rejected(self):
        scopes = {"alpha": ["playground/f2/dag/../../etc/passwd"]}
        self.assertFalse(scopes_disjoint(scopes))

    def test_dot_segment_and_empty_segment_rejected(self):
        self.assertFalse(scopes_disjoint({"alpha": ["playground/./a.py"]}))
        self.assertFalse(scopes_disjoint({"alpha": ["playground//a.py"]}))
        self.assertFalse(scopes_disjoint({"alpha": ["playground/a.py/"]}))

    def test_absolute_path_rejected(self):
        self.assertFalse(scopes_disjoint({"alpha": ["/etc/passwd"]}))
        self.assertFalse(scopes_disjoint({"alpha": ["/playground/a.py"]}))

    def test_backslash_path_rejected(self):
        self.assertFalse(scopes_disjoint({"alpha": ["playground\\a.py"]}))

    def test_non_string_path_rejected(self):
        self.assertFalse(scopes_disjoint({"alpha": [42]}))
        self.assertFalse(scopes_disjoint({"alpha": [None]}))
        self.assertFalse(scopes_disjoint({"alpha": ["playground/a.py", 7]}))

    def test_control_character_path_rejected(self):
        self.assertFalse(scopes_disjoint({"alpha": ["playground/a\x00.py"]}))

    # --- malformed containers -------------------------------------------

    def test_malformed_containers_rejected(self):
        self.assertFalse(scopes_disjoint([]))
        self.assertFalse(scopes_disjoint("playground/a.py"))
        self.assertFalse(scopes_disjoint(None))
        self.assertFalse(scopes_disjoint({}))
        self.assertFalse(scopes_disjoint({"": ["playground/a.py"]}))
        self.assertFalse(scopes_disjoint({"alpha": []}))
        self.assertFalse(scopes_disjoint({"alpha": "playground/a.py"}))
        self.assertFalse(scopes_disjoint({"alpha": ("playground/a.py",)}))
        self.assertFalse(scopes_disjoint({1: ["playground/a.py"]}))


if __name__ == "__main__":
    unittest.main()
