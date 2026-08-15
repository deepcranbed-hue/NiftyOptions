"""
Capture-layer §1/§1.4 tests (capture_layer_fix_brief).

- Acceptance row 1: the Breeze field mapping is traceable to fixtures/breeze_chain_raw.json,
  field-for-field (best_bid_price->bid, best_offer_price->ask, ltp->ltp, ...).
- §1.4: the save path persists absent fields as NULL, never coerced to 0.0.

No mocked dependencies.
"""
import json
import os
import sqlite3
import tempfile

import pytest

import chain_store
from backend.quant.breeze_loader import process_breeze_chain

FIXTURE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                       "fixtures", "breeze_chain_raw.json")


def _load_fixture():
    with open(FIXTURE) as f:
        return json.load(f)["Success"]


# ---------- Acceptance row 1: mapping traceable to the raw document ----------

def test_breeze_mapping_matches_fixture_field_for_field():
    raw = _load_fixture()
    rows, spot = process_breeze_chain(raw, days_to_expiry=2.0)
    assert spot == pytest.approx(24410.5)             # spot_price
    row = next(r for r in rows if r["strike"] == 24400.0)

    # Call side: best_bid_price=104.5, best_offer_price=106.5, ltp=105.5
    assert row["call_bid"] == pytest.approx(104.5)
    assert row["call_ask"] == pytest.approx(106.5)
    assert row["call_ltp"] == pytest.approx(105.5)
    assert row["call_mid"] == pytest.approx((104.5 + 106.5) / 2.0)   # derived
    assert row["call_quote_state"] == "TWO_SIDED"

    # Put side: best_bid_price=94.5, best_offer_price=96.5, ltp=95.5
    assert row["put_bid"] == pytest.approx(94.5)
    assert row["put_ask"] == pytest.approx(96.5)
    assert row["put_ltp"] == pytest.approx(95.5)
    assert row["put_mid"] == pytest.approx((94.5 + 96.5) / 2.0)
    assert row["put_quote_state"] == "TWO_SIDED"


def test_mid_is_derived_not_fetched():
    # There is no `mid` field in the raw Breeze document — it must be computed, and only
    # from a genuine two-sided quote.
    raw = _load_fixture()
    assert all("best_bid_price" in it and "mid" not in it for it in raw)
    rows, _ = process_breeze_chain(raw, days_to_expiry=2.0)
    assert rows[0]["call_mid"] is not None


# ---------- §1.4: no silent 0.0 defaults in the save path ----------

def test_absent_quote_persists_as_null_not_zero():
    raw = _load_fixture()
    rows, spot = process_breeze_chain(raw, days_to_expiry=2.0)
    # Blank one side's quotes on the parsed row (simulating an absent field).
    rows[0]["call_bid"] = None
    rows[0]["call_ask"] = None

    db = tempfile.mktemp(suffix=".db")
    try:
        cid = chain_store.save_from_json_rows(
            rows, expiry="2026-07-31", spot=spot, vix=12.3,
            captured_at="2026-07-06T04:00:00.000Z", db=db)
        assert cid is not None
        con = sqlite3.connect(db)
        val = con.execute(
            "SELECT call_bid, call_ask FROM chain_rows WHERE strike = 24400").fetchone()
        con.close()
        # The old code wrote 0.0 here (the defect). Post-fix it must be NULL.
        assert val == (None, None)
    finally:
        os.remove(db)
