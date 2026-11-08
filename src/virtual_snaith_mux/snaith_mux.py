"""Virtual Snaith multiplexor with immediate command acknowledgements."""

from snaith_mux.snaith_mux import (
    DEFAULT_PORT,
    DEFAULT_TIMEOUT,
    FirmwareError,
    _selection_for_command,
    _validate_connection,
)
from snaith_mux.snaith_mux import (
    SnaithMux as HardwareSnaithMux,
)


class SnaithMux(HardwareSnaithMux):
    """Simulate the Snaith mux API without opening network connections.

    Selection and deselection immediately update commanded_selection. No
    electrical behavior, cross-slot relay state, switching delays, or network
    faults are simulated. Selecting an absent slot raises FirmwareError and
    disconnects, matching the hardware driver's error cleanup policy.

    Construction and context entry do not connect. Connection arguments and
    timeout assignments use the hardware driver's validation. Timeout and
    response-size settings are accepted for API compatibility, but operations
    do not wait or assemble wire responses. Disconnect and reset forget the
    commanded selection; configured installed slots persist across reconnects.

    Parameters
    ----------
    max_response_bytes : int, optional
        Hardware-compatible response limit, default 2048; validated but unused.
    slots : tuple[str, ...], optional
        Distinct installed slot letters A..Z, default ("A",). An empty tuple
        represents a controller with no muxes. Does not restrict deselect_all().
    """

    def __init__(
        self, *, max_response_bytes: int = 2048, slots: tuple[str, ...] = ("A",)
    ) -> None:
        super().__init__(max_response_bytes=max_response_bytes)
        if not isinstance(slots, tuple) or any(
            not isinstance(slot, str) or len(slot) != 1 or not "A" <= slot <= "Z"
            for slot in slots
        ):
            raise ValueError("slots must be a tuple of uppercase ASCII letters A..Z")
        if len(set(slots)) != len(slots):
            raise ValueError("slots must be distinct")
        self._slots = tuple(sorted(slots))
        self._connected = False

    @property
    def connected(self) -> bool:
        """Whether the in-memory session is open."""
        return self._connected

    def connect(
        self,
        resource_name: str,
        *,
        port: int = DEFAULT_PORT,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        """Open an in-memory session without DNS or socket access.

        Parameters
        ----------
        resource_name : str
            Host identifier validated as in the hardware driver, but not resolved.
        port : int, optional
            Valid TCP port, default 23; otherwise unused.
        timeout : float, optional
            Positive finite timeout in seconds, default 5; stored without waits.
        """
        if self.connected:
            raise RuntimeError("Already connected; disconnect first")
        _validate_connection(resource_name, port, timeout)
        self._timeout = timeout
        self._welcome_message = "Virtual Snaith mux controller"
        self._connected = True

    def disconnect(self) -> None:
        """Close the in-memory session idempotently and forget commanded state."""
        self._connected = False
        self._welcome_message = None
        self._commanded_selection = None

    def query(self, command: str) -> str:
        """Validate and immediately acknowledge a supported controller command.

        Parameters
        ----------
        command : str
            Same commands and known pixel masks accepted by the hardware driver.

        Returns
        -------
        str
            Virtual firmware text for v, configured slot bitfield for c, or an
            empty selection acknowledgement. Presence queries forget selection.
        """
        selection = _selection_for_command(command)
        self._require_connected()
        if command == "v":
            return "virtual-snaith-mux-0.1.0"
        if command == "c":
            self._commanded_selection = None
            return str(sum(1 << (ord(slot) - ord("A")) for slot in self._slots))
        if selection is not None:
            slot, _ = selection
            if slot is not None and slot not in self._slots:
                self.disconnect()
                raise FirmwareError(f"ERROR: virtual slot {slot} is not installed")
            self._commanded_selection = selection
        return ""

    def reset(self) -> None:
        """Simulate explicit controller reset by disconnecting and forgetting state."""
        self._require_connected()
        self.disconnect()
