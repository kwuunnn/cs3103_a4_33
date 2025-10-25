import time
from hudp import GameNetAPI, CHANNEL_RELIABLE, CHANNEL_UNRELIABLE


def on_receive(channel, seq, ts, payload):
    now = int(time.time() * 1000)
    rtt = now - ts if channel == CHANNEL_RELIABLE else None
    ch_str = "R" if channel == CHANNEL_RELIABLE else "U"
    print(f"[RECV {ch_str}] seq={seq} ts={ts} payload={payload} rtt={rtt}ms")


# Receiver binds to port 10000
receiver = GameNetAPI(local_addr=("127.0.0.1", 10001), on_receive=on_receive)

print("Receiver running... press Ctrl+C to stop")
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    receiver.stop()
    print("Receiver stopped")
    print("Metrics:", receiver.get_metrics())
