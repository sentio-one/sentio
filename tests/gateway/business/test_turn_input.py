"""The working agent must be shown the verified facts, or it cannot cite any in its decision."""
from gateway.business.models import EpisodeRef, Fact
from gateway.business.policy import PolicyBundle
from gateway.business.turns import business_turn_input


def _policy(facts):
    return PolicyBundle(id="p", version="v1", facts=tuple(facts), actions=(), rules=("Be brief",),
                        criteria=(), missing=(), capabilities=())


def test_turn_input_lists_each_verified_fact_with_its_id():
    facts = [Fact("brief:offer", "Premium is free until 1 January 2027.", "brief.md", "a1"),
             Fact("flow:greeting", "Good morning. This is Anika.", "flow.md", "b2")]
    text = business_turn_input(None, None, EpisodeRef("ep-1", 3), _policy(facts))
    assert "[brief:offer] Premium is free until 1 January 2027." in text
    assert "[flow:greeting] Good morning. This is Anika." in text
    assert "fact_ids" in text and "cite" in text.lower()


def test_turn_input_without_facts_says_so_instead_of_inventing_a_list():
    text = business_turn_input(None, None, EpisodeRef("ep-1", 3), _policy([]))
    assert "No verified facts" in text
