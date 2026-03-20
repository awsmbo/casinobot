import asyncio
import random
import time

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import (
    BotCommand,
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    CallbackQuery,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import BOT_TOKEN
import db
from utils import mimriks
from settings import get_settings, get, set_value, set_multiple, DEFAULTS

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

# Состояние для раундов (chat_id -> message_id для редактирования)
round_messages_cache = {}

# Rocket: (chat_id, user_id) -> {"amount": int, "multiplier": float, "active": bool, "message_id": int}
rocket_games = {}

# Roulette: chat_id -> {"active": bool, "end_ts": float, "message_id": int, "bets": [...]}
roulette_rounds = {}

# Сапёр: (chat_id, user_id) -> состояние игры
mines_games = {}

# 5 коэффициентов по порядку открытия (каждый больше предыдущего), произведение ≈ ×50.5
MINES_MULTS = [1.2, 1.45, 1.8, 2.6, 6.2]
MINES_SAFE = 5
MINES_GRID = 16


@dp.my_chat_member()
async def on_bot_chat_member(event: ChatMemberUpdated):
    """Запоминаем пользователя, который добавил бота в группу / вернул бота в чат."""
    if event.chat.type not in ("group", "supergroup"):
        return
    old = event.old_chat_member.status
    new = event.new_chat_member.status
    # Впервые в чате или после left/kicked
    if old in ("left", "kicked") and new in ("member", "administrator", "restricted"):
        u = event.from_user
        if u is not None and not u.is_bot:
            await db.set_chat_admin(event.chat.id, u.id)


async def _thread_allowed(message: types.Message) -> bool:
    """Возвращает True, если сообщение можно обрабатывать в этом подчате.

    Логика:
    - Для лички и обычных чатов без тем — всегда True.
    - Для суперчатов с темами: если для chat_id не задан thread_id — True.
    - Если задан — только когда message_thread_id совпадает.
    """
    if message.chat.type == "private":
        return True

    # В обычных группах без тем message_thread_id обычно None
    if message.chat.type in {"group", "supergroup"}:
        allowed_thread = await db.get_chat_thread(message.chat.id)
        if not allowed_thread:
            return True
        return (getattr(message, "message_thread_id", None) or 0) == allowed_thread

    return True


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
    """Отправляет новое сообщение раунда и снимает кнопки со старого. Возвращает message_id нового сообщения."""
    ready_count = await db.get_round_ready_count(chat_id)
    bets = await db.get_all_bets(chat_id)
    total_participants = len(bets)
    kb = _build_round_keyboard(chat_id, ready_count, total_participants)

    msg_id = await db.get_round_message(chat_id)
    # Если есть предыдущее сообщение раунда — убираем у него кнопки
    if msg_id:
        try:
            await bot.edit_message_reply_markup(
                chat_id=chat_id,
                message_id=msg_id,
                reply_markup=None,
            )
        except Exception:
            # Если не смогли снять клавиатуру — просто продолжаем, не падаем
            pass

    # Если для чата задан обязательный thread_id — всегда отвечаем туда
    message_thread_id = None
    if reply_to_message and reply_to_message.chat.type in {"group", "supergroup"}:
        allowed_thread = await db.get_chat_thread(chat_id)
        if allowed_thread:
            message_thread_id = allowed_thread
        else:
            message_thread_id = getattr(reply_to_message, "message_thread_id", None)

    msg = await bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=kb,
        reply_to_message_id=reply_to_message.message_id if reply_to_message else None,
        message_thread_id=message_thread_id,
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
        "Другие команды: /transfer, /coinflip, /mines, /rob, /leaderboard\n"
        "Сундуки появляются случайно!\n\n"
        f"🔗 Исходный код: {GITHUB_URL}"
    )


# старт
@dp.message(Command("start"))
async def start(message: types.Message):
    if not await _thread_allowed(message):
        return
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
    if not await _thread_allowed(message):
        return
    await message.reply(
        "📋 Команды:\n\n"
        "/registration — зарегистрироваться (500 мимриков)\n"
        "/balance — проверить баланс\n"
        "/bet <сумма> — сделать ставку (можно добавить к ставке во время раунда)\n"
        "/bank — текущий банк раунда\n"
        "/transfer @user <сумма> — передать мимрики другому игроку\n"
        "/coinflip <сумма> — 50/50: проиграть или удвоить\n"
        "/mines <сумма> — сапёр: 4×4, множители или бомба\n"
        "/rocket <сумма> — игра с растущим множителем и риском взрыва\n"
        "/rob @user — попытаться украсть мимрики (10% шанс, 1 раз в 30 мин)\n"
        "/leaderboard — топ игроков\n"
        "Сундуки появляются случайно в чате!"
    )


# регистрация
@dp.message(Command("registration"))
async def register(message: types.Message):
    if not await _thread_allowed(message):
        return
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
    if not await _thread_allowed(message):
        return
    bal = await db.get_balance(message.from_user.id, message.chat.id)

    if bal is None:
        await message.reply("Сначала зарегистрируйтесь /registration")
        return

    w = mimriks(bal)
    await message.reply(f"💰 Ваш баланс: {bal} {w}")


