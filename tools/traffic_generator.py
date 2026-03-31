from __future__ import annotations

import argparse
import os
import socket
from pathlib import Path


def generate_payload(mode: str, size_bytes: int) -> bytes:
    """Membangun payload benchmark bergaya UDP atau TCP pada ukuran tertentu."""

    header = f"{mode.upper()}:{size_bytes}:".encode("ascii")
    body = os.urandom(max(0, size_bytes - len(header)))
    return (header + body)[:size_bytes]


def generate_socket_payload(mode: str, size_bytes: int) -> bytes:
    """Melewatkan payload lewat socket lokal agar pola traffic lebih realistis."""

    if mode == "tcp":
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.connect(("127.0.0.1", port))
        conn, _ = server.accept()
        payload = generate_payload(mode, size_bytes)
        client.sendall(payload)
        received = b""
        while len(received) < size_bytes:
            received += conn.recv(size_bytes - len(received))
        conn.close()
        client.close()
        server.close()
        return received
    udp_server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_server.bind(("127.0.0.1", 0))
    port = udp_server.getsockname()[1]
    udp_client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    payload = generate_payload(mode, size_bytes)
    chunk_size = 8_192
    for index in range(0, len(payload), chunk_size):
        udp_client.sendto(payload[index : index + chunk_size], ("127.0.0.1", port))
    received = b""
    while len(received) < size_bytes:
        chunk, _ = udp_server.recvfrom(65_536)
        received += chunk
    udp_client.close()
    udp_server.close()
    return received[:size_bytes]


def write_payload_file(path: str | Path, mode: str, size_bytes: int) -> Path:
    """Menulis payload benchmark ke file agar bisa dikirim melalui adapter RF."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = generate_socket_payload(mode, size_bytes)
    path.write_bytes(payload)
    return path


def main() -> int:
    """CLI pembangkit payload UDP/TCP untuk benchmark throughput RF."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["udp", "tcp"], required=True)
    parser.add_argument("--size-bytes", type=int, required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    write_payload_file(args.output, args.mode, args.size_bytes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
