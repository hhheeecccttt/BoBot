"""SQLite persistence. No setup needed — tables auto-create on first run."""
import os
import sqlite3
import threading
from datetime import date

# Overridable for hosting (e.g. Fly.io volume): DB_PATH=/data/bot.db
DB_PATH = os.getenv("DB_PATH", "bot.db")
_lock = threading.Lock()


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _lock, get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            balance INTEGER NOT NULL DEFAULT 0,
            total_spins INTEGER NOT NULL DEFAULT 0,
            spins_used_today INTEGER NOT NULL DEFAULT 0,
            last_reset TEXT NOT NULL DEFAULT '',
            last_spin_date TEXT NOT NULL DEFAULT '',
            streak INTEGER NOT NULL DEFAULT 0,
            longest_streak INTEGER NOT NULL DEFAULT 0,
            total_earned INTEGER NOT NULL DEFAULT 0,
            low_pulls INTEGER NOT NULL DEFAULT 0,
            mid_pulls INTEGER NOT NULL DEFAULT 0,
            high_pulls INTEGER NOT NULL DEFAULT 0,
            elite_pulls INTEGER NOT NULL DEFAULT 0,
            dih_pulls INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            rarity TEXT NOT NULL,
            quality TEXT NOT NULL,
            ore TEXT NOT NULL,
            acquired_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_inv_user ON inventory(user_id);
        CREATE TABLE IF NOT EXISTS market (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            seller_id TEXT NOT NULL,
            rarity TEXT NOT NULL,
            quality TEXT NOT NULL,
            ore TEXT NOT NULL,
            price INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS achievements (
            user_id TEXT NOT NULL,
            ach_id TEXT NOT NULL,
            unlocked_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (user_id, ach_id)
        );
        """)

# ---------- users ----------

def get_user(user_id: str) -> dict:
    with _lock, get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if row is None:
            conn.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
            conn.commit()
            row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return dict(row)


def update_user(user_id: str, **fields):
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    with _lock, get_conn() as conn:
        conn.execute(f"UPDATE users SET {cols} WHERE user_id = ?", (*fields.values(), user_id))
        conn.commit()


def reset_spins_if_new_day(user_id: str, today: str) -> dict:
    """Reset spins_used_today if it's a new UTC day. Returns fresh user dict."""
    u = get_user(user_id)
    if u["last_reset"] != today:
        update_user(user_id, spins_used_today=0, last_reset=today)
        u = get_user(user_id)
    return u


def update_streak(user_id: str, today: str) -> dict:
    """Call on every spin. Returns fresh user dict with updated streak."""
    from datetime import datetime as _dt
    u = get_user(user_id)
    last = u["last_spin_date"] or ""
    streak = u["streak"] or 0
    if last == today:
        pass  # already counted today
    elif last == "":
        streak = 1
    else:
        try:
            td = _dt.strptime(today, "%Y-%m-%d").date()
            yd = td.fromordinal(td.toordinal() - 1).isoformat()
            streak = streak + 1 if last == yd else 1
        except ValueError:
            streak = 1
    longest = max(u["longest_streak"] or 0, streak)
    update_user(user_id, streak=streak, longest_streak=longest, last_spin_date=today)
    return get_user(user_id)

# ---------- inventory ----------

def add_item(user_id: str, rarity: str, quality: str, ore: str) -> int:
    with _lock, get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO inventory (user_id, rarity, quality, ore) VALUES (?,?,?,?)",
            (user_id, rarity, quality, ore),
        )
        conn.commit()
        return cur.lastrowid


def remove_one_item(user_id: str, rarity: str, quality: str, ore: str):
    """Remove a single matching item. Returns item id or None."""
    with _lock, get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM inventory WHERE user_id=? AND rarity=? AND quality=? AND ore=? ORDER BY id LIMIT 1",
            (user_id, rarity, quality, ore),
        ).fetchone()
        if row is None:
            return None
        conn.execute("DELETE FROM inventory WHERE id=?", (row["id"],))
        conn.commit()
        return row["id"]


def remove_item_by_id(user_id: str, item_id: int):
    with _lock, get_conn() as conn:
        row = conn.execute("SELECT * FROM inventory WHERE id=? AND user_id=?", (item_id, user_id)).fetchone()
        if row is None:
            return None
        conn.execute("DELETE FROM inventory WHERE id=?", (item_id,))
        conn.commit()
        return dict(row)


