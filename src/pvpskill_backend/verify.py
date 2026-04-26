"""In-memory verification-code store.

The PvpSkill Minecraft mod POSTs to ``/verify/start`` and gets a short code.
The user pastes the code in the Discord verify channel, the bot POSTs to
``/verify/redeem``, and we establish the MC-UUID ↔ Discord-user link.

Codes expire after ``Settings.verify_code_ttl_seconds`` (default 10 min).
"""

from __future__ import annotations

import secrets
import string
import time
from dataclasses import dataclass


ALPHABET = string.ascii_uppercase + string.digits
# Avoid visually ambiguous characters so users don't mistype them.
AMBIGUOUS = set("0O1IL")
SAFE_ALPHABET = "".join(c for c in ALPHABET if c not in AMBIGUOUS)


@dataclass(slots=True)
class PendingCode:
    code: str
    mc_uuid: str
    mc_name: str
    created_at: float
    expires_at: float


class VerifyStore:
    def __init__(self, ttl_seconds: int, code_length: int) -> None:
        self.ttl = ttl_seconds
        self.length = code_length
        # One active code per MC-UUID — newest wins.
        self._by_uuid: dict[str, PendingCode] = {}
        self._by_code: dict[str, PendingCode] = {}

    def start(self, mc_uuid: str, mc_name: str) -> PendingCode:
        self._purge()
        old = self._by_uuid.pop(mc_uuid, None)
        if old is not None:
            self._by_code.pop(old.code, None)
        code = "".join(secrets.choice(SAFE_ALPHABET) for _ in range(self.length))
        now = time.time()
        pc = PendingCode(
            code=code,
            mc_uuid=mc_uuid,
            mc_name=mc_name,
            created_at=now,
            expires_at=now + self.ttl,
        )
        self._by_uuid[mc_uuid] = pc
        self._by_code[code] = pc
        return pc

    def redeem(self, code: str) -> PendingCode | None:
        self._purge()
        pc = self._by_code.pop(code.upper().strip(), None)
        if pc is None:
            return None
        self._by_uuid.pop(pc.mc_uuid, None)
        return pc

    def peek(self, code: str) -> PendingCode | None:
        self._purge()
        return self._by_code.get(code.upper().strip())

    def _purge(self) -> None:
        now = time.time()
        expired = [c for c, pc in self._by_code.items() if pc.expires_at < now]
        for c in expired:
            pc = self._by_code.pop(c)
            self._by_uuid.pop(pc.mc_uuid, None)
