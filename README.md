# Ore Spin Bot 🎰

Discord bot: 3 spins/day, rarity rolls, quality rolls, quicksell, player market, inventory dropdown, streaks, stats, achievements.

## Quick answers to your questions

- **Player market — impossible?** No, totally doable. Included: `/market_post`, `/market_view`, `/market_buy`, `/market_cancel`.
- **Inventory dropdown — impossible?** No, easy. `/inventory` shows an embed + a Discord dropdown (Select menu) to inspect each stack. Discord limits dropdowns to 25 options, so huge inventories show the top 25.
- **Custom emojis / Nitro?** Bots do NOT get free Nitro. But your bot can use **custom emojis from any server it's in** if you add them there (Server Settings → Emoji → upload). Then put `<:name:id>` in `config.py` emoji fields. The bot currently uses default unicode emoji so it works with zero setup.
- **Achievements — too ambitious?** No, they're just "if X then grant badge". Included 12 starter ones.
- **Streak** — tracked (consecutive days with ≥1 spin). Currently set to **no penalty** (`STREAK_REQUIRED_FOR_FULL_LUCK = 1` in `config.py`) so everyone gets normal % from day 1. Tell me if you want reduced luck for broken streaks and I'll wire it in.

## Setup (5 min)

1. **Create the bot user:**
   - Go to https://discord.com/developers/applications → New Application → Bot → Reset Token → copy it.
   - Enable **Privileged Gateway Intents: nothing required** (defaults fine).
   - OAuth2 → URL Generator → scopes: `bot`, `applications.commands` → permissions: Send Messages, Embed Links, Use Slash Commands → open URL, invite to your server.

2. **Run:**
```bash
cd /home/hwang/projects/bot
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then edit .env and paste DISCORD_TOKEN
python3 bot.py
```

3. In Discord, use `/spin`. Slash commands can take ~1 min to appear the first time.

## Commands

| Command | What it does |
|---|---|
| `/spin` | Roll rarity + quality + ore. Shows % and "1 in X". Has a 💸 quicksell button. 3/day, resets 00:00 UTC |
| `/balance` | Your $ |
| `/inventory` | Your ores + dropdown inspector |
| `/quicksell <rarity> [quality] [ore] [amount]` | Sell matching ores at flat rarity value |
| `/quicksell_all` | Sell everything |
| `/market_post` | Pick an ore from a dropdown, set a price in the popup |
| `/market_view` | Browse newest 10 listings |
| `/market_buy <id>` | Buy a listing |
| `/market_cancel <id>` | Take down your listing |
| `/stats [user]` | Total spins, pulls per rarity, streak, balance |
| `/achievements` | Your badges |
| `/ores` | All rarities, odds, quicksell values, and ore lists |
| `/leaderboard` | Top 10 richest players |
| `/help` | List of every command |

## Values / odds (from your spec)

- Rarities: Low 70% ($10), Mid 25% ($28), High 4% ($175), Elite 0.9% ($778), DIH 0.1% ($28,000)
- Quality: Chipped 80%, Scratched 19%, Perfect 1%
- Combined example: DIH Perfect = 0.1% × 1% = **0.001% = 1 in 100,000** ✅ (bot computes this automatically per spin)

## Customize your ores

Edit `config.py` → `ORES`. That's it:
```python
ORES = {
  "Low": ["Pebble", "Coal"],
  "Mid": ["Emerald", "Amethyst"],  # your real gems here
  ...
}
```
Rarity chances, quicksell values, and emoji also live in `config.py`.

Data is stored in `bot.db` (SQLite, auto-created). Delete it to wipe all balances/inventories.
