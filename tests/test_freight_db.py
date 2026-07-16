import sqlite3

from freight_db import FreightDB


def _all_rows(db):
    with sqlite3.connect(db.db_path) as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, channel_order_id, awb_number, freight_amount FROM shipments ORDER BY id"
        )
        return cur.fetchall()


def test_upsert_by_awb_then_order_id_updates_same_row():
    db = FreightDB()
    # First insert keyed by both
    db.insert_freight(channel_order_id="402-1", shiprocket_order_id="10",
                      awb_number="AWB-1", freight_amount=50.0)
    # Update via AWB only
    db.insert_freight(channel_order_id=None, shiprocket_order_id="10",
                      awb_number="AWB-1", freight_amount=60.0)
    # Update via order id only
    db.insert_freight(channel_order_id="402-1", shiprocket_order_id="10",
                      awb_number=None, freight_amount=70.0)

    rows = _all_rows(db)
    assert len(rows) == 1
    _id, order_id, awb, amount = rows[0]
    assert order_id == "402-1"
    assert awb == "AWB-1"
    assert amount == 70.0


def test_conflicting_two_row_case_prefers_awb_row():
    """AWB matches row A, order id matches row B: the AWB row wins and row B
    is left untouched (no silent row merging/deletion)."""
    db = FreightDB()
    db.insert_freight(channel_order_id=None, shiprocket_order_id="1",
                      awb_number="AWB-X", freight_amount=10.0)      # row A
    db.insert_freight(channel_order_id="402-9", shiprocket_order_id="2",
                      awb_number=None, freight_amount=20.0)         # row B

    # Conflicting upsert: AWB→A, order→B
    db.insert_freight(channel_order_id="402-9", shiprocket_order_id="3",
                      awb_number="AWB-X", freight_amount=99.0)

    rows = _all_rows(db)
    assert len(rows) == 2
    by_awb = {r[2]: r for r in rows}
    # Row A (AWB match) got the new amount but NOT the stolen order id
    assert by_awb["AWB-X"][3] == 99.0
    assert by_awb["AWB-X"][1] is None
    # Row B untouched
    assert by_awb[None][1] == "402-9"
    assert by_awb[None][3] == 20.0


def test_keyless_insert_rejected():
    db = FreightDB()
    db.insert_freight(channel_order_id=None, shiprocket_order_id="55",
                      awb_number=None, freight_amount=42.0)
    assert _all_rows(db) == []

    # Empty strings are normalized to None and rejected the same way
    db.insert_freight(channel_order_id="", shiprocket_order_id="55",
                      awb_number="", freight_amount=42.0)
    assert _all_rows(db) == []


def test_get_freight_maps_split_shape():
    db = FreightDB()
    db.insert_freight(channel_order_id="402-1", shiprocket_order_id="1",
                      awb_number="AWB-1", freight_amount=50.0)
    db.insert_freight(channel_order_id="402-2", shiprocket_order_id="2",
                      awb_number=None, freight_amount=30.0)
    db.insert_freight(channel_order_id=None, shiprocket_order_id="3",
                      awb_number="AWB-3", freight_amount=25.0)

    maps = db.get_freight_maps()
    assert set(maps.keys()) == {"by_awb", "by_order_id"}
    assert maps["by_awb"] == {"AWB-1": 50.0, "AWB-3": 25.0}
    assert maps["by_order_id"] == {"402-1": 50.0, "402-2": 30.0}


def test_get_all_freight_costs_flat_dict_compat():
    db = FreightDB()
    db.insert_freight(channel_order_id="402-1", shiprocket_order_id="1",
                      awb_number="AWB-1", freight_amount=50.0)
    costs = db.get_all_freight_costs()
    assert costs == {"AWB-1": 50.0, "402-1": 50.0}


def test_is_order_in_db():
    db = FreightDB()
    db.insert_freight(channel_order_id="402-1", shiprocket_order_id="1",
                      awb_number="AWB-1", freight_amount=50.0)
    assert db.is_order_in_db(channel_order_id="402-1") is True
    assert db.is_order_in_db(awb_number="AWB-1") is True
    assert db.is_order_in_db(channel_order_id="402-nope") is False


def test_singleton_per_path(tmp_path):
    a = FreightDB(str(tmp_path / "a.db"))
    a2 = FreightDB(str(tmp_path / "a.db"))
    b = FreightDB(str(tmp_path / "b.db"))
    assert a is a2
    assert a is not b
