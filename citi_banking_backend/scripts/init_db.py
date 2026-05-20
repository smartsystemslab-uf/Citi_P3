import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from app.seed import init_db

if __name__ == "__main__":
    init_db(seed=True)
    print("Database initialized in ./data/citi_banking.db")
