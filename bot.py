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


# ---------- inventory dropdown ----------

class InventorySelect(discord.ui.Select):
    def __init__(self, owner_id: int, items: list[dict]):
        self.owner_id = owner_id
        self.lookup = {(i["rarity"], i["quality"], i["ore"]): i for i in items}
        options = []
        for i in items[:25]:  # Discord limit
            label = f"{i['quality']} {i['ore']} x{i['count']}"[:100]
            desc = f"{i['rarity']} • quicksell ${config.quicksell_value(i['rarity']):,} each"[:100]
            options.append(discord.SelectOption(label=label, description=desc,
                                                value=f"{i['rarity']}|{i['quality']}|{i['ore']}"))
        super().__init__(placeholder="Select an ore to inspect…", options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("That's not your inventory!", ephemeral=True)
            return
        rarity, quality, ore = self.values[0].split("|")
        item = self.lookup[(rarity, quality, ore)]
        value_each = config.quicksell_value(rarity)
        pct, one_in = config.combined_odds(rarity, quality)
        await interaction.response.send_message(
            f"{config.RARITIES[rarity]['emoji']} **{quality} {ore}**\n"
            f"Rarity: **{rarity}** | Quality: **{quality}**\n"
            f"Owned: **x{item['count']}**\n"
            f"Quicksell: **${value_each:,}** each (**${value_each * item['count']:,}** for all)\n"
            f"Odds: **{pct:.4g}%** ({one_in})\n"
            f"Tip: `/quicksell` to sell, `/market_post` to list it for other players.",
            ephemeral=True,
        )


class InventoryView(discord.ui.View):
    def __init__(self, owner_id: int, items: list[dict]):
        super().__init__(timeout=180)
        if items:
            self.add_item(InventorySelect(owner_id, items))


# ---------- bot events ----------

@bot.event
async def on_ready():
    db.init_db()
    try:
        await bot.tree.sync()
    except Exception as e:
        print(f"Slash sync failed: {e}")
    print(f"Logged in as {bot.user} — /spin ready!")


# ---------- commands ----------

@bot.tree.command(name="spin", description=f"Use one of your {config.SPINS_PER_DAY} daily spins!")
async def spin(interaction: discord.Interaction):
    await interaction.response.defer()
    uid = str(interaction.user.id)
    db.init_db()
    u = db.reset_spins_if_new_day(uid, today_str())

    if u["spins_used_today"] >= config.SPINS_PER_DAY:
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
    spins_left = config.SPINS_PER_DAY - u["spins_used_today"]

    newly = check_achievements(uid, u, rarity, quality)
    ach_text = ("\n\n" + "\n".join(newly)) if newly else ""

    embed = discord.Embed(
        title=f"{config.RARITIES[rarity]['emoji']} {quality} {ore}!",
        description=f"**Rarity:** {rarity} ({config.RARITIES[rarity]['chance']}%)\n"
                    f"**Quality:** {quality} {config.QUALITIES[quality]['emoji']} ({config.QUALITIES[quality]['chance']}%)",
        color=config.RARITIES[rarity]["color"],
    )
    embed.add_field(name="📊 Odds", value=f"**{pct:.4g}%**\n{one_in}", inline=True)
    embed.add_field(name="💰 Quicksell", value=f"${value:,}", inline=True)
    embed.add_field(name="🎰 Spins left today", value=f"{spins_left}/{config.SPINS_PER_DAY}", inline=True)
    embed.set_footer(text=f"🔥 {u['streak']}-day streak • {interaction.user.display_name}")
    if rarity == "DIH":
        embed.add_field(name="‼️", value="**DIH TIER?! NO WAY.** @everyone look at this pull!! (remove ping if annoying)", inline=False)

    view = SpinView(interaction.user.id, item_id, rarity, quality, ore)
    # set quicksell button label (first child is the quicksell button)
    view.children[0].label = f"Quicksell ${value:,}"
    await interaction.followup.send(embed=embed, view=view)
    if newly:
        await interaction.followup.send(f"🏆 Achievement unlocked!\n" + "\n".join(newly))


@bot.tree.command(name="balance", description="Check your balance.")
async def balance(interaction: discord.Interaction):
    u = db.get_user(str(interaction.user.id))
    await interaction.response.send_message(
        f"💰 {interaction.user.mention} — Balance: **${u['balance']:,}** | Total earned: **${u['total_earned']:,}**"
    )


@bot.tree.command(name="inventory", description="See what ores you own (with dropdown).")
async def inventory(interaction: discord.Interaction):
    uid = str(interaction.user.id)
    items = db.get_inventory_grouped(uid)
    if not items:
        await interaction.response.send_message("🎒 Your inventory is empty! Use `/spin` to roll some ores.")
        return
    total = sum(i["count"] for i in items)
    desc = "\n".join(
        f"{config.RARITIES[i['rarity']]['emoji']} **{i['quality']} {i['ore']}** x{i['count']} — `${config.quicksell_value(i['rarity']):,}` ea"
        for i in items[:25]
    )
    embed = discord.Embed(title=f"🎒 {interaction.user.display_name}'s Inventory ({total} ores)",
                          description=desc, color=0x00BCD4)
    if len(items) > 25:
        embed.set_footer(text=f"Showing 25 of {len(items)} unique stacks — dropdown limited by Discord.")
    await interaction.response.send_message(embed=embed, view=InventoryView(interaction.user.id, items))


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

@bot.tree.command(name="market_post", description="List one of your ores on the player market.")
@app_commands.describe(rarity="Rarity", quality="Quality (Chipped/Scratched/Perfect)",
                       ore="Exact ore name (see /inventory)", price="Price in $")
@app_commands.choices(rarity=[app_commands.Choice(name=r, value=r) for r in config.RARITIES],
                      quality=[app_commands.Choice(name=q, value=q) for q in config.QUALITIES])
async def market_post(interaction: discord.Interaction, rarity: str, quality: str, ore: str, price: int):
    if price < 1:
        await interaction.response.send_message("❌ Price must be at least $1.", ephemeral=True)
        return
    listing_id = db.market_list(str(interaction.user.id), rarity, quality, ore, price)
    if listing_id is None:
        await interaction.response.send_message("❌ You don't own that ore. Check `/inventory` for exact names.", ephemeral=True)
        return
    quick = config.quicksell_value(rarity)
    await interaction.response.send_message(
        f"📦 Listed **{quality} {ore}** ({rarity}) for **${price:,}**! (ID: `{listing_id}`)\n"
        f"Quicksell value would've been ${quick:,} — {'🤑 profit mindset!' if price > quick else '⚠️ cheaper than quicksell!'}"
    )


@bot.tree.command(name="market_view", description="Browse the player market.")
async def market_view(interaction: discord.Interaction):
    listings = db.market_view(10)
    if not listings:
        await interaction.response.send_message("📭 Market is empty! Be the first with `/market_post`.")
        return
    lines = [f"`{l['id']}` {config.RARITIES[l['rarity']]['emoji']} **{l['quality']} {l['ore']}** ({l['rarity']}) — **${l['price']:,}** — <@{l['seller_id']}>"
             for l in listings]
    await interaction.response.send_message("🏪 **Player Market** (newest 10)\n" + "\n".join(lines) +
                                            "\n\nBuy with `/market_buy <id>`")


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
        f"🪨 Low: **{u['low_pulls']}** | 💚 Mid: **{u['mid_pulls']}** | 💎 High: **{u['high_pulls']}**\n"
        f"👑 Elite: **{u['elite_pulls']}** | 🌟 DIH: **{u['dih_pulls']}**\n"
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


@bot.tree.command(name="odds", description="Show the full rarity + quality odds table.")
async def odds(interaction: discord.Interaction):
    lines = []
    for r, ri in config.RARITIES.items():
        for q, qi in config.QUALITIES.items():
            pct, one_in = config.combined_odds(r, q)
            lines.append(f"{ri['emoji']} {r} + {q}: **{pct:.4g}%** ({one_in})")
    # Discord message limit: split fine — this is ~15 lines, OK
    await interaction.response.send_message("🎲 **Full Odds**\n" + "\n".join(lines))


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Missing DISCORD_TOKEN — copy .env.example to .env and paste your bot token.")
    db.init_db()
    bot.run(TOKEN)
