import os
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

    if u["total_spins"] >= 1:
        grant("first_spin")
    if u["total_spins"] >= 10:
        grant("spins_10")
    if u["total_spins"] >= 100:
        grant("spins_100")
    if rarity == "High":
        grant("high_pull")
    if rarity == "Elite":
        grant("elite_pull")
    if rarity == "DIH":
        grant("dih_pull")
    if quality == "Perfect":
        grant("perfect_pull")
    if u["balance"] >= 1000:
        grant("rich_1k")
    if u["balance"] >= 10000:
        grant("rich_10k")
    if u["streak"] >= 3:
        grant("streak_3")
    if u["streak"] >= 7:
        grant("streak_7")
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


def inspect_text(rarity: str, quality: str, ore: str, count: int) -> str:
    """Quality first, then odds (% + 1-in), quicksell, tip."""
    value_each = config.quicksell_value(rarity)
    pct, one_in = config.combined_odds(rarity, quality)
    return (
        f"**{ore} ({quality}) x{count}**\n"
        f"Rarity: **{config.tier_name(rarity)}**\n"
        f"Odds: **{pct:.4g}%** ({one_in} chance)\n"
        f"Quicksell: **${value_each:,}** each (**${value_each * count:,}** for all)\n"
        f"Tip: `/quicksell` to sell, `/market_list` to list it for other players."
    )


class QualityInspectSelect(discord.ui.Select):
    """Level 2 dropdown: pick a quality stack of one ore to inspect."""

    def __init__(self, owner_id: int, stacks: list[dict]):
        self.owner_id = owner_id
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
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("That's not your inventory!", ephemeral=True)
            return
        if self.values[0] == "none":
            return
        rarity, quality = self.values[0].split("|")
        s = self.lookup[(rarity, quality)]
        await interaction.response.send_message(
            inspect_text(rarity, quality, s["ore"], s["count"]), ephemeral=True)


class OrePageView(discord.ui.View):
    """Level 2 view: quality breakdown of one ore + inspect dropdown."""

    def __init__(self, owner_id: int, ore: str, stacks: list[dict]):
        super().__init__(timeout=180)
        if stacks:
            self.add_item(QualityInspectSelect(owner_id, stacks))


class OreSelect(discord.ui.Select):
    """Level 1 dropdown: pick which ore page to open."""

    def __init__(self, owner_id: int, ores: list[dict]):
        self.owner_id = owner_id
        options = []
        for o in ores[:25]:  # Discord limit
            label = f"{ore_tier_label(o['ore'], o['rarities'])} x{o['count']}"[:100]
            options.append(discord.SelectOption(label=label, value=o["ore"]))
        super().__init__(placeholder="Choose which ore page to open…", options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("That's not your inventory!", ephemeral=True)
            return
        ore = self.values[0]
        stacks = db.get_ore_detail(str(self.owner_id), ore)
        if not stacks:
            await interaction.response.send_message("Nothing there anymore.", ephemeral=True)
            return
        lines = [f"**{s['ore']} ({s['quality']})** x{s['count']}"
                 for s in stacks]
        embed = discord.Embed(title=f"⛏️ {ore}",
                              description="\n".join(lines), color=0x00BCD4)
        await interaction.response.send_message(
            embed=embed, view=OrePageView(self.owner_id, ore, stacks), ephemeral=True)


class InventoryView(discord.ui.View):
    def __init__(self, owner_id: int, ores: list[dict]):
        super().__init__(timeout=180)
        if ores:
            self.add_item(OreSelect(owner_id, ores))


# ---------- bot events ----------

@bot.event
async def on_ready():
    db.init_db()
    try:
        await bot.tree.sync()
    except Exception as e:
        print(f"Slash sync failed: {e}")
    print(f"Logged in as {bot.user} — /spin ready!" + (" [UNLIMITED SPINS TEST MODE]" if UNLIMITED_SPINS else ""))


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
    db.update_user(uid, spins_used_today=u["spins_used_today"] + 1,
                   total_spins=u["total_spins"] + 1, **{key: u[key] + 1})
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
        title=f"{ore} ({quality})!",
        description=f"{config.tier_name(rarity)}",
        color=config.RARITIES[rarity]["color"],
    )
    embed.add_field(name="📊 Odds", value=f"{one_in} chance", inline=True)
    embed.add_field(name="💰 Quicksell", value=f"${value:,}", inline=True)
    embed.add_field(name="🎰 Spins left today", value=spins_left_text, inline=True)
    embed.set_footer(text=f"🔥 {u['streak']}-day streak • {interaction.user.display_name}")
    if rarity == "DIH":
        embed.add_field(name="‼️", value="**DIH TIER?! NO WAY.** @everyone look at this pull!! (remove ping if annoying)", inline=False)

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


