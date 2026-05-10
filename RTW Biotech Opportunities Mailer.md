# Project Specification: RTW Biotech Opportunities Mailer

## 🤖 **AI Agent Instructions: Read First**
> **Role:** You are an autonomous AI software engineer tasked with building this project. 
> **Communication Protocol:** If you encounter any ambiguities, edge cases not covered in this specification, or if you have doubts about the implementation details, **STOP and ask the user how to proceed**. Do not make blind assumptions regarding the file parsing or naming logic. 
> **Important Technical Note:** Unlike simple static sites, financial websites like RTW Funds often heavily rely on JavaScript/React to load their tables and document links dynamically. There is a high chance we will need to emulate a browser (using tools like `Playwright` or `Selenium`) instead of standard `requests` + `BeautifulSoup`. Please evaluate the target URLs first and suggest the appropriate scraping strategy before writing the full implementation.

---

## 📌 Project Overview
The objective of this project is to build an automated, daily Python script that scrapes the RTW Biotech Opportunities Ltd website for newly published PDF documents (Factsheets, Letters, Results, and Presentations), downloads them, and emails them to a designated list of subscribers. 

This project utilizes a local JSON file to track the state of previously sent documents to prevent duplicate emails. It will be scheduled to run automatically using GitHub Actions.

---

## 📁 Folder Structure
The project should follow this clean, maintainable structure:

```text
rtw-mailer/
├── .github/
│   └── workflows/
│       └── daily.yml          # GitHub Actions workflow for the daily CRON job
├── .env.example               # Example environment variables file
├── .gitignore                 # Standard Python gitignore (excluding .env, venv, etc.)
├── main.py                    # The core scraping, renaming, and mailing logic
├── requirements.txt           # Python dependencies (Playwright, bs4, resend, etc.)
├── sent_documents.json        # State file tracking already sent PDFs
└── README.md                  # Setup and usage instructions for humans

```

---

## 🎯 Target URLs & Scope

The script must extract **only PDF files** from the following two pages:

1. **Results & Presentations:** [`https://www.rtwfunds.com/rtw-biotech-opportunities-ltd/results-presentations/`](https://www.rtwfunds.com/rtw-biotech-opportunities-ltd/results-presentations/)
2. **Factsheets & Letters:** [`https://www.rtwfunds.com/rtw-biotech-opportunities-ltd/factsheets-letters/`](https://www.rtwfunds.com/rtw-biotech-opportunities-ltd/factsheets-letters/)

---

## 🗂️ File Naming Conventions (Strictly Enforced)

The scraped PDFs must be renamed before being saved and attached to the email. The naming logic depends on the source page and the type of document. **The YYMM prefix must ALWAYS reflect the REFERENCE DATE of the document, not the publication date.**

### 1. Factsheets and Letters

* **Target Pattern:** `{YYMM} RTW {Report/Report_Letter}.pdf`
* **Rules:**
* Extract the Year and Month the document references and convert it to `YYMM`.
* Determine if it's a standard report or a letter, and map it to either `Report` or `Report_Letter`.


* **Example:** * "March 2024 Factsheet" ➔ `2403 RTW Report.pdf`

### 2. Results and Presentations

* **Target Pattern:** `{YYMM} {Cleaned_Name}.pdf`
* **Rules:**
* Keep the original name of the presentation/result, but **strip the year** out of the text if it is present.
* Append `{YYMM}` to the front based on the document's reference period.
* **Critical Time Logic:**
* If the document is labeled **"interim"**, the `{MM}` must always be **`06`**.
* If the document refers to a **"full year"** or is a general annual presentation (e.g., just lists a year with no specific month/interim marker), the `{MM}` must always be **`12`**.




* **Examples:**
* "2023 Interim Results Presentation" ➔ `2306 Interim Results Presentation.pdf`
* "Full Year 2022 Results" ➔ `2212 Full Year Results.pdf`
* "Company Overview Presentation 2024" ➔ `2412 Company Overview Presentation.pdf` *(Since the presentation is for the year 2024 overall, it defaults to the end-of-year reference month `12`).*



---

## 🏗️ Architecture & Stack

* **Language:** Python 3.11+
* **Scraping:** `Playwright` (recommended due to likely JS-rendering) or `BeautifulSoup4` + `requests` (only if the site is purely static).
* **Email Provider:** Resend API (via the `resend` Python package).
* **State Management:** A local `sent_documents.json` file.
* **Automation:** GitHub Actions (Scheduled CRON jobs with commit-back permissions to update the state file).

---

## ⚙️ Core Logic Flow

1. **Initialize State:** Load `sent_documents.json` to get a list of previously processed document IDs or URLs. If the file is missing, start with an empty state.
2. **Scrape URLs:** Visit the two target URLs. Wait for the DOM to load (if using Playwright) and extract all PDF links alongside their display titles.
3. **Filter:** Discard any documents whose URLs already exist in the `sent_documents.json` state.
4. **Process & Rename:** Apply the strict reference-date naming conventions outlined above to the unsent documents.
5. **Download:** Fetch the PDF bytes. Implement a robust retry mechanism (e.g., 3 retries, 5-second delay) for transient HTTP errors (429, 500, 502, 503, 504) to handle rate limiting.
6. **Dispatch Email:** Send each new PDF as a separate email via the Resend API. The email subject should match the cleaned PDF filename (without the `.pdf` extension).
7. **Update State:** Append the successfully sent document to `sent_documents.json` (recording the URL, sent date, generated filename, and SHA-256 hash).
8. **Commit State:** In the GitHub Action, commit and push the updated `sent_documents.json` back to the repository so the next run knows what was sent.

---

## 🛠️ Setup & Environment Variables

The application will require a `.env` file for local development and matching GitHub Repository Secrets/Variables for production:

```bash
# .env
RESEND_API_KEY=re_xxxxxxxxxxxxxxxxx
RESEND_FROM_EMAIL="RTW Biotech <reports@yourdomain.com>"
RESEND_TO_EMAIL="recipient1@domain.com,recipient2@domain.com"

```

---

## 🚀 Execution Commands

The agent should set up the CLI using `argparse` to allow for standard runs and dry runs (useful for testing the naming conventions without spamming the Resend API or downloading large PDFs).

```bash
# Install dependencies
pip install -r requirements.txt
playwright install chromium # If Playwright is chosen

# Dry run: Scrape, parse, and output the generated names to the console without sending emails
python main.py --dry-run

# Production run
python main.py

```

```

```