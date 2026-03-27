import json
import os
from datetime import date
from typing import Any, Optional

import aiosqlite

DB_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(DB_DIR, "casino.db")


def default_user_stats() -> dict[str, Any]:
    return {
        "plays": {"round": 0, "coinflip": 0, "mines": 0, "roulette": 0, "slot": 0},
        "won": {"round": 0, "coinflip": 0, "mines": 0, "roulette": 0, "slot": 0},
        "lost": {"round": 0, "coinflip": 0, "mines": 0, "roulette": 0, "slot": 0},
        "totals": {"won": 0, "lost": 0},
        "max_win": {"amount": 0, "game": ""},
        "max_loss": {"amount": 0, "game": ""},
        "rob": {
            "attempts": 0,
            "success": 0,
            "stolen_as_robber": 0,
            "fines_paid": 0,
            "stolen_from_victim": 0,
        },
        "chest": {"opened": 0, "mimriks": 0},
        "transfer": {"sent": 0},
    }


GAME_LABELS_RU = {
    "round": "Раунд ставок",
    "coinflip": "Coinflip",
    "mines": "Сапёр",
    "roulette": "Рулетка",
    "slot": "Слот",
}


async def init_db():
    old_users, old_bets = [], []
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=FULL")
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='users'"
        )
        has_users = await cursor.fetchone()
        needs_migration = False

        if has_users:
            cursor = await db.execute("PRAGMA table_info(users)")
            columns = await cursor.fetchall()
            col_names = [c[1] for c in columns]
            if "chat_id" not in col_names:
                needs_migration = True
                cursor = await db.execute("SELECT user_id, username, balance FROM users")
                old_users = await cursor.fetchall()
                cursor = await db.execute("SELECT user_id, amount FROM bets")
                old_bets = await cursor.fetchall()
                await db.execute("DROP TABLE users")
                await db.execute("DROP TABLE bets")

        await db.execute("""
        CREATE TABLE IF NOT EXISTS users(
            user_id INTEGER,
            chat_id INTEGER,
            username TEXT,
            balance INTEGER,
            PRIMARY KEY (user_id, chat_id)
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS bets(
            user_id INTEGER,
            chat_id INTEGER,
            amount INTEGER,
            PRIMARY KEY (user_id, chat_id)
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS round_messages(
            chat_id INTEGER PRIMARY KEY,
            message_id INTEGER
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS round_ready(
            chat_id INTEGER,
            user_id INTEGER,
            PRIMARY KEY (chat_id, user_id)
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS chest_spawns(
            chat_id INTEGER PRIMARY KEY,
            last_spawn REAL,
            next_spawn REAL
        )
        """)
        # Миграция: добавить next_spawn если нет
        cursor = await db.execute("PRAGMA table_info(chest_spawns)")
        cols = [r[1] for r in await cursor.fetchall()]
        if "next_spawn" not in cols:
            await db.execute("ALTER TABLE chest_spawns ADD COLUMN next_spawn REAL")

        await db.execute("""
        CREATE TABLE IF NOT EXISTS rob_cooldown(
            user_id INTEGER,
            chat_id INTEGER,
            last_rob REAL,
            PRIMARY KEY (user_id, chat_id)
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS chat_threads(
            chat_id INTEGER PRIMARY KEY,
            thread_id INTEGER
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS chat_admins(
            chat_id INTEGER PRIMARY KEY,
            admin_user_id INTEGER NOT NULL
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS user_stats(
            user_id INTEGER,
            chat_id INTEGER,
            stats_json TEXT NOT NULL,
            PRIMARY KEY (user_id, chat_id)
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS daily_quest_state(
            user_id INTEGER,
            chat_id INTEGER,
            day TEXT NOT NULL,
            coinflip_count INTEGER DEFAULT 0,
            rob_success INTEGER DEFAULT 0,
            roulette_wins INTEGER DEFAULT 0,
            mines_plays INTEGER DEFAULT 0,
            slot_plays INTEGER DEFAULT 0,
            reward_coinflip INTEGER DEFAULT 0,
            reward_rob INTEGER DEFAULT 0,
            reward_roulette INTEGER DEFAULT 0,
            reward_mines INTEGER DEFAULT 0,
            reward_slot INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, chat_id, day)
        )
        """)

        cursor = await db.execute("PRAGMA table_info(daily_quest_state)")
        dq_cols = [r[1] for r in await cursor.fetchall()]
        if dq_cols and "slot_plays" not in dq_cols:
            await db.execute(
                "ALTER TABLE daily_quest_state ADD COLUMN slot_plays INTEGER DEFAULT 0"
            )
        if dq_cols and "reward_slot" not in dq_cols:
            await db.execute(
                "ALTER TABLE daily_quest_state ADD COLUMN reward_slot INTEGER DEFAULT 0"
            )

        if needs_migration:
            for uid, username, balance in old_users:
                await db.execute(
                    "INSERT INTO users VALUES (?, ?, ?, ?)",
                    (uid, uid, username or "User", balance),
                )
            for uid, amount in old_bets:
                await db.execute(
                    "INSERT INTO bets VALUES (?, ?, ?)",
                    (uid, uid, amount),
                )

        await db.commit()


