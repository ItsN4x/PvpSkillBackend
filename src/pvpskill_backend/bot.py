"""Discord bot — runs in the same process as the FastAPI backend."""

from __future__ import annotations

import logging
import time

import discord
from discord import app_commands

from .config import settings
from .db import Database
from .mctiers import MctiersClient, title_for_points
from .verify import VerifyStore


log = logging.getLogger("pvpskill.bot")

# Brand colour — matches HyZodiacAPI Branding (BlueViolet).
BRAND = 0x8A2BE2


# --- Embed builders --------------------------------------------------------


def profile_embed(p: dict) -> discord.Embed:
    name = p.get("name", "Unknown")
    points = int(p.get("points", 0) or 0)
    title = title_for_points(points)
    overall = p.get("overall")
    region = p.get("region") or "—"

    e = discord.Embed(
        title=f"{name} — {title}",
        description=(
            f"**Points:** {points}\n"
            f"**Region:** {region}\n"
            f"**Overall rank:** {'#' + str(overall) if overall else '—'}"
        ),
        colour=BRAND,
    )
    rankings: dict = p.get("rankings") or {}
    if rankings:
        lines: list[str] = []
        for slug, r in sorted(rankings.items()):
            tier = r.get("tier")
            pos = r.get("pos", 0)
            retired = r.get("retired", False)
            label = f"{'LT' if pos else 'HT'}{tier}"
            marker = " (retired)" if retired else ""
            lines.append(f"`{slug:<7}` → **{label}**{marker}")
        e.add_field(name="Tiers", value="\n".join(lines), inline=False)
    else:
        e.add_field(name="Tiers", value="*No ranked tiers yet.*", inline=False)

    e.set_thumbnail(url=f"https://mc-heads.net/avatar/{p.get('uuid', name)}/128")
    e.set_footer(text="mctiers.com · PvpSkill")
    return e


def error_embed(title: str, description: str) -> discord.Embed:
    return discord.Embed(title=title, description=description, colour=0xE11D48)


def ok_embed(title: str, description: str) -> discord.Embed:
    return discord.Embed(title=title, description=description, colour=BRAND)


# --- Bot -------------------------------------------------------------------


