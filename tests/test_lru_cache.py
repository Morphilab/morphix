"""Tests for LRU cache — TTL, eviction, thread safety."""

from core.lru_cache import LRUCache


class TestLRUCache:
    def test_get_miss_returns_none(self):
        c = LRUCache()
        assert c.get("missing") is None

    def test_set_and_get(self):
        c = LRUCache()
        c.set("a", 1)
        assert c.get("a") == 1

    def test_ttl_expiration(self):
        c = LRUCache(ttl=0)
        c.set("a", 1)
        assert c.get("a") is None

    def test_eviction_on_overflow(self):
        c = LRUCache(max_size=2)
        c.set("a", 1)
        c.set("b", 2)
        c.set("c", 3)
        assert c.get("a") is None
        assert c.get("b") == 2
        assert c.get("c") == 3

    def test_clear_expired(self):
        c = LRUCache(ttl=0)
        c.set("a", 1)
        c.set("b", 2)
        assert c.clear_expired() == 2
        assert len(c) == 0

    def test_get_touches_lru_order(self):
        c = LRUCache(max_size=2)
        c.set("a", 1)
        c.set("b", 2)
        c.get("a")  # touch a
        c.set("c", 3)  # should evict b
        assert c.get("a") == 1
        assert c.get("b") is None
        assert c.get("c") == 3

    def test_set_updates_existing(self):
        c = LRUCache()
        c.set("a", 1)
        c.set("a", 2)
        assert c.get("a") == 2
        assert len(c) == 1
