from backend.collectors.netdata import NetdataCollector
from backend.collectors.docker import DockerCollector


class Orchestrator:

    def __init__(self):
        self.netdata_collector = NetdataCollector()
        self.docker_collector = DockerCollector()