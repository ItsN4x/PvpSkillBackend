"""FastAPI application exposed to the PvpSkill Minecraft mod."""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import Annotated, AsyncIterator

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from . import __version__
from .config import settings
from .db import Database
from .mctiers import MctiersClient, title_for_points
from .verify import VerifyStore


# --- Request / response models ---------------------------------------------


class VerifyStartRequest(BaseModel):
    mc_uuid: str = Field(min_length=32, max_length=36)
    mc_name: str = Field(min_length=1, max_length=64)


class VerifyStartResponse(BaseModel):
    code: str
    expires_in: int
    channel_id: str
    guild_id: str
    invite_url: str


class LinkStatusResponse(BaseModel):
    linked: bool
    discord_id: str | None = None
    linked_at: int | None = None


class UnlinkRequest(BaseModel):
    mc_uuid: str


class PlayerSummary(BaseModel):
    uuid: str
    name: str
    region: str | None = None
    points: int = 0
    overall: int | None = None
    title: str
    rankings: dict[str, dict] = Field(default_factory=dict)
    discord_id: str | None = None


def player_summary(profile: dict) -> PlayerSummary:
    points = int(profile.get("points", 0) or 0)
    return PlayerSummary(
        uuid=profile.get("uuid", ""),
        name=profile.get("name", ""),
        region=profile.get("region"),
        points=points,
        overall=profile.get("overall"),
        title=title_for_points(points),
        rankings=profile.get("rankings", {}) or {},
        discord_id=profile.get("discord_id"),
    )


# --- Dependencies ----------------------------------------------------------


def get_db(app: FastAPI) -> Database:
    return app.state.db  # type: ignore[no-any-return]


def get_mctiers(app: FastAPI) -> MctiersClient:
    return app.state.mctiers  # type: ignore[no-any-return]


def get_verify(app: FastAPI) -> VerifyStore:
    return app.state.verify  # type: ignore[no-any-return]


# --- App factory -----------------------------------------------------------


def create_app(
    db: Database,
    mctiers: MctiersClient,
    verify: VerifyStore,
    discord_ready: "callable[[], bool] | None" = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.db = db
        app.state.mctiers = mctiers
        app.state.verify = verify
        yield

    app = FastAPI(
        title="PvpSkill Backend",
        version=__version__,
        description="Backend for the PvpSkill Minecraft mod: account linking + mctiers.com proxy.",
        lifespan=lifespan,
    )

    DbDep = Annotated[Database, Depends(lambda: db)]
    MctiersDep = Annotated[MctiersClient, Depends(lambda: mctiers)]
    VerifyDep = Annotated[VerifyStore, Depends(lambda: verify)]

    @app.get("/healthz", tags=["meta"])
    async def healthz() -> dict:
        return {
            "status": "ok",
            "version": __version__,
            "discord_ready": bool(discord_ready and discord_ready()),
        }

    @app.get("/stats", tags=["meta"])
    async def stats_endpoint(db: DbDep) -> dict:
        return await db.stats()

    # --- Verification flow (MC → backend → Discord) ------------------------

    @app.post("/verify/start", response_model=VerifyStartResponse, tags=["verify"])
    async def verify_start(body: VerifyStartRequest, verify: VerifyDep) -> VerifyStartResponse:
        pc = verify.start(body.mc_uuid.lower(), body.mc_name)
        return VerifyStartResponse(
            code=pc.code,
            expires_in=int(pc.expires_at - pc.created_at),
            channel_id=str(settings.discord_verify_channel_id),
            guild_id=str(settings.discord_guild_id),
            invite_url=settings.discord_invite_url,
        )

    @app.get("/link/by-uuid/{mc_uuid}", response_model=LinkStatusResponse, tags=["link"])
    async def link_by_uuid(mc_uuid: str, db: DbDep) -> LinkStatusResponse:
        row = await db.link_by_uuid(mc_uuid.lower())
        if row is None:
            return LinkStatusResponse(linked=False)
        return LinkStatusResponse(
            linked=True,
            discord_id=row["discord_id"],
            linked_at=row["linked_at"],
        )

    @app.post("/link/unlink", response_model=LinkStatusResponse, tags=["link"])
    async def unlink_endpoint(body: UnlinkRequest, db: DbDep) -> LinkStatusResponse:
        at = int(time.time())
        changed = await db.unlink_by_uuid(body.mc_uuid.lower(), at)
        return LinkStatusResponse(linked=not changed)

    # --- mctiers proxy (cached, used by the mod) ---------------------------

    @app.get("/mctiers/by-name/{name}", response_model=PlayerSummary, tags=["mctiers"])
    async def mctiers_by_name(name: str, api: MctiersDep) -> PlayerSummary:
        data = await api.profile_by_name(name)
        if data is None:
            raise HTTPException(status_code=404, detail=f"No mctiers profile for '{name}'")
        return player_summary(data)

    @app.get("/mctiers/by-uuid/{uuid}", response_model=PlayerSummary, tags=["mctiers"])
    async def mctiers_by_uuid(uuid: str, api: MctiersDep) -> PlayerSummary:
        data = await api.profile_by_uuid(uuid)
        if data is None:
            raise HTTPException(status_code=404, detail=f"No mctiers profile for '{uuid}'")
        return player_summary(data)

    @app.get("/mctiers/rank/{uuid}", tags=["mctiers"])
    async def mctiers_rank(uuid: str, api: MctiersDep) -> dict:
        rank = await api.rank_for_uuid(uuid)
        return {"uuid": uuid, "rank": rank}

    @app.get("/mctiers/top/{gamemode}", tags=["mctiers"])
    async def mctiers_top(gamemode: str, count: int = 10, api: MctiersDep = None) -> dict:  # type: ignore[assignment]
        count = max(1, min(50, count))
        if gamemode == "overall":
            data = await api.overall(count=count, offset=0)
            entries = [player_summary(p).model_dump() for p in data]
        else:
            data = await api.gamemode(gamemode, count=count, offset=0)
            entries = []
            for tier_num in sorted(data.keys(), key=lambda x: int(x)):
                for p in data[tier_num]:
                    p = {**p, "tier": int(tier_num)}
                    entries.append(p)
        return {"gamemode": gamemode, "entries": entries}

    @app.get("/mctiers/modes", tags=["mctiers"])
    async def mctiers_modes(api: MctiersDep) -> dict:
        return await api.modes()

    return app
