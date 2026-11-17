import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from config import load_config

class ConfigTests(unittest.TestCase):
    def test_mimo_key_isolated_from_inherited_openai_key(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'.env.default').write_text('OPENAI_MODEL=default\n',encoding='utf-8')
            (root/'.env').write_text('MIMO_API_KEY=local-example\nOPENAI_MODEL=mimo-v2.6-pro\n',encoding='utf-8')
            with patch.dict(os.environ,{'OPENAI_API_KEY':'unrelated-example'},clear=True):
                config=load_config(root)
                self.assertEqual(config['OPENAI_API_KEY'],'local-example')
                self.assertEqual(config['OPENAI_MODEL'],'mimo-v2.6-pro')
            with patch.dict(os.environ,{'MIMO_API_KEY':'process-example'},clear=True):
                self.assertEqual(load_config(root)['OPENAI_API_KEY'],'process-example')
