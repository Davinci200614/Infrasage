
import requests

from backend.collectors.netdata import NetdataCollector


def test_get_chart_data_returns_first_valid_sample(monkeypatch):
    collector = NetdataCollector("http://localhost:19999/")

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "labels": ["time", "user", "system"],
                "data": [
                    [100, None, None],
                    [101, 20, 10],
                ],
            }

    def fake_get(*args, **kwargs):
        return FakeResponse()

    monkeypatch.setattr(collector.session, "get", fake_get)

    result = collector._get_chart_data("system.cpu")

    assert result == {
        "time": 101,
        "user": 20,
        "system": 10,
    }


def test_get_chart_data_returns_none_when_no_data(monkeypatch):
    collector = NetdataCollector()

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"labels": ["time", "user"], "data": []}

    monkeypatch.setattr(
        collector.session,
        "get",
        lambda *args, **kwargs: FakeResponse()
    )

    assert collector._get_chart_data("system.cpu") is None


def test_get_network_interfaces_extracts_only_net_charts(monkeypatch):
    collector = NetdataCollector()

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "charts": {
                    "net.eth0": {},
                    "net.wlan0": {},
                    "system.cpu": {},
                    "net_packets.eth0": {},
                }
            }

    monkeypatch.setattr(
        collector.session,
        "get",
        lambda *args, **kwargs: FakeResponse()
    )

    assert collector._get_network_interfaces() == ["eth0", "wlan0"]


def test_get_disk_charts_extracts_disk_metadata(monkeypatch):
    collector = NetdataCollector()

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "charts": {
                    "disk_space.root": {
                        "title": "Root space",
                        "units": "GiB",
                        "family": "disk",
                        "context": "disk.space",
                    },
                    "disk_util.sda": {
                        "title": "Disk utilization",
                        "units": "%",
                        "family": "disk",
                        "context": "disk.util",
                    },
                    "system.cpu": {"title": "CPU"},
                }
            }

    monkeypatch.setattr(
        collector.session,
        "get",
        lambda *args, **kwargs: FakeResponse()
    )

    result = collector._get_disk_charts()

    assert set(result) == {"disk_space.root", "disk_util.sda"}
    assert result["disk_space.root"]["units"] == "GiB"


def test_derive_cpu_metrics():
    collector = NetdataCollector()

    result = collector._derive_cpu_metrics({
        "user": 20,
        "system": 10,
        "nice": None,
        "iowait": 5,
    })

    assert result == {
        "activity_percent": 30,
        "iowait_percent": 5,
    }


def test_derive_cpu_metrics_returns_none_without_data():
    collector = NetdataCollector()

    assert collector._derive_cpu_metrics(None) is None


def test_derive_ram_metrics():
    collector = NetdataCollector()

    result = collector._derive_ram_metrics(
        {
            "free": 100,
            "used": 300,
            "cached": 50,
            "buffers": 50,
        },
        {"avail": 200},
    )

    assert result["total_mib"] == 500
    assert result["used_percent"] == 60
    assert result["available_mib"] == 200
    assert result["available_percent"] == 40


def test_derive_ram_metrics_rejects_incomplete_or_zero_total():
    collector = NetdataCollector()

    assert collector._derive_ram_metrics(
        {"free": 10, "used": None, "cached": 0, "buffers": 0},
        None,
    ) is None

    assert collector._derive_ram_metrics(
        {"free": 0, "used": 0, "cached": 0, "buffers": 0},
        None,
    ) is None


def test_derive_swap_metrics():
    collector = NetdataCollector()

    result = collector._derive_swap_metrics({
        "free": 75,
        "used": 25,
    })

    assert result == {
        "total_mib": 100,
        "used_mib": 25,
        "used_percent": 25,
    }


def test_derive_network_metrics():
    collector = NetdataCollector()

    result = collector._derive_network_metrics(
        {"net.eth0": {"received": -100, "sent": -50}},
        {
            "eth0": {
                "packets": {
                    "received": 10,
                    "sent": -20,
                    "multicast": 2,
                },
                "duplex": {"full": 1, "half": 0},
                "operstate": {"up": 1, "down": 0},
                "mtu": {"mtu": 1500},
            }
        },
    )

    assert result["eth0"]["received_kbps"] == 100
    assert result["eth0"]["sent_kbps"] == 50
    assert result["eth0"]["total_kbps"] == 150
    assert result["eth0"]["packets"]["sent_per_second"] == 20
    assert result["eth0"]["duplex"] == "full"
    assert result["eth0"]["operational_state"] == "up"
    assert result["eth0"]["mtu_bytes"] == 1500


