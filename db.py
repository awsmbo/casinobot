import os

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
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR REPLACE INTO bets VALUES (?, ?, ?)",
            (user_id, chat_id, amount),
        )
        await db.commit()


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
