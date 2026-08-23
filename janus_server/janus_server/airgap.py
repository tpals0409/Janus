"""Runtime and artifact gates for closed-network orchestration."""

from __future__ import annotations

from contextlib import contextmanager
import ipaddress
import socket
from typing import Any, Iterator
from urllib.parse import urlparse


class AirgapViolation(RuntimeError):
    pass


def _local_host(host: Any) -> bool:
    if isinstance(host, bytes):
        host = host.decode(errors="replace")
    text = str(host).strip().strip("[]").lower()
    if text == "localhost":
        return True
    try:
        return ipaddress.ip_address(text).is_loopback
    except ValueError:
        return False


@contextmanager
def local_network_only() -> Iterator[list[dict[str, Any]]]:
    """Deny DNS and TCP/UDP destinations other than loopback for this process."""
    events: list[dict[str, Any]] = []
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_sendto = socket.socket.sendto
    original_sendmsg = getattr(socket.socket, "sendmsg", None)
    original_getaddrinfo = socket.getaddrinfo

    def inspect(address: Any) -> None:
        if isinstance(address, str):
            events.append({"kind": "unix_socket", "target": address})
            return
        host = address[0] if isinstance(address, tuple) and address else address
        if not _local_host(host):
            events.append({"kind": "blocked", "target": str(host)})
            raise AirgapViolation(f"external network destination blocked: {host!r}")
        events.append({"kind": "loopback", "target": str(host)})

    def guarded_connect(sock, address):
        inspect(address)
        return original_connect(sock, address)

    def guarded_connect_ex(sock, address):
        inspect(address)
        return original_connect_ex(sock, address)

    def guarded_getaddrinfo(host, *args, **kwargs):
        if host is not None and not _local_host(host):
            events.append({"kind": "blocked_dns", "target": str(host)})
            raise AirgapViolation(f"external DNS lookup blocked: {host!r}")
        return original_getaddrinfo(host, *args, **kwargs)

    def guarded_sendto(sock, data, *args):
        if not args:
            raise TypeError("sendto requires an address")
        inspect(args[-1])
        return original_sendto(sock, data, *args)

    def guarded_sendmsg(sock, buffers, ancdata=(), flags=0, address=None):
        if address is not None:
            inspect(address)
            return original_sendmsg(sock, buffers, ancdata, flags, address)
        return original_sendmsg(sock, buffers, ancdata, flags)

    socket.socket.connect = guarded_connect
    socket.socket.connect_ex = guarded_connect_ex
    socket.socket.sendto = guarded_sendto
    if original_sendmsg is not None:
        socket.socket.sendmsg = guarded_sendmsg
    socket.getaddrinfo = guarded_getaddrinfo
    try:
        yield events
    finally:
        socket.socket.connect = original_connect
        socket.socket.connect_ex = original_connect_ex
        socket.socket.sendto = original_sendto
        if original_sendmsg is not None:
            socket.socket.sendmsg = original_sendmsg
        socket.getaddrinfo = original_getaddrinfo


def assert_local_artifacts(value: Any) -> None:
    """Reject remote URIs embedded anywhere in durable orchestration state."""
    if isinstance(value, dict):
        for key, item in value.items():
            assert_local_artifacts(key)
            assert_local_artifacts(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            assert_local_artifacts(item)
    elif isinstance(value, str):
        parsed = urlparse(value)
        if parsed.scheme in {"http", "https", "ftp", "s3", "gs"}:
            raise AirgapViolation(f"remote artifact URI blocked: {value!r}")
