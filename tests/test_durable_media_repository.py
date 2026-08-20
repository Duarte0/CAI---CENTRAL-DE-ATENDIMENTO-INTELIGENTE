import inspect

from src.core import db, durable_media_repository


def test_durable_media_persistence_is_owned_by_repository() -> None:
    public_operations = (
        "reserve_transcription",
        "set_transcription_status",
        "get_transcription",
        "get_completed_transcriptions",
        "recover_stale_transcriptions",
        "release_transcription_publication",
        "reserve_image_extraction",
        "set_image_extraction_status",
        "get_image_extraction",
        "get_completed_image_extractions",
        "recover_stale_image_extractions",
        "release_image_publication",
        "get_pending_content_extractions",
    )

    for name in public_operations:
        operation = getattr(db, name)
        assert operation is getattr(durable_media_repository, name)
        assert inspect.getmodule(operation) is durable_media_repository

    assert not hasattr(db, "_reserve_content_sync")
    assert not hasattr(db, "_set_content_status_sync")
    assert not hasattr(db, "_claim_due_content_sync")
    assert not hasattr(db, "_get_pending_content_extractions_sync")
