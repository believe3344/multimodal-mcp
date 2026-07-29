from state import MultimodalState, make_cache_key, make_image_id


def test_cache_key_changes_with_request_parameters(png_bytes: bytes) -> None:
    first = make_cache_key([png_bytes], "model-a", "chat", "high", "read")
    assert first == make_cache_key([png_bytes], "model-a", "chat", "high", "read")
    assert first != make_cache_key([png_bytes], "model-b", "chat", "high", "read")
    assert first != make_cache_key([png_bytes], "model-a", "chat", "low", "read")
    assert first != make_cache_key([png_bytes], "model-a", "chat", "high", "compare")


def test_image_id_is_content_addressed(png_bytes: bytes) -> None:
    image_id = make_image_id(png_bytes)
    assert image_id.startswith("img_")
    assert image_id == make_image_id(png_bytes)
    assert image_id != make_image_id(png_bytes + b"x")


def test_cache_expires_and_tracks_hits_and_misses() -> None:
    now = [10.0]
    state = MultimodalState(cache_ttl=5, clock=lambda: now[0])
    state.put_cached("key", "value")
    assert state.get_cached("key") == "value"
    now[0] = 16.0
    assert state.get_cached("key") is None
    assert state.stats()["cache_hits"] == 1
    assert state.stats()["cache_misses"] == 1


def test_image_store_evicts_lru_and_respects_byte_limit() -> None:
    state = MultimodalState(image_max_entries=2, image_max_bytes=6)
    first = state.put_image(b"aaa", "image/png")
    second = state.put_image(b"bb", "image/png")
    assert state.get_image(first) is not None
    third = state.put_image(b"cccc", "image/jpeg")
    assert state.get_image(second) is None
    assert state.get_image(first) is None
    assert state.get_image(third) is not None
    assert state.stats()["image_bytes"] == 4


def test_clear_is_targeted(png_bytes: bytes) -> None:
    state = MultimodalState()
    state.put_cached("key", "value")
    state.put_image(png_bytes, "image/png")
    state.clear("cache")
    assert state.stats()["cache_entries"] == 0
    assert state.stats()["image_entries"] == 1
    state.clear("images")
    assert state.stats()["image_entries"] == 0


def test_peek_cached_does_not_change_hit_or_miss_counters() -> None:
    state = MultimodalState()
    state.put_cached("known", "value")
    assert state.peek_cached("known") == "value"
    assert state.peek_cached("missing") is None
    assert state.stats()["cache_hits"] == 0
    assert state.stats()["cache_misses"] == 0
