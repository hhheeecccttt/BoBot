import os
import json
import functools
import random
from datetime import datetime, timezone, timedelta

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

import config
import db

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
# Testing: set UNLIMITED_SPINS=1 to bypass the daily spin limit.
UNLIMITED_SPINS = os.getenv("UNLIMITED_SPINS", "0") == "1"

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


# ---------- roll logic ----------

def roll_rarity() -> str:
    r = random.random() * 100
    cumulative = 0.0
    for name, info in config.RARITIES.items():
        cumulative += info["chance"]
        if r < cumulative:
            return name
    return "Low"


def roll_quality() -> str:
    r = random.random() * 100
    cumulative = 0.0
    for name, info in config.QUALITIES.items():
        cumulative += info["chance"]
        if r < cumulative:
            return name
    return "Heavily Chipped"


def roll_one() -> tuple[str, str, str]:
    rarity = roll_rarity()
    quality = roll_quality()
    ore = random.choice(config.ORES[rarity])
    return rarity, quality, ore


def today_str() -> str:
    return datetime.now(config.RESET_TIMEZONE).date().isoformat()


def guild_scope(interaction) -> str:
    """Server id for data isolation (each server = independent economy). DMs use 'DM'."""
    g = getattr(interaction, "guild", None)
    return str(g.id) if g and getattr(g, "id", None) else "DM"


def SUID(interaction, raw_id=None) -> str:
    """Scoped user id: 'guild:user'. Pass raw user id for someone other than the caller."""
    uid = raw_id if raw_id is not None else interaction.user.id
    return f"{guild_scope(interaction)}:{uid}"


async def _spin_ping(uid: str, interaction: discord.Interaction) -> str:
    """Mention + unread-mail nudge (stops once mail is checked)."""
    text = interaction.user.mention
    try:
        n = db.unread_mail_count(uid)
    except Exception:
        n = 0
    if n > 0:
        text += f"\n📬 You have {n} unread mail! Check `/mail`."
    return text


def time_until_reset() -> str:
    now = datetime.now(config.RESET_TIMEZONE)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    delta = tomorrow - now
    h, rem = divmod(int(delta.total_seconds()), 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m}m"


def check_achievements(user_id: str, u: dict, rarity: str, quality: str,
                       collectors: bool = True, pulled: bool = False) -> list[str]:
    """Grant eligible achievements. Returns list of newly unlocked names.
    collectors=False skips the (expensive) collector scan - spin passes it once.
    pulled=True only for actual /spin pulls - Pull achievements never fire otherwise."""
    try:
        have = set(db.get_achievements(user_id))
    except Exception:
        have = set()
    eligible: list[str] = []

    def grant(aid: str):
        if aid not in have and aid not in eligible:
            eligible.append(aid)

    ts = u["total_spins"]
    if ts >= 1:
        grant("first_spin")
    if ts >= 10:
        grant("spins_10")
    if ts >= 100:
        grant("spins_100")
    if ts >= 1000:
        grant("spins_1000")
    if ts >= 3650:
        grant("spins_3650")
    if pulled and rarity == "Elite":
        grant("elite_pull")
    if pulled and rarity == "Mythical":
        grant("dih_pull")
        if u.get("dih_pulls", 0) >= 3:
            grant("dih_3")
        if quality == "Perfect Condition":
            grant("jackpot")
    if pulled and quality == "Perfect Condition":
        grant("perfect_pull")
        if u.get("perfect_pulls", 0) >= 10:
            grant("perfect_10")
        if u.get("perfect_pulls", 0) >= 30:
            grant("perfect_30")
    bal = u["balance"]
    if bal >= 100:
        grant("rich_100")
    if bal >= 1000:
        grant("rich_1k")
    if bal >= 5000:
        grant("rich_5k")
    if bal >= 10000:
        grant("rich_10k")
    if bal >= 20000:
        grant("rich_20k")
    if bal >= 30000:
        grant("rich_30k")
    if bal >= 50000:
        grant("rich_50k")
    if bal >= 75000:
        grant("rich_75k")
    if bal >= 100000:
        grant("rich_100k")
    if bal >= 200000:
        grant("rich_200k")
    if bal >= 300000:
        grant("rich_300k")
    if bal >= 500000:
        grant("rich_500k")
    if bal >= 1000000:
        grant("rich_1m")
    if u.get("sell_count", 0) >= 10:
        grant("merchant_10")
    if u.get("buy_count", 0) >= 10:
        grant("customer_10")
    if u.get("sell_count", 0) >= 20:
        grant("merchant_20")
    if u.get("sell_count", 0) >= 50:
        grant("merchant_50")
    if u.get("sell_count", 0) >= 100:
        grant("merchant_100")
    if u.get("buy_count", 0) >= 20:
        grant("customer_20")
    if u.get("buy_count", 0) >= 50:
        grant("customer_50")
    if u.get("buy_count", 0) >= 100:
        grant("customer_100")
    if u.get("trade_count", 0) >= 1:
        grant("trader")
    if u.get("trade_count", 0) >= 10:
        grant("trader_10")
    if u.get("gift_ore_count", 0) >= 1:
        grant("gifter")
    if u.get("gift_ore_count", 0) >= 10:
        grant("gifter_10")
    if u.get("gift_money_count", 0) >= 1:
        grant("gifter_money")
    if u.get("gift_money_count", 0) >= 10:
        grant("gifter_money_10")
    if u.get("market_put_count", 0) >= 1:
        grant("marketer_1")
    if u.get("market_put_count", 0) >= 3:
        grant("marketer_3")
    if u.get("market_put_count", 0) >= 10:
        grant("marketer_10")
    if u.get("market_put_count", 0) >= 20:
        grant("marketer_20")
    if u.get("market_put_count", 0) >= 50:
        grant("marketer_50")
    if u.get("market_put_count", 0) >= 100:
        grant("marketer_100")
    try:
        _guild = user_id.split(":")[0] if ":" in user_id else "DM"
        if db.get_rank(_guild, user_id) in range(1, 11):
            grant("moneybags")
        if db.get_spins_rank(_guild, user_id) in range(1, 11):
            grant("hard_working")
    except Exception:
        pass
    try:
        if db.count_inventory(user_id) >= 50:
            grant("packed")
        if db.count_inventory(user_id) >= 100:
            grant("loaded")
    except Exception:
        pass
    try:
        _vrows = db.vault_grouped(user_id)
        _vn = sum(r["count"] for r in _vrows)
        if _vn >= 1:
            grant("vault_1")
        if _vn >= 10:
            grant("vault_10")
        if any(r["quality"] == "Perfect Condition" for r in _vrows):
            grant("vault_perfect")
        if sum(r["count"] for r in _vrows if r["quality"] == "Perfect Condition") >= 10:
            grant("vault_perfect_10")
        if any(r["rarity"] == "Mythical" for r in _vrows):
            grant("vault_mythical")
    except Exception:
        pass
    # collectors: own every ore of the tier right now (inventory + vault together)
    if collectors:
        for aid in current_collectors(user_id):
            grant(aid)
    if u["streak"] >= 3:
        grant("streak_3")
    if u["streak"] >= 7:
        grant("streak_7")
    if u["streak"] >= 30:
        grant("streak_30")
    if u["streak"] >= 100:
        grant("streak_100")
    if u["streak"] >= 365:
        grant("streak_365")
    # It's Over: everything else is done
    if collectors and len(have | set(eligible)) == len(config.ACHIEVEMENTS) - 1:
        if "all_done" not in have and "all_done" not in eligible:
            eligible.append("all_done")
    db.grant_many(user_id, [a for a in eligible if a not in have])
    return [f"🏆 **{config.ACHIEVEMENTS[aid][0]}** - {config.ACHIEVEMENTS[aid][1]}"
            for aid in eligible if aid not in have]


COLLECTOR_IDS = (
    "collector_low", "collector_mid", "collector_high", "collector_elite", "collector_dih",
    "collector_all", "qcollector_low", "qcollector_mid", "qcollector_high",
    "qcollector_elite", "qcollector_dih", "qcollector_all",
)


def current_collectors(user_id: str) -> set[str]:
    """Collectors the user qualifies for RIGHT NOW (inventory + vault together).
    Single DB round trip so huge inventories stay fast."""
    owned_ores, owned_pairs = db.owned_sets(user_id)
    earned = set()
    for tier, aid in (("Low", "collector_low"), ("Mid", "collector_mid"),
                      ("High", "collector_high"), ("Elite", "collector_elite"),
                      ("Mythical", "collector_dih")):
        if all(o in owned_ores for o in config.ORES[tier]):
            earned.add(aid)
    if all(o in owned_ores for ores in config.ORES.values() for o in ores):
        earned.add("collector_all")
    quals = list(config.QUALITIES.keys())
    for tier, aid in (("Low", "qcollector_low"), ("Mid", "qcollector_mid"),
                      ("High", "qcollector_high"), ("Elite", "qcollector_elite"),
                      ("Mythical", "qcollector_dih")):
        if all((o, q) in owned_pairs for o in config.ORES[tier] for q in quals):
            earned.add(aid)
    if all((o, q) in owned_pairs for ores in config.ORES.values()
           for o in ores for q in quals):
        earned.add("qcollector_all")
    return earned


def sync_collectors(user_id: str) -> list[str]:
    """Achievements can no longer be lost - this is intentionally a no-op.
    (Kept so all existing call sites keep working.)"""
    return []


def format_achievements(newly: list[str]) -> str:
    """One message no matter how many unlock at once (compact when huge)."""
    if not newly:
        return ""
    full = "🏆 Achievement unlocked!\n" + "\n".join(newly)
    if len(full) <= 1900:
        return full
    names = []
    for line in newly:
        # line looks like "🏆 **Name** - desc" -> keep just Name
        m = line.split("**")
        names.append(m[1] if len(m) > 1 else line)
    return f"🏆 Achievements unlocked ({len(newly)}): " + ", ".join(f"**{n}**" for n in names)


async def achievement_reply(interaction: discord.Interaction, mention: str,
                            newly: list[str], ref_message=None):
    """Announce unlocks as a public REPLY (falls back to plain public sends). Always one message."""
    if not newly:
        return
    seen, unique = set(), []
    for line in newly:
        if line not in seen:
            seen.add(line)
            unique.append(line)
    text = f"{mention} " + format_achievements(unique)
    try:
        ref = ref_message or await interaction.original_response()
        await interaction.channel.send(content=text, reference=ref)
        return
    except Exception:
        pass
    try:
        await interaction.channel.send(content=text)
        return
    except Exception:
        pass
    try:
        await interaction.followup.send(text, ephemeral=False)
    except Exception:
        pass


def _spin_limits(gid: str) -> tuple[int, bool, int]:
    """Live server spin limits: (spins_per_day, unlimited_day, max_per_spin)."""
    raw_spd = (db.get_setting("spins_per_day", gid) or "10").strip().lower()
    unlimited_day = raw_spd in ("inf", "infinite", "unlimited")
    try:
        spins_per_day = 10 ** 12 if unlimited_day else max(1, int(raw_spd))
    except ValueError:
        spins_per_day = 10
    try:
        max_spin = max(1, min(100000, int(db.get_setting("max_spin", gid) or 1)))
    except ValueError:
        max_spin = 1
    return spins_per_day, unlimited_day, max_spin


def _spin_result_embed(pulls: list, n: int, u: dict, spins_left_text: str,
                       display_name: str) -> tuple:
    """Shared single/multi spin result embed. Returns (embed, rarity, quality, ore)."""
    if n == 1:
        rarity, quality, ore = pulls[0]
        value = config.quicksell_value(rarity)
        embed = discord.Embed(
            title=f"{ore} ({quality} {config.QUALITIES[quality]['emoji']})!",
            description=f"{config.tier_name(rarity)} {config.RARITIES[rarity]['dot']}",
            color=config.RARITIES[rarity]["color"],
        )
        embed.add_field(name="💰 Quicksell", value=f"${value:,}", inline=True)
        embed.add_field(name="🎰 Spins left today", value=spins_left_text, inline=True)
        embed.set_footer(text=f"🔥 {u['streak']}-day streak • {display_name}")
        if rarity == "Mythical":
            embed.add_field(name="🌟", value="**MYTHICAL TIER PULL!!** Insane luck.", inline=False)
        return embed, rarity, quality, ore
    best = max(pulls, key=lambda p: (SPIN_ORDER.index(p[0]),
                                     list(config.QUALITIES.keys()).index(p[1])))
    haul_value = sum(config.quicksell_value(p[0]) for p in pulls)
    counts = {r: sum(1 for p in pulls if p[0] == r) for r in SPIN_ORDER}
    lines = [f"{config.RARITIES[r]['dot']} {config.tier_name(r)} x{counts[r]}"
             for r in SPIN_ORDER if counts[r]]
    embed = discord.Embed(title=f"🎰 x{n} spins!", color=0x9E9E9E)
    embed.add_field(
        name="⭐ Best pull",
        value=f"**{best[2]} ({best[1]})** - {config.tier_name(best[0])} {config.RARITIES[best[0]]['dot']}",
        inline=False)
    embed.add_field(name="📦 Haul", value="\n".join(lines) if lines else "-", inline=True)
    embed.add_field(name="💰 Haul quicksell value", value=f"${haul_value:,}", inline=True)
    embed.add_field(name="🎰 Spins left today", value=spins_left_text, inline=True)
    embed.set_footer(text=f"🔥 {u['streak']}-day streak • {display_name}")
    return embed, best[0], best[1], best[2]


async def _spin_achievements_reply(interaction: discord.Interaction, uid: str, u: dict,
                                   pulls: list, result_msg, mention: str):
    """Shared post-spin achievement check + public reply."""
    newly: list[str] = []
    first = True
    for r, q, _o in sorted(set(pulls)):
        try:
            newly += check_achievements(uid, u, r, q, collectors=first, pulled=True)
        except Exception as e:
            print(f"SPINDBG check failed for {uid} ({r},{q}): {type(e).__name__}: {e}")
        first = False
    if newly:
        seen, unique = set(), []
        for line in newly:
            if line not in seen:
                seen.add(line)
                unique.append(line)
        await achievement_reply(interaction, mention, unique, ref_message=result_msg)


# ---------- spin view (quicksell button) ----------