# ставка
@dp.message(Command("bet"))
async def bet(message: types.Message):
    if not await _thread_allowed(message):
        return
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
    if not await _thread_allowed(message):
        return
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
    if not await _thread_allowed(message):
        return
    top = await db.get_leaderboard(message.chat.id)

    text = "🏆 Топ игроков:\n\n"
    for i, (username, bal) in enumerate(top, start=1):
        text += f"{i}. {username} — {bal} {mimriks(bal)}\n"

    await message.reply(text)


# перевод
@dp.message(Command("transfer"))
async def transfer(message: types.Message):
    if not await _thread_allowed(message):
        return
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
    if not await _thread_allowed(message):
        return
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


def _mines_game_text(state: dict, *, over: bool = False, win: bool = False) -> str:
    amount = state["amount"]
    product = state["product"]
    safe_opened = state["safe_opened"]
    win_amt = int(amount * product)

    if over and not win:
        return (
            "💥 Бомба! Ставка проиграна.\n\n"
            f"Ставка: {amount} {mimriks(amount)}\n"
            f"До взрыва открыто множителей: {safe_opened}/{MINES_SAFE}\n"
            f"Множитель был: ×{product:.2f}"
        )

    if over and win:
        return (
            "🎉 Поздравляем!\n\n"
            f"Вы забрали {win_amt} {mimriks(win_amt)}.\n"
            f"Итоговый множитель: ×{product:.2f} ({safe_opened} из {MINES_SAFE})"
        )

    lines = [
        "💣 Сапёр",
        "",
        f"Ставка: {amount} {mimriks(amount)}",
        f"Текущий множитель: ×{product:.2f}",
        f"Можно забрать сейчас: {win_amt} {mimriks(win_amt)}",
        "",
        f"Открыто множителей: {safe_opened}/{MINES_SAFE}",
    ]
    if safe_opened == 0:
        lines.append("Откройте клетку. После первого множителя появится кнопка «Забрать приз».")
    else:
        lines.append("Дальше — только клетки с «▢» или заберите приз.")
    return "\n".join(lines)


def _mines_build_keyboard(state: dict) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    safe_cells = state["safe_cells"]
    revealed = state["revealed"]
    revealed_mults = state["revealed_mults"]
    active = state["active"]

    for row in range(4):
        row_btns = []
        for col in range(4):
            idx = row * 4 + col
            if idx in revealed:
                if idx in revealed_mults:
                    m = revealed_mults[idx]
                    label = f"×{m:.2f}"
                else:
                    label = "💥"
                row_btns.append(
                    InlineKeyboardButton(text=label, callback_data="ms_n")
                )
            else:
                row_btns.append(
                    InlineKeyboardButton(text="▢", callback_data=f"ms_o_{idx}")
                )
        builder.row(*row_btns)

    if active and state["safe_opened"] >= 1:
        builder.row(
            InlineKeyboardButton(text="💰 Забрать приз", callback_data="ms_c"),
        )

    return builder.as_markup()


async def _mines_finish(
    chat_id: int,
    message_id: int,
    key: tuple,
    state: dict,
    *,
    win: bool,
):
    state["active"] = False
    mines_games.pop(key, None)
    text = _mines_game_text(state, over=True, win=win)
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=None,
        )
    except Exception:
        pass


@dp.message(Command("mines"))
async def mines_cmd(message: types.Message):
    """Игра «Сапёр»: 4×4 клеток, 5 множителей, 11 бомб. /mines <ставка>"""
    if not await _thread_allowed(message):
        return
    args = (message.text or "").split()
    if len(args) != 2:
        await message.reply("Использование: /mines <ставка>")
        return
    try:
        amount = int(args[1])
    except ValueError:
        await message.reply("Ставка должна быть числом.")
        return
    if amount < 1:
        await message.reply("Минимальная ставка — 1 мимрик.")
        return

    chat_id = message.chat.id
    user_id = message.from_user.id
    key = (chat_id, user_id)

    if key in mines_games and mines_games[key].get("active"):
        await message.reply("У вас уже идёт игра в сапёра. Сначала закончите её.")
        return

    bal = await db.get_balance(user_id, chat_id)
    if bal is None:
        await message.reply("Сначала /registration")
        return
    if amount > bal:
        await message.reply(f"Недостаточно {mimriks(amount)}.")
        return

    await db.change_balance(user_id, chat_id, -amount)

    safe_cells = set(random.sample(range(MINES_GRID), MINES_SAFE))
    state = {
        "amount": amount,
        "owner_id": user_id,
        "safe_cells": safe_cells,
        "revealed": set(),
        "revealed_mults": {},
        "safe_opened": 0,
        "product": 1.0,
        "active": True,
        "message_id": 0,
    }

    text = _mines_game_text(state)
    kb = _mines_build_keyboard(state)
    msg = await message.reply(text, reply_markup=kb)
    state["message_id"] = msg.message_id
    mines_games[key] = state


