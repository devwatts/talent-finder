"""The operations behind the MCP tools.

Each one loads a search's state from the database, does one step, saves what it produced and
returns a short summary for Claude Code to show the user.
"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from psycopg.types.json import Jsonb

from talent import employers, rubric as rubrics, score as scoring, search as linkedin
from talent.company import research_company
from talent.db import connect
from talent.jd import parse_jd
from talent.rubric import Rubric

BORDERLINE = 8          # probe profiles put in front of the recruiter
RARELY = 0.8            # a criterion this share of profiles say nothing about is not separating anyone
POLL_SECONDS = 3

SAVE_PROFILE = """
insert into app.profiles (id, data) values (%s, %s)
on conflict (id) do update set data = excluded.data, fetched_at = now()"""

SAVE_SCORE = """
insert into app.scores (search_id, rubric_version, profile_id, stage, assessment, score)
values (%s, %s, %s, %s, %s, %s)
on conflict (search_id, rubric_version, profile_id)
do update set stage = excluded.stage, assessment = excluded.assessment, score = excluded.score"""

RANKED = """
select p.data, s.assessment, s.score
from app.scores s join app.profiles p on p.id = s.profile_id
where s.search_id = %s and s.rubric_version = %s and s.stage = %s
order by s.score desc"""

LATEST_SCORE = """
select p.data, s.assessment, s.score, r.rubric
from app.scores s
join app.profiles p on p.id = s.profile_id
join app.rubrics r on r.search_id = s.search_id and r.version = s.rubric_version
where s.search_id = %s and s.profile_id = %s
order by s.rubric_version desc limit 1"""


def load(search_id):
    """The parsed JD, the hiring company and the latest rubric (version 0 and None before a draft)."""
    with connect() as db:
        jd, company = db.execute("select jd, company from app.searches where id = %s", (search_id,)).fetchone()
        latest = db.execute(
            "select version, rubric from app.rubrics where search_id = %s order by version desc limit 1",
            (search_id,)).fetchone()
    if latest is None:
        return jd, company, 0, None
    return jd, company, latest[0], Rubric(**latest[1])


def save_rubric(search_id, version, rubric):
    with connect() as db:
        db.execute("insert into app.rubrics (search_id, version, rubric) values (%s, %s, %s)",
                   (search_id, version, Jsonb(rubric.model_dump())))


def assess_profiles(search_id, profiles, stage):
    """Read every profile against the latest rubric and store the profile, the reading and the score."""
    jd, company, version, rubric = load(search_id)
    kinds = employers.lookup(job for profile in profiles for job in profile["experience"])

    def read(profile):
        return scoring.assess(rubric, jd, company, employers.annotate(profile, kinds))

    with ThreadPoolExecutor(8) as pool:
        readings = list(pool.map(read, profiles))

    with connect() as db:
        for profile, reading in zip(profiles, readings):
            db.execute(SAVE_PROFILE, (profile["id"], Jsonb(profile)))
            db.execute(SAVE_SCORE, (search_id, version, profile["id"], stage,
                                    Jsonb(reading.model_dump()), scoring.score(rubric, reading)))


def ranked(search_id, version, stage):
    with connect() as db:
        return db.execute(RANKED, (search_id, version, stage)).fetchall()


def card(profile, reading, points, rubric):
    """One candidate in a few lines, for lists."""
    silent = {f["id"] for f in reading["findings"] if f["strength"] == 0 and not f["contradicted"]}
    current = profile["experience"][0] if profile["experience"] else {}
    return {
        "profile_id": profile["id"],
        "name": profile["name"],
        "url": profile["url"],
        "headline": profile["headline"],
        "current": f"{current.get('title')} at {current.get('company')}",
        "score": points,
        "role_match": reading["role_match"],
        "domain_match": reading["domain_match"],
        "level": f"{reading['level']['equivalent']} (hiring {rubric.level})",
        "summary": reading["summary"],
        "to_verify": [c.looks_for for c in rubric.criteria if c.must and c.id in silent],
    }


def start_search(jd_text: str, company_website: str | None = None) -> dict:
    jd = parse_jd(jd_text)
    company = research_company(jd_text, company_website)
    with connect() as db:
        search_id = db.execute(
            "insert into app.searches (jd_text, jd, company) values (%s, %s, %s) returning id",
            (jd_text, Jsonb(jd.model_dump()), Jsonb(company.model_dump()))).fetchone()[0]
    return {
        "search_id": search_id,
        "title": jd.title,
        "level": jd.scope.level,
        "will_do": jd.outcomes,
        "deal_breakers": [f"{c.kind}: {c.value}" for c in jd.constraints if c.hard],
        "company": company.summary,
        "open_questions": jd.open_questions,
    }


def draft_rubric(search_id: int, answers: str = "") -> dict:
    jd, company, version, previous = load(search_id)
    rubric, flags = rubrics.draft(jd, company, answers, previous)
    save_rubric(search_id, version + 1, rubric)
    return {
        "rubric_version": version + 1,
        "rubric": rubric.model_dump(),
        "removed_by_bias_check": [flag.model_dump() for flag in flags],
        "search_cost_usd": {size: linkedin.cost(size) for size in (25, 50, 100)},
    }


def probe(search_id: int, size: int = 15) -> dict:
    _, _, version, rubric = load(search_id)
    profiles = linkedin.search(rubric.filters, size)
    assess_profiles(search_id, profiles, "probe")

    rows = [row for row in ranked(search_id, version, "probe") if row[2] > 0]
    borderline = []
    if rows:
        median = rows[len(rows) // 2][2]
        nearest = sorted(rows, key=lambda row: abs(row[2] - median))[:BORDERLINE]
        borderline = sorted(nearest, key=lambda row: row[2], reverse=True)

    silent = [f["id"] for row in rows for f in row[1]["findings"] if f["strength"] == 0]
    rarely = [c.looks_for for c in rubric.criteria if rows and silent.count(c.id) >= RARELY * len(rows)]
    return {
        "found": len(profiles),
        "rubric_version": version,
        "borderline": [card(*row, rubric) for row in borderline],
        "rarely_evidenced": rarely,
        "full_run_cost_usd": {size: linkedin.cost(size) for size in (50, 100, 200)},
    }


def calibrate(search_id: int, verdicts: list[dict]) -> dict:
    """Each verdict is {"profile_id": ..., "good_fit": true or false, "note": optional}."""
    _, _, version, rubric = load(search_id)
    lines = []
    with connect() as db:
        for verdict in verdicts:
            db.execute(
                "insert into app.calibrations (search_id, profile_id, good_fit, note) values (%s, %s, %s, %s) "
                "on conflict (search_id, profile_id) do update set good_fit = excluded.good_fit, note = excluded.note",
                (search_id, verdict["profile_id"], verdict["good_fit"], verdict.get("note")))
            profile, reading, points, _ = db.execute(LATEST_SCORE, (search_id, verdict["profile_id"])).fetchone()
            strengths = {f["id"]: f["strength"] for f in reading["findings"]}
            said = "GOOD FIT" if verdict["good_fit"] else "NOT A FIT"
            if verdict.get("note"):
                said += f": {verdict['note']}"
            lines.append(f"- {profile['headline']} | scored {points} | {reading['summary']} | "
                         f"level {reading['level']['equivalent']} | evidence strength 0-4 {strengths} | "
                         f"recruiter says {said}")

    revision = rubrics.revise(rubric, "\n".join(lines))
    save_rubric(search_id, version + 1, revision.rubric)
    return {"rubric_version": version + 1, "changes": revision.changes}


def run_search(search_id: int, max_profiles: int = 50) -> dict:
    _, _, version, rubric = load(search_id)

    def set_status(status, progress):
        with connect() as db:
            db.execute("update app.searches set status = %s, progress = %s where id = %s",
                       (status, progress, search_id))

    def work():
        try:
            profiles = linkedin.search(rubric.filters, max_profiles)
            set_status("running", f"scoring {len(profiles)} profiles")
            assess_profiles(search_id, profiles, "full")
            set_status("done", f"{len(profiles)} profiles scored")
        except Exception as error:
            set_status("failed", str(error))

    with connect() as db:
        db.execute("update app.rubrics set frozen = true where search_id = %s and version = %s",
                   (search_id, version))
    set_status("running", "searching LinkedIn")
    threading.Thread(target=work).start()
    return {"status": "running", "rubric_version": version, "estimated_cost_usd": linkedin.cost(max_profiles)}


def search_status(search_id):
    with connect() as db:
        return db.execute("select status, progress from app.searches where id = %s", (search_id,)).fetchone()


def get_results(search_id: int, top: int = 10, wait_seconds: int = 0) -> dict:
    """Waits up to wait_seconds for a running search, so the caller does not have to poll in a tight loop."""
    deadline = time.monotonic() + wait_seconds
    status, progress = search_status(search_id)
    while status == "running" and time.monotonic() < deadline:
        time.sleep(POLL_SECONDS)
        status, progress = search_status(search_id)

    with connect() as db:
        frozen = db.execute(
            "select version, rubric from app.rubrics where search_id = %s and frozen order by version desc limit 1",
            (search_id,)).fetchone()
    if frozen is None:
        return {"status": status, "progress": "no full run has been started"}

    version, rubric = frozen[0], Rubric(**frozen[1])
    rows = ranked(search_id, version, "full")
    kept = [row for row in rows if row[2] > 0]
    return {
        "status": status,
        "progress": progress,
        "rubric_version": version,
        "scored": len(rows),
        "excluded_by_deal_breaker": len(rows) - len(kept),
        "ranked": [{"rank": rank, **card(*row, rubric)} for rank, row in enumerate(kept[:top], 1)],
    }


def get_candidate(search_id: int, profile_id: str) -> dict:
    with connect() as db:
        profile, reading, points, rubric = db.execute(LATEST_SCORE, (search_id, profile_id)).fetchone()
    findings = {f["id"]: f for f in reading["findings"]}
    nothing = {"strength": 0, "contradicted": False, "evidence": ""}
    return {
        "name": profile["name"],
        "url": profile["url"],
        "headline": profile["headline"],
        "score": points,
        "summary": reading["summary"],
        "role_match": reading["role_match"],
        "domain_match": reading["domain_match"],
        "level": reading["level"],
        "criteria": [{"looks_for": c["looks_for"], "weight": c["weight"], "must": c["must"],
                      **{key: findings.get(c["id"], nothing)[key] for key in nothing}}
                     for c in rubric["criteria"]],
    }
