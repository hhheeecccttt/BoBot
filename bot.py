import os
import json
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


def time_until_reset() -> str:
    now = datetime.now(config.RESET_TIMEZONE)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    delta = tomorrow - now
    h, rem = divmod(int(delta.total_seconds()), 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m}m"


def check_achievements(user_id: str, u: dict, rarity: str, quality: str) -> list[str]:
    """Grant eligible achievements. Returns list of newly unlocked names."""
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
    if ts >= 10000:
        grant("spins_10000")
    if rarity == "Elite":
        grant("elite_pull")
    if rarity == "DIH":
        grant("dih_pull")
        if u.get("dih_pulls", 0) >= 10:
            grant("dih_10")
        if u.get("dih_pulls", 0) >= 100:
            grant("dih_100")
    if quality == "Perfect":
        grant("perfect_pull")
        if u.get("perfect_pulls", 0) >= 10:
            grant("perfect_10")
        if u.get("perfect_pulls", 0) >= 100:
            grant("perfect_100")
    bal = u["balance"]
    if bal >= 100:
        grant("rich_100")
    if bal >= 1000:
        grant("rich_1k")
    if bal >= 5000:
        grant("rich_5k")
    if bal >= 10000:
        grant("rich_10k")
    if bal >= 25000:
        grant("rich_25k")
    if bal >= 50000:
        grant("rich_50k")
    if bal >= 100000:
        grant("rich_100k")
    if bal >= 250000:
        grant("rich_250k")
    if bal >= 500000:
        grant("rich_500k")
    if bal >= 1000000:
        grant("rich_1m")
    if u.get("sell_count", 0) >= 10:
        grant("merchant_10")
    if u.get("buy_count", 0) >= 10:
        grant("customer_10")
    # collectors: own every ore of the tier right now
    for tier, aid in (("Low", "collector_low"), ("Mid", "collector_mid"),
                      ("High", "collector_high"), ("Elite", "collector_elite"),
                      ("DIH", "collector_dih")):
        if db.owns_all_ores(user_id, config.ORES[tier]):
            grant(aid)
    if all(db.owns_all_ores(user_id, ores) for ores in config.ORES.values()):
        grant("collector_all")
    if u["streak"] >= 3:
        grant("streak_3")
    if u["streak"] >= 7:
        grant("streak_7")
    if u["streak"] >= 30:
        grant("streak_30")
    if u["streak"] >= 100:
        grant("streak_100")
    return newly


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


def inspect_text(uid: str, rarity: str, quality: str, ore: str, count: int) -> str:
    """Quality first, then odds (% + 1-in), quicksell, origin, tip."""
    value_each = config.quicksell_value(rarity)
    pct, one_in = config.combined_odds(rarity, quality)
    origins = db.origin_counts(uid, rarity, quality, ore)
    market_n = origins.get("market", 0)
    if market_n >= count and count > 0:
        origin_line = "🛒 Bought on the player market\n"
    elif market_n > 0:
        origin_line = f"🛒 {market_n}x bought on the player market\n"
    else:
        origin_line = ""
    return (
        f"**{ore} ({quality}) x{count}**\n"
        f"Rarity: **{config.tier_name(rarity)}**\n"
        f"Odds: **{pct:.4g}%** ({one_in} chance)\n"
        f"Quicksell: **${value_each:,}** each (**${value_each * count:,}** for all)\n"
        f"{origin_line}"
        f"Tip: `/quicksell` to sell, `/market_list` to list it for other players."
    )


class QuicksellAmountModal(discord.ui.Modal, title="Quicksell"):
    amount = discord.ui.TextInput(label="How many? (number or ALL)", placeholder="e.g. 5 or ALL",
                                  max_length=8)

    def __init__(self, owner_id: int, rarity: str, quality: str, ore: str):
        super().__init__()
        self.owner_id = owner_id
        self.rarity = rarity
        self.quality = quality
        self.ore = ore

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("That's not yours!", ephemeral=True)
            return
        uid = str(self.owner_id)
        stacks = db.get_ore_detail(uid, self.ore)
        stack = next((s for s in stacks if s["rarity"] == self.rarity and s["quality"] == self.quality), None)
        if not stack:
            await interaction.response.send_message("❌ That stack is gone.", ephemeral=True)
            return
        raw = str(self.amount.value).strip().lower()
        if raw in ("all", "max"):
            n = stack["count"]
        else:
            try:
                n = int(raw)
            except ValueError:
                await interaction.response.send_message("❌ Type a number or ALL.", ephemeral=True)
                return
        n = max(0, min(n, stack["count"]))
        if n <= 0:
            await interaction.response.send_message("❌ Nothing to sell.", ephemeral=True)
            return
        earned = 0
        for _ in range(n):
            if db.remove_one_item(uid, self.rarity, self.quality, self.ore) is None:
                break
            earned += config.quicksell_value(self.rarity)
        u = db.get_user(uid)
        db.update_user(uid, balance=u["balance"] + earned, total_earned=u["total_earned"] + earned)
        newly = check_achievements(uid, db.get_user(uid), "", "")
        await interaction.response.send_message(
            f"💸 Sold **{n}x {self.ore} ({self.quality})** for **${earned:,}**!", ephemeral=True)
        if newly:
            await interaction.followup.send(
                f"{interaction.user.mention} 🏆 Achievement unlocked!\n" + "\n".join(newly),
                ephemeral=True)


