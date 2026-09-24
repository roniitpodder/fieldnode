import os
import sys
import tempfile
import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning)

# Use a throwaway DB — must be set BEFORE app modules are imported.
_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
