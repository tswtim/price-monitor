# Price Monitor — Мониторинг цен конкурентов на красную икру

## Что это за проект

Автоматический сбор цен на красную икру с сайтов конкурентов:
- **apeti.ru** — парсинг HTML (lxml + httpx)
- **seafood-shop.ru** (Икорный) — прямой JSON API
- **delikateska.ru** (Деликатеска) — GraphQL API

Результат: таблица сравнения цен, нормализованных к цене за 100 г, с историей изменений.

## Как запустить

```bash
# Сбор цен со всех сайтов
python -m monitor.cli --run

# Показать последний отчёт (без повторного сбора)
python -m monitor.cli --report

# История изменений цен
python -m monitor.cli --history
```

## Структура

```
monitor/
├── cli.py              # Точка входа: --run, --report, --history
├── config.py           # Сайты, товары, цены — здесь менять настройки
├── normalize.py        # Фильтрация и нормализация цен
├── store.py            # SQLite-база с историей
├── report.py           # Таблицы, CSV, JSON
└── adapters/
    ├── apeti.py        # apeti.ru
    ├── seafood_shop.py # seafood-shop.ru
    └── delikateska.py  # delikateska.ru (GraphQL)
data/                   # База, отчёты, снапшоты (не в git)
```

## Настройка под себя

1. **Наша цена**: отредактируй `data/user_price.json` — `{"price_rub": 1590}`
2. **Товары для отслеживания**: `monitor/config.py` → `DEFAULT_TARGETS`
3. **Добавить сайт**: создай адаптер в `monitor/adapters/` по образцу существующих

## Перенос на другой компьютер

```bash
# 1. Скопируй папку price-monitor/
# 2. Установи зависимости
pip install -r requirements.txt
playwright install chromium

# 3. Готово — запускай
python -m monitor.cli --run
```

Все `.claude/` команды и скиллы находятся внутри проекта — Claude Code подхватит их автоматически при запуске из этой папки.
