"""The rubric a search is judged by, and the filters it is searched with.

A rubric is drafted from the parsed JD, the hiring company and the recruiter's answers, checked
for bias, and later revised from the recruiter's yes or no on borderline profiles.
"""
from typing import Literal

from pydantic import BaseModel, Field

from talent.employers import KIND
from talent.llm import ask


class Criterion(BaseModel):
    id: str
    looks_for: str             # what on a profile would show this
    weight: Literal[1, 2, 3]   # 3 means the role cannot be done without it
    must: bool                 # a deal-breaker: a profile that contradicts it is excluded


class Filters(BaseModel):
    titles: list[str] = Field(max_length=8)
    locations: list[str] = Field(max_length=10)
    experience: list[Literal["<1", "1-2", "3-5", "6-10", "10+"]]   # LinkedIn's bands of total years


class Rubric(BaseModel):
    criteria: list[Criterion] = Field(max_length=12)
    level: Literal["entry", "mid", "senior", "staff", "principal"]
    comparable_employers: list[KIND]   # empty means any kind of employer counts
    target_level: str                  # the scope wanted, in a sentence or two
    filters: Filters


class Flag(BaseModel):
    id: str                    # the criterion's id
    reason: str


class Flags(BaseModel):
    flags: list[Flag]


class Revision(BaseModel):
    rubric: Rubric
    changes: list[str]         # what changed and why, one line each


DRAFT = """Write the rubric a recruiter will use to source and judge candidates for this role.

- Criteria: what must be evident on a LinkedIn profile, most important first. Build them from what
  the person will do, not from a keyword list. Each must be checkable from work history, skills,
  education or certifications, and fit in one sentence of at most 30 words.
  Do not count years of experience: seniority is judged separately.
- must is true only for stated deal-breakers (licence, work authorisation, clearance and the like)
  and for anything the recruiter's answers make non-negotiable.
- level: the level being hired on the ladder entry, mid, senior, staff, principal, read from the
  scope of the role and not from its title, since titles differ between companies.
- comparable_employers: leave empty unless the recruiter's answers say that only some kinds of
  employer count; then list those kinds.
- target_level: the scope wanted, given the hiring company. Two sentences at most.
- Filters: titles people in this job actually hold today, including common variants. Locations as
  LinkedIn would name them (cities and metro areas, not neighbourhoods): the JD's location and
  nearby cities within commuting distance, or the whole country when the role is remote there.
  Experience: the bands of total working years that people at the target level usually fall in.
  This only narrows who is searched; it is never a criterion.
{previous}
PARSED JOB DESCRIPTION:
{jd}

HIRING COMPANY:
{company}

RECRUITER'S ANSWERS AND FEEDBACK:
{answers}"""

BIAS = """Review the criteria of this hiring rubric for bias. Flag, by id, any criterion that judges a
candidate on, or works as a stand-in for, age, gender, ethnicity, nationality, religion,
disability, family status or career gaps, rather than on ability to do the job. The seniority
of the role, where the job is located, and legal requirements such as work authorisation,
licences and clearances are legitimate. Return no flags if the criteria are clean.

{rubric}"""

REVISE = """A recruiter reviewed borderline candidates scored with this rubric. Revise the rubric so
that it would have agreed with the recruiter: change weights, reword, add or drop criteria, or
adjust the level and filters. Change only what the verdicts justify, keep criterion ids stable,
and list each change with its reason. Feedback about seniority belongs in level, target_level
and the experience filter, and feedback about employers in comparable_employers, never in a
criterion.

RUBRIC:
{rubric}

RECRUITER'S VERDICTS:
{verdicts}"""


def draft(jd: dict, company: dict, answers: str = "", previous: Rubric | None = None) -> tuple[Rubric, list[Flag]]:
    """Draft the rubric, or redraft an earlier one, and drop whatever the bias check flags."""
    earlier = f"\nREVISE THIS EARLIER DRAFT:\n{previous.model_dump_json()}\n" if previous else ""
    rubric = ask(DRAFT.format(previous=earlier, jd=jd, company=company, answers=answers or "(none)"), Rubric)

    flags = ask(BIAS.format(rubric=rubric.model_dump_json(indent=1)), Flags).flags
    criteria_ids = {c.id for c in rubric.criteria}
    flags = [flag for flag in flags if flag.id in criteria_ids]    # it sometimes comments on a filter; only criteria are removed
    flagged = {flag.id for flag in flags}
    rubric.criteria = [c for c in rubric.criteria if c.id not in flagged]
    return rubric, flags


def revise(rubric: Rubric, verdicts: str) -> Revision:
    return ask(REVISE.format(rubric=rubric.model_dump_json(indent=1), verdicts=verdicts), Revision)