class SpinView(discord.ui.View):
    def __init__(self, owner_id: int, item_id: int | None, rarity: str, quality: str, ore: str,
                 guild: str = "DM", amount: int = 1):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.item_id = item_id
        self.rarity = rarity
        self.quality = quality
        self.ore = ore
        self.sold = False
        self.guild = guild
        self.amount = max(1, amount)
        if item_id is None:
            self.remove_item(self.quicksell)
        else:
            self.quicksell.label = f"Quicksell ${config.quicksell_value(rarity):,}"

    @discord.ui.button(label="Spin Again", style=discord.ButtonStyle.primary, emoji="🎰")
    async def again(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("That's not your spin!", ephemeral=True)
            return
        await interaction.response.defer()
        uid = SUID(interaction, self.owner_id)
        db.init_db()
        u = db.reset_spins_if_new_day(uid, user_today(uid))
        gid = guild_scope(interaction)
        spins_per_day, unlimited_day, max_spin = _spin_limits(gid)
        if UNLIMITED_SPINS:
            n = min(self.amount, max_spin)
            spins_left_text = "∞ (test mode)"
        else:
            remaining = spins_per_day - u["spins_used_today"]
            if remaining <= 0:
                await interaction.followup.send(
                    f"❌ You're out of spins! You get **{spins_per_day}** per day.\n"
                    f"⏳ Resets in **{user_time_until_reset(uid)}**.")
                return
            n = min(self.amount, max_spin, remaining)
            spins_left_text = None  # computed after spinning
        pulls, u, item_id = _run_spin_batch(uid, n)
        if spins_left_text is None:
            spins_left_text = ("∞" if unlimited_day
                               else f"{spins_per_day - u['spins_used_today']}/{spins_per_day}")
        embed, rarity, quality, ore = _spin_result_embed(
            pulls, n, u, spins_left_text, interaction.user.display_name)
        view = SpinView(interaction.user.id, item_id, rarity, quality, ore,
                        guild=gid, amount=n)
        if not UNLIMITED_SPINS and not unlimited_day and u["spins_used_today"] >= spins_per_day:
            view.again.disabled = True
        result_msg = await interaction.followup.send(content=await _spin_ping(uid, interaction),
                                                     embed=embed, view=view)
        await _spin_achievements_reply(interaction, uid, u, pulls, result_msg,
                                       interaction.user.mention)

    @discord.ui.button(label="Inspect", style=discord.ButtonStyle.secondary, emoji="🔍")
    async def inspect(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("That's not your spin!", ephemeral=True)
            return
        pop = StackInspectView(self.owner_id, SUID(interaction, self.owner_id), "inv",
                               [{"rarity": self.rarity, "quality": self.quality,
                                 "ore": self.ore, "count": 1}],
                               False, guild=self.guild)
        await interaction.response.send_message(
            content="🔍 Inspect - pick a stack (1 shown):", view=pop, ephemeral=True)

    @discord.ui.button(label="", style=discord.ButtonStyle.green, emoji="💸")
    async def quicksell(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("That's not your spin!", ephemeral=True)
            return
        if self.sold:
            await interaction.response.send_message("Already sold!", ephemeral=True)
            return
        removed = db.remove_item_by_id(SUID(interaction, self.owner_id), self.item_id)
        if removed is None:
            await interaction.response.send_message("Item already gone (sold/listed?).", ephemeral=True)
            return
        value = config.quicksell_value(self.rarity)
        u = db.get_user(SUID(interaction, self.owner_id))
        db.update_user(SUID(interaction, self.owner_id), balance=u["balance"] + value,
                       total_earned=u["total_earned"] + value)
        self.sold = True
        button.disabled = True
        button.label = f"Sold for ${value:,}"
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(f"💸 Quicksold **{self.quality} {self.ore}** for **${value:,}**!", ephemeral=True)# ---------- inventory (two levels: ore page -> quality stacks) ----------

def ore_tier_label(ore: str, rarities: list[str]) -> str:
    """Emerald + [Mid] -> Emerald (Mid Tier)."""
    tiers = ", ".join(config.tier_name(r) for r in sorted(rarities, key=config.tier_index))
    return f"{ore} ({tiers})"


def _inspect_lines(rarity: str, quality: str, total_count: int) -> str:
    """Shared Tier/Quality/Odds/Quicksell block used by every inspect card."""
    value_each = config.quicksell_value(rarity)
    pct, one_in = config.combined_odds(rarity, quality)
    r_chance = config.RARITIES[rarity]["chance"]
    q_chance = config.QUALITIES[quality]["chance"]
    return (
        f"Tier: **{config.tier_name(rarity)}** - {r_chance:g}% (1 in {config.rarity_one_in(r_chance)} chance)\n"
        f"Quality: **{quality}** - {q_chance:g}% (1 in {config.rarity_one_in(q_chance)} chance)\n"
        f"Odds: **{pct:.4g}%** ({one_in} chance)\n"
        f"Quicksell: **${value_each:,}** each (**${value_each * total_count:,}** for all)"
    )


def _inspect_embed(ore: str, rarity: str, quality: str, total_count: int,
                   color: int, extra: str = "") -> discord.Embed:
    """Every inspect card: `Ore (Quality)` title + the shared body block."""
    body = _inspect_lines(rarity, quality, total_count)
    if extra:
        body += f"\n{extra}"
    return discord.Embed(title=f"{ore} ({quality})", description=body, color=color)


def _rarity_color(rarity: str) -> int:
    return config.RARITIES.get(rarity, {}).get("color", 0x9E9E9E)


async def inspect_text(interaction: discord.Interaction, uid: str, rarity: str, quality: str,
                       ore: str, count: int) -> discord.Embed:
    """Inventory inspect card (with market-origin lines when relevant)."""
    origins = db.origin_counts(uid, rarity, quality, ore)
    market_n = origins.get("market", 0)
    extra = ""
    if market_n > 0:
        sellers = db.origin_sellers(uid, rarity, quality, ore)
        parts = []
        for sid, c in list(sellers.items())[:3]:
            parts.append(f"🛒 Bought from **{(await display_name(interaction, sid))}** x{c}")
        unknown = market_n - sum(sellers.values())
        if unknown > 0:
            parts.append(f"🛒 Bought on the player market x{unknown}")
        extra = "\n".join(parts)
    return _inspect_embed(ore, rarity, quality, count, _rarity_color(rarity), extra)


# ---------- unified inventory/vault browser ----------
# One message: quality filter + ore dropdown (All ores included) + inspect +
# Quicksell/Vault buttons (inventory) or Un-vault (vault).
# Buttons appear whenever scoped (an ore picked and/or a quality picked),
# i.e. everywhere EXCEPT all-qualities + all-ores.

def _scope_targets(uid: str, source: str, ore: str | None, quality: str | None,
                   tier: str | None = None) -> list[dict]:
    items = db.get_inventory_grouped(uid) if source in ("inv", "gift", "trade", "list") else db.vault_grouped(uid)
    return [i for i in items
            if (ore is None or i["ore"] == ore)
            and (quality is None or i["quality"] == quality)
            and (tier is None or i["rarity"] == tier)]


class ScopeQuicksellModal(discord.ui.Modal, title="Quicksell"):
    """Sells up to N (or ALL) across the current browser scope. Batched."""
    amount = discord.ui.TextInput(label="How many? (number or ALL)", placeholder="e.g. 5 or ALL",
                                  max_length=8)

    def __init__(self, owner_id: int, ore: str | None, quality: str | None, tier: str | None = None,
                 bview=None, browser_message=None):
        super().__init__()
        self.owner_id = owner_id
        self.ore = ore
        self.quality = quality
        self.tier = tier
        self.bview = bview
        self.browser_message = browser_message

    async def _refresh_browser(self):
        if self.bview is None or self.browser_message is None:
            return
        try:
            v = self.bview
            fresh = InvBrowser(v.viewer_id, v.target_id, v.target_name, v.public,
                               source=v.source, quality=v.quality, ore=v.ore, tier=v.tier,
                               guild=v.guild, recip_id=v.recip_id, recip_name=v.recip_name,
                               trade_tid=v.trade_tid, trade_side=v.trade_side)
            await self.browser_message.edit(embed=fresh.render(), view=fresh)
        except Exception:
            pass

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("That's not yours!", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        uid = SUID(interaction, self.owner_id)
        targets = _scope_targets(uid, "inv", self.ore, self.quality, self.tier)
        if not targets:
            await interaction.followup.send("❌ Nothing to sell.", ephemeral=True)
            return
        raw = str(self.amount.value).strip().lower()
        sell_all = raw in ("all", "max")
        if not sell_all:
            try:
                limit = int(raw)
            except ValueError:
                await interaction.followup.send("❌ Type a number or ALL.", ephemeral=True)
                return
            limit = max(0, limit)
        sold, earned = 0, 0
        for t in targets:
            want = t["count"] if sell_all else min(limit - sold, t["count"])
            if want <= 0:
                break
            got = db.remove_many_items(uid, t["rarity"], t["quality"], t["ore"], want)
            sold += got
            earned += got * config.quicksell_value(t["rarity"])
            if not sell_all and sold >= limit:
                break
        u = db.get_user(uid)
        db.update_user(uid, balance=u["balance"] + earned, total_earned=u["total_earned"] + earned)
        newly = check_achievements(uid, db.get_user(uid), "", "")
        if sold >= 10 and db.grant_achievement(uid, "wholesaler"):
            newly.append(f"🏆 **{config.ACHIEVEMENTS['wholesaler'][0]}** - {config.ACHIEVEMENTS['wholesaler'][1]}")
        if sold >= 100 and db.grant_achievement(uid, "mass_seller"):
            newly.append(f"🏆 **{config.ACHIEVEMENTS['mass_seller'][0]}** - {config.ACHIEVEMENTS['mass_seller'][1]}")
        sync_collectors(uid)
        scope = " ".join(x for x in (self.ore or "", f"({self.quality})" if self.quality else "") if x)
        await self._refresh_browser()
        result_msg = await interaction.followup.send(
            f"💸 Sold **{sold}** ore(s){' ' + scope if scope else ''} for **${earned:,}**!",
            ephemeral=True)
        if newly:
            await achievement_reply(interaction, interaction.user.mention, newly,
                                    ref_message=result_msg)


class ScopeVaultModal(discord.ui.Modal, title="Vault - how many?"):
    """Moves up to N (or ALL) of the current scope into the vault. Origins preserved."""

    amount = discord.ui.TextInput(label="How many? (number or ALL)", placeholder="e.g. 5 or ALL",
                                  max_length=8)

    def __init__(self, owner_id: int, ore: str | None, quality: str | None, tier: str | None = None,
                 bview=None, browser_message=None):
        super().__init__()
        self.owner_id = owner_id
        self.ore = ore
        self.quality = quality
        self.tier = tier
        self.bview = bview
        self.browser_message = browser_message

    async def _refresh_browser(self):
        if self.bview is None or self.browser_message is None:
            return
        try:
            v = self.bview
            fresh = InvBrowser(v.viewer_id, v.target_id, v.target_name, v.public,
                               source=v.source, quality=v.quality, ore=v.ore, tier=v.tier,
                               guild=v.guild, recip_id=v.recip_id, recip_name=v.recip_name,
                               trade_tid=v.trade_tid, trade_side=v.trade_side)
            await self.browser_message.edit(embed=fresh.render(), view=fresh)
        except Exception:
            pass

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("That's not yours!", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        uid = SUID(interaction, self.owner_id)
        targets = _scope_targets(uid, "inv", self.ore, self.quality, self.tier)
        if not targets:
            await interaction.followup.send("❌ Nothing to store.", ephemeral=True)
            return
        raw = str(self.amount.value).strip().lower()
        store_all = raw in ("all", "max")
        if not store_all:
            try:
                limit = int(raw)
            except ValueError:
                await interaction.followup.send("❌ Type a number or ALL.", ephemeral=True)
                return
            limit = max(0, limit)
        moved = 0
        for t in targets:
            want = t["count"] if store_all else min(limit - moved, t["count"])
            if want <= 0:
                break
            # batched per-origin move (origins preserved):
            remaining = want
            for origin, c in db.origin_counts(uid, t["rarity"], t["quality"], t["ore"]).items():
                k = min(c, remaining)
                if k <= 0:
                    continue
                got = db.remove_many_items(uid, t["rarity"], t["quality"], t["ore"], k, origin=origin)
                db.vault_add_many(uid, [(t["rarity"], t["quality"], t["ore"], origin)] * got)
                moved += got
                remaining -= got
                if remaining <= 0:
                    break
            if not store_all and moved >= limit:
                break
        vault_newly = check_achievements(uid, db.get_user(uid), "", "") if moved > 0 else []
        await self._refresh_browser()
        result_msg = await interaction.followup.send(
            f"🗝️ Stored **{moved}** ore(s) in your vault!", ephemeral=True)
        if vault_newly:
            await achievement_reply(interaction, interaction.user.mention, vault_newly,
                                    ref_message=result_msg)


class ScopeUnvaultModal(discord.ui.Modal, title="Un-vault - how many?"):
    amount = discord.ui.TextInput(label="How many? (number or ALL)", placeholder="e.g. 5 or ALL",
                                  max_length=8)

    def __init__(self, owner_id: int, ore: str | None, quality: str | None, tier: str | None = None,
                 bview=None, browser_message=None):
        super().__init__()
        self.owner_id = owner_id
        self.ore = ore
        self.quality = quality
        self.tier = tier
        self.bview = bview
        self.browser_message = browser_message

    async def _refresh_browser(self):
        if self.bview is None or self.browser_message is None:
            return
        try:
            v = self.bview
            fresh = InvBrowser(v.viewer_id, v.target_id, v.target_name, v.public,
                               source=v.source, quality=v.quality, ore=v.ore, tier=v.tier,
                               guild=v.guild, recip_id=v.recip_id, recip_name=v.recip_name,
                               trade_tid=v.trade_tid, trade_side=v.trade_side)
            await self.browser_message.edit(embed=fresh.render(), view=fresh)
        except Exception:
            pass

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("That's not yours!", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        uid = SUID(interaction, self.owner_id)
        targets = _scope_targets(uid, "vault", self.ore, self.quality, self.tier)
        if not targets:
            await interaction.followup.send("❌ Nothing to take.", ephemeral=True)
            return
        raw = str(self.amount.value).strip().lower()
        take_all = raw in ("all", "max")
        if not take_all:
            try:
                limit = int(raw)
            except ValueError:
                await interaction.followup.send("❌ Type a number or ALL.", ephemeral=True)
                return
            limit = max(0, limit)
        moved = 0
        for t in targets:
            want = t["count"] if take_all else min(limit - moved, t["count"])
            if want <= 0:
                break
            remaining = want
            for origin, c in db.vault_origin_counts(uid, t["rarity"], t["quality"], t["ore"]).items():
                k = min(c, remaining)
                if k <= 0:
                    continue
                got = db.vault_remove_many(uid, t["rarity"], t["quality"], t["ore"], k, origin=origin)
                db.add_many_items(uid, [(t["rarity"], t["quality"], t["ore"])] * got, origin=origin)
                moved += got
                remaining -= got
                if remaining <= 0:
                    break
            if not take_all and moved >= limit:
                break
        await self._refresh_browser()
        await interaction.followup.send(
            f"📦 Took **{moved}** ore(s) out of your vault!", ephemeral=True)


class BrowserTierSelect(discord.ui.Select):
    def __init__(self, current: str | None):
        super().__init__(placeholder="Filter by tier…", options=[
            discord.SelectOption(label="All tiers", value="all", default=(current is None))
        ] + [discord.SelectOption(label=config.tier_name(r), value=r,
                                   default=(r == current),
                                   emoji=config.RARITIES[r].get("dot", ""))
             for r in config.RARITIES])

    async def callback(self, interaction: discord.Interaction):
        view: InvBrowser = self.view
        if interaction.user.id != view.viewer_id:
            await interaction.response.send_message("That's not yours! Run `/inventory` yourself.",
                                                    ephemeral=True)
            return
        view.tier = None if self.values[0] == "all" else self.values[0]
        view.ore = None
        view.selected = None
        embed = view.render()
        await interaction.response.edit_message(embed=embed, view=view)


class BrowserQualitySelect(discord.ui.Select):
    def __init__(self, current: str | None):
        super().__init__(placeholder="Sort by quality…", options=[
            discord.SelectOption(label="All qualities", value="all", default=(current is None))
        ] + [discord.SelectOption(label=f"Only {q}", value=q, default=(q == current))
             for q in config.QUALITIES])

    async def callback(self, interaction: discord.Interaction):
        view: InvBrowser = self.view
        if interaction.user.id != view.viewer_id:
            await interaction.response.send_message("That's not yours! Run `/inventory` yourself.",
                                                    ephemeral=True)
            return
        view.quality = None if self.values[0] == "all" else self.values[0]
        embed = view.render()
        await interaction.response.edit_message(embed=embed, view=view)


class BrowserOreSelect(discord.ui.Select):
    def __init__(self, ores: list[dict], current: str | None):
        # bare ore names (tier lives in the tier dropdown)
        options = [discord.SelectOption(label="All ores", value="all",
                                        default=(current is None))]
        for o in ores[:24]:
            options.append(discord.SelectOption(
                label=f"{o['ore']} x{o['count']}"[:100],
                value=o["ore"], default=(o["ore"] == current)))
        super().__init__(placeholder="Choose which ore page to open…", options=options)
        self._ores = ores

    async def callback(self, interaction: discord.Interaction):
        view: InvBrowser = self.view
        if interaction.user.id != view.viewer_id:
            await interaction.response.send_message("That's not yours! Run `/inventory` yourself.",
                                                    ephemeral=True)
            return
        view.ore = None if self.values[0] == "all" else self.values[0]
        view.selected = None
        # auto-match the tier dropdown to the picked ore
        if view.ore:
            match = next((o for o in self._ores if o["ore"] == view.ore), None)
            if match and match["rarities"]:
                view.tier = sorted(match["rarities"], key=config.tier_index)[0]
        embed = view.render()
        await interaction.response.edit_message(embed=embed, view=view)


class StackInspectView(discord.ui.View):
    """Ephemeral pop-up holding an inspect dropdown (opened via Inspect button)."""

    def __init__(self, viewer_id: int, target_id: int, source: str,
                 stacks: list[dict], public: bool, guild: str = "DM", inv: bool = True,
                 list_mode: bool = False, bview=None, browser_message=None):
        super().__init__(timeout=180)
        self.viewer_id = viewer_id
        self.target_id = target_id
        self.source = source
        self.public = public
        self.guild = guild
        self._inv = inv
        self.selected = None
        self.list_mode = list_mode
        self.bview = bview
        self.browser_message = browser_message
        if stacks:
            self.add_item(BrowserInspectSelect(stacks))
        if list_mode:
            self.add_item(self._list_btn())

    def _list_btn(self):
        view = self

        async def cb(interaction: discord.Interaction):
            if interaction.user.id != view.viewer_id:
                await interaction.response.send_message("That's not yours!", ephemeral=True)
                return
            sel = view.selected
            if not sel or len(sel) != 3:
                try:
                    first_opt = view.children[0].options[0]
                    sel = tuple(first_opt.value.split("|", 2)) if first_opt.value != "none" else None
                except Exception:
                    sel = None
            if not sel:
                await interaction.response.send_message("❌ Pick a stack first!", ephemeral=True)
                return
            rarity, quality, ore = sel
            await interaction.response.send_modal(
                ListAmountModal(view.viewer_id, ore, quality, rarity,
                                bview=view.bview, browser_message=view.browser_message))
        btn = discord.ui.Button(label="List this", style=discord.ButtonStyle.green, emoji="📋")
        btn.callback = cb
        return btn
        if list_mode:
            self.add_item(self._list_btn())
        if list_mode:
            self.add_item(self._list_btn())

    def _data_inv(self) -> bool:
        return self._inv

    def _list_btn(self):
        view = self

        async def cb(interaction: discord.Interaction):
            if interaction.user.id != view.viewer_id:
                await interaction.response.send_message("That's not yours!", ephemeral=True)
                return
            sel = view.selected
            if not sel or len(sel) != 3:
                # default to first shown stack
                sel = None
                try:
                    first_opt = view.children[0].options[0]
                    if first_opt.value != "none":
                        sel = tuple(first_opt.value.split("|", 2))
                except Exception:
                    pass
            if not sel:
                await interaction.response.send_message("❌ Pick a stack first!", ephemeral=True)
                return
            rarity, quality, ore = sel
            tier = next((r for r in config.RARITIES if r == rarity), None)
            await interaction.response.send_modal(
                ListAmountModal(view.viewer_id, ore, quality, tier,
                                bview=view.bview, browser_message=view.browser_message))
        btn = discord.ui.Button(label="List this", style=discord.ButtonStyle.green, emoji="📋")
        btn.callback = cb
        return btn


class BrowserInspectSelect(discord.ui.Select):
    def __init__(self, stacks: list[dict]):
        # value includes ore: main pages list many ores, and two ores can
        # share a rarity+quality (duplicate values = Discord 400 error)
        self.lookup = {(s["rarity"], s["quality"], s["ore"]): s for s in stacks}
        options = []
        for s in stacks[:25]:
            label = f"{s['ore']} ({s['quality']}) x{s['count']}"[:100]
            desc = f"{config.tier_name(s['rarity'])} • quicksell ${config.quicksell_value(s['rarity']):,} each"[:100]
            options.append(discord.SelectOption(label=label, description=desc,
                                                value=f"{s['rarity']}|{s['quality']}|{s['ore']}"))
        super().__init__(placeholder="Inspect a stack…", options=options or [
            discord.SelectOption(label="(empty)", value="none")])

    async def callback(self, interaction: discord.Interaction):
        view: InvBrowser = self.view
        if interaction.user.id != view.viewer_id:
            await interaction.response.send_message("Run `/inventory` yourself to browse!",
                                                    ephemeral=True)
            return
        if self.values[0] == "none":
            return
        rarity, quality, ore = self.values[0].split("|", 2)
        s = self.lookup[(rarity, quality, ore)]
        view.selected = (rarity, quality, ore)
        scoped = f"{view.guild}:{view.target_id}"
        if view.source == "vault" or not view._data_inv():
            embed = vault_inspect_text(scoped, rarity, quality, s["ore"], s["count"])
        else:
            embed = await inspect_text(interaction, scoped, rarity, quality,
                                       s["ore"], s["count"])
        await interaction.response.send_message(embed=embed, ephemeral=True)


class InvBrowser(discord.ui.View):
    """Unified inventory/vault browser. One message for everything.

    source='inv': Quicksell + Vault buttons (own only), shown whenever scoped
      (ore picked and/or quality picked) - hidden for all/all.
    source='vault': Un-vault button, same rule. Plus vault has its own quality filter.
    """

    def __init__(self, viewer_id: int, target_id: int, target_name: str, public: bool,
                 source: str = "inv", quality: str | None = None, ore: str | None = None,
                 tier: str | None = None, recip_id: int | None = None,
                 recip_name: str = "", trade_tid: int | None = None,
                 trade_side: str | None = None, guild: str = "DM",
                 gift_from: str = "inv"):
        super().__init__(timeout=300)
        self.viewer_id = viewer_id
        self.target_id = target_id
        self.target_name = target_name
        self.public = public
        self.source = source
        self.quality = quality
        self.ore = ore
        self.tier = tier
        self.selected = None
        self.recip_id = recip_id if recip_id is not None else target_id
        self.recip_name = recip_name
        self.trade_tid = trade_tid
        self.trade_side = trade_side
        self.guild = guild
        self.gift_from = gift_from
        self.page = 0
        self._pages = 1
        self._rebuild()

    def _t(self) -> str:
        """Scoped target id for all DB reads in this browser."""
        return f"{self.guild}:{self.target_id}"

    def _inv(self) -> bool:
        return self.source in ("inv", "gift", "trade", "list")

    def _data_inv(self) -> bool:
        """True if rows come from inventory (gift-from-vault reads vault)."""
        if self.source == "gift":
            return self.gift_from != "vault"
        return self._inv()

    # ----- data (one grouped query per render; overview + totals derived in Python) -----
    def _grouped_all(self) -> list[dict]:
        if self._data_inv():
            return db.get_inventory_grouped(self._t())
        return db.vault_grouped(self._t())

    def overviews(self, grouped: list[dict] | None = None) -> list[dict]:
        if grouped is None:
            grouped = self._grouped_all()
        by_ore: dict[str, dict] = {}
        for r in grouped:
            if self.quality and r["quality"] != self.quality:
                continue
            if self.tier and r["rarity"] != self.tier:
                continue
            o = by_ore.setdefault(r["ore"], {"ore": r["ore"], "count": 0, "rarities": []})
            o["count"] += r["count"]
            if r["rarity"] not in o["rarities"]:
                o["rarities"].append(r["rarity"])
        ores = list(by_ore.values())
        ores.sort(key=lambda o: (min(config.tier_index(r) for r in o["rarities"]), -o["count"]))
        return ores

    def stacks(self) -> list[dict]:
        if not self.ore:
            return []
        if self._data_inv():
            rows = db.get_ore_detail(self._t(), self.ore, self.quality)
        else:
            rows = db.vault_detail(self._t(), self.ore)
            if self.quality:
                rows = [r for r in rows if r["quality"] == self.quality]
        if self.tier:
            rows = [r for r in rows if r["rarity"] == self.tier]
        return rows

    def totals(self, grouped: list[dict] | None = None) -> tuple[int, int]:
        if grouped is None:
            grouped = self._grouped_all()
        n = v = 0
        for r in grouped:
            if self.ore and r["ore"] != self.ore:
                continue
            if self.quality and r["quality"] != self.quality:
                continue
            if self.tier and r["rarity"] != self.tier:
                continue
            n += r["count"]
            v += config.quicksell_value(r["rarity"]) * r["count"]
        return n, v

    # ----- render -----
    def render(self):
        grouped = self._grouped_all()
        ores = self.overviews(grouped)
        stacks = self.stacks()
        if self.ore and not stacks:
            self.ore, self.selected = None, None
            stacks = []
            ores = self.overviews(grouped)
        pages = max(1, (len(ores) + 24) // 25)
        self.page = self.page % pages
        page_ores = ores[self.page * 25:(self.page + 1) * 25]
        n, v = self.totals(grouped)
        self._page_ores = page_ores
        self._pages = pages
        icon = {"inv": "🎒", "vault": "🗝️", "gift": "🎁", "trade": "🔄", "list": "📦"}.get(
            self.source, "🎒")
        what = {"inv": "Inventory", "vault": "Vault",
                "gift": f"Gift for {self.recip_name}", "trade": "Trade offer",
                "list": "List an ore"}.get(self.source, "Inventory")
        if self.ore:
            r0 = min(stacks, key=lambda s: config.tier_index(s["rarity"]))["rarity"] if stacks else None
            dot = config.RARITIES[r0]["dot"] if r0 else icon
            title = f"{dot} {self.ore} ({n} ores) (${v:,})"
            lines = []
            for s in stacks:
                mark = "▶ " if (s["rarity"], s["quality"], s["ore"]) == self.selected else ""
                lines.append(f"{mark}**{s['ore']} ({s['quality']})** x{s['count']}")
            desc = "\n".join(lines) or "Empty!"
        else:
            title = f"{icon} {self.target_name}'s {what} ({n} ores) (${v:,})"
            desc = "\n".join(f"**{o['ore']}** x{o['count']}" for o in page_ores) or "Empty!"
        if self.quality:
            title += f" - {self.quality} only"
        if self.tier:
            title += f" - {config.tier_name(self.tier)}"
        if pages > 1 and not self.ore:
            title += f" (page {self.page + 1}/{pages})"
        self._rebuild(page_ores, stacks)
        return discord.Embed(title=title, description=desc,
                             color=0x00BCD4 if self.source == "inv" else 0x795548)

    def _rebuild(self, ores: list[dict] | None = None, stacks: list[dict] | None = None):
        self.clear_items()
        if ores is None:
            ores = self.overviews()
        if stacks is None:
            stacks = self.stacks()
        # tier always stays up (auto-matches the picked ore)
        self.add_item(BrowserTierSelect(self.tier))
        self.add_item(BrowserOreSelect(ores, self.ore))
        self.add_item(BrowserQualitySelect(self.quality))
        # inspect button on every UI, never grayed
        if self.ore and stacks:
            if self.selected is None:
                self.selected = (stacks[0]["rarity"], stacks[0]["quality"], stacks[0]["ore"])
            inspect_stacks = stacks
        else:
            all_stacks = [r for r in _scope_targets(f"{self.guild}:{self.target_id}", self.source, None,
                                                    self.quality, self.tier)]
            all_stacks.sort(key=lambda r: (config.tier_index(r["rarity"]),
                                           list(config.QUALITIES.keys()).index(r["quality"])))
            inspect_stacks = all_stacks or None
        if inspect_stacks:
            self.add_item(self._inspect_btn(inspect_stacks, disabled=False))
        mine = self.viewer_id == self.target_id
        full = bool(self.tier and self.ore and self.quality)
        if mine:
            if self.source == "inv":
                self.add_item(self._qs_btn())
                self.add_item(self._vault_btn())
            elif self.source == "vault":
                self.add_item(self._unvault_btn())
            elif self.source == "gift":
                self.add_item(self._gift_btn())
            elif self.source == "trade" and self.ore and stacks:
                self.add_item(self._trade_select_btn())
            elif self.source == "list" and full:
                self.add_item(self._list_this_btn())
        # page turns on every UI
        if getattr(self, "_pages", 1) > 1 and not self.ore:
            self.add_item(self._prev_btn())
            self.add_item(self._next_btn())

    def _page_btn(self, delta: int, emoji: str):
        view = self

        async def cb(interaction: discord.Interaction):
            if interaction.user.id != view.viewer_id:
                await interaction.response.send_message("That's not yours!", ephemeral=True)
                return
            view.page = (view.page + delta) % max(1, getattr(view, "_pages", 1))
            await interaction.response.edit_message(embed=view.render(), view=view)
        btn = discord.ui.Button(emoji=emoji, style=discord.ButtonStyle.secondary)
        btn.callback = cb
        return btn

    def _prev_btn(self):
        return self._page_btn(-1, "◀")

    def _next_btn(self):
        return self._page_btn(1, "▶")

    def _inspect_btn(self, stacks: list[dict], disabled: bool = False):
        view = self

        async def cb(interaction: discord.Interaction):
            if interaction.user.id != view.viewer_id:
                await interaction.response.send_message("That's not yours!", ephemeral=True)
                return
            if len(stacks) == 1 and view.source != "list":
                # single stack: show it directly, no picker needed
                # (list mode, market view and spin always show the picker)
                s = stacks[0]
                scoped = f"{view.guild}:{view.target_id}"
                if view.source == "vault" or not view._data_inv():
                    embed = vault_inspect_text(scoped, s["rarity"], s["quality"],
                                               s["ore"], s["count"])
                else:
                    embed = await inspect_text(interaction, scoped, s["rarity"], s["quality"],
                                               s["ore"], s["count"])
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return
            pop = StackInspectView(view.viewer_id, view.target_id, view.source,
                                   stacks, view.public, guild=view.guild,
                                   inv=view._data_inv(),
                                   list_mode=(view.source == "list"),
                                   bview=view, browser_message=interaction.message)
            await interaction.response.send_message(
                content=f"🔍 Inspect - pick a stack ({len(stacks)} shown):",
                view=pop, ephemeral=True)
        btn = discord.ui.Button(label="Inspect", style=discord.ButtonStyle.secondary, emoji="🔍",
                                disabled=disabled)
        btn.callback = cb
        return btn

    def _list_this_btn(self):
        view = self

        async def cb(interaction: discord.Interaction):
            if interaction.user.id != view.viewer_id:
                await interaction.response.send_message("That's not yours!", ephemeral=True)
                return
            await interaction.response.send_modal(
                ListAmountModal(view.viewer_id, view.ore, view.quality, view.tier,
                                bview=view, browser_message=interaction.message))
        btn = discord.ui.Button(label="List this", style=discord.ButtonStyle.green, emoji="📋")
        btn.callback = cb
        return btn

    def _trade_select_btn(self):
        view = self

        async def cb(interaction: discord.Interaction):
            if interaction.user.id != view.viewer_id:
                await interaction.response.send_message("That's not yours!", ephemeral=True)
                return
            t = trades.get(view.trade_tid)
            if t is None or interaction.user.id != t[f"{view.trade_side}_id"]:
                await interaction.response.send_message("Trade expired or not yours!",
                                                        ephemeral=True)
                return
            stacks = view.stacks()
            if not stacks:
                await interaction.response.send_message("❌ Nothing to offer here.", ephemeral=True)
                return
            stack = stacks[0]  # top stack (filter quality first to offer Perfect etc.)
            t[f"{view.trade_side}_pick"] = {"rarity": stack["rarity"],
                                            "quality": stack["quality"], "ore": view.ore}
            t["a_ok"] = t["b_ok"] = False
            _persist_trade(t)
            await interaction.response.send_message(
                content=f"✅ Offer set: **{view.ore} ({stack['quality']})**. Back to the trade!",
                ephemeral=True)
            try:
                await t["message"].edit(embed=trade_embed(t))
            except Exception:
                pass
        btn = discord.ui.Button(label="Select", style=discord.ButtonStyle.green, emoji="🤝")
        btn.callback = cb
        return btn

    def _qs_btn(self):
        view = self

        async def cb(interaction: discord.Interaction):
            if interaction.user.id != view.viewer_id:
                await interaction.response.send_message("That's not yours!", ephemeral=True)
                return
            m = ScopeQuicksellModal(view.viewer_id, view.ore, view.quality, view.tier,
                                    bview=view, browser_message=interaction.message)
            await interaction.response.send_modal(m)
        btn = discord.ui.Button(label="Quicksell", style=discord.ButtonStyle.green, emoji="💸")
        btn.callback = cb
        return btn

    def _vault_btn(self):
        view = self

        async def cb(interaction: discord.Interaction):
            if interaction.user.id != view.viewer_id:
                await interaction.response.send_message("That's not yours!", ephemeral=True)
                return
            m = ScopeVaultModal(view.viewer_id, view.ore, view.quality, view.tier,
                                bview=view, browser_message=interaction.message)
            await interaction.response.send_modal(m)
        btn = discord.ui.Button(label="Vault", style=discord.ButtonStyle.secondary, emoji="🗝️")
        btn.callback = cb
        return btn

    def _gift_btn(self):
        view = self

        async def cb(interaction: discord.Interaction):
            if interaction.user.id != view.viewer_id:
                await interaction.response.send_message("That's not yours!", ephemeral=True)
                return
            await interaction.response.send_modal(
                ScopeGiftModal(view.viewer_id, view.recip_id, view.ore, view.quality, view.tier,
                               source=(view.gift_from or "inv")))
        btn = discord.ui.Button(label="Gift", style=discord.ButtonStyle.green, emoji="🎁")
        btn.callback = cb
        return btn

    def _unvault_btn(self):
        view = self

        async def cb(interaction: discord.Interaction):
            if interaction.user.id != view.viewer_id:
                await interaction.response.send_message("That's not yours!", ephemeral=True)
                return
            m = ScopeUnvaultModal(view.viewer_id, view.ore, view.quality, view.tier,
                                  bview=view, browser_message=interaction.message)
            await interaction.response.send_modal(m)
        btn = discord.ui.Button(label="Un-vault", style=discord.ButtonStyle.primary, emoji="📦")
        btn.callback = cb
        return btn


# ---------- vault inspect text (used by the unified browser) ----------

def vault_inspect_text(uid: str, rarity: str, quality: str, ore: str, count: int) -> discord.Embed:
    origins = db.vault_origin_counts(uid, rarity, quality, ore)
    market_n = origins.get("market", 0)
    extra = f"🛒 {market_n}x bought on the player market" if market_n else ""
    return _inspect_embed(ore, rarity, quality, count, _rarity_color(rarity), extra)


@bot.tree.command(name="vault", description="See vaults (yours, or a public one). No quicksell here.")
@app_commands.describe(user="Optional: view another player's public vault")
async def vault(interaction: discord.Interaction, user: discord.User | None = None):
    target = user or interaction.user
    tid = SUID(interaction, target.id)
    t = db.get_user(tid)
    public = bool(t.get("vault_public", 0))
    if target.id != interaction.user.id and not public:
        await interaction.response.send_message(
            f"🔒 **{(await display_name(interaction, tid))}'s** vault is private.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=not public)
    if db.vault_count(tid) == 0:
        await interaction.followup.send("🗝️ Vault is empty! Store ores via inventory.",
                                        ephemeral=not public)
        return
    name = await display_name(interaction, tid)
    view = InvBrowser(interaction.user.id, target.id, name, public, source="vault",
                      guild=guild_scope(interaction))
    await interaction.followup.send(embed=view.render(), view=view, ephemeral=not public)


# ---------- bot events ----------

@bot.event
async def on_ready():
    db.init_db()
    try:
        await bot.tree.sync()
    except Exception as e:
        print(f"Slash sync failed: {e}")
    print(f"Logged in as {bot.user} - /spin ready!" + (" [UNLIMITED SPINS TEST MODE]" if UNLIMITED_SPINS else ""))
    await restore_trades()


async def restore_trades():
    """Re-attach views to trade messages after a restart so active trades survive."""
    for row in db.open_trades():
        t = _mem_trade(row)
        if not t["channel_id"] or not t["message_id"]:
            continue
        try:
            channel = bot.get_channel(int(t["channel_id"])) or await bot.fetch_channel(int(t["channel_id"]))
            message = await channel.fetch_message(int(t["message_id"]))
        except Exception:
            db.delete_trade(t["id"])
            continue
        t["message"] = message
        trades[t["id"]] = t
        try:
            if t["stage"] == "main":
                await message.edit(embed=trade_embed(t), view=TradeMainView(t["id"]))
            else:
                await message.edit(view=TradeRequestView(t["id"]))
        except Exception:
            pass
    if trades:
        print(f"Restored {len(trades)} active trade(s).")


# ---------- commands ----------

SPIN_ORDER = ["Low", "Mid", "High", "Elite", "Mythical"]
SPIN_KEYS = {"Low": "low_pulls", "Mid": "mid_pulls", "High": "high_pulls",
             "Elite": "elite_pulls", "Mythical": "dih_pulls"}


def _run_spin_batch(uid: str, n: int):
    """Rolls n times, inserts, updates counters/streak/rarest. Returns (pulls, u, item_id)."""
    pulls: list[tuple[str, str, str]] = [roll_one() for _ in range(n)]
    item_id = None
    if n == 1:
        item_id = db.add_item(uid, *pulls[0])  # THE item (for quicksell button)
    else:
        db.add_many_items(uid, pulls)  # batched
    u = db.get_user(uid)
    counts = {r: sum(1 for p in pulls if p[0] == r) for r in SPIN_ORDER}
    perfect_n = sum(1 for p in pulls if p[1] == "Perfect Condition")
    updates = dict(spins_used_today=u["spins_used_today"] + n, total_spins=u["total_spins"] + n)
    for r in SPIN_ORDER:
        updates[SPIN_KEYS[r]] = u[SPIN_KEYS[r]] + counts[r]
    updates["perfect_pulls"] = u.get("perfect_pulls", 0) + perfect_n
    db.update_user(uid, **updates)
    u = db.update_streak(uid, user_today(uid))
    u = db.get_user(uid)
    # rarest spin ever (global best, kept even if sold - stored as rarity|quality|ore)
    try:
        qualities = list(config.QUALITIES.keys())
        best_pull = max(pulls, key=lambda p: (config.tier_index(p[0]), qualities.index(p[1])))
        cur = parse_best(u.get("rarest_spin", ""))
        cur_idx = (config.tier_index(cur[0]), qualities.index(cur[1])) if cur else (-1, -1)
        new_idx = (config.tier_index(best_pull[0]), qualities.index(best_pull[1]))
        if new_idx > cur_idx:
            db.update_user(uid, rarest_spin="|".join(best_pull))
            u = db.get_user(uid)
    except Exception:
        pass
    return pulls, u, item_id


@bot.tree.command(name="spin", description=f"Spin! Optional amount (server limit applies).")
@app_commands.describe(amount="How many spins (default 1)")
async def spin(interaction: discord.Interaction, amount: app_commands.Range[int, 1, 100000] = 1):
    await interaction.response.defer()
    uid = SUID(interaction)
    db.init_db()
    u = db.reset_spins_if_new_day(uid, user_today(uid))
    raw_spd = (db.get_setting("spins_per_day", guild_scope(interaction)) or "10").strip().lower()
    unlimited_day = raw_spd in ("inf", "infinite", "unlimited")
    try:
        spins_per_day = 10 ** 12 if unlimited_day else max(1, int(raw_spd))
    except ValueError:
        spins_per_day = 10
    try:
        max_spin = max(1, min(100000, int(db.get_setting("max_spin", guild_scope(interaction)) or 1)))
    except ValueError:
        max_spin = 1
    amount = min(amount, max_spin)

    if UNLIMITED_SPINS:
        n = amount
        spins_left_text = "∞ (test mode)"
    else:
        remaining = spins_per_day - u["spins_used_today"]
        if remaining <= 0:
            await interaction.followup.send(
                f"❌ You're out of spins! You get **{spins_per_day}** per day.\n"
                f"⏳ Resets in **{user_time_until_reset(uid)}**.\n"
                f"🔥 Streak: **{u['streak']}** day(s) - spin daily to keep it!"
            )
            return
        n = min(amount, remaining)
        spins_left_text = None  # computed after spinning

    pulls, u, item_id = _run_spin_batch(uid, n)
    if spins_left_text is None:
        spins_left_text = "∞" if unlimited_day else f"{spins_per_day - u['spins_used_today']}/{spins_per_day}"

    ping = await _spin_ping(uid, interaction)

    embed, rarity, quality, ore = _spin_result_embed(
        pulls, n, u, spins_left_text, interaction.user.display_name)
    view = SpinView(interaction.user.id, item_id, rarity, quality, ore,
                    guild=guild_scope(interaction), amount=n)
    if not UNLIMITED_SPINS and not unlimited_day and u["spins_used_today"] >= spins_per_day:
        view.again.disabled = True
    result_msg = await interaction.followup.send(content=ping, embed=embed, view=view)
    # achievements AFTER the result so "thinking" always resolves fast;
    # collectors scanned once (first pair) instead of per pair
    print(f"SPINDBG spins={u['total_spins']} pairs={sorted(set(pulls))}")
    await _spin_achievements_reply(interaction, uid, u, pulls, result_msg,
                                   interaction.user.mention)


@bot.tree.command(name="wallet", description="Check wallet money (yours, or a public one).")
@app_commands.describe(user="Optional: view another player's public wallet")
async def wallet(interaction: discord.Interaction, user: discord.User | None = None):
    target = user or interaction.user
    tid = SUID(interaction, target.id)
    t = db.get_user(tid)
    public = bool(t.get("balance_public", 0))
    if target.id != interaction.user.id and not public:
        await interaction.response.send_message(
            f"🔒 **{(await display_name(interaction, tid))}'s** wallet is private.", ephemeral=True)
        return
    u = db.get_user(tid)
    name = await display_name(interaction, tid)
    embed = discord.Embed(title=f"💵 {name}'s Wallet", color=0x4CAF50)
    embed.add_field(name="💵 Wallet", value=f"**${u['balance']:,}**", inline=False)
    embed.set_footer(text=f"Total earned: ${u['total_earned']:,}")
    await interaction.response.send_message(embed=embed, ephemeral=not public)


@bot.tree.command(name="networth", description="Full net worth breakdown.")
@app_commands.describe(user="Optional: view another player's public net worth")
async def networth(interaction: discord.Interaction, user: discord.User | None = None):
    target = user or interaction.user
    tid = SUID(interaction, target.id)
    t = db.get_user(tid)
    public = bool(t.get("networth_public", 0))
    if target.id != interaction.user.id and not public:
        await interaction.response.send_message(
            f"🔒 **{(await display_name(interaction, tid))}'s** net worth is private.",
            ephemeral=True)
        return
    u = db.get_user(tid)
    _, iv = db.inventory_value(tid)
    _, vv = db.vault_value(tid)
    mv = db.market_assets(tid)
    bank = u.get("bank_balance", 0)
    total = u["balance"] + bank + iv + vv + mv
    name = await display_name(interaction, tid)
    embed = discord.Embed(title=f"💎 {name}'s Net Worth: **${total:,}**", color=0x9C27B0)
    embed.add_field(name="💵 Wallet", value=f"${u['balance']:,}", inline=True)
    embed.add_field(name="🏦 Bank", value=f"${bank:,}", inline=True)
    embed.add_field(name="🎒 Inventory assets", value=f"${iv:,}", inline=True)
    embed.add_field(name="🗝️ Vault assets", value=f"${vv:,}", inline=True)
    embed.add_field(name="🏪 Market assets", value=f"${mv:,}", inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=not public)


class BankTransferModal(discord.ui.Modal, title="Bank transfer"):
    amount = discord.ui.TextInput(label="How much? (number or ALL)", placeholder="e.g. 500 or ALL",
                                  max_length=12)

    def __init__(self, owner_id: int, direction: str):
        super().__init__()
        self.owner_id = owner_id
        self.direction = direction  # to_bank | to_balance

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("That's not yours!", ephemeral=True)
            return
        uid = SUID(interaction, self.owner_id)
        u = db.get_user(uid)
        raw = str(self.amount.value).strip().lower()
        if self.direction == "to_bank":
            avail, col = u["balance"], "balance"
        else:
            avail, col = u.get("bank_balance", 0), "bank"
        if raw in ("all", "max"):
            n = avail
        else:
            try:
                n = int(raw)
            except ValueError:
                await interaction.response.send_message("❌ Type a number or ALL.", ephemeral=True)
                return
        n = max(0, min(n, avail))
        if n <= 0:
            await interaction.response.send_message("❌ Nothing to move.", ephemeral=True)
            return
        if self.direction == "to_bank":
            db.update_user(uid, balance=u["balance"] - n,
                           bank_balance=u.get("bank_balance", 0) + n)
            await interaction.response.send_message(f"🏦 Moved **${n:,}** to your bank!",
                                                    ephemeral=True)
        else:
            db.update_user(uid, balance=u["balance"] + n,
                           bank_balance=u.get("bank_balance", 0) - n)
            await interaction.response.send_message(f"💵 Withdrew **${n:,}** to your wallet!",
                                                    ephemeral=True)





@bot.tree.command(name="bank", description="See banks (yours, or a public one).")
@app_commands.describe(user="Optional: view another player's public bank")
async def bank(interaction: discord.Interaction, user: discord.User | None = None):
    target = user or interaction.user
    tid = SUID(interaction, target.id)
    t = db.get_user(tid)
    public = bool(t.get("bank_public", 0))
    if target.id != interaction.user.id and not public:
        await interaction.response.send_message(
            f"🔒 **{(await display_name(interaction, tid))}'s** bank is private.", ephemeral=True)
        return
    t = db.get_user(tid)
    name = await display_name(interaction, tid)
    embed = discord.Embed(title=f"🏦 {name}'s Bank", color=0x3F51B5)
    embed.add_field(name="💰 Stored", value=f"**${t.get('bank_balance', 0):,}**")
    view = BankView(interaction.user.id, target.id)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=not public)


class BankView(discord.ui.View):
    def __init__(self, viewer_id: int, target_id: int):
        super().__init__(timeout=180)
        self.viewer_id = viewer_id
        self.target_id = target_id
        if viewer_id != target_id:
            self.withdraw.disabled = True
            self.deposit.disabled = True

    @discord.ui.button(label="Withdraw", style=discord.ButtonStyle.green, emoji="💵")
    async def withdraw(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.viewer_id or self.viewer_id != self.target_id:
            await interaction.response.send_message("That's not yours!", ephemeral=True)
            return
        await interaction.response.send_modal(BankTransferModal(self.viewer_id, "to_balance"))

    @discord.ui.button(label="Deposit", style=discord.ButtonStyle.primary, emoji="🏦")
    async def deposit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.viewer_id or self.viewer_id != self.target_id:
            await interaction.response.send_message("That's not yours!", ephemeral=True)
            return
        await interaction.response.send_modal(BankTransferModal(self.viewer_id, "to_bank"))


@bot.tree.command(name="inventory", description="See inventories (yours, or a public one).")
@app_commands.describe(user="Optional: view another player's public inventory")
async def inventory(interaction: discord.Interaction, user: discord.User | None = None):
    target = user or interaction.user
    tid = SUID(interaction, target.id)
    t = db.get_user(tid)
    public = bool(t.get("inv_public", 0))
    if target.id != interaction.user.id and not public:
        await interaction.response.send_message(
            f"🔒 **{(await display_name(interaction, tid))}'s** inventory is private.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=not public)
    if db.count_inventory(tid) == 0:
        await interaction.followup.send("🎒 Inventory is empty!", ephemeral=not public)
        return
    name = await display_name(interaction, tid)
    view = InvBrowser(interaction.user.id, target.id, name, public, source="inv",
                      guild=guild_scope(interaction))
    await interaction.followup.send(embed=view.render(), view=view, ephemeral=not public)


@bot.tree.command(name="quicksell_all", description="Sell your ENTIRE inventory instantly.")
async def quicksell_all(interaction: discord.Interaction):
    await interaction.response.defer()
    uid = SUID(interaction)
    items = db.get_inventory_grouped(uid)
    if not items:
        await interaction.followup.send("Inventory is already empty!")
        return
    earned = sum(config.quicksell_value(t["rarity"]) * t["count"] for t in items)
    count = sum(t["count"] for t in items)
    db.clear_stacks(uid, items)  # batched: one statement per stack
    u = db.get_user(uid)
    db.update_user(uid, balance=u["balance"] + earned, total_earned=u["total_earned"] + earned)
    newly = check_achievements(uid, db.get_user(uid), "", "")
    if count >= 10 and db.grant_achievement(uid, "wholesaler"):
        newly.append(f"🏆 **{config.ACHIEVEMENTS['wholesaler'][0]}** - {config.ACHIEVEMENTS['wholesaler'][1]}")
    if count >= 100 and db.grant_achievement(uid, "mass_seller"):
        newly.append(f"🏆 **{config.ACHIEVEMENTS['mass_seller'][0]}** - {config.ACHIEVEMENTS['mass_seller'][1]}")
    sync_collectors(uid)
    result_msg = await interaction.followup.send(f"💸 Sold **{count}** ores for **${earned:,}**! Wallet: **${u['balance'] + earned:,}**.")
    if newly:
        await achievement_reply(interaction, interaction.user.mention, newly,
                                ref_message=result_msg)


# ----- market -----

# ---------- market listing (amount first, then price) ----------

class ListAmountModal(discord.ui.Modal, title="List ores"):
    amount = discord.ui.TextInput(label="How many? (number or ALL)", placeholder="e.g. 3 or ALL",
                                  max_length=8)
    price = discord.ui.TextInput(label="Price per ore ($)", placeholder="e.g. 500",
                                 max_length=12)

    def __init__(self, owner_id: int, ore: str | None, quality: str | None,
                 tier: str | None = None, bview=None, browser_message=None):
        super().__init__()
        self.owner_id = owner_id
        self.ore = ore
        self.quality = quality
        self.tier = tier
        self.bview = bview
        self.browser_message = browser_message

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("That's not yours!", ephemeral=True)
            return
        uid = SUID(interaction, self.owner_id)
        gid = guild_scope(interaction)
        targets = _scope_targets(uid, "inv", self.ore, self.quality, self.tier)
        total = sum(t["count"] for t in targets)
        if total <= 0:
            await interaction.response.send_message("❌ Nothing to list.", ephemeral=True)
            return
        raw = str(self.amount.value).strip().lower()
        if raw in ("all", "max"):
            n = total
        else:
            try:
                n = int(raw)
            except ValueError:
                await interaction.response.send_message("❌ Amount must be a number or ALL.",
                                                        ephemeral=True)
                return
        n = max(1, min(n, total))
        try:
            price = int(str(self.price.value).replace(",", "").replace("$", "").strip())
        except ValueError:
            await interaction.response.send_message("❌ Price must be a whole number.",
                                                    ephemeral=True)
            return
        if price < 1:
            await interaction.response.send_message("❌ Price must be at least $1.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        made = db.market_list_many(gid, uid, targets, price, n)
        if made > 0:
            uu = db.get_user(uid)
            db.update_user(uid, market_put_count=uu.get("market_put_count", 0) + made)
            newly_put = check_achievements(uid, db.get_user(uid), "", "")
            if newly_put:
                await achievement_reply(interaction, interaction.user.mention, newly_put)
        sync_collectors(uid)
        if self.bview is not None and self.browser_message is not None:
            try:
                v = self.bview
                fresh = InvBrowser(v.viewer_id, v.target_id, v.target_name, v.public,
                                   source=v.source, quality=v.quality, ore=v.ore, tier=v.tier,
                                   guild=v.guild, recip_id=v.recip_id, recip_name=v.recip_name,
                                   trade_tid=v.trade_tid, trade_side=v.trade_side)
                await self.browser_message.edit(embed=fresh.render(), view=fresh)
            except Exception:
                pass
        await interaction.followup.send(
            f"📦 Listed **{made}x** for **${price:,}** each!", ephemeral=True)



@bot.tree.command(name="market_list", description="List your ores (pick + amount + price).")
async def market_list(interaction: discord.Interaction):
    uid = SUID(interaction)
    if db.count_inventory(uid) == 0:
        await interaction.response.send_message("🎒 Your inventory is empty! Use `/spin` first.",
                                                ephemeral=True)
        return
    view = InvBrowser(interaction.user.id, interaction.user.id,
                      interaction.user.display_name, False, source="list",
                      guild=guild_scope(interaction))
    await interaction.response.send_message(embed=view.render(), view=view, ephemeral=True)


PAGE_SIZE = 10
BROWSE_LIMIT = 50  # per stage; pages flip through these 10 at a time


async def seller_name(interaction: discord.Interaction, seller_id: str) -> str:
    """Seller display name (always shown)."""
    return await display_name(interaction, seller_id)


async def format_listings(interaction: discord.Interaction, listings: list[dict]) -> list[str]:
    lines = []
    for l in listings:
        seller = await seller_name(interaction, l["seller_id"])
        lines.append(f"`{l['id']}` **{l['ore']}** - **${l['price']:,}** - {seller}")
    return lines


async def render_market(owner_id: int, interaction: discord.Interaction,
                        ore: str | None = None, quality: str | None = None,
                        sort: str | None = None, page: int = 0, tier: str | None = None):
    """One renderer for every market stage. Returns (embed, view)."""
    guild = guild_scope(interaction)
    all_listings = db.market_browse(guild, ore=ore, quality=quality, sort=sort or "new",
                                    limit=BROWSE_LIMIT, tier=tier)
    pages = max(1, (len(all_listings) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = page % pages
    chunk = all_listings[page * PAGE_SIZE:page * PAGE_SIZE + PAGE_SIZE]
    lines = await format_listings(interaction, chunk)
    scope = " - ".join(x for x in (
        f"{ore}" if ore else None,
        f"({quality})" if quality else None,
        config.tier_name(tier) if tier else None) if x)
    if not sort or sort == "new":
        title = f"🏪 {scope + ' - ' if scope else ''}latest"
    else:
        label = {"cheapest": "cheapest", "average": "closest to average",
                 "expensive": "most expensive"}[sort]
        title = f"🏪 {scope + ' - ' if scope else ''}{label}"
    if pages > 1:
        title += f" (page {page + 1}/{pages})"
    embed = discord.Embed(title=title, description="\n".join(lines) if lines else "Sold out!",
                          color=0x9C27B0)
    embed.set_footer(text="Inspect a listing, then hit Buy • flip pages with ◀ ▶")
    return embed, MarketBrowser(owner_id, ore=ore, quality=quality, sort=sort,
                                page=page, pages=pages, chunk=chunk, tier=tier,
                                guild=guild_scope(interaction))


class MarketTierSelect(discord.ui.Select):
    def __init__(self, owner_id: int, current: str | None = None):
        self.owner_id = owner_id
        super().__init__(placeholder="Filter by tier…", options=[
            discord.SelectOption(label="All tiers", value="all", default=(current is None))
        ] + [discord.SelectOption(label=config.tier_name(r), value=r,
                                   default=(r == current),
                                   emoji=config.RARITIES[r].get("dot", ""))
             for r in config.RARITIES])

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Use `/market_view` to browse yourself!", ephemeral=True)
            return
        view = self.view
        view.tier = None if self.values[0] == "all" else self.values[0]
        # changing tier resets ore (ore may not exist in that tier); quality stays
        if view.ore and view.tier:
            ores = db.market_ores_with_rarity(view.guild)
            match = next((o for o in ores if o["ore"] == view.ore), None)
            if not match or view.tier not in match["rarities"]:
                view.ore = None
        embed, view2 = await render_market(view.owner_id, interaction, ore=view.ore,
                                           quality=view.quality, sort=view.sort, page=0,
                                           tier=view.tier)
        await interaction.response.edit_message(embed=embed, view=view2)


class MarketQualityTopSelect(discord.ui.Select):
    """Stage 0 second dropdown: filter by quality across ALL ores."""

    def __init__(self, owner_id: int, current: str | None = None, tier: str | None = None):
        self.owner_id = owner_id
        self.tier = tier
        super().__init__(placeholder="Filter by quality…", options=[
            discord.SelectOption(label="All qualities", value="all", default=(current is None))
        ] + [discord.SelectOption(label=q, value=q, default=(q == current))
             for q in config.QUALITIES])

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Use `/market_view` to browse yourself!", ephemeral=True)
            return
        view = self.view
        view.quality = None if self.values[0] == "all" else self.values[0]
        embed, view2 = await render_market(view.owner_id, interaction, ore=view.ore,
                                           quality=view.quality, sort=view.sort, page=0,
                                           tier=view.tier)
        await interaction.response.edit_message(embed=embed, view=view2)


class MarketOreFilterSelect(discord.ui.Select):
    def __init__(self, owner_id: int, guild: str, current: str | None = None, tier: str | None = None):
        self.owner_id = owner_id
        self.tier = tier
        ores = db.market_ores_with_rarity(guild)
        if tier:
            ores = [o for o in ores if tier in o["rarities"]]
        # lowest tier first
        ores.sort(key=lambda o: min(config.tier_index(r) for r in o["rarities"]))
        options = [discord.SelectOption(label="All ores", value="all", default=(current is None))]
        for o in ores[:24]:
            options.append(discord.SelectOption(
                label=f"{o['ore']}"[:100], value=o["ore"],
                default=(o["ore"] == current)))
        super().__init__(placeholder="Filter by ore…", options=options or [
            discord.SelectOption(label="(no listings)", value="none")])

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Use `/market_view` to browse yourself!", ephemeral=True)
            return
        if self.values[0] == "none":
            return
        view = self.view
        if self.values[0] == "all":
            view.ore = None
        else:
            view.ore = self.values[0]
            view.quality = None  # reset quality (may not exist for the new ore)
            # auto-match tier to the picked ore
            ores = db.market_ores_with_rarity(view.guild)
            match = next((o for o in ores if o["ore"] == view.ore), None)
            if match and match["rarities"]:
                view.tier = sorted(match["rarities"], key=config.tier_index)[0]
        embed, view2 = await render_market(view.owner_id, interaction, ore=view.ore,
                                           quality=view.quality, sort=view.sort, page=0,
                                           tier=view.tier)
        await interaction.response.edit_message(embed=embed, view=view2)


class MarketQualitySelect(discord.ui.Select):
    """One quality dropdown for every stage (works with or without an ore)."""

    def __init__(self, owner_id: int, guild: str, ore: str | None = None, current: str | None = None,
                 tier: str | None = None):
        self.owner_id = owner_id
        self.ore = ore
        self.tier = tier
        quals = db.market_qualities(guild, ore) if ore else list(config.QUALITIES.keys())
        options = [discord.SelectOption(label="All qualities", value="all",
                                        default=(current is None))]
        options += [discord.SelectOption(label=q, value=q, default=(q == current))
                    for q in quals[:24]]
        super().__init__(placeholder="Filter by quality…", options=options or [
            discord.SelectOption(label="(none)", value="none")])

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Use `/market_view` to browse yourself!", ephemeral=True)
            return
        if self.values[0] == "none":
            return
        view = self.view
        view.quality = None if self.values[0] == "all" else self.values[0]
        embed, view2 = await render_market(view.owner_id, interaction,
                                           ore=view.ore, quality=view.quality,
                                           sort=view.sort, page=0, tier=view.tier)
        await interaction.response.edit_message(embed=embed, view=view2)


class MarketQualityFilterSelect(discord.ui.Select):
    def __init__(self, owner_id: int, ore: str, guild: str, current: str | None = None,
                 tier: str | None = None):
        self.owner_id = owner_id
        self.ore = ore
        self.tier = tier
        quals = db.market_qualities(guild, ore)
        options = [discord.SelectOption(label=q, value=q, default=(q == current))
                   for q in quals[:25]]
        super().__init__(placeholder="Filter by quality…", options=options or [
            discord.SelectOption(label="(none)", value="none")])

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Use `/market_view` to browse yourself!", ephemeral=True)
            return
        if self.values[0] == "none":
            return
        embed, view = await render_market(self.owner_id, interaction,
                                          ore=self.ore, quality=self.values[0], tier=self.tier)
        await interaction.response.edit_message(embed=embed, view=view)


class MarketSortSelect(discord.ui.Select):
    def __init__(self, owner_id: int, ore: str, quality: str, current: str | None,
                 tier: str | None = None):
        self.owner_id = owner_id
        self.ore = ore
        self.quality = quality
        self.tier = tier
        super().__init__(placeholder="Sort…", options=[
            discord.SelectOption(label="Latest", value="new", emoji="🆕",
                                 default=(current in (None, "new"))),
            discord.SelectOption(label="Cheapest", value="cheapest", emoji="💲",
                                 default=(current == "cheapest")),
            discord.SelectOption(label="Closest to average", value="average", emoji="📊",
                                 default=(current == "average")),
            discord.SelectOption(label="Most expensive", value="expensive", emoji="💎",
                                 default=(current == "expensive")),
        ])

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Use `/market_view` to browse yourself!", ephemeral=True)
            return
        view = self.view
        view.sort = self.values[0]
        embed, view2 = await render_market(view.owner_id, interaction, ore=view.ore,
                                           quality=view.quality, sort=view.sort, page=0,
                                           tier=view.tier)
        await interaction.response.edit_message(embed=embed, view=view2)


class MarketListingInspectSelect(discord.ui.Select):
    """Pick one of the shown listings to inspect it."""

    def __init__(self, listings: list[dict]):
        options = []
        for l in listings[:25]:
            label = f"{l['ore']} - ${l['price']:,}"[:100]
            desc = f"{l['quality']} • {config.tier_name(l['rarity'])} • ID {l['id']}"[:100]
            options.append(discord.SelectOption(label=label, description=desc,
                                                value=str(l["id"])))
        super().__init__(placeholder="Inspect a listing…", options=options or [
            discord.SelectOption(label="(none)", value="none")])

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            return
        try:
            self.view.selected_id = int(self.values[0])
        except Exception:
            pass
        listing = db.market_get(int(self.values[0]))
        if listing is None:
            await interaction.response.send_message("❌ That listing just sold!", ephemeral=True)
            return
        seller = await seller_name(interaction, listing["seller_id"])
        embed = _inspect_embed(listing["ore"], listing["rarity"], listing["quality"], 1,
                               0x9C27B0,
                               f"Price: **${listing['price']:,}**\nSeller: **{seller}**")
        # private inspect so only the person who clicked sees it
        await interaction.response.send_message(embed=embed, ephemeral=True)


class BuyAmountModal(discord.ui.Modal, title="Buy - how many?"):
    amount = discord.ui.TextInput(label="How many? (number or ALL)", placeholder="e.g. 2 or ALL",
                                  max_length=8)

    def __init__(self, buyer_id: int, ore: str, quality: str, unit_price: int):
        super().__init__()
        self.buyer_id = buyer_id
        self.ore = ore
        self.quality = quality
        self.unit_price = unit_price

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.buyer_id:
            await interaction.response.send_message("That's not yours!", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        raw = str(self.amount.value).strip().lower()
        gid = guild_scope(interaction)
        buyer = SUID(interaction, self.buyer_id)
        if raw in ("all", "max"):
            # how many can they afford at cheapest-first prices
            cands = db.market_browse(gid, ore=self.ore, quality=self.quality,
                                     sort="cheapest", limit=1000)
            cands = [l for l in cands if l["seller_id"] != buyer]
            running, n = 0, 0
            bu = db.get_user(buyer)
            for l in cands:
                if running + l["price"] > bu["balance"]:
                    break
                running += l["price"]
                n += 1
            n = max(n, 0)
        else:
            try:
                n = int(raw)
            except ValueError:
                await interaction.followup.send("❌ Type a number or ALL.", ephemeral=True)
                return
            n = max(0, n)
        if n <= 0:
            await interaction.followup.send("❌ Nothing to buy.", ephemeral=True)
            return
        bought = db.market_buy_many(gid, buyer, self.ore, self.quality, n)
        if bought is None:
            await interaction.followup.send("❌ Not enough money for that many.", ephemeral=True)
            return
        rows, total = bought
        if not rows:
            await interaction.followup.send("❌ Those just sold out!", ephemeral=True)
            return
        # seller side: merchant/supplier + silent checks + aggregated mail
        from collections import defaultdict
        per_seller = defaultdict(lambda: [0, 0])
        for l in rows:
            db.grant_achievement(l["seller_id"], "merchant")
            if l["rarity"] == "Mythical":
                db.grant_achievement(l["seller_id"], "supplier")
            check_achievements(l["seller_id"], db.get_user(l["seller_id"]), "", "")
            per_seller[l["seller_id"]][0] += 1
            per_seller[l["seller_id"]][1] += l["price"]
        for l in rows:
            if l.get("origin") == "market" and l["price"] > config.quicksell_value(l["rarity"]):
                if db.grant_achievement(l["seller_id"], "scalper"):
                    db.add_mail(l["seller_id"],
                                f"🏆 **{config.ACHIEVEMENTS['scalper'][0]}** - {config.ACHIEVEMENTS['scalper'][1]}")
        buyer_name = await display_name(interaction, buyer)
        for sid, (cnt, sub) in per_seller.items():
            db.add_mail(sid, f"💰 **{buyer_name}** bought **{cnt}x {self.ore} ({self.quality})** "
                             f"from you for **${sub:,}**!")
        # buyer side: counters + achievements
        bu = db.get_user(buyer)
        ups = {}
        if any(l["rarity"] == "Mythical" for l in rows):
            ups["dih_pulls"] = bu.get("dih_pulls", 0) + sum(1 for l in rows if l["rarity"] == "Mythical")
        if any(l["quality"] == "Perfect Condition" for l in rows):
            ups["perfect_pulls"] = bu.get("perfect_pulls", 0) + sum(1 for l in rows if l["quality"] == "Perfect Condition")
        if ups:
            db.update_user(buyer, **ups)
        db.grant_achievement(buyer, "customer")
        r0 = rows[0]
        newly = check_achievements(buyer, db.get_user(buyer), r0["rarity"], r0["quality"])
        if any(l["rarity"] == "Mythical" for l in rows) and db.grant_achievement(buyer, "investor"):
            newly.append(f"🏆 **{config.ACHIEVEMENTS['investor'][0]}** - {config.ACHIEVEMENTS['investor'][1]}")
        # rarest buy tracking
        try:
            qualities = list(config.QUALITIES.keys())
            best = max(rows, key=lambda l: (config.tier_index(l["rarity"]),
                                            qualities.index(l["quality"])))
            bu2 = db.get_user(buyer)
            cur = parse_best(bu2.get("rarest_buy", ""))
            cur_idx = (config.tier_index(cur[0]), qualities.index(cur[1])) if cur else (-1, -1)
            new_idx = (config.tier_index(best["rarity"]), qualities.index(best["quality"]))
            if new_idx > cur_idx:
                db.update_user(buyer, rarest_buy=f"{best['rarity']}|{best['quality']}|{best['ore']}")
        except Exception:
            pass
        msg = await interaction.followup.send(
            f"✅ {interaction.user.mention} bought **{len(rows)}x {self.ore} ({self.quality})** "
            f"for **${total:,}**!")
        if newly:
            await achievement_reply(interaction, interaction.user.mention, newly,
                                    ref_message=msg)


class MarketBrowser(discord.ui.View):
    """All dropdowns always up: tier + ore + quality + sort, inspect button, pages."""

    def __init__(self, owner_id: int, ore: str | None = None, quality: str | None = None,
                 sort: str | None = None, page: int = 0, pages: int = 1,
                 chunk: list[dict] | None = None, tier: str | None = None, guild: str = "DM"):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.ore = ore
        self.quality = quality
        self.sort = sort
        self.page = page
        self.pages = pages
        self.tier = tier
        self.guild = guild
        self.chunk = chunk or []
        self.add_item(MarketTierSelect(owner_id, current=tier))
        self.add_item(MarketOreFilterSelect(owner_id, guild, current=ore, tier=tier))
        self.add_item(MarketQualitySelect(owner_id, guild, ore=ore, current=quality, tier=tier))
        self.add_item(MarketSortSelect(owner_id, ore, quality, sort, tier=tier))
        if self.chunk:
            self.add_item(self._inspect_btn(disabled=False))

    def _inspect_btn(self, disabled: bool = False):
        view = self

        async def cb(interaction: discord.Interaction):
            pop = MarketInspectPopup(interaction.user.id, view.chunk)
            await interaction.response.send_message(
                content=f"🔍 Inspect - pick a listing ({len(view.chunk)} shown):",
                view=pop, ephemeral=True)
        btn = discord.ui.Button(label="Inspect", style=discord.ButtonStyle.secondary, emoji="🔍",
                                disabled=disabled)
        btn.callback = cb
        return btn
    async def _flip(self, interaction: discord.Interaction, delta: int):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Use `/market_view` to browse yourself!",
                                                    ephemeral=True)
            return
        embed, view = await render_market(self.owner_id, interaction, ore=self.ore,
                                          quality=self.quality, sort=self.sort,
                                          page=self.page + delta, tier=self.tier)
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="", emoji="◀", style=discord.ButtonStyle.secondary, row=4)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._flip(interaction, -1)

    @discord.ui.button(label="", emoji="▶", style=discord.ButtonStyle.secondary, row=4)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._flip(interaction, 1)




class MarketInspectPopup(discord.ui.View):
    """Ephemeral pop-up: pick a listing to inspect + Buy button for the picked one."""

    def __init__(self, viewer_id: int, listings: list[dict]):
        super().__init__(timeout=180)
        self.viewer_id = viewer_id
        self.listings = listings
        self.selected_id = listings[0]["id"] if listings else None
        self.add_item(MarketListingInspectSelect(listings))
        self.add_item(self._buy_btn())

    def _buy_btn(self):
        view = self

        async def cb(interaction: discord.Interaction):
            await view._do_buy(interaction)
        btn = discord.ui.Button(label="Buy", style=discord.ButtonStyle.green, emoji="🛒")
        btn.callback = cb
        return btn

    async def _do_buy(self, interaction: discord.Interaction):
        if interaction.user.id != self.viewer_id:
            await interaction.response.send_message("That's not yours!", ephemeral=True)
            return
        if not self.selected_id:
            await interaction.response.send_message("❌ Pick a listing first!", ephemeral=True)
            return
        listing = db.market_get(self.selected_id, guild_scope(interaction))
        if listing is None:
            await interaction.response.send_message("❌ That listing just sold!", ephemeral=True)
            return
        buyer = SUID(interaction, self.viewer_id)
        u = db.get_user(buyer)
        if u["balance"] < listing["price"]:
            await interaction.response.send_message(
                f"❌ You need ${listing['price']:,} but only have ${u['balance']:,}.",
                ephemeral=True)
            return
        ok, msg = db.market_buy(self.selected_id, buyer, guild_scope(interaction))
        if not ok:
            await interaction.response.send_message(f"❌ {msg}", ephemeral=True)
            return
        db.grant_achievement(listing["seller_id"], "merchant")
        if listing["rarity"] == "Mythical":
            db.grant_achievement(listing["seller_id"], "supplier")
        if listing.get("origin") == "market" and listing["price"] > config.quicksell_value(listing["rarity"]):
            if db.grant_achievement(listing["seller_id"], "scalper"):
                db.add_mail(listing["seller_id"],
                            f"🏆 **{config.ACHIEVEMENTS['scalper'][0]}** - {config.ACHIEVEMENTS['scalper'][1]}")
        check_achievements(listing["seller_id"], db.get_user(listing["seller_id"]), "", "")
        buyer_name = await display_name(interaction, buyer)
        db.add_mail(listing["seller_id"],
                    f"💰 **{buyer_name}** bought your **{listing['ore']} ({listing['quality']})** "
                    f"for **${listing['price']:,}**!")
        bu = db.get_user(buyer)
        _bump_obtained(buyer, bu, listing["rarity"], listing["quality"])
        db.grant_achievement(buyer, "customer")
        newly = check_achievements(buyer, db.get_user(buyer), listing["rarity"], listing["quality"])
        if listing["rarity"] == "Mythical" and db.grant_achievement(buyer, "investor"):
            newly.append(f"🏆 **{config.ACHIEVEMENTS['investor'][0]}** - {config.ACHIEVEMENTS['investor'][1]}")
        # rarest buy tracking
        try:
            qualities = list(config.QUALITIES.keys())
            bu2 = db.get_user(buyer)
            new_idx = (config.tier_index(listing["rarity"]), qualities.index(listing["quality"]))
            cur = parse_best(bu2.get("rarest_buy", ""))
            cur_idx = (config.tier_index(cur[0]), qualities.index(cur[1])) if cur else (-1, -1)
            if new_idx > cur_idx:
                db.update_user(buyer, rarest_buy=f"{listing['rarity']}|{listing['quality']}|{listing['ore']}")
        except Exception:
            pass
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        try:
            result = await interaction.response.send_message(
                f"✅ {interaction.user.mention} bought **{listing['quality']} {listing['ore']}** "
                f"for **${listing['price']:,}**!")
        except Exception:
            result = None
        try:
            if result is not None:
                await interaction.message.edit(view=self)
        except Exception:
            pass
        if newly:
            await achievement_reply(interaction, interaction.user.mention, newly)

@bot.tree.command(name="market_view", description="Browse the player market (latest + filters).")
async def market_view(interaction: discord.Interaction):
    await interaction.response.defer()
    if not db.market_browse(guild_scope(interaction), limit=1):
        await interaction.followup.send("📭 Market is empty! Be the first with `/market_list`.")
        return
    embed, view = await render_market(interaction.user.id, interaction)
    await interaction.followup.send(embed=embed, view=view)


@bot.tree.command(name="market_cancel", description="Cancel your listings (browse + filters).")
async def market_cancel(interaction: discord.Interaction):
    uid = SUID(interaction)
    if not db.market_by_seller(uid, limit=1):
        await interaction.response.send_message("📭 You have no listings! Make one with `/market_list`.",
                                                ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    embed, view = await render_cancel_browser(interaction.user.id, interaction)
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)


@bot.tree.command(name="market_cancel_all", description="Cancel ALL your listings (items return to you).")
async def market_cancel_all(interaction: discord.Interaction):
    n = db.market_cancel_all(SUID(interaction))
    if n == 0:
        await interaction.response.send_message("📭 You have no listings!", ephemeral=True)
    else:
        await interaction.response.send_message(
            f"🚫 Cancelled **{n}** listing(s) - items returned to your inventory.", ephemeral=True)


def cancel_format(listings: list[dict]) -> list[str]:
    return [f"`{l['id']}` **{l['ore']}** - **${l['price']:,}**" for l in listings]


async def render_cancel_browser(owner_id: int, interaction: discord.Interaction,
                                 ore: str | None = None, quality: str | None = None,
                                 page: int = 0, tier: str | None = None):
    """Cancel browser mirroring market_list: tier/ore/quality filters + inspect + pages."""
    scope = SUID(interaction, owner_id)
    all_listings = db.market_by_seller(scope, ore, quality, limit=1000, tier=tier)
    all_listings.sort(key=lambda l: -l["id"])
    pages = max(1, (len(all_listings) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = page % pages
    chunk = all_listings[page * PAGE_SIZE:page * PAGE_SIZE + PAGE_SIZE]
    scope_txt = " - ".join(x for x in (
        f"{ore}" if ore else None,
        f"({quality})" if quality else None,
        config.tier_name(tier) if tier else None) if x)
    title = f"🚫 Your listings"
    if scope_txt:
        title += f" - {scope_txt}"
    if pages > 1:
        title += f" (page {page + 1}/{pages})"
    embed = discord.Embed(title=title,
                          description="\n".join(cancel_format(chunk)) if chunk else "Nothing here!",
                          color=0xF44336)
    embed.set_footer(text="Inspect a listing, then hit Cancel")
    return embed, CancelBrowser(owner_id, scope, ore=ore, quality=quality,
                                page=page, pages=pages, chunk=chunk, tier=tier)


class CancelTierSelect(discord.ui.Select):
    def __init__(self, owner_id: int, scope: str, current: str | None = None):
        self.owner_id = owner_id
        self.scope = scope
        super().__init__(placeholder="Filter by tier…", options=[
            discord.SelectOption(label="All tiers", value="all", default=(current is None))
        ] + [discord.SelectOption(label=config.tier_name(r), value=r,
                                   default=(r == current),
                                   emoji=config.RARITIES[r].get("dot", ""))
             for r in config.RARITIES])

    async def callback(self, interaction: discord.Interaction):
        view: CancelBrowser = self.view
        if interaction.user.id != view.owner_id:
            await interaction.response.send_message("That's not yours!", ephemeral=True)
            return
        view.tier = None if self.values[0] == "all" else self.values[0]
        view.ore = None
        embed, view2 = await render_cancel_browser(view.owner_id, interaction, tier=view.tier)
        await interaction.response.edit_message(embed=embed, view=view2)


class CancelOreSelect(discord.ui.Select):
    def __init__(self, owner_id: int, scope: str, current: str | None = None,
                 tier: str | None = None):
        self.owner_id = owner_id
        self.scope = scope
        self.tier = tier
        ores = db.market_seller_ores(scope)
        if tier:
            ores = [o for o in ores if tier in o["rarities"]]
        ores.sort(key=lambda o: min(config.tier_index(r) for r in o["rarities"]))
        options = [discord.SelectOption(label="All ores", value="all",
                                        default=(current is None))]
        for o in ores[:24]:
            options.append(discord.SelectOption(label=f"{o['ore']}"[:100], value=o["ore"],
                                                default=(o["ore"] == current)))
        super().__init__(placeholder="Filter by ore…", options=options or [
            discord.SelectOption(label="(no listings)", value="none")])

    async def callback(self, interaction: discord.Interaction):
        view: CancelBrowser = self.view
        if interaction.user.id != view.owner_id:
            await interaction.response.send_message("That's not yours!", ephemeral=True)
            return
        if self.values[0] == "none":
            return
        view.ore = None if self.values[0] == "all" else self.values[0]
        view.quality = None
        ores = db.market_seller_ores(view.scope)
        if view.tier:
            ores = [o for o in ores if view.tier in o["rarities"]]
        if view.ore:
            match = next((o for o in ores if o["ore"] == view.ore), None)
            if match and match["rarities"]:
                view.tier = sorted(match["rarities"], key=config.tier_index)[0]
        embed, view2 = await render_cancel_browser(view.owner_id, interaction, ore=view.ore,
                                                    quality=view.quality,
                                                    tier=view.tier)
        await interaction.response.edit_message(embed=embed, view=view2)


class CancelQualitySelect(discord.ui.Select):
    def __init__(self, owner_id: int, scope: str, ore: str | None = None,
                 current: str | None = None, tier: str | None = None):
        self.owner_id = owner_id
        self.scope = scope
        self.ore = ore
        self.tier = tier
        if ore:
            quals = db.market_seller_qualities(scope, ore)
        else:
            quals = list(config.QUALITIES.keys())
        options = [discord.SelectOption(label="All qualities", value="all",
                                        default=(current is None))]
        options += [discord.SelectOption(label=q, value=q, default=(q == current))
                    for q in quals[:24]]
        super().__init__(placeholder="Filter by quality…", options=options or [
            discord.SelectOption(label="(none)", value="none")])

    async def callback(self, interaction: discord.Interaction):
        view: CancelBrowser = self.view
        if interaction.user.id != view.owner_id:
            await interaction.response.send_message("That's not yours!", ephemeral=True)
            return
        if self.values[0] == "none":
            return
        view.quality = None if self.values[0] == "all" else self.values[0]
        embed, view2 = await render_cancel_browser(view.owner_id, interaction, ore=view.ore,
                                                    quality=view.quality,
                                                    tier=view.tier)
        await interaction.response.edit_message(embed=embed, view=view2)


class CancelListingInspectSelect(discord.ui.Select):
    def __init__(self, listings: list[dict]):
        options = []
        for l in listings[:25]:
            label = f"{l['ore']} - ${l['price']:,}"[:100]
            desc = f"{l['quality']} • {config.tier_name(l['rarity'])} • ID {l['id']}"[:100]
            options.append(discord.SelectOption(label=label, description=desc,
                                                value=str(l["id"])))
        super().__init__(placeholder="Inspect a listing…", options=options or [
            discord.SelectOption(label="(none)", value="none")])

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            return
        view = self.view  # CancelInspectPopup
        if interaction.user.id != view.owner_id:
            await interaction.response.send_message("That's not yours!", ephemeral=True)
            return
        try:
            view.selected_id = int(self.values[0])
        except Exception:
            pass
        listing = db.market_get(int(self.values[0]))
        if listing is None or listing["seller_id"] != view.scope:
            await interaction.response.send_message("❌ That listing is gone!", ephemeral=True)
            return
        try:
            total_n = len(db.market_by_seller(view.scope, listing["ore"], listing["quality"],
                                             limit=100000, tier=listing["rarity"]))
        except Exception:
            total_n = 1
        total_n = max(1, total_n)
        embed = _inspect_embed(listing["ore"], listing["rarity"], listing["quality"],
                               total_n, 0xF44336)
        # info-only card; cancelling happens via the pop-up Cancel button
        await interaction.response.send_message(embed=embed, ephemeral=True)


class CancelAmountModal(discord.ui.Modal, title="Cancel listings"):
    amount = discord.ui.TextInput(label="How many? (number or ALL)", placeholder="e.g. 3 or ALL",
                                  max_length=8)

    def __init__(self, owner_id: int, ore: str | None, quality: str | None,
                 tier: str | None = None, browser_message=None):
        super().__init__()
        self.owner_id = owner_id
        self.ore = ore
        self.quality = quality
        self.tier = tier
        self.browser_message = browser_message

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("That's not yours!", ephemeral=True)
            return
        uid = SUID(interaction, self.owner_id)
        total = len(db.market_by_seller(uid, self.ore, self.quality, limit=100000,
                                        tier=self.tier))
        if total <= 0:
            await interaction.response.send_message("❌ Nothing to cancel.", ephemeral=True)
            return
        raw = str(self.amount.value).strip().lower()
        if raw in ("all", "max"):
            n = total
        else:
            try:
                n = int(raw)
            except ValueError:
                await interaction.response.send_message("❌ Amount must be a number or ALL.",
                                                        ephemeral=True)
                return
        n = max(1, min(n, total))
        await interaction.response.defer(ephemeral=True)
        done = db.market_cancel_many(uid, self.ore, self.quality, self.tier, n)
        await interaction.followup.send(
            f"🚫 Cancelled **{done}** listing(s) - items returned to your inventory.",
            ephemeral=True)
        if self.browser_message is not None:
            try:
                embed, view = await render_cancel_browser(
                    self.owner_id, interaction, ore=self.ore, quality=self.quality,
                    page=0, tier=self.tier)
                await self.browser_message.edit(embed=embed, view=view)
            except Exception:
                pass


class CancelInspectPopup(discord.ui.View):
    """Ephemeral pop-up: pick a listing to inspect + Cancel button for the picked one."""

    def __init__(self, owner_id: int, scope: str, listings: list[dict]):
        super().__init__(timeout=180)
        self.owner_id = owner_id
        self.scope = scope
        self.listings = listings
        self.selected_id = listings[0]["id"] if listings else None
        self.add_item(CancelListingInspectSelect(listings))
        self.add_item(self._cancel_btn())

    def _cancel_btn(self):
        view = self

        async def cb(interaction: discord.Interaction):
            if interaction.user.id != view.owner_id:
                await interaction.response.send_message("That's not yours!", ephemeral=True)
                return
            if not view.selected_id:
                await interaction.response.send_message("❌ Pick a listing first!",
                                                        ephemeral=True)
                return
            listing = db.market_get(view.selected_id)
            if listing is None or listing["seller_id"] != view.scope:
                await interaction.response.send_message("❌ That listing is gone!",
                                                        ephemeral=True)
                return
            ok, msg = db.market_cancel(view.selected_id, view.scope,
                                       guild_scope(interaction))
            for item in view.children:
                if isinstance(item, discord.ui.Button):
                    item.disabled = True
            try:
                await interaction.response.edit_message(view=view)
            except Exception:
                pass
            await interaction.followup.send(("🚫 " if ok else "❌ ") + msg, ephemeral=True)
        btn = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.danger, emoji="🗑️")
        btn.callback = cb
        return btn


class CancelBrowser(discord.ui.View):
    """Mirrors the market_list UI: tier + ore + quality, pages + Inspect, Cancel This."""

    def __init__(self, owner_id: int, scope: str, ore: str | None = None,
                 quality: str | None = None, page: int = 0, pages: int = 1,
                 chunk: list[dict] | None = None, tier: str | None = None):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.scope = scope
        self.ore = ore
        self.quality = quality
        self.page = page
        self.pages = pages
        self.tier = tier
        self.chunk = chunk or []
        self.add_item(CancelTierSelect(owner_id, scope, current=tier))
        self.add_item(CancelOreSelect(owner_id, scope, current=ore, tier=tier))
        self.add_item(CancelQualitySelect(owner_id, scope, ore=ore, current=quality, tier=tier))
        if self.chunk:
            self.add_item(self._inspect_btn())
        if pages > 1:
            self.add_item(self._prev_btn())
            self.add_item(self._next_btn())
        if self.tier and self.ore and self.quality:
            self.add_item(self._cancel_this_btn())

    def _cancel_this_btn(self):
        view = self

        async def cb(interaction: discord.Interaction):
            if interaction.user.id != view.owner_id:
                await interaction.response.send_message("That's not yours!", ephemeral=True)
                return
            await interaction.response.send_modal(
                CancelAmountModal(view.owner_id, view.ore, view.quality, view.tier,
                                  browser_message=interaction.message))
        btn = discord.ui.Button(label="Cancel this", style=discord.ButtonStyle.danger, emoji="🗑️",
                                row=3)
        btn.callback = cb
        return btn

    def _page_btn(self, delta: int, emoji: str):
        view = self

        async def cb(interaction: discord.Interaction):
            if interaction.user.id != view.owner_id:
                await interaction.response.send_message("That's not yours!", ephemeral=True)
                return
            await view._flip(interaction, delta)
        btn = discord.ui.Button(label="", emoji=emoji, style=discord.ButtonStyle.secondary, row=3)
        btn.callback = cb
        return btn

    def _prev_btn(self):
        return self._page_btn(-1, "◀")

    def _next_btn(self):
        return self._page_btn(1, "▶")

    def _inspect_btn(self):
        view = self

        async def cb(interaction: discord.Interaction):
            if interaction.user.id != view.owner_id:
                await interaction.response.send_message("That's not yours!", ephemeral=True)
                return
            pop = CancelInspectPopup(interaction.user.id, view.scope, view.chunk)
            await interaction.response.send_message(
                content=f"🔍 Inspect - pick a listing ({len(view.chunk)} shown):",
                view=pop, ephemeral=True)
        btn = discord.ui.Button(label="Inspect", style=discord.ButtonStyle.secondary, emoji="🔍",
                                row=3)
        btn.callback = cb
        return btn

    async def _flip(self, interaction: discord.Interaction, delta: int):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("That's not yours!", ephemeral=True)
            return
        embed, view = await render_cancel_browser(self.owner_id, interaction, ore=self.ore,
                                                  quality=self.quality,
                                                  page=self.page + delta, tier=self.tier)
        await interaction.response.edit_message(embed=embed, view=view)


# ----- stats / streak / achievements -----

@bot.tree.command(name="stats", description="See stats (yours, or a public one).")
async def stats(interaction: discord.Interaction, user: discord.User | None = None):
    target = user or interaction.user
    tid = SUID(interaction, target.id)
    t = db.get_user(tid)
    public = bool(t.get("stats_public", 0))
    if target.id != interaction.user.id and not public:
        await interaction.response.send_message(
            f"🔒 **{(await display_name(interaction, tid))}'s** stats are private.", ephemeral=True)
        return
    view = StatsView(interaction.user.id, tid, await display_name(interaction, tid))
    await interaction.response.send_message(embed=await view.make_embed(interaction), view=view,
                                            ephemeral=not public)


def parse_best(val: str) -> tuple[str, str, str] | None:
    """Stored global best looks like 'Mythical|Perfect|Painite'. Old data may be just a rarity."""
    if not val:
        return None
    parts = val.split("|")
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]
    return None


class StatsPageSelect(discord.ui.Select):
    def __init__(self, current: int):
        super().__init__(placeholder="Choose page…", options=[
            discord.SelectOption(label="Stats", value="0", emoji="📊", default=(current == 0)),
            discord.SelectOption(label="Rarest spin", value="1", emoji="✨", default=(current == 1)),
            discord.SelectOption(label="Rarest buy", value="2", emoji="🛒", default=(current == 2)),
        ])

    async def callback(self, interaction: discord.Interaction):
        view: StatsView = self.view
        if interaction.user.id != view.viewer_id:
            await interaction.response.send_message("Run `/stats` yourself to browse!",
                                                    ephemeral=True)
            return
        view.page = int(self.values[0])
        view.sync_page()
        await interaction.response.edit_message(embed=await view.make_embed(interaction), view=view)


class StatsView(discord.ui.View):
    """3 pages: stats, rarest spun inspect, rarest bought inspect (global bests, kept forever)."""

    def __init__(self, viewer_id: int, target_id: str, target_name: str):
        super().__init__(timeout=300)
        self.viewer_id = viewer_id
        self.target_id = target_id
        self.target_name = target_name
        self.page = 0
        self.add_item(StatsPageSelect(self.page))

    def sync_page(self):
        self.children[0].options = [
            discord.SelectOption(label="Stats", value="0", emoji="📊",
                                 default=(self.page == 0)),
            discord.SelectOption(label="Rarest spin", value="1", emoji="✨",
                                 default=(self.page == 1)),
            discord.SelectOption(label="Rarest buy", value="2", emoji="🛒",
                                 default=(self.page == 2)),
        ]

    async def make_embed(self, interaction: discord.Interaction) -> discord.Embed:
        u = db.get_user(self.target_id)
        if self.page == 0:
            inv_count = db.count_inventory(self.target_id)
            ach_n = len(db.get_achievements(self.target_id))
            ach_total = len(config.ACHIEVEMENTS)
            _, iv = db.inventory_value(self.target_id)
            _, vv = db.vault_value(self.target_id)
            mv = db.market_assets(self.target_id)
            bank = u.get("bank_balance", 0)
            networth = u["balance"] + bank + iv + vv + mv
            embed = discord.Embed(title=f"📊 {self.target_name}'s Stats", color=0x00BCD4)
            embed.description = (
                f"🎰 Total spins: **{u['total_spins']}**\n"
                f"🪨 Low Tier: **{u['low_pulls']}** | 💚 Mid Tier: **{u['mid_pulls']}** | 💎 High Tier: **{u['high_pulls']}**\n"
                f"👑 Elite Tier: **{u['elite_pulls']}** | 🌟 Mythical Tier: **{u['dih_pulls']}**\n"
                f"🔥 Streak: **{u['streak']}** days (best: **{u['longest_streak']}**)\n"
                f"💵 Wallet: **${u['balance']:,}** | Earned: **${u['total_earned']:,}**\n"
                f"🏦 Bank: **${bank:,}**\n"
                f"🎒 Inventory: **{inv_count}** ores (**${iv:,}** assets)\n"
                f"🗝️ Vault assets: **${vv:,}**\n"
                f"🏪 Market assets: **${mv:,}**\n"
                f"💎 Net worth: **${networth:,}**\n"
                f"🏆 Achievements: **{ach_n}/{ach_total}**")
        elif self.page == 1:
            embed = await self._best_embed(interaction, "✨ Rarest spun",
                                           u.get("rarest_spin", ""), market_bought=False)
        else:
            embed = await self._best_embed(interaction, "🛒 Rarest bought",
                                           u.get("rarest_buy", ""), market_bought=True)
        embed.set_footer(text=f"Page {self.page + 1}/3")
        return embed

    async def _best_embed(self, interaction: discord.Interaction, title: str,
                          stored: str, market_bought: bool) -> discord.Embed:
        triple = parse_best(stored)
        if triple is None:
            # legacy data or nothing yet: best currently-held item
            item = db.latest_rarest(self.target_id, market_bought=market_bought)
            if item is None:
                return discord.Embed(title=title, description="Nothing here yet!", color=0x9E9E9E)
            triple = (item["rarity"], item["quality"], item["ore"])
        rarity, quality, ore = triple
        return await inspect_text(interaction, self.target_id, rarity, quality, ore, 1)



@bot.tree.command(name="achievements", description="See achievements (yours, or a public one).")
@app_commands.describe(user="Optional: view another player's public achievements")
async def achievements(interaction: discord.Interaction, user: discord.User | None = None):
    target = user or interaction.user
    tid = SUID(interaction, target.id)
    t = db.get_user(tid)
    public = bool(t.get("ach_public", 0))
    if target.id != interaction.user.id and not public:
        await interaction.response.send_message(
            f"🔒 **{(await display_name(interaction, tid))}'s** achievements are private.",
            ephemeral=True)
        return
    unlocked = set(db.get_achievements(tid))
    view = AchievementsView(interaction.user.id, unlocked)
    await interaction.response.send_message(embed=view.make_embed(), view=view,
                                            ephemeral=not public)

class AchievementsScopeSelect(discord.ui.Select):
    def __init__(self, current: str):
        super().__init__(placeholder="Whose achievements…", options=[
            discord.SelectOption(label="My Achievements", value="mine",
                                 default=(current == "mine")),
            discord.SelectOption(label="Global Achievements", value="global",
                                 default=(current == "global")),
        ], row=0)
    async def callback(self, interaction: discord.Interaction):
        view: AchievementsView = self.view
        if interaction.user.id != view.owner_id:
            await interaction.response.send_message("That's not yours! Run `/achievements` yourself.",
                                                    ephemeral=True)
            return
        view.scope = self.values[0]
        view.page = 0
        view.refresh_items()
        view.sync_select()
        await interaction.response.edit_message(embed=view.make_embed(), view=view)


class AchievementsView(discord.ui.View):
    PER_PAGE = 10

    def __init__(self, owner_id: int, unlocked: set[str]):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.unlocked = unlocked
        self.page = 0
        self.scope = "mine"
        self.all_items = list(config.ACHIEVEMENTS.items())
        self.items = self.all_items
        self.pages = max(1, (len(self.items) + self.PER_PAGE - 1) // self.PER_PAGE)
        self.scope_select = AchievementsScopeSelect(self.scope)
        self.add_item(self.scope_select)

    def sync_select(self):
        """Rebuild dropdown options so the shown selection matches."""
        self.scope_select.options = [
            discord.SelectOption(label="My Achievements", value="mine",
                                 default=(self.scope == "mine")),
            discord.SelectOption(label="Global Achievements", value="global",
                                 default=(self.scope == "global")),
        ]

    def refresh_items(self):
        if self.scope == "global":
            counts, _total = db.achievement_counts()
            self.items = sorted(self.all_items,
                                key=lambda kv: (-counts.get(kv[0], 0), kv[1][0]))
        else:
            self.items = self.all_items
        self.pages = max(1, (len(self.items) + self.PER_PAGE - 1) // self.PER_PAGE)

    def make_embed(self) -> discord.Embed:
        chunk = self.items[self.page * self.PER_PAGE:(self.page + 1) * self.PER_PAGE]
        if self.scope == "global":
            counts, total = db.achievement_counts()
            lines = []
            for aid, (name, desc) in chunk:
                n = counts.get(aid, 0)
                pct = (100.0 * n / total) if total else 0.0
                mark = "✅" if aid in self.unlocked else "🔒"
                lines.append(f"{mark} **{name}** - {desc} {pct:.1f}%")
            embed = discord.Embed(
                title=f"🌍 Global Achievements ({len(self.unlocked)}/{len(self.all_items)}) % of players",
                description="\n".join(lines) if lines else "Nothing here!",
                color=0xFFD700)
        else:
            lines = [f"{'✅' if aid in self.unlocked else '🔒'} **{name}** - {desc}"
                     for aid, (name, desc) in chunk]
            embed = discord.Embed(
                title=f"🏆 Achievements ({len(self.unlocked)}/{len(self.all_items)})",
                description="\n".join(lines) if lines else "Nothing here!",
                color=0xFFD700)
        embed.set_footer(text=f"Page {self.page + 1}/{self.pages}")
        return embed

    async def _turn(self, interaction: discord.Interaction, delta: int):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("That's not yours! Run `/achievements` yourself.",
                                                    ephemeral=True)
            return
        self.page = (self.page + delta) % self.pages
        await interaction.response.edit_message(embed=self.make_embed(), view=self)

    @discord.ui.button(label="", emoji="◀", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._turn(interaction, -1)

    @discord.ui.button(label="", emoji="▶", style=discord.ButtonStyle.secondary, row=2)
    async def forward(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._turn(interaction, 1)


@bot.tree.command(name="odds", description="Show rarities, odds and values.")
async def odds(interaction: discord.Interaction):
    embed = discord.Embed(title="🎲 Odds & Values", color=0xFF9800)
    for r, ri in config.RARITIES.items():
        embed.add_field(
            name=f"{ri['emoji']} {config.tier_name(r)}",
            value=f"Quicksell: **${ri['value']:,}**\n"
                  f"Odds: **{ri['chance']:g}%** (1 in {config.rarity_one_in(ri['chance'])} chance)",
            inline=False,
        )
    q_lines = []
    for q, qi in config.QUALITIES.items():
        q_lines.append(f"{qi['emoji']} **{q}** - {qi['chance']}%")
    embed.add_field(name="✨ Qualities (rolled on every spin)", value="\n".join(q_lines), inline=False)
    embed.add_field(
        name="📊 How combo odds work",
        value="rarity% × quality% - e.g. Mythical Perfect = 0.1% × 1% = **0.001% (1 in 100,000)**",
        inline=False,
    )
    await interaction.response.send_message(embed=embed)


class OresTierSelect(discord.ui.Select):
    def __init__(self, current: str):
        super().__init__(placeholder="Pick a tier…", options=[
            discord.SelectOption(label=config.tier_name(r), value=r, default=(r == current),
                                 emoji=config.RARITIES[r].get("dot", ""))
            for r in config.RARITIES
        ])

    async def callback(self, interaction: discord.Interaction):
        view: OresView = self.view
        if interaction.user.id != view.owner_id:
            await interaction.response.send_message("Run `/ores` yourself to browse!", ephemeral=True)
            return
        view.tier = self.values[0]
        view.sync_select()
        await interaction.response.edit_message(embed=view.make_embed(), view=view)


class OresView(discord.ui.View):
    """Paged ore catalog. Starts on Low Tier. Flip pages or jump via dropdown."""

    def __init__(self, scope_uid: str):
        super().__init__(timeout=300)
        self.scope_uid = scope_uid
        self.owner_id = int(scope_uid.split(":")[-1])
        self.tiers = list(config.RARITIES.keys())
        self.tier = self.tiers[0]
        self.tier_select = OresTierSelect(self.tier)
        self.add_item(self.tier_select)

    def make_embed(self) -> discord.Embed:
        r = self.tier
        ri = config.RARITIES[r]
        idx = self.tiers.index(r)
        try:
            owned = {row["ore"] for row in db.get_inventory_grouped(self.scope_uid)}
            owned |= {row["ore"] for row in db.vault_grouped(self.scope_uid)}
        except Exception:
            owned = set()
        embed = discord.Embed(
            title=f"{ri['dot']} {config.tier_name(r)} Ores",
            description="\n".join(f"{'✅' if o in owned else '🔒'} **{o}**" for o in config.ORES[r]),
            color=ri["color"])
        embed.add_field(name="💰 Quicksell", value=f"${ri['value']:,} each", inline=True)
        embed.add_field(name="📊 Rarity odds",
                        value=f"{ri['chance']:g}% (1 in {config.rarity_one_in(ri['chance'])} chance)",
                        inline=True)
        embed.set_footer(text=f"Tier {idx + 1}/{len(self.tiers)}")
        return embed

    def sync_select(self):
        """Rebuild dropdown options so the shown selection always matches the page."""
        self.tier_select.options = [
            discord.SelectOption(label=config.tier_name(r), value=r, default=(r == self.tier),
                                 emoji=config.RARITIES[r].get("dot", ""))
            for r in config.RARITIES
        ]

    async def _turn(self, interaction: discord.Interaction, delta: int):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Run `/ores` yourself to browse!", ephemeral=True)
            return
        idx = self.tiers.index(self.tier)
        self.tier = self.tiers[(idx + delta) % len(self.tiers)]
        self.sync_select()
        await interaction.response.edit_message(embed=self.make_embed(), view=self)

    @discord.ui.button(emoji="◀", style=discord.ButtonStyle.secondary, row=1)
    async def prev_tier(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._turn(interaction, -1)

    @discord.ui.button(emoji="▶", style=discord.ButtonStyle.secondary, row=1)
    async def next_tier(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._turn(interaction, 1)


@bot.tree.command(name="ores", description="Browse every ore, tier by tier.")
@app_commands.describe(user="Optional: show another player's public checkmarks")
async def ores(interaction: discord.Interaction, user: discord.User | None = None):
    target = user or interaction.user
    tid = SUID(interaction, target.id)
    t = db.get_user(tid)
    public = bool(t.get("ores_public", 0))
    if target.id != interaction.user.id and not public:
        await interaction.response.send_message(
            f"🔒 **{(await display_name(interaction, tid))}'s** ore collection is private.",
            ephemeral=True)
        return
    view = OresView(tid)
    await interaction.response.send_message(embed=view.make_embed(), view=view,
                                            ephemeral=not public)


_name_cache: dict[int, str] = {}


async def display_name(interaction: discord.Interaction, user_id: str) -> str:
    """Plain name, never a ping. Accepts scoped ('guild:user') or raw ids."""
    try:
        uid = int(str(user_id).split(":")[-1])
    except (ValueError, TypeError):
        return str(user_id)
    if interaction.guild:
        m = interaction.guild.get_member(uid)
        if m:
            return m.display_name
    if uid in _name_cache:
        return _name_cache[uid]
    u = interaction.client.get_user(uid)
    if u:
        _name_cache[uid] = u.display_name
        return u.display_name
    try:
        u = await interaction.client.fetch_user(uid)
        _name_cache[uid] = u.display_name
        return u.display_name
    except Exception:
        return f"User {uid}"


@bot.tree.command(name="leaderboard", description="Top 10 richest players.")
async def leaderboard(interaction: discord.Interaction):
    await interaction.response.defer()
    view = LeaderboardView(interaction.user.id, guild_scope(interaction))
    embed = await view.make_embed(interaction)
    await interaction.followup.send(embed=embed, view=view)


class LeaderboardBoardSelect(discord.ui.Select):
    def __init__(self, current: str):
        super().__init__(placeholder="Choose leaderboard…", options=[
            discord.SelectOption(label="Money", value="money", emoji="💰",
                                 default=(current == "money")),
            discord.SelectOption(label="Spins", value="spins", emoji="🎰",
                                 default=(current == "spins")),
            discord.SelectOption(label="Net Worth", value="networth", emoji="💎",
                                 default=(current == "networth")),
        ])

    async def callback(self, interaction: discord.Interaction):
        view: LeaderboardView = self.view
        if interaction.user.id != view.owner_id:
            await interaction.response.send_message("Run `/leaderboard` yourself to browse!",
                                                    ephemeral=True)
            return
        view.page = {"money": 0, "spins": 1, "networth": 2}[self.values[0]]
        view.sync_board()
        await interaction.response.edit_message(embed=await view.make_embed(interaction), view=view)


class LeaderboardView(discord.ui.View):
    """Page 0: richest. Page 1: most spins. Page 2: net worth. Switched via dropdown."""

    def __init__(self, owner_id: int, guild: str):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.guild = guild
        self.page = 0
        self.add_item(LeaderboardBoardSelect("money"))

    async def make_embed(self, interaction: discord.Interaction) -> discord.Embed:
        import asyncio as _aio
        medals = ["🥇", "🥈", "🥉"]
        if self.page == 0:
            top = db.top_balances(self.guild, 10)
            if not top:
                return discord.Embed(title="💰 Richest Players",
                                     description="📭 Nobody has any money yet!",
                                     color=0xFFD700)
            names = await _aio.gather(*[display_name(interaction, r["user_id"]) for r in top])
            lines, top_ids = [], set()
            for i, row in enumerate(top):
                top_ids.add(row["user_id"])
                medal = medals[i] if i < 3 else f"`#{i+1}`"
                lines.append(f"{medal} **{names[i]}** - **${row['balance']:,}**")
            uid = f"{self.guild}:{interaction.user.id}"
            if uid not in top_ids:
                rank = db.get_rank(self.guild, uid)
                u = db.get_user(uid)
                name = await display_name(interaction, uid)
                lines.append(f"\n`#{rank}` **{name}** - **${u['balance']:,}**")
            embed = discord.Embed(title="💰 Richest Players", description="\n".join(lines),
                                  color=0xFFD700)
        elif self.page == 1:
            top = db.top_spinners(self.guild, 10)
            if not top:
                return discord.Embed(title="🎰 Most Spins",
                                     description="📭 Nobody has spun yet!",
                                     color=0x9E9E9E)
            names = await _aio.gather(*[display_name(interaction, r["user_id"]) for r in top])
            lines, top_ids = [], set()
            for i, row in enumerate(top):
                top_ids.add(row["user_id"])
                medal = medals[i] if i < 3 else f"`#{i+1}`"
                lines.append(f"{medal} **{names[i]}** - **{row['total_spins']:,}** spins")
            uid = f"{self.guild}:{interaction.user.id}"
            if uid not in top_ids:
                rank = db.get_spins_rank(self.guild, uid)
                u = db.get_user(uid)
                name = await display_name(interaction, uid)
                lines.append(f"\n`#{rank}` **{name}** - **{u['total_spins']:,}** spins")
            embed = discord.Embed(title="🎰 Most Spins", description="\n".join(lines),
                                  color=0x9E9E9E)
        else:
            top = db.top_networth(self.guild, 10)
            if not top:
                return discord.Embed(title="💎 Highest Net Worth",
                                     description="📭 Nothing to show yet!",
                                     color=0x9C27B0)
            names = await _aio.gather(*[display_name(interaction, r["user_id"]) for r in top])
            lines, top_ids = [], set()
            for i, row in enumerate(top):
                top_ids.add(row["user_id"])
                medal = medals[i] if i < 3 else f"`#{i+1}`"
                lines.append(f"{medal} **{names[i]}** - **${row['networth']:,}**")
            uid = f"{self.guild}:{interaction.user.id}"
            if uid not in top_ids:
                rank = db.get_networth_rank(self.guild, uid)
                name = await display_name(interaction, uid)
                lines.append(f"\n`#{rank}` **{name}** - **${db.net_worth(self.guild, uid):,}**")
            embed = discord.Embed(title="💎 Highest Net Worth", description="\n".join(lines),
                                  color=0x9C27B0)
        embed.set_footer(text=f"Page {self.page + 1}/3")
        return embed

    def sync_board(self):
        self.children[0].options = [
            discord.SelectOption(label="Money", value="money", emoji="💰",
                                 default=(self.page == 0)),
            discord.SelectOption(label="Spins", value="spins", emoji="🎰",
                                 default=(self.page == 1)),
            discord.SelectOption(label="Net Worth", value="networth", emoji="💎",
                                 default=(self.page == 2)),
        ]


@bot.tree.command(name="help", description="Show every command.")
async def help_cmd(interaction: discord.Interaction):
    await interaction.response.send_message(
        "🤖 **BoBot Commands**\n"
        "🎰 `/spin [amount]` - roll ores\n"
        "🎒 `/inventory [@user]` - browse ores, inspect, quicksell, vault\n"
        "🗝️ `/vault [@user]` - long-term storage, un-vault\n"
        "💸 `/quicksell_all` - sell your entire inventory instantly\n"
        "📦 `/market_list` - list ores (amount + price)\n"
        "🏪 `/market_view` - browse, filter, inspect, buy\n"
        "🚫 `/market_cancel` - take down listings\n"
        "🚫 `/market_cancel_all` - take down ALL listings\n"
        "🔄 `/trade @user` - trade ores (one-sided OK, both accept)\n"
        "🎁 `/gift ore @user` - gift ores (they get mail)\n"
        "🎁 `/gift money @user` - gift wallet/bank money\n"
        "💵 `/wallet [@user]` - wallet money\n"
        "🏦 `/bank [@user]` - bank + Deposit / Withdraw\n"
        "💎 `/networth [@user]` - wallet + bank + all assets\n"
        "💰 `/leaderboard` - money, spins + net worth boards\n"
        "📊 `/stats [@user]` - stats, rarest spin, rarest buy\n"
        "🏆 `/achievements [@user]` - badges (yours + global)\n"
        "🎲 `/odds` - rarity odds + values\n"
        "⛏️ `/ores [@user]` - every ore with your checkmarks\n"
        "📬 `/mail [@user]` - notifications\n"
        "⚙️ `/settings` - privacy + timezone\n"
        "❓ `/faq` - game guide (coming soon)\n"
        "❓ `/help` - this message",
        ephemeral=True,
    )


# ---------- mail + faq ----------

class MailView(discord.ui.View):
    PER_PAGE = 5

    def __init__(self, viewer_id: int, target_id: int, target_name: str = "Your"):
        super().__init__(timeout=300)
        self.viewer_id = viewer_id
        self.target_id = target_id
        self.target_name = target_name
        self.page = 0
        self.items = db.get_mail(str(target_id), limit=50)
        self.pages = max(1, (len(self.items) + self.PER_PAGE - 1) // self.PER_PAGE)
        if viewer_id != target_id:
            self.clear.disabled = True

    def make_embed(self) -> discord.Embed:
        chunk = self.items[self.page * self.PER_PAGE:(self.page + 1) * self.PER_PAGE]
        embed = discord.Embed(title=f"📬 {self.target_name}'s Mail ({len(self.items)})", color=0x00BCD4,
                              description="\n\n".join(
                                  f"{m['text']}\n-# {m['created_at']}" for m in chunk) or "No mail!")
        embed.set_footer(text=f"Page {self.page + 1}/{self.pages}")
        return embed

    async def _turn(self, interaction: discord.Interaction, delta: int):
        if interaction.user.id != self.viewer_id:
            await interaction.response.send_message("That's not yours!", ephemeral=True)
            return
        self.page = (self.page + delta) % self.pages
        await interaction.response.edit_message(embed=self.make_embed(), view=self)

    @discord.ui.button(emoji="◀", style=discord.ButtonStyle.secondary, row=1)
    async def prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._turn(interaction, -1)

    @discord.ui.button(emoji="▶", style=discord.ButtonStyle.secondary, row=1)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._turn(interaction, 1)

    @discord.ui.button(label="Clear all", style=discord.ButtonStyle.danger, emoji="🗑️", row=1)
    async def clear(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.viewer_id or self.viewer_id != self.target_id:
            await interaction.response.send_message("That's not yours!", ephemeral=True)
            return
        n = db.clear_mail(str(self.target_id))
        self.items, self.page = [], 0
        self.pages = 1
        await interaction.response.edit_message(
            content=f"🗑️ Cleared {n} message(s).", embed=None, view=None)


@bot.tree.command(name="mail", description="Read mail (yours, or a public one).")
@app_commands.describe(user="Optional: view another player's public mail")
async def mail(interaction: discord.Interaction, user: discord.User | None = None):
    target = user or interaction.user
    tid = SUID(interaction, target.id)
    t = db.get_user(tid)
    public = bool(t.get("mail_public", 0))
    if target.id != interaction.user.id and not public:
        await interaction.response.send_message(
            f"🔒 **{(await display_name(interaction, tid))}'s** mail is private.", ephemeral=True)
        return
    if target.id == interaction.user.id:
        db.mark_mail_read(tid)  # checking your mail clears the spin nudge
    view = MailView(interaction.user.id, tid, await display_name(interaction, tid))
    await interaction.response.send_message(embed=view.make_embed(), view=view,
                                            ephemeral=not public)


@bot.tree.command(name="faq", description="Coming soon.")
async def faq(interaction: discord.Interaction):
    embed = discord.Embed(title="❓ BoBot FAQ", description="Coming soon!",
                          color=0x607D8B)
    await interaction.response.send_message(embed=embed)


# ---------- settings ----------

TIMEZONES = [
    ("UTC", "UTC"),
    ("US Eastern", "America/New_York"),
    ("US Central", "America/Chicago"),
    ("US Mountain", "America/Denver"),
    ("US Pacific", "America/Los_Angeles"),
    ("US Alaska", "America/Anchorage"),
    ("US Hawaii", "Pacific/Honolulu"),
    ("UK", "Europe/London"),
    ("Central Europe", "Europe/Paris"),
    ("Eastern Europe", "Europe/Bucharest"),
    ("Brazil", "America/Sao_Paulo"),
    ("India", "Asia/Kolkata"),
    ("Singapore", "Asia/Singapore"),
    ("Japan/Korea", "Asia/Tokyo"),
    ("Australia East", "Australia/Sydney"),
    ("New Zealand", "Pacific/Auckland"),
]


def user_tzinfo(uid: str):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(db.get_user(uid).get("timezone") or "UTC")
    except Exception:
        return config.RESET_TIMEZONE


def user_today(uid: str) -> str:
    return datetime.now(user_tzinfo(uid)).date().isoformat()


def user_time_until_reset(uid: str) -> str:
    from datetime import timedelta as _td
    now = datetime.now(user_tzinfo(uid))
    tomorrow = (now + _td(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    delta = tomorrow - now
    h, rem = divmod(int(delta.total_seconds()), 3600)
    m, _s = divmod(rem, 60)
    return f"{h}h {m}m"


SETTING_DEFS = [
    ("inventory", "inv_public", "🎒 Inventory"),
    ("vault", "vault_public", "🗝️ Vault"),
    ("ores", "ores_public", "⛏️ Ores"),
    ("wallet", "balance_public", "💵 Wallet"),
    ("bank", "bank_public", "🏦 Bank"),
    ("networth", "networth_public", "💎 Net Worth"),
    ("stats", "stats_public", "📊 Stats"),
    ("achievements", "ach_public", "🏆 Achievements"),
    ("mail", "mail_public", "📬 Mail"),
    ("timezone", "timezone", "🕐 Timezone"),
]


def settings_embed(uid: str, selected: str) -> discord.Embed:
    u = db.get_user(uid)
    lines = []
    for key, col, label in SETTING_DEFS:
        if key == "timezone":
            state = f"**{u.get(col, 'UTC')}**"
        else:
            state = "🌍 Public" if u.get(col, 0) else "🔒 Private"
        mark = "▶ " if key == selected else ""
        lines.append(f"{mark}{label}: {state}")
    embed = discord.Embed(title="⚙️ Settings",
                          description="\n".join(lines) + "\n\nPublic = others can view it. "
                                      "Timezone sets when YOUR day resets.",
                          color=0x607D8B)
    return embed


class SettingSelect(discord.ui.Select):
    def __init__(self, selected: str):
        super().__init__(placeholder="Choose a setting…", options=[
            discord.SelectOption(label=label, value=key, default=(key == selected))
            for key, _, label in SETTING_DEFS
        ])

    async def callback(self, interaction: discord.Interaction):
        view: SettingsView = self.view
        if interaction.user.id != view.owner_id:
            await interaction.response.send_message("That's not yours! Run `/settings` yourself.",
                                                    ephemeral=True)
            return
        view.selected = self.values[0]
        view.sync_select()
        await interaction.response.edit_message(
            embed=settings_embed(SUID(interaction, view.owner_id), view.selected), view=view)


class ZoneSelect(discord.ui.Select):
    def __init__(self, current: str):
        super().__init__(placeholder="Pick your timezone…", options=[
            discord.SelectOption(label=label, value=tz, default=(tz == current))
            for label, tz in TIMEZONES
        ])

    async def callback(self, interaction: discord.Interaction):
        view: SettingsView = self.view
        if interaction.user.id != view.owner_id:
            await interaction.response.send_message("That's not yours! Run `/settings` yourself.",
                                                    ephemeral=True)
            return
        db.update_user(SUID(interaction, view.owner_id), timezone=self.values[0])
        view.sync_select()
        await interaction.response.edit_message(
            embed=settings_embed(SUID(interaction, view.owner_id), view.selected), view=view)


class SettingsView(discord.ui.View):
    def __init__(self, owner_id: int, scope_uid: str, tz: str):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.scope_uid = scope_uid
        self.selected = "inventory"
        self._rebuild(tz)

    def _rebuild(self, tz: str | None = None):
        self.clear_items()
        self.setting_select = SettingSelect(self.selected)
        self.add_item(self.setting_select)
        if self.selected == "timezone":
            if tz is None:
                tz = db.get_user(self.scope_uid).get("timezone", "UTC")
            self.add_item(ZoneSelect(tz))
        else:
            self.add_item(self._toggle_btn())

    def _toggle_btn(self):
        view = self

        async def cb(interaction: discord.Interaction):
            if interaction.user.id != view.owner_id:
                await interaction.response.send_message("That's not yours! Run `/settings` yourself.",
                                                        ephemeral=True)
                return
            uid = SUID(interaction, view.owner_id)
            col = next(c for k, c, _ in SETTING_DEFS if k == view.selected)
            u = db.get_user(uid)
            db.update_user(uid, **{col: 0 if u.get(col, 0) else 1})
            await interaction.response.edit_message(
                embed=settings_embed(uid, view.selected), view=view)
        btn = discord.ui.Button(label="Switch private/public",
                                style=discord.ButtonStyle.primary, emoji="🔄")
        btn.callback = cb
        return btn

    def sync_select(self):
        """Rebuild dropdown options so the shown selection matches."""
        self._rebuild()


@bot.tree.command(name="settings", description="Privacy + timezone settings.")
async def settings(interaction: discord.Interaction):
    uid = SUID(interaction)
    view = SettingsView(interaction.user.id, uid, db.get_user(uid).get("timezone", "UTC"))
    await interaction.response.send_message(
        embed=settings_embed(uid, view.selected), view=view, ephemeral=True)


# ---------- trading (one ore per side, both accept; persisted in DB) ----------

trades: dict[int, dict] = {}


def _pick_loads(s: str | None):
    return json.loads(s) if s else None


def _pick_dumps(p) -> str | None:
    return json.dumps(p) if p else None


def _bump_obtained(uid: str, u: dict, rarity: str, quality: str):
    """Count any-source obtains toward Obtain achievements (market/trade/gift)."""
    ups = {}
    if rarity == "Mythical":
        ups["dih_pulls"] = u.get("dih_pulls", 0) + 1
    if quality == "Perfect Condition":
        ups["perfect_pulls"] = u.get("perfect_pulls", 0) + 1
    if ups:
        db.update_user(uid, **ups)


def _persist_trade(t: dict):
    db.update_trade(t["id"], a_pick=_pick_dumps(t["a_pick"]), b_pick=_pick_dumps(t["b_pick"]),
                    a_ok=int(t["a_ok"]), b_ok=int(t["b_ok"]), stage=t.get("stage", "request"),
                    a_name=t["a_name"], b_name=t["b_name"],
                    channel_id=t.get("channel_id", ""), message_id=t.get("message_id", ""))


def _mem_trade(row: dict) -> dict:
    return {"id": row["id"], "a_id": int(row["a_id"]), "b_id": int(row["b_id"]),
            "a_name": row["a_name"], "b_name": row["b_name"],
            "a_pick": _pick_loads(row["a_pick"]), "b_pick": _pick_loads(row["b_pick"]),
            "a_ok": bool(row["a_ok"]), "b_ok": bool(row["b_ok"]), "stage": row["stage"],
            "channel_id": row["channel_id"], "message_id": row["message_id"], "message": None}


def trade_embed(t: dict) -> discord.Embed:
    def fmt(pick):
        if not pick:
            return "*nothing yet*"
        return f"**{pick['ore']} ({pick['quality']})** [{config.tier_name(pick['rarity'])}]"
    a_ok = "✅" if t["a_ok"] else "⏳"
    b_ok = "✅" if t["b_ok"] else "⏳"
    embed = discord.Embed(title=f"🔄 Trade #{t['id']}", color=0x00BCD4)
    embed.add_field(name=f"{a_ok} {t['a_name']}'s offer", value=fmt(t["a_pick"]), inline=True)
    embed.add_field(name=f"{b_ok} {t['b_name']}'s offer", value=fmt(t["b_pick"]), inline=True)
    embed.set_footer(text="Offers optional - one side can give nothing. Both hit Accept.")
    return embed


class TradeRequestView(discord.ui.View):
    """The other player accepts or declines."""

    def __init__(self, tid: int):
        super().__init__(timeout=180)
        self.tid = tid

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.green, emoji="🤝")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        t = trades.get(self.tid)
        if t is None:
            await interaction.response.send_message("Trade expired!", ephemeral=True)
            return
        if interaction.user.id != t["b_id"]:
            await interaction.response.send_message("This trade isn't for you!", ephemeral=True)
            return
        t["message"] = interaction.message
        t["stage"] = "main"
        _persist_trade(t)
        view = TradeMainView(self.tid)
        await interaction.response.edit_message(embed=trade_embed(t), view=view)

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger, emoji="✖")
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        t = trades.get(self.tid)
        if t is None:
            return
        if interaction.user.id not in (t["a_id"], t["b_id"]):
            await interaction.response.send_message("Not your trade!", ephemeral=True)
            return
        trades.pop(self.tid, None)
        db.delete_trade(self.tid)
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="❌ Trade declined.", embed=None, view=self)

    async def on_timeout(self):
        t = trades.pop(self.tid, None)
        db.delete_trade(self.tid)
        if t and t.get("message"):
            try:
                await t["message"].edit(content="⌛ Trade request expired.", embed=None, view=None)
            except Exception:
                pass


class TradeMainView(discord.ui.View):
    def __init__(self, tid: int):
        super().__init__(timeout=300)
        self.tid = tid

    def _side(self, interaction: discord.Interaction):
        t = trades.get(self.tid)
        if t is None:
            return None, None
        if interaction.user.id == t["a_id"]:
            return t, "a"
        if interaction.user.id == t["b_id"]:
            return t, "b"
        return t, None

    @discord.ui.button(label="Set my offer (P1)", style=discord.ButtonStyle.primary, emoji="📦", row=0)
    async def set_a(self, interaction: discord.Interaction, button: discord.ui.Button):
        t, side = self._side(interaction)
        if t is None:
            await interaction.response.send_message("Trade expired!", ephemeral=True)
            return
        if side != "a":
            await interaction.response.send_message("That's Player 1's button!", ephemeral=True)
            return
        view = InvBrowser(t["a_id"], t["a_id"], t["a_name"], False, source="trade",
                          trade_tid=self.tid, trade_side="a", guild=guild_scope(interaction))
        await interaction.response.send_message(embed=view.render(), view=view, ephemeral=True)

    @discord.ui.button(label="Set my offer (P2)", style=discord.ButtonStyle.primary, emoji="📦", row=0)
    async def set_b(self, interaction: discord.Interaction, button: discord.ui.Button):
        t, side = self._side(interaction)
        if t is None:
            await interaction.response.send_message("Trade expired!", ephemeral=True)
            return
        if side != "b":
            await interaction.response.send_message("That's Player 2's button!", ephemeral=True)
            return
        view = InvBrowser(t["b_id"], t["b_id"], t["b_name"], False, source="trade",
                          trade_tid=self.tid, trade_side="b", guild=guild_scope(interaction))
        await interaction.response.send_message(embed=view.render(), view=view, ephemeral=True)

    @discord.ui.button(label="Accept (P1)", style=discord.ButtonStyle.green, emoji="✅", row=1)
    async def ok_a(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._accept(interaction, "a")

    @discord.ui.button(label="Accept (P2)", style=discord.ButtonStyle.green, emoji="✅", row=1)
    async def ok_b(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._accept(interaction, "b")

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger, emoji="✖", row=1)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        t, side = self._side(interaction)
        if t is None or side is None:
            await interaction.response.send_message("Not your trade!", ephemeral=True)
            return
        trades.pop(self.tid, None)
        db.delete_trade(self.tid)
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="❌ Trade cancelled.", embed=None, view=self)

    async def _accept(self, interaction: discord.Interaction, side: str):
        t, who = self._side(interaction)
        if t is None:
            await interaction.response.send_message("Trade expired!", ephemeral=True)
            return
        if who != side:
            await interaction.response.send_message("That's the other player's button!", ephemeral=True)
            return
        if not t["a_pick"] and not t["b_pick"]:
            await interaction.response.send_message("☝️ At least one side must offer something!",
                                                    ephemeral=True)
            return
        t[f"{side}_ok"] = True
        _persist_trade(t)
        if t["a_ok"] and t["b_ok"]:
            await self._execute(interaction, t)
        else:
            await interaction.response.edit_message(embed=trade_embed(t), view=self)

    async def _execute(self, interaction: discord.Interaction, t: dict):
        a, b = SUID(interaction, t["a_id"]), SUID(interaction, t["b_id"])
        pa, pb = t["a_pick"], t["b_pick"]
        ia = db.take_one_item(a, pa["rarity"], pa["quality"], pa["ore"]) if pa else None
        ib = db.take_one_item(b, pb["rarity"], pb["quality"], pb["ore"]) if pb else None
        if (pa and ia is None) or (pb and ib is None):
            # rollback whatever was taken
            if ia is not None:
                db.add_item(a, ia["rarity"], ia["quality"], ia["ore"], ia.get("origin", "spin"), ia.get("origin_detail", ""))
            if ib is not None:
                db.add_item(b, ib["rarity"], ib["quality"], ib["ore"], ib.get("origin", "spin"), ib.get("origin_detail", ""))
            trades.pop(t["id"], None)
            db.delete_trade(t["id"])
            await interaction.response.edit_message(
                content="❌ Trade failed - someone no longer has their ore.", embed=None, view=None)
            return
        if ia is not None:
            db.add_item(b, ia["rarity"], ia["quality"], ia["ore"], ia.get("origin", "spin"), ia.get("origin_detail", ""))
            ub = db.get_user(b)
            _bump_obtained(b, ub, ia["rarity"], ia["quality"])
        if ib is not None:
            db.add_item(a, ib["rarity"], ib["quality"], ib["ore"], ib.get("origin", "spin"), ib.get("origin_detail", ""))
            ua = db.get_user(a)
            _bump_obtained(a, ua, ib["rarity"], ib["quality"])
        trades.pop(t["id"], None)
        db.delete_trade(t["id"])
        # obtain achievements (incl. Jackpot) per recipient + collector rechecks
        newly_a, newly_b = [], []
        for _uid in (a, b):
            _u = db.get_user(_uid)
            db.update_user(_uid, trade_count=_u.get("trade_count", 0) + 1)
        if ib is not None:
            newly_a = check_achievements(a, db.get_user(a), ib["rarity"], ib["quality"])
            sync_collectors(a)
        if ia is not None:
            newly_b = check_achievements(b, db.get_user(b), ia["rarity"], ia["quality"])
            sync_collectors(b)
        if ia is not None and ib is not None:
            result = (f"🔄 **Trade complete!** {t['a_name']} got **{pb['ore']} ({pb['quality']})**, "
                      f"{t['b_name']} got **{pa['ore']} ({pa['quality']})**.")
        elif ia is not None:
            result = (f"🎁 **{t['a_name']}** gave **{pa['ore']} ({pa['quality']})** "
                      f"to **{t['b_name']}**!")
        else:
            result = (f"🎁 **{t['b_name']}** gave **{pb['ore']} ({pb['quality']})** "
                      f"to **{t['a_name']}**!")
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content=result, embed=None, view=self)
        # reply to the trade pinging both + per-user achievement replies
        try:
            ref = t.get("message")
            await interaction.channel.send(
                content=f"<@{t['a_id']}> <@{t['b_id']}> 🔄 Trade finished!",
                reference=ref)
            if newly_a:
                await achievement_reply(interaction, f"<@{t['a_id']}>", newly_a,
                                        ref_message=ref)
            if newly_b:
                await achievement_reply(interaction, f"<@{t['b_id']}>", newly_b,
                                        ref_message=ref)
        except Exception:
            pass

    async def on_timeout(self):
        t = trades.pop(self.tid, None)
        db.delete_trade(self.tid)
        if t and t.get("message"):
            try:
                await t["message"].edit(content="⌛ Trade expired.", embed=None, view=None)
            except Exception:
                pass


@bot.tree.command(name="trade", description="Trade ores with another player (one-sided OK, both accept).")
@app_commands.describe(user="The player to trade with")
async def trade(interaction: discord.Interaction, user: discord.User):
    if user.id == interaction.user.id:
        await interaction.response.send_message("❌ You can't trade with yourself!", ephemeral=True)
        return
    if user.bot:
        await interaction.response.send_message("❌ You can't trade with a bot!", ephemeral=True)
        return
    tid = db.create_trade(guild_scope(interaction), str(interaction.user.id), str(user.id),
                          interaction.user.display_name, user.display_name)
    trades[tid] = {"id": tid, "a_id": interaction.user.id, "b_id": user.id,
                   "a_name": interaction.user.display_name, "b_name": user.display_name,
                   "a_pick": None, "b_pick": None, "a_ok": False, "b_ok": False,
                   "stage": "request", "channel_id": "", "message_id": "", "message": None}
    await interaction.response.send_message(
        f"{user.mention}, **{interaction.user.display_name}** wants to trade with you!\n"
        f"({interaction.user.display_name} can cancel from here too)",
        view=TradeRequestView(tid))
    try:
        msg = await interaction.original_response()
        trades[tid]["message"] = msg
        trades[tid]["channel_id"] = str(msg.channel.id)
        trades[tid]["message_id"] = str(msg.id)
        _persist_trade(trades[tid])
    except Exception:
        pass


# ---------- admin + kill-switch ----------

def is_admin(interaction: discord.Interaction) -> bool:
    try:
        return bool(interaction.guild and interaction.user.guild_permissions.administrator)
    except Exception:
        return False


def resolve_achievement(text: str) -> str | None:
    """Match an achievement by id or display name (case-insensitive). Returns id or None."""
    t = text.strip().lower()
    if t in config.ACHIEVEMENTS:
        return t
    for aid, (name, _desc) in config.ACHIEVEMENTS.items():
        if name.lower() == t:
            return aid
    return None


def install_killswitch():
    """Adds a global check to every slash command: when disabled, only admins can use anything."""
    for cmd in bot.tree.walk_commands():
        if isinstance(cmd, app_commands.Command):
            cmd.add_check(_enabled_check)


async def _enabled_check(interaction: discord.Interaction) -> bool:
    if db.get_setting("commands_enabled") != "1" and not is_admin(interaction):
        raise app_commands.CheckFailure("disabled")
    return True


@bot.tree.error
async def on_app_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    err = error
    if isinstance(err, app_commands.CommandInvokeError) and err.original is not None:
        err = err.original
    if isinstance(err, app_commands.CheckFailure):
        msg = "🔒 Commands are temporarily disabled by an admin."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception:
            pass
        return
    # NEVER swallow: log everything else (this hid real bugs before)
    import traceback as _tb
    print(f"COMMAND ERROR in /{(interaction.command.name if interaction.command else '?')}: "
          f"{type(err).__name__}: {err}")
    _tb.print_exception(type(err), err, err.__traceback__)
    try:
        msg = "❌ Something broke running that command - try again."
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except Exception:
        pass


@bot.tree.command(name="admin_event_give", description="[ADMIN] Event prize (grants Winner).")
@app_commands.describe(user="Winner", amount="How much ($)", description="What event they won")
async def admin_event_give(interaction: discord.Interaction, user: discord.User,
                           amount: app_commands.Range[int, 1, 100_000_000],
                           description: str = "BoBo event"):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Admins only!", ephemeral=True)
        return
    uid = SUID(interaction, user.id)
    u = db.get_user(uid)
    db.update_user(uid, balance=u["balance"] + amount, total_earned=u["total_earned"] + amount)
    newly = check_achievements(uid, db.get_user(uid), "", "")
    if db.grant_achievement(uid, "winner"):
        newly.append(f"🏆 **{config.ACHIEVEMENTS['winner'][0]}** - {config.ACHIEVEMENTS['winner'][1]}")
    db.add_mail(uid, f"🎉 You won **${amount:,}** from the **{description}**!")
    await interaction.response.send_message(
        f"{user.mention} won **${amount:,}** from the **{description}**!\n" + format_achievements(newly))


@bot.tree.command(name="admin_give", description="[ADMIN] Give money (no Winner achievement).")
@app_commands.describe(user="Who gets the money", amount="How much ($)")
async def admin_give(interaction: discord.Interaction, user: discord.User,
                     amount: app_commands.Range[int, 1, 100_000_000]):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Admins only!", ephemeral=True)
        return
    uid = SUID(interaction, user.id)
    u = db.get_user(uid)
    db.update_user(uid, balance=u["balance"] + amount, total_earned=u["total_earned"] + amount)
    newly = check_achievements(uid, db.get_user(uid), "", "")
    db.add_mail(uid, f"💰 An admin gave you **${amount:,}**!")
    msg = f"💰 {user.mention} got **${amount:,}** from an admin!"
    if newly:
        msg += "\n" + format_achievements(newly)
    await interaction.response.send_message(msg)


@bot.tree.command(name="admin_take", description="[ADMIN] Remove money from a player.")
@app_commands.describe(user="Who loses money", amount="How much ($)")
async def admin_take(interaction: discord.Interaction, user: discord.User,
                     amount: app_commands.Range[int, 1, 100_000_000]):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Admins only!", ephemeral=True)
        return
    uid = SUID(interaction, user.id)
    u = db.get_user(uid)
    taken = min(amount, u["balance"])
    db.update_user(uid, balance=u["balance"] - taken)
    name = await display_name(interaction, uid)
    await interaction.response.send_message(f"💸 An admin removed **${taken:,}** from **{name}**.")


@bot.tree.command(name="admin_ach_add", description="[ADMIN] Grant an achievement to a player.")
@app_commands.describe(user="Who", achievement_id="Id or name (e.g. dih_pull or Flawless)")
async def admin_ach_add(interaction: discord.Interaction, user: discord.User, achievement_id: str):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Admins only!", ephemeral=True)
        return
    aid = resolve_achievement(achievement_id)
    if aid is None:
        await interaction.response.send_message(
            f"❌ Unknown. Valid names: {', '.join(n for _, (n, _) in sorted(config.ACHIEVEMENTS.items(), key=lambda kv: kv[1][0]))}"[:1900],
            ephemeral=True)
        return
    uid = SUID(interaction, user.id)
    if db.grant_achievement(uid, aid):
        name = await display_name(interaction, uid)
        await interaction.response.send_message(
            f"🏆 **{name}** was granted **{config.ACHIEVEMENTS[aid][0]}**!")
    else:
        await interaction.response.send_message("They already have that one.", ephemeral=True)


@bot.tree.command(name="admin_ach_remove", description="[ADMIN] Remove an achievement from a player.")
@app_commands.describe(user="Who", achievement_id="Id or name to remove")
async def admin_ach_remove(interaction: discord.Interaction, user: discord.User, achievement_id: str):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Admins only!", ephemeral=True)
        return
    aid = resolve_achievement(achievement_id)
    if aid is None:
        await interaction.response.send_message("❌ Unknown achievement.", ephemeral=True)
        return
    uid = SUID(interaction, user.id)
    with db._lock, db.get_conn() as _conn:
        cur = _conn.execute("DELETE FROM achievements WHERE user_id=? AND ach_id=?", (uid, aid))
        _conn.commit()
        removed = cur.rowcount
    name = await display_name(interaction, uid)
    if removed:
        await interaction.response.send_message(f"🗑️ Removed **{config.ACHIEVEMENTS[aid][0]}** from **{name}**.")
    else:
        await interaction.response.send_message("They didn't have that one.", ephemeral=True)


@bot.tree.command(name="admin_disable", description="[ADMIN] Emergency: disable all commands.")
async def admin_disable(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Admins only!", ephemeral=True)
        return
    db.set_setting("commands_enabled", "0")
    await interaction.response.send_message("🔒 All commands disabled (admins bypass).")


@bot.tree.command(name="admin_enable", description="[ADMIN] Re-enable all commands.")
async def admin_enable(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Admins only!", ephemeral=True)
        return
    db.set_setting("commands_enabled", "1")
    await interaction.response.send_message("🔓 Commands re-enabled!")


@bot.tree.command(name="admin_nuke", description="[ADMIN] FACTORY RESET: wipe everything.")
async def admin_nuke(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Admins only!", ephemeral=True)
        return
    view = NukeConfirmView(interaction.user.id)
    await interaction.response.send_message(
        "☢️ **FACTORY RESET?** This wipes **everything**: all balances, inventories, vaults, "
        "market listings, trades, mail, achievements and settings. **Cannot be undone.**",
        view=view, ephemeral=True)


class NukeConfirmView(discord.ui.View):
    def __init__(self, owner_id: int):
        super().__init__(timeout=60)
        self.owner_id = owner_id

    @discord.ui.button(label="YES, WIPE EVERYTHING", style=discord.ButtonStyle.danger, emoji="☢️")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Not yours!", ephemeral=True)
            return
        import bot as _me  # noqa - module ref to clear in-memory trades
        with db._lock, db.get_conn() as conn:
            for table in ("users", "inventory", "market", "achievements", "trades",
                          "kv", "mail", "vault"):
                try:
                    conn.execute(f"DELETE FROM {table}")
                except Exception:
                    pass
            conn.commit()
        trades.clear()
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content="☢️ Factory reset complete. Fresh start for everyone.", embed=None, view=self)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="✖")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Not yours!", ephemeral=True)
            return
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="Phew. Cancelled - nothing was touched.",
                                                embed=None, view=self)
@bot.tree.command(name="admin_help", description="[ADMIN] List admin commands.")
async def admin_help(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Admins only!", ephemeral=True)
        return
    await interaction.response.send_message(
        "🛠️ **Admin Commands**\n"
        "🎉 `/admin_event_give @user $amount [description]` - event prize + Winner\n"
        "💰 `/admin_give @user $amount` - give money (no Winner)\n"
        "💸 `/admin_take @user $amount` - remove money\n"
        "🏆 `/admin_ach_add @user <id or name>` - grant achievement\n"
        "🗑️ `/admin_ach_remove @user <id or name>` - remove achievement\n"
        "🎰 `/admin_spins <per_day> <max_at_once>` - spin limits (`-1` = unlimited)\n"
        "🔒 `/admin_disable` / `/admin_enable` - emergency kill switch\n"
        "☢️ `/admin_nuke` - FACTORY RESET everything\n"
        "❓ `/admin_help` - this message",
        ephemeral=True)


@bot.tree.command(name="admin_spins", description="[ADMIN] Set spins per day and max per /spin.")
@app_commands.describe(per_day="Spins per day (number, or -1 for unlimited)",
                       max_at_once="Max per /spin call (number or 'infinite' = 100000)")
async def admin_spins(interaction: discord.Interaction, per_day: str, max_at_once: str):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Admins only!", ephemeral=True)
        return

    def parse_spins(raw: str, cap: int) -> int | str | None:
        raw = raw.strip().lower()
        if raw in ("infinite", "inf", "unlimited") or raw == "-1":
            return "inf"
        try:
            v = int(raw)
        except ValueError:
            return None
        return max(1, min(v, cap))

    pd = parse_spins(per_day, 1000000)
    mx = parse_spins(max_at_once, 100000)
    if pd is None or mx is None:
        await interaction.response.send_message(
            "❌ Type a number, `-1` for unlimited, or `infinite`.", ephemeral=True)
        return
    if mx == "inf":
        mx = 100000  # hard cap per call
    db.set_setting("spins_per_day", str(pd), guild_scope(interaction))
    db.set_setting("max_spin", str(mx), guild_scope(interaction))
    await interaction.response.send_message(
        f"🎰 Spins set: **{pd}/day**, up to **{mx}** per /spin.")


# ---------- gifting (one-way, instant, notified via mail) ----------

class ScopeGiftModal(discord.ui.Modal, title="Gift - how many?"):
    """Moves up to N (or ALL) of the current scope to the recipient + mail."""

    amount = discord.ui.TextInput(label="How many? (number or ALL)", placeholder="e.g. 1 or ALL",
                                  max_length=8)

    def __init__(self, owner_id: int, recip_id: int, ore: str | None, quality: str | None,
                 tier: str | None = None, source: str = "inv"):
        super().__init__()
        self.owner_id = owner_id
        self.recip_id = recip_id
        self.ore = ore
        self.quality = quality
        self.tier = tier
        self.source = source

    async def _recipient_name(self, interaction: discord.Interaction, r: str) -> str:
        try:
            u = await interaction.client.fetch_user(int(str(self.recip_id)))
            return u.display_name
        except Exception:
            pass
        return await display_name(interaction, r)

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("That's not yours!", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        g, r = SUID(interaction, self.owner_id), SUID(interaction, self.recip_id)
        data_source = "vault" if self.source == "vault" else "inv"
        targets = _scope_targets(g, data_source, self.ore, self.quality, self.tier)
        if not targets:
            await interaction.followup.send("❌ Nothing to gift.", ephemeral=True)
            return
        raw = str(self.amount.value).strip().lower()
        gift_all = raw in ("all", "max")
        if not gift_all:
            try:
                limit = int(raw)
            except ValueError:
                await interaction.followup.send("❌ Type a number or ALL.", ephemeral=True)
                return
            limit = max(0, limit)
        moved = 0
        moved_dih, moved_perfect = 0, 0
        desc_parts = []
        from_vault = (self.source == "vault")
        for t in targets:
            want = t["count"] if gift_all else min(limit - moved, t["count"])
            if want <= 0:
                break
            remaining = want
            origins = (db.vault_origin_counts(g, t["rarity"], t["quality"], t["ore"])
                       if from_vault
                       else db.origin_counts(g, t["rarity"], t["quality"], t["ore"]))
            for origin, c in origins.items():
                k = min(c, remaining)
                if k <= 0:
                    continue
                if from_vault:
                    got = db.vault_remove_many(g, t["rarity"], t["quality"], t["ore"], k,
                                               origin=origin)
                else:
                    got = db.remove_many_items(g, t["rarity"], t["quality"], t["ore"], k,
                                               origin=origin)
                db.add_many_items(r, [(t["rarity"], t["quality"], t["ore"])] * got, origin=origin)
                moved += got
                if t["rarity"] == "Mythical":
                    moved_dih += got
                if t["quality"] == "Perfect Condition":
                    moved_perfect += got
                remaining -= got
                if remaining <= 0:
                    break
            desc_parts.append(f"{t['ore']} ({t['quality']})")
            if not gift_all and moved >= limit:
                break
        giver_name = await display_name(interaction, g)
        what = ", ".join(desc_parts[:3]) + ("…" if len(desc_parts) > 3 else "")
        db.add_mail(r, f"🎁 **{giver_name}** gifted you **{moved}x** {what}!")
        if moved > 0:
            _gu = db.get_user(g)
            db.update_user(g, gift_ore_count=_gu.get("gift_ore_count", 0) + 1)
            _giver_newly = check_achievements(g, db.get_user(g), "", "")
        else:
            _giver_newly = []
        sync_collectors(g)  # giver may have broken a set
        # recipient obtain checks per distinct gifted pair (Jackpot needs the combo)
        gifted_pairs = sorted({(t["rarity"], t["quality"]) for t in targets})
        newly2 = []
        if moved > 0:
            _ru = db.get_user(r)
            _bumps = {}
            if moved_dih:
                _bumps["dih_pulls"] = _ru.get("dih_pulls", 0) + moved_dih
            if moved_perfect:
                _bumps["perfect_pulls"] = _ru.get("perfect_pulls", 0) + moved_perfect
            if _bumps:
                db.update_user(r, **_bumps)
        if moved > 0:
            first2 = True
            for rr, qq in gifted_pairs:
                newly2 += check_achievements(r, db.get_user(r), rr, qq, collectors=first2)
                first2 = False
        recip_name = await self._recipient_name(interaction, r)
        result_msg = await interaction.followup.send(
            f"🎁 Gifted **{moved}x** {what} to **{recip_name}**!", ephemeral=True)
        if _giver_newly:
            await achievement_reply(interaction, interaction.user.mention, _giver_newly,
                                    ref_message=result_msg)
        if newly2:
            # achievements go to the recipient's mail, not a reply
            names = []
            for line in newly2:
                m = line.split("**")
                names.append(m[1] if len(m) > 1 else line)
            db.add_mail(r, "🏆 Achievements unlocked: " + ", ".join(f"**{n}**" for n in names))


gift_group = app_commands.Group(name="gift", description="Gift ores or money.")


@gift_group.command(name="ore", description="Gift ores to another player (they get mail).")
@app_commands.describe(user="Who gets the gift",
                       source="Gift from inventory or vault")
@app_commands.choices(source=[app_commands.Choice(name="Inventory", value="inv"),
                              app_commands.Choice(name="Vault", value="vault")])
async def gift_ore(interaction: discord.Interaction, user: discord.User, source: str = "inv"):
    await interaction.response.defer(ephemeral=True)
    if user.id == interaction.user.id:
        await interaction.followup.send("❌ You can't gift yourself!", ephemeral=True)
        return
    if user.bot:
        await interaction.followup.send("❌ You can't gift a bot!", ephemeral=True)
        return
    uid = SUID(interaction)
    has_any = (db.count_inventory(uid) if source != "vault" else db.vault_count(uid)) > 0
    if not has_any:
        await interaction.followup.send(f"🎒 Your {source} is empty!", ephemeral=True)
        return
    view = InvBrowser(interaction.user.id, interaction.user.id,
                      interaction.user.display_name, False, source="gift",
                      recip_id=user.id,
                      recip_name=await display_name(interaction, str(user.id)),
                      guild=guild_scope(interaction), gift_from=source)
    await interaction.followup.send(embed=view.render(), view=view, ephemeral=True)


@gift_group.command(name="money", description="Gift money from wallet or bank.")
@app_commands.describe(user="Who gets the money", amount="How much ($)",
                       source="Take it from wallet or bank")
@app_commands.choices(source=[app_commands.Choice(name="Wallet", value="balance"),
                              app_commands.Choice(name="Bank", value="bank")])
async def gift_money(interaction: discord.Interaction, user: discord.User,
                     amount: app_commands.Range[int, 1, 100_000_000], source: str):
    await interaction.response.defer(ephemeral=True)
    if user.id == interaction.user.id:
        await interaction.followup.send("❌ You can't gift yourself!", ephemeral=True)
        return
    if user.bot:
        await interaction.followup.send("❌ You can't gift a bot!", ephemeral=True)
        return
    u = db.get_user(SUID(interaction))
    avail = u["balance"] if source == "balance" else u.get("bank_balance", 0)
    if avail < amount:
        await interaction.followup.send(
            f"❌ Only **${avail:,}** in {source}.", ephemeral=True)
        return
    view = GiftMoneyView(interaction.user.id, user.id,
                         await display_name(interaction, str(user.id)), amount, source)
    embed = discord.Embed(title="🎁 Confirm gift",
                          description=f"Send **${amount:,}** from your **{source}** to "
                                      f"**{(await display_name(interaction, str(user.id)))}**?",
                          color=0x4CAF50)
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)


class GiftMoneyView(discord.ui.View):
    def __init__(self, giver_id: int, recip_id: int, recip_name: str, amount: int, source: str):
        super().__init__(timeout=180)
        self.giver_id = giver_id
        self.recip_id = recip_id
        self.recip_name = recip_name
        self.amount = amount
        self.source = source

    @discord.ui.button(label="Send", style=discord.ButtonStyle.green, emoji="🎁")
    async def send(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.giver_id:
            await interaction.response.send_message("That's not yours!", ephemeral=True)
            return
        g, r = SUID(interaction, self.giver_id), SUID(interaction, self.recip_id)
        u = db.get_user(g)
        avail = u["balance"] if self.source == "balance" else u.get("bank_balance", 0)
        if avail < self.amount:
            await interaction.response.send_message("❌ Not enough left!", ephemeral=True)
            return
        if self.source == "balance":
            db.update_user(g, balance=u["balance"] - self.amount)
        else:
            db.update_user(g, bank_balance=u.get("bank_balance", 0) - self.amount)
        ru = db.get_user(r)
        db.update_user(r, balance=ru["balance"] + self.amount)
        _mu = db.get_user(g)
        db.update_user(g, gift_money_count=_mu.get("gift_money_count", 0) + 1)
        newly = check_achievements(r, db.get_user(r), "", "")
        check_achievements(g, db.get_user(g), "", "")
        giver_name = await display_name(interaction, g)
        db.add_mail(r, f"🎁 **{giver_name}** gifted you **${self.amount:,}**!")
        if newly:
            db.add_mail(r, format_achievements(newly))
        button.disabled = True
        await interaction.response.edit_message(
            content=f"🎁 Sent **${self.amount:,}** to **{self.recip_name}**!", embed=None, view=self)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="✖")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.giver_id:
            await interaction.response.send_message("That's not yours!", ephemeral=True)
            return
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="Cancelled.", embed=None, view=self)


bot.tree.add_command(gift_group)


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Missing DISCORD_TOKEN - copy .env.example to .env and paste your bot token.")
    db.init_db()
    install_killswitch()
    bot.run(TOKEN)
