"""Tests for the dialogue turn-state machine (Phase 3)."""

from core.dialogue.turn_state import TurnState, TurnStateMachine


def test_initial_state_is_idle():
    machine = TurnStateMachine()
    assert machine.state == TurnState.IDLE


def test_caller_starts_speaking_from_idle():
    machine = TurnStateMachine()
    is_barge_in = machine.caller_starts_speaking()
    assert machine.state == TurnState.CALLER_SPEAKING
    assert is_barge_in is False


def test_agent_starts_speaking():
    machine = TurnStateMachine()
    machine.agent_starts_speaking()
    assert machine.state == TurnState.AGENT_SPEAKING


def test_caller_speaking_while_agent_speaking_is_interruption():
    machine = TurnStateMachine()
    machine.agent_starts_speaking()
    is_barge_in = machine.caller_starts_speaking()
    assert machine.state == TurnState.INTERRUPTED
    assert is_barge_in is True


def test_acknowledge_interruption_returns_to_caller_speaking():
    machine = TurnStateMachine()
    machine.agent_starts_speaking()
    machine.caller_starts_speaking()
    machine.acknowledge_interruption()
    assert machine.state == TurnState.CALLER_SPEAKING


def test_acknowledge_interruption_is_a_no_op_outside_interrupted_state():
    machine = TurnStateMachine()
    machine.acknowledge_interruption()
    assert machine.state == TurnState.IDLE


def test_normal_turn_taking_cycle_returns_to_idle():
    machine = TurnStateMachine()
    machine.caller_starts_speaking()
    machine.caller_stops_speaking()
    assert machine.state == TurnState.WAITING

    machine.agent_starts_speaking()
    assert machine.state == TurnState.AGENT_SPEAKING

    machine.agent_stops_speaking()
    assert machine.state == TurnState.IDLE


def test_reset_returns_to_idle_from_any_state():
    machine = TurnStateMachine()
    machine.agent_starts_speaking()
    machine.caller_starts_speaking()
    machine.reset()
    assert machine.state == TurnState.IDLE
