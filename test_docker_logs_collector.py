from backend.collectors.docker_logs import DockerLogsCollector


collector = DockerLogsCollector()

result = collector.collect(
    container_id="infrasage-test",
    tail=100
)

print("SOURCE:", result["source"])
print("SUCCESS:", result["success"])

if result["success"]:
    print("\nCONTAINER:")
    print(result["container"])

    print("\nSTDOUT:")
    print(result["logs"]["stdout"])

    print("\nSTDERR:")
    print(result["logs"]["stderr"])

    print("\nLINE COUNTS:")
    print(result["line_counts"])

else:
    print("ERROR:", result["error"])