# Snaith mux

A small, synchronous Python 3.13+ driver for the Snaith PCB TCP multiplexor.
Runtime dependencies: Python standard library only. No centralcontrol imports,
experiment routing, or EQE adapter. Instances are not thread safe.

Install with `pip install .`. For development use `uv sync --group dev`.

```python
from snaith_mux import PIXEL_MASKS, SnaithMux

print(dict(PIXEL_MASKS))  # available without connecting
with SnaithMux() as mux:
    # Hardware example: opt in by replacing this host with your controller.
    mux.connect("YOUR-CONTROLLER-HOST", port=23, timeout=5)
    print(mux.welcome_message)
    print(mux.firmware_version)
    mux.select_pixel("A", 2)
    print(mux.commanded_selection)  # ('A', 515), not readback
    mux.deselect_slot("A")
    mux.deselect_all()
# TCP connection closed
```

For a hardware-free example run `uv run python examples/fake_controller.py`.
It starts a local TCP peer and selects, deselects, and disconnects. It does not
simulate physical relays. If the VM blocks loopback TCP, the fake-socket tests
still require no network. Never substitute a real controller in the tests.

Construction validates the response-size limit and does no I/O. Context entry
returns the disconnected instance; connect explicitly. A second connect is an
error. `connected` means a local session exists, not that the peer is alive.
All hardware operations require a connection. A failed welcome closes the
partially opened connection. `disconnect()` is idempotent and closes TCP only:
it sends no `exit`, deselection, or reset. Firmware may react to socket closure;
this driver cannot promise the relays remain unchanged. Explicitly deselect
before disconnecting if required. Context exit closes even after an exception.

`reset()` explicitly sends `reset\r\n` and closes TCP. It does not wait for a
prompt because reset may terminate the connection. Return means send completed,
not reset completed. Reconnect explicitly; relay state is unknown. No operation
resets automatically. Reset, relay deselection, and connection closure are
separate actions.

The transport sends ASCII commands with exactly one CRLF and waits for all four
bytes of `>>> `. It removes that exact suffix, handles fragmented input, and
bounds response bytes and time. Configure the communications timeout in seconds
with `connect(..., timeout=5.0)`. Sending each command gets the full timeout;
receiving the complete response then gets a fresh, separate timeout of the same
length. Each socket read uses the remaining response budget, so fragmented or
slowly trickling responses cannot extend it. Connection establishment and the
complete welcome response also receive separate allowances. Reconnecting uses
the timeout passed to that connect call (or the default 5 seconds). While
connected, read or assign `mux.timeout` to inspect or change the setting in
seconds. Assignments must be positive and finite (booleans are rejected), send
no commands, and apply to subsequent I/O. Getting or setting the property while
disconnected raises RuntimeError. EOF, timeout,
non-ASCII responses, oversize responses, and firmware ERROR responses raise exceptions. Any command
I/O/response failure closes the session and forgets commanded state. No retry
occurs: an acknowledgement can be lost after a command took effect. Value errors
before transmission preserve the session and prior state. FirmwareError and
ProtocolError derive from RuntimeError; socket errors and TimeoutError propagate.
Normal library logging installs no handlers and changes no root settings.

`query(command)` exposes validated `v`, `c`, `s`, and established pixel-mask
selection commands. `firmware_version` queries `v`. `detected_muxes` queries `c`
and decodes a decimal presence bitfield (bit zero -> A). Presence probing may
reinitialize mux electronics according to a comment in mc.py; it forgets
commanded selection and must not be treated as a passive relay-state query.
Calling `query("c")` has the same state invalidation policy.

`commanded_selection` is only the last successfully acknowledged selection
command: `(slot, mask)`, `(None, 0)` for all-off, or None for unknown. It is
cleared on disconnect, reset, presence probing, or communication failure. It is
not a model of simultaneous slot states, exclusive DUT selection, contact
readback, or proof of physical switching completion. Other clients may change
the controller. Logical DUT names and routing belong in the consumer.

## Protocol evidence and limits

Read-only local references inspected during implementation:

- `../centralcontrol/src/centralcontrol/mc.py`: port 23, CRLF writes, welcome
  prompt, `v`, `c`, empty response expected for `s` and selections, slot letters
  decoded from presence bits, explicit `reset`, and Snaith pixel lookup.
- `../centralcontrol/tests/test_pcb.py`: historical hardware test expectations
  include `sA515`, `sA0`, all-off `s`, and 16-bit direct latch writes. These tests
  were inspected, never imported or executed.
- `../spectral_response/subVIs/pcb_open_tcp_connection.vi`,
  `pcb_query_command_tcp.vi`, and `pcb_select_pixel_tcp.vi`: inspected binary
  resources and zlib-decompressed resource text. TCP connection/read/write
  controls, `>>> `, `ERROR:`, and selection failure labels corroborate transport
  and error conventions. Embedded strings do not establish dataflow execution
  order; no reset/select ordering is inferred from them.
- Adjacent Acton SP2150, SRS SR830, Rigol DP800, Thorlabs MC2000B, and Keithley
  2400 drivers: src layout, explicit connect, connected state, context lifecycle,
  and properties for queries. EQE session and Multiplexor role were inspected;
  this driver provides neither their orchestration nor completion guarantees.

The driver assumes plain ASCII TCP and does not interpret or reply to Telnet
control messages. This matches the user's recollection of Arduino firmware
using the Ethernet library, which provides TCP without automatic Telnet
negotiation. Firmware source and a hardware traffic capture were unavailable,
so this remains an explicit transport assumption.

The Python reference uses telnetlib, but its bundled implementation initiates
no negotiation: it only sends refusal replies if the peer sends option commands.
Its use therefore does not establish that the controller requires Telnet.
LabVIEW's inspected resources contain TCP primitives, but their strings alone
do not rule out additional protocol handling in the VI dataflow. If a controller
sends Telnet control bytes, this driver will reject a completed non-ASCII
response, or raise EOF/timeout if no full prompt arrives, and close TCP.

Reference Snaith numbering (not confirmed against physical PCB labels):

| Pixel | Decimal relay mask |
| --- | --- |
| 0 (off) | 0 |
| 1 | 259 |
| 2 | 515 |
| 3 | 1027 |
| 4 | 2051 |
| 5 | 4099 |
| 6 | 8195 |
| 7 | 16387 |
| 8 | 32771 |

Nonzero selections send five decimal digits, e.g. `sA00515`; off sends `sA0`.
A..Z is the syntactic slot range accepted by the driver, inferred from the
reference's letter/bit convention, not a verified installed-slot count. The
reference hardware tests use A..H. No automatic presence query or reset occurs
on connect. The prompt and an empty body are the reference's acknowledgement
convention; neither proves electrical completion. `ERROR` is recognized as a
case-insensitive word anywhere in a response, consistent with LabVIEW's ERROR
search. Unexpected nonempty selection responses fail conservatively.

The firmware implementation and hardware timing documentation were not present
in the inspected sources. Whether slot writes preserve other slots, whether
writes are atomic, exact reset behavior, and physical settling time remain
unresolved. Direct arbitrary relay-mask programming is intentionally omitted
because its electrical semantics cannot be validated from these references.
The known pixel commands remain usable without assuming cross-slot effects.

## Validation

```sh
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run pytest
uv build
```

Tests own their fake sockets and cover framing, one-byte fragmentation,
non-ASCII rejection, welcome cleanup, disconnected errors, limits, EOF, timeouts,
firmware errors, pixel mapping, validation, deselection, reset, and commanded
state. These establish software behavior only, not hardware compatibility.
