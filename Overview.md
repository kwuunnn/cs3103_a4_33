Overview — H-UDP demo

This file contains a beginner-friendly explanation of the assignment and a detailed walkthrough of the code in this repository (`hudp.py`, `sender.py`, `receiver.py`). If you're new to networking or Python, read this file first.

1. Assignment in plain English

- Objective: Implement a hybrid transport protocol (H-UDP) on top of UDP that supports two logical channels:

  - Reliable channel: ensures in-order delivery using acknowledgements (ACKs), retransmission and buffering/reordering.
  - Unreliable channel: sends packets without ACKs or buffering (fire-and-forget).

- Why this matters: Game networking often needs both low-latency unreliable updates (e.g., player movement) and reliable delivery for important events (e.g., inventory changes). The assignment asks you to implement an API that lets application code choose which channel to use per-packet.

- Key requirements:
  - Packet header must include: ChannelType (1 byte), SeqNo (2 bytes), Timestamp (4 bytes), followed by payload bytes.
  - Sender API (GameNetAPI) must let application mark packets as reliable or unreliable.
  - Receiver must buffer and reorder reliable packets; deliver reliable packets in order.
  - Retransmit reliable packets until ACK or until a skip timeout t (default 200 ms), after which the receiver should skip the missing packet and continue.
  - Measure basic metrics (sent/received counts per channel, retransmissions, acks, marked loss, etc.).
  - Provide sample sender and receiver applications and a way to simulate network loss/delay/jitter for experiments.

2. Files in this repo

- `hudp.py` — core H-UDP implementation with `GameNetAPI`.
- `sender.py` — demo sender app that randomly marks packets reliable/unreliable and sends them to the receiver.
- `receiver.py` — demo receiver app that uses `GameNetAPI` and prints out receives and RTTs.
- `README.md` — instructions to run and experiment (quick start and emulator notes).

3. Design and header format

Each data packet uses the header layout below (packed in network byte order/big-endian):

| ChannelType (1 byte) | SeqNo (2 bytes) | TimestampMs (4 bytes) | Payload bytes... |

- ChannelType: 0 means reliable, 1 means unreliable.
- SeqNo: 16-bit sequence number (wraps at 65535 -> 0).
- TimestampMs: 32-bit timestamp in milliseconds (sender sets it when sending) — used to compute RTT or one-way delay at receiver.

ACK packets use a short header that includes an ACK flag and the seq/timestamp.

4. Walkthrough: hudp.py (core implementation)

Important constants and utilities

- DATA_HDR_FMT = "!B H I"
  - Format for struct.pack/unpack for data header: 1-byte unsigned, 2-byte unsigned, 4-byte unsigned (big-endian).
- ACK_HDR_FMT = "!B B H I" — ACK adds a one-byte ACK_FLAG after channel byte.
- CHANNEL_RELIABLE = 0, CHANNEL_UNRELIABLE = 1
- DEFAULT_SKIP_MS = 200 — t threshold to skip missing packets.
- RETX_INTERVAL_MS = 50 — retransmit interval.
- now_ms() returns int(time.time() \* 1000) — current time in milliseconds.

GameNetAPI.**init**

- Binds a UDP socket to `local_addr` and starts two threads:
  - receiver_thread -> \_recv_loop: reads incoming datagrams (ACKs and data) and handles them.
  - retrans_thread -> \_retrans_loop: periodically retransmits un-acked reliable packets.
- Initializes these important structures:
  - `next_seq` and `seq_lock`: safe sequence number assignment for outgoing packets.
  - `sent_buffer`: dictionary mapping sequence number to packet metadata (for retransmit and ack handling).
  - `recv_buffer`: for buffering out-of-order reliable packets, keyed by seq number.
  - `recv_next_expected`: next sequence number expected for reliable in-order delivery.
  - `metrics`: counters for sent/received/retransmissions/etc.

Packet packing/unpacking

- \_pack_data(channel, seq, timestamp_ms, payload) -> header+payload bytes.
- \_unpack_data(data) -> (channel, seq, timestamp, payload) or None if too short.
- \_pack_ack(seq, timestamp) and \_unpack_ack(data): pack/unpack ACK packets.

send(payload: bytes, reliable: bool=True)

- Assigns a sequence number under `seq_lock` and stamps the packet with timestamp.
- Packs header and payload and sends via `sock.sendto()` to `peer_addr`.
- If reliable:
  - Stores an entry in `sent_buffer` with: pkt (bytes), first_send time, last_send time, acked flag, retrans_count.
  - Increments metrics["sent_reliable"].
- If unreliable: increments metrics["sent_unreliable"].
- Returns the sequence number.

\_recv_loop() — receiver thread

- Calls `recvfrom()` in a loop with a short timeout so the thread can exit cleanly.
- If incoming packet looks like an ACK (checked by \_unpack_ack), calls \_handle_ack(seq, ts).
- Otherwise parses as data.
- If channel is UNRELIABLE:
  - Immediately calls `on_receive(CHANNEL_UNRELIABLE, seq, ts, payload)` — no reordering.
