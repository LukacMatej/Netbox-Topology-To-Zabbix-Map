from zabbix_map_sync.trigger_picker import (
    CableTriggerContext,
    TriggerChoice,
    load_cable_trigger_context,
    render_trigger_picker_html,
    save_cable_trigger_selection,
)
from zabbix_map_sync.zabbix import ZabbixHost


class FakeNetBoxClient:
    def __init__(self, cable, device_pair, devices_by_id, trigger_names=()):
        self._cable = cable
        self._device_pair = device_pair
        self._devices_by_id = devices_by_id
        self._trigger_names = trigger_names
        self.saved = None

    def get_cable(self, cable_id):
        assert cable_id == "42"
        return self._cable

    def resolve_cable_device_pair(self, cable):
        assert cable is self._cable
        return self._device_pair

    def fetch_devices_by_ids(self, device_ids):
        assert device_ids == set(self._device_pair)
        return self._devices_by_id

    def get_cable_trigger_names(self, cable):
        return self._trigger_names

    def set_cable_custom_field(self, cable_id, field_name, value):
        self.saved = (cable_id, field_name, value)
        return {}


class FakeZabbixClient:
    def __init__(self, hosts_by_name, triggers):
        self._hosts_by_name = hosts_by_name
        self._triggers = triggers
        self.requested_hostids = None

    def get_hosts_by_names(self, names):
        return {name: self._hosts_by_name[name] for name in names if name in self._hosts_by_name}

    def list_triggers_for_hosts(self, hostids):
        self.requested_hostids = hostids
        return self._triggers


def test_load_cable_trigger_context_scopes_triggers_to_link_devices() -> None:
    cable = {"id": "42"}
    netbox = FakeNetBoxClient(
        cable=cable,
        device_pair=("1", "2"),
        devices_by_id={
            "1": {"id": "1", "name": "Switch 1"},
            "2": {"id": "2", "name": "Switch 2"},
        },
        trigger_names=("Link down",),
    )
    zabbix = FakeZabbixClient(
        hosts_by_name={
            "Switch 1": ZabbixHost(hostid="10", host="switch-1", name="Switch 1"),
            "Switch 2": ZabbixHost(hostid="20", host="switch-2", name="Switch 2"),
        },
        triggers=[
            {"triggerid": "555", "description": "Link down"},
            {"triggerid": "556", "description": "High CPU"},
        ],
    )

    context = load_cable_trigger_context(netbox, zabbix, "42")

    assert context.device_a == "Switch 1"
    assert context.device_b == "Switch 2"
    assert context.selected_triggers == ("Link down",)
    assert context.available_triggers == (
        TriggerChoice(triggerid="555", description="Link down"),
        TriggerChoice(triggerid="556", description="High CPU"),
    )
    assert zabbix.requested_hostids == ["10", "20"]


def test_load_cable_trigger_context_raises_without_resolvable_devices() -> None:
    netbox = FakeNetBoxClient(cable={"id": "42"}, device_pair=None, devices_by_id={})
    zabbix = FakeZabbixClient(hosts_by_name={}, triggers=[])

    try:
        load_cable_trigger_context(netbox, zabbix, "42")
    except ValueError as exc:
        assert "cable_id=42" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_save_cable_trigger_selection_strips_blank_entries() -> None:
    netbox = FakeNetBoxClient(cable={}, device_pair=("1", "2"), devices_by_id={})

    save_cable_trigger_selection(netbox, "42", ["Link down", "  ", "High CPU"])

    assert netbox.saved == ("42", "zabbix_triggers", ["Link down", "High CPU"])


def test_render_trigger_picker_html_marks_selected_and_escapes() -> None:
    context = CableTriggerContext(
        cable_id="42",
        device_a="Switch <1>",
        device_b="Switch 2",
        selected_triggers=("Link down",),
        available_triggers=(
            TriggerChoice(triggerid="555", description="Link down"),
            TriggerChoice(triggerid="556", description="High CPU"),
        ),
    )

    body = render_trigger_picker_html(context)

    assert "Switch &lt;1&gt;" in body
    assert "value=\"Link down\" checked" in body
    assert "value=\"High CPU\">" in body
    assert "/cables/42/triggers" in body


def test_render_trigger_picker_html_handles_no_available_triggers() -> None:
    context = CableTriggerContext(
        cable_id="42",
        device_a="Switch 1",
        device_b="Switch 2",
        selected_triggers=(),
        available_triggers=(),
    )

    body = render_trigger_picker_html(context)

    assert "No Zabbix triggers found" in body
