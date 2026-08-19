"""Инвариант: то, что мы пишем в Saved Messages, мы же умеем не читать обратно."""

from __future__ import annotations

from tg_data.report import error_report, is_report, pull_report


def test_pull_report_is_recognized() -> None:
    assert is_report(pull_report(5, 4))


def test_error_report_is_recognized() -> None:
    assert is_report(error_report("ChannelPrivateError"))


def test_legacy_report_without_marker_is_recognized() -> None:
    assert is_report("✅ tg-data pull\nНовых сообщений: 0\nИсточников: 0")
    assert is_report("❌ tg-data error\nChannelPrivateError")


def test_user_note_is_not_a_report() -> None:
    assert not is_report("напоминание: продлить ВНЖ")


def test_note_mentioning_tg_data_is_not_a_report() -> None:
    assert not is_report("✅ tg-data pull прошёл, надо глянуть логи")


def test_empty_text_is_not_a_report() -> None:
    assert not is_report(None)
    assert not is_report("")
