#!/usr/bin/env python3
"""Self-contained SHA-256 digest verification helper plus its unit tests.

Standard library only. No network access, no filesystem IO, no subprocesses,
no third-party dependencies. The module is standalone: it can be copied to any
Python 3 interpreter and executed directly.

Public API
----------
verify_sha256(data, expected_hex) -> bool
    Constant-time comparison of the SHA-256 digest of ``data`` against a
    caller supplied ``expected_hex`` string.

The helper is deliberately fail-closed: every malformed input yields ``False``
rather than an exception, so callers can treat the return value as the single
authoritative verdict.

Example
-------
    >>> verify_sha256(
    ...     b"abc",
    ...     "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
    ... )
    True
"""

import hashlib
import hmac
import re
import unittest

__all__ = ["verify_sha256", "VerifySha256Tests"]

# A SHA-256 digest is exactly 64 hexadecimal characters. Only the lowercase
# alphabet is accepted: uppercase and mixed-case digests are rejected so that a
# single canonical textual form exists for every digest.
_HEX64_RE = re.compile(r"[0-9a-f]{64}")

# Well-known SHA-256 test vectors (FIPS 180-4 / NIST examples).
SHA256_ABC = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
SHA256_EMPTY = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def verify_sha256(data, expected_hex) -> bool:
    """Verify that ``data`` hashes to ``expected_hex``.

    Parameters
    ----------
    data:
        The payload to hash. Must be a ``bytes`` instance. ``bytearray``,
        ``memoryview`` and ``str`` are rejected.
    expected_hex:
        The expected digest as a ``str`` of exactly 64 lowercase hexadecimal
        characters (``[0-9a-f]``). Any other length, alphabet, case, or type
        is rejected. ``bool`` and numeric values are rejected even though
        ``bool`` is a subclass of ``int``.

    Returns
    -------
    bool
        ``True`` only when ``data`` is ``bytes`` and its SHA-256 digest matches
        ``expected_hex``; ``False`` for every other case. The comparison uses
        :func:`hmac.compare_digest` on the two 64-character digest strings so
        that the check does not leak timing information about the expected
        value.
    """
    if not isinstance(data, bytes):
        return False

    # bool is a subclass of int, so reject it explicitly before the str check
    # to keep the intent obvious: only a real digest string is acceptable.
    if isinstance(expected_hex, bool) or not isinstance(expected_hex, str):
        return False

    # fullmatch anchors both ends, so a trailing newline or embedded space
    # cannot sneak past the length/character-class requirement.
    if _HEX64_RE.fullmatch(expected_hex) is None:
        return False

    digest = hashlib.sha256(data).hexdigest()
    return hmac.compare_digest(digest, expected_hex)


class VerifySha256Tests(unittest.TestCase):
    """Offline unit tests for :func:`verify_sha256`."""

    def test_known_hash_for_abc(self):
        """The canonical SHA-256 of b"abc" is accepted."""
        self.assertTrue(verify_sha256(b"abc", SHA256_ABC))

    def test_known_hash_for_empty_payload(self):
        """The canonical SHA-256 of b"" is accepted."""
        self.assertTrue(verify_sha256(b"", SHA256_EMPTY))

    def test_wrong_content_is_rejected(self):
        """A correct digest for different content must not verify."""
        self.assertFalse(verify_sha256(b"abd", SHA256_ABC))
        self.assertFalse(verify_sha256(b"", SHA256_ABC))
        self.assertFalse(verify_sha256(b"abc ", SHA256_ABC))

    def test_uppercase_digest_is_rejected(self):
        """Uppercase digests are not the canonical lowercase form."""
        self.assertFalse(verify_sha256(b"abc", SHA256_ABC.upper()))

    def test_malformed_digest_is_rejected(self):
        """Wrong length, wrong alphabet, whitespace and trailing junk fail."""
        malformed = (
            "",
            "abc",
            SHA256_ABC[:-1],
            SHA256_ABC + "0",
            SHA256_ABC[:-1] + "z",
            "z" * 64,
            " " + SHA256_ABC,
            SHA256_ABC + " ",
            SHA256_ABC + "\n",
            SHA256_ABC.replace("a", "a "),
        )
        for candidate in malformed:
            with self.subTest(candidate=candidate):
                self.assertFalse(verify_sha256(b"abc", candidate))

    def test_wrong_type_is_rejected(self):
        """Non-bytes payloads and non-string digests are rejected."""
        for payload in ("abc", bytearray(b"abc"), memoryview(b"abc"), None, 42, ["a"]):
            with self.subTest(payload=type(payload).__name__):
                self.assertFalse(verify_sha256(payload, SHA256_ABC))

        for digest in (None, 42, SHA256_ABC.encode("ascii"), bytearray(SHA256_ABC.encode("ascii"))):
            with self.subTest(digest=type(digest).__name__):
                self.assertFalse(verify_sha256(b"abc", digest))

    def test_bool_or_numeric_digest_is_rejected(self):
        """Booleans and numbers are never accepted as digests."""
        for digest in (True, False, 0, 1, 64, 255, -1, 3.5):
            with self.subTest(digest=repr(digest)):
                self.assertFalse(verify_sha256(b"abc", digest))

    def test_return_value_is_boolean(self):
        """The helper always returns a bool, never a truthy proxy."""
        self.assertIs(verify_sha256(b"abc", SHA256_ABC), True)
        self.assertIs(verify_sha256(b"abc", SHA256_ABC.upper()), False)
        self.assertIs(verify_sha256("abc", SHA256_ABC), False)

    def test_binary_payload_with_nul_bytes(self):
        """Arbitrary binary payloads hash and compare correctly."""
        payload = bytes(range(256))
        digest = hashlib.sha256(payload).hexdigest()

        self.assertTrue(verify_sha256(payload, digest))
        self.assertFalse(verify_sha256(payload, SHA256_ABC))


if __name__ == "__main__":
    unittest.main(verbosity=2)
