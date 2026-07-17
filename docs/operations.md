# Operations

## Daily workflow

1. `sync` first applies rows you previously marked `Approved`.
2. It fetches settled outgoing transactions from the selected Monobank accounts.
3. New transactions appear as `Pending` rows in `Review`.
   Review rows are automatically sorted by `Date` from oldest to newest.
4. Correct `Date`, `Category`, `Shop`, or `Amount PLN` if necessary.
5. Change each accepted row to `Approved`; use `Rejected` for rows that should never be imported.
6. Run `expense-agent apply` for immediate application, or wait for the next 23:59 sync.

Final statuses are `Synced`, `Conflict`, or `Error`. A conflict means an existing row changed after an edit proposal was created; the agent does not overwrite it.

## Manual commands

Natural-language commands support English, Russian, Ukrainian, and Polish:

```powershell
expense-agent chat "add 42 zł Lidl today as Еда и продукты"
expense-agent chat "change yesterday's McDonald's expense to 30 zł"
```

The model creates a proposal only. For edits, the agent searches the current monthly sheets. If several rows match, the CLI asks you to select one. Deletion is not supported in version 1.

## Scheduling

Install the task while the project virtual environment is active:

```powershell
expense-agent install-schedule
```

The Windows task runs daily at 23:59, starts after a missed schedule when the PC becomes available, requires network access, ignores overlapping runs, and has a two-hour limit. The computer must be on for execution.

## Backups and recovery

Before setup migration, manual apply, or scheduled sync, the spreadsheet is exported to `data/backups/`. The newest ten XLSX files are retained.

To restore:

1. Stop the scheduled task.
2. Open the desired backup in Excel or import it into a temporary Google Sheet for inspection.
3. Use Google Sheets version history for the live spreadsheet, or manually copy the affected ranges from the backup.
4. Run `expense-agent doctor`, then resume syncing.

## Common errors

- **Missing configuration:** copy `.env.example` to `.env` and fill all required values.
- **No selected accounts:** run `expense-agent setup`.
- **Google OAuth error:** delete only `secrets/google_token.json`, then rerun the command to authorize again.
- **Monobank 429:** wait at least 60 seconds; the adapter intentionally spaces statement requests.
- **NBP 404:** weekends and holidays automatically fall back to the previous publication date.
- **Only 2026 is supported:** out-of-year transactions remain unapplied by design.
- **Conflict:** inspect the current target row and create a new edit proposal.

## Adding Pekao later

Implement Pekao as a separate normalization adapter after obtaining a redacted real export sample. Feed normalized Pekao records into the same categorization, Review, approval, and apply pipeline. Do not mix Pekao parsing into the Monobank client.