async def register_user(user_id, chat_id, username):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "SELECT user_id FROM users WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id),
        )
        user = await cursor.fetchone()

        if user is None:
            await db.execute(
                "INSERT INTO users VALUES (?, ?, ?, ?)",
                (user_id, chat_id, username, 500),
            )
            await db.commit()
            return True
        return False


async def get_balance(user_id, chat_id):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "SELECT balance FROM users WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id),
        )
        row = await cursor.fetchone()
        return row[0] if row else None


async def change_balance(user_id, chat_id, amount):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE users SET balance = balance + ? WHERE user_id = ? AND chat_id = ?",
            (amount, user_id, chat_id),
        )
        await db.commit()


async def set_bet(user_id, chat_id, amount):
    async with aiosqlite.connect(DB_NAME) as conn:
        await conn.execute(
            "INSERT OR REPLACE INTO bets VALUES (?, ?, ?)",
            (user_id, chat_id, amount),
        )
        await conn.commit()


async def place_bet_atomic(user_id, chat_id, username: str, amount: int, current_bet: Optional[int]):
    """Атомарно: списание, ставка, обновление username в одной транзакции."""
    new_total = amount + (current_bet or 0)
    async with aiosqlite.connect(DB_NAME) as conn:
        cursor = await conn.execute(
            "SELECT balance FROM users WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id),
        )
        row = await cursor.fetchone()
        if not row or row[0] < amount:
            return None
        await conn.execute(
            "UPDATE users SET balance = balance - ?, username = ? WHERE user_id = ? AND chat_id = ?",
            (amount, username, user_id, chat_id),
        )
        await conn.execute(
            "INSERT OR REPLACE INTO bets VALUES (?, ?, ?)",
            (user_id, chat_id, new_total),
        )
        await conn.commit()
    return new_total


async def get_bet(user_id, chat_id):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "SELECT amount FROM bets WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id),
        )
        row = await cursor.fetchone()
        return row[0] if row else None


async def get_all_bets(chat_id):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            """
            SELECT users.user_id, users.username, bets.amount
            FROM bets
            JOIN users ON users.user_id = bets.user_id AND users.chat_id = bets.chat_id
            WHERE bets.chat_id = ?
            """,
            (chat_id,),
        )
        return await cursor.fetchall()


async def clear_bets(chat_id):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM bets WHERE chat_id = ?", (chat_id,))
        await db.commit()


async def get_leaderboard(chat_id):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            """
            SELECT username, balance
            FROM users
            WHERE chat_id = ?
            ORDER BY balance DESC
            LIMIT 10
            """,
            (chat_id,),
        )
        return await cursor.fetchall()


# Round message (for ready button)
async def set_round_message(chat_id, message_id):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR REPLACE INTO round_messages VALUES (?, ?)",
            (chat_id, message_id),
        )
        await db.commit()


