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
    return "Chipped"


def roll_one() -> tuple[str, str, str]:
    rarity = roll_rarity()
    quality = roll_quality()
    ore = random.choice(config.ORES[rarity])
    return rarity, quality, ore


def today_str() -> str:
    return datetime.now(config.RESET_TIMEZONE).date().isoformat()


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
                       collectors: bool = True) -> list[str]:
    """Grant eligible achievements. Returns list of newly unlocked names.
    collectors=False skips the (expensive) collector scan — spin passes it once."""
    newly = []

    def grant(aid: str):
        if db.grant_achievement(user_id, aid):
            newly.append(f"🏆 **{config.ACHIEVEMENTS[aid][0]}** — {config.ACHIEVEMENTS[aid][1]}")

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
    if rarity == "Elite":
        grant("elite_pull")
    if rarity == "DIH":
        grant("dih_pull")
        if u.get("dih_pulls", 0) >= 3:
            grant("dih_3")
        if quality == "Perfect":
            grant("jackpot")
    if quality == "Perfect":
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
    if u.get("sell_count", 0) >= 10:
        grant("merchant_10")
    if u.get("buy_count", 0) >= 10:
        grant("customer_10")
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
    # It's Over: everything else is done (grant() dedupes, so this is safe to check every time)
    if collectors and len(db.get_achievements(user_id)) == len(config.ACHIEVEMENTS) - 1:
        grant("all_done")
    return newly


COLLECTOR_IDS = (
    "collector_low", "collector_mid", "collector_high", "collector_elite", "collector_dih",
    "collector_all", "qcollector_low", "qcollector_mid", "qcollector_high",
    "qcollector_elite", "qcollector_dih", "qcollector_all",
)


def current_collectors(user_id: str) -> set[str]:
    """Collectors the user qualifies for RIGHT NOW (inventory + vault together)."""
    earned = set()
    for tier, aid in (("Low", "collector_low"), ("Mid", "collector_mid"),
                      ("High", "collector_high"), ("Elite", "collector_elite"),
                      ("DIH", "collector_dih")):
        if db.owns_all_ores(user_id, config.ORES[tier]):
            earned.add(aid)
    if all(db.owns_all_ores(user_id, ores) for ores in config.ORES.values()):
        earned.add("collector_all")
    quals = list(config.QUALITIES.keys())
    for tier, aid in (("Low", "qcollector_low"), ("Mid", "qcollector_mid"),
                      ("High", "qcollector_high"), ("Elite", "qcollector_elite"),
                      ("DIH", "qcollector_dih")):
        if db.owns_all_stacks(user_id, [(o, q) for o in config.ORES[tier] for q in quals]):
            earned.add(aid)
    if db.owns_all_stacks(user_id, [(o, q) for ores in config.ORES.values()
                                    for o in ores for q in quals]):
        earned.add("qcollector_all")
    return earned


