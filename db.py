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
            dih_pulls INTEGER NOT NULL DEFAULT 0,
            perfect_pulls INTEGER NOT NULL DEFAULT 0,
            buy_count INTEGER NOT NULL DEFAULT 0,
            sell_count INTEGER NOT NULL DEFAULT 0,
            inv_public INTEGER NOT NULL DEFAULT 0,
            ach_public INTEGER NOT NULL DEFAULT 0,
            stats_public INTEGER NOT NULL DEFAULT 0,
            bank_public INTEGER NOT NULL DEFAULT 0,
            vault_public INTEGER NOT NULL DEFAULT 0,
            market_public INTEGER NOT NULL DEFAULT 0,
            bank_balance INTEGER NOT NULL DEFAULT 0,
            rarest_spin TEXT NOT NULL DEFAULT '',
            rarest_buy TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            rarity TEXT NOT NULL,
            quality TEXT NOT NULL,
            ore TEXT NOT NULL,
            origin TEXT NOT NULL DEFAULT 'spin',
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
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            a_id TEXT NOT NULL,
            b_id TEXT NOT NULL,
            a_name TEXT NOT NULL DEFAULT '',
            b_name TEXT NOT NULL DEFAULT '',
            a_pick TEXT,
            b_pick TEXT,
            a_ok INTEGER NOT NULL DEFAULT 0,
            b_ok INTEGER NOT NULL DEFAULT 0,
            stage TEXT NOT NULL DEFAULT 'request',
            channel_id TEXT NOT NULL DEFAULT '',
            message_id TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS kv (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS mail (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            text TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            is_read INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_mail_user ON mail(user_id);
        CREATE TABLE IF NOT EXISTS vault (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            rarity TEXT NOT NULL,
            quality TEXT NOT NULL,
            ore TEXT NOT NULL,
            origin TEXT NOT NULL DEFAULT 'spin',
            stored_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_vault_user ON vault(user_id);
        """)
        # --- migrations for DBs created before these columns existed ---
        for _table, _col, _ddl in (
            ("users", "perfect_pulls", "INTEGER NOT NULL DEFAULT 0"),
            ("users", "buy_count", "INTEGER NOT NULL DEFAULT 0"),
            ("users", "sell_count", "INTEGER NOT NULL DEFAULT 0"),
            ("users", "inv_public", "INTEGER NOT NULL DEFAULT 0"),
            ("users", "ach_public", "INTEGER NOT NULL DEFAULT 0"),
            ("users", "stats_public", "INTEGER NOT NULL DEFAULT 0"),
            ("users", "bank_public", "INTEGER NOT NULL DEFAULT 0"),
            ("users", "vault_public", "INTEGER NOT NULL DEFAULT 0"),
            ("users", "market_public", "INTEGER NOT NULL DEFAULT 0"),
            ("users", "bank_balance", "INTEGER NOT NULL DEFAULT 0"),
            ("users", "rarest_spin", "TEXT NOT NULL DEFAULT ''"),
            ("users", "rarest_buy", "TEXT NOT NULL DEFAULT ''"),
            ("inventory", "origin", "TEXT NOT NULL DEFAULT 'spin'"),
        ):
            try:
                conn.execute(f"ALTER TABLE {_table} ADD COLUMN {_col} {_ddl}")
            except Exception:
                pass  # already exists
        conn.commit()

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

def add_item(user_id: str, rarity: str, quality: str, ore: str, origin: str = "spin") -> int:
    with _lock, get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO inventory (user_id, rarity, quality, ore, origin) VALUES (?,?,?,?,?)",
            (user_id, rarity, quality, ore, origin),
        )
        conn.commit()
        return cur.lastrowid


def take_one_item(user_id: str, rarity: str, quality: str, ore: str):
    """Remove a single matching item. Returns the full row dict or None (for trades)."""
    with _lock, get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM inventory WHERE user_id=? AND rarity=? AND quality=? AND ore=? ORDER BY id LIMIT 1",
            (user_id, rarity, quality, ore),
        ).fetchone()
        if row is None:
            return None
        item = dict(row)
        conn.execute("DELETE FROM inventory WHERE id=?", (item["id"],))
        conn.commit()
        return item


def add_many_items(user_id: str, pulls: list[tuple[str, str, str]], origin: str = "spin"):
    """Batch insert (multi-spin). One transaction — fast even for 1000s of rows."""
    if not pulls:
        return
    with _lock, get_conn() as conn:
        conn.executemany(
            "INSERT INTO inventory (user_id, rarity, quality, ore, origin) VALUES (?,?,?,?,?)",
            [(user_id, r, q, o, origin) for r, q, o in pulls],
        )
        conn.commit()


def remove_many_items(user_id: str, rarity: str, quality: str, ore: str,
                      limit: int | None = None, origin: str | None = None) -> int:
    """Batch delete from one stack. Single statement — returns rows removed."""
    extra, params = "", []
    if origin is not None:
        extra, params = " AND origin = ?", [origin]
    with _lock, get_conn() as conn:
        if limit is None:
            cur = conn.execute(
                f"DELETE FROM inventory WHERE user_id=? AND rarity=? AND quality=? AND ore=?{extra}",
                (user_id, rarity, quality, ore, *params),
            )
        else:
            cur = conn.execute(
                f"""DELETE FROM inventory WHERE id IN (
                     SELECT id FROM inventory WHERE user_id=? AND rarity=? AND quality=? AND ore=?{extra}
                     ORDER BY id LIMIT ?)""",
                (user_id, rarity, quality, ore, *params, limit),
            )
        conn.commit()
        return cur.rowcount


def clear_stacks(user_id: str, stacks: list[dict]) -> int:
    """Delete every row in the given grouped stacks. Returns total removed."""
    if not stacks:
        return 0
    total = 0
    with _lock, get_conn() as conn:
        for t in stacks:
            cur = conn.execute(
                "DELETE FROM inventory WHERE user_id=? AND rarity=? AND quality=? AND ore=?",
                (user_id, t["rarity"], t["quality"], t["ore"]),
            )
            total += cur.rowcount
        conn.commit()
        return total


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


def get_ores_overview(user_id: str) -> list[dict]:
    """Level 1: one row per ore name, all qualities combined: [{ore, count, rarities}, ...]"""
    with _lock, get_conn() as conn:
        rows = conn.execute(
            """SELECT ore, COUNT(*) as count, GROUP_CONCAT(DISTINCT rarity) as rarities
               FROM inventory WHERE user_id=? GROUP BY ore ORDER BY count DESC""",
            (user_id,),
        ).fetchall()
        return [{"ore": r["ore"], "count": r["count"],
                 "rarities": (r["rarities"] or "").split(",")} for r in rows]


def get_ores_by_quality(user_id: str, quality: str) -> list[dict]:
    """Level 1 filtered: ores the user owns in one quality: [{ore, count, rarities}, ...]"""
    with _lock, get_conn() as conn:
        rows = conn.execute(
            """SELECT ore, COUNT(*) as count, GROUP_CONCAT(DISTINCT rarity) as rarities
               FROM inventory WHERE user_id=? AND quality=? GROUP BY ore ORDER BY count DESC""",
            (user_id, quality),
        ).fetchall()
        return [{"ore": r["ore"], "count": r["count"],
                 "rarities": (r["rarities"] or "").split(",")} for r in rows]


def get_ore_detail(user_id: str, ore: str, quality: str | None = None) -> list[dict]:
    """Level 2: quality breakdown for one ore: [{rarity, quality, ore, count}, ...]
    Sorted Chipped -> Scratched -> Perfect. Optional quality filter."""
    import config as _cfg
    order = " ".join(f"WHEN '{q}' THEN {i}" for i, q in enumerate(_cfg.QUALITIES))
    clauses, params = ["user_id = ?", "ore = ?"], [user_id, ore]
    if quality:
        clauses.append("quality = ?")
        params.append(quality)
    with _lock, get_conn() as conn:
        rows = conn.execute(
            f"""SELECT rarity, quality, ore, COUNT(*) as count FROM inventory
               WHERE {' AND '.join(clauses)} GROUP BY rarity, quality, ore
               ORDER BY CASE quality {order} END""",
            params,
        ).fetchall()
        return [dict(r) for r in rows]


def owns_all_ores(user_id: str, ore_names: list[str]) -> bool:
    """True if the user currently holds at least one of every listed ore (collectors)."""
    if not ore_names:
        return False
    with _lock, get_conn() as conn:
        rows = conn.execute("SELECT DISTINCT ore FROM inventory WHERE user_id=?", (user_id,)).fetchall()
        owned = {r["ore"] for r in rows}
        return all(o in owned for o in ore_names)


def owns_all_stacks(user_id: str, pairs: list[tuple[str, str]]) -> bool:
    """True if the user holds every (ore, quality) combo (quality collectors)."""
    if not pairs:
        return False
    with _lock, get_conn() as conn:
        rows = conn.execute("SELECT DISTINCT ore, quality FROM inventory WHERE user_id=?",
                            (user_id,)).fetchall()
        owned = {(r["ore"], r["quality"]) for r in rows}
        return all(p in owned for p in pairs)


def origin_counts(user_id: str, rarity: str, quality: str, ore: str) -> dict:
    """How a stack was obtained: {'spin': n, 'market': m, ...}"""
    with _lock, get_conn() as conn:
        rows = conn.execute(
            """SELECT origin, COUNT(*) as count FROM inventory
               WHERE user_id=? AND rarity=? AND quality=? AND ore=?
               GROUP BY origin""",
            (user_id, rarity, quality, ore),
        ).fetchall()
        return {r["origin"]: r["count"] for r in rows}


def inventory_value(user_id: str, ore: str | None = None, quality: str | None = None) -> tuple[int, int]:
    """(total count, total quicksell value), optionally for one ore / quality. Values via config."""
    import config as _cfg
    clauses, params = ["user_id = ?"], [user_id]
    if ore:
        clauses.append("ore = ?")
        params.append(ore)
    if quality:
        clauses.append("quality = ?")
        params.append(quality)
    with _lock, get_conn() as conn:
        rows = conn.execute(
            f"SELECT rarity, COUNT(*) as count FROM inventory WHERE {' AND '.join(clauses)} GROUP BY rarity",
            params,
        ).fetchall()
        total_n = sum(r["count"] for r in rows)
        total_v = sum(_cfg.quicksell_value(r["rarity"]) * r["count"] for r in rows)
        return total_n, total_v

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


def market_get(listing_id: int) -> dict | None:
    with _lock, get_conn() as conn:
        row = conn.execute("SELECT * FROM market WHERE id=?", (listing_id,)).fetchone()
        return dict(row) if row else None


def market_ores() -> list[str]:
    """All ore names currently listed (for the filter dropdown)."""
    with _lock, get_conn() as conn:
        rows = conn.execute("SELECT DISTINCT ore FROM market ORDER BY ore").fetchall()
        return [r["ore"] for r in rows]


def market_ores_with_rarity() -> list[dict]:
    """One row per ore with its rarities: [{ore, rarities: [..]}, ...]"""
    with _lock, get_conn() as conn:
        rows = conn.execute("SELECT DISTINCT ore, rarity FROM market ORDER BY ore, rarity").fetchall()
        grouped: dict[str, list[str]] = {}
        for r in rows:
            grouped.setdefault(r["ore"], []).append(r["rarity"])
        return [{"ore": ore, "rarities": rars} for ore, rars in grouped.items()]


def market_qualities(ore: str) -> list[str]:
    with _lock, get_conn() as conn:
        rows = conn.execute("SELECT DISTINCT quality FROM market WHERE ore=? ORDER BY quality", (ore,)).fetchall()
        return [r["quality"] for r in rows]


def market_browse(ore: str | None = None, quality: str | None = None,
                  sort: str = "new", limit: int = 10) -> list[dict]:
    """sort: new | cheapest | expensive | average (closest to mean price)."""
    clauses, params = [], []
    if ore:
        clauses.append("ore = ?")
        params.append(ore)
    if quality:
        clauses.append("quality = ?")
        params.append(quality)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with _lock, get_conn() as conn:
        if sort == "average":
            avg = conn.execute(f"SELECT AVG(price) a FROM market {where}", params).fetchone()["a"] or 0
            rows = conn.execute(
                f"SELECT * FROM market {where} ORDER BY ABS(price - ?) ASC LIMIT ?",
                (*params, avg, limit)).fetchall()
        else:
            order = {"cheapest": "price ASC", "expensive": "price DESC"}.get(sort, "id DESC")
            rows = conn.execute(f"SELECT * FROM market {where} ORDER BY {order} LIMIT ?",
                                (*params, limit)).fetchall()
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
        conn.execute("UPDATE users SET balance = balance - ?, buy_count = buy_count + 1 WHERE user_id=?",
                     (listing["price"], buyer_id))
        conn.execute("UPDATE users SET balance = balance + ?, total_earned = total_earned + ?, sell_count = sell_count + 1 WHERE user_id=?",
                     (listing["price"], listing["price"], listing["seller_id"]))
        # transfer item (marked as market-bought for inspect provenance)
        conn.execute("INSERT INTO inventory (user_id, rarity, quality, ore, origin) VALUES (?,?,?,?,?)",
                     (buyer_id, listing["rarity"], listing["quality"], listing["ore"], "market"))
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


def market_cancel_all(seller_id: str) -> int:
    """Cancel every listing by this seller, returning items. Returns count."""
    with _lock, get_conn() as conn:
        rows = conn.execute("SELECT * FROM market WHERE seller_id=?", (seller_id,)).fetchall()
        for l in rows:
            conn.execute("INSERT INTO inventory (user_id, rarity, quality, ore) VALUES (?,?,?,?)",
                         (seller_id, l["rarity"], l["quality"], l["ore"]))
        conn.execute("DELETE FROM market WHERE seller_id=?", (seller_id,))
        conn.commit()
        return len(rows)


def market_seller_ores(seller_id: str) -> list[dict]:
    """Distinct ores this seller has listed: [{ore, rarities}, ...]"""
    with _lock, get_conn() as conn:
        rows = conn.execute("SELECT DISTINCT ore, rarity FROM market WHERE seller_id=? ORDER BY ore, rarity",
                            (seller_id,)).fetchall()
        grouped: dict[str, list[str]] = {}
        for r in rows:
            grouped.setdefault(r["ore"], []).append(r["rarity"])
        return [{"ore": ore, "rarities": rars} for ore, rars in grouped.items()]


def market_seller_qualities(seller_id: str, ore: str) -> list[str]:
    with _lock, get_conn() as conn:
        rows = conn.execute("SELECT DISTINCT quality FROM market WHERE seller_id=? AND ore=? ORDER BY quality",
                            (seller_id, ore)).fetchall()
        return [r["quality"] for r in rows]


def market_by_seller(seller_id: str, ore: str | None = None,
                     quality: str | None = None, limit: int = 10) -> list[dict]:
    clauses, params = ["seller_id = ?"], [seller_id]
    if ore:
        clauses.append("ore = ?")
        params.append(ore)
    if quality:
        clauses.append("quality = ?")
        params.append(quality)
    with _lock, get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM market WHERE {' AND '.join(clauses)} ORDER BY id DESC LIMIT ?",
            (*params, limit)).fetchall()
        return [dict(r) for r in rows]

# ---------- trades (persisted so restarts don't kill active trades) ----------

def create_trade(a_id: str, b_id: str, a_name: str, b_name: str,
                 channel_id: str = "", message_id: str = "") -> int:
    with _lock, get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO trades (a_id, b_id, a_name, b_name, channel_id, message_id) VALUES (?,?,?,?,?,?)",
            (a_id, b_id, a_name, b_name, channel_id, message_id),
        )
        conn.commit()
        return cur.lastrowid


def get_trade(tid: int) -> dict | None:
    with _lock, get_conn() as conn:
        row = conn.execute("SELECT * FROM trades WHERE id=?", (tid,)).fetchone()
        return dict(row) if row else None


def update_trade(tid: int, **fields):
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    with _lock, get_conn() as conn:
        conn.execute(f"UPDATE trades SET {cols} WHERE id=?", (*fields.values(), tid))
        conn.commit()


def delete_trade(tid: int):
    with _lock, get_conn() as conn:
        conn.execute("DELETE FROM trades WHERE id=?", (tid,))
        conn.commit()


def open_trades() -> list[dict]:
    with _lock, get_conn() as conn:
        rows = conn.execute("SELECT * FROM trades ORDER BY id").fetchall()
        return [dict(r) for r in rows]


def max_trade_id() -> int:
    with _lock, get_conn() as conn:
        row = conn.execute("SELECT MAX(id) m FROM trades").fetchone()
        return row["m"] or 0


# ---------- kv settings (kill-switch, spins/day, max spin) ----------

KV_DEFAULTS = {
    "commands_enabled": "1",
    "spins_per_day": "3",
    "max_spin": "1000",
}


def get_setting(key: str) -> str:
    with _lock, get_conn() as conn:
        row = conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        if row is None:
            return KV_DEFAULTS.get(key, "")
        return row["value"]


def set_setting(key: str, value: str):
    with _lock, get_conn() as conn:
        conn.execute("INSERT INTO kv (key, value) VALUES (?,?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        conn.commit()


# ---------- mail ----------

def add_mail(user_id: str, text: str):
    with _lock, get_conn() as conn:
        conn.execute("INSERT INTO mail (user_id, text) VALUES (?,?)", (user_id, text))
        conn.commit()


def get_mail(user_id: str, limit: int = 50) -> list[dict]:
    with _lock, get_conn() as conn:
        rows = conn.execute("SELECT * FROM mail WHERE user_id=? ORDER BY id DESC LIMIT ?",
                            (user_id, limit)).fetchall()
        return [dict(r) for r in rows]


def clear_mail(user_id: str) -> int:
    with _lock, get_conn() as conn:
        cur = conn.execute("DELETE FROM mail WHERE user_id=?", (user_id,))
        conn.commit()
        return cur.rowcount


# ---------- vault (same shape as inventory, no quicksell) ----------

def vault_add(user_id: str, rarity: str, quality: str, ore: str, origin: str = "spin") -> int:
    with _lock, get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO vault (user_id, rarity, quality, ore, origin) VALUES (?,?,?,?,?)",
            (user_id, rarity, quality, ore, origin),
        )
        conn.commit()
        return cur.lastrowid


def vault_take_one(user_id: str, rarity: str, quality: str, ore: str):
    with _lock, get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM vault WHERE user_id=? AND rarity=? AND quality=? AND ore=? ORDER BY id LIMIT 1",
            (user_id, rarity, quality, ore),
        ).fetchone()
        if row is None:
            return None
        item = dict(row)
        conn.execute("DELETE FROM vault WHERE id=?", (item["id"],))
        conn.commit()
        return item


def vault_add_many(user_id: str, rows: list[tuple[str, str, str, str]]):
    """Batch insert into vault. rows = [(rarity, quality, ore, origin)]."""
    if not rows:
        return
    with _lock, get_conn() as conn:
        conn.executemany(
            "INSERT INTO vault (user_id, rarity, quality, ore, origin) VALUES (?,?,?,?,?)",
            [(user_id, r, q, o, org) for r, q, o, org in rows],
        )
        conn.commit()


def vault_remove_many(user_id: str, rarity: str, quality: str, ore: str, limit: int,
                      origin: str | None = None) -> int:
    extra, params = (" AND origin = ?", [origin]) if origin is not None else ("", [])
    with _lock, get_conn() as conn:
        cur = conn.execute(
            f"""DELETE FROM vault WHERE id IN (
                 SELECT id FROM vault WHERE user_id=? AND rarity=? AND quality=? AND ore=?{extra}
                 ORDER BY id LIMIT ?)""",
            (user_id, rarity, quality, ore, *params, limit),
        )
        conn.commit()
        return cur.rowcount


def vault_origin_counts(user_id: str, rarity: str, quality: str, ore: str) -> dict:
    with _lock, get_conn() as conn:
        rows = conn.execute(
            """SELECT origin, COUNT(*) as count FROM vault
               WHERE user_id=? AND rarity=? AND quality=? AND ore=? GROUP BY origin""",
            (user_id, rarity, quality, ore),
        ).fetchall()
        return {r["origin"]: r["count"] for r in rows}


def vault_by_quality(user_id: str, quality: str) -> list[dict]:
    with _lock, get_conn() as conn:
        rows = conn.execute(
            """SELECT ore, COUNT(*) as count, GROUP_CONCAT(DISTINCT rarity) as rarities
               FROM vault WHERE user_id=? AND quality=? GROUP BY ore ORDER BY count DESC""",
            (user_id, quality),
        ).fetchall()
        return [{"ore": r["ore"], "count": r["count"],
                 "rarities": (r["rarities"] or "").split(",")} for r in rows]


def vault_overview(user_id: str) -> list[dict]:
    with _lock, get_conn() as conn:
        rows = conn.execute(
            """SELECT ore, COUNT(*) as count, GROUP_CONCAT(DISTINCT rarity) as rarities
               FROM vault WHERE user_id=? GROUP BY ore ORDER BY count DESC""",
            (user_id,),
        ).fetchall()
        return [{"ore": r["ore"], "count": r["count"],
                 "rarities": (r["rarities"] or "").split(",")} for r in rows]


def vault_detail(user_id: str, ore: str) -> list[dict]:
    import config as _cfg
    order = " ".join(f"WHEN '{q}' THEN {i}" for i, q in enumerate(_cfg.QUALITIES))
    with _lock, get_conn() as conn:
        rows = conn.execute(
            f"""SELECT rarity, quality, ore, COUNT(*) as count FROM vault
               WHERE user_id=? AND ore=? GROUP BY rarity, quality, ore
               ORDER BY CASE quality {order} END""",
            (user_id, ore),
        ).fetchall()
        return [dict(r) for r in rows]


def vault_value(user_id: str, ore: str | None = None) -> tuple[int, int]:
    import config as _cfg
    clauses, params = ["user_id = ?"], [user_id]
    if ore:
        clauses.append("ore = ?")
        params.append(ore)
    with _lock, get_conn() as conn:
        rows = conn.execute(
            f"SELECT rarity, COUNT(*) as count FROM vault WHERE {' AND '.join(clauses)} GROUP BY rarity",
            params,
        ).fetchall()
        return (sum(r["count"] for r in rows),
                sum(_cfg.quicksell_value(r["rarity"]) * r["count"] for r in rows))


def vault_count(user_id: str) -> int:
    with _lock, get_conn() as conn:
        row = conn.execute("SELECT COUNT(*) c FROM vault WHERE user_id=?", (user_id,)).fetchone()
        return row["c"]


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

# ---------- leaderboard ----------

def top_balances(limit: int = 10) -> list[dict]:
    with _lock, get_conn() as conn:
        rows = conn.execute(
            "SELECT user_id, balance, total_spins FROM users ORDER BY balance DESC, total_spins DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_rank(user_id: str) -> int:
    """1-based rank by balance. Returns -1 if user not found."""
    with _lock, get_conn() as conn:
        row = conn.execute("SELECT balance FROM users WHERE user_id=?", (user_id,)).fetchone()
        if row is None:
            return -1
        higher = conn.execute("SELECT COUNT(*) c FROM users WHERE balance > ?", (row["balance"],)).fetchone()
        return higher["c"] + 1
