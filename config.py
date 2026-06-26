"""
Configuration for Inventory Tracker App
"""

# PostgreSQL configuration
DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "database": "inventory_db",
    "user": "postgres",
    "password": "postgres",  # Change this to your password
}

# Ollama configuration
OLLAMA_CONFIG = {
    "base_url": "http://localhost:11434",
    "model": "tinyllama",  # Default model; can be changed in UI
    "timeout": 60,
}

# App configuration
APP_CONFIG = {
    "title": "AI Inventory Tracker",
    "geometry": "1280x800",
    "min_size": (1100, 700),
    "low_stock_threshold": 5.0,
}
