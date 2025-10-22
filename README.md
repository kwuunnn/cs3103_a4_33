# H-UDP Demo (Hybrid UDP transport)

This repository contains a small Python demo that implements a hybrid transport layer protocol (H-UDP) over UDP. It provides both reliable and unreliable logical channels on top of a single UDP socket and demonstrates retransmission, buffering, and in-order delivery for reliable packets.

Files

- `hudp.py` - The H-UDP implementation exposing `GameNetAPI` (send/receive, metrics).
- `sender.py` - Simple sender that randomly marks outgoing packets as reliable/unreliable and sends to the receiver.
- `receiver.py` - Simple receiver that prints incoming packets and RTT for reliable packets.

Requirements

- Python 3.8+ (Windows, Linux, macOS).

Quick test (local loopback)

1. Open two terminals.
2. In terminal A, run the receiver (binds to UDP port 10000):

```bash
python receiver.py
```

3. In terminal B, run the sender (binds to local port 10001 and sends to 10000):

```bash
python sender.py
```

You should see logs in both terminals showing sent packets, receives, ACKs, retransmissions, and final metrics.

Tuning parameters

- `DEFAULT_SKIP_MS` in `hudp.py` controls the skip threshold t (default 200 ms). Change this if you want to use a different threshold for experiments.
- `RETX_INTERVAL_MS` controls the retransmission interval (default 50 ms).
- `sender.py` has `packet_rate` and `duration` variables to change packet sending rate and test length.

Interpreting logs/metrics

- Sender prints whether each packet was sent on Reliable (R) or Unreliable (U) channel along with sequence number and payload.
- Receiver prints incoming packets and RTT for reliable packets (calculated as now - timestamp included in packet).
- At shutdown both sender and receiver print a `Metrics` dictionary that includes counts for sent/received reliable/unreliable packets, retransmissions, acks received, and lost packets marked.

Simulating network conditions

- Windows: use "Clumsy" (https://jagt.github.io/clumsy/) to add packet loss, delay, duplication, or reorder on the loopback interface.
- Linux: use `tc`/`netem` to add delay/loss/jitter, e.g.:

```bash
# add 100ms delay with 20ms jitter on interface eth0 (run as root)
sudo tc qdisc add dev eth0 root netem delay 100ms 20ms
# add 10% packet loss
sudo tc qdisc change dev eth0 root netem loss 10%
# remove netem
sudo tc qdisc del dev eth0 root netem
```

Note: When testing locally on loopback (`127.0.0.1`), some emulators may not affect loopback traffic. If so, run sender and receiver on two local virtual interfaces or use a small emulator program (see below).

Optional: small network emulator
If you cannot use Clumsy/tc, you can insert a small Python-based emulator between sender and receiver. The repo does not include it by default, but it is straightforward to write a UDP forwarder that drops or delays packets randomly.

Recommended experimental settings (for reproducibility)

- Test duration: 30–60 seconds per run.
- Packet rate: 10–100 packets/s.
- Network scenarios: (i) Low loss: loss < 2%, small delay (10–30 ms). (ii) High loss: loss >= 10%, varied delay/jitter.

What to report

- Per-channel metrics (reliable vs unreliable): packet delivery ratio, average RTT/one-way latency, jitter, throughput.
- Show sample logs and plots comparing reliability vs latency trade-offs under the different conditions.

If you'd like, I can:

- Add a tiny network emulator script to this repo.
- Add a small test harness that runs both sender and receiver and collects metrics into CSV for plotting.
- Adjust `hudp.py` to print more verbose debug logs or write logs to a file.

Which of the above would you like me to do next?
