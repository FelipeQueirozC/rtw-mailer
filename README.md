# RTW Biotech Opportunities Mailer

Automated Python mailer for RTW Biotech Opportunities Ltd PDFs. It scrapes the RTW Factsheets & Letters and Results & Presentations pages, renames newly published PDFs using the document reference period, emails them through Resend, and records sent URLs in `sent_documents.json`.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Edit `.env`:

```bash
RESEND_API_KEY=re_xxxxxxxxxxxxxxxxx
RESEND_FROM_EMAIL="RTW@yourdomain.com>"
RESEND_TO_EMAIL="recipient1@domain.com,recipient2@domain.com"
```

## Usage

Dry run, with no email and no state changes:

```bash
python main.py --dry-run
```

Production run:

```bash
python main.py
```

Runs print timestamped progress logs for state loading, page scraping, discovered/new document counts, downloads, sends, output saves, and state updates. These logs also appear in GitHub Actions.

Use a different state file:

```bash
python main.py --state-file tmp_state.json
```

Download PDFs already marked as `seeded` in state into `output/` without sending emails:

```bash
python main.py --download-seeded
```

## First Production Run

When `sent_documents.json` is missing or empty, the script treats the run as a bootstrap:

- It discovers all current PDFs on both RTW pages.
- It emails only the newest Factsheets & Letters PDF.
- It records every discovered PDF in state so historical documents are not emailed later.
- The emailed document is marked `sent`; historical documents are marked `seeded`.
- Seeded historical PDFs are saved locally under `output/`.

## Tests

```bash
pytest
```