@dp.callback_query(F.data.startswith("ms_"))
async def mines_callback(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    user_id = callback.from_user.id
    key = (chat_id, user_id)
    mid = callback.message.message_id

    state = mines_games.get(key)
    if not state or not state.get("active"):
        # Сообщение сапёра могло быть от другого игрока
        for (c, owner_id), st in mines_games.items():
            if c == chat_id and st.get("message_id") == mid and st.get("active"):
                if owner_id != user_id:
                    await callback.answer("Это игра другого игрока.", show_alert=True)
                    return
                break
        await callback.answer("Игра не найдена или уже завершена.", show_alert=True)
        return

    if mid != state["message_id"]:
        await callback.answer("Устаревшее сообщение.", show_alert=True)
        return

    if state.get("owner_id", user_id) != user_id:
        await callback.answer("Это не ваша игра.", show_alert=True)
        return

    data = callback.data or ""

    if data == "ms_n":
        await callback.answer()
        return

    if data == "ms_c":
        if state["safe_opened"] < 1:
            await callback.answer("Сначала откройте хотя бы одну клетку с множителем.", show_alert=True)
            return
        win = int(state["amount"] * state["product"])
        await db.change_balance(user_id, chat_id, win)
        await _mines_finish(chat_id, state["message_id"], key, state, win=True)
        await callback.answer(f"Забрали {win} {mimriks(win)}!")
        return

    if not data.startswith("ms_o_"):
        await callback.answer()
        return

    try:
        idx = int(data[5:])  # после "ms_o_"
    except ValueError:
        await callback.answer()
        return

    if idx < 0 or idx >= MINES_GRID or idx in state["revealed"]:
        await callback.answer("Клетка уже открыта.")
        return

    state["revealed"].add(idx)

    if idx not in state["safe_cells"]:
        # Бомба
        await _mines_finish(chat_id, state["message_id"], key, state, win=False)
        await callback.answer("💥 Бомба!", show_alert=True)
        return

    # Множитель по порядку открытия безопасных клеток
    mult = MINES_MULTS[state["safe_opened"]]
    state["revealed_mults"][idx] = mult
    state["safe_opened"] += 1
    state["product"] *= mult

    if state["safe_opened"] >= MINES_SAFE:
        # Все 5 множителей — автоматический максимальный выигрыш
        win = int(state["amount"] * state["product"])
        await db.change_balance(user_id, chat_id, win)
        await _mines_finish(chat_id, state["message_id"], key, state, win=True)
        await callback.answer(f"Джекпот! +{win} {mimriks(win)}!")
        return

    text = _mines_game_text(state)
    kb = _mines_build_keyboard(state)
    try:
        await callback.message.edit_text(text=text, reply_markup=kb)
    except Exception:
        pass
    await callback.answer(f"×{mult:.2f}")


async def _roulette_round_runner(chat_id: int):
    """Запускает таймер раунда рулетки, принимает ставки 60 секунд и затем крутит рулетку."""
    state = roulette_rounds.get(chat_id)
    if not state or not state.get("active"):
        return

    end_ts = state["end_ts"]
    thread_id = state.get("thread_id")
    # Ждём до конца приёма ставок
    while True:
        now = time.time()
        if now >= end_ts:
            break
        # Обновляем сообщение раз в несколько секунд (без агрессивного спама)
        left = int(end_ts - now)
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=state["message_id"],
                text=(
                    "🎰 Рулетка запущена!\n"
                    f"До конца приёма ставок: ~{left} сек.\n\n"
                    "Делайте ставку командой:\n"
                    "/roulette <ставка> <цвет/число>\n"
                    "Примеры: /roulette 10 red, /roulette 25 17, /roulette 50 green"
                ),
            )
        except Exception:
            # Если сообщение удалить или нельзя обновить — просто продолжаем таймер
            pass

        # Обновляем не чаще раза в 5 секунд
        await asyncio.sleep(5)

    # Фиксируем состояние ещё раз
    state = roulette_rounds.get(chat_id)
    if not state or not state.get("active"):
        return

    state["active"] = False
    bets = state.get("bets") or []

    # Если ставок нет — просто сообщаем и выходим
    if not bets:
        try:
            await bot.send_message(
                chat_id,
                "🎰 Рулетка завершена.\nНи одной ставки не было сделано.",
                message_thread_id=thread_id,
            )
        except Exception:
            pass
        return

    # Крутим рулетку
    spin = random.randint(0, 36)
    if spin == 0:
        spin_color = "green"
    elif spin % 2 == 0:
        spin_color = "black"
    else:
        spin_color = "red"

    color_map = {
        "red": "🔴 красное",
        "black": "⚫ чёрное",
        "green": "🟢 зелёное (0)",
    }
    spin_text = color_map.get(spin_color, spin_color)

    # Обрабатываем ставки
    results_lines = [
        f"🎰 Рулетка завершена!\n"
        f"Выпало: {spin} ({spin_text})\n"
        f"Результаты ставок:\n"
    ]

    # Для краткости собираем по пользователю суммарный выигрыш
    user_wins: dict[int, int] = {}

    for bet in bets:
        uid = bet["user_id"]
        amount = bet["amount"]
        bet_type = bet["bet_type"]
        bet_color = bet["bet_color"]
        bet_number = bet["bet_number"]

        win = 0
        reason = ""

        if bet_type == "color":
            if bet_color == spin_color:
                if bet_color in {"red", "black"}:
                    win = amount * 2
                else:  # green (0)
                    win = amount * 50
                reason = "угадал цвет"
            else:
                win = 0
                reason = "не угадал цвет"
        else:  # number
            if bet_number == spin:
                # Число 0 оплачивается повышенным коэффициентом 50x
                if bet_number == 0:
                    win = amount * 50
                else:
                    win = amount * 36
                reason = "угадал число"
            else:
                win = 0
                reason = "не угадал число"

        if win > 0:
            await db.change_balance(uid, chat_id, win)
            user_wins[uid] = user_wins.get(uid, 0) + win

        # Получаем отображаемое имя пользователя
        try:
            member = await bot.get_chat_member(chat_id, uid)
            user = member.user
            if user.username:
                user_name = f"@{user.username}"
            else:
                user_name = user.full_name
        except Exception:
            user_name = f"id {uid}"

        # Формируем описание бета
        if bet_type == "color":
            bet_desc = color_map.get(bet_color, bet_color)
        else:
            bet_desc = f"число {bet_number}"

        results_lines.append(
            f"- {user_name}: ставка {amount} на {bet_desc} — {reason}"
            + (f", выигрыш {win}" if win > 0 else "")
        )

    # Отправляем сводку в чат отдельным сообщением
    try:
        await bot.send_message(
            chat_id,
            "\n".join(results_lines),
            message_thread_id=thread_id,
        )
    except Exception:
        pass

