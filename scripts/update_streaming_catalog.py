#!/usr/bin/env python3
"""Build the Optimus streaming catalog JSON.

The script is designed for GitHub Actions, but it also runs locally. It avoids
hardcoded secrets: set TMDB_READ_TOKEN for TMDB ids and recently added lists.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import requests


TMDB_BASE_URL = "https://api.themoviedb.org/3"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

SERVICES = {
    "netflix": {
        "name": "Netflix",
        "provider_id": "8",
        "flixpatrol": "netflix",
        "regions": ("US", "CA", "GB", "AU", "NZ", "IE", "DE", "FR", "ES", "IT", "NL", "SE", "NO", "DK", "FI", "BR", "MX", "JP", "KR", "IN"),
    },
    "prime": {
        "name": "Prime Video",
        "provider_id": "9",
        "flixpatrol": "amazon-prime",
        "regions": ("US", "CA", "GB", "AU", "NZ", "IE", "DE", "FR", "ES", "IT", "NL", "SE", "NO", "DK", "FI", "BR", "MX", "JP", "IN"),
    },
    "disney": {
        "name": "Disney+",
        "provider_id": "337",
        "flixpatrol": "disney",
        "regions": ("US", "CA", "GB", "AU", "NZ", "IE", "DE", "FR", "ES", "IT", "NL", "SE", "NO", "DK", "FI", "BR", "MX", "JP", "KR", "IN"),
    },
    "hulu": {
        "name": "Hulu",
        "provider_id": "15",
        "flixpatrol": "hulu",
        "regions": ("US",),
    },
    "paramount": {
        "name": "Paramount+",
        "provider_id": "531",
        "flixpatrol": "paramount-plus",
        "regions": ("US", "CA", "GB", "AU", "IE", "DE", "FR", "IT", "BR", "MX"),
    },
    "crave": {
        "name": "Crave",
        "provider_id": "230",
        "flixpatrol": "",
        "regions": ("CA",),
    },
}

COUNTRY_SLUGS = {
    "US": "united-states",
    "CA": "canada",
    "GB": "united-kingdom",
    "AU": "australia",
    "NZ": "new-zealand",
    "IE": "ireland",
    "DE": "germany",
    "FR": "france",
    "ES": "spain",
    "IT": "italy",
    "NL": "netherlands",
    "SE": "sweden",
    "NO": "norway",
    "DK": "denmark",
    "FI": "finland",
    "BR": "brazil",
    "MX": "mexico",
    "JP": "japan",
    "KR": "south-korea",
    "IN": "india",
}


class CatalogBuilder:
    def __init__(self, tmdb_read_token: str = "", tmdb_api_key: str = "") -> None:
        self.tmdb_read_token = tmdb_read_token.strip()
        self.tmdb_api_key = tmdb_api_key.strip()
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
            "Referer": "https://flixpatrol.com/top10/",
        })
        self._tmdb_cache: dict[tuple[str, str], dict[str, Any] | None] = {}

    @property
    def has_tmdb(self) -> bool:
        return bool(self.tmdb_read_token or self.tmdb_api_key)

    def build(self, service_filter: set[str] | None, region_filter: set[str] | None, skip_recent: bool) -> dict[str, Any]:
        payload = {
            "schema": 1,
            "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "source": {
                "top10": "flixpatrol",
                "recently_added": "tmdb_discover",
            },
            "services": {},
        }

        for service_slug, service in SERVICES.items():
            if service_filter and service_slug not in service_filter:
                continue
            service_payload: dict[str, Any] = {
                "name": service["name"],
                "provider_id": service["provider_id"],
                "regions": {},
            }
            for region in service["regions"]:
                if region_filter and region not in region_filter:
                    continue
                print(f"Building {service_slug}/{region}", flush=True)
                region_payload = {
                    "top_movies": self.top10(service, region, "movie"),
                    "top_shows": self.top10(service, region, "tvshow"),
                    "recently_added": [] if skip_recent else self.recently_added(service, region),
                }
                service_payload["regions"][region] = region_payload
            payload["services"][service_slug] = service_payload
        return payload

    def top10(self, service: dict[str, Any], region: str, media_type: str) -> list[dict[str, Any]]:
        flix_service = service.get("flixpatrol")
        country = COUNTRY_SLUGS.get(region)
        if not flix_service or not country:
            return []

        list_type = "movies" if media_type == "movie" else "shows"
        today = date.today()
        entries: list[dict[str, str]] = []
        source_date = ""
        for day in (today, today - timedelta(days=1), today - timedelta(days=2)):
            source_date = day.isoformat()
            entries = self._flixpatrol_titles(flix_service, country, source_date, list_type)
            if entries:
                break
        results = []
        for rank, entry in enumerate(entries[:10], start=1):
            title = entry["title"]
            tmdb_item = self._tmdb_item_for_title(media_type, title)
            results.append({
                "rank": rank,
                "media_type": media_type,
                "title": tmdb_item.get("title") if tmdb_item else title,
                "tmdb_id": tmdb_item.get("tmdb_id") if tmdb_item else None,
                "source_title": title,
                "source_slug": entry.get("slug"),
                "source_date": source_date,
            })
        return results

    def recently_added(self, service: dict[str, Any], region: str) -> list[dict[str, Any]]:
        if not self.has_tmdb:
            return []
        movies = self._tmdb_discover(service, region, "movie", limit=12)
        shows = self._tmdb_discover(service, region, "tvshow", limit=12)
        interleaved: list[dict[str, Any]] = []
        for index in range(max(len(movies), len(shows))):
            if index < len(movies):
                interleaved.append(movies[index])
            if index < len(shows):
                interleaved.append(shows[index])
        return interleaved[:20]

    def _flixpatrol_titles(self, service: str, country: str, source_date: str, list_type: str) -> list[dict[str, str]]:
        url = f"https://flixpatrol.com/top10/{service}/{country}/{source_date}/"
        page = self._fetch_page(url)
        if not page:
            return []
        marker = "TOP 10 Movies" if list_type == "movies" else "TOP 10 TV Shows"
        start = page.find(marker)
        if start < 0:
            return []
        end = self._section_end(page, start)
        chunk = page[start:end]
        return (self._html_titles(chunk) or self._markdown_titles(chunk))[:10]

    def _fetch_page(self, url: str) -> str:
        for fetcher in (self._fetch_direct, self._fetch_reader):
            page = fetcher(url)
            if page:
                return page
        return ""

    def _fetch_direct(self, url: str) -> str:
        try:
            response = self.session.get(url, timeout=8)
            if response.status_code == 404:
                return ""
            if response.ok:
                return response.text
        except requests.RequestException as exc:
            print(f"Direct fetch failed: {url} | {exc}", file=sys.stderr)
        return ""

    def _fetch_reader(self, url: str) -> str:
        reader_url = f"https://r.jina.ai/http://{url}"
        try:
            response = self.session.get(reader_url, timeout=25)
            if response.ok:
                return response.text
        except requests.RequestException as exc:
            print(f"Reader fetch failed: {url} | {exc}", file=sys.stderr)
        return ""

    @staticmethod
    def _section_end(page: str, start: int) -> int:
        table_end = page.find("</table>", start)
        if table_end > start:
            return table_end
        next_section = page.find("\n### TOP 10 ", start + 10)
        if next_section > start:
            return next_section
        return start + 12000

    @staticmethod
    def _html_titles(chunk: str) -> list[dict[str, str]]:
        entries = []
        seen = set()
        for slug, value in re.findall(r'<a\b[^>]*href="/title/([^"/]+)/"[^>]*>(.*?)</a>', chunk, flags=re.S):
            title = clean_title(html.unescape(re.sub(r"<[^>]+>", "", value)))
            if title and title not in seen:
                entries.append({"title": title, "slug": slug})
                seen.add(title)
            if len(entries) >= 10:
                break
        return entries

    @staticmethod
    def _markdown_titles(chunk: str) -> list[dict[str, str]]:
        entries = []
        seen = set()
        for value, slug in re.findall(r"\[([^\]\n]+?)\]\(https://flixpatrol\.com/title/([^/)]+)/\)", chunk):
            title = clean_title(value)
            if title and not title.startswith(("Image ", "![")) and title not in seen:
                entries.append({"title": title, "slug": slug})
                seen.add(title)
            if len(entries) >= 10:
                break
        return entries

    def _tmdb_item_for_title(self, requested_media_type: str, title: str) -> dict[str, Any] | None:
        if not self.has_tmdb:
            return None
        cache_key = (requested_media_type, normal_title(title))
        if cache_key in self._tmdb_cache:
            return self._tmdb_cache[cache_key]

        candidates = []
        for media_type in (requested_media_type, "tvshow" if requested_media_type == "movie" else "movie"):
            tmdb_type = "movie" if media_type == "movie" else "tv"
            data = self._tmdb_get(f"/search/{tmdb_type}", {
                "language": "en-US",
                "query": title,
                "page": "1",
            })
            for item in (data.get("results") or [])[:5] if data else []:
                if item.get("id"):
                    candidates.append((match_score(item, media_type, requested_media_type, title), media_type, item))
            time.sleep(0.08)

        if not candidates:
            self._tmdb_cache[cache_key] = None
            return None
        _score, media_type, best = sorted(candidates, key=lambda candidate: candidate[0], reverse=True)[0]
        result = {
            "media_type": media_type,
            "tmdb_id": best["id"],
            "title": best.get("title") or best.get("name") or title,
        }
        self._tmdb_cache[cache_key] = result
        return result

    def _tmdb_discover(self, service: dict[str, Any], region: str, media_type: str, limit: int) -> list[dict[str, Any]]:
        today = date.today()
        one_year_ago = today - timedelta(days=365)
        tmdb_type = "movie" if media_type == "movie" else "tv"
        params = {
            "language": "en-US",
            "watch_region": region,
            "with_watch_providers": service["provider_id"],
            "with_watch_monetization_types": "flatrate",
            "page": "1",
        }
        if media_type == "movie":
            params.update({
                "region": region,
                "sort_by": "primary_release_date.desc",
                "release_date.gte": one_year_ago.isoformat(),
                "release_date.lte": today.isoformat(),
            })
        else:
            params.update({
                "include_null_first_air_dates": "false",
                "sort_by": "first_air_date.desc",
                "first_air_date.gte": one_year_ago.isoformat(),
                "first_air_date.lte": today.isoformat(),
            })

        data = self._tmdb_get(f"/discover/{tmdb_type}", params)
        results = []
        for item in (data.get("results") or [])[:limit] if data else []:
            tmdb_id = item.get("id")
            if not tmdb_id:
                continue
            results.append({
                "media_type": media_type,
                "tmdb_id": tmdb_id,
                "title": item.get("title") or item.get("name") or "",
                "release_date": item.get("release_date") or item.get("first_air_date") or "",
                "poster_path": item.get("poster_path") or "",
                "backdrop_path": item.get("backdrop_path") or "",
            })
        return results

    def _tmdb_get(self, path: str, params: dict[str, str]) -> dict[str, Any] | None:
        url = f"{TMDB_BASE_URL}{path}"
        headers = {"Accept": "application/json"}
        request_params = dict(params)
        if self.tmdb_read_token:
            headers["Authorization"] = f"Bearer {self.tmdb_read_token}"
        elif self.tmdb_api_key:
            request_params["api_key"] = self.tmdb_api_key
        try:
            response = self.session.get(url, params=request_params, headers=headers, timeout=15)
            if response.status_code == 401:
                print("TMDB authentication failed. Check TMDB_READ_TOKEN.", file=sys.stderr)
                return None
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            print(f"TMDB request failed: {url} | {exc}", file=sys.stderr)
        except ValueError as exc:
            print(f"TMDB returned invalid JSON: {url} | {exc}", file=sys.stderr)
        return None


def clean_title(title: str) -> str:
    value = " ".join(title.split())
    return value if value and len(value) <= 100 else ""


def normal_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (title or "").lower())


def match_score(item: dict[str, Any], media_type: str, requested_media_type: str, title: str) -> float:
    score = 0.0
    result_title = item.get("title") or item.get("name") or item.get("original_title") or item.get("original_name") or ""
    query = normal_title(title)
    result = normal_title(result_title)
    if result == query:
        score += 120
    elif result.startswith(query) or query.startswith(result):
        score += 45
    elif query in result or result in query:
        score += 25
    if media_type == requested_media_type:
        score += 8
    if item.get("poster_path"):
        score += 18
    if item.get("backdrop_path"):
        score += 8
    score += min(float(item.get("vote_count") or 0), 50) / 2
    score += min(float(item.get("popularity") or 0), 50) / 5
    return score


def parse_csv(value: str) -> set[str] | None:
    if not value:
        return None
    return {item.strip() for item in value.split(",") if item.strip()}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Optimus streaming catalog JSON.")
    parser.add_argument("--output", default="data/streaming_catalog.json", help="JSON output path.")
    parser.add_argument("--services", default="", help="Comma-separated service slugs to build.")
    parser.add_argument("--regions", default="", help="Comma-separated region codes to build.")
    parser.add_argument("--skip-recent", action="store_true", help="Skip TMDB recently added discovery.")
    args = parser.parse_args()

    builder = CatalogBuilder(
        tmdb_read_token=os.environ.get("TMDB_READ_TOKEN", ""),
        tmdb_api_key=os.environ.get("TMDB_API_KEY", ""),
    )
    if not builder.has_tmdb:
        print("TMDB_READ_TOKEN is not set. TMDB ids and recently added entries will be omitted.", file=sys.stderr)

    payload = builder.build(
        service_filter=parse_csv(args.services),
        region_filter=parse_csv(args.regions),
        skip_recent=args.skip_recent,
    )
    write_json(Path(args.output), payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

