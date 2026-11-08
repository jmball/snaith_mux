"""Small synchronous controller protocol; instances are not thread safe."""

import logging
import math
import re
import socket
import time
from types import MappingProxyType, TracebackType

_LOGGER = logging.getLogger(__name__)
PROMPT = b">>> "
PIXEL_MASKS = MappingProxyType({0: 0, **{p: (1 << (p + 7)) | 3 for p in range(1, 9)}})


class ProtocolError(RuntimeError):
    """The controller response is incomplete or violates the protocol."""


class FirmwareError(ProtocolError):
    """The controller reported ERROR or rejected an action."""


class SnaithMux:
    """Own a TCP connection without performing I/O during construction.

    Parameters
    ----------
    timeout : float, optional
        Positive finite connection and per-transaction deadline in seconds.
    max_response_bytes : int, optional
        Maximum wire bytes per response, including Telnet traffic and prompt.
    """

    def __init__(self, *, timeout: float = 5.0, max_response_bytes: int = 65536):
        if isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive and finite")
        if type(max_response_bytes) is not int or max_response_bytes < len(PROMPT):
            raise ValueError("max_response_bytes must be an integer >= 4")
        self._timeout = timeout
        self._limit = max_response_bytes
        self._socket: socket.socket | None = None
        self._welcome: str | None = None
        self._selection: tuple[str | None, int] | None = None

    @property
    def connected(self) -> bool:
        """Whether a local session is open; this is not a peer liveness probe."""
        return self._socket is not None

    @property
    def welcome_message(self) -> str | None:
        """Welcome text for the active session, or None while disconnected."""
        return self._welcome

    @property
    def commanded_selection(self) -> tuple[str | None, int] | None:
        """Last acknowledged selection command, not relay readback.

        Returns
        -------
        tuple[str or None, int] or None
            Slot and mask; (None, 0) means all-off was acknowledged. None means
            unknown. No claim is made about other slots or physical completion.
        """
        return self._selection

    def __enter__(self) -> "SnaithMux":
        """Return this instance without connecting.

        Returns
        -------
        SnaithMux
            This instance.
        """
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close on exit, preserving any exception from the context body.

        Parameters
        ----------
        exc_type : type[BaseException] or None
            Exception type from the context body.
        exc : BaseException or None
            Exception raised by the context body.
        traceback : TracebackType or None
            Traceback from the context body.
        """
        try:
            self.disconnect()
        except Exception:
            if exc_type is None:
                raise
            _LOGGER.warning("Connection cleanup failed", exc_info=True)

    def connect(self, resource_name: str, *, port: int = 23) -> None:
        """Open TCP and require a complete welcome prompt; never reset.

        Parameters
        ----------
        resource_name : str
            Hostname or IP address, without a port or VISA syntax.
        port : int, optional
            TCP port, default 23.
        """
        if self.connected:
            raise RuntimeError("Already connected; disconnect first")
        if (
            not isinstance(resource_name, str)
            or not resource_name
            or any(c.isspace() or ord(c) < 32 for c in resource_name)
        ):
            raise ValueError("resource_name must be a nonempty hostname or IP address")
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("port must be an integer in 1..65535")
        self._socket = socket.create_connection((resource_name, port), self._timeout)
        try:
            self._welcome = self._read_response(time.monotonic() + self._timeout)
        except BaseException:
            self._close_after_failure()
            raise

    def disconnect(self) -> None:
        """Close TCP only, idempotently; do not deselect, reset, or send exit.

        Relay behavior on peer disconnection is firmware dependent. Explicitly
        call deselect_all() first when required. Forget commanded state.
        """
        sock, self._socket = self._socket, None
        self._selection = None
        self._welcome = None
        if sock is not None:
            sock.close()

    def _close_after_failure(self) -> None:
        try:
            self.disconnect()
        except Exception:
            _LOGGER.warning("Failed to close after protocol failure", exc_info=True)

    def _require_socket(self) -> socket.socket:
        if self._socket is None:
            raise RuntimeError("Not connected")
        return self._socket

    def _read_response(self, deadline: float) -> str:
        sock = self._require_socket()
        data = bytearray()
        wire_count = 0
        state = "data"
        option_command = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Timed out waiting for complete >>> prompt")
            sock.settimeout(remaining)
            try:
                chunk = sock.recv(1)
            except TimeoutError as error:
                raise TimeoutError(
                    "Timed out waiting for complete >>> prompt"
                ) from error
            if not chunk:
                raise ProtocolError("EOF before complete >>> prompt")
            wire_count += 1
            if wire_count > self._limit:
                raise ProtocolError("Response exceeds max_response_bytes")
            byte = chunk[0]
            # Minimal Telnet: refuse all options, discard bounded subnegotiation.
            if state == "option":
                if option_command in (251, 253):  # WILL -> DONT; DO -> WONT
                    sock.sendall(
                        bytes((255, 254 if option_command == 251 else 252, byte))
                    )
                state = "data"
                continue
            if state == "sub":
                if byte == 255:
                    state = "sub_iac"
                continue
            if state == "sub_iac":
                state = "data" if byte == 240 else "sub"
                continue
            if state == "iac":
                if byte in (251, 252, 253, 254):
                    option_command, state = byte, "option"
                    continue
                if byte == 250:
                    state = "sub"
                    continue
                state = "data"
                if byte != 255:
                    continue
            elif byte == 255:
                state = "iac"
                continue
            data.append(byte)
            if data.endswith(PROMPT):
                try:
                    response = data[: -len(PROMPT)].decode("ascii").strip()
                except UnicodeDecodeError as error:
                    raise ProtocolError("Non-ASCII controller response") from error
                if re.search(r"\bERROR\b", response, re.IGNORECASE):
                    raise FirmwareError(response)
                return response

    def query(self, command: str) -> str:
        """Send a validated protocol command and require its full response.

        Parameters
        ----------
        command : str
            v, c, s, or s followed by A..Z and an established Snaith pixel mask.
            No terminator. Arbitrary relay masks are deliberately unsupported.

        Returns
        -------
        str
            ASCII response without prompt or surrounding whitespace. Selection
            requires an empty response. Failures invalidate state and close TCP.
        """
        if not isinstance(command, str) or not re.fullmatch(
            r"[vc]|s(?:[A-Z][0-9]{1,5})?", command
        ):
            raise ValueError("Unsupported command or invalid command framing")
        selection = None
        if command.startswith("s"):
            if command == "s":
                selection = (None, 0)
            else:
                mask = int(command[2:])
                if mask not in PIXEL_MASKS.values():
                    raise ValueError(
                        "Only established Snaith pixel masks are supported"
                    )
                selection = (command[1], mask)
        sock = self._require_socket()
        if command == "c":
            self._selection = None
        try:
            deadline = time.monotonic() + self._timeout
            sock.settimeout(self._timeout)
            sock.sendall(command.encode("ascii") + b"\r\n")
            response = self._read_response(deadline)
            if selection is not None:
                if response:
                    raise FirmwareError(
                        f"Expected empty selection acknowledgement: {response!r}"
                    )
                self._selection = selection
            return response
        except BaseException:
            self._close_after_failure()
            raise

    @property
    def firmware_version(self) -> str:
        """Query firmware text using v (requires a connection)."""
        return self.query("v")

    @property
    def detected_muxes(self) -> tuple[str, ...]:
        """Query c and decode its presence bitfield, bit zero meaning slot A.

        This may probe/reinitialize mux hardware in firmware; it is not relay
        readback. Commanded selection is therefore forgotten before querying.
        """
        self._selection = None
        response = self.query("c")
        if not re.fullmatch(r"[0-9]+", response) or int(response) >= 1 << 26:
            self._close_after_failure()
            raise ProtocolError(f"Invalid mux presence bitfield: {response!r}")
        mask = int(response)
        return tuple(chr(65 + bit) for bit in range(26) if mask & (1 << bit))

    def select_pixel(self, slot: str, pixel: int) -> None:
        """Command a Snaith pixel; zero deselects the addressed slot.

        Parameters
        ----------
        slot : str
            Single uppercase ASCII letter A..Z; installed range is not verified.
        pixel : int
            Reference numbering 1..8, or 0 for off. Booleans are rejected.
        """
        if not isinstance(slot, str) or not re.fullmatch("[A-Z]", slot):
            raise ValueError("slot must be one uppercase ASCII letter A..Z")
        if type(pixel) is not int or pixel not in PIXEL_MASKS:
            raise ValueError("pixel must be an integer in 0..8")
        mask = PIXEL_MASKS[pixel]
        self.query(f"s{slot}{mask:05d}" if mask else f"s{slot}0")

    def deselect_slot(self, slot: str) -> None:
        """Command zero mask for one slot.

        Parameters
        ----------
        slot : str
            Single uppercase ASCII letter A..Z.
        """
        self.select_pixel(slot, 0)

    def deselect_all(self) -> None:
        """Send s and require an empty response plus the full prompt."""
        self.query("s")

    def reset(self) -> None:
        """Send explicit controller reset and close TCP, without awaiting a prompt.

        Successful return means bytes were sent, not that reset completed.
        Reconnect explicitly. Relay state after reset is unknown.
        """
        sock = self._require_socket()
        try:
            sock.settimeout(self._timeout)
            sock.sendall(b"reset\r\n")
        finally:
            self.disconnect()
