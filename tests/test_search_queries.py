"""Tests for QuerySpec construction, negative site exclusions, and the
default query set.  Pure unit tests -- no live Serper calls."""
from __future__ import annotations

import pytest

from app.search import (
    DEFAULT_QUERIES,
    DEFAULT_QUERY_SPECS,
    NEGATIVE_SITE_EXCLUSIONS,
    VALID_CATEGORIES,
    QuerySpec,
    build_queries,
    ordered_query_specs,
)


# ---------------------------------------------------------------------------
# QuerySpec validation
# ---------------------------------------------------------------------------


class TestQuerySpecValidation:
    def test_valid_spec_constructs(self):
        spec = QuerySpec("AI agency", "ai-consulting", 50)
        assert spec.query == "AI agency"
        assert spec.category == "ai-consulting"
        assert spec.priority == 50

    def test_empty_query_rejected(self):
        with pytest.raises(ValueError):
            QuerySpec("", "ai-consulting", 50)

    def test_whitespace_query_rejected(self):
        with pytest.raises(ValueError):
            QuerySpec("   ", "ai-consulting", 50)

    def test_unknown_category_rejected(self):
        with pytest.raises(ValueError):
            QuerySpec("AI agency", "not-a-category", 50)

    @pytest.mark.parametrize("cat", sorted(VALID_CATEGORIES))
    def test_all_valid_categories_accepted(self, cat):
        spec = QuerySpec("q", cat, 1)
        assert spec.category == cat


# ---------------------------------------------------------------------------
# build_query / negative site exclusions
# ---------------------------------------------------------------------------


class TestBuildQuery:
    def test_includes_all_negative_exclusions_exactly_once(self):
        spec = QuerySpec("AI agency", "ai-consulting", 50)
        q = spec.build_query()
        for excl in NEGATIVE_SITE_EXCLUSIONS:
            assert q.count(excl) == 1, f"{excl} should appear exactly once"

    def test_base_query_present(self):
        spec = QuerySpec("AI automation agency", "automation", 50)
        assert "AI automation agency" in spec.build_query()

    def test_deterministic(self):
        spec = QuerySpec("AI agency", "ai-consulting", 50)
        assert spec.build_query() == spec.build_query()

    def test_exclusions_appended_after_base(self):
        spec = QuerySpec("my base query", "ai-consulting", 50)
        q = spec.build_query()
        # base query comes first
        assert q.startswith("my base query ")
        # all exclusions come after
        for excl in NEGATIVE_SITE_EXCLUSIONS:
            assert q.index(excl) > q.index("my base query")


# ---------------------------------------------------------------------------
# build_queries / ordered_query_specs
# ---------------------------------------------------------------------------


class TestBuildQueries:
    def test_default_non_empty(self):
        qs = build_queries()
        assert len(qs) > 0
        for q in qs:
            assert isinstance(q, str) and q.strip()

    def test_no_duplicates(self):
        qs = build_queries()
        assert len(qs) == len(set(qs)), "duplicate full queries detected"

    def test_bounded_count(self):
        qs = build_queries()
        assert 8 <= len(qs) <= 12

    def test_ordered_by_descending_priority(self):
        specs = ordered_query_specs()
        priorities = [s.priority for s in specs]
        assert priorities == sorted(priorities, reverse=True)

    def test_deterministic_order(self):
        assert build_queries() == build_queries()

    def test_each_query_has_exclusions(self):
        for q in build_queries():
            for excl in NEGATIVE_SITE_EXCLUSIONS:
                assert excl in q


# ---------------------------------------------------------------------------
# DEFAULT_QUERY_SPECS / DEFAULT_QUERIES compatibility
# ---------------------------------------------------------------------------


class TestDefaultQuerySet:
    def test_all_categories_valid(self):
        for spec in DEFAULT_QUERY_SPECS:
            assert spec.category in VALID_CATEGORIES

    def test_all_queries_non_empty(self):
        for spec in DEFAULT_QUERY_SPECS:
            assert spec.query.strip()

    def test_no_duplicate_specs(self):
        keys = [(s.query, s.category) for s in DEFAULT_QUERY_SPECS]
        assert len(keys) == len(set(keys))

    def test_default_queries_alias_matches_specs(self):
        # DEFAULT_QUERIES is the backwards-compatible list[str] alias
        assert DEFAULT_QUERIES == [s.query for s in DEFAULT_QUERY_SPECS]

    def test_default_queries_count_matches_specs(self):
        assert len(DEFAULT_QUERIES) == len(DEFAULT_QUERY_SPECS)

    def test_priority_is_not_commercial_score(self):
        # priorities should be in a discovery-preference range, not 0-100
        # commercial scores; just sanity check they are ints and distinct-ish
        for spec in DEFAULT_QUERY_SPECS:
            assert isinstance(spec.priority, int)
            assert spec.priority > 0