class QualityInspectSelect(discord.ui.Select):
    """Level 2 dropdown: pick a quality stack of one ore to inspect."""

    def __init__(self, viewer_id: int, target_id: int, stacks: list[dict], public: bool):
        self.viewer_id = viewer_id
        self.target_id = target_id
        self.public = public
        self.lookup = {(s["rarity"], s["quality"]): s for s in stacks}
        options = []
        for s in stacks[:25]:  # Discord limit
            label = f"{s['ore']} ({s['quality']}) x{s['count']}"[:100]
            desc = f"{config.tier_name(s['rarity'])} • quicksell ${config.quicksell_value(s['rarity']):,} each"[:100]
            options.append(discord.SelectOption(label=label, description=desc,
                                                value=f"{s['rarity']}|{s['quality']}"))
        super().__init__(placeholder="Inspect a stack…", options=options or [
            discord.SelectOption(label="(empty)", value="none")])

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.viewer_id:
            await interaction.response.send_message("Run `/inventory` yourself to browse!",
                                                    ephemeral=True)
            return
        if self.values[0] == "none":
            return
        rarity, quality = self.values[0].split("|")
        s = self.lookup[(rarity, quality)]
        self.view.selected = (rarity, quality)
        await interaction.response.send_message(
            inspect_text(str(self.target_id), rarity, quality, s["ore"], s["count"]),
            ephemeral=not self.public)


class OrePageView(discord.ui.View):
    """Level 2 view: quality breakdown of one ore + inspect dropdown + quicksell button."""

    def __init__(self, viewer_id: int, target_id: int, target_name: str, ore: str,
                 stacks: list[dict], public: bool, quality_filter: str | None = None):
        super().__init__(timeout=180)
        self.viewer_id = viewer_id
        self.target_id = target_id
        self.target_name = target_name
        self.ore = ore
        self.stacks = stacks
        self.public = public
        self.quality_filter = quality_filter
        self.selected = (stacks[0]["rarity"], stacks[0]["quality"]) if stacks else None
        if stacks:
            self.add_item(QualityInspectSelect(viewer_id, target_id, stacks, public))
        if viewer_id != target_id:
            self.quicksell.disabled = True

    def page_embed(self) -> discord.Embed:
        n, v = db.inventory_value(str(self.target_id), self.ore, self.quality_filter)
        lines = []
        for s in self.stacks:
            mark = "▶ " if (s["rarity"], s["quality"]) == self.selected else ""
            lines.append(f"{mark}**{s['ore']} ({s['quality']})** x{s['count']}")
        title = f"⛏️ {self.ore} ({n} ores) (${v:,})"
        if self.quality_filter:
            title += f" — {self.quality_filter} only"
        return discord.Embed(title=title, description="\n".join(lines), color=0x00BCD4)

    @discord.ui.button(label="Quicksell", style=discord.ButtonStyle.green, emoji="💸")
    async def quicksell(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.viewer_id or self.viewer_id != self.target_id:
            await interaction.response.send_message("That's not yours!", ephemeral=True)
            return
        if not self.selected:
            await interaction.response.send_message("Nothing to sell!", ephemeral=True)
            return
        rarity, quality = self.selected
        await interaction.response.send_modal(
            QuicksellAmountModal(self.viewer_id, rarity, quality, self.ore))

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, emoji="↩")
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.viewer_id:
            await interaction.response.send_message("That's not yours! Run `/inventory` yourself.",
                                                    ephemeral=True)
            return
        embed, view = render_inventory(str(self.target_id), self.target_name,
                                       self.viewer_id, self.public, self.quality_filter)
        await interaction.response.edit_message(embed=embed, view=view)


