#!/usr/bin/env python3
"""Fail-closed comparison of receipt fields to an expected run identity.

This pure predicate compares run ID, run attempt and commit SHA. It does NOT
authenticate the receipt, attest a GitHub Actions origin or prove artifact
provenance. A trusted consumer must separately verify original GitHub Actions
run/attempt/SHA and artifact contents. A locally fabricated receipt containing
matching fields will also return True.

Standard library only. No filesystem, network or subprocess access.
"""
import re
import unittest

# The complete, exact set of fields a receipt must carry.
RECEIPT_FIELDS = frozenset({"run_id", "run_attempt", "head_sha"})

# A Git commit SHA: exactly 40 characters, lowercase hexadecimal only.
SHA_RE = re.compile(r"[0-9a-f]{40}")


def _is_positive_int(value):
    """True only for a real integer greater than zero (never a bool)."""
    return type(value) is int and value > 0


def _is_commit_sha(value):
    """True only for a 40-character lowercase hexadecimal commit SHA string."""
    return isinstance(value, str) and SHA_RE.fullmatch(value) is not None


def matches_run_identity(receipt, run_id, run_attempt, head_sha):
    """Return True only if receipt fields match an expected run identity.

    This is field equality, not authentication or proof of GitHub origin.
    The caller must verify the trusted source and artifact independently.

    The receipt must be a mapping holding exactly the keys ``run_id``,
    ``run_attempt`` and ``head_sha``. Both counters must be integers greater
    than zero (``bool`` is not accepted as an integer) and the SHA must be a
    40-character lowercase hexadecimal string. The same rules apply to the
    expected values. Every other input -- including wrong types -- returns
    False rather than raising.
    """
    if not isinstance(receipt, dict):
        return False
    if set(receipt) != RECEIPT_FIELDS:
        return False
    if not _is_positive_int(receipt["run_id"]):
        return False
    if not _is_positive_int(receipt["run_attempt"]):
        return False
    if not _is_commit_sha(receipt["head_sha"]):
        return False
    if not _is_positive_int(run_id):
        return False
    if not _is_positive_int(run_attempt):
        return False
    if not _is_commit_sha(head_sha):
        return False
    return (receipt["run_id"] == run_id
            and receipt["run_attempt"] == run_attempt
            and receipt["head_sha"] == head_sha)


