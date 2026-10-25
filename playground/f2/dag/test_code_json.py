#!/usr/bin/env python3
"""Strict JSON object parsing with an explicit root-key allow-list.

Self-contained, stdlib-only helper for the DAG code fixtures. It accepts a
plain JSON object and refuses everything else, raising ``ValueError`` for
every rejection:

* duplicate object keys (caught at any nesting depth via ``object_pairs_hook``)
* the non-standard ``NaN`` / ``Infinity`` / ``-Infinity`` constants
  (``parse_constant``) and floats that overflow to infinity
* a root value that is a list, a string, a number, a boolean or ``null``
* root keys that are not part of ``allowed_keys``
* input that is not a ``str``
* text longer than ``MAX_TEXT_LENGTH`` characters

``allowed_keys`` describes the *root* object only; nested objects keep their
own structure and are still checked for duplicate keys and non-finite
numbers. Nothing here touches the network or the filesystem.

Run the checks with::

    python3 -m unittest discover -s playground/f2/dag -p test_code_json.py
"""
from __future__ import annotations

import json
import math
import unittest

MAX_TEXT_LENGTH = 8192
"""Maximum accepted length of the raw JSON text, in characters."""


def _reject_constant(name: str):
    """``parse_constant`` hook: reject JSON's non-standard float constants."""
    raise ValueError(f"Non-standard JSON constant rejected: {name}")


def _strict_float(text: str) -> float:
    """``parse_float`` hook: reject numbers that overflow to infinity."""
    value = float(text)
    if not math.isfinite(value):
        raise ValueError(f"Non-finite JSON number rejected: {text}")
    return value


def _object_pairs(pairs):
    """``object_pairs_hook``: reject duplicate keys at any nesting depth."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key rejected: {key!r}")
        result[key] = value
    return result


def _allowed_key_set(allowed_keys) -> frozenset:
    """Normalize and validate the allow-list of root keys."""
    if isinstance(allowed_keys, (str, bytes, bytearray)):
        raise ValueError("allowed_keys must be a collection of key strings")
    try:
        keys = frozenset(allowed_keys)
    except TypeError as exc:
        raise ValueError("allowed_keys must be an iterable of key strings") from exc
    for key in keys:
        if not isinstance(key, str):
            raise ValueError("allowed_keys must contain only strings")
    return keys


def parse_strict_object(raw: str, allowed_keys: frozenset[str]) -> dict:
    """Parse ``raw`` as a strict JSON object with allow-listed root keys.

    Args:
        raw: the JSON text to parse.
        allowed_keys: the root keys that may appear in the object.

    Returns:
        The parsed object as a ``dict``.

    Raises:
        ValueError: if the input is not a string, is too long, is not valid
            JSON, is not a JSON object, contains duplicate keys, contains a
            non-finite number, or contains a root key outside
            ``allowed_keys``.
    """
    if not isinstance(raw, str):
        raise ValueError(f"Raw JSON must be a str, got {type(raw).__name__}")
    if len(raw) > MAX_TEXT_LENGTH:
        raise ValueError(
            f"Raw JSON exceeds the {MAX_TEXT_LENGTH} character limit: {len(raw)}"
        )
    allowed = _allowed_key_set(allowed_keys)
    try:
        parsed = json.loads(
            raw,
            object_pairs_hook=_object_pairs,
            parse_constant=_reject_constant,
            parse_float=_strict_float,
        )
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON text: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(
            f"Root JSON value must be an object, got {type(parsed).__name__}"
        )
    unknown = sorted(key for key in parsed if key not in allowed)
    if unknown:
        raise ValueError(
            "Unknown key(s) rejected: " + ", ".join(repr(key) for key in unknown)
        )
    return parsed


class ParseStrictObjectTests(unittest.TestCase):
    """Positive and negative checks for :func:`parse_strict_object`."""

    ALLOWED = frozenset({"alpha", "beta"})

    def reject(self, raw, allowed=None):
        """Assert that ``raw`` is refused with a ``ValueError``."""
        with self.assertRaises(ValueError):
            parse_strict_object(raw, self.ALLOWED if allowed is None else allowed)

    def test_valid_object_is_parsed(self):
        result = parse_strict_object('{"alpha": 1, "beta": "two"}', self.ALLOWED)
        self.assertEqual(result, {"alpha": 1, "beta": "two"})
        self.assertIsInstance(result, dict)

    def test_empty_object_is_accepted(self):
        self.assertEqual(parse_strict_object("{}", frozenset()), {})
        self.assertEqual(parse_strict_object("  {  }  ", frozenset()), {})

    def test_nested_values_are_preserved(self):
        raw = '{"alpha": {"n": [1, 2]}, "beta": null}'
        self.assertEqual(
            parse_strict_object(raw, self.ALLOWED),
            {"alpha": {"n": [1, 2]}, "beta": None},
        )

    def test_duplicate_keys_are_rejected(self):
        self.reject('{"alpha": 1, "alpha": 2}')

    def test_duplicate_nested_keys_are_rejected(self):
        self.reject('{"alpha": {"k": 1, "k": 2}}')

    def test_nan_and_infinity_are_rejected(self):
        self.reject('{"alpha": NaN}')
        self.reject('{"alpha": Infinity}')
        self.reject('{"alpha": -Infinity}')
        self.reject('{"alpha": {"deep": NaN}}')

    def test_overflowing_float_is_rejected(self):
        self.reject('{"alpha": 1e999}')

    def test_root_list_is_rejected(self):
        self.reject("[1, 2, 3]")
        self.reject("[]")

    def test_root_scalar_is_rejected(self):
        self.reject('"text"')
        self.reject("42")
        self.reject("null")
        self.reject("true")

    def test_unknown_root_key_is_rejected(self):
        self.reject('{"alpha": 1, "gamma": 2}')

    def test_non_string_input_is_rejected(self):
        for raw in (None, b"{}", bytearray(b"{}"), 42, 3.5, True, {"alpha": 1}, ["alpha"]):
            with self.subTest(raw=raw):
                self.reject(raw)

    def test_excessively_long_text_is_rejected(self):
        self.reject('{"alpha": "' + "x" * MAX_TEXT_LENGTH + '"}')
        self.reject("x" * (MAX_TEXT_LENGTH + 1))

    def test_maximum_length_text_is_accepted(self):
        prefix, suffix = '{"alpha": "', '"}'
        filler = "x" * (MAX_TEXT_LENGTH - len(prefix) - len(suffix))
        raw = prefix + filler + suffix
        self.assertEqual(len(raw), MAX_TEXT_LENGTH)
        self.assertEqual(parse_strict_object(raw, self.ALLOWED),
                         {"alpha": filler})

    def test_malformed_json_is_rejected(self):
        self.reject('{"alpha": 1')
        self.reject("{'alpha': 1}")
        self.reject('{"alpha": 1} trailing')

    def test_invalid_allowed_keys_are_rejected(self):
        with self.assertRaises(ValueError):
            parse_strict_object("{}", "alpha")
        with self.assertRaises(ValueError):
            parse_strict_object("{}", frozenset({1}))
        with self.assertRaises(ValueError):
            parse_strict_object("{}", None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
