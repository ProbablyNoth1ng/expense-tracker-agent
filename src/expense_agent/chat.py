from __future__ import annotations

import uuid
from datetime import date
from typing import Any, Callable, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from .constants import CATEGORIES
from .models import ChangeProposal


class ParsedCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["ADD", "EDIT"] = Field(description="Expense change to propose")
    date: str = Field(description="ISO date in 2026")
    category: str
    merchant: str
    amount_pln: float
    confidence: float
    reason: str


class ChatService:
    def __init__(self, *, parser: Any, sheets: Any, selector: Callable[[list[dict[str, Any]]], int] | None = None):
        self.parser = parser
        self.sheets = sheets
        self.selector = selector

    def create_proposal(self, text: str) -> ChangeProposal:
        parsed = self.parser.parse(text)
        action_value = str(parsed.get("action", "")).upper()
        if action_value not in {"ADD", "EDIT"}:
            raise ValueError("Version 1 supports only ADD and EDIT")
        action = cast(Literal["ADD", "EDIT"], action_value)
        category = str(parsed["category"])
        if category not in CATEGORIES:
            raise ValueError(f"Unknown category: {category}")
        transaction_date = date.fromisoformat(str(parsed["date"]))
        if transaction_date.year != 2026:
            raise ValueError("Only 2026 is supported")
        target = parsed.get("target")
        if action == "EDIT" and not target:
            candidates = self.sheets.find_expense_candidates(str(parsed["merchant"]))
            if not candidates:
                raise ValueError("No existing expense matches this edit request")
            if len(candidates) == 1:
                selected = candidates[0]
            elif self.selector is not None:
                selected = candidates[self.selector(candidates)]
            else:
                raise ValueError(f"Edit is ambiguous: {len(candidates)} matching rows")
            target = {
                "sheet": selected["sheet"],
                "row": selected["row"],
                "expected": selected["values"],
            }
        proposal = ChangeProposal(
            id=str(uuid.uuid4()),
            action=action,
            transaction_date=transaction_date,
            category=category,
            merchant=str(parsed["merchant"]),
            amount_pln=round(float(parsed["amount_pln"]), 2),
            source="chat",
            target=target,
            confidence=float(parsed.get("confidence", 0.8)),
            reason=str(parsed.get("reason", "natural-language request")),
        )
        self.sheets.stage_proposals([proposal])
        return proposal


class LangChainCommandParser:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        today: Callable[[], date] | None = None,
    ):
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as exc:
            raise RuntimeError("Install project dependencies with: pip install -e .") from exc

        self.today = today or date.today
        self.structured: Any = ChatOpenAI(
            api_key=SecretStr(api_key), model=model, temperature=0
        ).with_structured_output(
            ParsedCommand,
            method="json_schema",
            strict=True,
        )

    def parse(self, text: str) -> dict[str, Any]:
        prompt = (
            "Parse an expense ADD or EDIT request. Never produce DELETE. "
            f"Allowed categories: {CATEGORIES}. Current supported year is 2026. "
            f"Today's local date is {self.today().isoformat()}. "
            "Accept English, Russian, Ukrainian, or Polish. "
            f"Request: {text}"
        )
        result = cast(ParsedCommand, self.structured.invoke(prompt))
        return result.model_dump()