async def get_round_message(chat_id):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "SELECT message_id FROM round_messages WHERE chat_id = ?",
            (chat_id,),
        )
        row = await cursor.fetchone()
        return row[0] if row else None


async def clear_round_message(chat_id):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM round_messages WHERE chat_id = ?", (chat_id,))
        await db.execute("DELETE FROM round_ready WHERE chat_id = ?", (chat_id,))
        await db.commit()


async def add_round_ready(chat_id, user_id):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR IGNORE INTO round_ready VALUES (?, ?)",
            (chat_id, user_id),
        )
        await db.commit()


async def get_round_ready_count(chat_id):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM round_ready WHERE chat_id = ?",
            (chat_id,),
        )
        row = await cursor.fetchone()
        return row[0] if row else 0


# Chests
async def get_last_chest_time(chat_id):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "SELECT last_spawn FROM chest_spawns WHERE chat_id = ?",
            (chat_id,),
        )
        row = await cursor.fetchone()
        return row[0] if row else 0


async def get_next_chest_time(chat_id):
    """Время, когда следующий сундук должен появиться."""
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "SELECT next_spawn FROM chest_spawns WHERE chat_id = ?",
            (chat_id,),
        )
        row = await cursor.fetchone()
        return row[0] if row and row[0] else None


async def set_chest_spawned(chat_id):
    import time

    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR REPLACE INTO chest_spawns VALUES (?, ?, ?)",
            (chat_id, time.time(), None),
        )
        await db.commit()


async def set_next_chest_time(chat_id, next_spawn: float):
    import time

    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "SELECT last_spawn FROM chest_spawns WHERE chat_id = ?",
            (chat_id,),
        )
        row = await cursor.fetchone()
        if row:
            await db.execute(
                "UPDATE chest_spawns SET next_spawn = ? WHERE chat_id = ?",
                (next_spawn, chat_id),
            )
        else:
            await db.execute(
                "INSERT OR REPLACE INTO chest_spawns VALUES (?, ?, ?)",
                (chat_id, 0, next_spawn),
            )
        await db.commit()


# Rob
async def get_last_rob_time(user_id, chat_id):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "SELECT last_rob FROM rob_cooldown WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id),
        )
        row = await cursor.fetchone()
        return row[0] if row else 0


async def set_rob_used(user_id, chat_id):
    import time

    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR REPLACE INTO rob_cooldown VALUES (?, ?, ?)",
            (user_id, chat_id, time.time()),
        )
        await db.commit()


async def get_chat_thread(chat_id: int):
    """Возвращает разрешённый thread_id для чата или None, если не задан."""
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "SELECT thread_id FROM chat_threads WHERE chat_id = ?",
            (chat_id,),
        )
        row = await cursor.fetchone()
        return row[0] if row else None


async def set_chat_admin(chat_id: int, admin_user_id: int):
    """Кто добавил бота в чат — админ казино для этого чата."""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR REPLACE INTO chat_admins(chat_id, admin_user_id) VALUES (?, ?)",
            (chat_id, admin_user_id),
        )
        await db.commit()


async def get_chat_admin(chat_id: int) -> Optional[int]:
    """user_id админа чата или None, если ещё не зафиксирован (бот не ловил my_chat_member)."""
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "SELECT admin_user_id FROM chat_admins WHERE chat_id = ?",
            (chat_id,),
        )
        row = await cursor.fetchone()
        return row[0] if row else None


async def user_is_any_chat_admin(user_id: int) -> bool:
    """True, если пользователь — зафиксированный админ хотя бы одного чата (для /settings в личке)."""
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "SELECT 1 FROM chat_admins WHERE admin_user_id = ? LIMIT 1",
            (user_id,),
        )
        row = await cursor.fetchone()
        return row is not None


