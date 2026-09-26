"""
Central game config — edit this when you know your real ores/gems.
Everything else (bot.py, db.py) reads from here, so you only edit one place.
"""

from datetime import timezone

# --- Spins ---
SPINS_PER_DAY = 3
RESET_TIMEZONE = timezone.utc  # daily reset at 00:00 UTC

# Rarity: (chance %, quicksell value $, color for embed, dot emoji for spin line)
RARITIES = {
    "Low":     {"chance": 70.0, "value": 10,    "color": 0x9E9E9E, "emoji": "🪨", "dot": "⚪"},
    "Mid":     {"chance": 25.0, "value": 28,    "color": 0x4CAF50, "emoji": "💚", "dot": "🟢"},
    "High":    {"chance": 4.0,  "value": 175,   "color": 0x2196F3, "emoji": "💎", "dot": "🔵"},
    "Elite":   {"chance": 0.9,  "value": 778,   "color": 0x9C27B0, "emoji": "👑", "dot": "🟣"},
    "DIH":     {"chance": 0.1,  "value": 28000, "color": 0xFFD700, "emoji": "🌟", "dot": "🟡"},
}

# Quality: chance %
QUALITIES = {
    "Chipped":  {"chance": 80.0, "emoji": "🔹", "multiplier": 1.0},  # multiplier reserved for future value scaling
    "Scratched": {"chance": 19.0, "emoji": "🔷", "multiplier": 1.0},
    "Perfect":  {"chance": 1.0,  "emoji": "✨", "multiplier": 1.0},
}

# Placeholder ores per rarity.
# TODO: replace these with your real ores. Just add/remove names — no other code changes needed.
# e.g. ORES["Mid"] = ["Emerald", "Amethyst", "Jade"]
ORES = {
    "Low":   ["Pebble", "Coal", "Copper"],
    "Mid":   ["Iron", "Emerald", "Amethyst"],
    "High":  ["Ruby", "Sapphire", "Topaz"],
    "Elite": ["Diamond", "Opal", "Onyx"],
    "DIH":   ["Painite", "DIH Ore"],
}

# --- Streaks ---
# Your idea: "need to spin at least 3x days in a row to reach normal spin %"
# Implemented as: streak = consecutive days with >=1 spin.
# STREAK_REQUIRED_FOR_FULL_LUCK = 3 means new players get slightly reduced luck
# until they build a 3-day streak — set to 1 or 0 to disable the penalty.
STREAK_REQUIRED_FOR_FULL_LUCK = 1  # <-- 1 = no penalty, everyone gets normal % from day 1 (recommended)
STREAK_NEW_PLAYER_LUCK_MULTIPLIER = 1.0  # reserved: if you want to punish new players, lower DIH chance etc.

