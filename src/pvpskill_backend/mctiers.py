"""Thin async client for the mctiers.com v2 API.

Docs: https://mctiers.com/docs/v2  (served as a Scalar OpenAPI UI)

Endpoints we use:
- GET /mode/list                              → { "<slug>": { "title": str, ... } }
- GET /mode/overall?count=N&from=M            → List[Profile]
- GET /mode/{gamemode}?count=N&from=M         → Dict[tier_num, List[Profile]]
- GET /profile/{uuid}                         → Profile
- GET /profile/by-name/{name}                 → Profile
- GET /profile/by-discord/{discord_id}        → Profile
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx


# Combat title thresholds (from the mctiers.com frontend).
TITLES: list[tuple[int, str]] = [
    (400, "Combat Grandmaster"),
    (250, "Combat Master"),
    (100, "Combat Ace"),
    (50, "Combat Specialist"),
    (20, "Combat Cadet"),
    (10, "Combat Novice"),
]


def title_for_points(points: int) -> str:
    for threshold, title in TITLES:
        if points >= threshold:
            return title
    return "Unranked"


@dataclass(slots=True)
class CacheEntry:
    value: Any
    expires_at: float


class MctiersClient:
    def __init__(self, base_url: str, cache_ttl_seconds: int = 120) -> None:
        self.base_url = base_url.rstrip("/")
        self.cache_ttl = cache_ttl_seconds
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=5.0),
            headers={
                "User-Agent": "PvpSkill-Backend/1.0 (+https://github.com/ItsN4x/PvpSkill)",
                "Accept": "application/json",
            },
        )
        self._cache: dict[str, CacheEntry] = {}

    async def aclose(self) -> None:
        await self._client.aclose()

    def _get_cached(self, key: str) -> Any | None:
        entry = self._cache.get(key)
        if entry is None:
            return None
        if entry.expires_at < time.time():
            self._cache.pop(key, None)
            return None
        return entry.value

    def _put_cache(self, key: str, value: Any) -> None:
        self._cache[key] = CacheEntry(value=value, expires_at=time.time() + self.cache_ttl)

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any | None:
        key = f"{path}?{params or ''}"
        cached = self._get_cached(key)
        if cached is not None:
            return cached
        try:
            resp = await self._client.get(f"{self.base_url}{path}", params=params)
        except httpx.HTTPError:
            return None
        if resp.status_code == 404:
            self._put_cache(key, None)
            return None
        if resp.status_code // 100 != 2:
            return None
        data = resp.json()
        self._put_cache(key, data)
        return data

    # --- Profile ------------------------------------------------------------

    async def profile_by_name(self, name: str) -> dict | None:
        return await self._get(f"/profile/by-name/{name}")

    async def profile_by_uuid(self, uuid: str) -> dict | None:
        return await self._get(f"/profile/{uuid}")

    async def profile_by_discord(self, discord_id: str) -> dict | None:
        return await self._get(f"/profile/by-discord/{discord_id}")

    # --- Rankings -----------------------------------------------------------

    async def overall(self, count: int = 10, offset: int = 0) -> list[dict]:
        data = await self._get("/mode/overall", {"count": count, "from": offset})
        return data or []

    async def gamemode(self, gamemode: str, count: int = 10, offset: int = 0) -> dict:
        data = await self._get(f"/mode/{gamemode}", {"count": count, "from": offset})
        return data or {}

    async def modes(self) -> dict[str, dict]:
        data = await self._get("/mode/list")
        return data or {}

    # --- Rank lookup --------------------------------------------------------

    async def rank_for_uuid(self, uuid: str, max_pages: int = 200) -> int | None:
        """Binary-ish scan of /mode/overall to find a player's overall rank.

        Returns the 1-indexed position, or None if the player isn't ranked
        within the first ``max_pages * 50`` players.
        """
        per_page = 50
        uuid_norm = uuid.lower()
        for page in range(max_pages):
            batch = await self.overall(count=per_page, offset=page * per_page)
            if not batch:
                return None
            for i, entry in enumerate(batch):
                if (entry.get("uuid") or "").lower() == uuid_norm:
                    return page * per_page + i + 1
            if len(batch) < per_page:
                return None
        return None
