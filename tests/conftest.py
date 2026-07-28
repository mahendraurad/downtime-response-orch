"""Test-suite isolation from optional cloud infrastructure."""
import os


# Unit and API tests must never depend on, mutate, or incur cost against Azure.
os.environ.setdefault("HITL_REPOSITORY", "sqlite")
os.environ["LANGSMITH_TRACING"] = "false"
