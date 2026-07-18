from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Mapping, Protocol, cast
import unicodedata

from .constants import CATEGORIES, MCC_CATEGORY_RULES


@dataclass(frozen=True, slots=True)
class CategoryDecision:
    category: str
    confidence: float
    reason: str
    source: str = "model"


class CategoryModel(Protocol):
    def classify(self, **sanitized: Any) -> CategoryDecision: ...


class Categorizer:
    def __init__(
        self,
        *,
        store: Any,
        model: CategoryModel,
        mcc_rules: Mapping[int, str] | None = None,
    ):
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
    def _merchant_tokens(merchant: str) -> tuple[str, ...]:
        normalized = unicodedata.normalize("NFKD", merchant.casefold())
        tokens: list[str] = []
        token: list[str] = []
        for character in normalized:
            if unicodedata.combining(character):
                continue
            if character.isalnum():
                token.append(character)
            elif token:
                tokens.append("".join(token))
                token = []
        if token:
            tokens.append("".join(token))
        return tuple(tokens)

    @staticmethod
    def _matches_merchant_pattern(tokens: tuple[str, ...], pattern: tuple[str, ...]) -> bool:
        length = len(pattern)
        return any(tokens[index : index + length] == pattern for index in range(len(tokens) - length + 1))

    @classmethod
    def _matches_any_merchant_pattern(
        cls, tokens: tuple[str, ...], patterns: tuple[tuple[str, ...], ...]
    ) -> bool:
        return any(cls._matches_merchant_pattern(tokens, pattern) for pattern in patterns)

    @staticmethod
    def _builtin_category(merchant: str) -> str | None:
        tokens = Categorizer._merchant_tokens(merchant)

        # Evaluate subscriptions before game platforms: Game Pass is not a game-store purchase.
        if Categorizer._matches_any_merchant_pattern(
            tokens,
            (
                ("gamepass",), ("game", "pass"),
                ("playstationplus",), ("playstation", "plus"), ("psplus",), ("ps", "plus"),
                ("nintendoswitchonline",), ("nintendo", "switch", "online"),
                ("openai",), ("chatgpt",), ("google",),
            ),
        ):
            return "Подписки"
        if Categorizer._matches_any_merchant_pattern(
            tokens,
            (
                ("steam",), ("epicgames",), ("epic", "games"), ("gog",), ("g", "o", "g"),
                ("battlenet",), ("battle", "net"), ("blizzard",), ("ubisoft",),
                ("riotgame",), ("riotgames",), ("riot", "game"), ("riot", "games"),
                ("ea",), ("eacom",), ("eagames",), ("easports",), ("ea", "com"),
                ("ea", "games"), ("ea", "sports"), ("electronicarts",),
                ("electronic", "arts"),
            ),
        ):
            return "Игры"
        if Categorizer._matches_any_merchant_pattern(tokens, (("zabka",),)):
            return "Еда и продукты"
        if Categorizer._matches_any_merchant_pattern(tokens, (("rossmann",), ("hebe",))):
            return "Здоровье / аптека"
        if Categorizer._matches_any_merchant_pattern(tokens, (("kwiaciarnia",), ("flower",))):
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
            if not isfinite(decision.confidence) or not 0 <= decision.confidence <= 1:
                raise ValueError("model returned an invalid confidence")
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
        from pydantic import SecretStr

        self.structured = ChatOpenAI(
            api_key=SecretStr(api_key), model=model, temperature=0
        ).with_structured_output(CategoryDecision)

    def classify(self, **sanitized: Any) -> CategoryDecision:
        prompt = (
            "Categorize one expense. Use exactly one allowed category. "
            "Бытовые штуки means consumable household supplies; Для дома means durable home items or improvements. "
            f"Data: {sanitized}"
        )
        result: object = self.structured.invoke(prompt)
        if isinstance(result, CategoryDecision):
            return result
        if isinstance(result, Mapping):
            mapping = cast(Mapping[str, object], result)
            confidence = mapping["confidence"]
            if not isinstance(confidence, str | int | float):
                raise TypeError("structured category confidence must be numeric")
            return CategoryDecision(
                category=str(mapping["category"]),
                confidence=float(confidence),
                reason=str(mapping["reason"]),
                source=str(mapping.get("source", "model")),
            )
        raise TypeError("structured category result must be a CategoryDecision or mapping")
