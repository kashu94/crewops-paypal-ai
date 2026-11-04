"""The rules engine: duty arithmetic, the nine rules, and legality for one person on one trip.

Pure Python with no I/O, so every answer it gives can be reproduced from the data alone.
"""

from crewpay.engine.legality import Assessment, assess
from crewpay.engine.operation import NotFound, Operation, Positioning, build_operation
from crewpay.engine.rules import Finding, Outcome, RuleBook

__all__ = [
    "Assessment",
    "Finding",
    "NotFound",
    "Operation",
    "Outcome",
    "Positioning",
    "RuleBook",
    "assess",
    "build_operation",
]
