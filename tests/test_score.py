"""The scoring arithmetic and profile trimming. No model or network calls."""
from talent.rubric import Criterion, Filters, Rubric
from talent.score import Assessment, Finding, Level, score
from talent.search import cost, trim

CRITERIA = [
    Criterion(id="licence", looks_for="holds the licence", weight=3, must=True),
    Criterion(id="core", looks_for="does the core work", weight=3, must=False),
    Criterion(id="extra", looks_for="nice extra", weight=2, must=False),
]


def rubric(employers=()):
    return Rubric(criteria=CRITERIA, level="mid", comparable_employers=list(employers), target_level="",
                  filters=Filters(titles=[], locations=[], experience=[]))


def reading(role=4, domain=4, level="mid", employers="comparable", contradicted=(), **strength):
    strength = {"licence": 4, "core": 4, "extra": 4, **strength}
    findings = [Finding(id=name, strength=value, contradicted=name in contradicted, evidence="")
                for name, value in strength.items()]
    return Assessment(role_match=role, domain_match=domain, findings=findings, summary="",
                      level=Level(relevant_years=3, equivalent=level, employers=employers, reasoning=""))


def test_perfect_match_at_level_is_100():
    assert score(rubric(), reading()) == 100


def test_points_split_30_role_30_domain_40_evidence():
    assert score(rubric(), reading(role=0)) == 70
    assert score(rubric(), reading(domain=2)) == 85
    assert score(rubric(), reading(licence=0, core=0, extra=0)) == 60


def test_evidence_is_weighted_by_criterion():
    assert score(rubric(), reading(extra=0)) == 90       # the weight-2 criterion is a quarter of the evidence
    assert score(rubric(), reading(core=2)) == 92.5      # half strength on a weight-3 criterion


def test_contradicted_deal_breaker_scores_zero_but_a_silent_profile_does_not():
    assert score(rubric(), reading(licence=0, contradicted=("licence",))) == 0
    assert score(rubric(), reading(licence=0)) == 85
    assert score(rubric(), reading(core=0, contradicted=("core",))) == 85    # core is not a deal-breaker


def test_level_misses_cost_the_same_in_both_directions():
    assert score(rubric(), reading(level="entry")) == 75
    assert score(rubric(), reading(level="senior")) == 75
    assert score(rubric(), reading(level="staff")) == 40
    assert score(rubric(), reading(level="principal")) == 20


def test_employers_only_count_when_the_rubric_restricts_them():
    assert score(rubric(), reading(employers="not comparable")) == 100
    assert score(rubric(["big tech"]), reading(employers="not comparable")) == 60
    assert score(rubric(["big tech"]), reading(employers="unknown")) == 85


def test_cost_counts_pages_and_profiles():
    assert cost(25) == 0.20
    assert cost(40) == 0.36
    assert cost(100) == 0.80


def test_trim_keeps_what_a_recruiter_reads():
    raw = {
        "id": "x", "firstName": "A", "lastName": "B", "linkedinUrl": "u", "headline": "h",
        "about": "a" * 5000, "location": {"linkedinText": "Pune"}, "profilePicture": {"url": "..."},
        "skills": [{"name": "SQL"}] * 60,
        "experience": [{"position": "Engineer", "companyName": "Acme", "description": None,
                            "companyLinkedinUrl": "linkedin.com/company/acme",
                        "startDate": {"text": "Jan 2020"}, "endDate": {"text": "Present"},
                        "companyLogo": {"url": "..."}}],
    }
    profile = trim(raw)
    assert profile["name"] == "A B"
    assert profile["location"] == "Pune"
    assert len(profile["about"]) == 1500
    assert len(profile["skills"]) == 40
    assert "profilePicture" not in profile
    assert profile["experience"][0] == {"title": "Engineer", "company": "Acme",
                                        "company_url": "linkedin.com/company/acme", "type": None, "from": "Jan 2020",
                                        "to": "Present", "duration": None, "description": ""}