async def set_chat_thread(chat_id: int, thread_id: int | None):
    """Сохраняет разрешённый thread_id для чата. Если thread_id is None или 0 — снимает ограничение."""
    async with aiosqlite.connect(DB_NAME) as db:
        if not thread_id:
            await db.execute("DELETE FROM chat_threads WHERE chat_id = ?", (chat_id,))
        else:
            await db.execute(
                "INSERT OR REPLACE INTO chat_threads(chat_id, thread_id) VALUES (?, ?)",
                (chat_id, thread_id),
            )
        await db.commit()


async def get_chat_ids_with_users():
    """Возвращает список chat_id, в которых есть пользователи."""
    async with aiosqlite.connect(DB_NAME) as db_conn:
        cursor = await db_conn.execute("SELECT DISTINCT chat_id FROM users")
        rows = await cursor.fetchall()
        return [r[0] for r in rows]


async def get_user_id_by_username(chat_id, username):
    """Возвращает user_id по username в чате (сравнение без учёта регистра и без @/пробелов)."""
    async with aiosqlite.connect(DB_NAME) as db_conn:
        uname = (username or "").lstrip("@").strip().lower()
        if not uname:
            return None
        cursor = await db_conn.execute(
            """SELECT user_id FROM users
               WHERE chat_id = ? AND LOWER(TRIM(REPLACE(COALESCE(username,''), '@', ''))) = ?""",
            (chat_id, uname),
        )
        row = await cursor.fetchone()
        return row[0] if row else None


# Update username
async def update_username(user_id, chat_id, username):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE users SET username = ? WHERE user_id = ? AND chat_id = ?",
            (username, user_id, chat_id),
        )
        await db.commit()


# --- Статистика пользователя ---


async def get_user_stats(user_id: int, chat_id: int) -> dict[str, Any]:
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "SELECT stats_json FROM user_stats WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id),
        )
        row = await cursor.fetchone()
    if not row or not row[0]:
        return default_user_stats()
    try:
        data = json.loads(row[0])
    except (json.JSONDecodeError, TypeError):
        return default_user_stats()
    base = default_user_stats()
    # Мягкое слияние с дефолтом (новые ключи)
    for k, v in base.items():
        if k not in data:
            data[k] = v
        elif isinstance(v, dict) and isinstance(data[k], dict):
            for sk, sv in v.items():
                data[k].setdefault(sk, sv)
    return data


async def _save_user_stats(user_id: int, chat_id: int, stats: dict[str, Any]) -> None:
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            """
            INSERT INTO user_stats(user_id, chat_id, stats_json)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, chat_id) DO UPDATE SET stats_json = excluded.stats_json
            """,
            (user_id, chat_id, json.dumps(stats, ensure_ascii=False)),
        )
        await db.commit()


async def stats_record_game(
    user_id: int,
    chat_id: int,
    game_key: str,
    *,
    net_won: int = 0,
    net_lost: int = 0,
    count_play: bool = True,
) -> None:
    if net_won < 0 or net_lost < 0:
        return
    stats = await get_user_stats(user_id, chat_id)
    g = GAME_LABELS_RU.get(game_key, game_key)
    if count_play:
        stats["plays"].setdefault(game_key, 0)
        stats["plays"][game_key] = stats["plays"].get(game_key, 0) + 1
    if net_won > 0:
        stats["won"].setdefault(game_key, 0)
        stats["won"][game_key] = stats["won"].get(game_key, 0) + net_won
        stats["totals"]["won"] += net_won
        if net_won > stats["max_win"]["amount"]:
            stats["max_win"] = {"amount": net_won, "game": g}
    if net_lost > 0:
        stats["lost"].setdefault(game_key, 0)
        stats["lost"][game_key] = stats["lost"].get(game_key, 0) + net_lost
        stats["totals"]["lost"] += net_lost
        if net_lost > stats["max_loss"]["amount"]:
            stats["max_loss"] = {"amount": net_lost, "game": g}
    await _save_user_stats(user_id, chat_id, stats)


async def stats_record_rob_attempt(user_id: int, chat_id: int) -> None:
    stats = await get_user_stats(user_id, chat_id)
    stats["rob"]["attempts"] += 1
    await _save_user_stats(user_id, chat_id, stats)


