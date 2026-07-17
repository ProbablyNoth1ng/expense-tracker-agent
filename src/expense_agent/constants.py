from __future__ import annotations

CATEGORIES = (
    "Еда и продукты",
    "Транспорт",
    "Жильё / коммуналка",
    "Развлечения",
    "Одежда",
    "Здоровье / аптека",
    "Кафе и рестораны",
    "Бытовые штуки",
    "Налоги, зус, буг",
    "Подписки",
    "Прочее",
    "Для дома",
    "Переводы",
    "Игры",
)

MONTH_SHEETS = {
    1: "Styczeń",
    2: "Luty",
    3: "Marzec",
    4: "Kwiecień",
    5: "Maj",
    6: "Czerwiec",
    7: "Lipiec",
    8: "Sierpień",
    9: "Wrzesień",
    10: "Październik",
    11: "Listopad",
    12: "Grudzień",
}

REVIEW_STATUSES = ("Pending", "Approved", "Rejected", "Synced", "Conflict", "Error")

MCC_CATEGORY_RULES = {
    4111: "Транспорт",
    4121: "Транспорт",
    5411: "Еда и продукты",
    5812: "Кафе и рестораны",
    5814: "Кафе и рестораны",
    5912: "Здоровье / аптека",
}

ISO_NUMERIC_CURRENCIES = {980: "UAH", 978: "EUR", 840: "USD", 985: "PLN", 826: "GBP"}
