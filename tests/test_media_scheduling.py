import inspect

from src.workers import audio_worker, image_worker


def test_audio_reconciler_has_no_redis_transport_dependency():
    source = inspect.getsource(audio_worker.AudioTranscriptionWorker)
    assert "redis" not in source.lower()


def test_image_reconciler_has_no_redis_transport_dependency():
    source = inspect.getsource(image_worker.ImageExtractionWorker).lower()
    assert "redis" not in source
    assert "image_extraction_queue" not in source