@dp.message(Command("roulette"))
async def roulette(message: types.Message):
    """
    Многопользовательская рулетка с минутой на ставки.
    Форматы:
      /roulette                — запустить раунд рулетки в чате (если ещё не идёт)
      /roulette <ставка> <бет> — сделать ставку в текущем раунде

    Где <бет>:
      - red / красное          — ставка на красное (2x)
      - black / чёрное         — ставка на чёрное (2x)
      - green / зелёное / 0    — ставка на зелёное (0) (50x)
      - число 1–36             — ставка на конкретное число (36x)
      - число 0                — ставка на ноль (50x)
    """
    if not await _thread_allowed(message):
        return

    args = (message.text or "").split()
    chat_id = message.chat.id
    user_id = message.from_user.id

    # Глобальное состояние рулетки (по чату)
    global roulette_rounds

    # 1) Старт раунда: /roulette
    if len(args) == 1:
        state = roulette_rounds.get(chat_id)
        now = time.time()
        if state and state.get("active") and state.get("end_ts", 0) > now:
            left = int(state["end_ts"] - now)
            await message.reply(f"🎰 Рулетка уже запущена! До конца приёма ставок ~{left} сек.\n"
                               f"Ставка: /roulette <сумма> <цвет/число>")
            return

        end_ts = now + 60
        thread_id = getattr(message, "message_thread_id", None)
        text = (
            "🎰 Рулетка запущена!\n"
            "У вас есть 60 секунд, чтобы сделать ставки.\n\n"
            "Делайте ставку командой:\n"
            "/roulette <ставка> <цвет/число>\n"
            "Примеры: /roulette 10 red, /roulette 25 17, /roulette 50 green"
        )
        msg = await message.reply(text)

        roulette_rounds[chat_id] = {
            "active": True,
            "end_ts": end_ts,
            "message_id": msg.message_id,
            "thread_id": thread_id,
            "bets": [],  # список словарей: {user_id, amount, bet_type, bet_color, bet_number}
        }

        asyncio.create_task(_roulette_round_runner(chat_id))
        return

    # 2) Ставка: /roulette <ставка> <бет>
    if len(args) != 3:
        await message.reply(
            "Использование:\n"
            "/roulette — запустить раунд\n"
            "/roulette <ставка> <цвет/число> — сделать ставку в текущем раунде"
        )
        return

    state = roulette_rounds.get(chat_id)
    now = time.time()
    if not state or not state.get("active") or state.get("end_ts", 0) <= now:
        await message.reply("Сейчас нет активного раунда рулетки. Сначала запустите /roulette.")
        return

    try:
        amount = int(args[1])
    except ValueError:
        await message.reply("Ставка должна быть числом.")
        return

    if amount < 1:
        await message.reply("Минимальная ставка — 1 мимрик.")
        return

    bet_raw = args[2].strip().lower()

    bal = await db.get_balance(user_id, chat_id)
    if bal is None:
        await message.reply("Сначала /registration")
        return

    if amount > bal:
        await message.reply(f"Недостаточно {mimriks(amount)}.")
        return

    # Списываем ставку
    await db.change_balance(user_id, chat_id, -amount)

    # Определяем тип ставки
    bet_type = None  # 'color' или 'number'
    bet_color = None  # 'red' / 'black' / 'green'
    bet_number = None

    # Маппинг для цветов
    if bet_raw in {"red", "красное", "красный"}:
        bet_type = "color"
        bet_color = "red"
    elif bet_raw in {"black", "чёрное", "черное", "чёрный", "черный"}:
        bet_type = "color"
        bet_color = "black"
    elif bet_raw in {"green", "зелёное", "зеленое", "зелёный", "зеленый", "0"}:
        bet_type = "color"
        bet_color = "green"
    else:
        # Пытаемся интерпретировать как число 0–36
        try:
            n = int(bet_raw)
        except ValueError:
            await message.reply("Неверный тип ставки. Укажите цвет (red/black/green) или число 0–36.")
            # Возвращаем ставку
            await db.change_balance(user_id, chat_id, amount)
            return

        if not (0 <= n <= 36):
            await message.reply("Число должно быть от 0 до 36.")
            await db.change_balance(user_id, chat_id, amount)
            return

        bet_type = "number"
        bet_number = n

    # Сохраняем ставку в состояние раунда
    state["bets"].append(
        {
            "user_id": user_id,
            "amount": amount,
            "bet_type": bet_type,
            "bet_color": bet_color,
            "bet_number": bet_number,
        }
    )

    bet_desc: str
    color_map = {
        "red": "🔴 красное",
        "black": "⚫ чёрное",
        "green": "🟢 зелёное (0)",
    }
    if bet_type == "color":
        bet_desc = color_map.get(bet_color, bet_color)
    else:
        bet_desc = f"число {bet_number}"

    await message.reply(
        f"✅ Ваша ставка принята!\n"
        f"Ставка: {amount} {mimriks(amount)} на {bet_desc}.\n"
        f"Результат будет после окончания раунда."
    )

