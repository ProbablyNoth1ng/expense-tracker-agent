from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping
import unicodedata

from .constants import CATEGORIES, MCC_CATEGORY_RULES


@dataclass(frozen=True, slots=True)
class CategoryDecision:
    category: str
    confidence: float
    reason: str
    source: str = "model"


class Categorizer:
    def __init__(self, *, store: Any, model: Any, mcc_rules: Mapping[int, str] | None = None):
        self.store = store
        self.model = model
        self.mcc_rules = dict(MCC_CATEGORY_RULES if mcc_rules is None else mcc_rules)

    @staticmethod
    def _amount_band(amount: float) -> str:
        if amount < 25:
            return "small"
        if amount < 150:
            return "medium"
        return "large"

    @staticmethod
    def _merchant_key(merchant: str) -> str:
        normalized = unicodedata.normalize("NFKD", merchant.casefold())
        return "".join(character for character in normalized if character.isalnum())

    @staticmethod
    def _builtin_category(merchant: str) -> str | None:
        merchant_key = Categorizer._merchant_key(merchant)

        # Evaluate subscriptions before game platforms: Game Pass is not a game-store purchase.
        if any(
            pattern in merchant_key
            for pattern in (
                "gamepass",
                "playstationplus",
                "psplus",
                "nintendoswitchonline",
                "openai",
                "chatgpt",
                "google",
            )
        ):
            return "Подписки"
        if any(
            pattern in merchant_key
            for pattern in (
                "steam",
                "epicgames",
                "gog",
                "battlenet",
                "blizzard",
                "ubisoft",
                "riotgame",
            )
        ) or merchant_key in {"ea", "eacom", "eagames", "easports", "electronicarts"}:
            return "Игры"
        if "zabka" in merchant_key:
            return "Еда и продукты"
        if any(pattern in merchant_key for pattern in ("rossmann", "hebe")):
            return "Здоровье / аптека"
        if any(pattern in merchant_key for pattern in ("kwiaciarnia", "flower")):
            return "Развлечения"
        return None

    def categorize(self, merchant: str, mcc: int, amount_pln: float) -> CategoryDecision:
        if mcc == 4829:
            return CategoryDecision("Переводы", 1.0, "MCC 4829", "mcc_rule")
        builtin_category = self._builtin_category(merchant)
        if builtin_category:
            return CategoryDecision(builtin_category, 1.0, "built-in merchant rule", "merchant_rule")
        known = self.store.find_merchant_rule(merchant, mcc)
        if known:
            return CategoryDecision(known, 1.0, "remembered merchant correction", "merchant_rule")
        if mcc in self.mcc_rules:
            return CategoryDecision(self.mcc_rules[mcc], 0.95, f"MCC {mcc}", "mcc_rule")
        try:
            decision = self.model.classify(
                merchant=merchant,
                mcc=mcc,
                amount_band=self._amount_band(amount_pln),
                categories=CATEGORIES,
            )
            if decision.category not in CATEGORIES:
                raise ValueError("model returned an unknown category")
            if decision.confidence < 0.65:
                raise ValueError("model returned insufficient confidence")
            return decision
        except Exception as exc:
            return CategoryDecision("Прочее", 0.0, f"classification fallback: {type(exc).__name__}", "fallback")


class LangChainCategoryModel:
    def __init__(self, *, api_key: str, model: str):
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as exc:
            raise RuntimeError("Install project dependencies with: pip install -e .") from exc
        self.structured = ChatOpenAI(api_key=api_key, model=model, temperature=0).with_structured_output(CategoryDecision)

    def classify(self, **sanitized: Any) -> CategoryDecision:
        prompt = (
            "Categorize one expense. Use exactly one allowed category. "
            "Бытовые штуки means consumable household supplies; Для дома means durable home items or improvements. "
            f"Data: {sanitized}"
        )
        result = self.structured.invoke(prompt)
        if isinstance(result, CategoryDecision):
            return result
        if isinstance(result, Mapping):
            return CategoryDecision(
                category=str(result["category"]),
                confidence=float(result["confidence"]),
                reason=str(result["reason"]),
                source=str(result.get("source", "model")),
            )
        raise TypeError("structured category result must be a CategoryDecision or mapping")
