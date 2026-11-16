"""The JD parser against ten real job descriptions. Every expected value was read off the JD by hand.
These call the model, so they are slow and need the .env:

    uv run --env-file .env --with pytest pytest tests/test_jd.py
"""
from functools import cache

import pytest

from talent.jd import parse_jd

YEARS = {
    "data_engineer": 1, "digital_marketing_manager": 5, "electrician": 7, "financial_analyst": None,
    "hr_business_partner": None, "mechanical_engineer": 2, "product_manager": 3, "registered_nurse": 1,
    "restaurant_general_manager": 5, "supply_chain_manager": 5,
}


@cache
def parsed(name):
    return parse_jd(open(f"samples/{name}.txt").read())


def hard_constraints(name):
    return {c.kind for c in parsed(name).constraints if c.hard}


@pytest.mark.parametrize("name, years", YEARS.items())
def test_stated_years(name, years):
    assert parsed(name).scope.years_stated == years


@pytest.mark.parametrize("name", YEARS)
def test_lists_stay_focused(name):
    assert 5 <= len(parsed(name).skills) <= 20
    assert 3 <= len(parsed(name).outcomes) <= 7


def test_licences_and_clearances_are_hard_constraints():
    assert "certification" in hard_constraints("electrician")           # journeyman licence
    assert "certification" in hard_constraints("registered_nurse")      # state RN licence
    assert {"certification", "work_authorization"} <= hard_constraints("mechanical_engineer")   # clearance, citizenship


def test_a_jd_with_no_stated_deal_breakers_has_none():
    assert not hard_constraints("data_engineer")
    assert not hard_constraints("product_manager")
