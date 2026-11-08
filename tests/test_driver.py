"""Owned socket fakes; no instrument or external testing infrastructure."""

import socket
from collections import deque
from unittest.mock import patch

import pytest

from snaith_mux import PIXEL_MASKS, FirmwareError, ProtocolError, SnaithMux


class FakeSocket:
    def __init__(self, response: bytes = b">>> "):
        self.incoming: deque[int | Exception] = deque(b"Welcome\r\n>>> ")
        self.response = response
        self.sent: list[bytes] = []
        self.closed = False
        self.fail_send = False

    def recv(self, size: int) -> bytes:
        if not self.incoming:
            return b""
        value = self.incoming.popleft()
        if isinstance(value, Exception):
            raise value
        return bytes([value])

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)
        if self.fail_send:
            raise OSError("send failed")
        self.incoming.extend(self.response)

    def settimeout(self, timeout: float) -> None:
        assert timeout > 0

    def close(self) -> None:
        self.closed = True


def connect(fake: FakeSocket) -> SnaithMux:
    driver = SnaithMux()
    with patch("socket.create_connection", return_value=fake):
        driver.connect("fake")
    return driver


def test_lifecycle():
    with patch("socket.create_connection") as opening:
        with SnaithMux() as driver:
            assert not driver.connected
        opening.assert_not_called()
    fake = FakeSocket()
    driver = connect(fake)
    assert driver.welcome_message == "Welcome"
    with pytest.raises(RuntimeError, match="Already connected"):
        driver.connect("fake")
    driver.disconnect()
    driver.disconnect()
    assert fake.closed and fake.sent == []
    assert driver.commanded_selection is None


@pytest.mark.parametrize(
    "action",
    [
        lambda d: d.query("v"),
        lambda d: d.select_pixel("A", 1),
        lambda d: d.deselect_all(),
        lambda d: d.reset(),
        lambda d: d.firmware_version,
        lambda d: d.detected_muxes,
    ],
)
def test_disconnected(action):
    with pytest.raises(RuntimeError, match="Not connected"):
        action(SnaithMux())


def test_partial_connection_failure():
    fake = FakeSocket()
    fake.incoming = deque(b"welcome >>")
    driver = SnaithMux()
    with patch("socket.create_connection", return_value=fake):
        with pytest.raises(ProtocolError, match="EOF"):
            driver.connect("fake")
    assert fake.closed and not driver.connected and fake.sent == []


def test_open_failure():
    driver = SnaithMux()
    with patch("socket.create_connection", side_effect=OSError("open failed")):
        with pytest.raises(OSError):
            driver.connect("fake")
    assert not driver.connected


def test_mappings_and_deselection():
    assert dict(PIXEL_MASKS) == {
        0: 0,
        1: 259,
        2: 515,
        3: 1027,
        4: 2051,
        5: 4099,
        6: 8195,
        7: 16387,
        8: 32771,
    }
    fake = FakeSocket()
    driver = connect(fake)
    for pixel, mask in PIXEL_MASKS.items():
        driver.select_pixel("A", pixel)
        assert driver.commanded_selection == ("A", mask)
    assert fake.sent[1] == b"sA00259\r\n"
    driver.deselect_slot("B")
    assert fake.sent[-1] == b"sB0\r\n"
    assert driver.commanded_selection == ("B", 0)
    driver.deselect_all()
    assert fake.sent[-1] == b"s\r\n"
    assert driver.commanded_selection == (None, 0)


@pytest.mark.parametrize(
    "response, error",
    [
        (b">>>", ProtocolError),
        (b"", ProtocolError),
        (b"ERROR: mux failed\r\n>>> ", FirmwareError),
        (b"unexpected>>> ", FirmwareError),
        (b"\xff\xff>>> ", ProtocolError),
    ],
)
def test_bad_responses(response, error):
    fake = FakeSocket()
    driver = connect(fake)
    driver.select_pixel("A", 1)
    fake.response = response
    with pytest.raises(error):
        driver.select_pixel("A", 2)
    assert driver.commanded_selection is None
    assert not driver.connected and fake.closed
    assert fake.sent == [b"sA00259\r\n", b"sA00515\r\n"]


