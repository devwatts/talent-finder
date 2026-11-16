"""Company memory: what kind of employer each company is.

Titles and years do not compare across employers, so levelling needs to know where the work
was done. Each company is classified once and stored, which keeps the reading the same for
every candidate who worked there and lets a person correct it.
"""
from typing import Literal

from psycopg.types.json import Jsonb
from pydantic import BaseModel

from talent.db import connect
from talent.llm import ask

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
Use only what you reliably know. For a company you do not recognise, answer unknown rather than
guess. Return the names exactly as given.

COMPANIES:
{names}"""


def lookup(names) -> dict[str, str]:
    """Map each lower-cased company name to its kind, classifying any we have not seen before."""
    names = sorted({name.strip().lower() for name in names if name})
    with connect() as db:
        rows = db.execute("select name, data->>'kind' from app.employers where name = any(%s)", (names,))
        kinds = dict(rows.fetchall())
        unseen = [name for name in names if name not in kinds]
        for start in range(0, len(unseen), BATCH):
            batch = unseen[start:start + BATCH]
            reply = ask(PROMPT.format(names="\n".join(batch)), Employers)
            for employer in reply.employers:
                name = employer.name.strip().lower()
                if name in batch:
                    kinds[name] = employer.kind
                    db.execute("insert into app.employers values (%s, %s) on conflict do nothing",
                               (name, Jsonb({"kind": employer.kind})))
    return kinds


def annotate(profile: dict, kinds: dict) -> dict:
    """A copy of the profile with every job labelled by its employer's kind."""
    jobs = [{**job, "employer_kind": kinds.get((job["company"] or "").strip().lower(), "unknown")}
            for job in profile["experience"]]
    return {**profile, "experience": jobs}