def get_inventory_grouped(user_id: str) -> list[dict]:
    """Aggregated: [{rarity, quality, ore, count}, ...]"""
    with _lock, get_conn() as conn:
        rows = conn.execute(
            """SELECT rarity, quality, ore, COUNT(*) as count
               FROM inventory WHERE user_id=? GROUP BY rarity, quality, ore
               ORDER BY count DESC""",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def count_inventory(user_id: str) -> int:
    with _lock, get_conn() as conn:
        row = conn.execute("SELECT COUNT(*) c FROM inventory WHERE user_id=?", (user_id,)).fetchone()
        return row["c"]

# ---------- market ----------

def market_list(seller_id: str, rarity: str, quality: str, ore: str, price: int) -> int | None:
    """Removes one item from seller inventory and creates a listing. Returns listing id or None."""
    item_id = remove_one_item(seller_id, rarity, quality, ore)
    if item_id is None:
        return None
    with _lock, get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO market (seller_id, rarity, quality, ore, price) VALUES (?,?,?,?,?)",
            (seller_id, rarity, quality, ore, price),
        )
        conn.commit()
        return cur.lastrowid


def market_view(limit: int = 10) -> list[dict]:
    with _lock, get_conn() as conn:
        rows = conn.execute("SELECT * FROM market ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


def market_buy(listing_id: int, buyer_id: str):
    """Returns (ok: bool, message: str)."""
    with _lock, get_conn() as conn:
        listing = conn.execute("SELECT * FROM market WHERE id=?", (listing_id,)).fetchone()
        if listing is None:
            return False, "That listing doesn't exist (already sold?)."
        listing = dict(listing)
        if listing["seller_id"] == buyer_id:
            return False, "You can't buy your own listing. Use `/market_cancel` to take it down."
        buyer = dict(conn.execute("SELECT * FROM users WHERE user_id=?", (buyer_id,)).fetchone() or {"balance": 0})
        if buyer.get("balance", 0) < listing["price"]:
            return False, f"You need ${listing['price']:,} but only have ${buyer.get('balance',0):,}."
        seller = conn.execute("SELECT * FROM users WHERE user_id=?", (listing["seller_id"],)).fetchone()
        # transfer money
        conn.execute("UPDATE users SET balance = balance - ? WHERE user_id=?", (listing["price"], buyer_id))
        conn.execute("UPDATE users SET balance = balance + ?, total_earned = total_earned + ? WHERE user_id=?",
                     (listing["price"], listing["price"], listing["seller_id"]))
        # transfer item
        conn.execute("INSERT INTO inventory (user_id, rarity, quality, ore) VALUES (?,?,?,?)",
                     (buyer_id, listing["rarity"], listing["quality"], listing["ore"]))
        conn.execute("DELETE FROM market WHERE id=?", (listing_id,))
        conn.commit()
        return True, f"You bought **{listing['quality']} {listing['ore']}** ({listing['rarity']}) for **${listing['price']:,}**!"


def market_cancel(listing_id: int, user_id: str):
    with _lock, get_conn() as conn:
        listing = conn.execute("SELECT * FROM market WHERE id=? AND seller_id=?", (listing_id, user_id)).fetchone()
        if listing is None:
            return False, "Listing not found or not yours."
        listing = dict(listing)
        conn.execute("INSERT INTO inventory (user_id, rarity, quality, ore) VALUES (?,?,?,?)",
                     (user_id, listing["rarity"], listing["quality"], listing["ore"]))
        conn.execute("DELETE FROM market WHERE id=?", (listing_id,))
        conn.commit()
        return True, "Listing cancelled — item returned to your inventory."

# ---------- achievements ----------

def has_achievement(user_id: str, ach_id: str) -> bool:
    with _lock, get_conn() as conn:
        return conn.execute("SELECT 1 FROM achievements WHERE user_id=? AND ach_id=?", (user_id, ach_id)).fetchone() is not None


def grant_achievement(user_id: str, ach_id: str) -> bool:
    """Returns True if newly unlocked."""
    if has_achievement(user_id, ach_id):
        return False
    with _lock, get_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO achievements (user_id, ach_id) VALUES (?,?)", (user_id, ach_id))
        conn.commit()
        return True


def get_achievements(user_id: str) -> list[str]:
    with _lock, get_conn() as conn:
        rows = conn.execute("SELECT ach_id FROM achievements WHERE user_id=?", (user_id,)).fetchall()
        return [r["ach_id"] for r in rows]
