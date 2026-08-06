# Price Monitor — Мониторинг цен конкурентов на красную икру

Автоматический сбор и сравнение цен на красную икру с сайтов конкурентов.

## Сайты

| Сайт | Статус | Метод |
|---|---|---|
| apeti.ru | ✅ Работает | HTML-парсинг (lxml) |
| seafood-shop.ru | ✅ Работает | JSON API |
| delikateska.ru | ⚠ Нужен Playwright | Браузерная автоматизация |

## Установка

```bash
cd price-monitor

# Основные зависимости (уже есть: httpx, lxml)
pip install httpx lxml

# Для delikateska.ru:
pip install playwright
playwright install chromium
```

## Использование

### Быстрый запуск
```bash
python -m monitor.cli --run
```

### Показать последний отчёт
```bash
python -m monitor.cli --report
```

### История цен
```bash
python -m monitor.cli --history
```

### Тестирование на фикстурах
```bash
python -m monitor.cli --selftest
```

## Настройка

### Наша цена
Отредактируй `data/user_price.json`:
```json
{"price_rub": 1590}
```

### Параметры отслеживания
В `monitor/config.py`:
- `DEFAULT_TARGETS` — список отслеживаемых товаров (название, вес, ключевые слова)
- `DEFAULT_SITES` — список сайтов и методы сбора

## Claude Code интеграция

### Слэш-команда
```
/price-check
```

### Авто-триггер (скилл)
Claude Code сам запустит проверку при запросе:
- "проверь цены на икру"
- "сравни цены конкурентов"
- "какие цены на рынке"

## Структура проекта

```
price-monitor/
├── monitor/
│   ├── cli.py              # CLI (--run, --report, --history, --selftest)
│   ├── config.py           # Конфигурация сайтов и товаров
│   ├── normalize.py         # Извлечение веса, нормализация цен
│   ├── store.py            # SQLite-хранилище
│   ├── report.py           # Форматированный вывод
│   └── adapters/
│       ├── apeti.py        # apeti.ru (lxml + httpx)
│       ├── seafood_shop.py # seafood-shop.ru (JSON API)
│       └── delikateska.py  # delikateska.ru (httpx → Playwright)
├── data/
│   ├── prices.db           # SQLite база с историей
│   ├── user_price.json     # Наша цена
│   ├── reports/            # CSV/JSON отчёты
│   └── snapshots/          # Снапшоты сырых данных
├── .claude/
│   ├── commands/price-check.md
│   └── skills/price-monitor/SKILL.md
└── tests/fixtures/         # Тестовые фикстуры
```

## Формат вывода

Таблица сравнения с сортировкой по цене за 100 г, статистика рынка, позиция нашей цены, предупреждения об изменениях >5%.

## TODO

- [ ] Установить Playwright для delikateska.ru
- [ ] Настроить еженедельный запуск через Windows Task Scheduler
- [ ] Заморозить фикстуры для selftest
