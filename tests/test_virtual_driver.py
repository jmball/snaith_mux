"""Virtual-driver API and state tests, with network access forbidden."""

from unittest.mock import patch

import pytest

from snaith_mux import PIXEL_MASKS, FirmwareError
from virtual_snaith_mux import SnaithMux


@pytest.fixture(autouse=True)
def forbid_network():
    with (
        patch("socket.create_connection", side_effect=AssertionError("network I/O")),
        patch("socket.getaddrinfo", side_effect=AssertionError("DNS lookup")),
    ):
        yield


def test_lifecycle_and_timeout():
    with SnaithMux(slots=("C", "A")) as mux:
        assert not mux.connected
        assert mux.welcome_message is None
        assert mux.commanded_selection is None
        mux.connect("unused-host", timeout=1.5)
        assert mux.connected and mux.timeout == 1.5
        assert mux.welcome_message == "Virtual Snaith mux controller"
        assert mux.firmware_version.startswith("virtual-")
        assert mux.detected_muxes == ("A", "C")
        with pytest.raises(RuntimeError, match="Already connected"):
            mux.connect("unused-host")
        mux.timeout = 2.0
        assert mux.timeout == 2.0
        with pytest.raises(ValueError):
            mux.timeout = True
        assert mux.timeout == 2.0
    assert not mux.connected and mux.welcome_message is None
    mux.disconnect()
    mux.connect("unused-host")
    assert mux.timeout == 5.0
    assert mux.detected_muxes == ("A", "C")


def test_selection_and_presence_state():
    mux = SnaithMux(slots=("A", "B"))
    mux.connect("virtual")
    for pixel, mask in PIXEL_MASKS.items():
        mux.select_pixel("B", pixel)
        assert mux.commanded_selection == ("B", mask)
    mux.deselect_slot("A")
    assert mux.commanded_selection == ("A", 0)
    mux.deselect_all()
    assert mux.commanded_selection == (None, 0)
    assert mux.query("c") == "3"
    assert mux.commanded_selection is None
    assert mux.query("sA00515") == ""
    assert mux.commanded_selection == ("A", 515)
    assert mux.firmware_version
    assert mux.commanded_selection == ("A", 515)
    mux.disconnect()
    assert mux.commanded_selection is None


def test_absent_slot_failure_clears_session():
    mux = SnaithMux()
    mux.connect("virtual")
    mux.select_pixel("A", 1)
    with pytest.raises(FirmwareError, match="not installed"):
        mux.select_pixel("B", 1)
    assert not mux.connected
    assert mux.commanded_selection is None and mux.welcome_message is None
    mux.connect("virtual")
    assert mux.detected_muxes == ("A",)


def test_explicit_reset():
    mux = SnaithMux()
    mux.connect("virtual")
    mux.select_pixel("A", 1)
    mux.reset()
    assert not mux.connected and mux.commanded_selection is None
    mux.connect("virtual")
    assert mux.commanded_selection is None
    assert mux.detected_muxes == ("A",)


@pytest.mark.parametrize(
    "action",
    [
        lambda m: m.query("v"),
        lambda m: m.select_pixel("A", 1),
        lambda m: m.deselect_slot("A"),
        lambda m: m.deselect_all(),
        lambda m: m.reset(),
        lambda m: m.timeout,
        lambda m: setattr(m, "timeout", 2),
    ],
)
def test_disconnected_operations(action):
    with pytest.raises(RuntimeError, match="Not connected"):
        action(SnaithMux())


@pytest.mark.parametrize(
    "action",
    [
        lambda m: m.query("v\r\n"),
        lambda m: m.query("sA65535"),
        lambda m: m.query("reset"),
        lambda m: m.select_pixel("a", 1),
        lambda m: m.select_pixel("A", True),
        lambda m: m.select_pixel("A", 9),
    ],
)
def test_validation_preserves_selection(action):
    mux = SnaithMux()
    mux.connect("virtual")
    mux.select_pixel("A", 1)
    with pytest.raises(ValueError):
        action(mux)
    assert mux.connected and mux.commanded_selection == ("A", 259)


@pytest.mark.parametrize(
    "options",
    [
        {"slots": ("a",)},
        {"slots": ("AA",)},
        {"slots": ("A", "A")},
        {"slots": ("Å",)},
        {"max_response_bytes": 3},
    ],
)
def test_constructor_validation(options):
    with pytest.raises(ValueError):
        SnaithMux(**options)


@pytest.mark.parametrize(
    "host,options",
    [
        ("", {}),
        ("bad\nname", {}),
        ("virtual", {"port": 0}),
        ("virtual", {"timeout": 0}),
        ("virtual", {"timeout": True}),
    ],
)
def test_connection_validation(host, options):
    mux = SnaithMux()
    with pytest.raises(ValueError):
        mux.connect(host, **options)
    assert not mux.connected and mux.welcome_message is None


def test_no_installed_slots():
    mux = SnaithMux(slots=())
    mux.connect("virtual")
    assert mux.detected_muxes == ()
    mux.deselect_all()
    assert mux.commanded_selection == (None, 0)


def test_context_cleans_up_after_exception():
    mux = SnaithMux()
    with pytest.raises(ValueError, match="body"):
        with mux:
            mux.connect("virtual")
            mux.select_pixel("A", 1)
            raise ValueError("body")
    assert not mux.connected and mux.commanded_selection is None
