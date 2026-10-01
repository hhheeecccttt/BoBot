"""
Central game config - edit this when you know your real ores/gems.
Everything else (bot.py, db.py) reads from here, so you only edit one place.
"""

from datetime import timezone

# --- Spins ---
SPINS_PER_DAY = 10
RESET_TIMEZONE = timezone.utc  # daily reset at 00:00 UTC

# Rarity: (chance %, quicksell value $, color for embed, dot emoji for spin line)
RARITIES = {
    "Low":     {"chance": 70.0, "value": 10,    "color": 0x9E9E9E, "emoji": "🪨", "dot": "⚪"},
    "Mid":     {"chance": 25.0, "value": 28,    "color": 0x4CAF50, "emoji": "💚", "dot": "🟢"},
    "High":    {"chance": 4.0,  "value": 175,   "color": 0x2196F3, "emoji": "💎", "dot": "🔵"},
    "Elite":   {"chance": 0.9,  "value": 778,   "color": 0x9C27B0, "emoji": "👑", "dot": "🟣"},
    "Mythical": {"chance": 0.1,  "value": 28000, "color": 0xFFD700, "emoji": "🌟", "dot": "🟡"},
}

# Quality: chance %
QUALITIES = {
    "Heavily Chipped":  {"chance": 30.0, "emoji": "◾", "multiplier": 1.0},
    "Mildly Chipped":   {"chance": 25.0, "emoji": "◽", "multiplier": 1.0},
    "Lightly Chipped":  {"chance": 19.0, "emoji": "▫️", "multiplier": 1.0},
    "Heavily Scratched": {"chance": 13.0, "emoji": "▪️", "multiplier": 1.0},
    "Mildly Scratched": {"chance": 8.0,  "emoji": "🔹", "multiplier": 1.0},
    "Lightly Scratched": {"chance": 4.0,  "emoji": "🔷", "multiplier": 1.0},
    "Perfect Condition": {"chance": 1.0,  "emoji": "✨", "multiplier": 1.0},
}

# The full ore catalog per rarity.
ORES = {
    "Low": ["Copper", "Iron", "Tin", "Zinc", "Chromite", "Sulfur", "Silver",
            "Coal", "Quartz", "Nickel", "Lead", "Manganese"],
    "Mid": ["Topaz", "Jade", "Lapis Lazuli", "Obsidian", "Pyrite", "Azurite",
            "Sunstone", "Moonstone", "Amber", "Pearl", "Aragonite",
            "Calcite", "Kyanite", "Malachite", "Barite"],
    "High": ["Amethyst", "Fluorite", "Tanzanite", "Gold", "Red Beryl", "Tsavorite",
             "Opal", "Aquamarine", "Wulfenite", "Blue Topaz", "Morganite",
             "Imperial Topaz", "Chrome Diopside", "Spinel", "Peridot", "Iolite",
             "Sugalite", "Euclase"],
    "Elite": ["Emerald", "Ruby", "Garnet", "Chalcanthite", "Black Opal", "Paraíba Tourmaline",
              "Cobaltoan Calcite", "Vanadinite", "Benitoite", "Vivianite",
              "Rhodochrosite", "Diaboleite", "Linarite"],
    "Mythical": ["Painite", "Alexandrite", "Clear Diamond", "Blue Diamond",
                 "Pink Diamond", "Black Diamond", "Golden Diamond"],
}

# --- Streaks ---
# Your idea: "need to spin at least 3x days in a row to reach normal spin %"
# Implemented as: streak = consecutive days with >=1 spin.
# STREAK_REQUIRED_FOR_FULL_LUCK = 3 means new players get slightly reduced luck
# until they build a 3-day streak - set to 1 or 0 to disable the penalty.
STREAK_REQUIRED_FOR_FULL_LUCK = 1  # <-- 1 = no penalty, everyone gets normal % from day 1 (recommended)
STREAK_NEW_PLAYER_LUCK_MULTIPLIER = 1.0  # reserved: if you want to punish new players, lower Mythical chance etc.

