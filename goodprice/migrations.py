"""Additive offer migration; the caller owns the transaction."""
from sqlalchemy import inspect

from goodprice.models import (
    ListingOffer,
    ManualQuote,
    OfferEvaluationRecord,
    OfferObservation,
    SchemaVersion,
)


def migrate_offer_schema(session) -> None:
    connection = session.connection()
    tables = set(inspect(connection).get_table_names())
    legacy = "schema_versions" not in tables and "watch_tasks" in tables
    for model in (SchemaVersion, ListingOffer, OfferObservation, ManualQuote, OfferEvaluationRecord):
        model.__table__.create(connection, checkfirst=True)
    if session.get(SchemaVersion, 1) is None:
        session.add(SchemaVersion(id=1, version=1, legacy_install=legacy))
    else:
        session.get(SchemaVersion, 1).version = 1
