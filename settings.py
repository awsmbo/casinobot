"""Модуль настроек бота. Админ может менять через личку."""
import json
import os

SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")

DEFAULTS = {
    # Тайминги (секунды)
    "rob_cooldown": 1800,           # Кулдаун кражи (30 мин)
    "chest_min_interval": 60,       # Мин. интервал сундуков (1 мин)
    "chest_max_interval": 1200,     # Макс. интервал сундуков (20 мин)
    # Вероятности (0-1)
    "rob_success_chance": 0.1,      # Шанс успеха кражи (10%)
    # Кража
    "rob_steal_percent": 0.2,       # Процент кражи при успехе (20%)
    "rob_fine_percent": 0.1,        # Штраф при провале (10% от баланса жертвы)
    # Сундуки: награды (сумма, шанс в %), сумма шансов = 100
    "chest_rewards": [
        (500, 46),
        (1000, 20),
        (2000, 12),
        (5000, 12),
        (10000, 9),
        (100000, 1),
    ],
    # Золотой час: unix timestamp окончания (0 = выключено). Ставится командой /golden_hour
    "golden_hour_until": 0.0,
}


def _load() -> dict:
    if os.path.exists(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Мержим с дефолтами для новых ключей
                result = DEFAULTS.copy()
                for k, v in data.items():
                    if k in result:
                        result[k] = v
                return result
        except Exception:
            pass
    return DEFAULTS.copy()


def _save(data: dict):
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


_settings = None


def get_settings() -> dict:
    global _settings
    if _settings is None:
        _settings = _load()
    return _settings


def reload_settings():
    global _settings
    _settings = _load()


def get(key: str, default=None):
    return get_settings().get(key, default or DEFAULTS.get(key))


def set_value(key: str, value):
    data = get_settings()
    data[key] = value
    _save(data)
    reload_settings()


def set_multiple(updates: dict):
    data = get_settings()
    for k, v in updates.items():
        if k in DEFAULTS:
            data[k] = v
    _save(data)
    reload_settings()