def sync_collectors(user_id: str) -> list[str]:
    """Achievements can no longer be lost — this is intentionally a no-op.
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
        # line looks like "🏆 **Name** — desc" -> keep just Name
        m = line.split("**")
        names.append(m[1] if len(m) > 1 else line)
    return f"🏆 Achievements unlocked ({len(newly)}): " + ", ".join(f"**{n}**" for n in names)


async def achievement_reply(interaction: discord.Interaction, mention: str,
                            newly: list[str], ref_message=None):
    """Announce unlocks as a REPLY (falls back to followup). De-duped, always one message."""
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
    except Exception:
        try:
            await interaction.followup.send(text)
        except Exception:
            pass


# ---------- spin view (quicksell button) ----------

class SpinView(discord.ui.View):
    def __init__(self, owner_id: int, item_id: int, rarity: str, quality: str, ore: str):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.item_id = item_id
        self.rarity = rarity
        self.quality = quality
        self.ore = ore
        self.sold = False

    @discord.ui.button(label="", style=discord.ButtonStyle.green, emoji="💸")
    async def quicksell(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("That's not your spin!", ephemeral=True)
            return
        if self.sold:
            await interaction.response.send_message("Already sold!", ephemeral=True)
            return
        removed = db.remove_item_by_id(str(self.owner_id), self.item_id)
        if removed is None:
            await interaction.response.send_message("Item already gone (sold/listed?).", ephemeral=True)
            return
        value = config.quicksell_value(self.rarity)
        u = db.get_user(str(self.owner_id))
        db.update_user(str(self.owner_id), balance=u["balance"] + value,
                       total_earned=u["total_earned"] + value)
        self.sold = True
        button.disabled = True
        button.label = f"Sold for ${value:,}"
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(f"💸 Quicksold **{self.quality} {self.ore}** for **${value:,}**!", ephemeral=True)


# ---------- inventory (two levels: ore page -> quality stacks) ----------

def ore_tier_label(ore: str, rarities: list[str]) -> str:
    """Emerald + [Mid] -> Emerald (Mid Tier)."""
    tiers = ", ".join(config.tier_name(r) for r in sorted(rarities, key=config.tier_index))
    return f"{ore} ({tiers})"


async def inspect_text(interaction: discord.Interaction, uid: str, rarity: str, quality: str,
                       ore: str, count: int) -> str:
    """Quality first, then tier/quality/combo odds, quicksell, origin, tip."""
    value_each = config.quicksell_value(rarity)
    pct, one_in = config.combined_odds(rarity, quality)
    r_chance = config.RARITIES[rarity]["chance"]
    q_chance = config.QUALITIES[quality]["chance"]
    origins = db.origin_counts(uid, rarity, quality, ore)
    market_n = origins.get("market", 0)
    origin_block = ""
    if market_n > 0:
        sellers = db.origin_sellers(uid, rarity, quality, ore)
        parts = []
        for sid, c in list(sellers.items())[:3]:
            parts.append(f"🛒 Bought from **{(await display_name(interaction, sid))}** x{c}")
        unknown = market_n - sum(sellers.values())
        if unknown > 0:
            parts.append(f"🛒 Bought on the player market x{unknown}")
        origin_block = "\n".join(parts) + "\n" if parts else ""
    return (
        f"**{ore} ({quality}) x{count}**\n"
        f"Tier: **{config.tier_name(rarity)}** — {r_chance:g}% (1 in {config.rarity_one_in(r_chance)} chance)\n"
        f"Quality: **{quality}** — {q_chance:g}% (1 in {config.rarity_one_in(q_chance)} chance)\n"
        f"Odds: **{pct:.4g}%** ({one_in} chance)\n"
        f"Quicksell: **${value_each:,}** each (**${value_each * count:,}** for all)\n"
        f"{origin_block}"
        f"Tip: use the inventory **Quicksell** button, `/quicksell_all`, or `/market_list`."
    )


# ---------- unified inventory/vault browser ----------
# One message: quality filter + ore dropdown (All ores included) + inspect +
# Quicksell/Vault buttons (inventory) or Un-vault (vault).
# Buttons appear whenever scoped (an ore picked and/or a quality picked),
# i.e. everywhere EXCEPT all-qualities + all-ores.

def _scope_targets(uid: str, source: str, ore: str | None, quality: str | None,
                   tier: str | None = None) -> list[dict]:
    items = db.get_inventory_grouped(uid) if source in ("inv", "gift") else db.vault_grouped(uid)
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
                               source=v.source, quality=v.quality, ore=v.ore, tier=v.tier)
            await self.browser_message.edit(embed=fresh.render(), view=fresh)
        except Exception:
            pass

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("That's not yours!", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        uid = str(self.owner_id)
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
        sync_collectors(uid)
        scope = " ".join(x for x in (self.ore or "", f"({self.quality})" if self.quality else "") if x)
        await self._refresh_browser()
        await interaction.followup.send(
            f"💸 Sold **{sold}** ore(s){' ' + scope if scope else ''} for **${earned:,}**!",
            ephemeral=True)
        if newly:
            await achievement_reply(interaction, interaction.user.mention, newly)


class ScopeVaultModal(discord.ui.Modal, title="Vault — how many?"):
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
                               source=v.source, quality=v.quality, ore=v.ore, tier=v.tier)
            await self.browser_message.edit(embed=fresh.render(), view=fresh)
        except Exception:
            pass

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("That's not yours!", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        uid = str(self.owner_id)
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
        await self._refresh_browser()
        await interaction.followup.send(
            f"🗝️ Stored **{moved}** ore(s) in your vault!", ephemeral=True)


class ScopeUnvaultModal(discord.ui.Modal, title="Un-vault — how many?"):
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
                               source=v.source, quality=v.quality, ore=v.ore, tier=v.tier)
            await self.browser_message.edit(embed=fresh.render(), view=fresh)
        except Exception:
            pass

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("That's not yours!", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        uid = str(self.owner_id)
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
        options = [discord.SelectOption(label="All ores", value="all",
                                        default=(current is None))]
        for o in ores[:24]:
            options.append(discord.SelectOption(
                label=f"{ore_tier_label(o['ore'], o['rarities'])} x{o['count']}"[:100],
                value=o["ore"], default=(o["ore"] == current)))
        super().__init__(placeholder="Choose which ore page to open…", options=options)

    async def callback(self, interaction: discord.Interaction):
        view: InvBrowser = self.view
        if interaction.user.id != view.viewer_id:
            await interaction.response.send_message("That's not yours! Run `/inventory` yourself.",
                                                    ephemeral=True)
            return
        view.ore = None if self.values[0] == "all" else self.values[0]
        view.selected = None
        embed = view.render()
        await interaction.response.edit_message(embed=embed, view=view)


class StackInspectView(discord.ui.View):
    """Ephemeral pop-up holding an inspect dropdown (opened via Inspect button)."""

    def __init__(self, viewer_id: int, target_id: int, source: str,
                 stacks: list[dict], public: bool):
        super().__init__(timeout=180)
        self.viewer_id = viewer_id
        self.target_id = target_id
        self.source = source
        self.public = public
        self.selected = None
        if stacks:
            self.add_item(BrowserInspectSelect(stacks))


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
        view.selected = (rarity, quality)
        if view.source == "vault":
            text = vault_inspect_text(str(view.target_id), rarity, quality, s["ore"], s["count"])
        else:
            text = await inspect_text(interaction, str(view.target_id), rarity, quality,
                                      s["ore"], s["count"])
        await interaction.response.send_message(text, ephemeral=not view.public)


class InvBrowser(discord.ui.View):
    """Unified inventory/vault browser. One message for everything.

    source='inv': Quicksell + Vault buttons (own only), shown whenever scoped
      (ore picked and/or quality picked) — hidden for all/all.
    source='vault': Un-vault button, same rule. Plus vault has its own quality filter.
    """

    def __init__(self, viewer_id: int, target_id: int, target_name: str, public: bool,
                 source: str = "inv", quality: str | None = None, ore: str | None = None,
                 tier: str | None = None, recip_id: int | None = None,
                 recip_name: str = "", trade_tid: int | None = None,
                 trade_side: str | None = None):
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
        self._rebuild()

    def _inv(self) -> bool:
        return self.source in ("inv", "gift", "trade")

    # ----- data -----
    def overviews(self) -> list[dict]:
        if self._inv():
            fn = db.get_ores_by_quality if self.quality else db.get_ores_overview
            ores = fn(str(self.target_id), self.quality) if self.quality else fn(str(self.target_id))
        else:
            if self.quality:
                ores = db.vault_by_quality(str(self.target_id), self.quality)
            else:
                ores = db.vault_overview(str(self.target_id))
        if self.tier:
            ores = [o for o in ores if self.tier in o["rarities"]]
        ores.sort(key=lambda o: (min(config.tier_index(r) for r in o["rarities"]), -o["count"]))
        return ores

    def stacks(self) -> list[dict]:
        if not self.ore:
            return []
        if self._inv():
            rows = db.get_ore_detail(str(self.target_id), self.ore, self.quality)
        else:
            rows = db.vault_detail(str(self.target_id), self.ore)
            if self.quality:
                rows = [r for r in rows if r["quality"] == self.quality]
        if self.tier:
            rows = [r for r in rows if r["rarity"] == self.tier]
        return rows

    def totals(self) -> tuple[int, int]:
        rows = _scope_targets(str(self.target_id), self.source, self.ore, self.quality, self.tier)
        n = sum(r["count"] for r in rows)
        v = sum(config.quicksell_value(r["rarity"]) * r["count"] for r in rows)
        return n, v

    # ----- render -----
    def render(self):
        ores = self.overviews()
        stacks = self.stacks()
        if self.ore and not stacks:
            self.ore, self.selected = None, None
            stacks = []
            ores = self.overviews()
        n, v = self.totals()
        icon = {"inv": "🎒", "vault": "🗝️", "gift": "🎁", "trade": "🔄"}.get(self.source, "🎒")
        what = {"inv": "Inventory", "vault": "Vault",
                "gift": f"Gift for {self.recip_name}", "trade": "Trade offer"}.get(
            self.source, "Inventory")
        if self.ore:
            r0 = min(stacks, key=lambda s: config.tier_index(s["rarity"]))["rarity"] if stacks else None
            dot = config.RARITIES[r0]["dot"] if r0 else icon
            title = f"{dot} {self.ore} ({n} ores) (${v:,})"
            lines = []
            for s in stacks:
                mark = "▶ " if (s["rarity"], s["quality"]) == self.selected else ""
                lines.append(f"{mark}**{s['ore']} ({s['quality']})** x{s['count']}")
            desc = "\n".join(lines) or "Empty!"
        else:
            title = f"{icon} {self.target_name}'s {what} ({n} ores) (${v:,})"
            desc = "\n".join(
                f"**{o['ore']}** ({', '.join(config.tier_name(r) for r in sorted(o['rarities'], key=config.tier_index))}) x{o['count']}"
                for o in ores[:25]) or "Empty!"
        if self.quality:
            title += f" — {self.quality} only"
        if self.tier:
            title += f" — {config.tier_name(self.tier)}"
        if len(ores) > 25 and not self.ore:
            desc += f"\n-# Showing 25 of {len(ores)} ores."
        self._rebuild(ores, stacks)
        return discord.Embed(title=title, description=desc,
                             color=0x00BCD4 if self.source == "inv" else 0x795548)

    def _rebuild(self, ores: list[dict] | None = None, stacks: list[dict] | None = None):
        self.clear_items()
        if ores is None:
            ores = self.overviews()
        if stacks is None:
            stacks = self.stacks()
        # tier hides once an ore is picked (ore implies its tier)
        if self.ore is None:
            self.add_item(BrowserTierSelect(self.tier))
        self.add_item(BrowserOreSelect(ores, self.ore))
        self.add_item(BrowserQualitySelect(self.quality))
        # inspect lives behind a button opening a pop-up (all UIs)
        inspect_stacks = None
        if self.ore and stacks:
            if self.selected is None:
                self.selected = (stacks[0]["rarity"], stacks[0]["quality"])
            inspect_stacks = stacks
        elif self.source in ("vault", "gift", "inv"):
            all_stacks = [r for r in _scope_targets(str(self.target_id), self.source, None,
                                                    self.quality, self.tier)]
            all_stacks.sort(key=lambda r: (config.tier_index(r["rarity"]),
                                           list(config.QUALITIES.keys()).index(r["quality"])))
            if all_stacks:
                inspect_stacks = all_stacks
        if inspect_stacks:
            self.add_item(self._inspect_btn(inspect_stacks))
        mine = self.viewer_id == self.target_id
        scoped = self.ore is not None or self.quality is not None or self.tier is not None
        if mine and scoped:
            if self.source == "inv":
                self.add_item(self._qs_btn())
                self.add_item(self._vault_btn())
            elif self.source == "vault":
                self.add_item(self._unvault_btn())
            elif self.source == "gift":
                self.add_item(self._gift_btn())
            elif self.source == "trade" and self.ore and stacks:
                self.add_item(self._trade_select_btn())
            elif self.source == "trade" and self.ore and stacks:
                self.add_item(self._trade_select_btn())

    def _inspect_btn(self, stacks: list[dict]):
        view = self

        async def cb(interaction: discord.Interaction):
            if interaction.user.id != view.viewer_id:
                await interaction.response.send_message("That's not yours!", ephemeral=True)
                return
            pop = StackInspectView(view.viewer_id, view.target_id, view.source,
                                   stacks, view.public)
            await interaction.response.send_message(
                content=f"🔍 Inspect — pick a stack ({len(stacks)} shown):",
                view=pop, ephemeral=True)
        btn = discord.ui.Button(label="Inspect", style=discord.ButtonStyle.secondary, emoji="🔍")
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
                ScopeGiftModal(view.viewer_id, view.recip_id, view.ore, view.quality, view.tier))
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

def vault_inspect_text(uid: str, rarity: str, quality: str, ore: str, count: int) -> str:
    value_each = config.quicksell_value(rarity)
    pct, one_in = config.combined_odds(rarity, quality)
    r_chance = config.RARITIES[rarity]["chance"]
    q_chance = config.QUALITIES[quality]["chance"]
    origins = db.vault_origin_counts(uid, rarity, quality, ore)
    market_n = origins.get("market", 0)
    origin_line = f"🛒 {market_n}x bought on the player market\n" if market_n else ""
    return (
        f"**{ore} ({quality}) x{count}**\n"
        f"Tier: **{config.tier_name(rarity)}** — {r_chance:g}% (1 in {config.rarity_one_in(r_chance)} chance)\n"
        f"Quality: **{quality}** — {q_chance:g}% (1 in {config.rarity_one_in(q_chance)} chance)\n"
        f"Odds: **{pct:.4g}%** ({one_in} chance)\n"
        f"Quicksell value: **${value_each:,}** each (**${value_each * count:,}** total)\n"
        f"{origin_line}"
        f"Tip: use **Un-vault** to move it back to inventory."
    )


@bot.tree.command(name="vault", description="See vaults (yours, or a public one). No quicksell here.")
@app_commands.describe(user="Optional: view another player's public vault")
async def vault(interaction: discord.Interaction, user: discord.User | None = None):
    target = user or interaction.user
    tid = str(target.id)
    t = db.get_user(tid)
    public = bool(t.get("vault_public", 0))
    if target.id != interaction.user.id and not public:
        await interaction.response.send_message(
            f"🔒 **{(await display_name(interaction, tid))}'s** vault is private.", ephemeral=True)
        return
    if db.vault_count(tid) == 0:
        await interaction.response.send_message("🗝️ Vault is empty! Store ores via inventory.",
                                                ephemeral=not public)
        return
    name = await display_name(interaction, tid)
    view = InvBrowser(interaction.user.id, target.id, name, public, source="vault")
    await interaction.response.send_message(embed=view.render(), view=view, ephemeral=not public)


# ---------- bot events ----------

@bot.event
async def on_ready():
    db.init_db()
    try:
        await bot.tree.sync()
    except Exception as e:
        print(f"Slash sync failed: {e}")
    print(f"Logged in as {bot.user} — /spin ready!" + (" [UNLIMITED SPINS TEST MODE]" if UNLIMITED_SPINS else ""))
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

@bot.tree.command(name="spin", description=f"Spin! Optional amount (up to 100000).")
@app_commands.describe(amount="How many spins (default 1)")
async def spin(interaction: discord.Interaction, amount: app_commands.Range[int, 1, 100000] = 1):
    await interaction.response.defer()
    uid = str(interaction.user.id)
    db.init_db()
    u = db.reset_spins_if_new_day(uid, user_today(uid))
    raw_spd = (db.get_setting("spins_per_day") or "3").strip().lower()
    unlimited_day = raw_spd in ("inf", "infinite", "unlimited")
    try:
        spins_per_day = 10 ** 12 if unlimited_day else max(1, int(raw_spd))
    except ValueError:
        spins_per_day = 3
    try:
        max_spin = max(1, min(100000, int(db.get_setting("max_spin") or 100000)))
    except ValueError:
        max_spin = 100000
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
                f"🔥 Streak: **{u['streak']}** day(s) — spin daily to keep it!"
            )
            return
        n = min(amount, remaining)
        spins_left_text = None  # computed after spinning

    pulls: list[tuple[str, str, str]] = [roll_one() for _ in range(n)]
    item_id = None
    if n == 1:
        rarity, quality, ore = pulls[0]
        item_id = db.add_item(uid, rarity, quality, ore)  # THE item (for quicksell button)
    else:
        db.add_many_items(uid, pulls)  # batched: one transaction even for x1000

    # update counters in one go
    order = ["Low", "Mid", "High", "Elite", "DIH"]
    key = {"Low": "low_pulls", "Mid": "mid_pulls", "High": "high_pulls",
           "Elite": "elite_pulls", "DIH": "dih_pulls"}
    u = db.get_user(uid)
    counts = {r: sum(1 for p in pulls if p[0] == r) for r in order}
    perfect_n = sum(1 for p in pulls if p[1] == "Perfect")
    updates = dict(spins_used_today=u["spins_used_today"] + n, total_spins=u["total_spins"] + n)
    for r in order:
        updates[key[r]] = u[key[r]] + counts[r]
    updates["perfect_pulls"] = u.get("perfect_pulls", 0) + perfect_n
    db.update_user(uid, **updates)
    u = db.update_streak(uid, user_today(uid))
    u = db.get_user(uid)
    # rarest spin ever (global best, kept even if sold — stored as rarity|quality|ore)
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
    if spins_left_text is None:
        spins_left_text = "∞" if unlimited_day else f"{spins_per_day - u['spins_used_today']}/{spins_per_day}"

    ping = await _spin_ping(uid, interaction)

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
        embed.set_footer(text=f"🔥 {u['streak']}-day streak • {interaction.user.display_name}")
        if rarity == "DIH":
            embed.add_field(name="🌟", value="**DIH TIER PULL!!** Insane luck.", inline=False)
        if item_id is not None:
            view = SpinView(interaction.user.id, item_id, rarity, quality, ore)
            view.children[0].label = f"Quicksell ${value:,}"
            await interaction.followup.send(content=ping, embed=embed, view=view)
        else:
            await interaction.followup.send(content=ping, embed=embed)
    else:
        # summary for multi-spins: rarest pull first
        best = max(pulls, key=lambda p: (order.index(p[0]),
                                         list(config.QUALITIES.keys()).index(p[1])))
        haul_value = sum(config.quicksell_value(p[0]) for p in pulls)
        lines = [f"{config.RARITIES[r]['dot']} {config.tier_name(r)} x{counts[r]}"
                 for r in order if counts[r]]
        embed = discord.Embed(title=f"🎰 x{n} spins!", color=0x9E9E9E)
        embed.add_field(
            name="⭐ Best pull",
            value=f"**{best[2]} ({best[1]})** — {config.tier_name(best[0])} {config.RARITIES[best[0]]['dot']}",
            inline=False)
        embed.add_field(name="📦 Haul", value="\n".join(lines) if lines else "—", inline=True)
        embed.add_field(name="💰 Haul quicksell value", value=f"${haul_value:,}", inline=True)
        embed.add_field(name="🎰 Spins left today", value=spins_left_text, inline=True)
        embed.set_footer(text=f"🔥 {u['streak']}-day streak • {interaction.user.display_name}")
        await interaction.followup.send(content=ping, embed=embed)
    # achievements AFTER the result so "thinking" always resolves fast;
    # collectors scanned once (first pair) instead of per pair
    newly: list[str] = []
    first = True
    for r, q in sorted(set(pulls)):
        try:
            newly += check_achievements(uid, u, r, q, collectors=first)
        except Exception as e:
            print(f"SPINDBG check failed for {uid} ({r},{q}): {type(e).__name__}: {e}")
        first = False
    print(f"SPINDBG spins={u['total_spins']} pairs={sorted(set(pulls))} newly={len(newly)}")
    if newly:
        # de-dupe (multiple checks can grant different achievements; same one can't double-grant)
        seen, unique = set(), []
        for line in newly:
            if line not in seen:
                seen.add(line)
                unique.append(line)
        await achievement_reply(interaction, interaction.user.mention, unique)


@bot.tree.command(name="balance", description="Check money (yours, or a public one).")
@app_commands.describe(user="Optional: view another player's public balance")
async def balance(interaction: discord.Interaction, user: discord.User | None = None):
    target = user or interaction.user
    tid = str(target.id)
    t = db.get_user(tid)
    public = bool(t.get("balance_public", 0))
    if target.id != interaction.user.id and not public:
        await interaction.response.send_message(
            f"🔒 **{(await display_name(interaction, tid))}'s** balance is private.", ephemeral=True)
        return
    u = db.get_user(tid)
    _, iv = db.inventory_value(tid)
    _, vv = db.vault_value(tid)
    assets = iv + vv
    name = await display_name(interaction, tid)
    embed = discord.Embed(title=f"💰 {name}'s Balance", color=0x4CAF50)
    embed.add_field(name="💵 Balance", value=f"**${u['balance']:,}**", inline=True)
    embed.add_field(name="🎒 Assets", value=f"**${assets:,}**", inline=True)
    embed.add_field(name="🏦 Bank", value=f"**${u.get('bank_balance', 0):,}**", inline=True)
    embed.set_footer(text=f"Total earned: ${u['total_earned']:,}")
    view = BalanceView(interaction.user.id, target.id)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=not public)


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
        uid = str(self.owner_id)
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
            await interaction.response.send_message(f"💵 Withdrew **${n:,}** to your balance!",
                                                    ephemeral=True)


class BalanceView(discord.ui.View):
    def __init__(self, viewer_id: int, target_id: int):
        super().__init__(timeout=180)
        self.viewer_id = viewer_id
        self.target_id = target_id
        if viewer_id != target_id:
            self.to_bank.disabled = True

    @discord.ui.button(label="To bank", style=discord.ButtonStyle.primary, emoji="🏦")
    async def to_bank(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.viewer_id or self.viewer_id != self.target_id:
            await interaction.response.send_message("That's not yours!", ephemeral=True)
            return
        await interaction.response.send_modal(BankTransferModal(self.viewer_id, "to_bank"))


@bot.tree.command(name="bank", description="See banks (yours, or a public one).")
@app_commands.describe(user="Optional: view another player's public bank")
async def bank(interaction: discord.Interaction, user: discord.User | None = None):
    target = user or interaction.user
    tid = str(target.id)
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

    @discord.ui.button(label="Withdraw", style=discord.ButtonStyle.green, emoji="💵")
    async def withdraw(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.viewer_id or self.viewer_id != self.target_id:
            await interaction.response.send_message("That's not yours!", ephemeral=True)
            return
        await interaction.response.send_modal(BankTransferModal(self.viewer_id, "to_balance"))


@bot.tree.command(name="inventory", description="See inventories (yours, or a public one).")
@app_commands.describe(user="Optional: view another player's public inventory")
async def inventory(interaction: discord.Interaction, user: discord.User | None = None):
    target = user or interaction.user
    tid = str(target.id)
    t = db.get_user(tid)
    public = bool(t.get("inv_public", 0))
    if target.id != interaction.user.id and not public:
        await interaction.response.send_message(
            f"🔒 **{(await display_name(interaction, tid))}'s** inventory is private.", ephemeral=True)
        return
    if db.count_inventory(tid) == 0:
        await interaction.response.send_message("🎒 Inventory is empty!", ephemeral=not public)
        return
    name = await display_name(interaction, tid)
    view = InvBrowser(interaction.user.id, target.id, name, public, source="inv")
    await interaction.response.send_message(embed=view.render(), view=view, ephemeral=not public)


@bot.tree.command(name="quicksell_all", description="Sell your ENTIRE inventory instantly.")
async def quicksell_all(interaction: discord.Interaction):
    await interaction.response.defer()
    uid = str(interaction.user.id)
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
    sync_collectors(uid)
    await interaction.followup.send(f"💸 Sold **{count}** ores for **${earned:,}**! Balance: **${u['balance'] + earned:,}**.")
    if newly:
        await achievement_reply(interaction, interaction.user.mention, newly)


# ----- market -----

class MarketPriceModal(discord.ui.Modal, title="Set your price"):
    def __init__(self, owner_id: int, rarity: str, quality: str, ore: str):
        super().__init__()
        self.owner_id = owner_id
        self.rarity = rarity
        self.quality = quality
        self.ore = ore

    price = discord.ui.TextInput(label="Price ($)", placeholder="e.g. 500", max_length=12)

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("That's not your listing!", ephemeral=True)
            return
        try:
            amount = int(str(self.price.value).replace(",", "").replace("$", "").strip())
        except ValueError:
            await interaction.response.send_message("❌ Price must be a whole number, e.g. `500`.", ephemeral=True)
            return
        if amount < 1:
            await interaction.response.send_message("❌ Price must be at least $1.", ephemeral=True)
            return
        listing_id = db.market_list(str(self.owner_id), self.rarity, self.quality, self.ore, amount)
        if listing_id is None:
            await interaction.response.send_message("❌ You don't own that ore anymore.", ephemeral=True)
            return
        sync_collectors(str(self.owner_id))  # listing removes it from inventory
        quick = config.quicksell_value(self.rarity)
        await interaction.response.send_message(
            f"📦 Listed **{self.quality} {self.ore}** ({config.tier_name(self.rarity)}) for **${amount:,}**! (ID: `{listing_id}`)\n"
            f"Quicksell value would've been ${quick:,} — {'🤑 profit mindset!' if amount > quick else '⚠️ cheaper than quicksell!'}"
        )


class MarketSellTierSelect(discord.ui.Select):
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
            await interaction.response.send_message("That's not your inventory!", ephemeral=True)
            return
        view: MarketSellView = self.view
        view.tier = None if self.values[0] == "all" else self.values[0]
        view.refresh_ores()
        await interaction.response.edit_message(embed=view.make_embed(), view=view)


class MarketSellSelect(discord.ui.Select):
    """Level 1: pick which ore to list (same look as inventory)."""

    def __init__(self, owner_id: int, ores: list[dict], tier: str | None = None):
        self.owner_id = owner_id
        self.tier = tier
        if tier:
            ores = [o for o in ores if tier in o["rarities"]]
        options = []
        for o in ores[:25]:  # Discord limit
            label = f"{ore_tier_label(o['ore'], o['rarities'])} x{o['count']}"[:100]
            options.append(discord.SelectOption(label=label, value=o["ore"]))
        super().__init__(placeholder="Choose which ore to list…", options=options or [
            discord.SelectOption(label="(nothing in this tier)", value="none")])

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("That's not your inventory!", ephemeral=True)
            return
        if self.values[0] == "none":
            return
        ore = self.values[0]
        stacks = db.get_ore_detail(str(self.owner_id), ore)
        if self.tier:
            stacks = [s for s in stacks if s["rarity"] == self.tier]
        if not stacks:
            await interaction.response.send_message("Nothing there anymore.", ephemeral=True)
            return
        view = ListOrePageView(self.owner_id, ore, stacks)
        await interaction.response.send_message(embed=view.page_embed(), view=view, ephemeral=True)


class ListStackSelect(discord.ui.Select):
    """Level 2: choose which quality stack of the ore to list."""

    def __init__(self, owner_id: int, stacks: list[dict]):
        self.owner_id = owner_id
        options = []
        for s in stacks[:25]:
            label = f"{s['ore']} ({s['quality']}) x{s['count']}"[:100]
            desc = f"{config.tier_name(s['rarity'])} • quicksell ${config.quicksell_value(s['rarity']):,} each"[:100]
            options.append(discord.SelectOption(label=label, description=desc,
                                                value=f"{s['rarity']}|{s['quality']}"))
        super().__init__(placeholder="Choose which stack to list…", options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("That's not yours!", ephemeral=True)
            return
        self.view.selected = self.values[0]
        self.options = [
            discord.SelectOption(label=f"{s['ore']} ({s['quality']}) x{s['count']}"[:100],
                                 description=f"{config.tier_name(s['rarity'])} • quicksell ${config.quicksell_value(s['rarity']):,} each"[:100],
                                 value=f"{s['rarity']}|{s['quality']}",
                                 default=(f"{s['rarity']}|{s['quality']}" == self.values[0]))
            for s in self.view.stacks[:25]
        ]
        await interaction.response.edit_message(embed=self.view.page_embed(), view=self.view)


class ListOrePageView(discord.ui.View):
    """Ore page for listing: quality dropdown + List this button."""

    def __init__(self, owner_id: int, ore: str, stacks: list[dict]):
        super().__init__(timeout=180)
        self.owner_id = owner_id
        self.ore = ore
        self.stacks = stacks
        self.selected = f"{stacks[0]['rarity']}|{stacks[0]['quality']}"
        self.add_item(ListStackSelect(owner_id, stacks))

    def page_embed(self) -> discord.Embed:
        lines = []
        for s in self.stacks:
            mark = "▶ " if f"{s['rarity']}|{s['quality']}" == self.selected else ""
            lines.append(f"{mark}**{s['ore']} ({s['quality']})** x{s['count']}")
        return discord.Embed(title=f"📦 List: {self.ore}", description="\n".join(lines),
                             color=0x4CAF50)

    @discord.ui.button(label="List this", style=discord.ButtonStyle.green, emoji="📋")
    async def list_this(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("That's not yours!", ephemeral=True)
            return
        rarity, quality = self.selected.split("|")
        await interaction.response.send_modal(MarketPriceModal(self.owner_id, rarity, quality, self.ore))


class MarketSellView(discord.ui.View):
    def __init__(self, owner_id: int, ores: list[dict]):
        super().__init__(timeout=180)
        self.owner_id = owner_id
        self.ores = ores
        self.tier = None
        self.add_item(MarketSellTierSelect(owner_id))
        if ores:
            self.add_item(MarketSellSelect(owner_id, ores))

    def make_embed(self) -> discord.Embed:
        ores = self.ores
        if self.tier:
            ores = [o for o in ores if self.tier in o["rarities"]]
        desc = "\n".join(f"**{ore_tier_label(o['ore'], o['rarities'])}** x{o['count']}"
                         for o in ores[:25]) or "Nothing in this tier!"
        title = "📦 List an ore" + (f" — {config.tier_name(self.tier)}" if self.tier else "")
        return discord.Embed(title=title, description=desc, color=0x4CAF50)

    def refresh_ores(self):
        for child in self.children:
            if isinstance(child, MarketSellSelect):
                self.remove_item(child)
        self.add_item(MarketSellSelect(self.owner_id, self.ores, self.tier))


@bot.tree.command(name="market_list", description="List one of your ores on the player market (pick from dropdown).")
async def market_list(interaction: discord.Interaction):
    uid = str(interaction.user.id)
    ores = db.get_ores_overview(uid)
    if not ores:
        await interaction.response.send_message("🎒 Your inventory is empty! Use `/spin` first.", ephemeral=True)
        return
    # lowest tier first
    ores.sort(key=lambda o: (min(config.tier_index(r) for r in o["rarities"]), -o["count"]))
    desc = "\n".join(f"**{ore_tier_label(o['ore'], o['rarities'])}** x{o['count']}"
                     for o in ores[:25])
    embed = discord.Embed(title="📦 List an ore", description=desc, color=0x4CAF50)
    await interaction.response.send_message(
        embed=embed, view=MarketSellView(interaction.user.id, ores), ephemeral=True)


PAGE_SIZE = 10
BROWSE_LIMIT = 50  # per stage; pages flip through these 10 at a time


async def seller_name(interaction: discord.Interaction, seller_id: str) -> str:
    """Seller display name — Anonymous unless they've set market listings public."""
    try:
        if not db.get_user(seller_id).get("market_public", 0):
            return "Anonymous"
    except Exception:
        return "Anonymous"
    return await display_name(interaction, seller_id)


