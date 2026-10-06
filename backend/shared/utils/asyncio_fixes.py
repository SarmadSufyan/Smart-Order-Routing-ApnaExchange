"""A fix for a gap in Python's asyncio on Windows that blocked the gateway's clean stop.

On Windows asyncio's event loop (the "proactor" loop) closes a connection in
``_ProactorBasePipeTransport._call_connection_lost``: it tells the protocol, then
``sock.shutdown()``, ``sock.close()`` and finally ``server._detach()``, which tells the
server the connection is gone. If the other side has reset the connection and nothing
was reading any more (for example after a half-close), ``shutdown()`` raises WinError
10054 and the remaining steps are skipped: the socket stays open and the server keeps
counting a connection that no longer exists. Since Python 3.12 ``Server.wait_closed()``
waits for every counted connection, so it never returns. uvicorn awaits it before it runs
the app's shutdown, so Ctrl+C left the gateway in "Shutting down" until ``start_all.py``
killed it after 60 s (found 2026-10-06 with a logged-in browser; the RAFT nodes await
``wait_closed()`` too). Checked against Python 3.14.3.

The fix wraps that one method: if it fails part-way with an ``OSError``, the steps it
skipped are finished (close the socket, detach from the server). Nothing else changes.
It touches a private asyncio method, so it is applied only where that method exists,
only on Windows, and only once; if a later Python stops raising there, the wrapper
never does anything.
"""

import functools
import sys
from collections.abc import Callable
from typing import Any

from backend.shared.utils.logger import get_logger

logger = get_logger("asyncio_fixes")

_MARK = "_apnasor_reset_fix"


def finish_connection_lost(original: Callable[[Any, BaseException | None], None]) -> Callable[..., None]:
    """Wrap ``_call_connection_lost`` so a failing ``shutdown()`` can't leave the connection counted.

    Args:
        original: asyncio's own ``_ProactorBasePipeTransport._call_connection_lost``.

    Returns:
        The wrapped method (asyncio's own stays reachable as ``__wrapped__``). An
        ``OSError`` raised by the protocol itself (asyncio had finished its clean-up)
        is passed on unchanged.
    """

    @functools.wraps(original)
    def _call_connection_lost(transport: Any, exc: BaseException | None) -> None:
        try:
            original(transport, exc)
        except OSError as err:
            if getattr(transport, "_called_connection_lost", True):
                raise  # asyncio finished its clean-up: the error is the protocol's own
            sock = getattr(transport, "_sock", None)
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
                transport._sock = None
            server = getattr(transport, "_server", None)
            if server is not None:
                server._detach(transport)
                transport._server = None
            transport._called_connection_lost = True
            logger.debug("connection_reset_closed", error=str(err))

    setattr(_call_connection_lost, _MARK, True)
    return _call_connection_lost


def fix_windows_connection_reset() -> bool:
    """Install :func:`finish_connection_lost` on asyncio's Windows transport (once).

    Call it before the first connection is accepted: the gateway does it first thing in
    its ``lifespan``, a RAFT node before its event loop starts.

    Returns:
        True if the fix is in place, False where it doesn't apply (not Windows, or an
        asyncio without that method).
    """
    if sys.platform != "win32":
        return False
    from asyncio import proactor_events

    transport_cls = getattr(proactor_events, "_ProactorBasePipeTransport", None)
    original = getattr(transport_cls, "_call_connection_lost", None)
    if original is None:
        return False
    if not getattr(original, _MARK, False):
        transport_cls._call_connection_lost = finish_connection_lost(original)
    return True
