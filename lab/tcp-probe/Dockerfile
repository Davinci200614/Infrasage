FROM python:3.12-alpine
ARG PROBE_SHA
LABEL org.infrasage.tcp-probe="v1"
LABEL org.infrasage.tcp-probe.sha256="${PROBE_SHA}"
COPY backend/collectors/tcp_probe_helper.py /opt/infrasage-tcp-probe.py
USER 65534:65534
ENTRYPOINT ["python3", "-I", "/opt/infrasage-tcp-probe.py"]