async def stats_record_rob_success_robber(user_id: int, chat_id: int, stolen: int) -> None:
    stats = await get_user_stats(user_id, chat_id)
    stats["rob"]["success"] += 1
    stats["rob"]["stolen_as_robber"] += max(0, stolen)
    await _save_user_stats(user_id, chat_id, stats)


async def stats_record_rob_fine(user_id: int, chat_id: int, fine: int) -> None:
    stats = await get_user_stats(user_id, chat_id)
    stats["rob"]["fines_paid"] += max(0, fine)
    await _save_user_stats(user_id, chat_id, stats)


async def stats_record_rob_victim(user_id: int, chat_id: int, stolen: int) -> None:
    stats = await get_user_stats(user_id, chat_id)
    stats["rob"]["stolen_from_victim"] += max(0, stolen)
    await _save_user_stats(user_id, chat_id, stats)


async def stats_record_chest(user_id: int, chat_id: int, reward: int) -> None:
    stats = await get_user_stats(user_id, chat_id)
    stats["chest"]["opened"] += 1
    stats["chest"]["mimriks"] += max(0, reward)
    await _save_user_stats(user_id, chat_id, stats)


async def stats_record_transfer_sent(user_id: int, chat_id: int, amount: int) -> None:
    stats = await get_user_stats(user_id, chat_id)
    stats["transfer"]["sent"] += max(0, amount)
    await _save_user_stats(user_id, chat_id, stats)


# --- Ежедневные задания ---


def today_str() -> str:
    return date.today().isoformat()


async def _get_daily_row(user_id: int, chat_id: int, day: str):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            """
            SELECT coinflip_count, rob_success, roulette_wins, mines_plays, slot_plays,
                   reward_coinflip, reward_rob, reward_roulette, reward_mines, reward_slot
            FROM daily_quest_state
            WHERE user_id = ? AND chat_id = ? AND day = ?
            """,
            (user_id, chat_id, day),
        )
        return await cursor.fetchone()


async def _ensure_daily_row(user_id: int, chat_id: int, day: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            """
            INSERT OR IGNORE INTO daily_quest_state(
                user_id, chat_id, day,
                coinflip_count, rob_success, roulette_wins, mines_plays, slot_plays,
                reward_coinflip, reward_rob, reward_roulette, reward_mines, reward_slot
            ) VALUES (?, ?, ?, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
            """,
            (user_id, chat_id, day),
        )
        await db.commit()


async def daily_quest_bump_coinflip(user_id: int, chat_id: int, reward_amount: int, target: int) -> int:
    """+1 к счётчику coinflip (ставка уже проверена). Возвращает начисленную награду (0 или reward_amount)."""
    day = today_str()
    await _ensure_daily_row(user_id, chat_id, day)
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE daily_quest_state SET coinflip_count = coinflip_count + 1 WHERE user_id = ? AND chat_id = ? AND day = ?",
            (user_id, chat_id, day),
        )
        await db.commit()
    row = await _get_daily_row(user_id, chat_id, day)
    if not row:
        return 0
    cnt, _, _, _, _, r_cf, _, _, _, _ = row
    if r_cf or cnt < target:
        return 0
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE daily_quest_state SET reward_coinflip = 1 WHERE user_id = ? AND chat_id = ? AND day = ?",
            (user_id, chat_id, day),
        )
        await db.commit()
    return reward_amount


async def daily_quest_bump_rob_success(user_id: int, chat_id: int, reward_amount: int, target: int) -> int:
    day = today_str()
    await _ensure_daily_row(user_id, chat_id, day)
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE daily_quest_state SET rob_success = rob_success + 1 WHERE user_id = ? AND chat_id = ? AND day = ?",
            (user_id, chat_id, day),
        )
        await db.commit()
    row = await _get_daily_row(user_id, chat_id, day)
    if not row:
        return 0
    _, rob_ok, _, _, _, _, r_rob, _, _, _ = row
    if r_rob or rob_ok < target:
        return 0
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE daily_quest_state SET reward_rob = 1 WHERE user_id = ? AND chat_id = ? AND day = ?",
            (user_id, chat_id, day),
        )
        await db.commit()
    return reward_amount


