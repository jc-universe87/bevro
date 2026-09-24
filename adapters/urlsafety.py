"""What Bevro will and will not fetch, wherever it is fetching from.

The host worker sits on networks the API container does not - that is the
whole point of it - which makes this policy more important rather than less.
The same rules apply in both processes because both use this module: there is
no "worker mode" that relaxes anything.

    only http and https        no file://, no gopher://, no data:
    bounded redirects          a chain cannot be walked indefinitely
    same origin for work       an operation is called on the service's own
                               address, never one that came back in a payload
"""

from __future__ import annotations

from urllib.parse import urlsplit

import httpx

ALLOWED_SCHEMES = ("http", "https")
MAX_REDIRECTS = 3
DEFAULT_TIMEOUT = 20.0
# A host that is there accepts a connection at once; one that is not there
# from this process never will. Reading what it then sends can take as long
# as it likes - a large description is slow to produce, not slow to reach.
CONNECT_TIMEOUT = 5.0


class UnsafeUrl(ValueError):
    """An address Bevro will not fetch, whichever process asked."""


def check(url: str) -> str:
    """The address, if Bevro may fetch it. Raises if not."""
    parts = urlsplit(url)
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise UnsafeUrl("Bevro only connects over http or https.")
    if not parts.netloc:
        raise UnsafeUrl("That address has no host.")
    return url


def same_origin(base: str, url: str) -> bool:
    """Is this the service Bevro was connected to, and not somewhere else?"""
    a, b = urlsplit(base), urlsplit(url)
    return (a.scheme, a.hostname, a.port) == (b.scheme, b.hostname, b.port)


def client(*, timeout: float = DEFAULT_TIMEOUT, transport: httpx.BaseTransport | None = None, headers: dict[str, str] | None = None) -> httpx.Client:
    """An HTTP client with Bevro's policy already on it."""
    return httpx.Client(
        timeout=httpx.Timeout(timeout, connect=min(CONNECT_TIMEOUT, timeout)),
        transport=transport,
        follow_redirects=True,
        max_redirects=MAX_REDIRECTS,
        headers=headers or {},
    )
