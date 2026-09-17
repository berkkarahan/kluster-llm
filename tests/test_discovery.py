from __future__ import annotations

from kllm.discovery import DiscoveredMaster, join_key_id


def test_join_key_id_stable_and_truncated():
    a = join_key_id("secret-key")
    b = join_key_id("secret-key")
    assert a == b
    assert len(a) == 16
    assert a != join_key_id("other-key")
    assert all(c in "0123456789abcdef" for c in a)


def test_browse_masters_key_filter_unit():
    """Filter predicate used by browse_masters when key is provided."""
    kid = join_key_id("abc")
    found = [
        DiscoveredMaster(name="m1", host="1.1.1.1", port=18765, key_id=kid),
        DiscoveredMaster(name="m2", host="2.2.2.2", port=18765, key_id="deadbeefdeadbeef"),
        DiscoveredMaster(name="m3", host="3.3.3.3", port=18765, key_id=None),
    ]
    want = join_key_id("abc")
    filtered = [m for m in found if m.key_id == want]
    assert filtered == [found[0]]
