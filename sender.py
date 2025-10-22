import time
import random
from hudp import GameNetAPI, CHANNEL_RELIABLE, CHANNEL_UNRELIABLE

# Sender binds to different local port and sends to receiver port 10000
sender = GameNetAPI(local_addr=("127.0.0.1", 10001), peer_addr=("127.0.0.1", 10000))

packet_rate = 5  # packets per second → slower
duration = 5  # seconds
total_packets = packet_rate * duration

for i in range(total_packets):
    payload = {"id": i, "pos": [random.randint(0, 100), random.randint(0, 100)]}
    reliable = random.choice([True, False])
    # reliable = False
    seq = sender.send(str(payload).encode(), reliable=reliable)
    ch_str = "R" if reliable else "U"
    print(f"[SEND {ch_str}] seq={seq} payload={payload}")
    time.sleep(1 / packet_rate)  # 0.2 sec per packet at 5 packets/sec

time.sleep(2)  # wait for retransmissions/acks
sender.stop()
print("Sender stopped")
print("Metrics:", sender.get_metrics())