# --- Ракета ---
# Краш-мультипликатор: r ~ U(0,1), crash = min(10, 1/(1-r)) — тяжёлый хвост.
# Длительность полёта T ~ U[T_MIN, T_MAX] задаётся НЕЗАВИСИМО от crash_point,
# чтобы по «скорости» роста нельзя было угадать высоту краша.
# Отображение: mult(t) = crash_point^((t/T)^p), p>1 — плавный старт, ускорение к концу.


def _generate_crash_multiplier() -> float:
    """
    Генерирует множитель краша по модели crash-игр (Bustabit/Stake).
    r ~ U(0, 1) → multiplier = 1/(1-r), ограничен 10.00x, округление до 2 знаков.
    Распределение с тяжёлым хвостом: малые множители частые, большие редкие.
    """
    r = random.random()  # [0, 1)
    if r >= 0.9999:
        r = 0.9999
    return round(min(10.0, 1.0 / (1.0 - r)), 2)


# Длительность анимации (сек) — случайная, не связана с crash_point
ROCKET_DURATION_MIN = 5.0
ROCKET_DURATION_MAX = 11.0
ROCKET_TICK = 1.0
ROCKET_CURVE_P = 1.35


@dp.message(Command("rocket"))
async def rocket(message: types.Message):
    if not await _thread_allowed(message):
        return
    args = (message.text or "").split()
    if len(args) != 2:
        await message.reply("Использование: /rocket <сумма>")
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
    key = (chat_id, user_id)

    if key in rocket_games and rocket_games[key].get("active"):
        await message.reply("У вас уже запущена ракета. Дождитесь окончания игры.")
        return

    bal = await db.get_balance(user_id, chat_id)
    if bal is None:
        await message.reply("Сначала /registration")
        return

    if amount > bal:
        await message.reply(f"Недостаточно {mimriks(amount)}.")
        return

    await db.change_balance(user_id, chat_id, -amount)

    crash_point = _generate_crash_multiplier()
    duration = random.uniform(ROCKET_DURATION_MIN, ROCKET_DURATION_MAX)

    kb = InlineKeyboardBuilder()
    kb.add(InlineKeyboardButton(text="Забрать", callback_data="rocket_stop"))

    intro = (
        "🚀 Ракета взлетает!\n"
        f"Ставка: {amount} {mimriks(amount)}\n"
        f"Множитель: 1.00x\n\n"
        "Рост по фиксированной кривой до случайного краша (макс. 10.00x). "
        "Длительность полёта не подсказывает, на каком множителе будет взрыв.\n\n"
        "Нажмите «Забрать», чтобы забрать выигрыш."
    )
    msg = await message.reply(intro, reply_markup=kb.as_markup())

    rocket_games[key] = {
        "amount": amount,
        "multiplier": 1.0,
        "active": True,
        "message_id": msg.message_id,
        "crash_point": crash_point,
        "duration": duration,
        "flight_epoch": 0,
        "finalized": False,
    }

    asyncio.create_task(_rocket_flight(chat_id, user_id))


