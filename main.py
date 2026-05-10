from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag


RESULTS_URL = "https://www.rtwfunds.com/rtw-biotech-opportunities-ltd/results-presentations/"
FACTS_URL = "https://www.rtwfunds.com/rtw-biotech-opportunities-ltd/factsheets-letters/"
DEFAULT_STATE_FILE = Path("sent_documents.json")
DEFAULT_OUTPUT_DIR = Path("output")
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
RETRY_STATUSES = {429, 500, 502, 503, 504}

MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

QUARTER_MONTHS = {
    "1": 3,
    "2": 6,
    "3": 9,
    "4": 12,
}

SOURCE_LABELS = {
    "factsheets_letters": "Factsheets & Letters",
    "results_presentations": "Results & Presentations",
}


@dataclass(frozen=True)
class Document:
    source_page: str
    source_url: str
    title: str
    listing_date: str | None
    url: str
    generated_filename: str
    reference_year: int
    reference_month: int


@dataclass(frozen=True)
class EmailConfig:
    api_key: str
    from_email: str
    to_emails: list[str]


def log(message: str) -> None:
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    print(f"[{timestamp}] {message}", flush=True)


def source_label(source_page: str) -> str:
    return SOURCE_LABELS.get(source_page, source_page)


def normalize_text(value: str) -> str:
    value = value.replace("\u2013", "-").replace("\u2014", "-")
    value = value.replace("\xa0", " ")
    return re.sub(r"\s+", " ", value).strip()