def test_timeout_and_send_failure():
    for fail_send in (True, False):
        fake = FakeSocket(b"")
        driver = connect(fake)
        fake.fail_send = fail_send
        fake.incoming.append(socket.timeout("timeout"))
        with pytest.raises(OSError):
            driver.deselect_all()
        assert fake.closed and driver.commanded_selection is None
        assert fake.sent == [b"s\r\n"]


def test_limit():
    fake = FakeSocket()
    fake.incoming = deque(b"x" * 20)
    with patch("socket.create_connection", return_value=fake):
        with pytest.raises(ProtocolError, match="exceeds"):
            SnaithMux(max_response_bytes=8).connect("fake")
    assert fake.closed


@pytest.mark.parametrize(
    "slot,pixel",
    [
        ("a", 1),
        ("AA", 1),
        ("A\r\n", 1),
        ("Å", 1),
        ("A", True),
        ("A", 9),
        ("A", -1),
        ("A", "1"),
    ],
)
def test_invalid_selection(slot, pixel):
    fake = FakeSocket()
    driver = connect(fake)
    driver.select_pixel("A", 1)
    with pytest.raises(ValueError):
        driver.select_pixel(slot, pixel)
    assert len(fake.sent) == 1
    assert driver.commanded_selection == ("A", 259)


@pytest.mark.parametrize(
    "command", ["", "v\r\n", "reset", "sA65535", "sA-1", "sÅ0", "v\ns"]
)
def test_invalid_commands(command):
    fake = FakeSocket()
    driver = connect(fake)
    with pytest.raises(ValueError):
        driver.query(command)
    assert not fake.sent


def test_queries_exact_suffix_and_presence():
    fake = FakeSocket(b"version >\r\n>>> ")
    driver = connect(fake)
    assert driver.firmware_version == "version >"
    fake.response = b"5\r\n>>> "
    assert driver.detected_muxes == ("A", "C")
    assert fake.sent == [b"v\r\n", b"c\r\n"]
    fake.response = b"invalid>>> "
    with pytest.raises(ProtocolError):
        _ = driver.detected_muxes
    assert not driver.connected


def test_non_ascii_welcome_is_rejected_without_reply():
    fake = FakeSocket()
    fake.incoming = deque(b"\xff\xfb\x01Welcome>>> ")
    driver = SnaithMux()
    with patch("socket.create_connection", return_value=fake):
        with pytest.raises(ProtocolError, match="Non-ASCII"):
            driver.connect("fake")
    assert fake.sent == []
    assert fake.closed and not driver.connected


def test_explicit_reset():
    fake = FakeSocket()
    driver = connect(fake)
    driver.reset()
    assert fake.sent == [b"reset\r\n"]
    assert fake.closed and not driver.connected
    assert driver.commanded_selection is None


def test_constructor_validation():
    with pytest.raises(ValueError):
        SnaithMux(max_response_bytes=3)


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), True])
def test_timeout_validation_before_open(timeout):
    with patch("socket.create_connection") as opening:
        with pytest.raises(ValueError, match="timeout"):
            SnaithMux().connect("fake", timeout=timeout)
        opening.assert_not_called()


def test_context_exception_cleanup():
    fake = FakeSocket()
    with pytest.raises(ValueError, match="body"):
        with connect(fake):
            raise ValueError("body")
    assert fake.closed


def test_presence_query_forgets_selection():
    fake = FakeSocket()
    driver = connect(fake)
    driver.select_pixel("A", 1)
    fake.response = b"1>>> "
    assert driver.query("c") == "1"
    assert driver.commanded_selection is None


def test_reset_send_failure_closes():
    fake = FakeSocket()
    driver = connect(fake)
    fake.fail_send = True
    with pytest.raises(OSError):
        driver.reset()
    assert fake.closed and not driver.connected
    assert fake.sent == [b"reset\r\n"]


@pytest.mark.parametrize(
    "host,port", [("", 23), ("bad\nname", 23), ("fake", 0), ("fake", True)]
)
def test_connect_validation(host, port):
    with patch("socket.create_connection") as opening:
        with pytest.raises(ValueError):
            SnaithMux().connect(host, port=port)
        opening.assert_not_called()


def test_welcome_timeout_cleanup():
    fake = FakeSocket()
    fake.incoming = deque([socket.timeout("welcome")])
    driver = SnaithMux()
    with patch("socket.create_connection", return_value=fake):
        with pytest.raises(TimeoutError):
            driver.connect("fake")
    assert fake.closed and not driver.connected and fake.sent == []


