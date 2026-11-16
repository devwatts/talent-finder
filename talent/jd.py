"""Turn a job description into something the rest of the pipeline can work with.

The JD is read for what the person will do, not as a keyword list. Only conditions the JD
states as non-negotiable become hard constraints.
"""
from typing import Literal

from pydantic import BaseModel, Field

from talent.llm import ask


class Skill(BaseModel):
    name: str                 # standard name, abbreviations spelled out
    importance: Literal["required", "preferred", "mentioned"]
    one_of: str | None        # skills that share a label are alternatives; any one is enough
    quote: str                # the JD's own words


class Constraint(BaseModel):
    kind: Literal["location", "work_mode", "work_authorization", "language", "certification", "education", "other"]
    value: str
    hard: bool


class Scope(BaseModel):
    level: Literal["entry", "mid", "senior", "lead", "manager", "director", "executive"] | None
    manages_people: bool | None
    reports_to: str | None
    ownership: str | None     # what they are accountable for, in one line
    company_stage: str | None
    years_stated: int | None  # the minimum the JD asks for, if it gives a number


class ParsedJD(BaseModel):
    title: str
    outcomes: list[str] = Field(max_length=7)        # what the person will own or deliver
    skills: list[Skill] = Field(max_length=20)
    constraints: list[Constraint]
    scope: Scope
    open_questions: list[str] = Field(max_length=5)  # what to ask the recruiter before sourcing


PROMPT = """Parse this job description for a recruiter who will source candidates from it.

Rules:
- Use only what the JD says. Leave a field null or a list empty rather than guess.
- Outcomes are what the person will own or deliver, 3 to 7 of them, each in one plain sentence.
- Skills: at most 20, most important first. Keep what separates a qualified candidate from an
  unqualified one and could be evidenced on a profile. Leave out generic traits (communication,
  teamwork, attention to detail) and the teams the person merely works with.
  Spell out every abbreviation and use the standard name of each tool or technique.
  importance follows where the JD puts it: "required" or "preferred" for what it asks of the
  person, "mentioned" for anything that only appears where it describes the job.
  When the JD offers alternatives ("X or Y", "one of", "X/Y"), give them the same one_of label.
  Degrees and certifications are constraints, not skills.
- Constraints are conditions on the person (location, work mode, authorisation, language,
  certification, education). Mark hard only when the JD states it as a must and accepts no
  substitute: "a degree or equivalent experience" is not hard.
- Scope: level is your reading of the title, years and ownership together; null only when the
  JD gives no signal. For a range of years, years_stated is the lower number.
  company_stage describes the hiring company, never the candidate.
- Ignore boilerplate: benefits, perks, company marketing, equal-opportunity text.
- open_questions: at most 5 gaps or contradictions that would change who gets sourced.
  Do not ask about anything the JD already answers.

JOB DESCRIPTION:
{jd}"""


def parse_jd(text: str) -> ParsedJD:
    return ask(PROMPT.format(jd=text), ParsedJD)
