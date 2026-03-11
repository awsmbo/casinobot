import asyncio
import random

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from config import BOT_TOKEN, ADMIN_ID
import db

bot = Bot(BOT_TOKEN)
dp = Dispatcher()


# старт
@dp.message(Command("start"))
async def start(message: types.Message):
    await message.reply(
        "🎰 Добро пожаловать в казино!\n\n"
        "Начните с /registration, чтобы получить 500 монет.\n"
        "Список команд: /help"
    )


# помощь
@dp.message(Command("help"))
async def help_cmd(message: types.Message):
    await message.reply(
        "📋 Команды:\n\n"
        "/registration — зарегистрироваться (500 монет)\n"
        "/balance — проверить баланс\n"
        "/bet <сумма> — сделать ставку (можно добавить к ставке во время раунда)\n"
        "/bank — текущий банк раунда\n"
        "/leaderboard — топ игроков"
    )


# регистрация
@dp.message(Command("registration"))
async def register(message: types.Message):
    chat_id = message.chat.id
    created = await db.register_user(
        message.from_user.id,
        chat_id,
        message.from_user.username or message.from_user.first_name,
    )

    if created:
        await message.reply("🎉 Регистрация успешна! Вы получили 500 монет.")
    else:
        await message.reply("Вы уже зарегистрированы.")


# баланс
@dp.message(Command("balance"))
async def balance(message: types.Message):
    bal = await db.get_balance(message.from_user.id, message.chat.id)

    if bal is None:
        await message.reply("Сначала зарегистрируйтесь /registration")
        return

    await message.reply(f"💰 Ваш баланс: {bal}")


# ставка
@dp.message(Command("bet"))
async def bet(message: types.Message):

    args = message.text.split()

    if len(args) != 2:
        await message.reply("Использование: /bet 100")
        return

    try:
        amount = int(args[1])
    except ValueError:
        await message.reply("Ставка должна быть числом.")
        return

    if amount < 1:
        await message.reply("Минимальная ставка — 1 монета.")
        return

    chat_id = message.chat.id
    user_id = message.from_user.id
    balance = await db.get_balance(user_id, chat_id)

    if balance is None:
        await message.reply("Сначала /registration")
        return

    if amount > balance:
        await message.reply("Недостаточно монет.")
        return

    current_bet = await db.get_bet(user_id, chat_id)
    is_add = current_bet is not None
    new_total = amount + (current_bet or 0)

    await db.change_balance(user_id, chat_id, -amount)
    await db.set_bet(user_id, chat_id, new_total)

    bets = await db.get_all_bets(chat_id)
    total = sum(b[2] for b in bets)

    if is_add:
        lines = ["🔄 Добавлено к ставке: {} монет. Итого ваша ставка: {}.\n".format(amount, new_total)]
    else:
        lines = ["🎰 Ставка принята! Ваша ставка: {} монет.\n".format(amount)]
    lines.append("📊 Текущие ставки:\n")
    for user_id, username, bet_amount in bets:
        pct = round(bet_amount / total * 100) if total else 0
        lines.append(f"• {username}: {bet_amount} ({pct}%)")
    lines.append(f"\n💰 Банк: {total}")

    await message.reply("\n".join(lines))


# банк
@dp.message(Command("bank"))
async def bank(message: types.Message):
    bets = await db.get_all_bets(message.chat.id)

    if not bets:
        await message.reply("Пока нет ставок.")
        return

    total = sum(b[2] for b in bets)
    lines = ["🎰 Текущий банк:\n"]
    for user_id, username, amount in bets:
        pct = round(amount / total * 100) if total else 0
        lines.append(f"• {username}: {amount} ({pct}%)")
    lines.append(f"\n💰 Всего: {total}")

    await message.reply("\n".join(lines))


# лидерборд
@dp.message(Command("leaderboard"))
async def leaderboard(message: types.Message):
    top = await db.get_leaderboard(message.chat.id)

    text = "🏆 Топ игроков:\n\n"

    for i, (username, balance) in enumerate(top, start=1):
        text += f"{i}. {username} — {balance}\n"

    await message.reply(text)


# админ начисление
@dp.message(Command("addcoins"))
async def addcoins(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    if not message.reply_to_message:
        await message.reply("Ответьте на сообщение пользователя.")
        return

    args = message.text.split()
    if len(args) != 2:
        await message.reply("Использование: ответьте на сообщение и введите /addcoins <количество>")
        return

    try:
        amount = int(args[1])
    except ValueError:
        await message.reply("Количество должно быть числом.")
        return

    user_id = message.reply_to_message.from_user.id
    chat_id = message.chat.id
    await db.change_balance(user_id, chat_id, amount)

    await message.reply("Монеты начислены.")


# запуск колеса
@dp.message(Command("spin"))
async def spin(message: types.Message):

    if message.from_user.id != ADMIN_ID:
        return

    chat_id = message.chat.id
    bets = await db.get_all_bets(chat_id)

    if not bets:
        await message.reply("Нет ставок.")
        return

    countdown_msg = await message.reply("🎰 Запуск колеса...\n\n3...")
    await asyncio.sleep(1)
    await countdown_msg.edit_text("🎰 Запуск колеса...\n\n2...")
    await asyncio.sleep(1)
    await countdown_msg.edit_text("🎰 Запуск колеса...\n\n1...")
    await asyncio.sleep(1)

    total_bank = sum(b[2] for b in bets)

    users = []
    weights = []

    for user_id, username, amount in bets:
        users.append((user_id, username))
        weights.append(amount)

    winner = random.choices(users, weights=weights, k=1)[0]

    winner_id, winner_name = winner

    await db.change_balance(winner_id, chat_id, total_bank)

    await message.reply(
        f"🏆 Победитель: {winner_name}\n"
        f"💰 Выигрыш: {total_bank}"
    )

    await db.clear_bets(chat_id)


async def main():
    await db.init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())