def sanitize_filename_part(value: str) -> str:
    value = normalize_text(value)
    value = re.sub(r'[<>:"/\\|?*]', "", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip(" .-_")


def parse_listing_date(value: str | None) -> datetime | None:
    if not value:
        return None
    match = re.search(r"\b(\d{2})\.(\d{2})\.(\d{2})\b", value)
    if not match:
        return None
    day, month, year = (int(part) for part in match.groups())
    return datetime(2000 + year, month, day, tzinfo=timezone.utc)


def year_month_prefix(year: int, month: int) -> str:
    return f"{year % 100:02d}{month:02d}"


def factsheet_filename(title: str) -> tuple[str, int, int]:
    normalized = normalize_text(title)
    lowered = normalized.lower()

    month_match = re.search(
        r"\b("
        + "|".join(MONTHS.keys())
        + r")\s+((?:19|20)\d{2})\b",
        lowered,
        flags=re.IGNORECASE,
    )
    quarter_match = re.search(r"\bq([1-4])\s+((?:19|20)\d{2})\b", lowered, flags=re.IGNORECASE)

    if month_match:
        month = MONTHS[month_match.group(1).lower()]
        year = int(month_match.group(2))
    elif quarter_match:
        month = QUARTER_MONTHS[quarter_match.group(1)]
        year = int(quarter_match.group(2))
    else:
        raise ValueError(f"Cannot determine factsheet reference period from title: {title}")

    suffix = "Report_Letter" if "letter" in lowered else "Report"
    return f"{year_month_prefix(year, month)} RTW {suffix}.pdf", year, month


def results_filename(title: str, listing_date: str | None) -> tuple[str, int, int]:
    normalized = normalize_text(title)
    lowered = normalized.lower()
    years = [int(year) for year in re.findall(r"\b((?:19|20)\d{2})\b", normalized)]

    if years:
        year = years[0]
    else:
        parsed_date = parse_listing_date(listing_date)
        if parsed_date is None:
            raise ValueError(f"Cannot determine results reference year from title: {title}")
        year = parsed_date.year

    month = 6 if "interim" in lowered else 12
    cleaned = re.sub(r"\b(?:19|20)\d{2}\b", "", normalized)
    cleaned = sanitize_filename_part(cleaned)
    if not cleaned:
        raise ValueError(f"Cannot determine results filename from title: {title}")

    return f"{year_month_prefix(year, month)} {cleaned}.pdf", year, month


def filename_for_document(source_page: str, title: str, listing_date: str | None) -> tuple[str, int, int]:
    if source_page == "factsheets_letters":
        return factsheet_filename(title)
    if source_page == "results_presentations":
        return results_filename(title, listing_date)
    raise ValueError(f"Unknown source page: {source_page}")


def is_pdf_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.path.lower().endswith(".pdf")


def closest_previous_listing_date(heading: Tag) -> str | None:
    date_pattern = re.compile(r"\b\d{2}\.\d{2}\.\d{2}\b")
    previous = heading.find_previous(string=date_pattern)
    if previous is None:
        return None
    match = date_pattern.search(str(previous))
    return match.group(0) if match else None


def find_pdf_link_after_heading(heading: Tag, page_url: str) -> str | None:
    for sibling in heading.next_elements:
        if sibling is heading:
            continue
        if isinstance(sibling, Tag) and sibling.name == "h2":
            return None
        if isinstance(sibling, Tag) and sibling.name == "a" and sibling.get("href"):
            candidate = urljoin(page_url, str(sibling["href"]))
            link_text = normalize_text(sibling.get_text(" "))
            if is_pdf_url(candidate) or "pdf" in link_text.lower():
                return candidate
    return None


def parse_documents(html: str, source_page: str, source_url: str, page_url: str) -> list[Document]:
    soup = BeautifulSoup(html, "html.parser")
    documents: list[Document] = []

    for heading in soup.find_all("h2"):
        title = normalize_text(heading.get_text(" "))
        if not title:
            continue

        pdf_url = find_pdf_link_after_heading(heading, page_url)
        if not pdf_url or not is_pdf_url(pdf_url):
            continue

        listing_date = closest_previous_listing_date(heading)
        filename, year, month = filename_for_document(source_page, title, listing_date)
        documents.append(
            Document(
                source_page=source_page,
                source_url=source_url,
                title=title,
                listing_date=listing_date,
                url=pdf_url,
                generated_filename=filename,
                reference_year=year,
                reference_month=month,
            )
        )

    return documents


def make_http_session() -> Any:
    try:
        from curl_cffi import requests as curl_requests
    except ImportError:
        session = requests.Session()
    else:
        session = curl_requests.Session(impersonate="chrome")

    session.headers.update(DEFAULT_HEADERS)
    return session


def fetch_html(session: Any, url: str, params: dict[str, Any] | None = None) -> str:
    response = session.get(url, params=params, timeout=30)
    response.raise_for_status()
    return response.text


def scrape_source(
    session: Any,
    source_page: str,
    source_url: str,
    max_pages: int = 20,
) -> list[Document]:
    documents: list[Document] = []
    seen_urls: set[str] = set()
    label = source_label(source_page)
    log(f"Scraping {label}: {source_url}")

    for page in range(1, max_pages + 1):
        params: dict[str, Any] | None = None
        if page > 1:
            params = {"page": page, "query": "", "year": 0}
            if source_page == "results_presentations":
                params["type"] = ""

        log(f"Fetching {label} page {page}")
        html = fetch_html(session, source_url, params=params)
        page_url = requests.Request("GET", source_url, params=params).prepare().url or source_url
        page_documents = parse_documents(html, source_page, source_url, page_url)
        page_seen_urls: set[str] = set()
        new_page_documents = []
        for doc in page_documents:
            if doc.url in seen_urls or doc.url in page_seen_urls:
                continue
            page_seen_urls.add(doc.url)
            new_page_documents.append(doc)

        if not new_page_documents:
            log(f"No new unique PDFs found on {label} page {page}; stopping pagination.")
            break

        log(f"Found {len(new_page_documents)} unique PDFs on {label} page {page}.")
        for doc in new_page_documents:
            seen_urls.add(doc.url)
            documents.append(doc)

    log(f"Finished {label}: {len(documents)} unique PDFs discovered.")
    return documents


def scrape_all_documents() -> list[Document]:
    log("Starting RTW document scrape.")
    session = make_http_session()
    documents = []
    documents.extend(scrape_source(session, "factsheets_letters", FACTS_URL))
    documents.extend(scrape_source(session, "results_presentations", RESULTS_URL))
    log(f"Scrape complete: {len(documents)} total unique PDFs discovered.")
    return documents


def download_pdf(
    session: Any,
    url: str,
    retries: int = 3,
    delay_seconds: int = 5,
    sleep_func: Any = time.sleep,
) -> bytes:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            response = session.get(url, timeout=60)
            if response.status_code in RETRY_STATUSES and attempt < retries:
                sleep_func(delay_seconds)
                continue
            response.raise_for_status()
            return response.content
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                sleep_func(delay_seconds)
                continue
            raise

    if last_error:
        raise last_error
    raise RuntimeError(f"Failed to download PDF: {url}")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def output_path_for_filename(output_dir: Path, filename: str) -> Path:
    safe_name = Path(filename).name
    if not safe_name or safe_name != filename:
        raise ValueError(f"Unsafe output filename: {filename}")
    return output_dir / safe_name


def save_pdf_to_output(output_dir: Path, filename: str, pdf_bytes: bytes) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_path_for_filename(output_dir, filename)
    with path.open("wb") as file:
        file.write(pdf_bytes)
    return path


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"documents": []}
    if path.stat().st_size == 0:
        return {"documents": []}

    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    if isinstance(data, list):
        return {"documents": data}
    if isinstance(data, dict) and isinstance(data.get("documents"), list):
        return data
    raise ValueError(f"State file must contain an object with a documents list: {path}")


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        json.dump(state, file, indent=2, sort_keys=True)
        file.write("\n")
    temporary.replace(path)