def test_send_and_receive_have_separate_timeout_budgets():
    fake = FakeSocket()
    driver = SnaithMux()
    with patch("socket.create_connection", return_value=fake) as opening:
        driver.connect("fake", timeout=1.0)
        opening.assert_called_once_with(("fake", 23), 1.0)
    now = [0.0]
    original_send = fake.sendall
    original_recv = fake.recv

    def slow_send(data: bytes) -> None:
        now[0] += 0.9
        original_send(data)

    def fragmented_recv(size: int) -> bytes:
        now[0] += 0.2
        return original_recv(size)

    with (
        patch("snaith_mux.driver.time.monotonic", side_effect=lambda: now[0]),
        patch.object(fake, "sendall", side_effect=slow_send),
        patch.object(fake, "recv", side_effect=fragmented_recv),
        patch.object(fake, "settimeout") as setting,
    ):
        driver.deselect_all()
    assert now[0] == pytest.approx(1.7)
    assert [call.args[0] for call in setting.call_args_list] == pytest.approx(
        [1.0, 1.0, 0.8, 0.6, 0.4]
    )
    assert driver.commanded_selection == (None, 0)
    assert driver.connected


def test_response_timeout_is_not_restarted_for_each_byte():
    fake = FakeSocket()
    driver = SnaithMux()
    with patch("socket.create_connection", return_value=fake):
        driver.connect("fake", timeout=1.0)
    now = [0.0]
    original_recv = fake.recv

    def slow_recv(size: int) -> bytes:
        now[0] += 0.6
        return original_recv(size)

    with (
        patch("snaith_mux.driver.time.monotonic", side_effect=lambda: now[0]),
        patch.object(fake, "recv", side_effect=slow_recv),
        patch.object(fake, "settimeout") as setting,
    ):
        with pytest.raises(TimeoutError, match="complete"):
            driver.deselect_all()
    assert [call.args[0] for call in setting.call_args_list] == pytest.approx(
        [1.0, 1.0, 0.4]
    )
    assert fake.closed and not driver.connected
    assert driver.commanded_selection is None


def test_reconnect_uses_new_timeout_and_reset_uses_comms_timeout():
    driver = SnaithMux()
    for timeout in (0.5, 2.0):
        fake = FakeSocket()
        with patch("socket.create_connection", return_value=fake) as opening:
            driver.connect("fake", timeout=timeout)
            opening.assert_called_once_with(("fake", 23), timeout)
        with patch.object(fake, "settimeout") as setting:
            driver.reset()
            setting.assert_called_once_with(timeout)
        assert fake.closed


def test_timeout_property_requires_connection():
    driver = SnaithMux()
    for disconnected in (True, False):
        if not disconnected:
            fake = FakeSocket()
            with patch("socket.create_connection", return_value=fake):
                driver.connect("fake")
            driver.disconnect()
        with pytest.raises(RuntimeError, match="Not connected"):
            _ = driver.timeout
        with pytest.raises(RuntimeError, match="Not connected"):
            driver.timeout = 2.0


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan"), True])
def test_invalid_timeout_assignment_preserves_setting_and_state(value):
    fake = FakeSocket()
    driver = connect(fake)
    driver.select_pixel("A", 1)
    with pytest.raises(ValueError, match="timeout"):
        driver.timeout = value
    assert driver.timeout == 5.0
    assert driver.connected and driver.commanded_selection == ("A", 259)
    assert fake.sent == [b"sA00259\r\n"]


def test_timeout_property_applies_to_subsequent_send_receive_and_reset():
    fake = FakeSocket()
    driver = SnaithMux()
    with patch("socket.create_connection", return_value=fake):
        driver.connect("fake", timeout=1.0)
    assert driver.timeout == 1.0
    with patch.object(fake, "settimeout") as setting:
        driver.timeout = 2.5
        assert driver.timeout == 2.5
        setting.assert_not_called()
        assert fake.sent == []
        with patch("snaith_mux.driver.time.monotonic", return_value=10.0):
            driver.deselect_all()
        assert [call.args[0] for call in setting.call_args_list] == [2.5] * 5
        setting.reset_mock()
        driver.reset()
        setting.assert_called_once_with(2.5)
