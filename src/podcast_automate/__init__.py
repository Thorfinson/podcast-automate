"""Tools for personal deep-dive podcast production."""

# The release, as `pla --version` shows it; it equals the version in pyproject.toml (test_core guards that).
__version__ = "0.2.0"
# The pipeline's identity in the input hashes of research runs and probes (research.py, runner.py) and in a run
# manifest's pipeline_version. A release leaves it as it is, so an open run still resumes; what changes the meaning
# of a run is carried by RESEARCH_VERSION and the prompt versions (D-206).
PIPELINE_IDENTITY = "0.1.0"
