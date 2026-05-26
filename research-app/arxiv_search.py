from __future__ import annotations

import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass

ARXIV_API = "http://export.arxiv.org/api/query"
NS = {"a": "http://www.w3.org/2005/Atom"}


@dataclass
class ArxivPaper:
    title: str
    summary: str
    authors: list[str]
    published: str
    url: str

    def to_context(self) -> str:
        return (
            f"- {self.title} ({self.published[:10]})\n"
            f"  authors: {', '.join(self.authors[:5])}\n"
            f"  url: {self.url}\n"
            f"  abstract: {self.summary.strip()[:600]}"
        )


def search(query: str, max_results: int = 10) -> list[ArxivPaper]:
    """Fetch recent arXiv papers matching the query. Newest first."""
    params = urllib.parse.urlencode(
        {
            "search_query": f"all:{query}",
            "max_results": max_results,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
    )
    req = urllib.request.Request(f"{ARXIV_API}?{params}", headers={"User-Agent": "research-app/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        root = ET.fromstring(resp.read())

    papers: list[ArxivPaper] = []
    for entry in root.findall("a:entry", NS):
        title = (entry.findtext("a:title", default="", namespaces=NS) or "").strip()
        summary = (entry.findtext("a:summary", default="", namespaces=NS) or "").strip()
        published = entry.findtext("a:published", default="", namespaces=NS) or ""
        url = ""
        for link in entry.findall("a:link", NS):
            if link.get("rel") == "alternate":
                url = link.get("href", "")
                break
        authors = [
            (a.findtext("a:name", default="", namespaces=NS) or "").strip()
            for a in entry.findall("a:author", NS)
        ]
        papers.append(ArxivPaper(title=title, summary=summary, authors=authors, published=published, url=url))
    return papers
