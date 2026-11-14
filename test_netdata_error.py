from backend.collectors.netdata import NetdataCollector

collector = NetdataCollector("http://localhost:19998")

data = collector.collect()

print(data)