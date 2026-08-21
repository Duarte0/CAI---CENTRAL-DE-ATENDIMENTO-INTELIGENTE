import inspect

from src.core import conversation_cycle_repository, db


def test_cycle_persistence_implementation_is_owned_by_repository() -> None:
    public_operations = (
        "create_open_cycle",
        "close_cycle",
        "get_cycle",
        "get_latest_cycle",
        "get_previous_cycle",
        "list_cycles",
        "transition_cycle",
        "claim_cycle",
        "get_recoverable_cycles",
        "release_cycle_publication",
        "wake_unblocked_media_cycles",
        "save_cycle_messages",
        "get_content_states",
        "get_cycle_metrics",
        "get_cycle_result",
    )

    for name in public_operations:
        operation = getattr(db, name)
        assert operation is getattr(conversation_cycle_repository, name)
        assert inspect.getmodule(operation) is conversation_cycle_repository

    assert not hasattr(db, "_create_open_cycle_sync")
    assert not hasattr(db, "_get_cycle_result_sync")
