# TenderReady

TenderReady is a local-first MVP that helps African SMEs understand public tender documents. Save a business profile, upload a text-based tender PDF, and receive an AI-generated eligibility assessment, required-document checklist, deadlines, profile gaps, and an ordered submission plan. Findings include page-numbered quotations verified against the extracted PDF text.

## Run locally

Requires Python 3.11 or newer.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Add your AI provider key to `.env` as `OPENAI_API_KEY`. The app uses the OpenAI chat-completions API by default. `OPENAI_MODEL` and `OPENAI_BASE_URL` can be changed for a compatible provider endpoint. Without a key, profile saving and PDF validation work, but analysis returns a clear configuration error rather than sample results.

Start the app:

```bash
uvicorn app.main:app --reload
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). Save your business profile, choose a PDF up to 12 MB, then select **Analyze this tender**. Recent results and the profile are stored in the local SQLite database at `data/tenderready.sqlite3` (override with `TENDERREADY_DB_PATH`). Uploaded PDF files are not retained. Scanned/image-only PDFs need OCR before upload; this MVP extracts selectable PDF text only.

## Verify

```bash
pytest
```

## Notes

- Never put API keys in source control; `.env` and local database files are ignored.
- The AI assessment is decision support, not legal or procurement advice. Verify requirements and deadlines against the cited source passages and official tender notices.
