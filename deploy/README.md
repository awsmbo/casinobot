# Деплой на сервер

Скопируйте `casino-bot.service` в systemd и адаптируйте пути:

```bash
# Замените /root/casinobot на путь к вашему проекту
sudo cp casino-bot.service /etc/systemd/system/
sudo nano /etc/systemd/system/casino-bot.service   # измените User, WorkingDirectory, ExecStart если нужно

sudo systemctl daemon-reload
sudo systemctl enable casino-bot
sudo systemctl start casino-bot
sudo systemctl status casino-bot
```

Логи: `journalctl -u casino-bot -f`