async def _rocket_flight(chat_id: int, user_id: int):
    key = (chat_id, user_id)
    state = rocket_games.get(key)
    if not state or not state["active"]:
        return

    epoch_at_start = state["flight_epoch"]
    amount = state["amount"]
    crash_point = state["crash_point"]
    duration = state["duration"]
    p = ROCKET_CURVE_P
    t = 0.0

    def _aborted() -> bool:
        st = rocket_games.get(key)
        if st is not state:
            return True
        if st.get("flight_epoch") != epoch_at_start:
            return True
        if st.get("finalized"):
            return True
        return False

    while state.get("active") and t < duration:
        await asyncio.sleep(ROCKET_TICK)
        if _aborted():
            return

        t += ROCKET_TICK
        progress = min(1.0, t / duration)
        current_mult = crash_point ** (progress**p)
        current_mult = min(current_mult, crash_point)
        current_mult = round(current_mult, 2)
        state["multiplier"] = current_mult

        if _aborted():
            return

        if t >= duration:
            if _aborted():
                return
            state["active"] = False
            state["finalized"] = True
            text = (
                "🚀 Ракета взлетает!\n"
                f"Ставка: {amount} {mimriks(amount)}\n"
                f"Множитель: {crash_point:.2f}x\n\n"
                "💥 Ракета взорвалась! Ставка проиграна."
            )
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=state["message_id"],
                    text=text,
                    reply_markup=None,
                )
            except Exception:
                pass
            rocket_games.pop(key, None)
            return

        text = (
            "🚀 Ракета взлетает!\n"
            f"Ставка: {amount} {mimriks(amount)}\n"
            f"Множитель: {current_mult:.2f}x\n\n"
            "Рост по фиксированной кривой; момент краша заранее не угадывается.\n\n"
            "Нажмите «Забрать», чтобы забрать выигрыш."
        )
        try:
            kb = InlineKeyboardBuilder()
            kb.add(InlineKeyboardButton(text="Забрать", callback_data="rocket_stop"))
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=state["message_id"],
                text=text,
                reply_markup=kb.as_markup(),
            )
        except Exception:
            state["active"] = False
            state["finalized"] = True
            rocket_games.pop(key, None)
            return


