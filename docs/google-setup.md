# Google OAuth setup

The agent edits your existing Google Spreadsheet. It does not create a replacement spreadsheet.

1. Open [Google Cloud Console](https://console.cloud.google.com/).
2. Create or select a project.
3. Enable **Google Sheets API** and **Google Drive API**.
4. Configure the OAuth consent screen for an external or internal desktop application.
5. Add your Google account as a test user if the application remains in testing mode.
6. Create an OAuth client ID with application type **Desktop app**.
7. Download the client JSON to `secrets/google_credentials.json`.
8. Copy the ID from the existing spreadsheet URL:

   ```text
   https://docs.google.com/spreadsheets/d/SPREADSHEET_ID/edit
   ```

9. Put the ID in `.env` as `GOOGLE_SPREADSHEET_ID`.
10. Run `expense-agent setup --dry-run`.

The first authenticated command opens a browser for Google consent and stores the refresh token in `secrets/google_token.json`. Both files are local and ignored by Git.

The requested scopes allow spreadsheet edits and read-only Drive export for local backups.

## Template migration

`expense-agent setup` performs these operations after creating a local XLSX backup:

- validates `Podsumowanie` and all 12 Polish month tabs;
- adds a visible `Review` tab;
- adds a hidden `Agent Log` tab;
- standardizes the 12 approved categories;
- updates monthly and annual formulas;
- adds category/status validation;
- extends category chart source ranges.

Run `setup --dry-run` first. It validates access without changing the spreadsheet.