@bot.tree.command(name="inventory", description="See what ores you own (with dropdown).")
async def inventory(interaction: discord.Interaction):
    uid = str(interaction.user.id)
    ores = db.get_ores_overview(uid)
    if not ores:
        await interaction.response.send_message("🎒 Your inventory is empty! Use `/spin` to roll some ores.")
        return
    total = sum(o["count"] for o in ores)
    desc = "\n".join(
        f"**{ore_tier_label(o['ore'], o['rarities'])}** x{o['count']}"
        for o in ores[:25]
    )
    embed = discord.Embed(title=f"🎒 {interaction.user.display_name}'s Inventory ({total} ores)",
                          description=desc, color=0x00BCD4)
    if len(ores) > 25:
        embed.set_footer(text=f"Showing 25 of {len(ores)} ores — dropdown limited by Discord.")
    await interaction.response.send_message(embed=embed, view=InventoryView(interaction.user.id, ores))


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
    check_achievements(uid, db.get_user(uid), "", "")
    await interaction.followup.send(f"💸 Sold **{sold}** ore(s) for **${earned:,}**! New balance: **${u['balance'] + earned:,}**.")


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
    await interaction.followup.send(f"💸 Sold **{count}** ores for **${earned:,}**! Balance: **${u['balance'] + earned:,}**.")


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
    desc = "\n".join(f"**{ore_tier_label(o['ore'], o['rarities'])}** x{o['count']}"
                     for o in ores[:25])
    embed = discord.Embed(title="📦 List an ore", description=desc, color=0x4CAF50)
    await interaction.response.send_message(
        embed=embed, view=MarketSellView(interaction.user.id, ores), ephemeral=True)


async def format_listings(interaction: discord.Interaction, listings: list[dict]) -> list[str]:
    lines = []
    for l in listings:
        seller = await display_name(interaction, l["seller_id"])
        lines.append(f"`{l['id']}` **{l['ore']}** ({config.tier_name(l['rarity'])}) — **${l['price']:,}** — {seller}")
    return lines


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
        ore = self.values[0]
        listings = db.market_browse(ore=ore, limit=10)
        lines = await format_listings(interaction, listings)
        embed = discord.Embed(title=f"🏪 {ore} — latest 10",
                              description="\n".join(lines) if lines else "Sold out!",
                              color=0x9C27B0)
        await interaction.response.edit_message(
            embed=embed, view=MarketBrowser(self.owner_id, ore=ore, listings=listings))


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
        quality = self.values[0]
        listings = db.market_browse(ore=self.ore, quality=quality, limit=10)
        lines = await format_listings(interaction, listings)
        embed = discord.Embed(title=f"🏪 {self.ore} ({quality}) — latest 10",
                              description="\n".join(lines) if lines else "Sold out!",
                              color=0x9C27B0)
        await interaction.response.edit_message(
            embed=embed, view=MarketBrowser(self.owner_id, ore=self.ore,
                                            quality=quality, listings=listings))


