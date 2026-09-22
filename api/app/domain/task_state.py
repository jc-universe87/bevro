"""Task states and the legal transitions between them.

The state names are the product contract from the build brief. The transition
table is the single place that decides which moves are allowed; everything
else asks it rather than checking strings by hand.
"""

from enum import StrEnum


class TaskState(StrEnum):
    CREATED = "created"
    QUEUED = "queued"
    WORKING = "working"
    WAITING = "waiting"
    NEEDS_INPUT = "needs_input"
    NEEDS_APPROVAL = "needs_approval"
    SCHEDULED = "scheduled"
    MONITORING = "monitoring"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATES: frozenset[TaskState] = frozenset(
    {TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED}
)

# States in which a task can be paused / diverted / resolved by someone else.
_ACTIVE = {
    TaskState.WORKING,
    TaskState.WAITING,
    TaskState.NEEDS_INPUT,
    TaskState.NEEDS_APPROVAL,
    TaskState.SCHEDULED,
    TaskState.MONITORING,
}

TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    # A task may need something from the user before it can even be queued
    # (for example: which project to work in).
    TaskState.CREATED: frozenset({TaskState.QUEUED, TaskState.SCHEDULED, TaskState.NEEDS_INPUT, TaskState.CANCELLED, TaskState.FAILED}),
    TaskState.QUEUED: frozenset({TaskState.WORKING, TaskState.NEEDS_INPUT, TaskState.CANCELLED, TaskState.FAILED}),
    TaskState.SCHEDULED: frozenset({TaskState.QUEUED, TaskState.CANCELLED}),
    TaskState.WORKING: frozenset(
        (_ACTIVE - {TaskState.WORKING}) | {TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED}
    ),
    TaskState.WAITING: frozenset({TaskState.WORKING, TaskState.FAILED, TaskState.CANCELLED}),
    TaskState.NEEDS_INPUT: frozenset({TaskState.WORKING, TaskState.QUEUED, TaskState.CANCELLED}),
    TaskState.NEEDS_APPROVAL: frozenset({TaskState.WORKING, TaskState.QUEUED, TaskState.CANCELLED, TaskState.FAILED}),
    TaskState.MONITORING: frozenset({TaskState.WORKING, TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED}),
    TaskState.COMPLETED: frozenset(),
    TaskState.FAILED: frozenset(),
    TaskState.CANCELLED: frozenset(),
}


# A person may ask for a failed task to be tried again. That is not a state
# transition an agent or a timer can make: FAILED stays terminal for the
# table above; only the task service's retry_task() moves it back to QUEUED.
RETRYABLE_STATES: frozenset[TaskState] = frozenset({TaskState.FAILED})


def can_retry(state: TaskState) -> bool:
    return state in RETRYABLE_STATES


class IllegalTransition(Exception):
    def __init__(self, current: TaskState, target: TaskState) -> None:
        self.current = current
        self.target = target
        super().__init__(f"cannot move a task from {current} to {target}")


def can_transition(current: TaskState, target: TaskState) -> bool:
    return target in TRANSITIONS[current]


def assert_transition(current: TaskState, target: TaskState) -> None:
    if not can_transition(current, target):
        raise IllegalTransition(current, target)


def is_terminal(state: TaskState) -> bool:
    return state in TERMINAL_STATES
