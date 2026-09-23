"""Password hashing and policy.

Parameters are measured on the deployment host, not copied from a README. On vultr_tokyo
(3 vCPU, shared with four other production stacks, 3 GiB container limit):

    19 MiB  t=2 p=1   27.6 ms median / 38.4 ms p95     ← OWASP's current floor
    39 MiB  t=2 p=1   77.6 ms
    46 MiB  t=2 p=1   96.4 ms / 100.8 ms p95           ← chosen
    64 MiB  t=3 p=4   84.2 ms                          ← argon2-cffi's default
    64 MiB  t=3 p=1  178.4 ms / 202.6 ms p95

The library default is rejected for **p=4**: four threads per hash on a 3-vCPU box that is also
running someone else's production would let a burst of logins contend with it. p=1 keeps a hash
on one core so concurrency is governed by the semaphore below rather than by the scheduler.

46 MiB at t=2 is 2.4x OWASP's memory floor and still finishes in about 100 ms, which keeps the
login interaction honest. Memory is the real budget: peak is CONCURRENCY x 46 MiB, so the
semaphore bounds it at ~184 MiB and password verification cannot starve document generation,
which is the memory-hungry part of this product. Raising memory_cost without raising the
semaphore would trade away throughput for a smaller gain than it looks.

Re-measure with scripts/bench_argon2.py when the host changes; PHC strings carry their own
parameters, so raising them later upgrades existing hashes on next successful login.
"""
from __future__ import annotations

import hmac
import re
import threading
import unicodedata

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from argon2.low_level import Type

# measured above; change together with the benchmark note
MEMORY_KIB = 47104          # 46 MiB
TIME_COST = 2
PARALLELISM = 1
CONCURRENCY = 4             # bounds peak memory at ~184 MiB and keeps hashing off the job threads

_hasher = PasswordHasher(time_cost=TIME_COST, memory_cost=MEMORY_KIB, parallelism=PARALLELISM,
                         hash_len=32, salt_len=16, type=Type.ID)
_slots = threading.BoundedSemaphore(CONCURRENCY)

# A single-factor password is the only thing standing between an attacker and the account, so it
# carries the longer floor. As one factor among several the requirement drops — but not below the
# 8 that NIST 800-63B sets as an absolute minimum.
MIN_LENGTH_SINGLE_FACTOR = 15
MIN_LENGTH_WITH_MFA = 8
# An upper bound only so a multi-megabyte body cannot turn a login into a memory attack. Argon2
# itself has no length limit and nothing is truncated below this.
MAX_LENGTH = 1024


class PasswordError(ValueError):
    """Rejected by policy. The message is shown to the person and says what to change."""


def normalise(password: str) -> str:
    """NFKC, and nothing else.

    NIST 800-63B asks for Unicode normalisation so that a password typed on a different keyboard
    or IME still matches — without it a CJK user can be locked out by an input method they did
    not choose. Spaces are *not* stripped and no character is removed: a password is whatever
    the person typed, normalised only into a canonical encoding of those same characters.
    """
    return unicodedata.normalize("NFKC", password)


# Checked locally. The plaintext a person types is never sent to an outside service, so this is a
# small embedded list plus terms specific to this deployment rather than a breach-corpus API.
_COMMON = {
    "password", "passw0rd", "123456", "12345678", "123456789", "1234567890", "qwerty", "abc123",
    "letmein", "welcome", "admin", "administrator", "iloveyou", "monkey", "dragon", "sunshine",
    "princess", "football", "baseball", "trustno1", "changeme", "default", "root", "toor",
    "111111", "000000", "1qaz2wsx", "qwertyuiop", "asdfghjkl", "zxcvbnm", "passw0rd!",
    "p@ssword", "p@ssw0rd", "secret", "master", "hello", "freedom", "whatever", "qazwsx",
    "correcthorsebatterystaple",          # famous enough to be in every wordlist
}
_SERVICE_TERMS = {"docweave", "wegreen", "dw.wegreen.ltd", "文档", "模板"}


def _squash(s: str) -> str:
    return re.sub(r"[^a-z0-9一-鿿]+", "", s.lower())


def check_policy(password: str, *, email: str | None = None, display_name: str | None = None,
                 single_factor: bool = True) -> None:
    """Raise PasswordError with an actionable message, or return.

    No composition rules — no "must contain an uppercase and a symbol". They push people toward
    Password1! and buy nothing. Length, plus refusing what is already in every wordlist or is
    derived from this service or this account, is what actually helps.
    """
    p = normalise(password)
    floor = MIN_LENGTH_SINGLE_FACTOR if single_factor else MIN_LENGTH_WITH_MFA
    if len(p) < floor:
        raise PasswordError(f"口令至少需要 {floor} 个字符（当前 {len(p)} 个）。"
                            f"长句子比复杂符号更好记也更安全。")
    if len(p) > MAX_LENGTH:
        raise PasswordError(f"口令过长（上限 {MAX_LENGTH} 个字符）。")

    squashed = _squash(p)
    # Appending digits to a wordlist entry is the single most common way to "meet" a length
    # requirement, so password123456789 has to fail for the same reason password does.
    stem = re.sub(r"\d+$", "", squashed)
    if squashed in _COMMON or p.lower() in _COMMON or (len(stem) >= 4 and stem in _COMMON):
        raise PasswordError("这个口令在常见泄露口令名单里，请换一个。")
    # a long password built only by repeating one short chunk is as weak as the chunk
    if len(set(p)) <= 4 and len(p) >= floor:
        raise PasswordError("这个口令重复度太高，请换一个。")
    for term in _SERVICE_TERMS:
        if term and _squash(term) and _squash(term) in squashed:
            raise PasswordError("口令不能包含本服务的名称，请换一个。")
    for value in (email or "", display_name or ""):
        local = _squash(value.split("@")[0] if "@" in value else value)
        if len(local) >= 4 and local in squashed:
            raise PasswordError("口令不能包含你的邮箱或姓名，请换一个。")


def hash_password(password: str) -> str:
    """A PHC string: algorithm, parameters and salt travel with the digest."""
    with _slots:
        return _hasher.hash(normalise(password))


def verify(phc: str, password: str) -> bool:
    """False for a wrong password, and False for anything else that goes wrong.

    Deliberately total: a mismatch, a corrupt stored hash and an unexpected library error must be
    indistinguishable to the caller, because any difference between them — including a different
    exception reaching a different error path — is a signal about the account.
    """
    with _slots:
        try:
            return _hasher.verify(phc, normalise(password))
        except (VerifyMismatchError, InvalidHashError):
            return False
        except Exception:  # noqa: BLE001 - see docstring: never distinguishable from a mismatch
            return False


def needs_rehash(phc: str) -> bool:
    """True when the stored hash predates the current parameters.

    Called after a *successful* verification, which is the only moment the plaintext is in hand,
    so parameters can be raised later without anyone exporting passwords.
    """
    try:
        return _hasher.check_needs_rehash(phc)
    except Exception:  # noqa: BLE001 - an unparsable hash is stale by definition
        return True


def constant_time_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def parameters() -> dict:
    """What is actually in force — for the ops docs and for `manage.py info`, so the deployed
    parameters can be read off the running service instead of trusted from a comment."""
    return {"algorithm": "argon2id", "memory_kib": MEMORY_KIB, "time_cost": TIME_COST,
            "parallelism": PARALLELISM, "hash_len": 32, "salt_len": 16, "concurrency": CONCURRENCY}
