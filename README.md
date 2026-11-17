# TenderReady

TenderReady is a local-first MVP that helps African SMEs understand public tender documents. Save a business profile, upload one or more tender files, and receive an AI-generated eligibility assessment, required-document checklist, deadlines, profile gaps, and an ordered submission plan. Every cited passage is checked against extracted text and labeled with its document and page, paragraph, sheet/row, slide, or line.

## Run locally

Requires Python 3.11 or newer. Install Tesseract OCR and its English language data to read scanned PDFs and image uploads:

```bash
# macOS
brew install tesseract

# Debian / Ubuntu
sudo apt-get update && sudo apt-get install -y tesseract-ocr tesseract-ocr-eng
```

On Windows, install Tesseract OCR and ensure `tesseract.exe` is on `PATH`. Set `TENDERREADY_OCR_LANGUAGE` in `.env` if documents use another installed language.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
[ -f .env ] || cp .env.example .env
```

For Claude, add your Anthropic key to `.env` as `ANTHROPIC_API_KEY`; the app calls Anthropic's Messages API directly and defaults to `claude-sonnet-4-5`. `CLAUDE_API_KEY` is also accepted. If a standard `sk-ant-` Claude key was entered under `OPENAI_API_KEY`, it is detected and routed to Anthropic, though `ANTHROPIC_API_KEY` is the recommended variable.

Alternatively, set `OPENAI_API_KEY` to use OpenAI's chat-completions API; `OPENAI_MODEL` and `OPENAI_BASE_URL` can be changed for a compatible endpoint. If both providers' keys are present, Anthropic is preferred. Without a key, profile saving and local document extraction work, but analysis returns a clear configuration error rather than sample results.

Start the app:

```bash
uvicorn app.main:app --reload
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). Save your business profile, select up to 20 files, then choose **Analyze this tender**. Supported formats are PDF, DOCX, XLSX, legacy XLS, PPTX, TXT, CSV, HTML, and PNG/JPEG/TIFF/BMP/WebP images. Each file is limited to 12 MB, and the combined upload to 40 MB. Scanned PDFs and image files are OCR-processed locally using Tesseract.

Recent results and the profile are stored in the local SQLite database at `data/tenderready.sqlite3` (override with `TENDERREADY_DB_PATH`). Uploaded files are processed in memory and are not retained. Extracted tender text is sent to the configured AI provider for analysis; do not upload documents you are not permitted to share with that provider. If OCR is unavailable, the app returns a clear installation error rather than silently analyzing incomplete text.

## Host a private tester preview

The repository includes a Render Blueprint in `render.yaml`. It creates a free Docker web service with Tesseract installed for OCR; Neon provides the PostgreSQL database so data survives Render restarts and idle spin-downs.

1. Create a free Neon project and copy its pooled PostgreSQL connection string with SSL enabled.
2. In Render, choose **New → Blueprint**, connect this repository, and deploy `render.yaml`.
3. When prompted, enter the Neon connection string as `DATABASE_URL`, a long random `TESTER_ACCESS_CODE`, and an AI provider key such as `ANTHROPIC_API_KEY`. Render generates `SESSION_SECRET`.
4. Share the Render URL and tester access code only with invited testers.

Set `APP_ENV=production` in the deployment. The app requires both `SESSION_SECRET` and `TESTER_ACCESS_CODE` in production and uses HTTPS-only session cookies. Each tester browser session gets its own profile and analysis history; the access code itself is shared, so this is suitable for a closed pilot, not individual user accounts. Clearing browser cookies creates a new tester workspace. Do not upload confidential tender documents: extracted text is sent to the configured AI provider. AI provider usage can incur charges.

Free Render services sleep when idle and may take about a minute to wake. Neon Free compute also scales to zero; the first database request after idle may be slower. This setup is for a small pilot, not production service-level guarantees.

## Verify

```bash
pytest
```

## Notes

- Never put API keys in source control; `.env` and local database files are ignored.
- The upload and extraction workflow runs locally; AI analysis requires a configured provider key and sends the extracted text to that provider.
- The AI assessment is decision support, not legal or procurement advice. Verify requirements and deadlines against the cited source passages and official tender notices.
