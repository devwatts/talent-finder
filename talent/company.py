"""Find out what kind of company is hiring. Candidates are levelled against it."""
import html
import re
import urllib.request
from typing import Literal

from pydantic import BaseModel

from talent.llm import ask


class Company(BaseModel):
    name: str | None
    industry: str | None
    headcount: Literal["1-50", "51-200", "201-1000", "1001-10000", "10000+"] | None
    stage: str | None          # seed, Series B, public, family-owned and so on
    summary: str               # what it does and how demanding its hiring bar is


PROMPT = """Describe the company that is hiring, for a recruiter who must judge how senior and how
strong a candidate needs to be. Use the job description, the website text if given, and what you
reliably know about the company. Leave a field null rather than guess. Keep the summary to two
sentences.

JOB DESCRIPTION:
{jd}

WEBSITE TEXT:
{site}"""


def website_text(url):
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=20) as reply:
        page = reply.read(400_000).decode("utf-8", "ignore")
    page = re.sub(r"(?s)<(script|style).*?</\1>", " ", page)
    text = html.unescape(re.sub(r"<[^>]+>", " ", page))
    return re.sub(r"\s+", " ", text)[:6000]


def research_company(jd_text: str, website: str | None = None) -> Company:
    site = "(none given)"
    if website:
        try:
            site = website_text(website)
        except OSError:
            site = "(could not be fetched)"
    return ask(PROMPT.format(jd=jd_text[:6000], site=site), Company)
