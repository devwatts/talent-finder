"""How does the pipeline's ranking compare with simpler ways of ranking the same people?

For every JD in samples/, the same LinkedIn profiles are ranked five ways: keywords and years,
embedding similarity, one model score per profile, the model ranking the whole list, and the
pipeline. The reference is a stronger model that ranks all of a role's profiles from the full
JD, twice, in shuffled order.

Profiles, baseline scores and the judge's rankings are cached in benchmark/cache/, so a re-run
only repeats the pipeline's own work.

    uv run --env-file .env python -m benchmark.run [role ...]
"""
import glob
import json
import math
import os
import random
import re
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from pydantic import BaseModel

from talent import pipeline, search as linkedin
from talent.db import connect
from talent.llm import MODEL, ask, client

JUDGE_MODEL = "gpt-5.6-sol"
PROFILES_PER_ROLE = 15
CACHE = "benchmark/cache"
PIPELINE = "pipeline (facts, code decides)"

RANK = """You are an experienced recruiter. Rank these LinkedIn profiles for the job below, best fit
first, and grade each: 3 = interview immediately, 2 = worth a conversation, 1 = weak, 0 = no.
Judge fit for this specific role, including whether the person is at the right level for it and
whether their experience was gained somewhere comparable. Include every profile exactly once.

JOB DESCRIPTION:
{jd}

PROFILES:
{profiles}"""

RATE = """Rate from 0 to 100 how well this LinkedIn profile fits the job.

JOB DESCRIPTION:
{jd}

PROFILE:
{profile}"""


class Ranked(BaseModel):
    n: int
    grade: Literal[0, 1, 2, 3]


class Ranking(BaseModel):
    ranking: list[Ranked]


class Rating(BaseModel):
    score: int


def readable(profile):
    return {key: value for key, value in profile.items() if key not in ("id", "url")}


def rank_list(jd_text, profiles, model, seed):
    """One call that ranks every profile. Returns each profile's position (0 is best) and grade."""
    order = list(range(len(profiles)))
    random.Random(seed).shuffle(order)
    listing = "\n\n".join(f"PROFILE {n}:\n{readable(profiles[i])}" for n, i in enumerate(order, 1))
    reply = ask(RANK.format(jd=jd_text[:6000], profiles=listing), Ranking, model=model)

    position = [len(profiles)] * len(profiles)      # anyone the model leaves out goes last
    grade = [0] * len(profiles)
    for place, item in enumerate(reply.ranking):
        if 1 <= item.n <= len(order):
            position[order[item.n - 1]] = place
            grade[order[item.n - 1]] = item.grade
    return position, grade


def total_years(profile):
    months = 0
    for job in profile["experience"]:
        for number, unit in re.findall(r"(\d+) (yr|mo)", job.get("duration") or ""):
            months += int(number) * (12 if unit == "yr" else 1)
    return months / 12


def keyword_score(jd, profile):
    """Share of the JD's skills named on the profile, plus raw years against the years the JD asks for."""
    text = json.dumps(profile).lower()
    weight = {"required": 2, "preferred": 1, "mentioned": 1}
    possible = sum(weight[skill["importance"]] for skill in jd["skills"]) or 1
    matched = sum(weight[skill["importance"]] for skill in jd["skills"] if skill["name"].lower() in text)
    wanted_years = jd["scope"]["years_stated"] or 3
    return 0.75 * matched / possible + 0.25 * min(1, total_years(profile) / wanted_years)


def embedding_scores(jd_text, profiles):
    texts = [jd_text[:6000]] + [json.dumps(readable(p))[:6000] for p in profiles]
    vectors = [item.embedding for item in client.embeddings.create(model="text-embedding-3-small", input=texts).data]

    def cosine(a, b):
        dot = sum(x * y for x, y in zip(a, b))
        return dot / math.sqrt(sum(x * x for x in a) * sum(y * y for y in b))

    return [cosine(vectors[0], vector) for vector in vectors[1:]]