@dp.callback_query(F.data == "rocket_stop")
async def rocket_stop_callback(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    user_id = callback.from_user.id
    key = (chat_id, user_id)

    state = rocket_games.get(key)
    if not state or not state.get("active"):
        await callback.answer("Игра уже завершена.", show_alert=True)
        return

    # Снимок множителя до инвалидации полёта (чтобы не промахнуться с тиком фона)
    amount = state["amount"]
    multiplier = state["multiplier"]
    # Инвалидируем цикл _rocket_flight — после этого он не редактирует сообщение
    state["flight_epoch"] = state.get("flight_epoch", 0) + 1
    state["active"] = False
    state["finalized"] = True

    win = int(amount * multiplier)

    await db.change_balance(user_id, chat_id, win)

    text = (
        "🚀 Ракета взлетает!\n"
        f"Ставка: {amount} {mimriks(amount)}\n"
        f"Множитель: {multiplier:.2f}x\n\n"
        f"🛑 Вы нажали «Забрать» и выиграли {win} {mimriks(win)}!"
    )

    try:
        await callback.message.edit_text(text, reply_markup=None)
    except Exception:
        pass

    rocket_games.pop(key, None)
    await callback.answer(f"Вы забрали {win} {mimriks(win)}!")

def _extract_utf16(text: str, offset: int, length: int) -> str:
    """Извлекает подстроку по offset и length в единицах UTF-16 (как в Telegram API)."""
    if not text or length <= 0:
        return ""
    utf16 = text.encode("utf-16-le")
    start = offset * 2
    end = (offset + length) * 2
    if start >= len(utf16):
        return ""
    end = min(end, len(utf16))
    return utf16[start:end].decode("utf-16-le", errors="replace")


async def _resolve_rob_target(message: types.Message, chat_id: int, user_id: int):
    """
    Определяет user_id жертвы для /rob.
    Возвращает target_id (int) или None.
    Приоритет: реплай → text_mention (entity с user) → mention/аргумент @username (поиск в БД).
    """
    # 1. Реплай на сообщение
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user.id

    text = (message.text or "").strip()

    # 2. Entity text_mention — Telegram передаёт точный user, без поиска по БД
    for entity in (message.entities or []):
        if getattr(entity, "type", None) == "text_mention":
            u = getattr(entity, "user", None)
            if u and getattr(u, "id", None):
                return u.id

    # 3. Entity mention — вырезаем текст по UTF-16 (offset/length в Telegram в UTF-16) и ищем в БД
    for entity in (message.entities or []):
        if getattr(entity, "type", None) == "mention":
            offset = getattr(entity, "offset", 0)
            length = getattr(entity, "length", 0)
            part = _extract_utf16(text, offset, length)
            username = (part or "").lstrip("@").strip().lower()
            if username:
                tid = await db.get_user_id_by_username(chat_id, username)
                if tid:
                    return tid

    # 4. Аргумент команды: /rob @username
    parts = text.split()
    if len(parts) >= 2 and parts[1].startswith("@"):
        username = parts[1][1:].strip().lower()
        if username:
            tid = await db.get_user_id_by_username(chat_id, username)
            if tid:
                return tid

    return None


# rob
@dp.message(Command("rob"))
async def rob(message: types.Message):
    if not await _thread_allowed(message):
        return
    chat_id = message.chat.id
    user_id = message.from_user.id

    bal = await db.get_balance(user_id, chat_id)
    if bal is None:
        await message.reply("Сначала /registration")
        return

    target_id = await _resolve_rob_target(message, chat_id, user_id)

    if target_id is None:
        await message.reply("Использование: /rob @username или реплай на сообщение цели.")
        return

    if target_id == user_id:
        await message.reply("Нельзя обворовать себя.")
        return

    target_bal = await db.get_balance(target_id, chat_id)
    if target_bal is None:
        await message.reply("Цель не зарегистрирована в этом чате.")
        return

    if target_bal < 10:
        await message.reply("У жертвы слишком мало мимриков для кражи (нужно минимум 10).")
        return

    rob_cooldown = get("rob_cooldown", 1800)
    last_rob = await db.get_last_rob_time(user_id, chat_id)
    if time.time() - last_rob < rob_cooldown:
        left = int(rob_cooldown - (time.time() - last_rob))
        m = left // 60
        s = left % 60
        await message.reply(
            f"⏳ Кража возможна раз в {rob_cooldown // 60} минут.\n"
            f"Подождите ещё {m} мин {s} сек."
        )
        return

    await db.set_rob_used(user_id, chat_id)

    rob_success = get("rob_success_chance", 0.1)
    steal_pct = get("rob_steal_percent", 0.2)
    fine_pct = get("rob_fine_percent", 0.1)

    if random.random() > rob_success:
        fine_amount = max(1, int(bal * fine_pct))
        fine_amount = min(fine_amount, bal)
        if fine_amount > 0:
            # Штраф списывается с грабителя и начисляется жертве
            await db.change_balance(user_id, chat_id, -fine_amount)
            await db.change_balance(target_id, chat_id, fine_amount)
            w = mimriks(fine_amount)
            await message.reply(
                f"🚔 Вас поймали! Вы потеряли {fine_amount} {w} (штраф за попытку кражи, начислен жертве)."
            )
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
    "rob_success_chance": ("Шанс успеха кражи (0-1)", float, "Вероятности"),
    "rob_steal_percent": ("% кражи при успехе (0-1)", float, "Кража"),
    "rob_fine_percent": ("% штрафа при провале (0-1)", float, "Кража"),
}


