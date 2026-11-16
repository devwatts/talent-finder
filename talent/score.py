"""Read one profile against the rubric, then turn the reading into a score.

The model only extracts facts. The score is arithmetic on those facts, so any ranking can be
explained and worked out again by hand.
"""
from typing import Literal

from pydantic import BaseModel

from talent.llm import ask
from talent.rubric import Rubric

ROLE_POINTS, DOMAIN_POINTS, EVIDENCE_POINTS = 30, 30, 40
LADDER = ["entry", "mid", "senior", "staff", "principal"]
LEVEL_FIT = {0: 1.0, 1: 0.75, 2: 0.4}          # keyed by how many levels the candidate is off
FAR_OFF = 0.2
EMPLOYER_FIT = {"comparable": 1.0, "not comparable": 0.6, "unknown": 0.85}


class Finding(BaseModel):
    id: str                                # the criterion's id
    strength: Literal[0, 1, 2, 3, 4]
    contradicted: bool
    evidence: str                          # the profile's own words; empty at strength 0


class Level(BaseModel):
    relevant_years: float                  # internships and student jobs left out
    equivalent: Literal["entry", "mid", "senior", "staff", "principal"]
    employers: Literal["comparable", "not comparable", "unknown"]
    reasoning: str


class Assessment(BaseModel):
    role_match: Literal[0, 1, 2, 3, 4]
    domain_match: Literal[0, 1, 2, 3, 4]
    findings: list[Finding]
    level: Level
    summary: str                           # two sentences a recruiter could act on


PROMPT = """Read this LinkedIn profile for the job below and report facts. Use only what the profile says.

- role_match: how close their current or most recent work is to the job, judged from titles,
  employers and descriptions together. 0 = unrelated work, 1 = an adjacent function, 2 = a
  related role in a different setting, 3 = the same role, 4 = the same role with the same kind
  of customers and scope.
- domain_match: how close their employers' industry and product are to the hiring company's.
  0 = unrelated industry, 1 = loosely related, 2 = the same broad sector, 3 = the same industry
  or product category, 4 = a direct competitor or the same product space.
- Findings: for every criterion give a strength. 0 = nothing on the profile speaks to it,
  1 = implied or adjacent experience only, 2 = some direct evidence, 3 = clear direct evidence,
  4 = extensive evidence, central to their recent work. Set contradicted only when the profile
  shows the opposite. Quote the words that support the strength.
- Level: decide where this person would land at the hiring company, on the ladder entry, mid,
  senior, staff, principal. Judge it from scope: what they owned, how independently, and with
  what impact. Do not judge it from titles or total years. Titles do not compare across
  employers (a startup "Senior" may be a mid elsewhere) and a year is worth more where the bar
  is higher. Each job is labelled with the kind of employer it was at. Leave internships and
  student jobs out.
- Employers: was the relevant work mostly done at the kinds of employer in the rubric's
  comparable_employers? Recent roles count most. Answer unknown when the employers are unknown
  or the rubric lists none.

JOB: {title}. The person will: {outcomes}

RUBRIC:
{rubric}

HIRING COMPANY:
{company}

PROFILE:
{profile}"""


def assess(rubric: Rubric, jd: dict, company: dict, profile: dict) -> Assessment:
    """`profile` should already have its jobs labelled by employers.annotate."""
    readable = {key: value for key, value in profile.items() if key not in ("id", "url")}
    prompt = PROMPT.format(
        title=jd["title"], outcomes=" ".join(jd["outcomes"]),
        rubric=rubric.model_dump_json(indent=1, exclude={"filters"}),
        company=company, profile=readable)
    return ask(prompt, Assessment)


def score(rubric: Rubric, assessment: Assessment) -> float:
    """0 to 100. Role match, domain match and evidence add up, then level and employers scale it."""
    findings = {finding.id: finding for finding in assessment.findings}

    for criterion in rubric.criteria:
        finding = findings.get(criterion.id)
        if criterion.must and finding and finding.contradicted:
            return 0.0

    earned = sum(c.weight * findings[c.id].strength / 4 for c in rubric.criteria if c.id in findings)
    evidence = earned / sum(c.weight for c in rubric.criteria)
    points = (ROLE_POINTS * assessment.role_match / 4
              + DOMAIN_POINTS * assessment.domain_match / 4
              + EVIDENCE_POINTS * evidence)

    levels_off = abs(LADDER.index(assessment.level.equivalent) - LADDER.index(rubric.level))
    points *= LEVEL_FIT.get(levels_off, FAR_OFF)
    if rubric.comparable_employers:
        points *= EMPLOYER_FIT[assessment.level.employers]
    return round(points, 1)
