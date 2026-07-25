from __future__ import annotations

from somach.sources import parse_adc_line, parse_meta_line


def test_adc_parser_accepts_only_plain_12_bit_values() -> None:
    assert parse_adc_line("2048\n") == 2048
    assert parse_adc_line("0") == 0
    assert parse_adc_line("4095") == 4095
    assert parse_adc_line("4096") is None
    assert parse_adc_line("#META,rate_hz=1000") is None
    assert parse_adc_line("boot:rst") is None
    assert parse_adc_line("1,2048") is None


def test_metadata_parser_matches_firmware_protocol() -> None:
    fields = parse_meta_line(
        "#META,rate_hz=999.80,missed_total=2,tx_drop_total=0,"
        "lo_plus=0,lo_minus=1,clip_low=3,clip_high=4"
    )
    assert fields["rate_hz"] == "999.80"
    assert fields["lo_minus"] == "1"
    assert fields["clip_high"] == "4"
