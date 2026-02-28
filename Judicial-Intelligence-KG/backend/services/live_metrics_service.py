from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx


class LiveMetricsService:
    """Fetches near-real-time legal activity signals from public web data."""

    def __init__(self) -> None:
        self.logger = logging.getLogger("live_metrics_service")
        self.timeout = httpx.Timeout(20.0, connect=8.0)
        self._cache: dict[str, Any] = {}
        self.court_scope = '("Chennai High Court" OR "Madras High Court" OR "High Court of Madras")'

    def fetch_summary(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        cache_ts = self._cache.get("ts")
        if isinstance(cache_ts, datetime) and (now - cache_ts) < timedelta(minutes=20):
            cached = self._cache.get("payload")
            if isinstance(cached, dict):
                return cached

        yesterday_start, yesterday_end = self._yesterday_window_utc()
        start_label = yesterday_start.strftime("%Y-%m-%d")
        end_label = yesterday_end.strftime("%Y-%m-%d")

        metrics = [
            {
                "id": "appeared_yesterday",
                "label": "Appeared In Court (Yesterday)",
                "query": f"{self.court_scope} AND (hearing OR listed OR appeared OR admitted) AND case",
            },
            {
                "id": "resolved_yesterday",
                "label": "Resolved Yesterday",
                "query": (
                    f"{self.court_scope} AND "
                    '(disposed OR "disposed of" OR resolved OR "final order" OR judgment OR conviction OR acquitted) '
                    "AND case"
                ),
            },
            {
                "id": "delayed_time_yesterday",
                "label": "Pushed Due To Delay",
                "query": f"{self.court_scope} AND (adjourned OR delayed OR backlog OR congestion) AND case",
            },
            {
                "id": "next_hearing_yesterday",
                "label": "Pushed To Next Hearing",
                "query": f'{self.court_scope} AND ("next hearing" OR adjourned OR "posted to") AND case',
            },
        ]

        for item in metrics:
            y_count = self._fetch_count_from_gdelt(
                query=item["query"],
                start_utc=yesterday_start,
                end_utc=yesterday_end,
            )
            item["count"] = y_count
            item["rolling_3d_count"] = None
            item["using_rolling_hint"] = False
            if y_count == 0:
                rolling_start = yesterday_start - timedelta(days=2)
                rolling_count = self._fetch_count_from_gdelt(
                    query=item["query"],
                    start_utc=rolling_start,
                    end_utc=yesterday_end,
                )
                item["rolling_3d_count"] = rolling_count
                item["using_rolling_hint"] = isinstance(rolling_count, int) and rolling_count > 0

        payload = {
            "date_range": f"{start_label} to {end_label}",
            "as_of_utc": now.isoformat(),
            "source": "GDELT public web index (signal-based estimate)",
            "scope": "Chennai High Court",
            "metrics": metrics,
            "note": (
                "These are live public-web signal counts, not official judiciary final totals. "
                "Use for trend awareness."
            ),
        }
        self._cache = {"ts": now, "payload": payload}
        return payload

    def _fetch_count_from_gdelt(
        self,
        *,
        query: str,
        start_utc: datetime,
        end_utc: datetime,
    ) -> int | None:
        params = {
            "query": query,
            "mode": "ArtList",
            "format": "json",
            "maxrecords": "250",
            "startdatetime": start_utc.strftime("%Y%m%d%H%M%S"),
            "enddatetime": end_utc.strftime("%Y%m%d%H%M%S"),
        }
        url = "https://api.gdeltproject.org/api/v2/doc/doc?" + urlencode(params)
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(url)
                response.raise_for_status()
                data = response.json()
            articles = data.get("articles", [])
            if not isinstance(articles, list):
                return 0
            return len(articles)
        except Exception as exc:
            self.logger.warning("Live metrics query failed for '%s': %s", query, exc)
            return None

    def _yesterday_window_utc(self) -> tuple[datetime, datetime]:
        now = datetime.now(timezone.utc)
        today_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
        yesterday_start = today_start - timedelta(days=1)
        yesterday_end = today_start - timedelta(seconds=1)
        return yesterday_start, yesterday_end
