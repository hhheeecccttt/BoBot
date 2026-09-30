# Ore Spin Bot 🎰

Discord bot: 3 spins/day, rarity rolls, quality rolls, quicksell, player market, inventory dropdown, streaks, stats, achievements.

**Server-independent:** every Discord server gets its own separate economy - balances, inventories, vaults, market, achievements, streaks, and even spins/day are all per-server. Same bot, different worlds. (Data from before this update lives under a legacy scope; each server starts fresh.)

## Quick answers to your questions

- **Player market - impossible?** No, totally doable. Included: `/market_post`, `/market_view`, `/market_buy`, `/market_cancel`.
- **Inventory dropdown - impossible?** No, easy. `/inventory` shows an embed + a Discord dropdown (Select menu) to inspect each stack. Discord limits dropdowns to 25 options, so huge inventories show the top 25.
- **Custom emojis / Nitro?** Bots do NOT get free Nitro. But your bot can use **custom emojis from any server it's in** if you add them there (Server Settings → Emoji → upload). Then put `<:name:id>` in `config.py` emoji fields. The bot currently uses default unicode emoji so it works with zero setup.
- **Achievements - too ambitious?** No, they're just "if X then grant badge". Included 12 starter ones.
- **Streak** - tracked (consecutive days with ≥1 spin). Currently set to **no penalty** (`STREAK_REQUIRED_FOR_FULL_LUCK = 1` in `config.py`) so everyone gets normal % from day 1. Tell me if you want reduced luck for broken streaks and I'll wire it in.

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
| `/spin [amount]` | Roll ores (up to 1000/call); summary for multi-spins |
| `/inventory [@user]` | Unified browser: All ores, quality filter, inspect, scoped quicksell/vault |
| `/quicksell_all` | Sell everything (batched, instant even for 30k) |
| `/market_list` | Pick an ore from a dropdown, set a price in the popup |
| `/market_view` | Latest + dropdown filters (ore → quality → cheapest/average/priciest), pages, inspect + Buy button |
| `/market_cancel` | Your latest 10 + dropdowns (ore → quality → pick + button) |
| `/market_cancel_all` | Cancel every listing, items return to you |
| `/trade @player` | Trade ores (one-sided OK, both accept) |
| `/gift ore @player` | Gift ores (they get mail) |
| `/gift money @player` | Gift balance/bank money (confirm UI) |
| `/stats [@user]` | Spins, pulls, streak, balance, assets, rarest spin/buy, achievement count |
| `/achievements [@user]` | Badges (paged, filterable) |
| `/odds` | Rarities, odds, quicksell values |
| `/ores` | Browse every ore tier by tier |
| `/leaderboard` | Richest + most spins, paged, no pings, shows your rank |
| `/balance` | Balance, assets, bank + transfer button |
| `/bank [@user]` | Bank balance + withdraw |
| `/vault [@user]` | Long-term storage (no quicksell) |
| `/mail [@user]` | Sale + gift notifications (paged) |
| `/settings` | Privacy: inventory / achievements / stats / vault / bank / balance / mail / market |
| `/faq` | How quicksell, market, spins work |
| `/help` | List of every command |
| Admin: `/admin_event_give`, `/admin_give`, `/admin_take`, `/admin_ach_add`, `/admin_ach_remove`, `/admin_disable`, `/admin_enable`, `/admin_spins` | Events + emergencies |

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
