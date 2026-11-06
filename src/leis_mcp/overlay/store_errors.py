"""Error type shared by the overlay without importing the store implementation."""

from ..errors import LeisMcpError


class OverlayError(LeisMcpError):
    pass
