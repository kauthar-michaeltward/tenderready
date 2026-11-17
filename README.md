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

For Claude, add your Anthropic key to `.env` as `ANTHROPIC_API_KEY`; the app calls Anthropic's Messages API directly and defaults to `claude-sonnet-4-5`. `CLAUDE_API_KEY` is also accepted. If a standard `sk-ant-` Claude key was entered under `OPENAI_API_KEY`, it is detected and routed to Anthropic, though `ANTHROPIC_API_KEY` is the recommended variable.

Alternatively, set `OPENAI_API_KEY` to use OpenAI's chat-completions API; `OPENAI_MODEL` and `OPENAI_BASE_URL` can be changed for a compatible endpoint. If both providers' keys are present, Anthropic is preferred. Without a key, profile saving and PDF validation work, but analysis returns a clear configuration error rather than sample results.

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
