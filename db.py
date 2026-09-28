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


# Every table below is scoped by guild_id: each Discord server gets a fully
# independent economy (its own balances, inventories, market, ...).
# Pre-scope databases are migrated with old rows kept under guild '0' (legacy).
_USERS_DDL = """CREATE TABLE users (
    guild_id TEXT NOT NULL DEFAULT '0',
    user_id TEXT NOT NULL,
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
    mail_public INTEGER NOT NULL DEFAULT 0,
    balance_public INTEGER NOT NULL DEFAULT 0,
    timezone TEXT NOT NULL DEFAULT 'UTC',
    bank_balance INTEGER NOT NULL DEFAULT 0,
    rarest_spin TEXT NOT NULL DEFAULT '',
    rarest_buy TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (guild_id, user_id)
);"""
_INVENTORY_DDL = """CREATE TABLE inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id TEXT NOT NULL DEFAULT '0',
    user_id TEXT NOT NULL,
    rarity TEXT NOT NULL,
    quality TEXT NOT NULL,
    ore TEXT NOT NULL,
    origin TEXT NOT NULL DEFAULT 'spin',
    origin_detail TEXT NOT NULL DEFAULT '',
    acquired_at TEXT NOT NULL DEFAULT (datetime('now'))
);"""
_VAULT_DDL = """CREATE TABLE vault (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id TEXT NOT NULL DEFAULT '0',
    user_id TEXT NOT NULL,
    rarity TEXT NOT NULL,
    quality TEXT NOT NULL,
    ore TEXT NOT NULL,
    origin TEXT NOT NULL DEFAULT 'spin',
    origin_detail TEXT NOT NULL DEFAULT '',
    stored_at TEXT NOT NULL DEFAULT (datetime('now'))
);"""
_MARKET_DDL = """CREATE TABLE market (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id TEXT NOT NULL DEFAULT '0',
    seller_id TEXT NOT NULL,
    rarity TEXT NOT NULL,
    quality TEXT NOT NULL,
    ore TEXT NOT NULL,
    price INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);"""
_ACH_DDL = """CREATE TABLE achievements (
    guild_id TEXT NOT NULL DEFAULT '0',
    user_id TEXT NOT NULL,
    ach_id TEXT NOT NULL,
    unlocked_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (guild_id, user_id, ach_id)
);"""
_TRADES_DDL = """CREATE TABLE trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id TEXT NOT NULL DEFAULT '0',
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
);"""
_MAIL_DDL = """CREATE TABLE mail (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id TEXT NOT NULL DEFAULT '0',
    user_id TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    is_read INTEGER NOT NULL DEFAULT 0
);"""
_KV_DDL = """CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);"""


def _ensure_guild_table(conn, table: str, create_ddl: str):
    """Create fresh, or rebuild pre-scope tables preserving rows under guild '0'."""
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if not cols:
        conn.executescript(create_ddl)
        return
    if "guild_id" in cols:
        return
    # idempotent: a previous interrupted migration may have left NAME_legacy behind
    conn.execute(f"DROP TABLE IF EXISTS {table}_legacy")
    conn.execute(f"ALTER TABLE {table} RENAME TO {table}_legacy")
    conn.executescript(create_ddl)
    keep = [c for c in cols if c != "id" or table in ("inventory", "vault", "market", "trades", "mail")]
    # 'id' AUTOINCREMENT cols are preserved as-is; everything else copied + guild '0'
    collist = ", ".join(keep)
    conn.execute(f"INSERT INTO {table} (guild_id, {collist}) SELECT '0', {collist} FROM {table}_legacy")
    conn.execute(f"DROP TABLE {table}_legacy")


