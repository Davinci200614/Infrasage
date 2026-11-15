from backend.collectors.docker import DockerCollector

collector = DockerCollector()

containers = collector.collect()

for container in containers:
    print("\n------------------------------")
    print("Nom :", container["name"])
    print("CPU :", container["cpu_percent"], "%")
    print("RAM utilisée :", container["memory_usage_mb"], "MB")
    print("RAM limite :", container["memory_limit_mb"], "MB")
    print("Statut :", container["status"])