def state_urls(state: dict[str, Any]) -> set[str]:
    return {entry["url"] for entry in state.get("documents", []) if isinstance(entry, dict) and entry.get("url")}


def state_status_counts(state: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in state.get("documents", []):
        if not isinstance(entry, dict):
            continue
        status = str(entry.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts


def is_empty_state(state: dict[str, Any]) -> bool:
    return len(state.get("documents", [])) == 0


def newest_factsheet_document(documents: list[Document]) -> Document:
    factsheets = [doc for doc in documents if doc.source_page == "factsheets_letters"]
    if not factsheets:
        raise ValueError("No Factsheets & Letters PDFs were discovered for bootstrap send.")
    return max(
        factsheets,
        key=lambda doc: (
            doc.reference_year,
            doc.reference_month,
            parse_listing_date(doc.listing_date) or datetime.min.replace(tzinfo=timezone.utc),
        ),
    )


def documents_for_regular_run(documents: list[Document], state: dict[str, Any]) -> list[Document]:
    known_urls = state_urls(state)
    return [doc for doc in documents if doc.url not in known_urls]


def make_state_entry(doc: Document, pdf_hash: str, status: str, sent_at: str | None = None) -> dict[str, Any]:
    entry = {
        "url": doc.url,
        "source_page": doc.source_page,
        "title": doc.title,
        "generated_filename": doc.generated_filename,
        "sha256": pdf_hash,
        "status": status,
        "timestamp": sent_at or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }
    if doc.listing_date:
        entry["listing_date"] = doc.listing_date
    return entry


def load_dotenv_if_available() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()


def email_config_from_env() -> EmailConfig:
    load_dotenv_if_available()
    api_key = os.getenv("RESEND_API_KEY", "").strip()
    from_email = os.getenv("RESEND_FROM_EMAIL", "").strip()
    to_emails = [email.strip() for email in os.getenv("RESEND_TO_EMAIL", "").split(",") if email.strip()]

    missing = []
    if not api_key:
        missing.append("RESEND_API_KEY")
    if not from_email:
        missing.append("RESEND_FROM_EMAIL")
    if not to_emails:
        missing.append("RESEND_TO_EMAIL")
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")

    return EmailConfig(api_key=api_key, from_email=from_email, to_emails=to_emails)


def send_pdf_email(config: EmailConfig, doc: Document, pdf_bytes: bytes) -> Any:
    import resend

    resend.api_key = config.api_key
    subject = doc.generated_filename.removesuffix(".pdf")
    attachment = base64.b64encode(pdf_bytes).decode("ascii")
    params: resend.Emails.SendParams = {
        "from": config.from_email,
        "to": config.to_emails,
        "subject": subject,
        "text": f"Attached: {doc.generated_filename}",
        "attachments": [
            {
                "filename": doc.generated_filename,
                "content": attachment,
            }
        ],
    }
    return resend.Emails.send(params)


def print_documents(label: str, documents: list[Document]) -> None:
    print(label)
    if not documents:
        print("  None")
        return
    for doc in documents:
        print(f"  - {doc.generated_filename} | {doc.title} | {doc.url}")


def run_dry_run(documents: list[Document], state: dict[str, Any]) -> int:
    print_documents("Discovered PDF documents:", documents)

    if is_empty_state(state):
        latest = newest_factsheet_document(documents)
        print()
        print("First-run bootstrap detected.")
        print(f"Would email only: {latest.generated_filename}")
        print(f"Would seed {len(documents)} discovered PDFs into state.")
        return 0

    new_documents = documents_for_regular_run(documents, state)
    print()
    print_documents("New PDF documents:", new_documents)
    return 0


def run_bootstrap(documents: list[Document], state_path: Path, email_config: EmailConfig, output_dir: Path) -> int:
    latest = newest_factsheet_document(documents)
    session = make_http_session()
    log("First production run detected: bootstrapping current archive.")
    log(f"Will email latest Factsheets & Letters document only: {latest.generated_filename}")
    log(f"Will seed {len(documents) - 1} historical documents and save seeded PDFs under {output_dir}.")

    pdf_cache: dict[str, bytes] = {}
    hash_cache: dict[str, str] = {}
    for index, doc in enumerate(documents, start=1):
        log(f"Downloading {index}/{len(documents)}: {doc.generated_filename}")
        pdf_bytes = download_pdf(session, doc.url)
        pdf_cache[doc.url] = pdf_bytes
        hash_cache[doc.url] = sha256_hex(pdf_bytes)
        log(f"Downloaded {doc.generated_filename} ({len(pdf_bytes)} bytes, sha256={hash_cache[doc.url][:12]}...).")

    log(f"Sending bootstrap email: {latest.generated_filename}")
    send_pdf_email(email_config, latest, pdf_cache[latest.url])
    log(f"Email sent: {latest.generated_filename}")

    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    state = {"documents": []}
    for doc in documents:
        status = "sent" if doc.url == latest.url else "seeded"
        if status == "seeded":
            path = save_pdf_to_output(output_dir, doc.generated_filename, pdf_cache[doc.url])
            log(f"Saved seeded PDF: {path}")
        state["documents"].append(make_state_entry(doc, hash_cache[doc.url], status, sent_at=timestamp))

    log(f"Writing bootstrap state to {state_path}.")
    save_state(state_path, state)
    print(f"Bootstrap complete. Sent {latest.generated_filename} and seeded {len(documents) - 1} documents.")
    return 0


def download_seeded_from_state(state: dict[str, Any], output_dir: Path) -> int:
    seeded_entries = [
        entry
        for entry in state.get("documents", [])
        if isinstance(entry, dict) and entry.get("status") == "seeded"
    ]
    if not seeded_entries:
        print("No seeded documents found in state.")
        return 0

    log(f"Backfilling {len(seeded_entries)} seeded PDFs into {output_dir}.")
    session = make_http_session()
    downloaded = 0
    skipped = 0

    for entry in seeded_entries:
        filename = str(entry.get("generated_filename", "")).strip()
        url = str(entry.get("url", "")).strip()
        expected_hash = str(entry.get("sha256", "")).strip()
        if not filename or not url:
            raise ValueError(f"Seeded state entry is missing generated_filename or url: {entry}")

        path = output_path_for_filename(output_dir, filename)
        if path.exists() and (not expected_hash or sha256_hex(path.read_bytes()) == expected_hash):
            log(f"Skipping existing seeded PDF: {path}")
            skipped += 1
            continue

        log(f"Downloading seeded PDF: {filename}")
        pdf_bytes = download_pdf(session, url)
        actual_hash = sha256_hex(pdf_bytes)
        if expected_hash and actual_hash != expected_hash:
            raise ValueError(f"Downloaded hash mismatch for {filename}: expected {expected_hash}, got {actual_hash}")

        save_pdf_to_output(output_dir, filename, pdf_bytes)
        downloaded += 1
        print(f"Saved {path}")

    print(f"Seeded PDF backfill complete. Downloaded {downloaded}, skipped {skipped}.")
    return 0


def run_regular(documents: list[Document], state: dict[str, Any], state_path: Path, email_config: EmailConfig) -> int:
    new_documents = documents_for_regular_run(documents, state)
    log(f"Regular run: {len(documents)} PDFs discovered, {len(state_urls(state))} URLs already in state.")
    if not new_documents:
        log("No new PDF documents found. Nothing to send.")
        return 0

    log(f"Found {len(new_documents)} new PDF documents to process.")
    session = make_http_session()
    failures = 0

    for index, doc in enumerate(new_documents, start=1):
        try:
            log(f"Processing new document {index}/{len(new_documents)}: {doc.generated_filename}")
            log(f"Downloading PDF: {doc.url}")
            pdf_bytes = download_pdf(session, doc.url)
            pdf_hash = sha256_hex(pdf_bytes)
            log(f"Downloaded {doc.generated_filename} ({len(pdf_bytes)} bytes, sha256={pdf_hash[:12]}...).")
            log(f"Sending email: {doc.generated_filename}")
            send_pdf_email(email_config, doc, pdf_bytes)
            log(f"Email sent: {doc.generated_filename}")
            state["documents"].append(make_state_entry(doc, pdf_hash, "sent"))
            log(f"Updating state file: {state_path}")
            save_state(state_path, state)
            print(f"Sent {doc.generated_filename}")
        except Exception as exc:
            failures += 1
            print(f"Failed to process {doc.generated_filename}: {exc}", file=sys.stderr)

    if failures:
        log(f"Run completed with {failures} failures.")
    else:
        log(f"Run completed successfully. Sent {len(new_documents)} new documents.")
    return 1 if failures else 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scrape RTW PDFs and email newly published documents.")
    parser.add_argument("--dry-run", action="store_true", help="Scrape and print generated filenames without sending emails or writing state.")
    parser.add_argument("--state-file", default=str(DEFAULT_STATE_FILE), help="Path to the JSON state file.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory for locally saved seeded PDFs.")
    parser.add_argument(
        "--download-seeded",
        action="store_true",
        help="Download documents already marked as seeded in state into the output directory without sending emails.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    state_path = Path(args.state_file)

    try:
        state = load_state(state_path)
        counts = state_status_counts(state)
        count_text = ", ".join(f"{status}={count}" for status, count in sorted(counts.items())) or "empty"
        log(f"Loaded state from {state_path}: {len(state.get('documents', []))} documents ({count_text}).")
        if args.download_seeded:
            return download_seeded_from_state(state, Path(args.output_dir))

        documents = scrape_all_documents()

        if args.dry_run:
            return run_dry_run(documents, state)

        email_config = email_config_from_env()
        if is_empty_state(state):
            return run_bootstrap(documents, state_path, email_config, Path(args.output_dir))
        return run_regular(documents, state, state_path, email_config)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
