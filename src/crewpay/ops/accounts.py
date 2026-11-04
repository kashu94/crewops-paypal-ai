"""Team accounts: who can sign in, with which role, and password resets.

An admin looks after the team's accounts: adds people, changes their role, switches an
account off, and resets a forgotten password. A reset gives a temporary password, shown once,
which the person must replace the next time they sign in. Admin accounts themselves, and
making anyone an admin, are for a super admin only, so an admin cannot raise their own
standing or lock out the people above them. Nobody changes or switches off their own account.

Accounts live in the store, seeded with the demo accounts.
"""

from __future__ import annotations

import secrets
import string
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from crewpay.agent.tools import Role

ADMIN_ROLES = frozenset({Role.ADMIN, Role.SUPER_ADMIN})


@dataclass(frozen=True, slots=True)
class DemoAccount:
    user: str
    role: Role
    crew_id: str | None
    label: str


DEMO_ACCOUNTS: tuple[DemoAccount, ...] = (
    DemoAccount("ananya.iyer", Role.CONTROLLER, None, "Ananya Iyer, desk 1"),
    DemoAccount("rohit.malhotra", Role.CONTROLLER, None, "Rohit Malhotra, desk 2"),
    DemoAccount("divya.rao", Role.CONTROLLER, None, "Divya Rao, desk 3"),
    DemoAccount("meera.bhatt", Role.APPROVER, None, "Meera Bhatt"),
    DemoAccount("karan.mehta", Role.APPROVER, None, "Karan Mehta"),
    DemoAccount("farah.qureshi", Role.APPROVER, None, "Farah Qureshi"),
    DemoAccount("imran.sheikh", Role.REPORTER, None, "Imran Sheikh, HYD station duty manager"),
    DemoAccount("lakshmi.hegde", Role.REPORTER, None, "Lakshmi Hegde, HYD station duty manager"),
    DemoAccount("nikhil.agarwal", Role.AUDITOR, None, "Nikhil Agarwal"),
    DemoAccount("priya.desai", Role.ADMIN, None, "Priya Desai"),
    DemoAccount("vikram.saxena", Role.SUPER_ADMIN, None, "Vikram Saxena"),
    DemoAccount("ops.speaker", Role.SPEAKER, None, "Ops-room speaker"),
    DemoAccount("c1111.nair", Role.CREW, "C-1111", "A. Nair, captain"),
    DemoAccount("c1157.reddy", Role.CREW, "C-1157", "D. Reddy, captain"),
    DemoAccount("c1167.iyer", Role.CREW, "C-1167", "R. Iyer, captain"),
    DemoAccount("c1191.nair", Role.CREW, "C-1191", "S. Nair, first officer"),
    DemoAccount("c1460.krishnan", Role.CREW, "C-1460", "S. Krishnan, cabin crew"),
    # Who the seats on T-1018 are offered to, so every callout can be answered by its crew.
    DemoAccount("c1123.verma", Role.CREW, "C-1123", "A. Verma, captain"),
    DemoAccount("c1177.rao", Role.CREW, "C-1177", "S. Rao, captain"),
    DemoAccount("c1211.bose", Role.CREW, "C-1211", "L. Bose, first officer"),
    DemoAccount("c1242.iyer", Role.CREW, "C-1242", "D. Iyer, first officer"),
    DemoAccount("c1184.chandra", Role.CREW, "C-1184", "K. Chandra, first officer"),
    DemoAccount("c1247.reddy", Role.CREW, "C-1247", "I. Reddy, first officer"),
    DemoAccount("c1341.chandra", Role.CREW, "C-1341", "H. Chandra, purser"),
    DemoAccount("c1357.gupta", Role.CREW, "C-1357", "A. Gupta, purser"),
    DemoAccount("c1530.malhotra", Role.CREW, "C-1530", "K. Malhotra, cabin crew"),
    DemoAccount("c1542.krishnan", Role.CREW, "C-1542", "A. Krishnan, cabin crew"),
)


class AccountError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Account:
    username: str
    role: str
    crew_id: str | None
    enabled: bool
    status: str
    """``active``, or ``must change password`` after a reset or a new account."""
    created: str


def temporary_password() -> str:
    """Twelve characters with every class the pool's policy asks for."""
    pools = (string.ascii_uppercase, string.ascii_lowercase, string.digits, "!#%+-=?@")
    chars = [secrets.choice(p) for p in pools]
    chars += [secrets.choice("".join(pools)) for _ in range(8)]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def check_may_manage(by_role: Role, by: str, username: str, role: Role | None, current: str | None):
    """Raise unless ``by`` may give ``username`` the ``role`` (or change them at all)."""
    if username == by:
        raise AccountError("you cannot change your own account")
    if by_role is not Role.SUPER_ADMIN:
        if role in ADMIN_ROLES:
            raise AccountError("only a super admin can make someone an admin")
        if current in {str(r) for r in ADMIN_ROLES}:
            raise AccountError("only a super admin can change an admin's account")


class Accounts(Protocol):
    def list(self) -> list[Account]: ...
    def create(self, username: str, role: Role, crew_id: str | None, email: str | None) -> str: ...
    def set_role(self, username: str, role: Role) -> None: ...
    def set_enabled(self, username: str, enabled: bool) -> None: ...
    def reset_password(self, username: str) -> str: ...


class StoreAccounts:
    """Accounts kept as documents, for a laptop and for tests."""

    def __init__(self, docs: Any) -> None:
        self.docs = docs
        if not docs.docs("account"):
            now = datetime.now(UTC).isoformat()
            for a in DEMO_ACCOUNTS:
                self._put(Account(a.user, str(a.role), a.crew_id, True, "active", now))

    def _put(self, account: Account) -> None:
        self.docs.put_doc("account", account.username, asdict(account))

    def _get(self, username: str) -> Account:
        found = self.docs.get_doc("account", username)
        if found is None:
            raise AccountError(f"no account {username}")
        return Account(**found)

    def list(self) -> list[Account]:
        return sorted((Account(**d) for d in self.docs.docs("account")), key=lambda a: a.username)

    def create(self, username: str, role: Role, crew_id: str | None, email: str | None) -> str:
        if self.docs.get_doc("account", username) is not None:
            raise AccountError(f"{username} already exists")
        now = datetime.now(UTC).isoformat()
        self._put(Account(username, str(role), crew_id, True, "must change password", now))
        return temporary_password()

    def set_role(self, username: str, role: Role) -> None:
        a = self._get(username)
        self._put(Account(a.username, str(role), a.crew_id, a.enabled, a.status, a.created))

    def set_enabled(self, username: str, enabled: bool) -> None:
        a = self._get(username)
        self._put(Account(a.username, a.role, a.crew_id, enabled, a.status, a.created))

    def reset_password(self, username: str) -> str:
        a = self._get(username)
        self._put(
            Account(a.username, a.role, a.crew_id, a.enabled, "must change password", a.created)
        )
        return temporary_password()


def current_role(accounts: Accounts, username: str) -> str | None:
    return next((a.role for a in accounts.list() if a.username == username), None)
