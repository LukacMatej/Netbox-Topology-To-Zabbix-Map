def test_cli_starts_web_server_by_default(monkeypatch) -> None:
    import zabbix_map_sync.cli as cli

    calls = []
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setattr(cli, "run_server", lambda **kwargs: calls.append(kwargs))

    assert cli.main([]) == 0
    assert calls == [{"host": "0.0.0.0", "port": 8080, "log_level": "INFO"}]


def test_cli_passes_host_port_and_log_level(monkeypatch) -> None:
    import zabbix_map_sync.cli as cli

    calls = []
    monkeypatch.setattr(cli, "run_server", lambda **kwargs: calls.append(kwargs))

    cli.main(["--host", "127.0.0.1", "--port", "7010", "--log-level", "WARNING"])

    assert calls == [{"host": "127.0.0.1", "port": 7010, "log_level": "WARNING"}]
