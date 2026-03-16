import asyncio
import random
import time

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import BOT_TOKEN, ADMIN_ID
import db
from utils import mimriks
from settings import get_settings, get, set_value, set_multiple, DEFAULTS

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

# Состояние для раундов (chat_id -> message_id для редактирования)
round_messages_cache = {}

# Золотая минута: {chat_id: {"end": ts, "earnings": {user_id: amount}}}
golden_minute_active = {}


def _build_round_keyboard(chat_id: int, ready_count: int, total: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.add(
        InlineKeyboardButton(
            text=f"Запустить ({ready_count}/{total})",
            callback_data=f"ready:{chat_id}",
        )
    )
    return builder.as_markup()


def _format_bets_text(bets: list, total: int, prefix: str = "") -> str:
    lines = []
    for user_id, username, amount in bets:
        pct = round(amount / total * 100) if total else 0
        lines.append(f"• {username}: {amount} ({pct}%)")
    return "\n".join(lines) if lines else "—"


async def _update_or_send_round_message(chat_id: int, text: str, reply_to_message=None) -> int:
    """Обновляет или отправляет сообщение раунда. Возвращает message_id."""
    ready_count = await db.get_round_ready_count(chat_id)
    bets = await db.get_all_bets(chat_id)
    total_participants = len(bets)
    kb = _build_round_keyboard(chat_id, ready_count, total_participants)

    msg_id = await db.get_round_message(chat_id)
    if msg_id:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=text,
                reply_markup=kb,
            )
            return msg_id
        except Exception:
            # Убираем кнопку с предыдущего сообщения перед отправкой нового
            try:
                await bot.edit_message_reply_markup(
                    chat_id=chat_id,
                    message_id=msg_id,
                    reply_markup=None,
                )
            except Exception:
                pass
            await db.clear_round_message(chat_id)

    msg = await bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=kb,
        reply_to_message_id=reply_to_message.message_id if reply_to_message else None,
    )
    await db.set_round_message(chat_id, msg.message_id)
    return msg.message_id


GITHUB_URL = "https://github.com/awsmbo/casinobot"


def _private_instructions() -> str:
    return (
        "📖 Как использовать бота в чатах:\n\n"
        "1. Добавьте бота в групповой чат\n"
        "2. Напишите /registration — получите 500 стартовых мимриков\n"
        "3. Делайте ставки: /bet 100\n"
        "4. Когда все готовы — нажмите кнопку «Запустить» под сообщением со ставками\n"
        "5. Победитель определяется случайно (чем больше ставка — тем выше шанс)\n\n"
        "Другие команды: /transfer, /coinflip, /rob, /leaderboard\n"
        "Сундуки и золотая минута появляются случайно!\n\n"
        f"🔗 Исходный код: {GITHUB_URL}"
    )


# старт
@dp.message(Command("start"))
async def start(message: types.Message):
    if message.chat.type == "private":
        await message.reply(
            "🎰 Добро пожаловать в казино!\n\n"
            + _private_instructions()
        )
    else:
        await message.reply(
            "🎰 Добро пожаловать в казино!\n\n"
            "Начните с /registration, чтобы получить 500 мимриков.\n"
            "Список команд: /help"
        )


