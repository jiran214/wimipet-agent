"""无需安装：python modules/desktop-world/run.py [--native]。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from desktop_world.app import main

if __name__ == "__main__":
    main()