class QualityFilterSelect(discord.ui.Select):
    """Level 1: show only one quality (or everything)."""

    def __init__(self, viewer_id: int, target_id: int, target_name: str, public: bool,
                 current: str | None):
        self.viewer_id = viewer_id
        self.target_id = target_id
        self.target_name = target_name
        self.public = public
        options = [discord.SelectOption(label="All qualities", value="all")]
        for q in config.QUALITIES:
            options.append(discord.SelectOption(label=f"Only {q}", value=q,
                                                default=(q == current)))
        super().__init__(placeholder="Sort by quality…", options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.viewer_id:
            await interaction.response.send_message("That's not yours! Run `/inventory` yourself.",
                                                    ephemeral=True)
            return
        quality = None if self.values[0] == "all" else self.values[0]
        embed, view = render_inventory(str(self.target_id), self.target_name,
                                       self.viewer_id, self.public, quality)
        await interaction.response.edit_message(embed=embed, view=view)


class OreSelect(discord.ui.Select):
    """Level 1 dropdown: pick which ore page to open."""

    def __init__(self, viewer_id: int, target_id: int, ores: list[dict], public: bool,
                 quality_filter: str | None):
        self.viewer_id = viewer_id
        self.target_id = target_id
        self.public = public
        self.quality_filter = quality_filter
        options = []
        for o in ores[:25]:  # Discord limit
            label = f"{ore_tier_label(o['ore'], o['rarities'])} x{o['count']}"[:100]
            options.append(discord.SelectOption(label=label, value=o["ore"]))
        super().__init__(placeholder="Choose which ore page to open…", options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.viewer_id:
            await interaction.response.send_message("That's not yours! Run `/inventory` yourself.",
                                                    ephemeral=True)
            return
        ore = self.values[0]
        stacks = db.get_ore_detail(str(self.target_id), ore, self.quality_filter)
        if not stacks:
            await interaction.response.send_message("Nothing there anymore.", ephemeral=True)
            return
        view = OrePageView(self.viewer_id, self.target_id,
                           await display_name(interaction, str(self.target_id)),
                           ore, stacks, self.public, self.quality_filter)
        await interaction.response.send_message(
            embed=view.page_embed(), view=view, ephemeral=not self.public)


class InventoryView(discord.ui.View):
    def __init__(self, viewer_id: int, target_id: int, target_name: str, ores: list[dict],
                 public: bool, quality_filter: str | None = None):
        super().__init__(timeout=180)
        self.add_item(QualityFilterSelect(viewer_id, target_id, target_name, public, quality_filter))
        if ores:
            self.add_item(OreSelect(viewer_id, target_id, ores, public, quality_filter))


def render_inventory(target_id: str, target_name: str, viewer_id: int, public: bool,
                     quality: str | None = None) -> tuple[discord.Embed, InventoryView]:
    if quality:
        ores = db.get_ores_by_quality(target_id, quality)
    else:
        ores = db.get_ores_overview(target_id)
    ores.sort(key=lambda o: (min(config.tier_index(r) for r in o["rarities"]), -o["count"]))
    n, v = db.inventory_value(target_id, quality=quality)
    title = f"🎒 {target_name}'s Inventory ({n} ores) (${v:,})"
    if quality:
        title += f" — {quality} only"
    desc = "\n".join(
        f"**{o['ore']}** ({', '.join(config.tier_name(r) for r in sorted(o['rarities'], key=config.tier_index))}) x{o['count']}"
        for o in ores[:25])
    embed = discord.Embed(title=title, description=desc or "Empty!", color=0x00BCD4)
    if len(ores) > 25:
        embed.set_footer(text=f"Showing 25 of {len(ores)} ores — dropdown limited by Discord.")
    return embed, InventoryView(viewer_id, int(target_id), target_name, ores, public, quality)


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

@bot.tree.command(name="spin", description=f"Use one of your {config.SPINS_PER_DAY} daily spins!")
async def spin(interaction: discord.Interaction):
    await interaction.response.defer()
    uid = str(interaction.user.id)
    db.init_db()
    u = db.reset_spins_if_new_day(uid, today_str())

    if not UNLIMITED_SPINS and u["spins_used_today"] >= config.SPINS_PER_DAY:
        await interaction.followup.send(
            f"❌ You're out of spins! You get **{config.SPINS_PER_DAY}** per day.\n"
            f"⏳ Resets in **{time_until_reset()}**.\n"
            f"🔥 Streak: **{u['streak']}** day(s) — spin daily to keep it!"
        )
        return

    rarity, quality, ore = roll_one()
    item_id = db.add_item(uid, rarity, quality, ore)

    # update counters
    key = {"Low": "low_pulls", "Mid": "mid_pulls", "High": "high_pulls",
           "Elite": "elite_pulls", "DIH": "dih_pulls"}[rarity]
    u = db.get_user(uid)
    updates = dict(spins_used_today=u["spins_used_today"] + 1,
                   total_spins=u["total_spins"] + 1, **{key: u[key] + 1})
    if quality == "Perfect":
        updates["perfect_pulls"] = u.get("perfect_pulls", 0) + 1
    db.update_user(uid, **updates)
    u = db.update_streak(uid, today_str())
    u = db.get_user(uid)

    pct, one_in = config.combined_odds(rarity, quality)
    value = config.quicksell_value(rarity)
    if UNLIMITED_SPINS:
        spins_left_text = "∞ (test mode)"
    else:
        spins_left_text = f"{config.SPINS_PER_DAY - u['spins_used_today']}/{config.SPINS_PER_DAY}"

    newly = check_achievements(uid, u, rarity, quality)
    ach_text = ("\n\n" + "\n".join(newly)) if newly else ""

    embed = discord.Embed(
        title=f"{ore} ({quality} {config.QUALITIES[quality]['emoji']})!",
        description=f"{config.tier_name(rarity)} {config.RARITIES[rarity]['dot']} ({config.RARITIES[rarity]['chance']:g}%)",
        color=config.RARITIES[rarity]["color"],
    )
    embed.add_field(name="💰 Quicksell", value=f"${value:,}", inline=True)
    embed.add_field(name="🎰 Spins left today", value=spins_left_text, inline=True)
    embed.set_footer(text=f"🔥 {u['streak']}-day streak • {interaction.user.display_name}")
    if rarity == "DIH":
        embed.add_field(name="🌟", value="**DIH TIER PULL!!** Insane luck.", inline=False)

    view = SpinView(interaction.user.id, item_id, rarity, quality, ore)
    # set quicksell button label (first child is the quicksell button)
    view.children[0].label = f"Quicksell ${value:,}"
    await interaction.followup.send(embed=embed, view=view)
    if newly:
        await interaction.followup.send(f"{interaction.user.mention} 🏆 Achievement unlocked!\n" + "\n".join(newly))


@bot.tree.command(name="balance", description="Check your balance.")
async def balance(interaction: discord.Interaction):
    u = db.get_user(str(interaction.user.id))
    await interaction.response.send_message(
        f"💰 {interaction.user.mention} — Balance: **${u['balance']:,}** | Total earned: **${u['total_earned']:,}**"
    )


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
    ores = db.get_ores_overview(tid)
    if not ores:
        await interaction.response.send_message("🎒 Inventory is empty!", ephemeral=not public)
        return
    name = await display_name(interaction, tid)
    embed, view = render_inventory(tid, name, interaction.user.id, public)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=not public)


@bot.tree.command(name="quicksell", description="Sell ores instantly for the lowest rarity value.")
@app_commands.describe(rarity="Which rarity to sell", quality="Optional: only this quality",
                       ore="Optional: only this ore name", amount="How many (default 1). Use 9999 for all.")
@app_commands.choices(rarity=[app_commands.Choice(name=r, value=r) for r in config.RARITIES])
async def quicksell(interaction: discord.Interaction, rarity: str, quality: str | None = None,
                    ore: str | None = None, amount: int = 1):
    await interaction.response.defer()
    uid = str(interaction.user.id)
    items = db.get_inventory_grouped(uid)
    targets = [i for i in items if i["rarity"] == rarity
               and (quality is None or i["quality"].lower() == quality.lower())
               and (ore is None or i["ore"].lower() == ore.lower())]
    if not targets:
        await interaction.followup.send("❌ You don't own anything matching that. Check `/inventory`.")
        return
    sold, earned = 0, 0
    for t in targets:
        n = t["count"] if amount >= 9999 else min(amount - sold, t["count"])
        if n <= 0:
            break
        for _ in range(n):
            if db.remove_one_item(uid, t["rarity"], t["quality"], t["ore"]) is None:
                break
            earned += config.quicksell_value(t["rarity"])
            sold += 1
        if sold >= amount and amount < 9999:
            break
    u = db.get_user(uid)
    db.update_user(uid, balance=u["balance"] + earned, total_earned=u["total_earned"] + earned)
    newly = check_achievements(uid, db.get_user(uid), "", "")
    await interaction.followup.send(f"💸 Sold **{sold}** ore(s) for **${earned:,}**! New balance: **${u['balance'] + earned:,}**.")
    if newly:
        await interaction.followup.send(f"{interaction.user.mention} 🏆 Achievement unlocked!\n" + "\n".join(newly))


@bot.tree.command(name="quicksell_all", description="Sell your ENTIRE inventory instantly.")
async def quicksell_all(interaction: discord.Interaction):
    await interaction.response.defer()
    uid = str(interaction.user.id)
    items = db.get_inventory_grouped(uid)
    if not items:
        await interaction.followup.send("Inventory is already empty!")
        return
    earned = 0
    count = 0
    for t in items:
        for _ in range(t["count"]):
            db.remove_one_item(uid, t["rarity"], t["quality"], t["ore"])
            earned += config.quicksell_value(t["rarity"])
            count += 1
    u = db.get_user(uid)
    db.update_user(uid, balance=u["balance"] + earned, total_earned=u["total_earned"] + earned)
    newly = check_achievements(uid, db.get_user(uid), "", "")
    await interaction.followup.send(f"💸 Sold **{count}** ores for **${earned:,}**! Balance: **${u['balance'] + earned:,}**.")
    if newly:
        await interaction.followup.send(f"{interaction.user.mention} 🏆 Achievement unlocked!\n" + "\n".join(newly))


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
        quick = config.quicksell_value(self.rarity)
        await interaction.response.send_message(
            f"📦 Listed **{self.quality} {self.ore}** ({config.tier_name(self.rarity)}) for **${amount:,}**! (ID: `{listing_id}`)\n"
            f"Quicksell value would've been ${quick:,} — {'🤑 profit mindset!' if amount > quick else '⚠️ cheaper than quicksell!'}"
        )


class MarketSellSelect(discord.ui.Select):
    """Level 1: pick which ore to list (same look as inventory)."""

    def __init__(self, owner_id: int, ores: list[dict]):
        self.owner_id = owner_id
        options = []
        for o in ores[:25]:  # Discord limit
            label = f"{ore_tier_label(o['ore'], o['rarities'])} x{o['count']}"[:100]
            options.append(discord.SelectOption(label=label, value=o["ore"]))
        super().__init__(placeholder="Choose which ore to list…", options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("That's not your inventory!", ephemeral=True)
            return
        ore = self.values[0]
        stacks = db.get_ore_detail(str(self.owner_id), ore)
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
        if ores:
            self.add_item(MarketSellSelect(owner_id, ores))


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


async def format_listings(interaction: discord.Interaction, listings: list[dict]) -> list[str]:
    lines = []
    for l in listings:
        seller = await display_name(interaction, l["seller_id"])
        lines.append(f"`{l['id']}` **{l['ore']}** ({config.tier_name(l['rarity'])}) — **${l['price']:,}** — {seller}")
    return lines


async def render_market(owner_id: int, interaction: discord.Interaction,
                        ore: str | None = None, quality: str | None = None,
                        sort: str | None = None, page: int = 0):
    """One renderer for every market stage. Returns (embed, view)."""
    all_listings = db.market_browse(ore=ore, quality=quality, sort=sort or "new",
                                    limit=BROWSE_LIMIT)
    pages = max(1, (len(all_listings) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = page % pages
    chunk = all_listings[page * PAGE_SIZE:page * PAGE_SIZE + PAGE_SIZE]
    lines = await format_listings(interaction, chunk)
    if ore is None:
        title = "🏪 Player Market — latest"
    elif quality is None:
        title = f"🏪 {ore} — latest"
    elif not sort:
        title = f"🏪 {ore} ({quality}) — latest"
    else:
        label = {"cheapest": "cheapest", "average": "closest to average",
                 "expensive": "most expensive"}[sort]
        title = f"🏪 {ore} ({quality}) — {label}"
    if pages > 1:
        title += f" (page {page + 1}/{pages})"
    embed = discord.Embed(title=title, description="\n".join(lines) if lines else "Sold out!",
                          color=0x9C27B0)
    embed.set_footer(text="Inspect a listing, then hit Buy • flip pages with ◀ ▶")
    return embed, MarketBrowser(owner_id, ore=ore, quality=quality, sort=sort,
                                page=page, pages=pages, chunk=chunk)


class MarketOreFilterSelect(discord.ui.Select):
    def __init__(self, owner_id: int):
        self.owner_id = owner_id
        ores = db.market_ores_with_rarity()
        # lowest tier first
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
            await interaction.response.send_message("Use `/market_view` to browse yourself!", ephemeral=True)
            return
        if self.values[0] == "none":
            return
        embed, view = await render_market(self.owner_id, interaction, ore=self.values[0])
        await interaction.response.edit_message(embed=embed, view=view)


class MarketQualityFilterSelect(discord.ui.Select):
    def __init__(self, owner_id: int, ore: str):
        self.owner_id = owner_id
        self.ore = ore
        quals = db.market_qualities(ore)
        options = [discord.SelectOption(label=q, value=q) for q in quals[:25]]
        super().__init__(placeholder="Filter by quality…", options=options or [
            discord.SelectOption(label="(none)", value="none")])

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Use `/market_view` to browse yourself!", ephemeral=True)
            return
        if self.values[0] == "none":
            return
        embed, view = await render_market(self.owner_id, interaction,
                                          ore=self.ore, quality=self.values[0])
        await interaction.response.edit_message(embed=embed, view=view)


class MarketSortSelect(discord.ui.Select):
    def __init__(self, owner_id: int, ore: str, quality: str, current: str | None):
        self.owner_id = owner_id
        self.ore = ore
        self.quality = quality
        super().__init__(placeholder="Sort…", options=[
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
                                          quality=self.quality, sort=self.values[0], page=0)
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
        seller = await display_name(interaction, listing["seller_id"])
        quick = config.quicksell_value(listing["rarity"])
        pct, one_in = config.combined_odds(listing["rarity"], listing["quality"])
        embed = discord.Embed(
            title=f"{listing['ore']} ({config.tier_name(listing['rarity'])}) — ${listing['price']:,}",
            description=f"Quality: **{listing['quality']}**\n"
                        f"Seller: **{seller}**\n"
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
    db.grant_achievement(listing["seller_id"], "merchant")
    check_achievements(listing["seller_id"], db.get_user(listing["seller_id"]), "", "")  # silent
    db.grant_achievement(buyer, "customer")
    newly = check_achievements(buyer, db.get_user(buyer), listing["rarity"], listing["quality"])
    if listing["rarity"] == "DIH" and db.grant_achievement(buyer, "investor"):
        newly.append(f"🏆 **{config.ACHIEVEMENTS['investor'][0]}** — {config.ACHIEVEMENTS['investor'][1]}")
    await interaction.response.send_message(f"✅ {msg}")
    if newly:
        await interaction.followup.send(
            f"{interaction.user.mention} 🏆 Achievement unlocked!\n" + "\n".join(newly))


class MarketBrowser(discord.ui.View):
    """Stage-based market browser: ore filter -> quality filter -> sort, inspect + pages everywhere."""

    def __init__(self, owner_id: int, ore: str | None = None, quality: str | None = None,
                 sort: str | None = None, page: int = 0, pages: int = 1,
                 chunk: list[dict] | None = None):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.ore = ore
        self.quality = quality
        self.sort = sort
        self.page = page
        self.pages = pages
        if ore is None:
            self.add_item(MarketOreFilterSelect(owner_id))
        elif quality is None:
            self.add_item(MarketQualityFilterSelect(owner_id, ore))
        else:
            self.add_item(MarketSortSelect(owner_id, ore, quality, sort))
        if chunk:
            self.add_item(MarketListingInspectSelect(chunk))

    async def _flip(self, interaction: discord.Interaction, delta: int):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Use `/market_view` to browse yourself!",
                                                    ephemeral=True)
            return
        embed, view = await render_market(self.owner_id, interaction, ore=self.ore,
                                          quality=self.quality, sort=self.sort,
                                          page=self.page + delta)
        await interaction.response.edit_message(embed=embed, view=view)

    async def _back(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Use `/market_view` to browse yourself!",
                                                    ephemeral=True)
            return
        if self.sort:
            embed, view = await render_market(self.owner_id, interaction,
                                              ore=self.ore, quality=self.quality)
        elif self.quality:
            embed, view = await render_market(self.owner_id, interaction, ore=self.ore)
        elif self.ore:
            embed, view = await render_market(self.owner_id, interaction)
        else:
            await interaction.response.send_message("Already at the start!", ephemeral=True)
            return
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="", emoji="◀", style=discord.ButtonStyle.secondary, row=4)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._flip(interaction, -1)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, emoji="↩", row=4)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._back(interaction)

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
                  listings: list[dict] | None = None, selected_id: int | None = None):
    """Build the cancel embed + view for a stage. Falls back to stage 1 if empty."""
    if listings is None:
        listings = db.market_by_seller(owner_id, ore, quality, limit=10)
    if not listings:
        # fall back to your latest 10 overall
        ore, quality, selected_id = None, None, None
        listings = db.market_by_seller(owner_id, limit=10)
    if ore is None:
        title = "🚫 Your listings — latest 10"
    elif quality is None:
        title = f"🚫 Your {ore} — latest 10"
    else:
        title = f"🚫 Your {ore} ({quality}) — latest 10"
    embed = discord.Embed(title=title, description="\n".join(cancel_lines(listings, selected_id))
                          if listings else "Nothing here!", color=0xF44336)
    return embed, CancelBrowser(int(owner_id), ore=ore, quality=quality,
                                listings=listings, selected_id=selected_id)


class CancelOreSelect(discord.ui.Select):
    def __init__(self, owner_id: int):
        self.owner_id = owner_id
        ores = db.market_seller_ores(str(owner_id))
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
        embed, view = render_cancel(str(self.owner_id), self.values[0], None)
        await interaction.response.edit_message(embed=embed, view=view)


class CancelQualitySelect(discord.ui.Select):
    def __init__(self, owner_id: int, ore: str):
        self.owner_id = owner_id
        self.ore = ore
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
        embed, view = render_cancel(str(self.owner_id), self.ore, self.values[0])
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
                 listings: list[dict] | None = None, selected_id: int | None = None):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.ore = ore
        self.quality = quality
        self.listings = listings or []
        self.selected_id = selected_id
        if ore is None:
            self.add_item(CancelOreSelect(owner_id))
        elif quality is None:
            self.add_item(CancelQualitySelect(owner_id, ore))
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
        embed, view = render_cancel(str(self.owner_id), self.ore, self.quality)
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
    u = db.get_user(tid)
    inv_count = db.count_inventory(tid)
    ach_n = len(db.get_achievements(tid))
    ach_total = len(config.ACHIEVEMENTS)
    name = await display_name(interaction, tid)
    await interaction.response.send_message(
        f"📊 **{name}'s Stats**\n"
        f"🎰 Total spins: **{u['total_spins']}**\n"
        f"🪨 Low Tier: **{u['low_pulls']}** | 💚 Mid Tier: **{u['mid_pulls']}** | 💎 High Tier: **{u['high_pulls']}**\n"
        f"👑 Elite Tier: **{u['elite_pulls']}** | 🌟 DIH Tier: **{u['dih_pulls']}**\n"
        f"🔥 Streak: **{u['streak']}** days (best: **{u['longest_streak']}**)\n"
        f"💰 Balance: **${u['balance']:,}** | Earned: **${u['total_earned']:,}**\n"
        f"🎒 Inventory: **{inv_count}** ores\n"
        f"🏆 Achievements: **{ach_n}/{ach_total}**",
        ephemeral=not public,
    )


@bot.tree.command(name="achievements", description="See your achievements.")
async def achievements(interaction: discord.Interaction):
    uid = str(interaction.user.id)
    u = db.get_user(uid)
    public = bool(u.get("ach_public", 0))
    unlocked = set(db.get_achievements(uid))
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
        embed = discord.Embed(
            title=f"⛏️ {config.tier_name(r)} Ores",
            description="\n".join(f"• **{o}**" for o in config.ORES[r]),
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
        f"🎰 `/spin` — roll an ore ({config.SPINS_PER_DAY}/day, resets 00:00 UTC)\n"
        "💰 `/balance` — your money\n"
        "🎒 `/inventory` — your ores + dropdown inspector\n"
        "💸 `/quicksell` — sell matching ores instantly\n"
        "💸 `/quicksell_all` — sell everything instantly\n"
        "📦 `/market_list` — list an ore (dropdown picker)\n"
        "🏪 `/market_view` — browse, filter, inspect & buy\n"
        "🚫 `/market_cancel` — take down a listing (dropdown picker)\n"
        "🚫 `/market_cancel_all` — take down ALL listings\n"
        "🔄 `/trade` — trade ores with another player\n"
        "📊 `/stats` — spins, pulls, streak, balance\n"
        "🏆 `/achievements` — your badges\n"
        "🎲 `/odds` — rarities, odds, values\n"
        "⛏️ `/ores` — browse every ore by tier\n"
        "💰 `/baltop` — top 10 richest players\n"
        "⚙️ `/settings` — privacy settings\n"
        "❓ `/help` — this message"
    )


# ---------- settings ----------

SETTING_DEFS = [
    ("inventory", "inv_public", "🎒 Inventory"),
    ("achievements", "ach_public", "🏆 Achievements"),
    ("stats", "stats_public", "📊 Stats"),
]


def settings_embed(uid: str, selected: str) -> discord.Embed:
    u = db.get_user(uid)
    lines = []
    for key, col, label in SETTING_DEFS:
        state = "🌍 Public" if u.get(col, 0) else "🔒 Private"
        mark = "▶ " if key == selected else ""
        lines.append(f"{mark}{label}: **{state}**")
    embed = discord.Embed(title="⚙️ Privacy Settings",
                          description="\n".join(lines) + "\n\nPublic = others can view it. Private = only you.",
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
        await interaction.response.edit_message(
            embed=settings_embed(str(view.owner_id), view.selected), view=view)


class SettingsView(discord.ui.View):
    def __init__(self, owner_id: int):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.selected = "inventory"
        self.add_item(SettingSelect(self.selected))

    @discord.ui.button(label="Switch private/public", style=discord.ButtonStyle.primary, emoji="🔄")
    async def toggle(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("That's not yours! Run `/settings` yourself.",
                                                    ephemeral=True)
            return
        uid = str(self.owner_id)
        col = next(c for k, c, _ in SETTING_DEFS if k == self.selected)
        u = db.get_user(uid)
        db.update_user(uid, **{col: 0 if u.get(col, 0) else 1})
        await interaction.response.edit_message(
            embed=settings_embed(uid, self.selected), view=self)


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
    embed.set_footer(text="Both sides: set your offer, then both hit Accept. 1 ore each.")
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


class OfferOreSelect(discord.ui.Select):
    """Ephemeral: pick which of YOUR ores to offer."""

    def __init__(self, tid: int, side: str):
        self.tid = tid
        self.side = side
        t = trades[tid]
        uid = str(t[f"{side}_id"])
        ores = db.get_ores_overview(uid)
        ores.sort(key=lambda o: (min(config.tier_index(r) for r in o["rarities"]), -o["count"]))
        options = [discord.SelectOption(
            label=f"{ore_tier_label(o['ore'], o['rarities'])} x{o['count']}"[:100], value=o["ore"])
            for o in ores[:25]]
        super().__init__(placeholder="Pick your ore…", options=options or [
            discord.SelectOption(label="(empty inventory)", value="none")])

    async def callback(self, interaction: discord.Interaction):
        t = trades.get(self.tid)
        if t is None or interaction.user.id != t[f"{self.side}_id"]:
            await interaction.response.send_message("Not yours!", ephemeral=True)
            return
        if self.values[0] == "none":
            return
        stacks = db.get_ore_detail(str(t[f"{self.side}_id"]), self.values[0])
        view = discord.ui.View(timeout=180)
        view.add_item(OfferQualitySelect(self.tid, self.side, self.values[0], stacks))
        n, v = db.inventory_value(str(t[f"{self.side}_id"]), self.values[0])
        await interaction.response.edit_message(
            content=f"**{self.values[0]}** ({n} ores) — pick the stack:",
            embed=None, view=view)


class OfferQualitySelect(discord.ui.Select):
    def __init__(self, tid: int, side: str, ore: str, stacks: list[dict]):
        self.tid = tid
        self.side = side
        self.ore = ore
        options = [discord.SelectOption(
            label=f"{s['ore']} ({s['quality']}) x{s['count']}"[:100],
            description=f"{config.tier_name(s['rarity'])}"[:100],
            value=f"{s['rarity']}|{s['quality']}") for s in stacks[:25]]
        super().__init__(placeholder="Pick the stack…", options=options)

    async def callback(self, interaction: discord.Interaction):
        t = trades.get(self.tid)
        if t is None or interaction.user.id != t[f"{self.side}_id"]:
            await interaction.response.send_message("Not yours!", ephemeral=True)
            return
        rarity, quality = self.values[0].split("|")
        t[f"{self.side}_pick"] = {"rarity": rarity, "quality": quality, "ore": self.ore}
        t["a_ok"] = t["b_ok"] = False  # new offer resets accepts
        _persist_trade(t)
        await interaction.response.edit_message(
            content=f"✅ Offer set: **{self.ore} ({quality})**. Back to the trade!", embed=None, view=None)
        try:
            await t["message"].edit(embed=trade_embed(t))
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
        view = discord.ui.View(timeout=180)
        view.add_item(OfferOreSelect(self.tid, "a"))
        await interaction.response.send_message(content="Pick the ore YOU offer (1 unit):",
                                                view=view, ephemeral=True)

    @discord.ui.button(label="Set my offer (P2)", style=discord.ButtonStyle.primary, emoji="📦", row=0)
    async def set_b(self, interaction: discord.Interaction, button: discord.ui.Button):
        t, side = self._side(interaction)
        if t is None:
            await interaction.response.send_message("Trade expired!", ephemeral=True)
            return
        if side != "b":
            await interaction.response.send_message("That's Player 2's button!", ephemeral=True)
            return
        view = discord.ui.View(timeout=180)
        view.add_item(OfferOreSelect(self.tid, "b"))
        await interaction.response.send_message(content="Pick the ore YOU offer (1 unit):",
                                                view=view, ephemeral=True)

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
        if not t["a_pick"] or not t["b_pick"]:
            await interaction.response.send_message("☝️ Both sides must set an offer first!",
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
        ia = db.take_one_item(a, pa["rarity"], pa["quality"], pa["ore"])
        ib = db.take_one_item(b, pb["rarity"], pb["quality"], pb["ore"])
        if ia is None or ib is None:
            # rollback whatever was taken
            if ia is not None:
                db.add_item(a, ia["rarity"], ia["quality"], ia["ore"], ia.get("origin", "spin"))
            if ib is not None:
                db.add_item(b, ib["rarity"], ib["quality"], ib["ore"], ib.get("origin", "spin"))
            trades.pop(t["id"], None)
            db.delete_trade(t["id"])
            await interaction.response.edit_message(
                content="❌ Trade failed — someone no longer has their ore.", embed=None, view=None)
            return
        db.add_item(b, ia["rarity"], ia["quality"], ia["ore"], ia.get("origin", "spin"))
        db.add_item(a, ib["rarity"], ib["quality"], ib["ore"], ib.get("origin", "spin"))
        trades.pop(t["id"], None)
        db.delete_trade(t["id"])
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content=f"🔄 **Trade complete!** {t['a_name']} got **{pb['ore']} ({pb['quality']})**, "
                    f"{t['b_name']} got **{pa['ore']} ({pa['quality']})**.",
            embed=None, view=self)

    async def on_timeout(self):
        t = trades.pop(self.tid, None)
        db.delete_trade(self.tid)
        if t and t.get("message"):
            try:
                await t["message"].edit(content="⌛ Trade expired.", embed=None, view=None)
            except Exception:
                pass


@bot.tree.command(name="trade", description="Trade ores with another player (1 for 1, both accept).")
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


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Missing DISCORD_TOKEN — copy .env.example to .env and paste your bot token.")
    db.init_db()
    bot.run(TOKEN)
