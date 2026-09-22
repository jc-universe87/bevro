import pytest

from app.domain.task_state import (
    TRANSITIONS,
    IllegalTransition,
    TaskState,
    assert_transition,
    can_transition,
    is_terminal,
)


def test_every_state_has_a_transition_row():
    assert set(TRANSITIONS) == set(TaskState)


def test_happy_path_is_legal():
    path = [TaskState.CREATED, TaskState.QUEUED, TaskState.WORKING, TaskState.COMPLETED]
    for current, nxt in zip(path, path[1:]):
        assert can_transition(current, nxt), f"{current} -> {nxt}"


@pytest.mark.parametrize("terminal", [TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED])
def test_terminal_states_cannot_move(terminal):
    assert is_terminal(terminal)
    for target in TaskState:
        assert not can_transition(terminal, target)


def test_created_cannot_jump_straight_to_completed():
    with pytest.raises(IllegalTransition):
        assert_transition(TaskState.CREATED, TaskState.COMPLETED)


def test_working_can_pause_for_input_and_resume():
    assert can_transition(TaskState.WORKING, TaskState.NEEDS_INPUT)
    assert can_transition(TaskState.NEEDS_INPUT, TaskState.WORKING)
    assert can_transition(TaskState.WORKING, TaskState.NEEDS_APPROVAL)
    assert can_transition(TaskState.NEEDS_APPROVAL, TaskState.WORKING)


def test_scheduled_and_monitoring_are_reachable():
    assert can_transition(TaskState.CREATED, TaskState.SCHEDULED)
    assert can_transition(TaskState.SCHEDULED, TaskState.QUEUED)
    assert can_transition(TaskState.WORKING, TaskState.MONITORING)
    assert can_transition(TaskState.MONITORING, TaskState.COMPLETED)


def test_anything_open_can_be_cancelled():
    for state in TaskState:
        if not is_terminal(state):
            assert can_transition(state, TaskState.CANCELLED), state
