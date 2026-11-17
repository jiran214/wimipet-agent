"""Defaults < private local overrides < process environment; never expose secrets."""
import os
from pathlib import Path
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]

def load_config(root=ROOT):
    config = {**dotenv_values(root / '.env.default'), **dotenv_values(root / '.env'), **os.environ}
    # Provider-specific credentials avoid an unrelated inherited OpenAI key.
    if config.get('MIMO_API_KEY'):
        config['OPENAI_API_KEY'] = config['MIMO_API_KEY']
    return config
