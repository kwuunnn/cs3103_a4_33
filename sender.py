# sender.py
import sys, time, random
from hudp import GameNetAPI

def sample_on_receive(channel, seq, ts, payload):
    # sender might also receive ACKs via API logs; we don't need to handle here
    pass

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("usage: python sender.py <local_port> <peer_ip:peer_port> [rate_pps]")
        sys.exit(1)
    local_port = int(sys.argv[1])
    peer = sys.argv[2]
    rate = float(sys.argv[3]) if len(sys.argv) >= 4 else 50.0

    peer_ip, peer_port = peer.split(":")
    api = GameNetAPI(local_addr=("0.0.0.0", local_port), peer_addr=(peer_ip, int(peer_port)),
                     on_receive=sample_on_receive)
    print("sender running. sending to", peer)
    try:
        seq = 0
        interval = 1.0 / rate
        while True:
            # prepare payload (simple JSON snippet or short string)
            reliable = (random.random() < 0.5)  # 50% reliable
            payload = f"msg {seq} reliable={reliable}".encode("utf-8")
            api.send(payload, reliable=reliable)
            seq += 1
            time.sleep(interval)
    except KeyboardInterrupt:
        print("stopping sender...")
        api.stop()
        print("metrics:", api.get_metrics())
