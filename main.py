#!/usr/bin/env python3
"""
Inventory Tracker - AI-powered inventory management using Ollama + PostgreSQL
"""

import sys
from app import InventoryApp

if __name__ == "__main__":
    app = InventoryApp()
    app.run()
