# Monobank Expense Agent

A local, review-first Python agent that imports Monobank expenses into an existing Google Spreadsheet.

The agent never writes imported data directly into monthly sheets. It creates proposals in a `Review` tab, where you can correct the category and change the status to `Approved`. Approved rows are applied by the next sync or by `expense-agent apply`.

## What is implemented

- Monobank personal API import from selected accounts.
- Settled outgoing transactions only; incoming payments and holds are ignored.
- PLN conversion through the official NBP API when the original transaction is not PLN.
- Deterministic merchant and MCC rules before `gpt-5.4-mini` fallback.
- Automatic learning from approved merchant/category corrections.
- Google OAuth desktop authentication and edits to the existing spreadsheet.
- `Review` and hidden `Agent Log` schemas, category migration, validation, formulas, and chart-range updates.
- Natural-language `ADD` and `EDIT` proposals through a local CLI.
- SQLite deduplication, sync cursors, proposal audit data, and FX cache.
- Local XLSX backups with ten-file rotation.
- Daily Windows Task Scheduler configuration for 23:59.
- Windows toast summaries.

Pekao and years after 2026 are intentionally out of scope for version 1. A vector database is not used because there is no document-retrieval problem.

## Install

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"
Copy-Item .env.example .env
```

Complete [Google setup](docs/google-setup.md), add your keys to `.env`, and run:

```powershell
expense-agent doctor
expense-agent setup --dry-run
expense-agent setup
expense-agent sync
```

```cmd
py -3.12 -m venv .venv
source .venv/Scripts/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"
cp .env.example .env
```

The first sync starts at July 1, 2026. It does not re-import the existing April–June rows.

## Commands

```text
expense-agent setup [--dry-run]
expense-agent sync
expense-agent apply
expense-agent chat "add 24 zł McDonald's today as Кафе и рестораны"
expense-agent doctor
expense-agent install-schedule
```

See [Operations](docs/operations.md) for review, recovery, backups, and scheduling.

## Tests

The suite uses only the standard library and dependency injection, so it can test external adapter behavior without bank or Google credentials:

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

With development dependencies installed:

```powershell
pytest -q
ruff check .
mypy src
python -m build
```

## Security

- `.env`, OAuth files, SQLite data, logs, and backups are ignored by Git.
- Only merchant text, MCC, amount band, and allowed categories are sent to OpenAI.
- Account IDs, balances, IBANs, tokens, and raw statement payloads are not sent to OpenAI.
- The language model cannot call spreadsheet write methods directly.

