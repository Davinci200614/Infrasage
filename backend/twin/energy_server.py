"""Compatibility entry point for the shared energy server."""
from backend.energy.meter import EnergyServer, run_server

if __name__ == '__main__':
    import signal
    import threading
    server = run_server()
    stopped = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    stopped.wait()
    server.stop()
