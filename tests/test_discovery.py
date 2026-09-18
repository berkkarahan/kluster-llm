from __future__ import annotations

from kllm.discovery import DiscoveredMaster, browse_masters, join_key_id


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


def test_browse_masters_falls_back_to_helper(monkeypatch):
    """When in-process mDNS is empty (LAN-blocked), use helper browse results."""
    kid = join_key_id("secret")
    monkeypatch.setattr(
        "kllm.discovery._browse_inprocess",
        lambda timeout=2.0, key=None: [],
    )
    monkeypatch.setattr(
        "kllm.discovery._browse_via_helper",
        lambda timeout, key: [
            DiscoveredMaster(name="m", host="192.168.68.63", port=18765, key_id=kid),
        ],
    )
    found = browse_masters(timeout=0.1, key="secret")
    assert len(found) == 1
    assert found[0].host == "192.168.68.63"


def test_browse_masters_skips_helper_when_inprocess_finds(monkeypatch):
    calls = {"helper": 0}
    monkeypatch.setattr(
        "kllm.discovery._browse_inprocess",
        lambda timeout=2.0, key=None: [
            DiscoveredMaster(name="m", host="10.0.0.1", port=18765, key_id="abcd"),
        ],
    )

    def boom(_timeout, _key):
        calls["helper"] += 1
        raise AssertionError("helper should not run")

    monkeypatch.setattr("kllm.discovery._browse_via_helper", boom)
    found = browse_masters(timeout=0.1, key=None)
    assert found[0].host == "10.0.0.1"
    assert calls["helper"] == 0
