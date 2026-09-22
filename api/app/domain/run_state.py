"""States of a single provider run.

A run is one invocation of one provider on behalf of a task. Its states are
deliberately simpler than the task's: the task is Bevro's view of the work,
the run is a record of what one provider did.
"""

from enum import StrEnum


class RunState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    NEEDS_INPUT = "needs_input"
    NEEDS_APPROVAL = "needs_approval"


RUN_TERMINAL = frozenset({RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED})
