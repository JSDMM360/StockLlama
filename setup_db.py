#!/usr/bin/env python3
"""
One-time setup: creates the PostgreSQL database for Inventory Tracker
Run this before launching the app for the first time.
"""

import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

# Edit these if your Postgres credentials differ
ADMIN_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "user": "postgres",
    "password": "postgres",
}

DB_NAME = "inventory_db"


def setup():
    print("=== Inventory Tracker — Database Setup ===\n")

    # 1. Connect to default 'postgres' database
    try:
        conn = psycopg2.connect(database="postgres", **ADMIN_CONFIG)
    except Exception as e:
        print(f"❌ Cannot connect to PostgreSQL: {e}")
        print("\nMake sure PostgreSQL is running and credentials in setup_db.py are correct.")
        return False

    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    cur = conn.cursor()

    # 2. Create database if it doesn't exist
    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DB_NAME,))
    if cur.fetchone():
        print(f"✅ Database '{DB_NAME}' already exists")
    else:
        cur.execute(f"CREATE DATABASE {DB_NAME}")
        print(f"✅ Created database '{DB_NAME}'")

    cur.close()
    conn.close()

    # 3. Connect to new database and create tables
    try:
        conn2 = psycopg2.connect(database=DB_NAME, **ADMIN_CONFIG)
        conn2.autocommit = True
        cur2 = conn2.cursor()

        cur2.execute("""
            CREATE TABLE IF NOT EXISTS inventory (
                id SERIAL PRIMARY KEY,
                item_name VARCHAR(255) NOT NULL,
                quantity NUMERIC(10, 2) NOT NULL,
                unit VARCHAR(50),
                category VARCHAR(100),
                date_used DATE,
                date_bought DATE,
                notes TEXT,
                raw_input TEXT,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS inventory_log (
                id SERIAL PRIMARY KEY,
                inventory_id INTEGER REFERENCES inventory(id) ON DELETE CASCADE,
                action VARCHAR(50),
                quantity_change NUMERIC(10, 2),
                notes TEXT,
                logged_at TIMESTAMP DEFAULT NOW()
            );
        """)
        print("✅ Tables created successfully")

        # Insert sample data
        cur2.execute("""
            INSERT INTO inventory (item_name, quantity, unit, category, date_bought, notes)
            VALUES 
                ('Rice', 10, 'kg', 'Food', CURRENT_DATE, 'Basmati rice'),
                ('Cooking Oil', 2, 'liters', 'Food', CURRENT_DATE, NULL),
                ('Notebooks', 5, 'pieces', 'Stationery', CURRENT_DATE, 'A4 ruled'),
                ('Hand Sanitizer', 3, 'bottles', 'Cleaning', CURRENT_DATE, '500ml each')
            ON CONFLICT DO NOTHING;
        """)
        print("✅ Sample data inserted")

        cur2.close()
        conn2.close()

    except Exception as e:
        print(f"❌ Table creation failed: {e}")
        return False

    print("\n🎉 Setup complete! You can now run: python main.py")
    return True


if __name__ == "__main__":
    setup()
