# TenderReady

**Find your fit. Submit with confidence.**

Live app: **https://tenderready.onrender.com**

## The African problem we solve

Public procurement is one of the biggest markets an African small business can sell into, from national departments and municipalities to state-owned utilities. In practice, most SMEs cannot use it:

- **Tender packs are long and scattered.** One tender can be an invitation letter, a bid specification, standard bidding forms and revised versions of each, often as scanned paper.
- **Small mistakes disqualify good businesses.** A missing SBD form, an expired tax compliance PIN, no CSD registration, the wrong B-BBEE or CIDB level, or a missed compulsory briefing ends the bid, however good the business is.
- **Owners cannot afford to guess.** Tender consultants are expensive, and small teams lose days reading tenders they were never eligible for.

TenderReady reads the tender for you. You describe your business once, upload the tender documents, and get a plain-language brief:

- **Eligibility assessment**: eligible, conditional, ineligible or unclear, with the reasons.
- **Mandatory documents**: every form and certificate the tender asks for.
- **Key deadlines**: briefing sessions, clarification cut-offs, closing dates and bid validity.
- **Missing requirements**: where your profile does not yet meet what the tender asks for.
- **Submission plan**: ordered next steps with due dates.

Every point is backed by a quote from the tender, labelled with the document and the page, table row or paragraph it came from. Click a source chip to see the exact passage. TenderReady checks each quote against the uploaded text, so it does not show claims it cannot trace back to the tender. It is built around South African tender requirements (SBD forms, SARS, CSD, B-BBEE, CIDB, COIDA) and works for any country's tenders through the business profile.

## How we kept tokens low

1. All reading happens on our side, not the AI's: PDFs, Word, Excel and scanned pages (OCR with Tesseract) are converted to text locally, so the AI only receives plain text, never images or raw files.
2. One AI call per tender: a single forced tool call returns the whole brief as structured JSON, with no chat back-and-forth or multi-step agent, and the fallback "Basic check" uses no AI tokens at all.
3. Short answers by design: the prompt asks for quotes under 200 characters, at most 3 sources per item and two-sentence reasons, and the output schema caps every list and field length.

## How to run

**Use the live app:** open https://tenderready.onrender.com (no sign-up needed).