# помощь
@dp.message(Command("help"))
async def help_cmd(message: types.Message):
    await message.reply(
        "📋 Команды:\n\n"
        "/registration — зарегистрироваться (500 мимриков)\n"
        "/balance — проверить баланс\n"
        "/bet <сумма> — сделать ставку (можно добавить к ставке во время раунда)\n"
        "/bank — текущий банк раунда\n"
        "/transfer @user <сумма> — передать мимрики другому игроку\n"
        "/coinflip <сумма> — 50/50: проиграть или удвоить\n"
        "/rob @user — попытаться украсть мимрики (10% шанс, 1 раз в 30 мин)\n"
        "/leaderboard — топ игроков\n"
        "Сундуки и золотая минута появляются случайно в чате!"
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
        await message.reply("🎉 Регистрация успешна! Вы получили 500 мимриков.")
    else:
        await message.reply("Вы уже зарегистрированы.")


# баланс
@dp.message(Command("balance"))
async def balance(message: types.Message):
    bal = await db.get_balance(message.from_user.id, message.chat.id)

    if bal is None:
        await message.reply("Сначала зарегистрируйтесь /registration")
        return

    w = mimriks(bal)
    await message.reply(f"💰 Ваш баланс: {bal} {w}")


# ставка
@dp.message(Command("bet"))
async def bet(message: types.Message):
    if not message.from_user:
        return

    parts = (message.text or "").strip().split()
    if len(parts) < 2:
        await message.reply("Использование: /bet 100")
        return

    try:
        amount = int(parts[1])
    except ValueError:
        await message.reply("Ставка должна быть числом.")
        return

    if amount < 1:
        await message.reply("Минимальная ставка — 1 мимрик.")
        return

    chat_id = message.chat.id
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name or "User"

    if await db.get_balance(user_id, chat_id) is None:
        await message.reply("Сначала /registration")
        return

    current_bet = await db.get_bet(user_id, chat_id)
    new_total = await db.place_bet_atomic(user_id, chat_id, username, amount, current_bet)

    if new_total is None:
        await message.reply(f"Недостаточно {mimriks(amount)}.")
        return

    is_add = current_bet is not None
    bets = await db.get_all_bets(chat_id)
    total = sum(b[2] for b in bets)

    if is_add:
        head = f"🔄 Добавлено к ставке: {amount} {mimriks(amount)}. Итого ваша ставка: {new_total} {mimriks(new_total)}.\n\n"
    else:
        head = f"🎰 Ставка принята! Ваша ставка: {amount} {mimriks(amount)}.\n\n"

    text = head + "📊 Текущие ставки:\n" + _format_bets_text(bets, total) + f"\n\n💰 Банк: {total}"

    try:
        await _update_or_send_round_message(chat_id, text, reply_to_message=message)
    except Exception:
        await message.reply("🎰 Ставка принята! Используйте /bank для просмотра ставок.")


# банк
@dp.message(Command("bank"))
async def bank(message: types.Message):
    bets = await db.get_all_bets(message.chat.id)

    if not bets:
        await message.reply("Пока нет ставок.")
        return

    total = sum(b[2] for b in bets)
    lines = ["🎰 Текущий банк:\n", _format_bets_text(bets, total), f"\n💰 Всего: {total}"]
    await message.reply("\n".join(lines))


# лидерборд
@dp.message(Command("leaderboard"))
async def leaderboard(message: types.Message):
    top = await db.get_leaderboard(message.chat.id)

    text = "🏆 Топ игроков:\n\n"
    for i, (username, bal) in enumerate(top, start=1):
        text += f"{i}. {username} — {bal} {mimriks(bal)}\n"

    await message.reply(text)


# перевод
@dp.message(Command("transfer"))
async def transfer(message: types.Message):
    args = message.text.split()
    target_text = None
    amount_str = None

    if message.reply_to_message and message.reply_to_message.from_user:
        if len(args) >= 2:
            amount_str = args[1]
            target_id = message.reply_to_message.from_user.id
        else:
            await message.reply("Использование: реплай + /transfer <сумма>")
            return
    elif len(args) >= 3 and args[1].startswith("@"):
        target_text = args[1]
        amount_str = args[2]
        target_id = None
    else:
        await message.reply("Использование: /transfer @username <сумма> или реплай + /transfer <сумма>")
        return

    try:
        amount = int(amount_str)
    except (ValueError, TypeError):
        await message.reply("Сумма должна быть числом.")
        return

    if amount < 1:
        await message.reply("Минимальная сумма — 1 мимрик.")
        return

    chat_id = message.chat.id
    from_id = message.from_user.id

    from_bal = await db.get_balance(from_id, chat_id)
    if from_bal is None:
        await message.reply("Сначала /registration")
        return

    if amount > from_bal:
        await message.reply(f"Недостаточно {mimriks(amount)}.")
        return

    if target_id is None:
        target_username = target_text[1:].lower()
        if target_username == (message.from_user.username or "").lower():
            await message.reply("Нельзя перевести себе.")
            return
        target_id = await db.get_user_id_by_username(chat_id, target_username)

    if target_id is None:
        await message.reply("Пользователь не найден в этом чате.")
        return

    if target_id == from_id:
        await message.reply("Нельзя перевести себе.")
        return

    target_bal = await db.get_balance(target_id, chat_id)
    if target_bal is None:
        await message.reply("Получатель не зарегистрирован в этом чате.")
        return

    await db.change_balance(from_id, chat_id, -amount)
    await db.change_balance(target_id, chat_id, amount)

    w = mimriks(amount)
    target_name = target_text or (message.reply_to_message.from_user.username or message.reply_to_message.from_user.first_name)
    await message.reply(f"✅ Переведено {amount} {w} пользователю {target_name}")


# coinflip
@dp.message(Command("coinflip"))
async def coinflip(message: types.Message):
    args = message.text.split()
    if len(args) != 2:
        await message.reply("Использование: /coinflip <сумма>")
        return

    try:
        amount = int(args[1])
    except ValueError:
        await message.reply("Сумма должна быть числом.")
        return

    if amount < 1:
        await message.reply("Минимальная ставка — 1 мимрик.")
        return

    chat_id = message.chat.id
    user_id = message.from_user.id

    bal = await db.get_balance(user_id, chat_id)
    if bal is None:
        await message.reply("Сначала /registration")
        return

    if amount > bal:
        await message.reply(f"Недостаточно {mimriks(amount)}.")
        return

    await db.change_balance(user_id, chat_id, -amount)

    if random.random() < 0.5:
        await db.change_balance(user_id, chat_id, amount * 2)
        bal = await db.get_balance(user_id, chat_id)
        w = mimriks(amount * 2)
        await message.reply(f"🪙 Орёл! Вы выиграли {amount * 2} {w}!\n"
        f"Ваш баланс: {bal} {mimriks(bal)}")
    else:
        bal = await db.get_balance(user_id, chat_id)
        w = mimriks(amount)
        await message.reply(f"🪙 Решка. Вы проиграли {amount} {w}.\n"
        f"Ваш баланс: {bal} {mimriks(bal)}")


# rob
@dp.message(Command("rob"))
async def rob(message: types.Message):
    bal = await db.get_balance(message.from_user.id, message.chat.id)
    if bal is None:
        await message.reply("Сначала /registration")
        return
    
    chat_id = message.chat.id
    user_id = message.from_user.id

    rob_bal = await db.get_balance(user_id, chat_id)
    if rob_bal is None:
        await message.reply("Сначала /registration")
        return

    target_id = None
    if message.reply_to_message and message.reply_to_message.from_user:
        target_id = message.reply_to_message.from_user.id
    else:
        args = message.text.split()
        if len(args) >= 2 and args[1].startswith("@"):
            username = args[1][1:].lower()
            target_id = await db.get_user_id_by_username(chat_id, username)

    if target_id is None:
        await message.reply("Использование: /rob @username или реплай на сообщение")
        return

    if target_id == user_id:
        await message.reply("Нельзя обворовать себя.")
        return

    target_bal = await db.get_balance(target_id, chat_id)
    if target_bal is None:
        await message.reply("Цель не зарегистрирована.")
        return

    if target_bal < 10:
        await message.reply("У жертвы слишком мало мимриков для кражи.")
        return

    rob_cooldown = get("rob_cooldown", 1800)
    last_rob = await db.get_last_rob_time(user_id, chat_id)
    if time.time() - last_rob < rob_cooldown:
        left = int(rob_cooldown - (time.time() - last_rob))
        m = left // 60
        await message.reply(f"⏳ Кража возможна раз в {rob_cooldown // 60} минут. Подождите ещё {m} мин.")
        return

    await db.set_rob_used(user_id, chat_id)

    rob_success = get("rob_success_chance", 0.1)
    steal_pct = get("rob_steal_percent", 0.2)
    fine_pct = get("rob_fine_percent", 0.1)

    if random.random() > rob_success:
        # Провал — штраф
        fine_amount = max(1, int(bal * fine_pct))
        fine_amount = min(fine_amount, rob_bal)
        if fine_amount > 0:
            await db.change_balance(user_id, chat_id, -fine_amount)
            w = mimriks(fine_amount)
            await message.reply(f"🚔 Вас поймали! Вы потеряли {fine_amount} {w} (штраф за попытку кражи).")
        else:
            await message.reply("🚔 Вас поймали! Кража не удалась. (Штраф не взимается — недостаточно мимриков)")
        return

    steal_amount = max(1, int(target_bal * steal_pct))
    await db.change_balance(target_id, chat_id, -steal_amount)
    await db.change_balance(user_id, chat_id, steal_amount)

    w = mimriks(steal_amount)
    await message.reply(f"💰 Кража удалась! Вы украли {steal_amount} {w}!")


# админ: настройки (только в личке)
SETTINGS_KEYS = {
    "rob_cooldown": ("Кулдаун кражи (сек)", int, "Тайминги"),
    "chest_min_interval": ("Мин. интервал сундуков (сек)", int, "Тайминги"),
    "chest_max_interval": ("Макс. интервал сундуков (сек)", int, "Тайминги"),
    "golden_minute_duration": ("Длительность золотой минуты (сек)", int, "Тайминги"),
    "golden_minute_per_message": ("Мимриков за сообщение в зол. мин", int, "Тайминги"),
    "rob_success_chance": ("Шанс успеха кражи (0-1)", float, "Вероятности"),
    "golden_minute_chance": ("Шанс золотой минуты (0-1)", float, "Вероятности"),
    "rob_steal_percent": ("% кражи при успехе (0-1)", float, "Кража"),
    "rob_fine_percent": ("% штрафа при провале (0-1)", float, "Кража"),
}


@dp.message(Command("settings"))
async def settings_cmd(message: types.Message):
    if message.from_user.id != ADMIN_ID or message.chat.type != "private":
        return
    s = get_settings()
    lines = ["⚙️ Текущие настройки:\n"]
    for key, (desc, _, _) in SETTINGS_KEYS.items():
        val = s.get(key, DEFAULTS.get(key))
        if isinstance(val, list):
            val = str(val)
        lines.append(f"• {key}: {val} ({desc})")
    lines.append("\nИзменить: /set <ключ> <значение>")
    await message.reply("\n".join(lines))


@dp.message(Command("set"))
async def set_cmd(message: types.Message):
    if message.from_user.id != ADMIN_ID or message.chat.type != "private":
        return
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        await message.reply(
            "Использование: /set <ключ> <значение>\n"
            "Ключи: rob_cooldown, chest_min_interval, chest_max_interval, "
            "golden_minute_duration, golden_minute_per_message, rob_success_chance, "
            "golden_minute_chance, rob_steal_percent, rob_fine_percent"
        )
        return
    key = args[1].lower()
    if key not in SETTINGS_KEYS:
        await message.reply(f"Неизвестный ключ. Доступные: {', '.join(SETTINGS_KEYS)}")
        return
    _, type_fn, _ = SETTINGS_KEYS[key]
    try:
        val = type_fn(args[2])
    except (ValueError, TypeError):
        await message.reply(f"Неверный формат. Нужно: {type_fn.__name__}")
        return
    set_value(key, val)
    await message.reply(f"✅ {key} = {val}")


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

    w = mimriks(amount)
    await message.reply(f"Мимрики начислены: {amount} {w}")


# Callback: готовность к спинну
@dp.callback_query(F.data.startswith("ready:"))
async def ready_callback(callback: CallbackQuery):
    try:
        chat_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Ошибка")
        return

    if callback.message.chat.id != chat_id:
        await callback.answer("Неверный чат")
        return

    user_id = callback.from_user.id
    bets = await db.get_all_bets(chat_id)
    participants = {b[0] for b in bets}

    if user_id not in participants:
        await callback.answer("Вы не участвуете в этом раунде")
        return

    await db.add_round_ready(chat_id, user_id)
    ready_count = await db.get_round_ready_count(chat_id)
    total = len(bets)

    total_bank = sum(b[2] for b in bets)
    text = "📊 Текущие ставки:\n" + _format_bets_text(bets, total_bank) + f"\n\n💰 Банк: {total_bank}"
    kb = _build_round_keyboard(chat_id, ready_count, total)

    try:
        await callback.message.edit_text(text=text, reply_markup=kb)
    except Exception:
        pass

    await callback.answer(f"Готов! ({ready_count}/{total})")

    if ready_count >= total:
        await _do_spin(chat_id, callback.message)


async def _do_spin(chat_id: int, msg_to_edit: types.Message):
    bets = await db.get_all_bets(chat_id)
    if not bets:
        return

    countdown_msg = msg_to_edit
    await countdown_msg.edit_text("🎰 Запуск колеса...\n\n3...")
    await asyncio.sleep(1)
    await countdown_msg.edit_text("🎰 Запуск колеса...\n\n2...")
    await asyncio.sleep(1)
    await countdown_msg.edit_text("🎰 Запуск колеса...\n\n1...")
    await asyncio.sleep(1)

    total_bank = sum(b[2] for b in bets)
    users = [(b[0], b[1]) for b in bets]
    weights = [b[2] for b in bets]
    winner = random.choices(users, weights=weights, k=1)[0]
    winner_id, winner_name = winner

    await db.change_balance(winner_id, chat_id, total_bank)
    await db.clear_bets(chat_id)
    await db.clear_round_message(chat_id)

    w = mimriks(total_bank)
    await countdown_msg.edit_text(
        f"🎰 Запуск колеса...\n\n"
        f"🏆 Победитель: {winner_name}\n"
        f"💰 Выигрыш: {total_bank} {w}"
    )


# запуск колеса (админ)
@dp.message(Command("spin"))
async def spin(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    chat_id = message.chat.id
    bets = await db.get_all_bets(chat_id)

    if not bets:
        await message.reply("Нет ставок.")
        return

    msg = await message.reply("🎰 Запуск колеса...\n\n3...")
    await asyncio.sleep(1)
    await msg.edit_text("🎰 Запуск колеса...\n\n2...")
    await asyncio.sleep(1)
    await msg.edit_text("🎰 Запуск колеса...\n\n1...")
    await asyncio.sleep(1)

    total_bank = sum(b[2] for b in bets)
    users = [(b[0], b[1]) for b in bets]
    weights = [b[2] for b in bets]
    winner = random.choices(users, weights=weights, k=1)[0]
    winner_id, winner_name = winner

    await db.change_balance(winner_id, chat_id, total_bank)
    await db.clear_bets(chat_id)
    await db.clear_round_message(chat_id)

    w = mimriks(total_bank)
    await msg.edit_text(
        f"🎰 Запуск колеса...\n\n"
        f"🏆 Победитель: {winner_name}\n"
        f"💰 Выигрыш: {total_bank} {w}"
    )


# Сундук
chest_available = {}  # chat_id -> message_id (если сундук есть и не взят)


def _roll_chest_reward():
    rewards = get("chest_rewards", DEFAULTS["chest_rewards"])
    r = random.random() * 100
    cumulative = 0
    for amount, chance in rewards:
        cumulative += chance
        if r < cumulative:
            return amount
    return rewards[0][0] if rewards else 500


def _is_night() -> bool:
    """Ночь: 01:00–07:00 по локальному времени сервера."""
    from datetime import datetime
    h = datetime.now().hour
    return 1 <= h < 7


@dp.message(Command("chest"))
async def chest_grab(message: types.Message):
    chat_id = message.chat.id
    user_id = message.from_user.id

    if chat_id not in chest_available:
        await message.reply("📦 Сундука нет. Он появляется случайно в чате!")
        return

    if await db.get_balance(user_id, chat_id) is None:
        await message.reply("Сначала /registration")
        return

    reward = _roll_chest_reward()
    await db.change_balance(user_id, chat_id, reward)

    del chest_available[chat_id]
    # Планируем следующий сундук через 1–20 минут
    min_i = get("chest_min_interval", 60)
    max_i = get("chest_max_interval", 1200)
    next_spawn = time.time() + random.randint(min_i, max_i)
    await db.set_next_chest_time(chat_id, next_spawn)

    w = mimriks(reward)
    await message.reply(f"🎁 Вы забрали сундук! +{reward} {w}!")


@dp.callback_query(F.data == "chest_grab")
async def chest_callback(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    user_id = callback.from_user.id

    if chat_id not in chest_available or chest_available[chat_id] != callback.message.message_id:
        await callback.answer("Сундук уже забран!")
        return

    if await db.get_balance(user_id, chat_id) is None:
        await callback.answer("Сначала зарегистрируйтесь", show_alert=True)
        return

    reward = _roll_chest_reward()
    await db.change_balance(user_id, chat_id, reward)

    del chest_available[chat_id]
    min_i = get("chest_min_interval", 60)
    max_i = get("chest_max_interval", 1200)
    next_spawn = time.time() + random.randint(min_i, max_i)
    await db.set_next_chest_time(chat_id, next_spawn)

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.edit_text(
            f"📦 Сундук забрал {callback.from_user.username or callback.from_user.first_name}!\n"
            f"🎁 +{reward} {mimriks(reward)}"
        )
    except Exception:
        pass

    await callback.answer(f"Получено {reward} {mimriks(reward)}!")


async def chest_spawn_task():
    while True:
        await asyncio.sleep(30)  # проверка каждые 30 секунд
        try:
            chat_ids = await db.get_chat_ids_with_users()
        except Exception:
            continue

        now = time.time()
        if _is_night():
            continue

        for chat_id in chat_ids:
            if chat_id in chest_available:
                continue
            next_spawn = await db.get_next_chest_time(chat_id)
            if next_spawn is None:
                # Первый сундук — планируем через 1–20 мин
                min_i = get("chest_min_interval", 60)
                max_i = get("chest_max_interval", 1200)
                next_spawn = now + random.randint(min_i, max_i)
                await db.set_next_chest_time(chat_id, next_spawn)
                continue
            if now < next_spawn:
                continue
            kb = InlineKeyboardBuilder()
            kb.add(InlineKeyboardButton(text="Забрать", callback_data="chest_grab"))
            try:
                msg = await bot.send_message(
                    chat_id,
                    "📦 В чате появился сундук!",
                    reply_markup=kb.as_markup(),
                )
                chest_available[chat_id] = msg.message_id
            except Exception:
                pass


# Золотая минута
async def golden_minute_task():
    while True:
        await asyncio.sleep(600)  # проверка каждые 10 минут
        try:
            chat_ids = await db.get_chat_ids_with_users()
        except Exception:
            continue

        for chat_id in chat_ids:
            if chat_id in golden_minute_active:
                continue
            if random.random() > get("golden_minute_chance", 0.15):
                continue

            duration = get("golden_minute_duration", 60)
            golden_minute_active[chat_id] = {
                "end": time.time() + duration,
                "earnings": {},  # user_id -> (amount, username)
            }
            try:
                await bot.send_message(chat_id, "🌟 В чате началась золотая минута! Пишите сообщения — получайте мимрики!")
            except Exception:
                golden_minute_active.pop(chat_id, None)


# Личка: на любое сообщение — инструкция
@dp.message(F.chat.type == "private", F.text, ~F.text.startswith("/"))
async def private_any_message(message: types.Message):
    await message.reply(_private_instructions())


@dp.message(F.text)
async def on_any_message(message: types.Message):
    chat_id = message.chat.id
    if chat_id not in golden_minute_active:
        return

    state = golden_minute_active[chat_id]
    if time.time() > state["end"]:
        earnings = state.get("earnings", {})  # user_id -> (amount, username)
        lines = ["🌟 Золотая минута окончена!\n\nСводка:"]
        items = [(amt, name) for (amt, name) in earnings.values() if amt > 0]
        items.sort(key=lambda x: -x[0])
        for amt, name in items:
            lines.append(f"• {name}: +{amt} {mimriks(amt)}")
        if len(lines) == 1:
            lines.append("Никто не заработал.")
        try:
            await message.reply("\n".join(lines))
        except Exception:
            pass
        golden_minute_active.pop(chat_id, None)
        return

    user_id = message.from_user.id
    if await db.get_balance(user_id, chat_id) is None:
        return

    per_msg = get("golden_minute_per_message", 10)
    prev = state["earnings"].get(user_id, (0, ""))
    amt = prev[0] + per_msg
    name = message.from_user.username or message.from_user.first_name or "?"
    state["earnings"][user_id] = (amt, name)
    await db.change_balance(user_id, chat_id, per_msg)


BOT_COMMANDS = [
    BotCommand(command="start", description="Приветствие"),
    BotCommand(command="help", description="Список команд"),
    BotCommand(command="registration", description="Регистрация (500 мимриков)"),
    BotCommand(command="balance", description="Баланс"),
    BotCommand(command="bet", description="Сделать ставку"),
    BotCommand(command="bank", description="Текущий банк раунда"),
    BotCommand(command="transfer", description="Перевести мимрики"),
    BotCommand(command="coinflip", description="50/50: удвоить или проиграть"),
    BotCommand(command="rob", description="Попытаться украсть мимрики"),
    BotCommand(command="chest", description="Забрать сундук"),
    BotCommand(command="leaderboard", description="Топ игроков"),
    BotCommand(command="addcoins", description="(админ) Начислить мимрики"),
    BotCommand(command="spin", description="(админ) Запустить рулетку"),
]


async def main():
    await db.init_db()
    await bot.set_my_commands(BOT_COMMANDS)
    asyncio.create_task(chest_spawn_task())
    asyncio.create_task(golden_minute_task())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
