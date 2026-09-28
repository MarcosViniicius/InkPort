"""Where a feed's books belong: the ``rss/<feed>`` category.

Kept in its own module so both the worker (when importing) and the repairs
(when fixing older imports) derive the exact same name.
"""

from __future__ import annotations

RSS_ROOT = "rss"


def feed_category_name(feed) -> str:
    """Category for a feed's books: ``rss/<feed name>``.

    ``destination_folder``, when set, names the leaf instead of the feed name,
    but the ``rss/`` prefix is always kept so feeds stay grouped together.
    """
    leaf = (getattr(feed, "destination_folder", "") or "").strip()
    if not leaf:
        leaf = (getattr(feed, "name", "") or "").strip()
    leaf = leaf or "feed"
    # Keep a single leaf: no stray separators inside the name.
    leaf = leaf.strip("/")
    return f"{RSS_ROOT}/{leaf}"
