"""Small safe additive schema migration for the classroom simulator.

For production use Alembic/Flask-Migrate. This migration only adds new nullable or
defaulted columns; it never drops or rewrites existing user data. MySQL does not
allow defaults on TEXT columns, so long-text policy fields are added as nullable.
"""
from sqlalchemy import inspect, text

COLUMNS = {
    "policies": {
        "policy_amount_limit": "NUMERIC(12, 2)",
        "insurer_name": "VARCHAR(120) DEFAULT 'Demo Insurance Company'",
        "premium_amount": "NUMERIC(12, 2) DEFAULT 0",
        "waiting_period": "TEXT NULL",
        "exclusions": "TEXT NULL",
        "cashless_details": "TEXT NULL",
        "terms_conditions": "TEXT NULL",
    },
    "members": {
        "date_of_birth": "DATE",
        "gender": "VARCHAR(30)",
        "is_covered": "BOOLEAN DEFAULT 1",
    },
    "claim_documents": {
        "original_filename": "VARCHAR(255)",
        "storage_path": "VARCHAR(500)",
        "content_type": "VARCHAR(120)",
        "file_size": "INTEGER",
        "uploaded_by_user_id": "INTEGER",
        "uploaded_at": "DATETIME",
        "version": "INTEGER DEFAULT 1",
        "replaces_document_id": "INTEGER",
        "resend_reason": "TEXT",
    },
    "claim_history": {
        "policy_id": "INTEGER",
        "member_id": "INTEGER",
        "event_type": "VARCHAR(60) DEFAULT 'status_change'",
    },
}


def migrate_additive_columns(engine):
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    with engine.begin() as connection:
        for table, columns in COLUMNS.items():
            if table not in tables:
                continue
            existing = {column["name"] for column in inspect(engine).get_columns(table)}
            for name, definition in columns.items():
                if name not in existing:
                    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}"))
