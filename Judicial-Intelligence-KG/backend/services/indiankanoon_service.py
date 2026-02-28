from __future__ import annotations

import os
import random
import re
import time
import logging
from dataclasses import dataclass
from urllib.parse import quote_plus, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)


@dataclass
class ExternalSearchResult:
    rank: int
    title: str
    snippet: str
    result_url: str
    court: str | None
    date: str | None
    document_url: str | None


class IndianKanoonService:
    BASE_URL = "https://indiankanoon.org"
    API_BASE_URL = "https://api.indiankanoon.org"

    def __init__(self) -> None:
        self.logger = logging.getLogger("indiankanoon_service")
        self.timeout = httpx.Timeout(20.0, connect=10.0)
        self.use_api = os.getenv("INDIAKANOON_USE_API", "1").strip().lower() in {
            "1",
            "true",
            "yes",
        }
        self.default_doctypes = os.getenv("INDIAKANOON_DOCTYPES", "judgments").strip()
        self.api_token = os.getenv("INDIAKANOON_API_TOKEN", "").strip()
        self.api_auth_header = os.getenv("INDIAKANOON_AUTH_HEADER", "").strip()

    def search(self, keyword: str, limit: int = 10) -> list[ExternalSearchResult]:
        if self.use_api:
            try:
                self.logger.info("Indiakanoon API search start keyword='%s' limit=%s", keyword, limit)
                return self._search_via_api(keyword, limit)
            except Exception:
                # Keep the pipeline robust even if API auth/format changes.
                self.logger.warning(
                    "Indiakanoon API search failed for keyword='%s', falling back to scrape",
                    keyword,
                )
                pass

        self.logger.info("Indiakanoon scrape search start keyword='%s' limit=%s", keyword, limit)
        return self._search_via_scrape(keyword, limit)

    def _search_via_api(self, keyword: str, limit: int) -> list[ExternalSearchResult]:
        maxpages = max(1, min(1000, (limit + 9) // 10))
        params = {
            "formInput": keyword,
            "doctypes": self.default_doctypes,
            "pagenum": 0,
            "maxpages": maxpages,
            "maxcites": 5,
        }
        url = f"{self.API_BASE_URL}/search/"
        payload = self._fetch_json(url, params=params)
        rows = self._parse_api_results(payload, limit=limit)
        results: list[ExternalSearchResult] = []
        for item in rows:
            result_url = item["result_url"]
            document_url = item.get("document_url")
            if not document_url:
                document_url = self._try_extract_document_url(result_url)
            results.append(
                ExternalSearchResult(
                    rank=item["rank"],
                    title=item["title"],
                    snippet=item["snippet"],
                    result_url=result_url,
                    court=item["court"],
                    date=item["date"],
                    document_url=document_url,
                )
            )
            time.sleep(0.12 + random.random() * 0.08)
        return results

    def _search_via_scrape(self, keyword: str, limit: int) -> list[ExternalSearchResult]:
        url = self._query_url(keyword)
        html = self._fetch(url)
        parsed = self._parse_results(html, limit=limit)
        results: list[ExternalSearchResult] = []
        for item in parsed:
            document_url = self._try_extract_document_url(item["result_url"])
            results.append(
                ExternalSearchResult(
                    rank=item["rank"],
                    title=item["title"],
                    snippet=item["snippet"],
                    result_url=item["result_url"],
                    court=item["court"],
                    date=item["date"],
                    document_url=document_url,
                )
            )
            # Respectful pacing for external site.
            time.sleep(0.2 + random.random() * 0.15)
        return results

    def _query_url(self, keyword: str) -> str:
        return f"{self.BASE_URL}/search/?formInput={quote_plus(keyword)}"

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
        reraise=True,
    )
    def _fetch(self, url: str) -> str:
        with httpx.Client(timeout=self.timeout, headers={"User-Agent": USER_AGENT}) as client:
            response = client.get(url, follow_redirects=True)
            response.raise_for_status()
            return response.text

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
        reraise=True,
    )
    def _fetch_json(self, url: str, params: dict) -> dict:
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if self.api_auth_header:
            headers["Authorization"] = self.api_auth_header
        elif self.api_token:
            headers["Authorization"] = f"Token {self.api_token}"

        with httpx.Client(timeout=self.timeout, headers=headers) as client:
            response = client.get(url, params=params, follow_redirects=True)
            response.raise_for_status()
            return response.json()

    def _parse_api_results(self, payload: dict, limit: int) -> list[dict[str, str | int | None]]:
        if not isinstance(payload, dict):
            return []

        raw_items = []
        for key in ["docs", "results", "data", "documents"]:
            value = payload.get(key)
            if isinstance(value, list):
                raw_items = value
                break
        if not raw_items and isinstance(payload.get("result"), list):
            raw_items = payload["result"]

        rows: list[dict[str, str | int | None]] = []
        for idx, item in enumerate(raw_items[:limit], start=1):
            if not isinstance(item, dict):
                continue
            title = (
                item.get("title")
                or item.get("docTitle")
                or item.get("headline")
                or item.get("name")
                or "Untitled"
            )
            snippet = (
                item.get("snippet")
                or item.get("headline")
                or item.get("doc")
                or item.get("fragment")
                or ""
            )
            result_url = (
                item.get("result_url")
                or item.get("url")
                or item.get("docUrl")
                or item.get("sourceUrl")
                or ""
            )
            if not result_url and item.get("tid"):
                result_url = urljoin(self.BASE_URL, f"/doc/{item['tid']}/")
            if not result_url and item.get("docid"):
                result_url = urljoin(self.BASE_URL, f"/doc/{item['docid']}/")

            result_url = urljoin(self.BASE_URL, str(result_url))
            if not self._is_http(result_url):
                continue

            document_url = (
                item.get("document_url")
                or item.get("pdfUrl")
                or item.get("downloadUrl")
                or None
            )
            if document_url:
                document_url = urljoin(self.BASE_URL, str(document_url))

            court = (
                item.get("court")
                or item.get("docsource")
                or item.get("author")
                or None
            )
            date = item.get("date") or item.get("publishdate") or item.get("docdate") or None

            rows.append(
                {
                    "rank": idx,
                    "title": " ".join(str(title).split())[:280],
                    "snippet": " ".join(str(snippet).split())[:900],
                    "result_url": result_url,
                    "document_url": document_url,
                    "court": str(court).strip() if court else None,
                    "date": str(date).strip() if date else None,
                }
            )
        return rows

    def _parse_results(self, html: str, limit: int) -> list[dict[str, str | int | None]]:
        soup = BeautifulSoup(html, "html.parser")
        # Primary selector used by Indiankanoon listing.
        candidates = soup.select("div.result")
        if not candidates:
            # Fallback if layout changes.
            candidates = soup.select("div.searchresult, article")

        rows: list[dict[str, str | int | None]] = []
        for idx, block in enumerate(candidates[:limit], start=1):
            anchor = block.select_one("a[href]")
            if not anchor:
                continue
            href = anchor.get("href", "").strip()
            if not href:
                continue
            result_url = urljoin(self.BASE_URL, href)
            title = " ".join(anchor.get_text(" ", strip=True).split()) or "Untitled"
            snippet_node = block.select_one(".snippet, .headline, .result_title + p, p")
            snippet = " ".join((snippet_node.get_text(" ", strip=True) if snippet_node else "").split())
            court, date = self._extract_meta(block.get_text(" ", strip=True))
            rows.append(
                {
                    "rank": idx,
                    "title": title[:280],
                    "snippet": snippet[:900],
                    "result_url": result_url,
                    "court": court,
                    "date": date,
                }
            )
        return rows

    def _extract_meta(self, text: str) -> tuple[str | None, str | None]:
        text = " ".join((text or "").split())
        # Very loose patterns to avoid brittleness.
        date_match = re.search(
            r"\b(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b",
            text,
        )
        date = date_match.group(1) if date_match else None
        # Assume court appears near "Court" or "Bench" markers.
        court_match = re.search(r"([A-Za-z\s]{3,60}(Court|Bench))", text)
        court = court_match.group(1).strip() if court_match else None
        return court, date

    def _try_extract_document_url(self, result_url: str) -> str | None:
        try:
            html = self._fetch(result_url)
        except Exception:
            return None
        soup = BeautifulSoup(html, "html.parser")
        for anchor in soup.select("a[href]"):
            href = anchor.get("href", "").strip()
            text = " ".join(anchor.get_text(" ", strip=True).lower().split())
            if ".pdf" in href.lower() or "pdf" in text or "download" in text:
                full = urljoin(result_url, href)
                if self._is_http(full):
                    return full
        return None

    def _is_http(self, url: str) -> bool:
        parsed = urlparse(url)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
