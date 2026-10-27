from sqlalchemy import inspect, text

from goodprice.db import init_db, make_session_factory, migrate_schema
from goodprice.models import Listing, ListingOffer, OfferObservation, SchemaVersion
from goodprice.services.task_service import TaskService


def test_old_database_migration_is_idempotent_and_preserves_legacy_values(tmp_db):
    factory = make_session_factory(tmp_db)
    with factory.kw["bind"].begin() as conn:
        conn.execute(text("CREATE TABLE watch_tasks (id INTEGER PRIMARY KEY, keyword TEXT)"))
        conn.execute(text("INSERT INTO watch_tasks VALUES (1, 'iphone')"))
        conn.execute(text("CREATE TABLE listings (id INTEGER PRIMARY KEY, external_id TEXT, "
                          "platform TEXT, title TEXT, price FLOAT, url TEXT)"))
        conn.execute(text("INSERT INTO listings VALUES (2, 'external', 'xianyu', 'old', 99, 'url')"))
    migrate_schema(factory)
    migrate_schema(factory)
    assert "listing_offers" in inspect(factory.kw["bind"]).get_table_names()
    with factory() as session:
        assert session.execute(text("SELECT keyword FROM watch_tasks")).scalar() == "iphone"
        assert session.execute(text("SELECT external_id FROM listings")).scalar() == "external"
        assert session.query(ListingOffer).count() == 0
        marker = session.get(SchemaVersion, 1)
        assert marker.legacy_install is True
        assert marker.version == 3


def test_task_version_changes_only_with_meaningful_updates(session_factory):
    service = TaskService(session_factory)
    task = service.create_task({"keyword": "x", "target_variant": "256GB"})
    assert task.target_variant == "256GB"
    assert service.update_task(task.id, {"keyword": "x"}).version == task.version
    assert service.update_task(task.id, {"target_variant": "128GB"}).version == task.version + 1


def test_init_db_remembers_legacy_install_before_creating_new_tables(tmp_db):
    factory = make_session_factory(tmp_db)
    with factory.kw["bind"].begin() as conn:
        conn.execute(text("CREATE TABLE watch_tasks (id INTEGER PRIMARY KEY, keyword TEXT)"))
    init_db(tmp_db)
    migrate_schema(factory)
    with factory() as session:
        assert session.get(SchemaVersion, 1).legacy_install is True


def test_task_delete_preserves_listing_history_without_dangling_task_id(session_factory):
    service = TaskService(session_factory)
    task = service.create_task({"keyword": "x"})
    with session_factory() as session:
        listing = Listing(platform="xianyu", external_id="e", title="t", price=1,
                          url="u", task_id=task.id)
        session.add(listing)
        session.commit()
        listing_id = listing.id
    assert service.delete_task(task.id)
    with session_factory() as session:
        assert session.get(Listing, listing_id).task_id is None


def test_listing_delete_cascades_offer_observations(session_factory):
    with session_factory() as session:
        listing = Listing(platform="xianyu", external_id="e", title="t", price=1, url="u")
        session.add(listing)
        session.flush()
        offer = ListingOffer(listing_id=listing.id, offer_key="sku:1", name="256GB")
        session.add(offer)
        session.flush()
        session.add(OfferObservation(offer_id=offer.id, fingerprint="f", payload={}))
        session.commit()
        session.delete(listing)
        session.commit()
        assert session.query(ListingOffer).count() == 0
        assert session.query(OfferObservation).count() == 0

def test_migration_advances_schema_marker_without_downgrading_future_version(session_factory):
    from goodprice.models import SchemaVersion
    with session_factory() as session:
        session.add(SchemaVersion(id=1, version=1, legacy_install=True))
        session.commit()
    migrate_schema(session_factory)
    with session_factory() as session:
        marker = session.get(SchemaVersion, 1)
        assert marker.version == 3 and marker.legacy_install
        marker.version = 99
        session.commit()
    migrate_schema(session_factory)
    with session_factory() as session:
        assert session.get(SchemaVersion, 1).version == 99
