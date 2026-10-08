"""Tiny fixtures for comparison math, change_type rules, and the distance rule."""
from __future__ import annotations

import math
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib


def test_change_type_rules() -> None:
    assert lib.change_type(0, 12) == "new_to_data"
    assert lib.change_type(8, 0) == "missing_from_data"
    assert lib.change_type(10, 15) == "added"
    assert lib.change_type(15, 10) == "dropped"
    assert lib.change_type(10, 10) == "unchanged"
    assert lib.change_type(0, 0) == "unchanged"


def test_comparison_math_change_and_pct() -> None:
    old_claims, new_claims = 200, 250
    change = new_claims - old_claims
    assert change == 50
    pct = lib.change_pct(old_claims, new_claims)
    assert pct is not None
    assert abs(pct - 25.0) < 1e-9
    assert lib.change_pct(0, 5) is None
    assert lib.change_type(old_claims, new_claims) == "added"
    # Roll-up: province rows sum to the company total.
    rows = [("ontario", 100, 130), ("quebec", 50, 40)]
    old_total = sum(r[1] for r in rows)
    new_total = sum(r[2] for r in rows)
    assert old_total == 150
    assert new_total == 170
    assert lib.change_type(old_total, new_total) == "added"
    assert abs(lib.change_pct(old_total, new_total) - (100 * 20 / 150)) < 1e-9


def test_box_edge_distance_overlap_is_zero() -> None:
    a = (-80.0, 48.0, -79.9, 48.1)
    b = (-79.95, 48.05, -79.8, 48.2)
    assert lib.box_edge_distance_km(a, a) == 0.0
    assert lib.box_edge_distance_km(a, b) == 0.0


def test_box_edge_distance_five_and_two_km() -> None:
    lat = 50.0
    km_per_lon = lib.KM_PER_DEG_LON_EQ * math.cos(math.radians(lat))
    # Two 0.01-deg boxes whose east-west gap is 5 km at lat 50.
    gap5 = 5.0 / km_per_lon
    a = (0.0, lat, 0.01, lat + 0.01)
    b = (0.01 + gap5, lat, 0.02 + gap5, lat + 0.01)
    d5 = lib.box_edge_distance_km(a, b)
    assert abs(d5 - 5.0) < 0.02
    gap2 = 2.0 / km_per_lon
    c = (0.01 + gap2, lat, 0.02 + gap2, lat + 0.01)
    d2 = lib.box_edge_distance_km(a, c)
    assert abs(d2 - 2.0) < 0.02
    assert d2 < 5.0 <= d5 or abs(d5 - 5.0) < 0.02


def test_north_south_distance_uses_lat_km() -> None:
    a = (-90.0, 60.0, -89.9, 60.1)
    # 1 degree of latitude, no overlap
    b = (-90.0, 61.1, -89.9, 61.2)
    d = lib.box_edge_distance_km(a, b)
    assert abs(d - lib.KM_PER_DEG_LAT) < 0.05


def test_cluster_rule() -> None:
    mean = 4.0
    assert lib.cluster_qualifies(10, mean, 10, 2.0) is True  # 10 >= 8
    assert lib.cluster_qualifies(9, mean, 10, 2.0) is False  # below min_n
    assert lib.cluster_qualifies(10, 6.0, 10, 2.0) is False  # 10 < 12
    assert lib.cluster_qualifies(25, 10.0, 25, 2.0) is True
    assert lib.cluster_qualifies(50, 10.0, 50, 3.0) is True  # 50 >= 30
    assert lib.cluster_qualifies(50, 20.0, 50, 3.0) is False  # 50 < 60
    assert lib.cluster_qualifies(60, 20.0, 50, 3.0) is True


def test_in_post_window_no_lookahead() -> None:
    cluster = date(2026, 6, 15)
    assert lib.in_post_window(date(2026, 6, 15), cluster) is True
    assert lib.in_post_window(date(2026, 6, 14), cluster) is False
    assert lib.in_post_window(date(2026, 9, 13), cluster) is True  # +90
    assert lib.in_post_window(date(2026, 9, 14), cluster) is False


def test_buy_kind_splits_open_market_and_placement() -> None:
    assert lib.buy_kind("sedi", "sedi-aem-x-2026-01-01-purchase-100-10") == "open_market"
    assert lib.buy_kind("sedi", "sedi-aem-x-2026-01-01-purchase-100-11") == "placement_or_private"
    assert lib.buy_kind("sedi", "sedi-aem-x-2026-01-01-purchase-100-15") == "placement_or_private"
    assert lib.buy_kind("form4", "form4-anything") == "open_market"


def test_junior_major_roles() -> None:
    mines = {"torex-gold", "seabridge-gold"}
    assert lib.role_for_company("Producer, Major", "agnico-eagle", mines) == "major"
    assert lib.role_for_company("Producer, Mid-tier", "iamgold", mines) == "major"
    assert lib.role_for_company("Explorer", "probe-gold", mines) == "junior"
    assert lib.role_for_company("Explorer, Prospect Generator", "midland-exploration", mines) == "junior"
    assert lib.role_for_company("Developer", "troilus-mining", mines) == "junior"
    assert lib.role_for_company("Producer, Junior", "dynacor", mines) == "junior"
    assert lib.role_for_company("Land Banks", "goldmining", mines) == "junior"
    assert lib.role_for_company("Royalty", "wheaton", mines) == "exclude"
    assert lib.role_for_company("Other", "major-drilling", mines) == "exclude"
    # Mine owner is major even if typed as a land bank.
    assert lib.role_for_company("Land Banks", "seabridge-gold", mines) == "major"


def test_valid_issue_date_drops_conversion_junk_and_future() -> None:
    assert lib.valid_issue_date("ontario", "2018-04-10") is None
    assert lib.valid_issue_date("ontario", "2025-03-01") == date(2025, 3, 1)
    assert lib.valid_issue_date("yukon", "8093340000") is None
    assert lib.valid_issue_date("nunavut", "7347888000") is None
    assert lib.valid_issue_date("british-columbia", "1969-06-01") is None
    assert lib.valid_issue_date("quebec", "2026-10-03") is None
    assert lib.valid_issue_date("quebec", "2026-10-02") == date(2026, 10, 2)


def test_month_list_counts_zeros_window() -> None:
    months = lib.month_list(date(2023, 7, 1), date(2026, 9, 30))
    assert months[0] == (2023, 7)
    assert months[-1] == (2026, 9)
    assert len(months) == 39