def test_derive_disk_metrics():
    collector = NetdataCollector()

    result = collector._derive_disk_metrics(
        {
            "disk_space.root": {
                "avail": 60,
                "used": 30,
                "reserved": 10,
            },
            "disk_util.sda": {"utilization": -70},
            "disk_ops.sda": {"reads": -5, "writes": -3},
            "disk_await.sda": {"reads": -2, "writes": -4},
        },
        {
            "disk_space.root": {
                "title": "Root",
                "units": "GiB",
                "family": "disk",
                "context": "disk.space",
            }
        },
    )

    assert result["disk_space.root"]["space"]["total_gib"] == 100
    assert result["disk_space.root"]["space"]["used_percent"] == 30
    assert result["disk_util.sda"]["utilization_percent"] == 70
    assert result["disk_ops.sda"]["operations_per_second"]["total"] == 8
    assert result["disk_await.sda"]["latency_ms_per_operation"] == {
        "reads": 2,
        "writes": 4,
    }


def test_collect_returns_metrics_and_derived_data(monkeypatch):
    collector = NetdataCollector()

    def fake_chart_data(chart_id, after=-5, points=1):
        values = {
            "system.cpu": {"user": 20, "system": 10, "iowait": 2},
            "system.ram": {
                "free": 100,
                "used": 300,
                "cached": 50,
                "buffers": 50,
            },
            "mem.available": {"avail": 200},
            "mem.swap": {"free": 75, "used": 25},
            "net.eth0": {"received": 100, "sent": 50},
            "net_packets.eth0": {
                "received": 10,
                "sent": 20,
                "multicast": 2,
            },
            "net_errors.eth0": {"inbound": 0, "outbound": 0},
            "net_drops.eth0": {"inbound": 0, "outbound": 0},
            "net_speed.eth0": {"speed": 1000},
            "net_duplex.eth0": {"full": 1, "half": 0},
            "net_operstate.eth0": {"up": 1, "down": 0},
            "net_carrier.eth0": {"carrier": 1, "no_carrier": 0},
            "net_mtu.eth0": {"mtu": 1500},
            "disk_space.root": {
                "avail": 60,
                "used": 30,
                "reserved": 10,
            },
        }

        return values.get(chart_id)

    monkeypatch.setattr(collector, "_get_chart_data", fake_chart_data)

    monkeypatch.setattr(
        collector,
        "_get_network_interfaces",
        lambda: ["eth0"]
    )

    monkeypatch.setattr(
        collector,
        "_get_disk_charts",
        lambda: {
            "disk_space.root": {
                "title": "Root",
                "units": "GiB",
                "family": "disk",
                "context": "disk.space",
            }
        }
    )

    result = collector.collect()

    assert result["source"] == "netdata"
    assert result["cpu"]["user"] == 20
    assert result["network"]["net.eth0"]["received"] == 100
    assert result["disk"]["disk_space.root"]["used"] == 30

    assert result["derived"]["cpu"]["activity_percent"] == 30
    assert result["derived"]["ram"]["total_mib"] == 500
    assert result["derived"]["network"]["eth0"]["speed_kbps"] == 1000
    assert result["derived"]["network"]["eth0"]["mtu_bytes"] == 1500

    assert result["errors"] == {}


def test_collect_records_network_discovery_error(monkeypatch):
    collector = NetdataCollector()

    monkeypatch.setattr(
        collector,
        "_get_chart_data",
        lambda *args, **kwargs: None
    )

    def fail_discovery():
        raise requests.ConnectionError("Netdata unavailable")

    monkeypatch.setattr(
        collector,
        "_get_network_interfaces",
        fail_discovery
    )

    monkeypatch.setattr(
        collector,
        "_get_disk_charts",
        lambda: {}
    )

    result = collector.collect()

    assert "network_discovery" in result["errors"]
    assert "Netdata unavailable" in result["errors"]["network_discovery"]
