
from backend.collectors.netdata import NetdataCollector

collector = NetdataCollector()

data = collector.collect()

print("Source:")
print(data["source"])

print("\nCPU:")
print(data["cpu"])

print("\nRAM:")
print(data["ram"])

print("\nNetwork:")
print(data["network"])

print("\nErrors:")
print(data["errors"])

print("\nDerived metrics:")
print(data.get("derived"))