class PvpSkillBot(discord.Client):
    def __init__(self, db: Database, mctiers: MctiersClient, verify: VerifyStore) -> None:
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.db = db
        self.mctiers = mctiers
        self.verify = verify
        self._register_commands()

    async def setup_hook(self) -> None:
        try:
            if settings.discord_guild_id:
                guild = discord.Object(id=settings.discord_guild_id)
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
                log.info("synced commands to guild %s", settings.discord_guild_id)
            else:
                await self.tree.sync()
                log.info("synced commands globally")
        except discord.HTTPException as exc:
            log.warning(
                "command sync failed (%s) — bot likely not in target guild yet; "
                "commands will sync once it joins",
                exc,
            )

    async def on_ready(self) -> None:
        log.info("logged in as %s (id=%s)", self.user, getattr(self.user, "id", "?"))
        log.info("currently in %d guilds", len(self.guilds))
        for g in self.guilds:
            log.info("  guild: %r id=%s", g.name, g.id)

    async def on_guild_join(self, guild: discord.Guild) -> None:
        log.info("joined guild %r id=%s — re-syncing commands", guild.name, guild.id)
        try:
            self.tree.copy_global_to(guild=discord.Object(id=guild.id))
            await self.tree.sync(guild=discord.Object(id=guild.id))
        except discord.HTTPException as exc:
            log.warning("guild sync failed: %s", exc)

    # ------------------------------------------------------------------
    # Command registration
    # ------------------------------------------------------------------

    def _register_commands(self) -> None:
        tree = self.tree

        # ---- /tier -----------------------------------------------------

        @tree.command(name="tier", description="Look up a player's tier list (mctiers.com).")
        @app_commands.describe(player="Minecraft username", gamemode="Specific gamemode (optional)")
        async def tier_cmd(
            interaction: discord.Interaction,
            player: str,
            gamemode: str | None = None,
        ) -> None:
            await interaction.response.defer(thinking=True)
            profile = await self.mctiers.profile_by_name(player)
            if profile is None:
                await interaction.followup.send(
                    embed=error_embed(
                        "Player not found",
                        f"`{player}` has no mctiers.com profile.\n"
                        f"Double-check the spelling — names are exact (e.g. `Marlowww` not `Marloww`).",
                    )
                )
                return
            if gamemode:
                r = (profile.get("rankings") or {}).get(gamemode.lower())
                if r is None:
                    await interaction.followup.send(
                        embed=error_embed(
                            f"{profile['name']} has no {gamemode} tier",
                            f"They are ranked in: `{', '.join((profile.get('rankings') or {}).keys()) or '—'}`.",
                        )
                    )
                    return
                tier = r.get("tier")
                pos = r.get("pos", 0)
                await interaction.followup.send(
                    embed=ok_embed(
                        f"{profile['name']} — {gamemode}",
                        f"**{'LT' if pos else 'HT'}{tier}**"
                        + (" (retired)" if r.get("retired") else ""),
                    )
                )
                return
            await interaction.followup.send(embed=profile_embed(profile))

        # ---- /profile --------------------------------------------------

        @tree.command(name="profile", description="Full mctiers.com profile for a player.")
        @app_commands.describe(player="Minecraft username")
        async def profile_cmd(interaction: discord.Interaction, player: str) -> None:
            await interaction.response.defer(thinking=True)
            profile = await self.mctiers.profile_by_name(player)
            if profile is None:
                await interaction.followup.send(
                    embed=error_embed("Player not found", f"No mctiers.com profile for `{player}`.")
                )
                return
            await interaction.followup.send(embed=profile_embed(profile))

        # ---- /rank -----------------------------------------------------

        @tree.command(name="rank", description="Overall leaderboard position (#1 … #N).")
        @app_commands.describe(player="Minecraft username")
        async def rank_cmd(interaction: discord.Interaction, player: str) -> None:
            await interaction.response.defer(thinking=True)
            profile = await self.mctiers.profile_by_name(player)
            if profile is None:
                await interaction.followup.send(
                    embed=error_embed("Player not found", f"No mctiers.com profile for `{player}`.")
                )
                return
            overall = profile.get("overall")
            if overall is None:
                overall = await self.mctiers.rank_for_uuid(profile["uuid"])
            if overall is None:
                await interaction.followup.send(
                    embed=error_embed(
                        f"{profile['name']} is unranked",
                        "Player has no overall leaderboard position.",
                    )
                )
                return
            await interaction.followup.send(
                embed=ok_embed(
                    f"{profile['name']} — #{overall}",
                    f"Points: **{profile.get('points', 0)}** · Title: **{title_for_points(profile.get('points', 0))}**",
                )
            )

        # ---- /title ----------------------------------------------------

        @tree.command(name="title", description="Show a player's combat title (Grandmaster/Master/…).")
        @app_commands.describe(player="Minecraft username")
        async def title_cmd(interaction: discord.Interaction, player: str) -> None:
            await interaction.response.defer(thinking=True)
            profile = await self.mctiers.profile_by_name(player)
            if profile is None:
                await interaction.followup.send(
                    embed=error_embed("Player not found", f"No mctiers.com profile for `{player}`.")
                )
                return
            pts = int(profile.get("points", 0) or 0)
            await interaction.followup.send(
                embed=ok_embed(
                    f"{profile['name']} — {title_for_points(pts)}",
                    f"Points: **{pts}**",
                )
            )

        # ---- /region ---------------------------------------------------

        @tree.command(name="region", description="Player's region (EU / NA / AS / …).")
        @app_commands.describe(player="Minecraft username")
        async def region_cmd(interaction: discord.Interaction, player: str) -> None:
            await interaction.response.defer(thinking=True)
            profile = await self.mctiers.profile_by_name(player)
            if profile is None:
                await interaction.followup.send(
                    embed=error_embed("Player not found", f"No mctiers.com profile for `{player}`.")
                )
                return
            await interaction.followup.send(
                embed=ok_embed(
                    profile["name"],
                    f"Region: **{profile.get('region') or '—'}**",
                )
            )

        # ---- /top ------------------------------------------------------

        @tree.command(name="top", description="Top players on mctiers.com.")
        @app_commands.describe(
            gamemode="overall / sword / axe / uhc / smp / pot / nethop / vanilla / mace",
            count="How many players to show (1–25)",
        )
        async def top_cmd(
            interaction: discord.Interaction,
            gamemode: str = "overall",
            count: int = 10,
        ) -> None:
            await interaction.response.defer(thinking=True)
            count = max(1, min(25, count))
            gm = gamemode.lower()
            if gm == "overall":
                data = await self.mctiers.overall(count=count, offset=0)
                if not data:
                    await interaction.followup.send(embed=error_embed("No data", "Rankings empty."))
                    return
                lines = [
                    f"`#{i+1:<3}` **{p['name']}** — {p.get('points', 0)} pts · "
                    f"{title_for_points(p.get('points', 0))}"
                    for i, p in enumerate(data)
                ]
                await interaction.followup.send(
                    embed=ok_embed(f"Top {count} — Overall", "\n".join(lines))
                )
                return
            by_tier = await self.mctiers.gamemode(gm, count=count, offset=0)
            if not by_tier:
                await interaction.followup.send(
                    embed=error_embed(
                        "Unknown gamemode",
                        f"`{gamemode}` isn't a known gamemode.",
                    )
                )
                return
            lines: list[str] = []
            seen = 0
            for tier_num in sorted(by_tier.keys(), key=lambda x: int(x)):
                for p in by_tier[tier_num]:
                    if seen >= count:
                        break
                    seen += 1
                    lines.append(
                        f"`HT{tier_num}` — **{p['name']}** · {p.get('region') or '—'}"
                    )
                if seen >= count:
                    break
            await interaction.followup.send(
                embed=ok_embed(f"Top {seen} — {gm}", "\n".join(lines) or "*No entries.*")
            )

        # ---- /compare --------------------------------------------------

        @tree.command(name="compare", description="Side-by-side tier comparison of two players.")
        async def compare_cmd(
            interaction: discord.Interaction, player1: str, player2: str
        ) -> None:
            await interaction.response.defer(thinking=True)
            p1 = await self.mctiers.profile_by_name(player1)
            p2 = await self.mctiers.profile_by_name(player2)
            if not p1 or not p2:
                missing = [n for n, p in [(player1, p1), (player2, p2)] if p is None]
                await interaction.followup.send(
                    embed=error_embed("Player not found", f"Missing: {', '.join(missing)}")
                )
                return
            r1 = p1.get("rankings") or {}
            r2 = p2.get("rankings") or {}
            all_modes = sorted(set(r1.keys()) | set(r2.keys()))
            lines = [
                f"`{slug:<8}` {('HT' + str(r1[slug]['tier'])) if slug in r1 else '—':<5}"
                f"  vs  {('HT' + str(r2[slug]['tier'])) if slug in r2 else '—'}"
                for slug in all_modes
            ]
            await interaction.followup.send(
                embed=ok_embed(
                    f"{p1['name']} vs {p2['name']}",
                    f"Points: **{p1.get('points', 0)}** vs **{p2.get('points', 0)}**\n"
                    + "\n".join(lines),
                )
            )

        # ---- /verify ---------------------------------------------------

        @tree.command(
            name="verify",
            description="Link your Minecraft account using the code from /pvpskill verify in-game.",
        )
        @app_commands.describe(code="The code shown in-game after running /pvpskill verify")
        async def verify_cmd(interaction: discord.Interaction, code: str) -> None:
            if (
                settings.discord_verify_channel_id
                and interaction.channel_id != settings.discord_verify_channel_id
            ):
                await interaction.response.send_message(
                    embed=error_embed(
                        "Wrong channel",
                        f"Use this command in <#{settings.discord_verify_channel_id}>.",
                    ),
                    ephemeral=True,
                )
                return
            pc = self.verify.redeem(code)
            if pc is None:
                await interaction.response.send_message(
                    embed=error_embed(
                        "Invalid or expired code",
                        "Run `/pvpskill verify` in-game again to get a fresh code.",
                    ),
                    ephemeral=True,
                )
                return
            await self.db.link(
                mc_uuid=pc.mc_uuid,
                mc_name=pc.mc_name,
                discord_id=str(interaction.user.id),
                at=int(time.time()),
            )
            await interaction.response.send_message(
                embed=ok_embed(
                    "Verified",
                    f"<@{interaction.user.id}> linked to **{pc.mc_name}**.",
                )
            )

        # ---- /unlink ---------------------------------------------------

        @tree.command(name="unlink", description="Unlink your Minecraft account.")
        async def unlink_cmd(interaction: discord.Interaction) -> None:
            ok = await self.db.unlink_by_discord(str(interaction.user.id), int(time.time()))
            if not ok:
                await interaction.response.send_message(
                    embed=error_embed("Not linked", "You don't have a linked Minecraft account."),
                    ephemeral=True,
                )
                return
            await interaction.response.send_message(
                embed=ok_embed("Unlinked", "Your Minecraft account is no longer linked."),
                ephemeral=True,
            )

        # ---- /whois ----------------------------------------------------

        @tree.command(name="whois", description="Show which Minecraft account a Discord user is linked to.")
        async def whois_cmd(interaction: discord.Interaction, user: discord.User) -> None:
            row = await self.db.link_by_discord(str(user.id))
            if row is None:
                await interaction.response.send_message(
                    embed=error_embed(
                        "Not linked",
                        f"{user.mention} hasn't linked a Minecraft account.",
                    ),
                    ephemeral=True,
                )
                return
            await interaction.response.send_message(
                embed=ok_embed(
                    "Linked account",
                    f"{user.mention} → **{row['mc_name']}** (`{row['mc_uuid']}`)",
                )
            )

        # ---- /stats ----------------------------------------------------

        @tree.command(name="stats", description="Bot stats.")
        async def stats_cmd(interaction: discord.Interaction) -> None:
            s = await self.db.stats()
            await interaction.response.send_message(
                embed=ok_embed(
                    "PvpSkill Bot — stats",
                    f"**Linked accounts:** {s.get('linked', 0)}",
                )
            )

        # ---- /help -----------------------------------------------------

        @tree.command(name="help", description="List all bot commands.")
        async def help_cmd(interaction: discord.Interaction) -> None:
            cmds = sorted(tree.get_commands(), key=lambda c: c.name)
            lines = [f"`/{c.name}` — {c.description}" for c in cmds]
            await interaction.response.send_message(
                embed=ok_embed("PvpSkill Bot — commands", "\n".join(lines))
            )


def make_bot(db: Database, mctiers: MctiersClient, verify: VerifyStore) -> PvpSkillBot:
    return PvpSkillBot(db=db, mctiers=mctiers, verify=verify)
