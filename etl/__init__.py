# Ensure Azure's bundled .python_packages/ is on sys.path.
# When function_app.py runs the ETL as a subprocess (e.g. `python -m etl...`),
# the child process doesn't inherit sys.path mutations from the parent.
# Without this shim, subprocesses can't find `requests`, `psycopg2`, etc.
import os as _os
import sys as _sys

_pkg = _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
    ".python_packages", "lib", "site-packages",
)
if _os.path.isdir(_pkg) and _pkg not in _sys.path:
    _sys.path.insert(0, _pkg)

from . import (
    sync_lookups,
    sync_companies,
    sync_workers,
    sync_equipment,
    sync_certifications,
    sync_forms,
    sync_incidents,
    sync_attachments,
)
