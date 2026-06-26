"""
Database manager for inventory tracking using PostgreSQL
"""

import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
from config import DB_CONFIG


class DatabaseManager:
    def __init__(self):
        self.conn = None
        self.connect()
        self.setup_tables()

    def connect(self):
        """Establish database connection."""
        try:
            self.conn = psycopg2.connect(**DB_CONFIG)
            self.conn.autocommit = True
            print("✅ Database connected successfully")
        except psycopg2.OperationalError as e:
            raise ConnectionError(f"Failed to connect to PostgreSQL: {e}")

    def setup_tables(self):
        """Create tables if they don't exist."""
        with self.conn.cursor() as cur:
            cur.execute("""
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
        print("✅ Tables ready")

    def insert_item(self, item: dict) -> int:
        """Insert a new inventory item."""
        with self.conn.cursor() as cur:
            cur.execute("""
                INSERT INTO inventory 
                    (item_name, quantity, unit, category, date_used, date_bought, notes, raw_input)
                VALUES 
                    (%(item_name)s, %(quantity)s, %(unit)s, %(category)s, 
                     %(date_used)s, %(date_bought)s, %(notes)s, %(raw_input)s)
                RETURNING id
            """, item)
            item_id = cur.fetchone()[0]

            # Log the insertion
            cur.execute("""
                INSERT INTO inventory_log (inventory_id, action, quantity_change, notes)
                VALUES (%s, 'added', %s, 'Initial entry')
            """, (item_id, item.get("quantity", 0)))

            return item_id

    def update_item(self, item_id: int, updates: dict):
        """Update an existing inventory item."""
        fields = ", ".join(f"{k} = %({k})s" for k in updates if k != "id")
        updates["updated_at"] = datetime.now()
        updates["id"] = item_id

        with self.conn.cursor() as cur:
            cur.execute(f"""
                UPDATE inventory SET {fields}, updated_at = %(updated_at)s
                WHERE id = %(id)s
            """, updates)

            if "quantity" in updates:
                cur.execute("""
                    INSERT INTO inventory_log (inventory_id, action, quantity_change, notes)
                    VALUES (%s, 'updated', %s, 'Quantity updated')
                """, (item_id, updates["quantity"]))

    def delete_item(self, item_id: int):
        """Delete an inventory item."""
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM inventory WHERE id = %s", (item_id,))

    def get_all_items(self, search: str = "", category: str = "") -> list:
        """Fetch all inventory items with optional filters."""
        query = "SELECT * FROM inventory WHERE 1=1"
        params = []

        if search:
            query += " AND item_name ILIKE %s"
            params.append(f"%{search}%")

        if category and category != "All":
            query += " AND category = %s"
            params.append(category)

        query += " ORDER BY updated_at DESC"

        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, params)
            return [dict(row) for row in cur.fetchall()]

    def get_categories(self) -> list:
        """Get unique categories."""
        with self.conn.cursor() as cur:
            cur.execute("SELECT DISTINCT category FROM inventory WHERE category IS NOT NULL ORDER BY category")
            return [row[0] for row in cur.fetchall()]

    def get_stats(self) -> dict:
        """Get summary statistics."""
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT 
                    COUNT(*) as total_items,
                    COUNT(DISTINCT category) as total_categories,
                    COUNT(DISTINCT item_name) as unique_items,
                    SUM(quantity) as total_quantity
                FROM inventory
            """)
            stats = dict(cur.fetchone())

            cur.execute("""
                SELECT COUNT(*) as today_entries
                FROM inventory
                WHERE DATE(created_at) = CURRENT_DATE
            """)
            stats.update(dict(cur.fetchone()))

            return stats

    def get_low_stock(self, threshold: float = 5.0) -> list:
        """Get items with low quantity."""
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT * FROM inventory 
                WHERE quantity <= %s 
                ORDER BY quantity ASC
            """, (threshold,))
            return [dict(row) for row in cur.fetchall()]

    def close(self):
        if self.conn:
            self.conn.close()
