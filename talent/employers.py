"""Company memory: what kind of employer each company is.

Titles and years do not compare across employers, so levelling needs to know where the work
was done. Each company is classified once, with a web search if the model does not recognise it,
and stored, which keeps the reading the same for every candidate who worked there and lets a
person correct it.
"""
from typing import Literal

import openai
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from talent.db import connect
from talent.llm import ask, ask_with_search

KIND = Literal["big tech", "product company", "startup", "IT services", "consulting", "enterprise", "other", "unknown"]
BATCH = 40


class Employer(BaseModel):
    name: str
    kind: KIND


class Employers(BaseModel):
    employers: list[Employer]


PROMPT = """Say what kind of employer each company is.
- big tech: a large technology company whose product is technology.
- product company: an established company that builds and sells its own software or internet product.
- startup: an early-stage company, typically under a few hundred people.
- IT services: builds or runs technology for client companies, including outsourcing and staffing firms.
- consulting: advisory and professional-services firms.
- enterprise: a company in another industry (bank, retailer, manufacturer, pharma, insurer) and its
  in-house or captive technology centres.
{how} Return the names exactly as given.

COMPANIES:
{names}"""

RECALL = "Use only what you reliably know. For a company you do not recognise, answer unknown rather than guess."
SEARCH = """Search the web for each company. The LinkedIn page in brackets says which company is meant;
leave it out of the name. Answer unknown unless you find that specific company."""


def classify(names, pages=None) -> dict[str, str]:
    """The kind of each company, from what the model knows or, given their LinkedIn pages, from the web."""
    if pages is None:
        reply = ask(PROMPT.format(how=RECALL, names="\n".join(names)), Employers)
    else:
        listed = "\n".join(f"{name} [{pages.get(name) or 'no page'}]" for name in names)
        reply = ask_with_search(PROMPT.format(how=SEARCH, names=listed), Employers)
    kinds = {employer.name.strip().lower(): employer.kind for employer in reply.employers}
    return {name: kind for name, kind in kinds.items() if name in names}


def lookup(jobs) -> dict[str, str]:
    """Map each lower-cased company name in these jobs to its kind, classifying any we have not seen before."""
    pages = {job["company"].strip().lower(): job.get("company_url") for job in jobs if job["company"]}
    names = sorted(pages)
    with connect() as db:
        rows = db.execute("select name, data->>'kind' from app.employers where name = any(%s)", (names,))
        kinds = dict(rows.fetchall())
        unseen = [name for name in names if name not in kinds]
        for start in range(0, len(unseen), BATCH):
            batch = unseen[start:start + BATCH]
            found = classify(batch)
            unknown = [name for name in batch if found.get(name) == "unknown"]
            if unknown:
                try:
                    found.update(classify(unknown, pages))
                except openai.APIStatusError:      # this endpoint has no web search
                    pass
            for name, kind in found.items():
                kinds[name] = kind
                db.execute("insert into app.employers values (%s, %s) on conflict do nothing",
                           (name, Jsonb({"kind": kind})))
    return kinds


def annotate(profile: dict, kinds: dict) -> dict:
    """A copy of the profile with every job labelled by its employer's kind."""
    jobs = [{**job, "employer_kind": kinds.get((job["company"] or "").strip().lower(), "unknown")}
            for job in profile["experience"]]
    return {**profile, "experience": jobs}