def init_db():
    with _lock, get_conn() as conn:
        for _table, _ddl in (
            ("users", _USERS_DDL), ("inventory", _INVENTORY_DDL), ("vault", _VAULT_DDL),
            ("market", _MARKET_DDL), ("achievements", _ACH_DDL), ("trades", _TRADES_DDL),
            ("mail", _MAIL_DDL),
        ):
            _ensure_guild_table(conn, _table, _ddl)
        conn.executescript(_KV_DDL)  # kv stays global (kill-switch must be global)
        conn.executescript("""
        CREATE INDEX IF NOT EXISTS idx_inv_user ON inventory(guild_id, user_id);
        CREATE INDEX IF NOT EXISTS idx_inv_stack ON inventory(guild_id, user_id, rarity, quality, ore);
        CREATE INDEX IF NOT EXISTS idx_inv_origin ON inventory(guild_id, user_id, origin);
        CREATE INDEX IF NOT EXISTS idx_inv_ore ON inventory(guild_id, user_id, ore);
        CREATE INDEX IF NOT EXISTS idx_vault_user ON vault(guild_id, user_id);
        CREATE INDEX IF NOT EXISTS idx_vault_stack ON vault(guild_id, user_id, rarity, quality, ore);
        CREATE INDEX IF NOT EXISTS idx_market_seller ON market(guild_id, seller_id);
        CREATE INDEX IF NOT EXISTS idx_market_ore ON market(guild_id, ore);
        CREATE INDEX IF NOT EXISTS idx_ach_user ON achievements(guild_id, user_id);
        CREATE INDEX IF NOT EXISTS idx_mail_user ON mail(guild_id, user_id);
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
            ("users", "mail_public", "INTEGER NOT NULL DEFAULT 0"),
            ("users", "balance_public", "INTEGER NOT NULL DEFAULT 0"),
            ("users", "timezone", "TEXT NOT NULL DEFAULT 'UTC'"),
            ("users", "bank_balance", "INTEGER NOT NULL DEFAULT 0"),
            ("users", "rarest_spin", "TEXT NOT NULL DEFAULT ''"),
            ("users", "rarest_buy", "TEXT NOT NULL DEFAULT ''"),
            ("inventory", "origin", "TEXT NOT NULL DEFAULT 'spin'"),
            ("inventory", "origin_detail", "TEXT NOT NULL DEFAULT ''"),
            ("vault", "origin_detail", "TEXT NOT NULL DEFAULT ''"),
        ):
            try:
                conn.execute(f"ALTER TABLE {_table} ADD COLUMN {_col} {_ddl}")
            except Exception:
                pass  # already exists
        # prune unlocks for achievements that no longer exist (keeps x/y honest)
        try:
            import config as _cfg
            valid = list(_cfg.ACHIEVEMENTS.keys())
            if valid:
                q = ",".join("?" for _ in valid)
                conn.execute(f"DELETE FROM achievements WHERE ach_id NOT IN ({q})", valid)
        except Exception:
            pass
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
    """Reset spins_used_today if it's a new day (in the user's timezone). Returns fresh user."""
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

def add_item(user_id: str, rarity: str, quality: str, ore: str, origin: str = "spin",
             origin_detail: str = "") -> int:
    with _lock, get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO inventory (user_id, rarity, quality, ore, origin, origin_detail) VALUES (?,?,?,?,?,?)",
            (user_id, rarity, quality, ore, origin, origin_detail),
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
    """True if the user currently holds at least one of every listed ore (collectors).
    Inventory + vault count together."""
    if not ore_names:
        return False
    with _lock, get_conn() as conn:
        owned = {r["ore"] for r in
                 conn.execute("SELECT DISTINCT ore FROM inventory WHERE user_id=?", (user_id,)).fetchall()}
        owned |= {r["ore"] for r in
                  conn.execute("SELECT DISTINCT ore FROM vault WHERE user_id=?", (user_id,)).fetchall()}
        return all(o in owned for o in ore_names)


def owns_all_stacks(user_id: str, pairs: list[tuple[str, str]]) -> bool:
    """True if the user holds every (ore, quality) combo (quality collectors).
    Inventory + vault count together."""
    if not pairs:
        return False
    with _lock, get_conn() as conn:
        owned = {(r["ore"], r["quality"]) for r in conn.execute(
            "SELECT DISTINCT ore, quality FROM inventory WHERE user_id=?", (user_id,)).fetchall()}
        owned |= {(r["ore"], r["quality"]) for r in conn.execute(
            "SELECT DISTINCT ore, quality FROM vault WHERE user_id=?", (user_id,)).fetchall()}
        return all(p in owned for p in pairs)


def revoke_achievement(user_id: str, ach_id: str) -> bool:
    """Returns True if something was removed."""
    with _lock, get_conn() as conn:
        cur = conn.execute("DELETE FROM achievements WHERE user_id=? AND ach_id=?",
                           (user_id, ach_id))
        conn.commit()
        return cur.rowcount > 0


def latest_rarest(user_id: str, market_bought: bool) -> dict | None:
    """Latest item of the rarest tier the user holds.
    market_bought=True -> only origin='market'; False -> everything else."""
    import config as _cfg
    order = " ".join(f"WHEN '{r}' THEN {i}" for i, r in enumerate(_cfg.RARITIES))
    clause = "origin = 'market'" if market_bought else "origin != 'market'"
    with _lock, get_conn() as conn:
        row = conn.execute(
            f"""SELECT * FROM inventory WHERE user_id=? AND {clause}
               ORDER BY CASE rarity {order} END DESC, id DESC LIMIT 1""",
            (user_id,),
        ).fetchone()
        return dict(row) if row else None


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


def origin_sellers(user_id: str, rarity: str, quality: str, ore: str) -> dict:
    """Who market-bought stack units came from: {seller_id: count}."""
    with _lock, get_conn() as conn:
        rows = conn.execute(
            """SELECT origin_detail, COUNT(*) as count FROM inventory
               WHERE user_id=? AND rarity=? AND quality=? AND ore=?
               AND origin='market' AND origin_detail != ''
               GROUP BY origin_detail""",
            (user_id, rarity, quality, ore),
        ).fetchall()
        return {r["origin_detail"]: r["count"] for r in rows}


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

def market_list(guild_id: str, seller_id: str, rarity: str, quality: str, ore: str,
                price: int) -> int | None:
    """Removes one item from seller inventory and creates a listing. Returns listing id or None."""
    item_id = remove_one_item(seller_id, rarity, quality, ore)
    if item_id is None:
        return None
    with _lock, get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO market (guild_id, seller_id, rarity, quality, ore, price) VALUES (?,?,?,?,?,?)",
            (guild_id, seller_id, rarity, quality, ore, price),
        )
        conn.commit()
        return cur.lastrowid


def market_view(guild_id: str, limit: int = 10) -> list[dict]:
    with _lock, get_conn() as conn:
        rows = conn.execute("SELECT * FROM market WHERE guild_id=? ORDER BY id DESC LIMIT ?",
                            (guild_id, limit)).fetchall()
        return [dict(r) for r in rows]


def market_get(listing_id: int, guild_id: str | None = None) -> dict | None:
    with _lock, get_conn() as conn:
        if guild_id is None:
            row = conn.execute("SELECT * FROM market WHERE id=?", (listing_id,)).fetchone()
        else:
            row = conn.execute("SELECT * FROM market WHERE id=? AND guild_id=?",
                               (listing_id, guild_id)).fetchone()
        return dict(row) if row else None


def market_ores(guild_id: str) -> list[str]:
    """All ore names currently listed (for the filter dropdown)."""
    with _lock, get_conn() as conn:
        rows = conn.execute("SELECT DISTINCT ore FROM market WHERE guild_id=? ORDER BY ore",
                            (guild_id,)).fetchall()
        return [r["ore"] for r in rows]


def market_ores_with_rarity(guild_id: str) -> list[dict]:
    """One row per ore with its rarities: [{ore, rarities: [..]}, ...]"""
    with _lock, get_conn() as conn:
        rows = conn.execute("SELECT DISTINCT ore, rarity FROM market WHERE guild_id=? ORDER BY ore, rarity",
                            (guild_id,)).fetchall()
        grouped: dict[str, list[str]] = {}
        for r in rows:
            grouped.setdefault(r["ore"], []).append(r["rarity"])
        return [{"ore": ore, "rarities": rars} for ore, rars in grouped.items()]


def market_qualities(guild_id: str, ore: str) -> list[str]:
    with _lock, get_conn() as conn:
        rows = conn.execute("SELECT DISTINCT quality FROM market WHERE guild_id=? AND ore=? ORDER BY quality",
                            (guild_id, ore)).fetchall()
        return [r["quality"] for r in rows]


def market_browse(guild_id: str, ore: str | None = None, quality: str | None = None,
                  sort: str = "new", limit: int = 10, tier: str | None = None) -> list[dict]:
    """sort: new | cheapest | expensive | average (closest to mean price)."""
    clauses, params = ["guild_id = ?"], [guild_id]
    if ore:
        clauses.append("ore = ?")
        params.append(ore)
    if quality:
        clauses.append("quality = ?")
        params.append(quality)
    if tier:
        clauses.append("rarity = ?")
        params.append(tier)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with _lock, get_conn() as conn:
        if sort == "average":
            avg = conn.execute(f"SELECT AVG(price) a FROM market {where}", params).fetchone()["a"] or 0
            rows = conn.execute(
                f"SELECT * FROM market {where} ORDER BY ABS(price - ?) ASC LIMIT ?",
                (*params, avg, limit)).fetchall()
        else:
            order = {"cheapest": "price ASC, id DESC",
                     "expensive": "price DESC, id DESC"}.get(sort, "id DESC")
            rows = conn.execute(f"SELECT * FROM market {where} ORDER BY {order} LIMIT ?",
                                (*params, limit)).fetchall()
        return [dict(r) for r in rows]


def market_buy(listing_id: int, buyer_id: str, guild_id: str | None = None):
    """Returns (ok: bool, message: str)."""
    with _lock, get_conn() as conn:
        if guild_id is None:
            listing = conn.execute("SELECT * FROM market WHERE id=?", (listing_id,)).fetchone()
        else:
            listing = conn.execute("SELECT * FROM market WHERE id=? AND guild_id=?",
                                   (listing_id, guild_id)).fetchone()
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
        # transfer item (marked as market-bought, seller remembered for inspect)
        conn.execute("INSERT INTO inventory (user_id, rarity, quality, ore, origin, origin_detail) VALUES (?,?,?,?,?,?)",
                     (buyer_id, listing["rarity"], listing["quality"], listing["ore"], "market", listing["seller_id"]))
        conn.execute("DELETE FROM market WHERE id=?", (listing_id,))
        conn.commit()
        return True, f"You bought **{listing['quality']} {listing['ore']}** ({listing['rarity']}) for **${listing['price']:,}**!"


def market_cancel(listing_id: int, user_id: str, guild_id: str | None = None):
    with _lock, get_conn() as conn:
        if guild_id is None:
            listing = conn.execute("SELECT * FROM market WHERE id=? AND seller_id=?",
                                   (listing_id, user_id)).fetchone()
        else:
            listing = conn.execute("SELECT * FROM market WHERE id=? AND seller_id=? AND guild_id=?",
                                   (listing_id, user_id, guild_id)).fetchone()
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
                     quality: str | None = None, limit: int = 10,
                     tier: str | None = None) -> list[dict]:
    clauses, params = ["seller_id = ?"], [seller_id]
    if ore:
        clauses.append("ore = ?")
        params.append(ore)
    if quality:
        clauses.append("quality = ?")
        params.append(quality)
    if tier:
        clauses.append("rarity = ?")
        params.append(tier)
    with _lock, get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM market WHERE {' AND '.join(clauses)} ORDER BY id DESC LIMIT ?",
            (*params, limit)).fetchall()
        return [dict(r) for r in rows]

# ---------- trades (persisted so restarts don't kill active trades) ----------

def create_trade(guild_id: str, a_id: str, b_id: str, a_name: str, b_name: str,
                 channel_id: str = "", message_id: str = "") -> int:
    with _lock, get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO trades (guild_id, a_id, b_id, a_name, b_name, channel_id, message_id) VALUES (?,?,?,?,?,?,?)",
            (guild_id, a_id, b_id, a_name, b_name, channel_id, message_id),
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
    "max_spin": "100000",
}


def get_setting(key: str, guild_id: str | None = None) -> str:
    with _lock, get_conn() as conn:
        if guild_id is not None:
            row = conn.execute("SELECT value FROM kv WHERE key=?", (f"{guild_id}:{key}",)).fetchone()
            if row is not None:
                return row["value"]
        row = conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        if row is None:
            return KV_DEFAULTS.get(key, "")
        return row["value"]


def set_setting(key: str, value: str, guild_id: str | None = None):
    with _lock, get_conn() as conn:
        conn.execute("INSERT INTO kv (key, value) VALUES (?,?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (f"{guild_id}:{key}" if guild_id is not None else key, value))
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


def unread_mail_count(user_id: str) -> int:
    with _lock, get_conn() as conn:
        row = conn.execute("SELECT COUNT(*) c FROM mail WHERE user_id=? AND is_read=0",
                           (user_id,)).fetchone()
        return row["c"]


def mark_mail_read(user_id: str):
    with _lock, get_conn() as conn:
        conn.execute("UPDATE mail SET is_read=1 WHERE user_id=?", (user_id,))
        conn.commit()


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


def vault_grouped(user_id: str) -> list[dict]:
    """Aggregated vault stacks: [{rarity, quality, ore, count}, ...]"""
    with _lock, get_conn() as conn:
        rows = conn.execute(
            """SELECT rarity, quality, ore, COUNT(*) as count
               FROM vault WHERE user_id=? GROUP BY rarity, quality, ore
               ORDER BY count DESC""",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]


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

def top_balances(guild_id: str, limit: int = 10) -> list[dict]:
    with _lock, get_conn() as conn:
        rows = conn.execute(
            "SELECT user_id, balance, total_spins FROM users WHERE user_id LIKE ? "
            "ORDER BY balance DESC, total_spins DESC LIMIT ?",
            (f"{guild_id}:%", limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_rank(guild_id: str, user_id: str) -> int:
    """1-based rank by balance within the server. Returns -1 if user not found."""
    with _lock, get_conn() as conn:
        row = conn.execute("SELECT balance FROM users WHERE user_id=?", (user_id,)).fetchone()
        if row is None:
            return -1
        higher = conn.execute(
            "SELECT COUNT(*) c FROM users WHERE user_id LIKE ? AND balance > ?",
            (f"{guild_id}:%", row["balance"],)).fetchone()
        return higher["c"] + 1
