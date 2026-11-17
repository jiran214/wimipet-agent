"""Defaults < private local overrides < process environment; never expose secrets."""
import os
from pathlib import Path
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]

def load_config(root=ROOT):
    return {**dotenv_values(root / '.env.default'), **dotenv_values(root / '.env'), **os.environ}