# --- Achievements ---
# id: (name, description, check handled in bot.py)
ACHIEVEMENTS = {
    "first_spin":   ("First Spin", "Use /spin for the first time"),
    "spins_10":     ("Starting Up", "Reach 10 total spins"),
    "spins_100":    ("Locked In", "Reach 100 total spins"),
    "spins_1000":   ("One Year", "Reach 1,000 total spins"),
    "spins_10000":  ("Degenerate", "Reach 10,000 total spins"),
    "elite_pull":   ("The Mediocre Leagues", "Pull an Elite Tier ore"),
    "dih_pull":     ("The Big Leagues", "Pull a DIH Tier ore"),
    "dih_10":       ("The Bigger Leagues", "Pull 10 DIH Tier ores"),
    "dih_100":      ("The Even Bigger Leagues", "Pull 100 DIH Tier ores"),
    "perfect_pull": ("Flawless", "Pull a Perfect condition ore"),
    "perfect_10":   ("Majestic", "Pull 10 Perfect condition ores"),
    "perfect_100":  ("Divine", "Pull 100 Perfect condition ores"),
    "rich_100":     ("On the Streets", "Reach a $100 balance"),
    "rich_1k":      ("First Bag", "Reach a $1,000 balance"),
    "rich_5k":      ("Kinda Financially Stable", "Reach a $5,000 balance"),
    "rich_10k":     ("Middle Class", "Reach a $10,000 balance"),
    "rich_25k":     ("Upper Class", "Reach a $25,000 balance"),
    "rich_50k":     ("Rich", "Reach a $50,000 balance"),
    "rich_100k":    ("Upper-Middle Class", "Reach a $100,000 balance"),
    "rich_250k":    ("Higher Echelon Middle Class", "Reach a $250,000 balance"),
    "rich_500k":    ("Quite Wealthy", "Reach a $500,000 balance"),
    "rich_1m":      ("Millionaire", "Reach a $1,000,000 balance"),
    "streak_3":     ("Starting Off", "Reach a 3-day streak"),
    "streak_7":     ("Consistent", "Reach a 7-day streak"),
    "streak_30":    ("Addicted", "Reach a 30-day streak"),
    "streak_100":   ("Geeked Out", "Reach a 100-day streak"),
    "merchant":     ("Merchant", "Sell something on the player market"),
    "customer":     ("Customer", "Buy something on the player market"),
    "merchant_10":  ("Experienced Merchant", "Sell 10 ores on the player market"),
    "customer_10":  ("Regular Customer", "Buy 10 ores on the player market"),
    "investor":     ("Investor", "Buy a DIH Tier ore from the player market"),
    "collector_low":   ("Rookie Collector", "Collect every Low Tier ore"),
    "collector_mid":   ("Amateur Collector", "Collect every Mid Tier ore"),
    "collector_high":  ("Experienced Collector", "Collect every High Tier ore"),
    "collector_elite": ("Pro Collector", "Collect every Elite Tier ore"),
    "collector_dih":   ("Top Collector", "Collect every DIH Tier ore"),
    "collector_all":   ("Semi-Maxxed Collection", "Collect every ore"),
}

# Category emoji shown in front of each achievement (organized view)
ACH_CATEGORIES = {
    "first_spin": "🎰", "spins_10": "🎰", "spins_100": "🎰", "spins_1000": "🎰", "spins_10000": "🎰",
    "elite_pull": "🍀", "dih_pull": "🍀", "dih_10": "🍀", "dih_100": "🍀",
    "perfect_pull": "🍀", "perfect_10": "🍀", "perfect_100": "🍀",
    "collector_low": "🗂️", "collector_mid": "🗂️", "collector_high": "🗂️",
    "collector_elite": "🗂️", "collector_dih": "🗂️", "collector_all": "🗂️",
    "rich_100": "💰", "rich_1k": "💰", "rich_5k": "💰", "rich_10k": "💰", "rich_25k": "💰",
    "rich_50k": "💰", "rich_100k": "💰", "rich_250k": "💰", "rich_500k": "💰", "rich_1m": "💰",
    "streak_3": "🔥", "streak_7": "🔥", "streak_30": "🔥", "streak_100": "🔥",
    "merchant": "🏪", "customer": "🏪", "merchant_10": "🏪", "customer_10": "🏪", "investor": "🏪",
}


def combined_odds(rarity: str, quality: str) -> tuple[float, str]:
    """
    Returns (percent, '1 in X' string) for a rarity+quality combo.
    percent = rarity_chance * quality_chance / 100
    one_in  = 100 / percent
    Example: DIH (0.1%) + Perfect (1%) -> 0.001% -> 1 in 100,000
    """
    r = RARITIES[rarity]["chance"]
    q = QUALITIES[quality]["chance"]
    pct = r * q / 100.0
    one_in = 100.0 / pct if pct > 0 else float("inf")
    # Whole numbers only: 1 in 7.68 -> 1 in 8
    return pct, f"1 in {round(one_in):,}"


def quicksell_value(rarity: str) -> int:
    """Lowest value of the rarity — flat per your spec (quality doesn't change it for now)."""
    return RARITIES[rarity]["value"]


def tier_name(rarity: str) -> str:
    """Display name: Low -> Low Tier. Internal keys stay short (DB-safe)."""
    return f"{rarity} Tier"


def tier_index(rarity: str) -> int:
    """0 = lowest tier. Used to sort dropdowns low -> high."""
    return list(RARITIES.keys()).index(rarity)
