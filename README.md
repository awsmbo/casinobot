# Casino Bot

Telegram-бот для казино с рулеткой на базе aiogram 3.

## Возможности

- Регистрация (500 стартовых монет)
- Ставки и банк
- Рулетка с взвешенным случайным выбором (больше ставка — больше шанс)
- Лидерборд топ-10
- Админ: начисление монет, запуск рулетки

## Установка

```bash
python -m venv venv
source venv/bin/activate  # Linux/macOS
# или: venv\Scripts\activate  # Windows

pip install -r requirements.txt
```

## Настройка

1. Скопируйте `.env.example` в `.env`:
   ```bash
   cp .env.example .env
   ```

2. Заполните `.env`:
   - `BOT_TOKEN` — токен от [@BotFather](https://t.me/BotFather)
   - `ADMIN_ID` — ваш Telegram user ID (узнать: [@userinfobot](https://t.me/userinfobot))

## Запуск

```bash
python bot.py
```

## Команды

| Команда | Описание |
|---------|----------|
| `/start` | Приветствие |
| `/help` | Список команд |
| `/registration` | Регистрация (500 монет) |
| `/balance` | Баланс |
| `/bet <сумма>` | Сделать ставку |
| `/bank` | Текущий банк раунда |
| `/leaderboard` | Топ игроков |
| `/addcoins <сумма>` | *(админ)* Начислить монеты (ответом на сообщение) |
| `/spin` | *(админ)* Запустить рулетку |
