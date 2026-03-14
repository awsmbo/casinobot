import os
from typing import Optional

import aiosqlite

DB_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(DB_DIR, "casino.db")


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


async def get_chat_ids_with_users():
    """Возвращает список chat_id, в которых есть пользователи."""
    async with aiosqlite.connect(DB_NAME) as db_conn:
        cursor = await db_conn.execute("SELECT DISTINCT chat_id FROM users")
        rows = await cursor.fetchall()
        return [r[0] for r in rows]


async def get_user_id_by_username(chat_id, username):
    """Возвращает user_id по username в чате."""
    async with aiosqlite.connect(DB_NAME) as db_conn:
        uname = (username or "").lstrip("@").lower()
        cursor = await db_conn.execute(
            "SELECT user_id FROM users WHERE chat_id = ? AND LOWER(REPLACE(COALESCE(username,''), '@', '')) = ?",
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
