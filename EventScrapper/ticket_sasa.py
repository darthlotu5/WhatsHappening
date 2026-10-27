import asyncio
import json
import re
from urllib.parse import urljoin
from datetime import datetime as dt

import bs4
import requests as req
from aiohttp import ClientSession, ClientTimeout, TCPConnector
from bs4 import BeautifulSoup
from EventScrapper import SUCCESS
from EventScrapper.base_extractor import BaseExtractor
from shared.contracts.event_model import Event
from EventScrapper.utils import build_request_response

class ScrapTicketSasa(BaseExtractor):
    def run(self):
        try:
            asyncio.run(self._load_page_parse_events())
            self.response = build_request_response(SUCCESS, "requests successful")

        except Exception as exc:
            self.response = build_request_response(500, str(exc))

    async def _fetch_html_async(
        self, url: str, session: ClientSession, encoding="utf-8", **kwargs
    ) -> str:
        resp = await session.request(
            method="GET", url=url, raise_for_status=True, **kwargs
        )
        html_doc = await resp.text(encoding)
        return html_doc

    @staticmethod
    def _parse_time(time_str: str, _format="%I:%M %p") -> dt.time:
        return dt.strptime(time_str.strip(), _format).time()

    @staticmethod
    def _parse_end_date(date_tag: bs4.element.Tag, _format="%A%d%B%Y") -> dt.date:
        date_string = list(date_tag.find("li").strings)[-1]
        # remove all white spaces or unneccessary strings from date
        date_string = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", date_string)
        date_string = re.sub(r"\s+|,|:", "", date_string)
        try:
            return dt.strptime(date_string, _format).date()
        except Exception:
            return None

    @staticmethod
    def _parse_start_date(
        date_tag: bs4.element.Tag, _format="%Y-%m-%dT%H:%M"
    ) -> dt.date:
        date_str = date_tag.get("content")
        return dt.strptime(date_str, _format).date()

    async def _parse_event(
        self, banner_url: str, url: str, session: ClientSession, **kwargs
    ):
        # fetch html
        try:
            html_doc = await self._fetch_html_async(url=url, session=session, **kwargs)
        except Exception as exc:
            html_doc = await self._fetch_html_async(
                url=url, session=session, encoding="ISO-8859-1", **kwargs
            )
        # parse the page
        soup = BeautifulSoup(html_doc, "html.parser")
        event_name_contents = soup.find("h1", attrs={"itemprop": "name"}).strings
        event_name = list(event_name_contents)[0]

        location_tag = soup.find("span", attrs={"itemprop": "location"})
        event_location = location_tag.find("span", attrs={"itemprop": "name"}).text

        # parse event date and times
        event_times = soup.find("div", attrs={"class": "date-times"})
        _start, _end = map(lambda x: x.string, event_times.find_all("span"))
        event_start_time = self._parse_time(_start)
        event_end_time = self._parse_time(_end)
        start_date_html = soup.find("div", attrs={"itemprop": "startDate"})
        event_start_date = self._parse_start_date(start_date_html)
        end_date_html = soup.find("ul", attrs={"class": "evt-specs"})
        event_end_date = self._parse_end_date(end_date_html)
        if event_end_date is None:
            event_end_date = event_start_date

        # load event object
        event = Event.create_event(
            event_name=event_name.strip(),
            event_location=event_location.strip(),
            start_date=event_start_date,
            end_date=event_end_date,
            start_time=event_start_time,
            end_time=event_end_time,
            banner_url=banner_url.strip(),
            event_url=url,
            site_name=self.site_name,
        )
        self.events.append(event)

    async def _load_page_parse_events(self, **kwargs) -> None:

        base_url = self.site_url
        url = base_url + "/events"
        response = req.get(url, timeout=30, headers={"User-Agent": "Dundaa/1.0 event-discovery"})
        response.raise_for_status()
        html_doc = response.content

        soup = BeautifulSoup(html_doc, "html.parser")
        payload = soup.find("script", id="__NUXT_DATA__")
        if payload:
            self.events.extend(self._parse_modern_events(payload.get_text()))
            return

        events_html = soup.select('div[itemtype="http://schema.org/Event"]')

        tasks = []
        async with ClientSession(
            timeout=ClientTimeout(total=30),
            connector=TCPConnector(limit=5),
            headers={"User-Agent": "Dundaa/1.0 event-discovery"},
        ) as session:
            for event_html in events_html:
                # print(each)
                event_banner_tag = event_html.find("img")
                event_banner = urljoin(base_url, event_banner_tag["src"])
                event_dt = event_html.select_one(".date-box")
                event_endpoint = event_dt.get("href")
                url = urljoin(base_url, event_endpoint)

                tasks.append(
                    self._parse_event(
                        banner_url=event_banner, url=url, session=session, **kwargs
                    )
                )

            await asyncio.gather(*tasks)

    def _parse_modern_events(self, payload):
        values = json.loads(payload)
        if not isinstance(values, list):
            raise ValueError("Unexpected TicketSasa Nuxt payload")
        events = {}
        required = {"id", "name", "loc", "slug_name", "main_image", "start_at"}
        for item in values:
            if not isinstance(item, dict) or not required.issubset(item):
                continue
            record = {}
            for field in required:
                reference = item[field]
                if type(reference) is not int or not 0 <= reference < len(values):
                    raise ValueError(f"Invalid TicketSasa field reference: {field}")
                record[field] = values[reference]
            started = dt.strptime(record["start_at"], "%a %d %b %Y %I:%M %p")
            event = Event(
                name=record["name"], location=record["loc"],
                start_date=started.date(), end_date=started.date(),
                start_time=started.time(), end_time=started.time(),
                banner_url=urljoin(self.site_url, record["main_image"]),
                site_name=self.site_name,
                url=urljoin(self.site_url, f"/events/{record['slug_name']}"),
                id=f"ticketsasa-{record['id']}",
            )
            events[event.id] = event
        if not events:
            raise ValueError("TicketSasa payload contained no recognizable event records")
        return list(events.values())