class MarketSortSelect(discord.ui.Select):
    def __init__(self, owner_id: int, ore: str, quality: str):
        self.owner_id = owner_id
        self.ore = ore
        self.quality = quality
        super().__init__(placeholder="Sort…", options=[
            discord.SelectOption(label="Cheapest", value="cheapest", emoji="💲"),
            discord.SelectOption(label="Closest to average", value="average", emoji="📊"),
            discord.SelectOption(label="Most expensive", value="expensive", emoji="💎"),
        ])

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Use `/market_view` to browse yourself!", ephemeral=True)
            return
        sort = self.values[0]
        listings = db.market_browse(ore=self.ore, quality=self.quality, sort=sort, limit=10)
        lines = await format_listings(interaction, listings)
        label = {"cheapest": "cheapest", "average": "closest to average",
                 "expensive": "most expensive"}[sort]
        embed = discord.Embed(title=f"🏪 {self.ore} ({self.quality}) — {label}",
                              description="\n".join(lines) if lines else "Sold out!",
                              color=0x9C27B0)
        await interaction.response.edit_message(
            embed=embed, view=MarketBrowser(self.owner_id, ore=self.ore,
                                            quality=self.quality, listings=listings))


class MarketListingInspectSelect(discord.ui.Select):
    """Pick one of the shown listings to inspect it."""

    def __init__(self, owner_id: int, listings: list[dict]):
        self.owner_id = owner_id
        options = []
        for l in listings[:25]:
            label = f"{l['ore']} — ${l['price']:,}"[:100]
            desc = f"{l['quality']} • {config.tier_name(l['rarity'])} • ID {l['id']}"[:100]
            options.append(discord.SelectOption(label=label, description=desc,
                                                value=str(l["id"])))
        super().__init__(placeholder="Inspect a listing…", options=options or [
            discord.SelectOption(label="(none)", value="none")])

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Use `/market_view` to browse yourself!", ephemeral=True)
            return
        if self.values[0] == "none":
            return
        listing = db.market_get(int(self.values[0]))
        if listing is None:
            await interaction.response.send_message("❌ That listing just sold!", ephemeral=True)
            return
        seller = await display_name(interaction, listing["seller_id"])
        quick = config.quicksell_value(listing["rarity"])
        pct, one_in = config.combined_odds(listing["rarity"], listing["quality"])
        await interaction.response.send_message(
            f"**{listing['ore']} ({config.tier_name(listing['rarity'])})** — **${listing['price']:,}**\n"
            f"Quality: **{listing['quality']}**\n"
            f"Seller: **{seller}**\n"
            f"Odds: **{pct:.4g}%** ({one_in} chance)\n"
            f"Quicksell value: **${quick:,}**\n"
            f"Buy it with `/market_buy {listing['id']}`",
            ephemeral=True,
        )


class MarketBrowser(discord.ui.View):
    """Stage-based market browser: ore filter -> quality filter -> sort, inspect at every stage."""

    def __init__(self, owner_id: int, ore: str | None = None, quality: str | None = None,
                 listings: list[dict] | None = None):
        super().__init__(timeout=300)
        if ore is None:
            self.add_item(MarketOreFilterSelect(owner_id))
        elif quality is None:
            self.add_item(MarketQualityFilterSelect(owner_id, ore))
        else:
            self.add_item(MarketSortSelect(owner_id, ore, quality))
        if listings:
            self.add_item(MarketListingInspectSelect(owner_id, listings))


@bot.tree.command(name="market_view", description="Browse the player market (latest 10 + filters).")
async def market_view(interaction: discord.Interaction):
    await interaction.response.defer()
    listings = db.market_browse(limit=10)
    if not listings and not db.market_ores():
        await interaction.followup.send("📭 Market is empty! Be the first with `/market_list`.")
        return
    lines = await format_listings(interaction, listings)
    embed = discord.Embed(title="🏪 Player Market — latest 10",
                          description="\n".join(lines) if lines else "Sold out!",
                          color=0x9C27B0)
    embed.set_footer(text="Buy with /market_buy <id> • use the dropdowns to filter & inspect")
    await interaction.followup.send(embed=embed, view=MarketBrowser(interaction.user.id,
                                                                    listings=listings))


