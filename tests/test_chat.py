import unittest
from datetime import date
from unittest.mock import Mock, patch

from expense_agent.chat import LangChainCommandParser, ParsedCommand


class LangChainCommandParserTests(unittest.TestCase):
    @patch("langchain_openai.ChatOpenAI")
    def test_uses_strict_json_schema_without_an_open_ended_target(self, chat_openai: Mock):
        parser = LangChainCommandParser(api_key="test-key", model="test-model")

        schema = ParsedCommand.model_json_schema()
        self.assertNotIn("target", schema["properties"])
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["required"]), set(schema["properties"]))
        self.assertTrue(all("default" not in field for field in schema["properties"].values()))
        chat_openai.return_value.with_structured_output.assert_called_once_with(
            ParsedCommand,
            method="json_schema",
            strict=True,
        )
        self.assertIs(parser.structured, chat_openai.return_value.with_structured_output.return_value)

    @patch("langchain_openai.ChatOpenAI")
    def test_parse_supplies_local_date_context_for_relative_dates(self, chat_openai: Mock):
        structured = chat_openai.return_value.with_structured_output.return_value
        structured.invoke.return_value = ParsedCommand(
            action="ADD",
            date="2026-07-17",
            category="Кафе и рестораны",
            merchant="McDonald's (TEST)",
            amount_pln=24.0,
            confidence=0.95,
            reason="Explicit expense request",
        )
        parser = LangChainCommandParser(
            api_key="test-key",
            model="test-model",
            today=lambda: date(2026, 7, 17),
        )

        result = parser.parse("add 24 zł McDonald's (TEST) today as Кафе и рестораны")

        prompt = structured.invoke.call_args.args[0]
        self.assertIn("Today's local date is 2026-07-17.", prompt)
        self.assertEqual(result["action"], "ADD")
        self.assertEqual(result["date"], "2026-07-17")
        self.assertEqual(result["category"], "Кафе и рестораны")
        self.assertEqual(result["merchant"], "McDonald's (TEST)")
        self.assertEqual(result["amount_pln"], 24.0)
        self.assertNotIn("target", result)


if __name__ == "__main__":
    unittest.main()
