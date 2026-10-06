"""asyncio_fixes: a connection reset must not keep a Windows asyncio server's
wait_closed() — and with it the gateway's clean stop — waiting forever."""

import asyncio
import socket
import struct
import sys
import time
import unittest
from unittest import mock

from backend.shared.utils.asyncio_fixes import finish_connection_lost, fix_windows_connection_reset

try:
    from asyncio import proactor_events
except ImportError:  # pragma: no cover - present on every platform today
    proactor_events = None

WAIT_S = 1.0


class FakeSocket:
    def __init__(self, reset: bool) -> None:
        self.reset, self.closed = reset, False

    def fileno(self) -> int:
        return -1 if self.closed else 7

    def shutdown(self, how: int) -> None:
        if self.reset:
            raise ConnectionResetError(10054, "An existing connection was forcibly closed by the remote host")

    def close(self) -> None:
        self.closed = True


class FakeServer:
    def __init__(self) -> None:
        self.detached: list = []

    def _detach(self, transport) -> None:
        self.detached.append(transport)


class FakeTransport:
    """The attributes asyncio's _call_connection_lost uses, nothing else."""

    def __init__(self, reset: bool = False, protocol_error: BaseException | None = None) -> None:
        self._called_connection_lost = False
        self._sock = FakeSocket(reset)
        self._server = FakeServer()
        self._protocol = mock.Mock()
        if protocol_error is not None:
            self._protocol.connection_lost.side_effect = protocol_error


@unittest.skipIf(proactor_events is None, "asyncio without proactor_events")
class FinishConnectionLostTests(unittest.TestCase):
    """The wrapper around asyncio's own method, on a fake transport (runs on every OS)."""

    def setUp(self) -> None:
        method = proactor_events._ProactorBasePipeTransport._call_connection_lost
        self.original = getattr(method, "__wrapped__", method)  # asyncio's own, even if the fix is installed
        self.fixed = finish_connection_lost(self.original)

    def test_asyncio_alone_forgets_the_server_when_shutdown_fails(self):
        transport = FakeTransport(reset=True)
        try:
            self.original(transport, None)
        except ConnectionResetError:
            pass
        else:
            self.skipTest("this Python handles the failed shutdown itself; the fix is no longer needed")
        self.assertEqual(transport._server.detached, [])  # the server keeps counting it
        self.assertFalse(transport._sock.closed)

    def test_failed_shutdown_still_closes_the_socket_and_detaches(self):
        transport = FakeTransport(reset=True)
        sock, server = transport._sock, transport._server
        self.fixed(transport, None)  # no exception
        transport._protocol.connection_lost.assert_called_once_with(None)
        self.assertTrue(sock.closed)
        self.assertEqual(server.detached, [transport])
        self.assertIsNone(transport._sock)
        self.assertIsNone(transport._server)
        self.assertTrue(transport._called_connection_lost)

    def test_normal_close_is_unchanged(self):
        transport = FakeTransport()
        sock, server = transport._sock, transport._server
        self.fixed(transport, None)
        self.assertTrue(sock.closed)
        self.assertEqual(server.detached, [transport])

    def test_an_error_from_the_protocol_itself_is_passed_on(self):
        transport = FakeTransport(protocol_error=OSError("protocol bug"))
        server = transport._server
        with self.assertRaisesRegex(OSError, "protocol bug"):
            self.fixed(transport, None)
        self.assertEqual(server.detached, [transport])  # asyncio's own clean-up ran


class InstallTests(unittest.TestCase):
    def test_not_installed_off_windows(self):
        with mock.patch.object(sys, "platform", "linux"):
            self.assertFalse(fix_windows_connection_reset())

    @unittest.skipUnless(sys.platform == "win32", "Windows only")
    def test_installed_once(self):
        self.assertTrue(fix_windows_connection_reset())
        first = proactor_events._ProactorBasePipeTransport._call_connection_lost
        self.assertTrue(fix_windows_connection_reset())
        second = proactor_events._ProactorBasePipeTransport._call_connection_lost
        self.assertIs(first, second)  # not wrapped twice
        self.assertFalse(hasattr(first.__wrapped__, "__wrapped__"))


async def reset_after_half_close() -> tuple[bool, list[BaseException | None]]:
    """A real server and client: the client half-closes (the server stops reading but keeps
    the connection), then resets it; the server closes its connections and its listener.

    Returns:
        (wait_closed() returned within WAIT_S, exceptions that reached the loop's handler).
    """
    loop = asyncio.get_running_loop()
    errors: list[BaseException | None] = []
    loop.set_exception_handler(lambda _loop, context: errors.append(context.get("exception")))
    transports: list[asyncio.Transport] = []

    class KeepsOpenAfterEof(asyncio.Protocol):
        def connection_made(self, transport):
            transports.append(transport)

        def data_received(self, data):
            transports[-1].write(b"ok")

        def eof_received(self):
            return True  # keep the connection after the client's half-close

    server = await loop.create_server(KeepsOpenAfterEof, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    def client() -> None:
        sock = socket.create_connection(("127.0.0.1", port))
        sock.sendall(b"ping")
        sock.shutdown(socket.SHUT_WR)  # half-close
        time.sleep(0.2)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        sock.close()  # with linger 0: a reset

    await asyncio.to_thread(client)
    await asyncio.sleep(0.3)
    for transport in transports:
        transport.close()  # what uvicorn does with its connections on shutdown
    server.close()
    try:
        await asyncio.wait_for(server.wait_closed(), WAIT_S)
        returned = True
    except TimeoutError:
        returned = False
    for transport in transports:  # don't leak a socket the unfixed run left open
        sock = getattr(transport, "_sock", None)
        if sock is not None:
            sock.close()
    return returned, errors


@unittest.skipUnless(sys.platform == "win32", "the bug is in asyncio's Windows event loop")
class RealConnectionResetTests(unittest.TestCase):
    def setUp(self) -> None:
        fix_windows_connection_reset()
        self.fixed = proactor_events._ProactorBasePipeTransport._call_connection_lost

    def test_the_reset_hangs_wait_closed_without_the_fix(self):
        """Proves the scenario below really hits the bug (else the fixed test proves nothing)."""
        with mock.patch.object(proactor_events._ProactorBasePipeTransport, "_call_connection_lost",
                               self.fixed.__wrapped__):
            returned, errors = asyncio.run(reset_after_half_close())
        if returned:
            self.skipTest("this Python no longer hangs here; the fix is no longer needed")
        self.assertTrue(any(isinstance(e, ConnectionResetError) for e in errors))

    def test_with_the_fix_wait_closed_returns_and_nothing_is_logged(self):
        returned, errors = asyncio.run(reset_after_half_close())
        self.assertTrue(returned, f"wait_closed() still waiting after {WAIT_S} s")
        self.assertEqual(errors, [])  # no "Exception in callback ... WinError 10054" any more


if __name__ == "__main__":
    unittest.main()
