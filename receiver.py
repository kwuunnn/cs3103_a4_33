# receiver.py
import sys, time, csv
from hudp import GameNetAPI, CHANNEL_RELIABLE, CHANNEL_UNRELIABLE

LOG_CSV = "recv_log.csv"


def on_receive(channel, seq, ts, payload):
    arrival = int(time.time() * 1000)
    if channel == CHANNEL_RELIABLE:
        print(f"[APP R] seq={seq} ts={ts} arrival={arrival} payload={payload.decode()}")
    else:
        print(f"[APP U] seq={seq} ts={ts} arrival={arrival} payload={payload.decode()}")
    # you can also append to CSV for later analysis
    with open(LOG_CSV, "a", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([arrival, channel, seq, ts, payload.decode()])


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python receiver.py <local_port>")
        sys.exit(1)
    local_port = int(sys.argv[1])
    api = GameNetAPI(
        local_addr=("0.0.0.0", local_port), peer_addr=None, on_receive=on_receive
    )
    print("receiver listening on port", local_port)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("stopping receiver...")
        api.stop()
        print("metrics:", api.get_metrics())
