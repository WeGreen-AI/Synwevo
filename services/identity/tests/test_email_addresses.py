"""Address syntax and normalisation shared by every mail-bearing route (registration, invitation,
sign-in code, address change, recovery, external binding).

Written after a real CUHK registration on production: the address passed, so the syntax layer was
not the blocker — but the same session showed the validator crashing (HeaderParseError) on a
trailing-dot domain, which turned a malformed address into a 500. Institutional forms are pinned
here so a future "tighter" rule cannot quietly refuse them.
"""
from __future__ import annotations

import pytest

from synwevo_core.identity import normalise_email, valid_email

ACCEPTED = [
    "1155123456@link.cuhk.edu.hk",        # CUHK student: numeric local part, four-label domain
    "alias@cuhk.edu.hk",                  # CUHK staff alias
    "Wong.Ka.Man@link.cuhk.edu.hk",       # student alias with dots, mixed case
    "s1234567@stu.example-uni.edu.cn",    # hyphenated institutional label
    "first.last+tag@mail.example.co.uk",  # plus tag, multi-level ccTLD
    "user@sub.dept.university.ac.jp",
    "ceo@company.io",
    "a@b.co",
    "x_y-z@example.com",
    "1155123456@LINK.CUHK.EDU.HK",
]
REFUSED = [
    "1155123456@link.cuhk.edu.hk.",   # trailing dot: used to raise instead of returning False
    "user@localhost",                 # no dot in domain
    "user@-example.com",              # label starting with a hyphen
    "user@example-.com",
    "user@example..com",              # empty label
    "user example@example.com",
    "user@@example.com",
    "@example.com",
    "user@",
    "us\ter@example.com",           # embedded control character
    "",
]


@pytest.mark.parametrize("address", ACCEPTED)
def test_institutional_and_ordinary_addresses_are_accepted(address):
    assert valid_email(address) is True


@pytest.mark.parametrize("address", REFUSED)
def test_malformed_addresses_are_refused_without_raising(address):
    assert valid_email(address) is False


def test_surrounding_whitespace_is_trimmed_not_refused():
    # pasted addresses often carry a trailing space or newline; the stored value is the trimmed one
    assert valid_email(" 1155123456@link.cuhk.edu.hk\n") is True
    assert normalise_email(" 1155123456@link.cuhk.edu.hk\n") == "1155123456@link.cuhk.edu.hk"


def test_normalisation_is_casefold_only():
    assert normalise_email("  Wong.Ka.Man@LINK.CUHK.edu.hk ") == "wong.ka.man@link.cuhk.edu.hk"
    # provider-specific rules are deliberately not applied: these are different addresses elsewhere
    assert normalise_email("a.b@example.com") != normalise_email("ab@example.com")
    assert normalise_email("a+tag@example.com") != normalise_email("a@example.com")
    assert normalise_email("x@link.cuhk.edu.hk") != normalise_email("x@cuhk.edu.hk")