class MatchesRunIdentityTests(unittest.TestCase):
    RUN_ID = 1234567890
    ATTEMPT = 2
    SHA = "0123456789abcdef0123456789abcdef01234567"

    def receipt(self, **overrides):
        """A well-formed receipt, optionally with fields replaced."""
        base = {"run_id": self.RUN_ID, "run_attempt": self.ATTEMPT,
                "head_sha": self.SHA}
        base.update(overrides)
        return base

    def test_valid_receipt_matches_expected_identity(self):
        self.assertIs(matches_run_identity(
            self.receipt(), self.RUN_ID, self.ATTEMPT, self.SHA), True)

    def test_fabricated_matching_receipt_is_not_origin_proof(self):
        """Locally made matching fields pass; provenance needs a trusted source."""
        constructed = {"run_id": self.RUN_ID, "run_attempt": self.ATTEMPT,
                       "head_sha": self.SHA}
        self.assertTrue(matches_run_identity(
            constructed, self.RUN_ID, self.ATTEMPT, self.SHA))

    def test_missing_field_is_rejected(self):
        for field in ("run_id", "run_attempt", "head_sha"):
            with self.subTest(field=field):
                receipt = self.receipt()
                del receipt[field]
                self.assertIs(matches_run_identity(
                    receipt, self.RUN_ID, self.ATTEMPT, self.SHA), False)

    def test_extra_field_is_rejected(self):
        for extra in ("conclusion", "workflow_run_id", "repository", "note"):
            with self.subTest(extra=extra):
                receipt = self.receipt()
                receipt[extra] = "value"
                self.assertIs(matches_run_identity(
                    receipt, self.RUN_ID, self.ATTEMPT, self.SHA), False)

    def test_non_integer_run_id_is_rejected(self):
        for bad in (True, False, 1.0, 0.5, "1234567890", None, 0, -1):
            with self.subTest(run_id=bad):
                self.assertIs(matches_run_identity(
                    self.receipt(run_id=bad), self.RUN_ID, self.ATTEMPT,
                    self.SHA), False)

    def test_non_integer_attempt_is_rejected(self):
        for bad in (True, False, 2.0, 0.5, "2", None, 0, -1):
            with self.subTest(run_attempt=bad):
                self.assertIs(matches_run_identity(
                    self.receipt(run_attempt=bad), self.RUN_ID, self.ATTEMPT,
                    self.SHA), False)

    def test_malformed_head_sha_is_rejected(self):
        for bad in ("0123456789ABCDEF0123456789abcdef01234567",
                    "0123456789ABCDEF0123456789ABCDEF01234567",
                    self.SHA.upper(), self.SHA[:-1], self.SHA + "0",
                    self.SHA[:-1] + "z", "", None, 12345, b"a" * 40):
            with self.subTest(head_sha=bad):
                self.assertIs(matches_run_identity(
                    self.receipt(head_sha=bad), self.RUN_ID, self.ATTEMPT,
                    self.SHA), False)

    def test_mismatched_values_are_rejected(self):
        other_sha = "f" * 40
        self.assertIs(matches_run_identity(
            self.receipt(), self.RUN_ID + 1, self.ATTEMPT, self.SHA), False)
        self.assertIs(matches_run_identity(
            self.receipt(), self.RUN_ID, self.ATTEMPT + 1, self.SHA), False)
        self.assertIs(matches_run_identity(
            self.receipt(), self.RUN_ID, self.ATTEMPT, other_sha), False)
        self.assertIs(matches_run_identity(
            self.receipt(head_sha=other_sha), self.RUN_ID, self.ATTEMPT,
            self.SHA), False)
        self.assertIs(matches_run_identity(
            self.receipt(run_attempt=self.ATTEMPT + 1), self.RUN_ID,
            self.ATTEMPT, self.SHA), False)

    def test_receipt_must_be_a_mapping(self):
        for bad in (None, [], ["run_id"], (), "receipt", 42, 4.5, object()):
            with self.subTest(receipt=bad):
                self.assertIs(matches_run_identity(
                    bad, self.RUN_ID, self.ATTEMPT, self.SHA), False)

    def test_expected_arguments_must_be_well_formed(self):
        receipt = self.receipt()
        for bad in (True, 1.0, "1234567890", 0, -1):
            with self.subTest(run_id=bad):
                self.assertIs(matches_run_identity(
                    receipt, bad, self.ATTEMPT, self.SHA), False)
        for bad in (True, 2.0, "2", 0, -1):
            with self.subTest(run_attempt=bad):
                self.assertIs(matches_run_identity(
                    receipt, self.RUN_ID, bad, self.SHA), False)
        for bad in (self.SHA.upper(), self.SHA[:-1], "", None, 12345):
            with self.subTest(head_sha=bad):
                self.assertIs(matches_run_identity(
                    receipt, self.RUN_ID, self.ATTEMPT, bad), False)

    def test_result_is_always_a_boolean(self):
        self.assertIsInstance(matches_run_identity(
            self.receipt(), self.RUN_ID, self.ATTEMPT, self.SHA), bool)
        self.assertIsInstance(matches_run_identity(
            self.receipt(run_id="bad"), self.RUN_ID, self.ATTEMPT, self.SHA),
            bool)

    def test_receipt_is_not_modified(self):
        receipt = self.receipt()
        snapshot = dict(receipt)
        matches_run_identity(receipt, self.RUN_ID, self.ATTEMPT, self.SHA)
        matches_run_identity(receipt, self.RUN_ID, self.ATTEMPT, "f" * 40)
        self.assertEqual(receipt, snapshot)


if __name__ == "__main__":
    unittest.main()
