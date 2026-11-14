
import requests

from backend.collectors.base import BaseCollector


class NetdataCollector(BaseCollector):

    def __init__(self, base_url="http://localhost:19999"):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()

    def _get_chart_data(self, chart_id):
        url = f"{self.base_url}/api/v1/data"

        params = {
            "chart": chart_id,
            "after": -5,
            "points": 1
        }

        response = self.session.get(
            url,
            params=params,
            timeout=5
        )

        response.raise_for_status()
        result = response.json()

        if not result.get("data"):
            return None

        return dict(zip(result["labels"], result["data"][0]))

    def _get_network_interfaces(self):
        url = f"{self.base_url}/api/v1/charts"

        response = self.session.get(url, timeout=5)
        response.raise_for_status()

        charts = response.json().get("charts", {})

        return [
            chart_id.split(".", 1)[1]
            for chart_id in charts
            if chart_id.startswith("net.")
        ]

    def collect(self):
        results = {
            "source": "netdata",
            "cpu": None,
            "ram": None,
            "available": None,
            "swap": None,
            "network": {},
            "network_details": {},
            "errors": {}
        }

        # 1. Métriques système
        charts = {
            "cpu": "system.cpu",
            "ram": "system.ram",
            "available": "mem.available",
            "swap": "mem.swap"
        }

        for category, chart_id in charts.items():
            try:
                results[category] = self._get_chart_data(chart_id)

                if results[category] is None:
                    results["errors"][category] = "No data available"

            except (
                requests.RequestException,
                ValueError,
                KeyError,
                TypeError
            ) as error:
                results["errors"][category] = str(error)

        # 2. Découverte des interfaces réseau
        try:
            interfaces = self._get_network_interfaces()

            for interface in interfaces:

                # Bande passante
                bandwidth_chart = f"net.{interface}"

                try:
                    data = self._get_chart_data(bandwidth_chart)

                    if data is None:
                        results["errors"][bandwidth_chart] = (
                            "No data available"
                        )
                    else:
                        results["network"][bandwidth_chart] = data

                except (
                    requests.RequestException,
                    ValueError,
                    KeyError,
                    TypeError
                ) as error:
                    results["errors"][bandwidth_chart] = str(error)

                # Autres métriques de l'interface
                metric_charts = {
                    "packets": f"net_packets.{interface}",
                    "errors": f"net_errors.{interface}",
                    "drops": f"net_drops.{interface}",
                    "speed": f"net_speed.{interface}",
                    "duplex": f"net_duplex.{interface}",
                    "operstate": f"net_operstate.{interface}",
                    "carrier": f"net_carrier.{interface}",
                    "mtu": f"net_mtu.{interface}"
                }

                results["network_details"][interface] = {}

                for metric, chart_id in metric_charts.items():
                    try:
                        data = self._get_chart_data(chart_id)

                        if data is None:
                            results["errors"][chart_id] = (
                                "No data available"
                            )
                        else:
                            results["network_details"][interface][
                                metric
                            ] = data

                    except (
                        requests.RequestException,
                        ValueError,
                        KeyError,
                        TypeError
                    ) as error:
                        results["errors"][chart_id] = str(error)

        except (
            requests.RequestException,
            ValueError,
            KeyError,
            TypeError
        ) as error:
            results["errors"]["network_discovery"] = str(error)

        # 3. Métriques dérivées
        results["derived"] = {
            "cpu": self._derive_cpu_metrics(results["cpu"]),
            "ram": self._derive_ram_metrics(
                results["ram"],
                results["available"]
            ),
            "swap": self._derive_swap_metrics(results["swap"]),
            "network": self._derive_network_metrics(
                results["network"],
                results["network_details"]
            )
        }

        return results

    def _derive_cpu_metrics(self, cpu):
        if cpu is None:
            return None

        activity_keys = [
            "user", "system", "nice",
            "irq", "softirq", "steal",
            "guest", "guest_nice"
        ]

        activity = sum(
            cpu.get(key, 0) or 0
            for key in activity_keys
        )

        return {
            "activity_percent": activity,
            "iowait_percent": cpu.get("iowait", 0)
        }

    def _derive_ram_metrics(self, ram, available):
        if ram is None:
            return None

        free = ram.get("free", 0) or 0
        used = ram.get("used", 0) or 0
        cached = ram.get("cached", 0) or 0
        buffers = ram.get("buffers", 0) or 0

        total = free + used + cached + buffers

        if total <= 0:
            return None

        available_mib = None
        available_percent = None

        if available is not None:
            available_mib = available.get("avail")

            if available_mib is not None:
                available_percent = (available_mib / total) * 100

        return {
            "total_mib": total,
            "used_percent": (used / total) * 100,
            "available_mib": available_mib,
            "available_percent": available_percent
        }

    def _derive_swap_metrics(self, swap):
        if swap is None:
            return None

        free = swap.get("free", 0) or 0
        used = swap.get("used", 0) or 0

        total = free + used

        if total <= 0:
            return None

        return {
            "total_mib": total,
            "used_mib": used,
            "used_percent": (used / total) * 100
        }

    def _get_active_state(self, data):
        if data is None:
            return None

        for key, value in data.items():
            if key != "time" and value == 1:
                return key

        return "unknown"

    @staticmethod
    def _absolute_value(value):
        if value is None:
            return None

        return abs(value)

    def _derive_network_metrics(self, network, network_details):
        derived = {}

        # Bande passante
        for chart_id, data in network.items():
            interface = chart_id.split(".", 1)[1]

            received = data.get("received")
            sent = data.get("sent")

            derived[interface] = {
                "received_kbps": self._absolute_value(received),
                "sent_kbps": self._absolute_value(sent),
                "total_kbps": (
                    abs(received) + abs(sent)
                    if received is not None and sent is not None
                    else None
                )
            }

        # Autres métriques réseau
        for interface, details in network_details.items():
            metrics = derived.setdefault(interface, {})

            packets = details.get("packets")
            errors = details.get("errors")
            drops = details.get("drops")
            speed = details.get("speed")
            duplex = details.get("duplex")
            operstate = details.get("operstate")
            carrier = details.get("carrier")
            mtu = details.get("mtu")

            if packets is not None:
                metrics["packets"] = {
                    "received_per_second": packets.get("received"),
                    "sent_per_second": self._absolute_value(
                        packets.get("sent")
                    ),
                    "multicast_per_second": packets.get("multicast")
                }

            if errors is not None:
                metrics["errors_per_second"] = {
                    "inbound": errors.get("inbound"),
                    "outbound": errors.get("outbound")
                }

            if drops is not None:
                metrics["drops_per_second"] = {
                    "inbound": drops.get("inbound"),
                    "outbound": drops.get("outbound")
                }

            if speed is not None:
                metrics["speed_kbps"] = speed.get("speed")

            if duplex is not None:
                metrics["duplex"] = self._get_active_state(duplex)

            if operstate is not None:
                metrics["operational_state"] = self._get_active_state(
                    operstate
                )

            if carrier is not None:
                metrics["carrier_state"] = self._get_active_state(carrier)

            if mtu is not None:
                metrics["mtu_bytes"] = mtu.get("mtu")

        return derived
