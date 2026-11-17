"""Find people on LinkedIn through Apify, and keep only what a recruiter would read."""
import json
import math
import os
import time
import urllib.request

API = "https://api.apify.com/v2"
ACTOR = "harvestapi~linkedin-profile-search"
PAGE = 25                                      # profiles per search page, and the free-plan cap per run
PRICE_PER_PAGE, PRICE_PER_PROFILE = 0.10, 0.004
EXPERIENCE_ID = {"<1": "1", "1-2": "2", "3-5": "3", "6-10": "4", "10+": "5"}   # LinkedIn's own band ids


def cost(profiles: int) -> float:
    return round(math.ceil(profiles / PAGE) * PRICE_PER_PAGE + profiles * PRICE_PER_PROFILE, 2)


def apify(method, path, body=None):
    request = urllib.request.Request(
        f"{API}/{path}", method=method,
        data=json.dumps(body).encode() if body else None,
        headers={"Authorization": f"Bearer {os.environ['APIFY_TOKEN']}", "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=60) as reply:
        return json.load(reply)


def trim(raw: dict) -> dict:
    """A full profile is about 30,000 characters, most of it image links. Keep the readable part."""
    def text(value):
        return (value or {}).get("text")

    return {
        "id": raw["id"],
        "name": f"{raw.get('firstName', '')} {raw.get('lastName', '')}".strip(),
        "url": raw.get("linkedinUrl"),
        "headline": raw.get("headline"),
        "location": (raw.get("location") or {}).get("linkedinText"),
        "about": (raw.get("about") or "")[:1500],
        "experience": [{
            "title": job.get("position"),
            "company": job.get("companyName"),
            "company_url": job.get("companyLinkedinUrl"),
            "type": job.get("employmentType"),
            "from": text(job.get("startDate")),
            "to": text(job.get("endDate")),
            "duration": job.get("duration"),
            "description": (job.get("description") or "")[:700],
        } for job in raw.get("experience") or []],
        "education": [{
            "school": school.get("schoolName"),
            "degree": school.get("degree"),
            "field": school.get("fieldOfStudy"),
            "period": school.get("period"),
        } for school in raw.get("education") or []],
        "certifications": [f"{c.get('title')} ({c.get('issuedBy')})" for c in raw.get("certifications") or []],
        "skills": [skill.get("name") for skill in (raw.get("skills") or [])[:40]],
    }


def run_page(filters, page, size):
    """One Apify run for one page of results. Returns the raw profiles."""
    run = apify("POST", f"acts/{ACTOR}/runs", {
        "profileScraperMode": "Full",
        "currentJobTitles": filters.titles,
        "locations": filters.locations,
        "yearsOfExperienceIds": [EXPERIENCE_ID[band] for band in filters.experience],
        "startPage": page, "takePages": 1, "maxItems": size,
    })["data"]
    while run["status"] in ("READY", "RUNNING"):
        time.sleep(5)
        run = apify("GET", f"actor-runs/{run['id']}")["data"]
    if run["status"] != "SUCCEEDED":
        raise RuntimeError(f"Apify run {run['id']} ended as {run['status']}")

    profiles = apify("GET", f"datasets/{run['defaultDatasetId']}/items?clean=true")
    message = run.get("statusMessage") or ""
    if not profiles and "limit" in message.lower():
        raise RuntimeError(f"Apify refused the search: {message}")
    return profiles


def search(filters, limit: int) -> list[dict]:
    """People who currently hold one of the titles, in one of the locations, within the experience bands."""
    found = {}
    for page in range(1, math.ceil(limit / PAGE) + 1):
        profiles = run_page(filters, page, min(PAGE, limit - len(found)))
        for raw in profiles:
            if raw.get("id"):
                found[raw["id"]] = trim(raw)
        if len(profiles) < PAGE:
            break                              # LinkedIn has nothing more for these filters
    return list(found.values())[:limit]
