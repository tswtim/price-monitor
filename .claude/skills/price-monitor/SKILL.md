---
name: price-monitor
description: Мониторинг цен конкурентов на красную икру. Автоматически срабатывает на запросы о сравнении цен, проверке конкурентов.
triggers:
  - "проверь цены"
  - "сравни цены"
  - "мониторинг цен"
  - "цены конкурентов"
  - "какие цены на икру"
  - "check competitor prices"
  - "price check"
---

# Price Monitor Skill

Сбор и анализ цен конкурентов на красную икру с сайтов:
- apeti.ru
- seafood-shop.ru (Икорный)
- delikateska.ru (Деликатеска)

## Как использовать

### Быстрая проверка
```bash
cd price-monitor && python -m monitor.cli --run
```

### Показать последний отчёт
```bash
cd price-monitor && python -m monitor.cli --report
```

### История цен
```bash
cd price-monitor && python -m monitor.cli --history
```

## Настройка

Файлы конфигурации:
- `monitor/config.py` — список сайтов, целевые товары
- `data/user_price.json` — наша цена `{"price_rub": 1590}`

## Примечания

- Для delikateska.ru требуется Playwright: `pip install playwright && playwright install chromium`
- Данные сохраняются в SQLite `data/prices.db`
- Отчёты в `data/reports/`
- Снапшоты в `data/snapshots/`