async def format_listings(interaction: discord.Interaction, listings: list[dict]) -> list[str]:
    lines = []
    for l in listings:
        seller = await seller_name(interaction, l["seller_id"])
        lines.append(f"`{l['id']}` **{l['ore']}** ({config.tier_name(l['rarity'])}) — **${l['price']:,}** — {seller}")
    return lines


async def render_market(owner_id: int, interaction: discord.Interaction,
                        ore: str | None = None, quality: str | None = None,
                        sort: str | None = None, page: int = 0, tier: str | None = None):
    """One renderer for every market stage. Returns (embed, view)."""
    all_listings = db.market_browse(ore=ore, quality=quality, sort=sort or "new",
                                    limit=BROWSE_LIMIT, tier=tier)
    pages = max(1, (len(all_listings) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = page % pages
    chunk = all_listings[page * PAGE_SIZE:page * PAGE_SIZE + PAGE_SIZE]
    lines = await format_listings(interaction, chunk)
    scope = " — ".join(x for x in (
        f"{ore}" if ore else None,
        f"({quality})" if quality else None,
        config.tier_name(tier) if tier else None) if x)
    if not sort or sort == "new":
        title = f"🏪 {scope + ' — ' if scope else ''}latest"
    else:
        label = {"cheapest": "cheapest", "average": "closest to average",
                 "expensive": "most expensive"}[sort]
        title = f"🏪 {scope + ' — ' if scope else ''}{label}"
    if pages > 1:
        title += f" (page {page + 1}/{pages})"
    embed = discord.Embed(title=title, description="\n".join(lines) if lines else "Sold out!",
                          color=0x9C27B0)
    embed.set_footer(text="Inspect a listing, then hit Buy • flip pages with ◀ ▶")
    return embed, MarketBrowser(owner_id, ore=ore, quality=quality, sort=sort,
                                page=page, pages=pages, chunk=chunk, tier=tier)


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
        tier = None if self.values[0] == "all" else self.values[0]
        # changing tier resets ore (ore may not exist in that tier)
        embed, view = await render_market(self.owner_id, interaction, tier=tier)
        await interaction.response.edit_message(embed=embed, view=view)


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
        if self.values[0] == "all":
            embed, view = await render_market(self.owner_id, interaction, tier=self.tier)
        else:
            embed, view = await render_market(self.owner_id, interaction,
                                              quality=self.values[0], tier=self.tier)
        await interaction.response.edit_message(embed=embed, view=view)


class MarketOreFilterSelect(discord.ui.Select):
    def __init__(self, owner_id: int, current: str | None = None, tier: str | None = None):
        self.owner_id = owner_id
        self.tier = tier
        ores = db.market_ores_with_rarity()
        if tier:
            ores = [o for o in ores if tier in o["rarities"]]
        # lowest tier first
        ores.sort(key=lambda o: min(config.tier_index(r) for r in o["rarities"]))
        options = [discord.SelectOption(label="All ores", value="all", default=(current is None))]
        for o in ores[:24]:
            tiers = ", ".join(config.tier_name(r) for r in sorted(o["rarities"], key=config.tier_index))
            options.append(discord.SelectOption(
                label=f"{o['ore']} ({tiers})"[:100], value=o["ore"],
                default=(o["ore"] == current)))
        super().__init__(placeholder="Filter by ore…", options=options or [
            discord.SelectOption(label="(no listings)", value="none")])

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Use `/market_view` to browse yourself!", ephemeral=True)
            return
        if self.values[0] == "none":
            return
        if self.values[0] == "all":
            embed, view = await render_market(self.owner_id, interaction, tier=self.tier)
        else:
            embed, view = await render_market(self.owner_id, interaction,
                                              ore=self.values[0], tier=self.tier)
        await interaction.response.edit_message(embed=embed, view=view)


class MarketQualityFilterSelect(discord.ui.Select):
    def __init__(self, owner_id: int, ore: str, current: str | None = None,
                 tier: str | None = None):
        self.owner_id = owner_id
        self.ore = ore
        self.tier = tier
        quals = db.market_qualities(ore)
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
        embed, view = await render_market(self.owner_id, interaction, ore=self.ore,
                                          quality=self.quality, sort=self.values[0], page=0,
                                          tier=self.tier)
        await interaction.response.edit_message(embed=embed, view=view)


class MarketListingInspectSelect(discord.ui.Select):
    """Pick one of the shown listings to inspect it."""

    def __init__(self, listings: list[dict]):
        options = []
        for l in listings[:25]:
            label = f"{l['ore']} — ${l['price']:,}"[:100]
            desc = f"{l['quality']} • {config.tier_name(l['rarity'])} • ID {l['id']}"[:100]
            options.append(discord.SelectOption(label=label, description=desc,
                                                value=str(l["id"])))
        super().__init__(placeholder="Inspect a listing…", options=options or [
            discord.SelectOption(label="(none)", value="none")])

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            return
        listing = db.market_get(int(self.values[0]))
        if listing is None:
            await interaction.response.send_message("❌ That listing just sold!", ephemeral=True)
            return
        seller = await seller_name(interaction, listing["seller_id"])
        quick = config.quicksell_value(listing["rarity"])
        pct, one_in = config.combined_odds(listing["rarity"], listing["quality"])
        r_chance = config.RARITIES[listing["rarity"]]["chance"]
        q_chance = config.QUALITIES[listing["quality"]]["chance"]
        embed = discord.Embed(
            title=f"{listing['ore']} ({config.tier_name(listing['rarity'])}) — ${listing['price']:,}",
            description=f"Quality: **{listing['quality']}** — {q_chance:g}% (1 in {config.rarity_one_in(q_chance)} chance)\n"
                        f"Seller: **{seller}**\n"
                        f"Tier: **{config.tier_name(listing['rarity'])}** — {r_chance:g}% (1 in {config.rarity_one_in(r_chance)} chance)\n"
                        f"Odds: **{pct:.4g}%** ({one_in} chance)\n"
                        f"Quicksell value: **${quick:,}**",
            color=0x9C27B0)
        view = ListingInspectView(interaction.user.id, listing["id"])
        # public inspect (not private) so anyone can see + buy
        await interaction.response.send_message(embed=embed, view=view, ephemeral=False)


class ListingInspectView(discord.ui.View):
    """Buy button for one inspected listing. Bound to whoever opened it."""

    def __init__(self, buyer_id: int, listing_id: int):
        super().__init__(timeout=300)
        self.buyer_id = buyer_id
        self.listing_id = listing_id

    @discord.ui.button(label="Buy", style=discord.ButtonStyle.green, emoji="🛒")
    async def buy(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.buyer_id:
            await interaction.response.send_message("Inspect it yourself to buy it!", ephemeral=True)
            return
        await execute_buy(interaction, self.buyer_id, self.listing_id)
        button.disabled = True
        try:
            await interaction.message.edit(view=self)
        except Exception:
            pass


async def execute_buy(interaction: discord.Interaction, buyer_id: int, listing_id: int):
    """Shared buy flow (was /market_buy). Sends its own responses."""
    buyer = str(buyer_id)
    listing = db.market_get(listing_id)
    if listing is None:
        await interaction.response.send_message("❌ That listing just sold!", ephemeral=True)
        return
    if listing["seller_id"] == buyer:
        await interaction.response.send_message("❌ You can't buy your own listing.", ephemeral=True)
        return
    u = db.get_user(buyer)
    if u["balance"] < listing["price"]:
        await interaction.response.send_message(
            f"❌ You need ${listing['price']:,} but only have ${u['balance']:,}.", ephemeral=True)
        return
    ok, msg = db.market_buy(listing_id, buyer)
    if not ok:
        await interaction.response.send_message(f"❌ {msg}", ephemeral=True)
        return
    # market obtains count toward obtain achievements too
    bu0 = db.get_user(buyer)
    bumps = {}
    if listing["rarity"] == "DIH":
        bumps["dih_pulls"] = bu0.get("dih_pulls", 0) + 1
    if listing["quality"] == "Perfect":
        bumps["perfect_pulls"] = bu0.get("perfect_pulls", 0) + 1
    if bumps:
        db.update_user(buyer, **bumps)
    # seller side: merchant + supplier + silent money tiers + mail + collector recheck
    db.grant_achievement(listing["seller_id"], "merchant")
    if listing["rarity"] == "DIH":
        db.grant_achievement(listing["seller_id"], "supplier")
    check_achievements(listing["seller_id"], db.get_user(listing["seller_id"]), "", "")  # silent
    sync_collectors(listing["seller_id"])
    buyer_name = await display_name(interaction, buyer)
    db.add_mail(listing["seller_id"],
                f"💰 **{buyer_name}** bought your **{listing['ore']} ({listing['quality']})** "
                f"for **${listing['price']:,}**!")
    # buyer side: rarest buy tracking (global best, kept even if sold)
    bu = db.get_user(buyer)
    try:
        qualities = list(config.QUALITIES.keys())
        new_idx = (config.tier_index(listing["rarity"]), qualities.index(listing["quality"]))
        cur = parse_best(bu.get("rarest_buy", ""))
        cur_idx = (config.tier_index(cur[0]), qualities.index(cur[1])) if cur else (-1, -1)
        if new_idx > cur_idx:
            db.update_user(buyer, rarest_buy=f"{listing['rarity']}|{listing['quality']}|{listing['ore']}")
    except Exception:
        pass
    db.grant_achievement(buyer, "customer")
    newly = check_achievements(buyer, db.get_user(buyer), listing["rarity"], listing["quality"])
    if listing["rarity"] == "DIH" and db.grant_achievement(buyer, "investor"):
        newly.append(f"🏆 **{config.ACHIEVEMENTS['investor'][0]}** — {config.ACHIEVEMENTS['investor'][1]}")
    await interaction.response.send_message(
        f"✅ {interaction.user.mention} bought **{listing['quality']} {listing['ore']}** "
        f"({config.tier_name(listing['rarity'])}) for **${listing['price']:,}**!")
    if newly:
        await achievement_reply(interaction, interaction.user.mention, newly)


class MarketBrowser(discord.ui.View):
    """Stage-based market browser: ore filter -> quality filter -> sort, inspect + pages everywhere."""

    def __init__(self, owner_id: int, ore: str | None = None, quality: str | None = None,
                 sort: str | None = None, page: int = 0, pages: int = 1,
                 chunk: list[dict] | None = None, tier: str | None = None):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.ore = ore
        self.quality = quality
        self.sort = sort
        self.page = page
        self.pages = pages
        self.tier = tier
        # tier hides once an ore is picked (ore implies its tier).
        # Every layout below fits Discord's 5-row limit.
        if ore is None:
            self.add_item(MarketTierSelect(owner_id, current=tier))
        if ore is None and quality is None:
            self.add_item(MarketOreFilterSelect(owner_id, tier=tier))
            self.add_item(MarketQualityTopSelect(owner_id, tier=tier))
            self.add_item(MarketSortSelect(owner_id, ore, quality, sort, tier=tier))
        elif quality is None:
            self.add_item(MarketOreFilterSelect(owner_id, current=ore, tier=tier))
            self.add_item(MarketQualityFilterSelect(owner_id, ore, tier=tier))
            self.add_item(MarketSortSelect(owner_id, ore, quality, sort, tier=tier))
            if chunk:
                self.add_item(MarketListingInspectSelect(chunk))
        elif ore is None:
            self.add_item(MarketQualityTopSelect(owner_id, current=quality, tier=tier))
            self.add_item(MarketSortSelect(owner_id, ore, quality, sort, tier=tier))
            if chunk:
                self.add_item(MarketListingInspectSelect(chunk))
        else:
            self.add_item(MarketOreFilterSelect(owner_id, current=ore, tier=tier))
            self.add_item(MarketQualityFilterSelect(owner_id, ore, current=quality, tier=tier))
            self.add_item(MarketSortSelect(owner_id, ore, quality, sort, tier=tier))
            if chunk:
                self.add_item(MarketListingInspectSelect(chunk))

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


@bot.tree.command(name="market_view", description="Browse the player market (latest + filters).")
async def market_view(interaction: discord.Interaction):
    await interaction.response.defer()
    if not db.market_browse(limit=1):
        await interaction.followup.send("📭 Market is empty! Be the first with `/market_list`.")
        return
    embed, view = await render_market(interaction.user.id, interaction)
    await interaction.followup.send(embed=embed, view=view)


@bot.tree.command(name="market_cancel", description="Cancel your listings (pick from dropdowns).")
async def market_cancel(interaction: discord.Interaction):
    uid = str(interaction.user.id)
    listings = db.market_by_seller(uid, limit=10)
    if not listings:
        await interaction.response.send_message("📭 You have no listings! Make one with `/market_list`.",
                                                ephemeral=True)
        return
    embed, view = render_cancel(uid, None, None, listings)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


@bot.tree.command(name="market_cancel_all", description="Cancel ALL your listings (items return to you).")
async def market_cancel_all(interaction: discord.Interaction):
    n = db.market_cancel_all(str(interaction.user.id))
    if n == 0:
        await interaction.response.send_message("📭 You have no listings!", ephemeral=True)
    else:
        await interaction.response.send_message(
            f"🚫 Cancelled **{n}** listing(s) — items returned to your inventory.", ephemeral=True)


def cancel_lines(listings: list[dict], selected_id: int | None = None) -> list[str]:
    lines = []
    for l in listings:
        mark = "▶ " if l["id"] == selected_id else ""
        lines.append(f"{mark}`{l['id']}` **{l['ore']} ({l['quality']})** — **${l['price']:,}**")
    return lines


def render_cancel(owner_id: str, ore: str | None, quality: str | None,
                  listings: list[dict] | None = None, selected_id: int | None = None,
                  tier: str | None = None):
    """Build the cancel embed + view for a stage. Falls back to stage 1 if empty."""
    if listings is None:
        listings = db.market_by_seller(owner_id, ore, quality, limit=10, tier=tier)
    if not listings:
        # fall back to your latest 10 overall
        ore, quality, selected_id, tier = None, None, None, None
        listings = db.market_by_seller(owner_id, limit=10)
    if ore is None and tier is None:
        title = "🚫 Your listings — latest 10"
    elif ore is None:
        title = f"🚫 Your {config.tier_name(tier)} listings — latest 10"
    elif quality is None:
        title = f"🚫 Your {ore} — latest 10"
    else:
        title = f"🚫 Your {ore} ({quality}) — latest 10"
    embed = discord.Embed(title=title, description="\n".join(cancel_lines(listings, selected_id))
                          if listings else "Nothing here!", color=0xF44336)
    return embed, CancelBrowser(int(owner_id), ore=ore, quality=quality,
                                listings=listings, selected_id=selected_id, tier=tier)


class CancelTierSelect(discord.ui.Select):
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
            await interaction.response.send_message("That's not yours!", ephemeral=True)
            return
        tier = None if self.values[0] == "all" else self.values[0]
        embed, view = render_cancel(str(self.owner_id), None, None, tier=tier)
        await interaction.response.edit_message(embed=embed, view=view)


class CancelOreSelect(discord.ui.Select):
    def __init__(self, owner_id: int, tier: str | None = None):
        self.owner_id = owner_id
        self.tier = tier
        ores = db.market_seller_ores(str(owner_id))
        if tier:
            ores = [o for o in ores if tier in o["rarities"]]
        ores.sort(key=lambda o: min(config.tier_index(r) for r in o["rarities"]))
        options = []
        for o in ores[:25]:
            tiers = ", ".join(config.tier_name(r) for r in sorted(o["rarities"], key=config.tier_index))
            options.append(discord.SelectOption(
                label=f"{o['ore']} ({tiers})"[:100], value=o["ore"]))
        super().__init__(placeholder="Filter by ore…", options=options or [
            discord.SelectOption(label="(no listings)", value="none")])

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("That's not yours!", ephemeral=True)
            return
        if self.values[0] == "none":
            return
        embed, view = render_cancel(str(self.owner_id), self.values[0], None, tier=self.tier)
        await interaction.response.edit_message(embed=embed, view=view)


class CancelQualitySelect(discord.ui.Select):
    def __init__(self, owner_id: int, ore: str, tier: str | None = None):
        self.owner_id = owner_id
        self.ore = ore
        self.tier = tier
        quals = db.market_seller_qualities(str(owner_id), ore)
        options = [discord.SelectOption(label=q, value=q) for q in quals[:25]]
        super().__init__(placeholder="Filter by quality…", options=options or [
            discord.SelectOption(label="(none)", value="none")])

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("That's not yours!", ephemeral=True)
            return
        if self.values[0] == "none":
            return
        embed, view = render_cancel(str(self.owner_id), self.ore, self.values[0], tier=self.tier)
        await interaction.response.edit_message(embed=embed, view=view)


class CancelListingSelect(discord.ui.Select):
    """Every stage has one: pick which listing to cancel, then hit the button."""

    def __init__(self, owner_id: int, listings: list[dict]):
        self.owner_id = owner_id
        options = []
        for l in listings[:25]:
            label = f"{l['ore']} ({l['quality']}) — ${l['price']:,}"[:100]
            options.append(discord.SelectOption(label=label, description=f"ID {l['id']}",
                                                value=str(l["id"])))
        super().__init__(placeholder="Choose one to cancel…", options=options or [
            discord.SelectOption(label="(none)", value="none")])

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("That's not yours!", ephemeral=True)
            return
        if self.values[0] == "none":
            return
        view: CancelBrowser = self.view
        embed, _ = render_cancel(str(self.owner_id), view.ore, view.quality,
                                 selected_id=int(self.values[0]))
        view.selected_id = int(self.values[0])
        await interaction.response.edit_message(embed=embed, view=view)


class CancelBrowser(discord.ui.View):
    def __init__(self, owner_id: int, ore: str | None = None, quality: str | None = None,
                 listings: list[dict] | None = None, selected_id: int | None = None,
                 tier: str | None = None):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.ore = ore
        self.quality = quality
        self.tier = tier
        self.listings = listings or []
        self.selected_id = selected_id
        self.add_item(CancelTierSelect(owner_id, current=tier))
        if ore is None:
            self.add_item(CancelOreSelect(owner_id, tier=tier))
        elif quality is None:
            self.add_item(CancelQualitySelect(owner_id, ore, tier=tier))
        if self.listings:
            self.add_item(CancelListingSelect(owner_id, self.listings))

    @discord.ui.button(label="Cancel this", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def cancel_this(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("That's not yours!", ephemeral=True)
            return
        if self.selected_id is None:
            await interaction.response.send_message("☝️ Pick a listing from the dropdown first!",
                                                    ephemeral=True)
            return
        ok, msg = db.market_cancel(self.selected_id, str(self.owner_id))
        embed, view = render_cancel(str(self.owner_id), self.ore, self.quality, tier=self.tier)
        await interaction.response.edit_message(embed=embed, view=view)
        await interaction.followup.send(("✅ " if ok else "❌ ") + msg, ephemeral=True)


# ----- stats / streak / achievements -----

@bot.tree.command(name="stats", description="See stats (yours, or a public one).")
async def stats(interaction: discord.Interaction, user: discord.User | None = None):
    target = user or interaction.user
    tid = str(target.id)
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
    """Stored global best looks like 'DIH|Perfect|Painite'. Old data may be just a rarity."""
    if not val:
        return None
    parts = val.split("|")
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]
    return None


class StatsView(discord.ui.View):
    """3 pages: stats, rarest spun inspect, rarest bought inspect (global bests, kept forever)."""

    def __init__(self, viewer_id: int, target_id: str, target_name: str):
        super().__init__(timeout=300)
        self.viewer_id = viewer_id
        self.target_id = target_id
        self.target_name = target_name
        self.page = 0

    async def make_embed(self, interaction: discord.Interaction) -> discord.Embed:
        u = db.get_user(self.target_id)
        if self.page == 0:
            inv_count = db.count_inventory(self.target_id)
            ach_n = len(db.get_achievements(self.target_id))
            ach_total = len(config.ACHIEVEMENTS)
            _, iv = db.inventory_value(self.target_id)
            _, vv = db.vault_value(self.target_id)
            assets = iv + vv
            embed = discord.Embed(title=f"📊 {self.target_name}'s Stats", color=0x00BCD4)
            embed.description = (
                f"🎰 Total spins: **{u['total_spins']}**\n"
                f"🪨 Low Tier: **{u['low_pulls']}** | 💚 Mid Tier: **{u['mid_pulls']}** | 💎 High Tier: **{u['high_pulls']}**\n"
                f"👑 Elite Tier: **{u['elite_pulls']}** | 🌟 DIH Tier: **{u['dih_pulls']}**\n"
                f"🔥 Streak: **{u['streak']}** days (best: **{u['longest_streak']}**)\n"
                f"💰 Balance: **${u['balance']:,}** | Earned: **${u['total_earned']:,}**\n"
                f"🎒 Inventory: **{inv_count}** ores\n"
                f"💸 Inventory quicksell value: **${iv:,}**\n"
                f"💎 Assets (inventory + vault): **${assets:,}**\n"
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
        text = await inspect_text(interaction, self.target_id, rarity, quality, ore, 1)
        return discord.Embed(title=title, description=text,
                             color=config.RARITIES.get(rarity, {}).get("color", 0x9E9E9E))

    async def _turn(self, interaction: discord.Interaction, delta: int):
        if interaction.user.id != self.viewer_id:
            await interaction.response.send_message("Run `/stats` yourself to browse!",
                                                    ephemeral=True)
            return
        self.page = (self.page + delta) % 3
        await interaction.response.edit_message(embed=await self.make_embed(interaction), view=self)

    @discord.ui.button(emoji="◀", style=discord.ButtonStyle.secondary, row=1)
    async def prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._turn(interaction, -1)

    @discord.ui.button(emoji="▶", style=discord.ButtonStyle.secondary, row=1)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._turn(interaction, 1)


@bot.tree.command(name="achievements", description="See achievements (yours, or a public one).")
@app_commands.describe(user="Optional: view another player's public achievements")
async def achievements(interaction: discord.Interaction, user: discord.User | None = None):
    target = user or interaction.user
    tid = str(target.id)
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


class AchievementsFilterSelect(discord.ui.Select):
    def __init__(self, current: str):
        super().__init__(placeholder="Show…", options=[
            discord.SelectOption(label="All", value="all", default=(current == "all")),
            discord.SelectOption(label="Completed", value="done", default=(current == "done")),
            discord.SelectOption(label="Incomplete", value="todo", default=(current == "todo")),
        ], row=0)

    async def callback(self, interaction: discord.Interaction):
        view: AchievementsView = self.view
        if interaction.user.id != view.owner_id:
            await interaction.response.send_message("That's not yours! Run `/achievements` yourself.",
                                                    ephemeral=True)
            return
        view.filter = self.values[0]
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
        self.filter = "all"
        self.all_items = list(config.ACHIEVEMENTS.items())
        self.items = self.all_items
        self.pages = max(1, (len(self.items) + self.PER_PAGE - 1) // self.PER_PAGE)
        self.filter_select = AchievementsFilterSelect(self.filter)
        self.add_item(self.filter_select)

    def sync_select(self):
        """Rebuild dropdown options so the shown selection matches the filter."""
        self.filter_select.options = [
            discord.SelectOption(label="All", value="all", default=(self.filter == "all")),
            discord.SelectOption(label="Completed", value="done", default=(self.filter == "done")),
            discord.SelectOption(label="Incomplete", value="todo", default=(self.filter == "todo")),
        ]

    def refresh_items(self):
        if self.filter == "done":
            self.items = [(a, v) for a, v in self.all_items if a in self.unlocked]
        elif self.filter == "todo":
            self.items = [(a, v) for a, v in self.all_items if a not in self.unlocked]
        else:
            self.items = self.all_items
        self.pages = max(1, (len(self.items) + self.PER_PAGE - 1) // self.PER_PAGE)

    def make_embed(self) -> discord.Embed:
        chunk = self.items[self.page * self.PER_PAGE:(self.page + 1) * self.PER_PAGE]
        filt = {"all": "", "done": " — completed", "todo": " — incomplete"}[self.filter]
        lines = [f"{'✅' if aid in self.unlocked else '🔒'} **{name}** — {desc}"
                 for aid, (name, desc) in chunk]
        embed = discord.Embed(
            title=f"🏆 Achievements ({len(self.unlocked)}/{len(self.all_items)}){filt}",
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

    @discord.ui.button(label="", emoji="◀", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._turn(interaction, -1)

    @discord.ui.button(label="", emoji="▶", style=discord.ButtonStyle.secondary, row=1)
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
        q_lines.append(f"{qi['emoji']} **{q}** — {qi['chance']}%")
    embed.add_field(name="✨ Qualities (rolled on every spin)", value="\n".join(q_lines), inline=False)
    embed.add_field(
        name="📊 How combo odds work",
        value="rarity% × quality% — e.g. DIH Perfect = 0.1% × 1% = **0.001% (1 in 100,000)**",
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

    def __init__(self, owner_id: int):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.tiers = list(config.RARITIES.keys())
        self.tier = self.tiers[0]
        self.tier_select = OresTierSelect(self.tier)
        self.add_item(self.tier_select)

    def make_embed(self) -> discord.Embed:
        r = self.tier
        ri = config.RARITIES[r]
        idx = self.tiers.index(r)
        try:
            owned = {row["ore"] for row in db.get_inventory_grouped(str(self.owner_id))}
            owned |= {row["ore"] for row in db.vault_grouped(str(self.owner_id))}
        except Exception:
            owned = set()
        embed = discord.Embed(
            title=f"{ri['dot']} {config.tier_name(r)} Ores",
            description="\n".join(f"{'✅' if o in owned else '⬜'} **{o}**" for o in config.ORES[r]),
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
async def ores(interaction: discord.Interaction):
    view = OresView(interaction.user.id)
    await interaction.response.send_message(embed=view.make_embed(), view=view)


async def display_name(interaction: discord.Interaction, user_id: str) -> str:
    """Plain name, never a ping."""
    try:
        uid = int(user_id)
    except (ValueError, TypeError):
        return str(user_id)
    if interaction.guild:
        m = interaction.guild.get_member(uid)
        if m:
            return m.display_name
    u = interaction.client.get_user(uid)
    if u:
        return u.display_name
    try:
        u = await interaction.client.fetch_user(uid)
        return u.display_name
    except Exception:
        return f"User {uid}"


@bot.tree.command(name="baltop", description="Top 10 richest players.")
async def baltop(interaction: discord.Interaction):
    await interaction.response.defer()
    top = db.top_balances(10)
    if not top:
        await interaction.followup.send("📭 Nobody has any money yet! Use `/spin` then `/quicksell`.")
        return
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    top_ids = set()
    for i, row in enumerate(top):
        top_ids.add(row["user_id"])
        medal = medals[i] if i < 3 else f"`#{i+1}`"
        name = await display_name(interaction, row["user_id"])
        lines.append(f"{medal} **{name}** — **${row['balance']:,}** ({row['total_spins']} spins)")
    # If the caller isn't on the board, show their placement relative to everyone
    uid = str(interaction.user.id)
    if uid not in top_ids:
        rank = db.get_rank(uid)
        u = db.get_user(uid)
        name = await display_name(interaction, uid)
        lines.append(f"\n`#{rank}` **{name}** — **${u['balance']:,}** ({u['total_spins']} spins)")
    await interaction.followup.send("💰 **Richest Players**\n" + "\n".join(lines))


@bot.tree.command(name="help", description="Show every command.")
async def help_cmd(interaction: discord.Interaction):
    await interaction.response.send_message(
        "🤖 **BoBot Commands**\n"
        "🎰 `/spin [amount]` — roll ores\n"
        "💰 `/baltop` — top 10 richest players\n"
        "💵 `/balance [@user]` — balance, assets, bank + transfer\n"
        "🏦 `/bank [@user]` — bank + withdraw\n"
        "🎒 `/inventory [@user]` — ores, ore pages, quicksell/vault\n"
        "🗝️ `/vault [@user]` — long-term storage + un-vault\n"
        "💸 `/quicksell_all` — sell everything instantly\n"
        "📦 `/market_list` — list an ore (dropdown picker)\n"
        "🏪 `/market_view` — browse, filter, inspect & buy\n"
        "🚫 `/market_cancel` — take down a listing (dropdowns + button)\n"
        "🚫 `/market_cancel_all` — take down ALL listings\n"
        "🔄 `/trade @user` — trade ores (one-sided OK, both accept)\n"
        "🎁 `/gift ore @user` — gift ores (they get mail)\n"
        "🎁 `/gift money @user` — gift balance/bank money\n"
        "📊 `/stats [@user]` — 3 pages: stats, rarest spin, rarest buy\n"
        "🏆 `/achievements [@user]` — badges (paged + filter)\n"
        "🎲 `/odds` — rarities, odds, values\n"
        "⛏️ `/ores` — browse every ore by tier\n"
        "📬 `/mail [@user]` — notifications\n"
        "⚙️ `/settings` — privacy settings\n"
        "❓ `/faq` — how quicksell/market/spins work\n"
        "❓ `/help` — this message"
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
    tid = str(target.id)
    t = db.get_user(tid)
    public = bool(t.get("mail_public", 0))
    if target.id != interaction.user.id and not public:
        await interaction.response.send_message(
            f"🔒 **{(await display_name(interaction, tid))}'s** mail is private.", ephemeral=True)
        return
    if target.id == interaction.user.id:
        db.mark_mail_read(tid)  # checking your mail clears the spin nudge
    view = MailView(interaction.user.id, target.id, await display_name(interaction, tid))
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
    ("achievements", "ach_public", "🏆 Achievements"),
    ("stats", "stats_public", "📊 Stats"),
    ("vault", "vault_public", "🗝️ Vault"),
    ("bank", "bank_public", "🏦 Bank"),
    ("balance", "balance_public", "💵 Balance"),
    ("mail", "mail_public", "📬 Mail"),
    ("market", "market_public", "🏪 Market listings (your seller name)"),
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
            embed=settings_embed(str(view.owner_id), view.selected), view=view)


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
        db.update_user(str(view.owner_id), timezone=self.values[0])
        view.sync_select()
        await interaction.response.edit_message(
            embed=settings_embed(str(view.owner_id), view.selected), view=view)


class SettingsView(discord.ui.View):
    def __init__(self, owner_id: int):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.selected = "inventory"
        self._rebuild()

    def _rebuild(self):
        self.clear_items()
        self.setting_select = SettingSelect(self.selected)
        self.add_item(self.setting_select)
        if self.selected == "timezone":
            tz = db.get_user(str(self.owner_id)).get("timezone", "UTC")
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
            uid = str(view.owner_id)
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


@bot.tree.command(name="settings", description="Privacy settings (inventory, achievements, stats).")
async def settings(interaction: discord.Interaction):
    view = SettingsView(interaction.user.id)
    await interaction.response.send_message(
        embed=settings_embed(str(interaction.user.id), view.selected), view=view, ephemeral=True)


# ---------- trading (one ore per side, both accept; persisted in DB) ----------

trades: dict[int, dict] = {}


def _pick_loads(s: str | None):
    return json.loads(s) if s else None


def _pick_dumps(p) -> str | None:
    return json.dumps(p) if p else None


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
    embed.set_footer(text="Offers optional — one side can give nothing. Both hit Accept.")
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
                          trade_tid=self.tid, trade_side="a")
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
                          trade_tid=self.tid, trade_side="b")
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
        a, b = str(t["a_id"]), str(t["b_id"])
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
                content="❌ Trade failed — someone no longer has their ore.", embed=None, view=None)
            return
        if ia is not None:
            db.add_item(b, ia["rarity"], ia["quality"], ia["ore"], ia.get("origin", "spin"), ia.get("origin_detail", ""))
        if ib is not None:
            db.add_item(a, ib["rarity"], ib["quality"], ib["ore"], ib.get("origin", "spin"), ib.get("origin_detail", ""))
        trades.pop(t["id"], None)
        db.delete_trade(t["id"])
        # obtain achievements (incl. Jackpot) per recipient + collector rechecks
        newly_a, newly_b = [], []
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
    tid = db.create_trade(str(interaction.user.id), str(user.id),
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


@bot.tree.command(name="admin_event_give", description="[ADMIN] Event prize (grants Winner).")
@app_commands.describe(user="Winner", amount="How much ($)", description="What event they won")
async def admin_event_give(interaction: discord.Interaction, user: discord.User,
                           amount: app_commands.Range[int, 1, 100_000_000],
                           description: str = "BoBo event"):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Admins only!", ephemeral=True)
        return
    uid = str(user.id)
    u = db.get_user(uid)
    db.update_user(uid, balance=u["balance"] + amount, total_earned=u["total_earned"] + amount)
    newly = check_achievements(uid, db.get_user(uid), "", "")
    if db.grant_achievement(uid, "winner"):
        newly.append(f"🏆 **{config.ACHIEVEMENTS['winner'][0]}** — {config.ACHIEVEMENTS['winner'][1]}")
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
    uid = str(user.id)
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
    uid = str(user.id)
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
    uid = str(user.id)
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
    uid = str(user.id)
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
        await interaction.response.edit_message(content="Phew. Cancelled — nothing was touched.",
                                                embed=None, view=self)
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
    db.set_setting("spins_per_day", str(pd))
    db.set_setting("max_spin", str(mx))
    await interaction.response.send_message(
        f"🎰 Spins set: **{pd}/day**, up to **{mx}** per /spin.")


# ---------- gifting (one-way, instant, notified via mail) ----------

class ScopeGiftModal(discord.ui.Modal, title="Gift — how many?"):
    """Moves up to N (or ALL) of the current scope to the recipient + mail."""

    amount = discord.ui.TextInput(label="How many? (number or ALL)", placeholder="e.g. 1 or ALL",
                                  max_length=8)

    def __init__(self, owner_id: int, recip_id: int, ore: str | None, quality: str | None,
                 tier: str | None = None):
        super().__init__()
        self.owner_id = owner_id
        self.recip_id = recip_id
        self.ore = ore
        self.quality = quality
        self.tier = tier

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("That's not yours!", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        g, r = str(self.owner_id), str(self.recip_id)
        targets = _scope_targets(g, "inv", self.ore, self.quality, self.tier)
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
        desc_parts = []
        for t in targets:
            want = t["count"] if gift_all else min(limit - moved, t["count"])
            if want <= 0:
                break
            remaining = want
            for origin, c in db.origin_counts(g, t["rarity"], t["quality"], t["ore"]).items():
                k = min(c, remaining)
                if k <= 0:
                    continue
                got = db.remove_many_items(g, t["rarity"], t["quality"], t["ore"], k, origin=origin)
                db.add_many_items(r, [(t["rarity"], t["quality"], t["ore"])] * got, origin=origin)
                moved += got
                remaining -= got
                if remaining <= 0:
                    break
            desc_parts.append(f"{t['ore']} ({t['quality']})")
            if not gift_all and moved >= limit:
                break
        giver_name = await display_name(interaction, g)
        what = ", ".join(desc_parts[:3]) + ("…" if len(desc_parts) > 3 else "")
        db.add_mail(r, f"🎁 **{giver_name}** gifted you **{moved}x** {what}!")
        sync_collectors(g)  # giver may have broken a set
        # recipient obtain checks per distinct gifted pair (Jackpot needs the combo)
        gifted_pairs = sorted({(t["rarity"], t["quality"]) for t in targets})
        newly2 = []
        if moved > 0:
            first2 = True
            for r, q in gifted_pairs:
                newly2 += check_achievements(r, db.get_user(r), r, q, collectors=first2)
                first2 = False
        recip_name = await display_name(interaction, r)
        await interaction.followup.send(
            f"🎁 Gifted **{moved}x** {what} to **{recip_name}**!", ephemeral=True)
        if newly2:
            await achievement_reply(interaction, f"<@{r}>", newly2)


gift_group = app_commands.Group(name="gift", description="Gift ores or money.")


@gift_group.command(name="ore", description="Gift ores to another player (they get mail).")
@app_commands.describe(user="Who gets the gift")
async def gift_ore(interaction: discord.Interaction, user: discord.User):
    if user.id == interaction.user.id:
        await interaction.followup.send("❌ You can't gift yourself!", ephemeral=True)
        return
    if user.bot:
        await interaction.followup.send("❌ You can't gift a bot!", ephemeral=True)
        return
    if db.count_inventory(str(interaction.user.id)) == 0:
        await interaction.followup.send("🎒 Your inventory is empty!", ephemeral=True)
        return
    view = InvBrowser(interaction.user.id, interaction.user.id,
                      interaction.user.display_name, False, source="gift",
                      recip_id=user.id,
                      recip_name=await display_name(interaction, str(user.id)))
    await interaction.followup.send(embed=view.render(), view=view, ephemeral=True)


@gift_group.command(name="money", description="Gift money from balance or bank.")
@app_commands.describe(user="Who gets the money", amount="How much ($)",
                       source="Take it from balance or bank")
@app_commands.choices(source=[app_commands.Choice(name="Balance", value="balance"),
                              app_commands.Choice(name="Bank", value="bank")])
async def gift_money(interaction: discord.Interaction, user: discord.User,
                     amount: app_commands.Range[int, 1, 100_000_000], source: str):
    if user.id == interaction.user.id:
        await interaction.followup.send("❌ You can't gift yourself!", ephemeral=True)
        return
    if user.bot:
        await interaction.followup.send("❌ You can't gift a bot!", ephemeral=True)
        return
    u = db.get_user(str(interaction.user.id))
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
        g, r = str(self.giver_id), str(self.recip_id)
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
        check_achievements(r, db.get_user(r), "", "")  # money tiers still unlock
        giver_name = await display_name(interaction, g)
        db.add_mail(r, f"🎁 **{giver_name}** gifted you **${self.amount:,}**!")
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
        raise SystemExit("Missing DISCORD_TOKEN — copy .env.example to .env and paste your bot token.")
    db.init_db()
    install_killswitch()
    bot.run(TOKEN)
