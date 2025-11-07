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

4. A `.csv` file will be generated in the current directory containing the performance metrics. You can use this data for further analysis or plotting.

Plotting results

1. Open a terminal.
2. Run the plotting script to generate graphs from the `.csv` data:

```bash
python plot_analysis.py
```

Tuning parameters

- `DEFAULT_SKIP_MS` in `hudp.py` controls the skip threshold t (default 200 ms). Change this if you want to use a different threshold for experiments.
- `RETX_INTERVAL_MS` controls the retransmission interval (default 50 ms).
- `sender.py` has `packet_rate` and `duration` variables to change packet sending rate and test length.

Interpreting logs/metrics

- Sender prints whether each packet was sent on Reliable (R) or Unreliable (U) channel along with sequence number and payload.
- Receiver prints incoming packets and RTT for reliable packets (calculated as now - timestamp included in packet).
- At shutdown both sender and receiver print a `metrics` dictionary that includes counts for sent/received reliable/unreliable packets, retransmissions, acks received, and lost packets marked.

Simulating network conditions

- Windows: use "Clumsy" (https://jagt.github.io/clumsy/) to add packet loss, delay, duplication, or reorder on the loopback interface.

Technical Report: [Link](https://docs.google.com/document/d/1nL475hQj0SV9WIocU3CHAzUm2NM5N2jgp7wVzrusTTY/edit?tab=t.0)
