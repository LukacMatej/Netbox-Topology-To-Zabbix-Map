from types import SimpleNamespace

from zabbix_map_sync.runner import DryRunResult
from zabbix_map_sync.sync import SyncResult


def test_cli_dry_run(monkeypatch, capsys) -> None:
    import zabbix_map_sync.cli as cli

    monkeypatch.setattr(
        cli,
        "run_synchronization",
        lambda dry_run=False: DryRunResult(total_nodes=3, total_links=2),
    )
    monkeypatch.setattr(cli, "parse_args", lambda: SimpleNamespace(dry_run=True))

    exit_code = cli.main()
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "Topology nodes: 3" in output
    assert "Dry-run complete" in output


def test_cli_prints_unresolved_rule_details(monkeypatch, capsys) -> None:
    import zabbix_map_sync.cli as cli

    def fake_run_sync(dry_run=False):
        return SyncResult(
            created=False,
            map_name="NetBox Topology",
            total_nodes=1,
            matched_hosts=1,
            skipped_nodes=0,
            image_nodes=0,
            total_links=0,
            unresolved_link_rules=1,
            unresolved_link_rule_details=(
                "Cable trigger: Switch 1 <-> Switch 2 | trigger='Link down'",
            ),
        )

    monkeypatch.setattr(cli, "run_synchronization", fake_run_sync)
    monkeypatch.setattr(cli, "parse_args", lambda: SimpleNamespace(dry_run=False))

    exit_code = cli.main()
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "Warning: 1 cable trigger(s)" in output
    assert "Cable trigger: Switch 1 <-> Switch 2" in output


def test_cli_serve_mode(monkeypatch) -> None:
    import zabbix_map_sync.cli as cli

    calls: list[tuple[str, int]] = []

    def fake_run_server(host: str, port: int) -> None:
        calls.append((host, port))

    monkeypatch.setattr(cli, "run_server", fake_run_server)
    monkeypatch.setattr(
        cli,
        "parse_args",
        lambda: SimpleNamespace(serve=True, host="127.0.0.1", port=9999, dry_run=False),
    )

    exit_code = cli.main()

    assert exit_code == 0
    assert calls == [("127.0.0.1", 9999)]
