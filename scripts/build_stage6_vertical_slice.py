"""Retired initial Stage 6 diagnostic; its obsolete output was removed.

The current construction entry is build_stage6_golden_evidence.py, whose input
adapter validates the current Stage 5 reviewed PDFs. This initial diagnostic
used obsolete RapidOCR boxes and must not regenerate or approve current data.
"""


def main() -> None:
    raise RuntimeError(
        "the initial Stage 6 vertical slice is retired; use "
        "scripts/build_stage6_golden_evidence.py with current Stage 5 inputs"
    )


if __name__ == "__main__":
    main()