async def daily_quest_bump_roulette_win(user_id: int, chat_id: int, reward_amount: int, target: int) -> int:
    day = today_str()
    await _ensure_daily_row(user_id, chat_id, day)
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE daily_quest_state SET roulette_wins = roulette_wins + 1 WHERE user_id = ? AND chat_id = ? AND day = ?",
            (user_id, chat_id, day),
        )
        await db.commit()
    row = await _get_daily_row(user_id, chat_id, day)
    if not row:
        return 0
    _, _, rw, _, _, _, _, r_r, _, _ = row
    if r_r or rw < target:
        return 0
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE daily_quest_state SET reward_roulette = 1 WHERE user_id = ? AND chat_id = ? AND day = ?",
            (user_id, chat_id, day),
        )
        await db.commit()
    return reward_amount


async def daily_quest_bump_mines_play(user_id: int, chat_id: int, reward_amount: int, target: int) -> int:
    day = today_str()
    await _ensure_daily_row(user_id, chat_id, day)
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE daily_quest_state SET mines_plays = mines_plays + 1 WHERE user_id = ? AND chat_id = ? AND day = ?",
            (user_id, chat_id, day),
        )
        await db.commit()
    row = await _get_daily_row(user_id, chat_id, day)
    if not row:
        return 0
    mines_cnt = row[3]
    r_m = row[8]
    if r_m or mines_cnt < target:
        return 0
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE daily_quest_state SET reward_mines = 1 WHERE user_id = ? AND chat_id = ? AND day = ?",
            (user_id, chat_id, day),
        )
        await db.commit()
    return reward_amount


async def daily_quest_bump_slot_play(user_id: int, chat_id: int, reward_amount: int, target: int) -> int:
    """+1 к счётчику /slot (ставка уже проверена ≥ порога)."""
    day = today_str()
    await _ensure_daily_row(user_id, chat_id, day)
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE daily_quest_state SET slot_plays = slot_plays + 1 WHERE user_id = ? AND chat_id = ? AND day = ?",
            (user_id, chat_id, day),
        )
        await db.commit()
    row = await _get_daily_row(user_id, chat_id, day)
    if not row:
        return 0
    slot_cnt = row[4]
    r_slot = row[9]
    if r_slot or slot_cnt < target:
        return 0
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE daily_quest_state SET reward_slot = 1 WHERE user_id = ? AND chat_id = ? AND day = ?",
            (user_id, chat_id, day),
        )
        await db.commit()
    return reward_amount


async def get_daily_quest_snapshot(user_id: int, chat_id: int) -> dict[str, Any]:
    day = today_str()
    await _ensure_daily_row(user_id, chat_id, day)
    row = await _get_daily_row(user_id, chat_id, day)
    if not row:
        return {
            "day": day,
            "coinflip_count": 0,
            "rob_success": 0,
            "roulette_wins": 0,
            "mines_plays": 0,
            "slot_plays": 0,
            "reward_coinflip": 0,
            "reward_rob": 0,
            "reward_roulette": 0,
            "reward_mines": 0,
            "reward_slot": 0,
        }
    (
        coinflip_count,
        rob_success,
        roulette_wins,
        mines_plays,
        slot_plays,
        reward_coinflip,
        reward_rob,
        reward_roulette,
        reward_mines,
        reward_slot,
    ) = row
    return {
        "day": day,
        "coinflip_count": coinflip_count,
        "rob_success": rob_success,
        "roulette_wins": roulette_wins,
        "mines_plays": mines_plays,
        "slot_plays": slot_plays,
        "reward_coinflip": reward_coinflip,
        "reward_rob": reward_rob,
        "reward_roulette": reward_roulette,
        "reward_mines": reward_mines,
        "reward_slot": reward_slot,
    }
