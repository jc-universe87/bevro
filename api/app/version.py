"""The one place Bevro's version is written.

Everything that shows a version - the API, Settings, the docs build - reads
it from here. `web/package.json` carries the same number for npm's sake and
is checked against this one by the release checklist.
"""

__version__ = "0.1.0"
