"""Put vendor/ on sys.path so tests import the payload package as `compress`."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "vendor"))