- If channel is RELIABLE:
  - Sends an ACK back to the sender immediately using \_pack_ack(seq, ts) and sock.sendto.
  - Buffers (ts, payload, arrival) in `recv_buffer` keyed by seq (if buffer not full).
  - Calls \_deliver_in_order_locked() to attempt in-order delivery immediately.

\_handle_ack(seq, ts)

- Finds the sent_buffer entry for seq (if exists), marks it acked and records ack_time.
- Computes rtt = ack_time - first_send and logs it.
- Removes the entry from sent_buffer and increments metrics["acks_received"].

\_deliver_in_order_locked()

- While true:
  - If the `recv_buffer` contains the packet for `recv_next_expected`, deliver it to application via `on_receive(CHANNEL_RELIABLE, seq, ts, payload)` and advance `recv_next_expected`.
  - Else: if `recv_buffer` not empty, examine the earliest stored packet's arrival time. If the earliest buffered packet arrived more than `skip_threshold_ms` ago, assume the missing `recv_next_expected` is lost; increment `recv_next_expected` to skip it and increment lost counter. The loop repeats to attempt delivery.
- This ensures receiver won't block forever waiting for missing packets: it skips after t ms as required.

\_retrans_loop()

- Periodically examines `sent_buffer` and retransmits packets that are not acked and whose `last_send` is older than RETX_INTERVAL_MS.
- If an entry's age (now - first_send) exceeds skip_threshold_ms, it is considered permanently lost and removed from the buffer.
- When retransmitting, the code calls `sock.sendto(info['pkt'], self.peer_addr)` and updates last_send and retrans_count and metrics.

stop() and get_metrics()

- stop(): sets running False, joins threads (with timeout), closes the socket.
- get_metrics(): returns a copy of the metrics dict.

Key behavior summary

- Sender: stores reliable packets in `sent_buffer` and retransmits them until acked or until skip timeout.
- Receiver: acknowledges reliable packets, buffers out-of-order reliable packets, attempts in-order delivery, and skips missing packets after t ms.
- This is similar to a selective repeat ARQ design with application-level skipping.

5. Walkthrough: sender.py (example run script)

- Creates GameNetAPI bound to port 10001 and with peer at 127.0.0.1:10000.
- Sends `packet_rate * duration` packets. For each packet:
  - Builds payload as a small dict and uses `str(payload).encode()` to convert to bytes.
  - Picks reliable randomly (True/False).
  - Calls `sender.send(payload, reliable=reliable)` and prints a line showing whether it was sent on R or U.
- Waits 2 seconds for retransmissions and ACKs, then stops and prints metrics.

6. Walkthrough: receiver.py (example run script)

- Defines `on_receive(channel, seq, ts, payload)` callback that prints the received packet and computes `rtt = now - ts` for reliable packets.
- Starts GameNetAPI bound to port 10000 with that callback.
- Runs an infinite loop until Ctrl+C, then stops and prints metrics.

7. Running locally (quick start)

- Terminal A: `python receiver.py`
- Terminal B: `python sender.py`

8. Common beginner questions and debugging tips

- "Why are ACKs needed?": If you want reliability on top of UDP (which doesn't provide it), the receiver must tell the sender which packets arrived so the sender can stop retransmitting.
- "Why retransmit often?": The RETX interval is small (50 ms) for responsiveness. You can increase it to reduce bandwidth.
- "Why skip after t ms?": In real-time games you prefer to move forward rather than block waiting for an old packet forever.
- If you hit socket address already in use: Make sure no other process is using that port. Try changing the port numbers or close the other process.
- If UDP packets are not being received: Check firewall rules, ensure IP/port are correct, and that `peer_addr` is set on the sender.

9. Where to change parameters for experiments

- `DEFAULT_SKIP_MS` inside `hudp.py` (default 200 ms). This is the t threshold.
- `RETX_INTERVAL_MS` inside `hudp.py` (default 50 ms).
- `packet_rate` and `duration` inside `sender.py`.
- `max_buffered` argument to the `GameNetAPI` constructor.

10. Suggestions for next steps

- Add JSON serialization for payloads instead of `str(...)`.
- Add a small Python network emulator (forwarder with delay/loss/jitter) if your OS tools don't affect loopback traffic.
- Add a experiments harness that runs sender+receiver and writes metrics into CSV files for plotting.
- Improve metrics: compute average RTT, jitter using RFC3550 formulas, throughput, and packet delivery ratio per channel.

If you'd like, I will:

- Add `Overview.md` (done) and optionally add a small python emulator script or update the demo to use JSON and produce CSV metrics.

---

Created: Overview.md — contains a beginner-friendly explanation of the assignment and a full line-by-line walkthrough of the code in this repo.
