from __future__ import annotations

import requests

from main import (
    Document,
    download_pdf,
    documents_for_regular_run,
    factsheet_filename,
    newest_factsheet_document,
    parse_documents,
    results_filename,
    scrape_source,
)


def test_factsheet_monthly_letter_filename() -> None:
    filename, year, month = factsheet_filename("Factsheet & Quarterly Letter - March 2026")

    assert filename == "2603 RTW Report_Letter.pdf"
    assert (year, month) == (2026, 3)


def test_factsheet_quarter_filename() -> None:
    filename, year, month = factsheet_filename("Factsheet Q4 2022")

    assert filename == "2212 RTW Report.pdf"
    assert (year, month) == (2022, 12)


def test_results_filename_interim_and_annual() -> None:
    interim, interim_year, interim_month = results_filename("2025 Interim Results Presentation", "11.09.25")
    annual, annual_year, annual_month = results_filename("Full Year 2022 Results", "31.12.22")

    assert interim == "2506 Interim Results Presentation.pdf"
    assert (interim_year, interim_month) == (2025, 6)
    assert annual == "2212 Full Year Results.pdf"
    assert (annual_year, annual_month) == (2022, 12)


def test_results_filename_strips_multiple_years() -> None:
    filename, year, month = results_filename("2023 Annual Review + 2024 Outlook Presentation", "31.12.23")

    assert filename == "2312 Annual Review + Outlook Presentation.pdf"
    assert (year, month) == (2023, 12)


def test_parse_documents_uses_nearby_pdf_links_and_ignores_non_pdfs() -> None:
    html = """
    <main>
      <p>15.04.26</p>
      <h2>Factsheet & Quarterly Letter - March 2026</h2>
      <a href="/media/report.pdf">Factsheet [2.05Mb PDF]</a>
      <p>11.09.25</p>
      <h2>2025 Interim Results Webinar</h2>
      <a href="https://example.com/watch">Watch</a>
    </main>
    """

    docs = parse_documents(html, "factsheets_letters", "https://www.rtwfunds.com/source/", "https://www.rtwfunds.com/source/")

    assert len(docs) == 1
    assert docs[0].generated_filename == "2603 RTW Report_Letter.pdf"
    assert docs[0].listing_date == "15.04.26"
    assert docs[0].url == "https://www.rtwfunds.com/media/report.pdf"


class FakeHtmlResponse:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


class FakeHtmlSession:
    def __init__(self, pages: list[str]) -> None:
        self.pages = pages

    def get(self, url: str, params: dict | None = None, timeout: int = 30) -> FakeHtmlResponse:
        return FakeHtmlResponse(self.pages.pop(0))


def test_scrape_source_deduplicates_repeated_featured_pdf() -> None:
    first_page = """
    <main>
      <p>30.03.26</p>
      <h2>2025 Annual Report and Audited Financial Statements</h2>
      <a href="/media/annual.pdf">Download [4.44Mb PDF]</a>
      <p>30.03.26</p>
      <h2>2025 Annual Report and Audited Financial Statements</h2>
      <a href="/media/annual.pdf">Download [4.44Mb PDF]</a>
    </main>
    """
    second_page = "<main></main>"

    docs = scrape_source(FakeHtmlSession([first_page, second_page]), "results_presentations", "https://www.rtwfunds.com/source/")

    assert len(docs) == 1
    assert docs[0].url == "https://www.rtwfunds.com/media/annual.pdf"


def test_regular_run_filters_known_urls() -> None:
    doc = Document(
        source_page="factsheets_letters",
        source_url="https://example.com/source",
        title="Factsheet - January 2025",
        listing_date="14.02.25",
        url="https://example.com/jan.pdf",
        generated_filename="2501 RTW Report.pdf",
        reference_year=2025,
        reference_month=1,
    )
    state = {"documents": [{"url": "https://example.com/jan.pdf"}]}

    assert documents_for_regular_run([doc], state) == []


def test_bootstrap_selects_newest_factsheet() -> None:
    older = Document(
        source_page="factsheets_letters",
        source_url="https://example.com/source",
        title="Factsheet - February 2026",
        listing_date="13.03.26",
        url="https://example.com/feb.pdf",
        generated_filename="2602 RTW Report.pdf",
        reference_year=2026,
        reference_month=2,
    )
    newer = Document(
        source_page="factsheets_letters",
        source_url="https://example.com/source",
        title="Factsheet & Quarterly Letter - March 2026",
        listing_date="15.04.26",
        url="https://example.com/mar.pdf",
        generated_filename="2603 RTW Report_Letter.pdf",
        reference_year=2026,
        reference_month=3,
    )

    assert newest_factsheet_document([older, newer]) == newer


class FakeResponse:
    def __init__(self, status_code: int, content: bytes = b"", error: requests.HTTPError | None = None) -> None:
        self.status_code = status_code
        self.content = content
        self._error = error

    def raise_for_status(self) -> None:
        if self._error:
            raise self._error


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.calls = 0

    def get(self, url: str, timeout: int) -> FakeResponse:
        self.calls += 1
        return self.responses.pop(0)


def test_download_pdf_retries_transient_status() -> None:
    sleeps: list[int] = []
    session = FakeSession([FakeResponse(503), FakeResponse(200, b"pdf")])

    data = download_pdf(session, "https://example.com/report.pdf", delay_seconds=1, sleep_func=sleeps.append)

    assert data == b"pdf"
    assert session.calls == 2
    assert sleeps == [1]