# --- Achievements ---
# id: (name, description, check handled in bot.py)
ACHIEVEMENTS = {
    "first_spin":   ("First Spin", "Use /spin for the first time"),
    "spins_10":     ("Starting Up", "Reach 10 total spins"),
    "spins_100":    ("Locked In", "Reach 100 total spins"),
    "spins_1000":   ("Degenerate", "Reach 1,000 total spins"),
    "spins_3650":   ("One Year", "Reach 3,650 total spins"),
    "elite_pull":   ("The Mediocre Leagues", "Pull an Elite Tier ore"),
    "dih_pull":     ("The Big Leagues", "Pull a Mythical Tier ore"),
    "dih_3":        ("The Bigger Leagues", "Obtain 3 Mythical Tier ores"),
    "perfect_pull": ("Flawless", "Pull a Perfect condition ore"),
    "perfect_10":   ("Majestic", "Obtain 10 Perfect condition ores"),
    "perfect_30":   ("Divine", "Obtain 30 Perfect condition ores"),
    "jackpot":      ("Jackpot", "Pull a Perfect condition Mythical Tier ore"),
    "collector_low":   ("Rookie Collector", "Collect every Low Tier ore"),
    "collector_mid":   ("Amateur Collector", "Collect every Mid Tier ore"),
    "collector_high":  ("Experienced Collector", "Collect every High Tier ore"),
    "collector_elite": ("Pro Collector", "Collect every Elite Tier ore"),
    "collector_dih":   ("Top Collector", "Collect every Mythical Tier ore"),
    "collector_all":   ("Semi-Maxxed Collection", "Collect every ore"),
    "qcollector_low":   ("Small Collector", "Collect every quality of Low Tier ores"),
    "qcollector_mid":   ("Medium Collector", "Collect every quality of Mid Tier ores"),
    "qcollector_high":  ("Big Collector", "Collect every quality of High Tier ores"),
    "qcollector_elite": ("Giant Collector", "Collect every quality of Elite Tier ores"),
    "qcollector_dih":   ("Luck Max Collector", "Collect every quality of Mythical Tier ores"),
    "qcollector_all":   ("Maxxed Completion", "Collect every quality of every ore"),
    "rich_100":     ("On the Streets", "Reach a $100 balance"),
    "rich_1k":      ("First Bag", "Reach a $1,000 balance"),
    "rich_5k":      ("Kinda Financially Stable", "Reach a $5,000 balance"),
    "rich_10k":     ("Middle Class", "Reach a $10,000 balance"),
    "rich_20k":     ("Upper Class", "Reach a $20,000 balance"),
    "rich_30k":     ("Rich", "Reach a $30,000 balance"),
    "rich_50k":     ("Upper-Middle Class", "Reach a $50,000 balance"),
    "rich_75k":     ("Higher Echelon Middle Class", "Reach a $75,000 balance"),
    "rich_100k":    ("Wealthy", "Reach a $100,000 balance"),
    "rich_200k":    ("Very Wealthy", "Reach a $200,000 balance"),
    "rich_300k":    ("Extremely Wealthy", "Reach a $300,000 balance"),
    "rich_500k":    ("Halfway to Greatness", "Reach a $500,000 balance"),
    "rich_1m":      ("Millionaire", "Reach a $1,000,000 balance"),
    "streak_3":     ("Starting Off", "Reach a 3-day streak"),
    "streak_7":     ("Consistent", "Reach a 7-day streak"),
    "streak_30":    ("Addicted", "Reach a 30-day streak"),
    "streak_100":   ("Geeked Out", "Reach a 100-day streak"),
    "streak_365":   ("One Year", "Reach a 365-day streak"),
    "merchant":     ("Merchant", "Sell something on the player market"),
    "customer":     ("Customer", "Buy something on the player market"),
    "merchant_10":  ("Experienced Merchant", "Sell 10 ores on the player market"),
    "customer_10":  ("Regular Customer", "Buy 10 ores on the player market"),
    "merchant_20":  ("Very Experienced Merchant", "Sell 20 ores on the player market"),
    "merchant_50":  ("Extremely Experienced Merchant", "Sell 50 ores on the player market"),
    "merchant_100": ("Veteran Merchant", "Sell 100 ores on the player market"),
    "customer_20":  ("Frequent Customer", "Buy 20 ores on the player market"),
    "customer_50":  ("Very Frequent Customer", "Buy 50 ores on the player market"),
    "customer_100": ("Bro's got a plan in mind lol", "Buy 100 ores on the player market"),
    "investor":     ("Investor", "Buy a Mythical Tier ore from the player market"),
    "supplier":     ("Purveyor", "Sell a Mythical Tier ore on the player market"),
    "marketer_1":   ("Small Marketer", "Put an ore on the market"),
    "marketer_3":   ("Medium Marketer", "Put 3 ores on the market"),
    "marketer_10":  ("Big Marketer", "Put 10 ores on the market"),
    "marketer_20":  ("Bigger Marketer", "Put 20 ores on the market"),
    "marketer_50":  ("Even Bigger Marketer", "Put 50 ores on the market"),
    "marketer_100": ("Supplier", "Put 100 ores on the market"),
    "scalper":      ("Scalper", "Buy something on the market, then sell it for more"),
    "trader":       ("Trader", "Trade an ore"),
    "trader_10":    ("That's a Deal", "Trade 10 times"),
    "gifter":       ("Gifter", "Gift an ore"),
    "gifter_money": ("Charity", "Gift money"),
    "gifter_10":    ("Frequent Gifter", "Gift ores 10 times"),
    "gifter_money_10": ("A Kind Soul", "Gift money 10 times"),
    "wholesaler":   ("Wholesaler", "Quicksell 10 ores at the same time"),
    "mass_seller":  ("Mass Seller", "Quicksell 100 ores at the same time"),
    "packed":       ("Packed", "Have 50 ores in your inventory"),
    "loaded":       ("Loaded", "Have 100 ores in your inventory"),
    "vault_1":      ("The Beginning", "Put an ore in the vault"),
    "vault_10":     ("The Part after the Beginning", "Put 10 ores in the vault"),
    "vault_perfect": ("Trophy", "Put a Perfect Condition ore in the vault"),
    "vault_perfect_10": ("Trophies", "Put 10 Perfect Condition ores in the vault"),
    "vault_mythical": ("Glorious", "Put a Mythical Tier ore in the vault"),
    "moneybags":    ("Moneybags", "Make it onto the top balance leaderboard"),
    "hard_working": ("Hard Working", "Make it onto the top spins leaderboard"),
    "winner":       ("Winner", "Win a BoBo event"),
    "all_done":     ("It's Over", "Achieve it all"),
}

