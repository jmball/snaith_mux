"""Select, deselect, and reset real hardware; run with --help for options."""

import argparse

from snaith_mux import SnaithMux


def main() -> None:
    """Connect, select a pixel, deselect, then explicitly reset the controller."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", help="Controller hostname or IP address")
    parser.add_argument("--port", type=int, default=23)
    parser.add_argument("--timeout", type=float, default=5.0, help="Timeout in seconds")
    parser.add_argument("--slot", default="A", help="Uppercase mux slot letter")
    parser.add_argument("--pixel", type=int, choices=range(1, 9), default=1)
    args = parser.parse_args()

    with SnaithMux() as mux:
        mux.connect(args.host, port=args.port, timeout=args.timeout)
        print("Welcome:", mux.welcome_message)
        print("Firmware:", mux.firmware_version)
        try:
            mux.select_pixel(args.slot, args.pixel)
            print("Last acknowledged selection:", mux.commanded_selection)
            mux.deselect_slot(args.slot)
        finally:
            if mux.connected:
                mux.deselect_all()
        mux.reset()


if __name__ == "__main__":
    main()
