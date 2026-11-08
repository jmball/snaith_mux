"""Hardware-free usage: uv run python examples/fake_controller.py."""

import socketserver
import threading

from snaith_mux import SnaithMux


class Controller(socketserver.StreamRequestHandler):
    """Minimal illustrative protocol peer, not a firmware emulator."""

    def handle(self) -> None:
        self.wfile.write(b"Fake Snaith controller\r\n>>> ")
        while line := self.rfile.readline():
            command = line.removesuffix(b"\r\n")
            print("Command:", command.decode())
            if command == b"v":
                response = b"fake-1.0"
            elif command == b"c":
                response = b"1"
            elif command.startswith(b"s"):
                response = b""
            else:
                response = b"ERROR: unsupported"
            self.wfile.write(response + b"\r\n>>> ")


def main() -> None:
    """Run a loopback peer and exercise explicit driver lifecycle."""
    with socketserver.TCPServer(("127.0.0.1", 0), Controller) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with SnaithMux() as mux:
                mux.connect("127.0.0.1", port=server.server_address[1])
                print(mux.firmware_version)
                mux.select_pixel("A", 2)
                print("Last acknowledged command:", mux.commanded_selection)
                mux.deselect_slot("A")
                mux.deselect_all()
            assert not mux.connected
        finally:
            server.shutdown()
            thread.join()


if __name__ == "__main__":
    main()
