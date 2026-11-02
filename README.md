# SFWC Instagram Metrics

A local-only Streamlit app for organizing Instagram post insights, reviewing OCR/CSV suggestions, and exporting spreadsheet-ready records. It does not sign in to Instagram, send files to cloud OCR, or call a remote API. Streamlit usage statistics are disabled and its server binds only to `127.0.0.1`.

## Windows setup and launch

1. Install Python 3.11 or newer from [python.org](https://www.python.org/downloads/windows/). Enable the Python Launcher option.
2. Open PowerShell in this project folder and run:

   ```powershell
   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
   .\setup.ps1
   .\run.ps1
   ```

3. Open the local address printed by Streamlit, normally `http://127.0.0.1:8501`.

The app and imported files are kept in this project folder under `app_data\`. Back up that directory securely. The SQLite database and files are not encrypted by the app; protect them with your Windows account and device encryption.

### Optional local OCR

Manual entry and CSV/XLSX imports work without OCR. For screenshot OCR, install the Windows build of Tesseract OCR separately, then make sure `tesseract.exe` is on `PATH` and restart the app. A common installer is the UB Mannheim Windows distribution. Verify the installer and source before installing it. You can check detection in PowerShell with:

```powershell
tesseract --version
```

OCR is run locally through `pytesseract` and Pillow. It may misread text; all suggestions remain drafts until a person reviews and saves them. If Tesseract is missing or fails, the screenshot is still kept unchanged and can be transcribed manually.

## Workflow

1. In the sidebar, create/select an account and create/select a snapshot. Snapshot IDs are unique per account. Capture time and reporting window are optional; the app never fills them from the import time.
2. In **Import**, enter one stable post/content ID and upload all screenshots for that post/snapshot together. This groups sources under one draft. You may instead enter values directly, import a CSV, or import the first worksheet in an `.xlsx` file.
3. In **Review**, inspect the original screenshot beside editable fields. Verify every OCR/CSV suggestion, including blanks and rounded values, then explicitly save it. A draft is not counted as a confirmed record.
4. In **Records**, filter and inspect saved records, displayed source values, files, raw OCR/import text, and edit history. Corrected metrics are added to the history; the first imported display values and rounded markers remain available.
5. In **Summary**, review discrepancy flags and comparable snapshot changes. In **Export**, download a content-log CSV or a workbook with Summary, Content log, Post details, and Account snapshots sheets.

CSV/XLSX headers are matched against common names such as `Post ID`, `Content ID`, `Views`, `Viewers`, `Accounts reached`/`Reach`, `Interactions`, `Likes`, `Comments`, `Saves`, `Shares`, `Accounts engaged`, `Follower views (%)`, `Views from Home`, `Snapshot ID`, and `Metric basis`. Unrecognized columns are ignored. A stable post/content ID is required for each imported row. Workbook import reads only the first worksheet. There was no `SFWC_Instagram_Metrics_Tracker.xlsx` in the workspace when this app was built, so compatibility with that particular workbook's layout has not been verified; normalize its first sheet to the supported names or export it to CSV if needed.

## Data rules and limitations

- Missing values remain null; `0` is a genuine zero; `--` is unavailable. Values like `5,029` and `1.6K` parse to `5029` and approximately `1600`; the displayed text and rounded marker are retained.
- Exports leave missing metrics blank and include a `Missing fields` list; rounded values have a separate `Rounded fields` marker.
- Percentage values are stored as percentage points (for example, `1.2` means `1.2%`) and exported as numeric values with percent display formatting in Excel.
- Views, viewers, accounts reached/reach, and accounts engaged remain distinct. The app does not substitute viewers for reach or claim viewers summed across posts are unique people.
- Itemized interactions are calculated only if likes, comments, saves, and shares are all present. Home + Profile + Other is calculated only when all three are present. Discrepancies are flagged, never reconciled by changing inputs.
- Records are unique by account, post/content ID, and snapshot. A later snapshot creates a separate record. Snapshot deltas are limited to lifetime values or selected periods with the same exact start/end dates. Unknown measurement bases are not compared.
- Imported files are copied byte-for-byte into `app_data\imports\`; SHA-256 duplicates are warned and not copied again. The app does not modify screenshots. OCR and source text are stored for traceability. CSV and workbook sources are retained locally too.
- Publication date is only accepted as entered/imported; dates seen in artwork are not inferred. Import timestamp, capture timestamp, and publication date remain separate.
- Account-level snapshots are separate from post records. Their optional metrics are stored as supplied JSON; the follower total has a dedicated numeric field.
- This is a local single-user app with no built-in authentication or database encryption. Keep the computer, project folder, and exports access-controlled.
- No Instagram API integration is implemented. Nothing collects Instagram credentials, browser cookies, or session data. I checked Meta's official [media insights](https://developers.facebook.com/documentation/instagram-platform/reference/instagram-media/insights) and [account insights](https://developers.facebook.com/documentation/instagram-platform/api-reference/instagram-user/insights) documentation on 2026-11-02; both pages identify v26.0 as the latest API version.

  If you later want optional read-only API collection, the documented routes are `/{ig-media-id}/insights` and `/{ig-user-id}/insights`. Setup requires a Meta developer app, a professional (Business or Creator) Instagram account, the account/media IDs, user-granted access, and an access token. Meta documents Instagram Login permissions `instagram_business_basic` and `instagram_business_manage_insights`; the Facebook Login route documents `instagram_basic`, `instagram_manage_insights`, and `pages_read_engagement` (additional ad permissions can apply to some Page-role setups). Token type, host, and required access depend on the chosen login route. Do not put tokens in source code; an eventual integration should use a Windows credential store and redact tokens.

  The media endpoint documents `views`, `likes`, `comments`, `saved`, `shares`, `total_interactions`, `reach`, `profile_activity`, and `profile_visits`, with some metrics restricted by media type. Its `follows` metric is listed for feed and story media. Account insights document `accounts_engaged`, `views`, `reach`, `follows_and_unfollows`, `profile_links_taps`, and other account metrics; `views` can have follower/non-follower and media-product breakdowns. API `reach` is distinct from the app's `viewers`, and the app would keep those names separate. The media endpoint does not document the screenshot's Home/Profile/Other view-source breakdown or follower/non-follower view percentages. Profile link taps are categorized contact-button taps, not necessarily the same as the screenshot's external link or business address labels. Meta says unavailable metrics may be returned as an empty dataset (not zero), data can be delayed up to 48 hours, and story insights have a short availability window. Any future API feature should preserve these distinctions rather than fill unsupported values.

  These API prerequisites are for a future integration only. The local manual, CSV, workbook, and OCR workflow does not require a Meta app, API access, or credentials.

## Local data and tests

- SQLite: `app_data\instagram_metrics.sqlite3`
- Original imports: `app_data\imports\`
- Run the unit tests:

  ```powershell
  .\.venv\Scripts\python.exe -m unittest discover -s tests -v
  ```

The sample “Consumed by Aja Barber book spotlight” data is only an acceptance-test fixture in `tests\test_metrics_store.py`; it is never preloaded as production data.
