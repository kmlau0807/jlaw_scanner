"""Entry point: `python run.py scan` (Phase 1 headless scanner)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.main import main

if __name__ == "__main__":
    main()