def run_role(name):
    """Rank one role's profiles every way and return the cached record, or None if too few were found."""
    path = f"{CACHE}/{name}.json"
    role = json.load(open(path)) if os.path.exists(path) else {}
    jd_text = open(f"samples/{name}.txt").read()

    if "search_id" not in role:
        role["search_id"] = pipeline.start_search(jd_text)["search_id"]
        pipeline.draft_rubric(role["search_id"])
    jd, _, version, rubric = pipeline.load(role["search_id"])

    if "profile_ids" not in role:
        profiles = linkedin.search(rubric.filters, PROFILES_PER_ROLE)
        role["profile_ids"] = [profile["id"] for profile in profiles]
    else:
        with connect() as db:
            stored = dict(db.execute("select id, data from app.profiles where id = any(%s)",
                                     (role["profile_ids"],)).fetchall())
        profiles = [stored[profile_id] for profile_id in role["profile_ids"]]
    json.dump(role, open(path, "w"))
    if len(profiles) < 6:
        return None

    if not pipeline.ranked(role["search_id"], version, "full"):
        pipeline.assess_profiles(role["search_id"], profiles, "full")
    points = {row[0]["id"]: row[2] for row in pipeline.ranked(role["search_id"], version, "full")}

    if "judge_position" not in role:
        with ThreadPoolExecutor(8) as pool:
            rated = list(pool.map(lambda p: ask(RATE.format(jd=jd_text[:6000], profile=readable(p)), Rating).score,
                                  profiles))
        role["baselines"] = {
            "keywords + years": [keyword_score(jd, profile) for profile in profiles],
            "embedding similarity": embedding_scores(jd_text, profiles),
            "model gives one score": rated,
            "model ranks the list": [-position for position in rank_list(jd_text, profiles, MODEL, seed=1)[0]],
        }
        first, second = (rank_list(jd_text, profiles, JUDGE_MODEL, seed) for seed in (7, 8))
        role["judge_position"] = [statistics.mean(pair) for pair in zip(first[0], second[0])]
        role["judge_grade"] = [statistics.mean(pair) for pair in zip(first[1], second[1])]
        role["judge_self_agreement"] = spearman(first[0], second[0])
        json.dump(role, open(path, "w"))

    role["scores"] = {**role["baselines"], PIPELINE: [points[profile_id] for profile_id in role["profile_ids"]]}
    return role


def spearman(a, b):
    """Rank correlation, with tied values sharing the average rank."""
    def ranks(values):
        ordered = sorted(values)
        return [ordered.index(v) + (ordered.count(v) - 1) / 2 for v in values]

    if len(set(a)) < 2 or len(set(b)) < 2:
        return 0.0
    return statistics.correlation(ranks(a), ranks(b))


def top_five_quality(scores, grades):
    """How much of the best possible top five, by the judge's grades, this ranking's top five captures (NDCG@5)."""
    def gain(order):
        return sum(grades[i] / math.log2(place + 2) for place, i in enumerate(order[:5]))

    best = gain(sorted(range(len(grades)), key=lambda i: -grades[i]))
    return gain(sorted(range(len(scores)), key=lambda i: -scores[i])) / best if best else 0.0


def pairs_ordered_correctly(scores, profile_ids, verdicts):
    """Share of (yes, no) pairs where the profile a person said yes to outscores the one they said no to."""
    by_id = dict(zip(profile_ids, scores))
    yes = [by_id[p] for p, verdict in verdicts.items() if verdict == "y"]
    no = [by_id[p] for p, verdict in verdicts.items() if verdict == "n"]
    wins = sum((y > n) + 0.5 * (y == n) for y in yes for n in no)
    return wins / (len(yes) * len(no))


def main():
    names = sys.argv[1:] or sorted(os.path.basename(path)[:-4] for path in glob.glob("samples/*.txt"))
    with ThreadPoolExecutor(4) as pool:
        roles = dict(zip(names, pool.map(run_role, names)))
    skipped = [name for name, role in roles.items() if role is None]
    roles = {name: role for name, role in roles.items() if role}

    profiles = sum(len(role["profile_ids"]) for role in roles.values())
    self_agreement = statistics.mean(role["judge_self_agreement"] for role in roles.values())
    print(f"{len(roles)} roles, {profiles} profiles. Too few profiles found for: {skipped or 'none'}")
    print(f"The judge agrees with itself across two shuffled runs at {self_agreement:.2f}\n")

    approaches = list(next(iter(roles.values()))["scores"])
    agreement = {a: [] for a in approaches}
    quality = {a: [] for a in approaches}
    for role in roles.values():
        reference = [-position for position in role["judge_position"]]
        for a in approaches:
            agreement[a].append(spearman(role["scores"][a], reference))
            quality[a].append(top_five_quality(role["scores"][a], role["judge_grade"]))
    winners = [max(approaches, key=lambda a: agreement[a][i]) for i in range(len(roles))]

    print(f"{'approach':32} {'rank agreement':>14} {'top-5 quality':>14} {'best on':>8}")
    for a in sorted(approaches, key=lambda a: -statistics.mean(agreement[a])):
        print(f"{a:32} {statistics.mean(agreement[a]):14.2f} {statistics.mean(quality[a]):14.2f} {winners.count(a):8}")

    print(f"\nrank agreement by role  {'pipeline':>9} {'one score':>10}")
    for i, name in enumerate(roles):
        print(f"  {name:28} {agreement[PIPELINE][i]:5.2f} {agreement['model gives one score'][i]:10.2f}")

    # A person's yes / no on the data engineer profiles is the only human check there is.
    verdicts_path = "benchmark/human_verdicts.json"
    if "data_engineer" in roles and os.path.exists(verdicts_path):
        role, verdicts = roles["data_engineer"], json.load(open(verdicts_path))
        print(f"\ndata engineer: share of yes/no pairs ordered as {len(verdicts)} human verdicts would have them")
        print(f"  {'reference judge':32} {pairs_ordered_correctly([-p for p in role['judge_position']], role['profile_ids'], verdicts):.2f}")
        for a in approaches:
            print(f"  {a:32} {pairs_ordered_correctly(role['scores'][a], role['profile_ids'], verdicts):.2f}")


if __name__ == "__main__":
    main()