@dp.message(Command("settings"))
async def settings_cmd(message: types.Message):
    if message.chat.type != "private":
        return
    if not await db.user_is_any_chat_admin(message.from_user.id):
        await message.reply(
            "Настройки доступны только тому, кто добавил бота в группу.\n"
            "Сначала добавьте бота в чат — вы станете админом казино для этой группы."
        )
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
    if message.chat.type != "private":
        return
    if not await db.user_is_any_chat_admin(message.from_user.id):
        await message.reply(
            "Команда доступна только тому, кто добавил бота в группу.\n"
            "Сначала добавьте бота в чат."
        )
        return
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        await message.reply(
            "Использование: /set <ключ> <значение>\n"
            "Ключи: rob_cooldown, chest_min_interval, chest_max_interval, "
            "rob_success_chance, rob_steal_percent, rob_fine_percent"
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


@dp.message(Command("set_thread"))
async def set_thread_cmd(message: types.Message):
    """Установка разрешённого thread_id для чата.

    Использование (только в личке, от имени того, кто добавил бота в этот чат):
    /set_thread <chat_id> <thread_id>
    Передать 0 вместо thread_id, чтобы снять ограничение для чата.
    """
    if message.chat.type != "private":
        return

    args = message.text.split()
    if len(args) != 3:
        await message.reply(
            "Использование: /set_thread <chat_id> <thread_id>\n"
            "chat_id можно узнать командой /debug_thread в нужном подчате.\n"
            "Укажите thread_id=0, чтобы снять ограничение."
        )
        return

    try:
        chat_id = int(args[1])
        thread_id = int(args[2])
    except ValueError:
        await message.reply("chat_id и thread_id должны быть числами.")
        return

    inviter = await db.get_chat_admin(chat_id)
    if inviter is None or inviter != message.from_user.id:
        await message.reply(
            "Настроить подчат может только тот пользователь, который добавил бота в этот чат."
        )
        return

    # Проверяем, что пользователь действительно админ в этом чате
    try:
        member = await bot.get_chat_member(chat_id, message.from_user.id)
    except Exception:
        await message.reply("Не удалось получить информацию о чате. Бот должен быть добавлен в этот чат.")
        return

    if member.status not in ("administrator", "creator"):
        await message.reply("Вы не являетесь администратором указанного чата.")
        return

    await db.set_chat_thread(chat_id, thread_id or None)

    if thread_id:
        await message.reply(f"✅ Для чата {chat_id} установлен thread_id = {thread_id}.")
    else:
        await message.reply(f"✅ Ограничение по thread_id для чата {chat_id} снято.")


# админ начисление (в группе — только тот, кто добавил бота)
@dp.message(Command("addcoins"))
async def addcoins(message: types.Message):
    if message.chat.type not in ("group", "supergroup"):
        return
    chat_id = message.chat.id
    inviter = await db.get_chat_admin(chat_id)
    if inviter is None:
        await message.reply(
            "Админ чата не зафиксирован. Удалите бота из группы и добавьте снова — "
            "тот, кто добавит, сможет использовать админ-команды."
        )
        return
    if message.from_user.id != inviter:
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

    # Определяем, в какой подчат отправить результат
    message_thread_id = None
    if countdown_msg.chat.type in {"group", "supergroup"}:
        allowed_thread = await db.get_chat_thread(chat_id)
        if allowed_thread:
            message_thread_id = allowed_thread
        else:
            message_thread_id = getattr(countdown_msg, "message_thread_id", None)

    # Итог раунда отправляем отдельным сообщением
    try:
        await bot.send_message(
            chat_id,
            f"🎰 Результат раунда ставок:\n\n"
            f"🏆 Победитель: {winner_name}\n"
            f"💰 Выигрыш: {total_bank} {w}",
            message_thread_id=message_thread_id,
        )
    except Exception:
        pass


# запуск колеса (админ чата — тот, кто добавил бота)
@dp.message(Command("spin"))
async def spin(message: types.Message):
    if not await _thread_allowed(message):
        return
    if message.chat.type not in ("group", "supergroup"):
        return
    chat_id = message.chat.id
    inviter = await db.get_chat_admin(chat_id)
    if inviter is None:
        await message.reply(
            "Админ чата не зафиксирован. Удалите бота из группы и добавьте снова — "
            "тот, кто добавит, сможет использовать /spin."
        )
        return
    if message.from_user.id != inviter:
        return

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

    # Определяем подчат для результата
    message_thread_id = None
    if message.chat.type in {"group", "supergroup"}:
        allowed_thread = await db.get_chat_thread(chat_id)
        if allowed_thread:
            message_thread_id = allowed_thread
        else:
            message_thread_id = getattr(message, "message_thread_id", None)

    try:
        await bot.send_message(
            chat_id,
            f"🎰 Результат раунда ставок (админский спин):\n\n"
            f"🏆 Победитель: {winner_name}\n"
            f"💰 Выигрыш: {total_bank} {w}",
            message_thread_id=message_thread_id,
        )
    except Exception:
        pass


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
    if not await _thread_allowed(message):
        return
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
                # Если задан обязательный thread_id — шлём сундук только туда
                message_thread_id = None
                allowed_thread = await db.get_chat_thread(chat_id)
                if allowed_thread:
                    message_thread_id = allowed_thread

                msg = await bot.send_message(
                    chat_id,
                    "📦 В чате появился сундук!",
                    reply_markup=kb.as_markup(),
                    message_thread_id=message_thread_id,
                )
                chest_available[chat_id] = msg.message_id
            except Exception:
                pass


# Личка: на любое сообщение — инструкция
@dp.message(F.chat.type == "private", F.text, ~F.text.startswith("/"))
async def private_any_message(message: types.Message):
    await message.reply(_private_instructions())


@dp.message(Command("debug_thread"))
async def debug_thread(message: types.Message):
    await message.reply(
        f"chat_id={message.chat.id}, thread_id={getattr(message, 'message_thread_id', None)}"
    )


BOT_COMMANDS = [
    BotCommand(command="start", description="Приветствие"),
    BotCommand(command="help", description="Список команд"),
    BotCommand(command="registration", description="Регистрация (500 мимриков)"),
    BotCommand(command="balance", description="Баланс"),
    BotCommand(command="bet", description="Сделать ставку"),
    BotCommand(command="bank", description="Текущий банк раунда"),
    BotCommand(command="transfer", description="Перевести мимрики"),
    BotCommand(command="coinflip", description="50/50: удвоить или проиграть"),
    BotCommand(command="mines", description="Сапёр: множители и бомбы"),
    BotCommand(command="rocket", description="Ракета: множитель до краша"),
    BotCommand(command="roulette", description="Рулетка: цвета и числа"),
    BotCommand(command="rob", description="Попытаться украсть мимрики"),
    BotCommand(command="chest", description="Забрать сундук"),
    BotCommand(command="leaderboard", description="Топ игроков"),
    BotCommand(command="spin", description="(админ) Запустить рулетку"),
]


async def main():
    await db.init_db()
    await bot.set_my_commands(BOT_COMMANDS)
    asyncio.create_task(chest_spawn_task())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
