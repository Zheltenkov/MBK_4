"""Safety checks for the production read-only reporting entry point."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import psycopg
import pytest

from analytics.production_leads import (
    ConnectionSettings,
    ReportConfigurationError,
    collect_report,
    load_password,
    main,
)


def _database_mock(read_only: str = "on"):
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = (
        datetime(2026, 11, 2, 12, tzinfo=timezone.utc),
        "2026-11-02 15:00:00", "mbk", "mbk_app", read_only,
    )
    cursor.description = []
    cursor.fetchall.return_value = []
    connector = MagicMock()
    connector.return_value.__enter__.return_value = connection
    return connector, connection, cursor


def test_connection_is_read_only_before_reports_and_shares_one_connection():
    connector, connection, cursor = _database_mock()
    settings = ConnectionSettings("example.invalid", 5432, "mbk", "mbk_app", "test-secret")
    with patch("analytics.production_leads.psycopg.connect", connector):
        report = collect_report(settings)
    connector.assert_called_once()
    assert "default_transaction_read_only=on" in connector.call_args.kwargs["options"]
    assert "statement_timeout=" in connector.call_args.kwargs["options"]
    assert connection.read_only is True
    assert connection.isolation_level is psycopg.IsolationLevel.REPEATABLE_READ
    assert report.transaction_read_only == "on"
    assert cursor.execute.call_count > 1
    # All sections use the exact timestamp received from the database, not client wall time.
    timestamps = [call.args[1]["as_of"] for call in cursor.execute.call_args_list[1:]]
    assert timestamps and set(timestamps) == {datetime(2026, 11, 2, 12, tzinfo=timezone.utc)}
    assert "test-secret" not in report.model_dump_json()
    assert "test-secret" not in repr(settings)


def test_reports_do_not_run_when_server_does_not_confirm_read_only():
    connector, _, cursor = _database_mock(read_only="off")
    with patch("analytics.production_leads.psycopg.connect", connector):
        with pytest.raises(RuntimeError, match="read-only"):
            collect_report(ConnectionSettings("example.invalid", 5432, "mbk", "mbk_app", "secret"))
    assert cursor.execute.call_count == 1


def test_legacy_credential_label_is_preserved_without_exposing_other_lines(tmp_path):
    path = tmp_path / "access.txt"
    path.write_text("other: unrelated\nmbk_app_password: test-secret\n", encoding="utf-8")
    assert load_password(path) == "test-secret"
    path.write_text("other: unrelated\n", encoding="utf-8")
    with pytest.raises(ReportConfigurationError) as error:
        load_password(path)
    assert "unrelated" not in str(error.value)
    # An empty credential must not consume the next line as a password.
    path.write_text("mbk_app_password:\nother: unrelated\n", encoding="utf-8")
    with pytest.raises(ReportConfigurationError):
        load_password(path)


def test_database_errors_do_not_leak_connection_or_customer_details(monkeypatch, capsys):
    monkeypatch.setenv("MBK_REPORT_PASSWORD", "test-secret")
    with patch("analytics.production_leads.collect_report",
               side_effect=psycopg.OperationalError("password=test-secret customer-private-data")):
        assert main([]) == 1
    output = capsys.readouterr().out
    assert "OperationalError" in output
    assert "test-secret" not in output
    assert "customer-private-data" not in output