@bot.tree.command(name="market_buy", description="Buy a listing by ID.")
@app_commands.describe(listing_id="The ID shown in /market_view")
async def market_buy(interaction: discord.Interaction, listing_id: int):
    # find seller first so we can award them the Merchant achievement
    seller_id = next((l["seller_id"] for l in db.market_view(1000) if l["id"] == listing_id), None)
    ok, msg = db.market_buy(listing_id, str(interaction.user.id))
    if ok:
        if seller_id:
            db.grant_achievement(seller_id, "merchant")
        await interaction.response.send_message(f"✅ {msg}")
    else:
        await interaction.response.send_message(f"❌ {msg}", ephemeral=True)


@bot.tree.command(name="market_cancel", description="Cancel your listing (item returns to you).")
async def market_cancel(interaction: discord.Interaction, listing_id: int):
    ok, msg = db.market_cancel(listing_id, str(interaction.user.id))
    await interaction.response.send_message(("✅ " if ok else "❌ ") + msg, ephemeral=not ok)


# ----- stats / streak / achievements -----

@bot.tree.command(name="stats", description="See your spin stats.")
async def stats(interaction: discord.Interaction, user: discord.User | None = None):
    target = user or interaction.user
    u = db.get_user(str(target.id))
    inv_count = db.count_inventory(str(target.id))
    await interaction.response.send_message(
        f"📊 **{target.display_name}'s Stats**\n"
        f"🎰 Total spins: **{u['total_spins']}**\n"
        f"🪨 Low Tier: **{u['low_pulls']}** | 💚 Mid Tier: **{u['mid_pulls']}** | 💎 High Tier: **{u['high_pulls']}**\n"
        f"👑 Elite Tier: **{u['elite_pulls']}** | 🌟 DIH Tier: **{u['dih_pulls']}**\n"
        f"🔥 Streak: **{u['streak']}** days (best: **{u['longest_streak']}**)\n"
        f"💰 Balance: **${u['balance']:,}** | Earned: **${u['total_earned']:,}**\n"
        f"🎒 Inventory: **{inv_count}** ores"
    )


@bot.tree.command(name="achievements", description="See your achievements.")
async def achievements(interaction: discord.Interaction):
    uid = str(interaction.user.id)
    unlocked = set(db.get_achievements(uid))
    lines = [f"{'✅' if aid in unlocked else '🔒'} **{name}** — {desc}"
             for aid, (name, desc) in config.ACHIEVEMENTS.items()]
    await interaction.response.send_message("🏆 **Achievements**\n" + "\n".join(lines))


@bot.tree.command(name="ores", description="Show all rarities, odds, values, and ores.")
async def ores(interaction: discord.Interaction):
    embed = discord.Embed(title="⛏️ Ores & Odds", color=0xFF9800)
    for r, ri in config.RARITIES.items():
        ore_list = ", ".join(config.ORES[r])
        exact = 100.0 / ri["chance"] if ri["chance"] > 0 else 0
        # Whole numbers, except near-1 odds which get one decimal (1 in 1.4)
        one_in_txt = f"{exact:.1f}" if round(exact) == 1 and exact != 1 else f"{round(exact):,}"
        embed.add_field(
            name=f"{ri['emoji']} {config.tier_name(r)}",
            value=f"Ores: **{ore_list}**\nQuicksell: **${ri['value']:,}**\n"
                  f"Odds: **{ri['chance']}%** (1 in {one_in_txt} chance)",
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
        "🏪 `/market_view` — browse the market + filters\n"
        "🛒 `/market_buy` — buy a listing by ID\n"
        "🚫 `/market_cancel` — take down your listing\n"
        "📊 `/stats` — spins, pulls, streak, balance\n"
        "🏆 `/achievements` — your badges\n"
        "⛏️ `/ores` — all rarities, odds, values, ore lists\n"
        "💰 `/baltop` — top 10 richest players\n"
        "❓ `/help` — this message"
    )


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Missing DISCORD_TOKEN — copy .env.example to .env and paste your bot token.")
    db.init_db()
    bot.run(TOKEN)