**Run it on your own machine** (Python 3.11+, full details under [Run locally](#run-locally)):

```bash
git clone https://github.com/kauthar-michaeltward/tenderready.git && cd tenderready
brew install tesseract            # or: sudo apt-get install -y tesseract-ocr tesseract-ocr-eng
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env              # then add ANTHROPIC_API_KEY=your-key to .env
uvicorn app.main:app --reload     # open http://127.0.0.1:8000
```

## What works

| Feature | Status |
|---|---|
| Business profile, saved per browser session | Working |
| Upload one or more tender files (PDF, Word, Excel, PowerPoint, text, CSV, HTML, images) | Working |
| Scanned PDFs and photos of pages, read with OCR | Working |
| AI tender analysis with cited, verified sources | Working |
| Progress bar while the analysis runs (about 1 to 3 minutes) | Working |
| Automatic retry, then a labelled "Basic check" if the AI cannot return a valid result | Working |
| History of your recent analyses | Working |

## How to try it (5 minutes)

1. **Open** https://tenderready.onrender.com. If it has been idle, the first load can take up to a minute while the free server wakes up.
2. **Fill in the business profile** with the example business below, then click **Save business profile**.
3. **Download a sample tender** from the table below. Upload **one sample at a time**, and never more than **3 files** in one analysis.
4. **Drop the file** into **Review a tender** and click **Analyze this tender**.
5. **Wait for the progress bar** (about 1 to 2 minutes for a sample), then read the brief. Click any source chip to see the quote it is based on.
6. **Try the other samples** to see the three different outcomes: eligible, conditional and ineligible. Earlier results stay under **Recent analyses**.

## Example business

Copy these values into the profile form.

| Field | Value |
|---|---|
| Business name | Kumbula Cloud Solutions (Pty) Ltd |
| Country | South Africa |
| Industry / sector | Managed IT services |
| Years in business | 7 |
| Annual revenue | 18500000 |
| Revenue currency | ZAR |
| Registration number | 2019/123456/07 |
| Certifications | B-BBEE Level 2, Microsoft Solutions Partner (Azure), CSD registered, Tax compliant (valid SARS PIN) |
| What does your business do? | Managed IT support, Cloud backup and disaster recovery, Microsoft Azure, Helpdesk 24/7, Network management |
| Regions where you operate | Gauteng, Western Cape |

This business is fictitious. It deliberately does **not** hold ISO 27001, so you can see TenderReady flag a real gap.

## Sample tender documents

All three tenders are **fictitious** and were written for testing. They are not real opportunities.

| Document | What it tests | Expected result for the example business | Download |
|---|---|---|---|
| RFQ 1123/2026: IT Helpdesk and Cloud Backup Support (PDF) | A small tender the business fully qualifies for | **Eligible**: matches the experience, Azure, Gauteng and compliance requirements | [Live app](https://tenderready.onrender.com/static/samples/sample-tender-it-helpdesk.pdf) · [GitHub](app/static/samples/sample-tender-it-helpdesk.pdf) |
| RFP 9001/2026: Managed IT Support and Cloud Backup (Word) | A tender in the business's own field | **Conditional**: a good fit, but ISO 27001 and some personnel requirements are missing | [Live app](https://tenderready.onrender.com/static/samples/sample-tender-it-support.docx) · [GitHub](app/static/samples/sample-tender-it-support.docx) |
| RFQ 0457/2026: Road Maintenance and Pothole Repairs (PDF) | A tender outside the business's field | **Ineligible**: needs a CIDB 4CE civil engineering grading and road maintenance experience | [Live app](https://tenderready.onrender.com/static/samples/sample-tender-road-maintenance.pdf) · [GitHub](app/static/samples/sample-tender-road-maintenance.pdf) |

AI results vary slightly between runs, so the exact wording and the number of items will differ.

You can also test with any real public tender from the National Treasury eTenders portal (https://www.etenders.gov.za). Do not upload documents marked confidential or restricted: the extracted text is sent to the AI provider.

## Good to know

- **Up to 3 files per analysis**: the analysis is tuned for at most 3 documents at a time, which keeps it to about 1 to 3 minutes and within the AI's answer limit. For a big tender pack, upload the 3 that matter most (usually the bid specification, the invitation letter and the standard bidding document) and skip duplicate or superseded versions.
- **Basic check**: if the AI fails twice, TenderReady shows a clearly labelled "Basic check (AI analysis unavailable)". It lists standard South African requirements (SBD forms, tax compliance, CSD, B-BBEE, CIDB, COIDA, ISO) and dates found in the documents, each with its quote. Eligibility is marked "unclear" because a basic check cannot judge it.
- **Privacy**: uploaded files are processed in memory and not stored. Your profile and results are private to your browser session. Clearing cookies starts a fresh session.
- **Decision support, not advice**: always confirm requirements and closing dates against the official tender documents.

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

## Deploy (Render + Neon)

The repository includes a Render Blueprint in `render.yaml`. It builds a free Docker web service with Tesseract installed for OCR. Neon provides the PostgreSQL database, so data survives Render restarts and idle spin-downs.

1. Create a free Neon project and copy its connection string (it ends in `?sslmode=require`).
2. In Render, choose **New > Blueprint**, connect this repository and deploy `render.yaml`.
3. When prompted, enter the Neon string as `DATABASE_URL` and your Claude key as `ANTHROPIC_API_KEY`. Render generates `SESSION_SECRET`.

The site is public. In production (`APP_ENV=production`) every visitor automatically gets a private browser session, so each person sees only their own profile and analyses. Setting `TESTER_ACCESS_CODE` is optional: if set, visitors must enter that code first. Analyses use the configured AI key and can incur charges, so set a spend limit with your AI provider.

Free Render services sleep when idle and can take about a minute to wake. Neon Free also scales to zero, so the first request after a quiet spell is slower.

## Verify

```bash
pytest
```

## Notes

- Never put API keys in source control; `.env` and local database files are ignored.
- The upload and extraction workflow runs locally; AI analysis requires a configured provider key and sends the extracted text to that provider.
- The AI assessment is decision support, not legal or procurement advice. Verify requirements and deadlines against the cited source passages and official tender notices.
