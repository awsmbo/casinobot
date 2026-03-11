#!/bin/bash
# Скрипт установки Casino Bot на Ubuntu/Debian
set -e

echo "=== Casino Bot — установка ==="

# Проверка, что мы в папке проекта
if [ ! -f "requirements.txt" ] || [ ! -f "bot.py" ]; then
    echo "Ошибка: запустите скрипт из папки проекта (где лежат bot.py и requirements.txt)"
    exit 1
fi

# Установка системных зависимостей
echo ""
echo "1. Установка системных пакетов..."
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git

# Создание venv
echo ""
echo "2. Создание виртуального окружения..."
if [ -d "venv" ]; then
    echo "   Папка venv уже есть, пересоздаю..."
    rm -rf venv
fi
python3 -m venv venv
source venv/bin/activate

# Установка зависимостей Python
echo ""
echo "3. Установка зависимостей Python..."
pip install -r requirements.txt

# Делаем скрипты исполняемыми
chmod +x run.sh 2>/dev/null || true

# Настройка .env
echo ""
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "4. Создан файл .env из шаблона."
    echo ""
    echo "   ВАЖНО: Отредактируйте .env и укажите:"
    echo "   - BOT_TOKEN — токен от @BotFather"
    echo "   - ADMIN_ID  — ваш Telegram ID (@userinfobot)"
    echo ""
    echo "   Команда: nano .env"
else
    echo "4. Файл .env уже существует, пропускаю."
fi

echo ""
echo "=== Установка завершена ==="
echo ""
echo "Дальнейшие шаги:"
echo "  1. Настройте .env:  nano .env"
echo "  2. Запуск бота:     ./run.sh"
echo "  3. Или в фоне:      nohup ./run.sh > bot.log 2>&1 &"
echo "  4. С systemd:       см. README.md раздел 'Деплой на сервер'"
echo ""