# Category emoji (kept for organization; display shows ✅/🔒 only)
ACH_CATEGORIES = {
    "first_spin": "🎰", "spins_10": "🎰", "spins_100": "🎰", "spins_1000": "🎰", "spins_3650": "🎰",
    "elite_pull": "🍀", "dih_pull": "🍀", "dih_3": "🍀", "dih_100": "🍀",
    "perfect_pull": "🍀", "perfect_10": "🍀", "perfect_30": "🍀", "jackpot": "🍀",
    "collector_low": "🗂️", "collector_mid": "🗂️", "collector_high": "🗂️",
    "collector_elite": "🗂️", "collector_dih": "🗂️", "collector_all": "🗂️",
    "qcollector_low": "🗂️", "qcollector_mid": "🗂️", "qcollector_high": "🗂️",
    "qcollector_elite": "🗂️", "qcollector_dih": "🗂️", "qcollector_all": "🗂️",
    "rich_100": "💰", "rich_1k": "💰", "rich_5k": "💰", "rich_10k": "💰", "rich_20k": "💰",
    "rich_30k": "💰", "rich_50k": "💰", "rich_75k": "💰", "rich_100k": "💰",
    "rich_200k": "💰", "rich_300k": "💰", "rich_500k": "💰", "rich_1m": "💰",
    "streak_3": "🔥", "streak_7": "🔥", "streak_30": "🔥", "streak_100": "🔥", "streak_365": "🔥",
    "merchant": "🏪", "customer": "🏪", "merchant_10": "🏪", "customer_10": "🏪",
    "merchant_20": "🏪", "merchant_50": "🏪", "merchant_100": "🏪",
    "customer_20": "🏪", "customer_50": "🏪", "customer_100": "🏪",
    "investor": "🏪", "supplier": "🏪", "scalper": "🏪",
    "marketer_1": "🏪", "marketer_3": "🏪", "marketer_10": "🏪", "marketer_20": "🏪",
    "marketer_50": "🏪", "marketer_100": "🏪",
    "trader": "🔄", "trader_10": "🔄",
    "gifter": "🎁", "gifter_money": "🎁", "gifter_10": "🎁", "gifter_money_10": "🎁",
    "wholesaler": "💸", "mass_seller": "💸",
    "packed": "🎒", "loaded": "🎒",
    "vault_1": "🗝️", "vault_10": "🗝️", "vault_perfect": "🗝️", "vault_perfect_10": "🗝️",
    "vault_mythical": "🗝️",
    "moneybags": "💰", "hard_working": "🎰",
    "winner": "🎉", "all_done": "🏆",
}


def combined_odds(rarity: str, quality: str) -> tuple[float, str]:
    """
    Returns (percent, '1 in X' string) for a rarity+quality combo.
    percent = rarity_chance * quality_chance / 100
    one_in  = 100 / percent
    Example: Mythical (0.1%) + Perfect (1%) -> 0.001% -> 1 in 100,000
    """
    r = RARITIES[rarity]["chance"]
    q = QUALITIES[quality]["chance"]
    pct = r * q / 100.0
    one_in = 100.0 / pct if pct > 0 else float("inf")
    # Whole numbers only: 1 in 7.68 -> 1 in 8
    return pct, f"1 in {round(one_in):,}"


def quicksell_value(rarity: str) -> int:
    """Lowest value of the rarity - flat per your spec (quality doesn't change it for now)."""
    return RARITIES[rarity]["value"]


def tier_name(rarity: str) -> str:
    """Display name: Low -> Low Tier. Internal keys stay short (DB-safe)."""
    return f"{rarity} Tier"


def tier_index(rarity: str) -> int:
    """0 = lowest tier. Used to sort dropdowns low -> high."""
    return list(RARITIES.keys()).index(rarity)


def rarity_one_in(chance: float) -> str:
    """100/chance as whole numbers, except near-1 odds which get one decimal (1.4)."""
    if chance <= 0:
        return "∞"
    exact = 100.0 / chance
    if round(exact) == 1 and exact != 1:
        return f"{exact:.1f}"
    return f"{round(exact):,}"
