# Snaith mux

Control a Snaith PCB multiplexor: select a pixel in a mux slot, deselect relays,
and query the controller. Includes a virtual driver for use without hardware.

## Installation

From the repository directory:

```sh
uv sync
```

This installs both drivers into the project environment using `uv.lock`.
No additional runtime dependencies are needed.

## Connect and select a pixel

This example operates real hardware. Replace the host with your controller's
hostname or IP address. It explicitly resets the controller after deselection.

```python
from snaith_mux import SnaithMux

with SnaithMux() as mux:
    mux.connect("YOUR-CONTROLLER-HOST", port=23, timeout=5.0)
    print(mux.firmware_version)

    try:
        mux.select_pixel("A", 2)
        print(mux.commanded_selection)  # ('A', 515)
        # Use the selected measurement path here.
        mux.deselect_slot("A")
    finally:
        if mux.connected:
            mux.deselect_all()
    mux.reset()  # Explicit controller reset; also closes the connection.
# The connection is closed on leaving the context.
```

Construction and context entry do not connect. Call `connect()` explicitly.
The driver owns the connection; context exit calls `disconnect()`, which is
also safe to call repeatedly. Disconnect before connecting again.

**Closing the connection does not send deselection or reset commands.** Call
`deselect_slot()` or `deselect_all()` explicitly when needed. If communication
fails, the connection is closed and relay state is unknown; the driver cannot
confirm deselection through that failed connection.

A runnable example is also provided:

```sh
uv run python examples/select_pixel.py YOUR-CONTROLLER-HOST --slot A --pixel 2
```

It selects the pixel, deselects the slot and all muxes, then explicitly resets
the controller and disconnects. Reset is performed only after those operations
succeed. Use `--help` to see its connection options without contacting hardware.

## Selection and controller information

| Operation | Behavior |
| --- | --- |
| `select_pixel(slot, pixel)` | Select pixel 1–8 in the addressed slot; pixel 0 deselects that slot. |
| `deselect_slot(slot)` | Send a zero relay mask to the addressed slot. |
| `deselect_all()` | Command all muxes off. |
| `commanded_selection` | Last acknowledged selection as `(slot, relay_mask)`, `(None, 0)` for all-off, or `None` for unknown. |
| `firmware_version` | Query the firmware version text. |
| `detected_muxes` | Query installed slots, returned as a tuple of letters. May reinitialize mux electronics and clears commanded selection. |
| `welcome_message` | Welcome text from the active connection, or `None` while disconnected. |
| `connected` | Whether a local session is open; does not probe controller liveness. |
| `reset()` | Send an explicit controller reset and disconnect. Reconnect explicitly afterward. |

Slots must be single uppercase letters A–Z; the installed range depends on the
controller. Logical device names and routing belong in your application.
Pixel mappings are available without connecting:

```python
from snaith_mux import PIXEL_MASKS

print(PIXEL_MASKS[2])  # 515
```

`commanded_selection` records a command acknowledgement, not physical relay
readback or confirmation that switching has settled. It does not describe
other slots. It is cleared by disconnect, reset, presence queries, and
communication failures. Cross-slot effects, physical pixel labels, and settling
times need verification for your hardware.

Reset is never automatic. Successful return from `reset()` means the reset
command was sent, not that reset has completed or the relays are off.

## Timeouts and errors

`connect(host, port=23, timeout=5.0)` configures the communications timeout in
seconds. While connected, use `mux.timeout` to read or change it. Values must be
positive and finite; booleans are rejected. Each send gets the full timeout,
then the complete response gets a separate allowance of the same length.
Receiving individual bytes does not restart the response timeout.

`SnaithMux(max_response_bytes=2048)` sets the response-size limit, including the
prompt. The minimum is four bytes.

Hardware operations require a connection. Invalid arguments raise `ValueError`
before transmission. Controller errors raise `FirmwareError`; incomplete or
invalid responses raise `ProtocolError`. Both are available from `snaith_mux`
and derive from `RuntimeError`. Socket errors and `TimeoutError` propagate.
Command communication failures close the connection and clear commanded state.
Commands are never retried automatically because a command may have taken
effect even when its acknowledgement was lost.

The driver uses plain ASCII TCP, CRLF command termination, and the complete
`>>> ` response prompt. Telnet negotiation is not supported. Selection commands
require an empty response before the prompt. For low-level access,
`query(command)` accepts `v`, `c`, `s`, and slot-selection commands using the
supported pixel masks; arbitrary relay masks are not supported. Instances are
synchronous and are not thread safe.

## Virtual driver

Use the same API without contacting hardware:

```python
from virtual_snaith_mux import SnaithMux

with SnaithMux(slots=("A", "B")) as mux:
    mux.connect("virtual", timeout=5.0)
    mux.select_pixel("B", 2)
    print(mux.commanded_selection)  # ('B', 515)
    mux.deselect_all()
```

The virtual driver reports configured slots (default A) and virtual firmware
text. Operations complete immediately. Selecting an absent slot raises
`FirmwareError` and disconnects. Installed slots persist across reconnects;
commanded selection follows the real driver's lifecycle. Timeout and
response-size settings are validated but do not simulate delays or buffering.
The virtual driver does not model electrical behavior or establish hardware
compatibility.

## Development

```sh
uv sync --group dev
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run pytest
uv build
```

Automated tests run without live hardware.
