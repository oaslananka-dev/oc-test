"""Reviewed regression cases for the production F2 strict JSON gate."""
import unittest

from ocfilo_f2_strict_json import parse_strict_object, MAX_JSON_CHARS


class StrictJsonAcceptanceTests(unittest.TestCase):
    ALLOWED = frozenset({"a", "b"})

    def test_valid_nested_json_preserved(self):
        self.assertEqual(
            parse_strict_object('{"a":{"sub":[null,true,1,2.5]},"b":"x"}',self.ALLOWED),
            {"a":{"sub":[None,True,1,2.5]}, "b":"x"})
        self.assertEqual(parse_strict_object("{}",frozenset()),{})

    def test_duplicate_root_and_nested_keys_refused(self):
        for value in ('{"a":1,"a":2}', '{"a":{"x":1,"x":2}}',
                      '{"a":0,"\\u0061":1}', '{"a": [{"x":1,"x":3}]}'):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "Duplicate"):
                parse_strict_object(value,self.ALLOWED)

    def test_nonfinite_and_nonstandard_numbers_refused(self):
        for value in ('{"a":NaN}', '{"a":Infinity}', '{"a":-Infinity}',
                      '{"a":1e999}', '{"a":{"n":1e999}}'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_strict_object(value,self.ALLOWED)

    def test_root_type_unknown_fields_and_invalid_json_refused(self):
        for value in ('[]', 'null', 'false', '"string"', '{"unknown":1}', '{bad}'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_strict_object(value,self.ALLOWED)

    def test_maximum_size_and_input_type_are_bounded(self):
        for value in (None, 3, True, [], b"{}", '{"a":"'+"x"*MAX_JSON_CHARS+'"}'):
            with self.subTest(type=type(value).__name__), self.assertRaises(ValueError):
                parse_strict_object(value,self.ALLOWED)
        self.assertEqual(parse_strict_object("{}",self.ALLOWED,max_chars=2),{})
        for limit in (-1,0,MAX_JSON_CHARS+1,2.3,True):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                parse_strict_object("{}",self.ALLOWED,max_chars=limit)

    def test_allowlist_must_be_explicit_string_set(self):
        for allow in ("a", None, ["a"], {"a": 1}, frozenset({1}), True):
            with self.subTest(allow=allow),self.assertRaises(ValueError):
                parse_strict_object("{}",allow)


if __name__ == "__main__":
    unittest.main()
