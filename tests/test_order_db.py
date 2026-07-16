import sqlite3

import config
from order_db import OrderDB


def test_sync_state_to_sync_meta_migration(tmp_path):
    """A legacy DB with a sync_state table is migrated to sync_meta with its
    data preserved."""
    db_path = str(tmp_path / "legacy_orders.db")
    with sqlite3.connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute("CREATE TABLE sync_state (key TEXT PRIMARY KEY, value TEXT)")
        cur.execute("INSERT INTO sync_state (key, value) VALUES (?, ?)",
                    ("last_synced_amazon", "2026-05-01T00:00:00Z"))
        conn.commit()

    db = OrderDB(db_path)

    with sqlite3.connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cur.fetchall()}
    assert "sync_meta" in tables
    assert "sync_state" not in tables

    assert db.get_last_synced_timestamp("amazon") == "2026-05-01T00:00:00Z"


def test_set_and_get_last_synced_timestamp_per_platform():
    db = OrderDB()
    assert db.get_last_synced_timestamp("amazon") is None
    db.set_last_synced_timestamp("2026-06-01T00:00:00Z", "amazon")
    db.set_last_synced_timestamp("2026-06-02T00:00:00Z", "woocommerce")
    assert db.get_last_synced_timestamp("amazon") == "2026-06-01T00:00:00Z"
    assert db.get_last_synced_timestamp("woocommerce") == "2026-06-02T00:00:00Z"


def test_finances_round_trip_fetched_flag():
    """The 'fetched' flag must round-trip through upsert/get unchanged."""
    db = OrderDB()
    finances = {"fees": 12.5, "refunds": 0.0, "fetched": True}
    db.upsert_order(
        order_id="402-rt", platform=config.AMAZON_PLATFORM_KEY,
        status="Shipped", created_at="2026-05-18T10:00:00Z",
        updated_at="2026-05-18T10:00:00Z",
        items=[{"sku": "S", "price": 1.0, "quantity": 1, "title": "T"}],
        finances=finances,
    )
    got = db.get_order("402-rt")
    assert got["finances"] == finances
    assert got["finances"]["fetched"] is True


def test_upsert_preserves_items_and_finances_when_omitted():
    db = OrderDB()
    db.upsert_order(
        order_id="402-p", platform=config.AMAZON_PLATFORM_KEY,
        status="Shipped", created_at="c", updated_at="u",
        items=[{"sku": "S1"}], finances={"fees": 5.0, "fetched": True},
    )
    # Status-only update: items/finances not passed
    db.upsert_order(
        order_id="402-p", platform=config.AMAZON_PLATFORM_KEY,
        status="Canceled", created_at="c", updated_at="u2",
    )
    got = db.get_order("402-p")
    assert got["status"] == "Canceled"
    assert got["items"] == [{"sku": "S1"}]
    assert got["finances"] == {"fees": 5.0, "fetched": True}


def test_get_all_orders():
    db = OrderDB()
    db.upsert_order(order_id="a", platform="P1", status="Shipped",
                    created_at="c", updated_at="u")
    db.upsert_order(order_id="b", platform="P2", status="Canceled",
                    created_at="c", updated_at="u")
    orders = db.get_all_orders()
    assert {o["order_id"] for o in orders} == {"a", "b"}


def test_singleton_per_path_distinct_instances(tmp_path):
    a = OrderDB(str(tmp_path / "a.db"))
    a2 = OrderDB(str(tmp_path / "a.db"))
    b = OrderDB(str(tmp_path / "b.db"))
    assert a is a2
    assert a is not b
    assert a.db_path != b.db_path

    # Data written through one path is not visible through the other
    a.set_last_synced_timestamp("2026-01-01T00:00:00Z", "amazon")
    assert b.get_last_synced_timestamp("amazon") is None
