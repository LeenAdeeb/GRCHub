"""Schema upgrade: an existing database from before a migration existed keeps its data and gains the new tables."""
from backend.database.migrate import migrate
from tests.conftest import needs_db


@needs_db
def test_upgrade_keeps_data_and_is_repeatable(world):
    before = world.conn.execute("SELECT count(*) FROM requirements").fetchone()[0]
    assert before > 0
    # Recreate the state of a database made before migrations existed.
    world.conn.execute("DROP TABLE mapping_suggestions; DROP TABLE schema_migrations;")
    applied = migrate(world.conn)
    assert applied == ["0002_mapping_suggestions"]                       # baseline recorded, not re-applied
    assert world.conn.execute("SELECT count(*) FROM requirements").fetchone()[0] == before
    assert migrate(world.conn) == []
    assert world.conn.execute("SELECT count(*) FROM mapping_suggestions").fetchone()[0] == 0
