# Casino Bot

Telegram-бот для казино с рулеткой на базе aiogram 3. Валютой служат **мимрики**.

**[→ Перейти к боту](https://t.me/casinobowlingbot)**

## Возможности

**Основное**
- Регистрация — 500 стартовых мимриков
- Ставки — можно добавлять к ставке во время раунда
- Рулетка — взвешенный случайный выбор (чем больше ставка, тем выше шанс победы)
- Игроки нажимают «Запустить», когда готовы; колесо запускается автоматически, когда все участвующие подтвердили готовность
- Лидерборд топ-10

**Дополнительные механики**
- `/transfer` — перевод мимриков другим игрокам (@username или реплай)
- `/coinflip` — 50/50: удвоить ставку или проиграть
- `/rob` — попытка украсть мимрики (10% шанс успеха, 1 раз в 24 часа, до 20% баланса жертвы)
- **Сундуки** — появляются случайно в чате, можно забрать кнопкой или `/chest` (награды 500–100 000 мимриков)
- **Золотая минута** — случайное событие: за каждое сообщение в чате +10 мимриков в течение 60 секунд

**Админ**
- `/addcoins` — начисление мимриков (ответом на сообщение)
- `/spin` — принудительный запуск рулетки

---

## Быстрая установка на сервер (Ubuntu/Debian)

### Шаг 1: Клонирование

```bash
git clone https://github.com/awsmbo/casinobot.git
cd casinobot
```

### Шаг 2: Автоматическая установка

```bash
chmod +x install.sh
./install.sh
```

Скрипт установит Python, venv, зависимости и создаст `.env` из шаблона.

### Шаг 3: Настройка

Отредактируйте `.env` и укажите токен бота и свой ID:

```bash
nano .env
```

- **BOT_TOKEN** — токен от [@BotFather](https://t.me/BotFather)
- **ADMIN_ID** — ваш Telegram ID (узнать: [@userinfobot](https://t.me/userinfobot))

### Шаг 4: Запуск

**Проверка (в терминале):**
```bash
./run.sh
```

**В фоне (работает после отключения SSH):**
```bash
nohup ./run.sh > bot.log 2>&1 &
tail -f bot.log   # просмотр логов
```

**Через systemd (автозапуск, перезапуск при падении):**
```bash
sudo cp deploy/casino-bot.service /etc/systemd/system/
# Если проект не в /root/casinobot — отредактируйте пути в файле
sudo systemctl daemon-reload
sudo systemctl enable casino-bot
sudo systemctl start casino-bot
```

---

## Установка вручную (локально или на другом сервере)

### Требования

- Python 3.9+
- Git

### 1. Клонирование репозитория

```bash
git clone https://github.com/awsmbo/casinobot.git
cd casinobot
```

### 2. Виртуальное окружение

```bash
python3 -m venv venv
source venv/bin/activate   # Linux/macOS
# или: venv\Scripts\activate   # Windows

pip install -r requirements.txt
```

### 3. Настройка .env

```bash
cp .env.example .env
nano .env   # или любой редактор
```

Заполните:
- `BOT_TOKEN` — токен от [@BotFather](https://t.me/BotFather)
- `ADMIN_ID` — ваш Telegram user ID ([@userinfobot](https://t.me/userinfobot))

### 4. Запуск

```bash
python bot.py
```

---

## Команды бота

| Команда | Описание |
|---------|----------|
| `/start` | Приветствие |
| `/help` | Список команд |
| `/registration` | Регистрация (500 мимриков) |
| `/balance` | Баланс |
| `/bet <сумма>` | Сделать ставку (можно добавлять к ставке во время раунда) |
| `/bank` | Текущий банк раунда |
| `/transfer @user <сумма>` | Перевести мимрики (или реплай + сумма) |
| `/coinflip <сумма>` | 50/50: удвоить или проиграть |
| `/rob @user` | Попытаться украсть мимрики (реплай тоже работает) |
| `/chest` | Забрать сундук, если он появился в чате |
| `/leaderboard` | Топ игроков |
| `/addcoins <сумма>` | *(админ)* Начислить мимрики (ответом на сообщение) |
| `/spin` | *(админ)* Запустить рулетку |

---

## Структура проекта

```
casinobot/
├── bot.py          # Основной код бота
├── config.py       # Загрузка настроек из .env
├── db.py           # Работа с SQLite
├── utils.py        # Хелперы (склонение мимриков и др.)
├── requirements.txt
├── .env.example    # Шаблон настроек
├── install.sh      # Скрипт установки на сервер
├── run.sh          # Запуск бота
└── deploy/
    ├── casino-bot.service   # systemd unit
    └── README.md            # Инструкция по systemd
```

---

## Частые вопросы

**Бот не отвечает?**  
Проверьте, что в `.env` указан правильный `BOT_TOKEN` и бот запущен.

**Connection reset / разрыв SSH?**  
Запускайте бота в фоне (`nohup ./run.sh > bot.log 2>&1 &`) или через systemd.

**Обновление с GitHub:**
```bash
cd casinobot
git pull
source venv/bin/activate
pip install -r requirements.txt
# Перезапустить бота (systemctl restart casino-bot или pkill + nohup)
```
