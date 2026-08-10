import sys
from pathlib import Path


def resource_path(relative_path: str) -> Path:
    base_path = Path(getattr(sys, "_MEIPASS", Path.cwd()))
    return base_path / relative_path
