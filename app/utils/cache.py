"""Tiny helpers for versioned cache keys.

A version key per cache family makes invalidation cheap and correct:
writes bump the version, so every key in the family changes at once and
stale entries are simply never read again (they expire via the TTL).
"""

from django.core.cache import cache


def cache_version(version_key):
    """Current version of a cache family; 0 when it was never bumped."""
    try:
        return int(cache.get(version_key, 0))
    except (TypeError, ValueError):
        return 0


def bump_cache_version(version_key):
    """Advance a cache family's version, retiring all its keys at once."""
    try:
        return cache.incr(version_key)
    except ValueError:
        cache.add(version_key, 1)
        return 1


def versioned_key(prefix, version_key, *parts):
    """Build a cache key that changes whenever its family version bumps."""
    suffix = ":".join(str(part) for part in parts)
    return f"{prefix}:v{cache_version(version_key)}:{suffix}"
