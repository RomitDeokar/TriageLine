"""Back-compat shim: the adapter driver lives in scripts/fdb_probe.py."""
from scripts.fdb_probe import _registry, drive, run  # noqa: F401
