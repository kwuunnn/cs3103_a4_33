import socket, threading, struct, time, logging
from typing import Callable

# Header formats
DATA_HDR_FMT = "!B H I"  # ChannelType (1), SeqNo (2), TimestampMs (4)
DATA_HDR_LEN = struct.calcsize(DATA_HDR_FMT)

ACK_HDR_FMT = "!B B H I"  # ChannelType(1), ACK_FLAG(1), SeqNo(2), TimestampMs(4)
ACK_HDR_LEN = struct.calcsize(ACK_HDR_FMT)
ACK_FLAG = 0xFF

# Channel types
CHANNEL_RELIABLE = 0
CHANNEL_UNRELIABLE = 1
CHANNEL_REGISTER = 2
CHANNEL_DEREGISTER = 3  # deregistration channel

# Default params
DEFAULT_SKIP_MS = 200
RETX_INTERVAL_MS = 50  # retransmit every 50ms until ack or skip

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def now_ms():
    return int(time.time() * 1000) & 0xFFFFFFFF


class GameNetAPI:
    def __init__(
        self,
        local_addr=("0.0.0.0", 10000),
        peer_addr=None,
        on_receive: Callable = None,
        skip_threshold_ms=DEFAULT_SKIP_MS,
        max_buffered=1024,
    ):
        """
        on_receive(channel, seq, timestamp_ms, payload_bytes)
        """
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(local_addr)
        self.peer_addr = peer_addr
        self.on_receive = on_receive or (lambda *args, **kw: None)
        self.skip_threshold_ms = skip_threshold_ms
        self.seq_lock = threading.Lock()
        self.next_seq_reliable = 0
        self.next_seq_unreliable = 0

        # Sender reliable buffer
        self.sent_buffer = {}
        self.sent_lock = threading.Lock()

        # Receiver sessions (per sender)
        self.sessions = {}
        self.registered_peers = set()

        self.max_buffered = max_buffered

        # Metrics
        self.metrics = {
            "sent_reliable": 0,
            "sent_unreliable": 0,
            "recv_reliable": 0,
            "recv_unreliable": 0,
            "acks_received": 0,
            "retransmissions": 0,
            "lost_marked": 0,
            "registrations": 0,
        }

        self.running = True
        self.receiver_thread = threading.Thread(target=self._recv_loop, daemon=True)
        self.retrans_thread = threading.Thread(target=self._retrans_loop, daemon=True)
        self.receiver_thread.start()
        self.retrans_thread.start()

    # --------------------------
    # Registration
    # --------------------------
    def register_peer(self, addr):
        """Manually register a known peer (sender/receiver handshake)."""
        self.registered_peers.add(addr)
        logging.info(f"[REGISTER] Added peer {addr}")
        # Send a small registration packet
        pkt = struct.pack(DATA_HDR_FMT, CHANNEL_REGISTER, 0, now_ms())
        self.sock.sendto(pkt, addr)

    # --------------------------
    # Sending
    # --------------------------
    def send(self, payload: bytes, reliable: bool = True):
        if self.peer_addr is None:
            raise RuntimeError("peer_addr not set")

        if self.peer_addr not in self.registered_peers:
            logging.warning(f"[SEND BLOCKED] {self.peer_addr} not registered yet")
            return None

        if reliable:
            with self.seq_lock:
                seq = self.next_seq_reliable
                self.next_seq_reliable = (self.next_seq_reliable + 1) & 0xFFFF
            channel = CHANNEL_RELIABLE
        else:
            with self.seq_lock:
                seq = self.next_seq_unreliable
                self.next_seq_unreliable = (self.next_seq_unreliable + 1) & 0xFFFF
            channel = CHANNEL_UNRELIABLE

        timestamp_ms = now_ms()
        pkt = self._pack_data(channel, seq, timestamp_ms, payload)

        # Send datagram
        self.sock.sendto(pkt, self.peer_addr)

        if reliable:
            self.metrics["sent_reliable"] += 1
            with self.sent_lock:
                self.sent_buffer[seq] = {
                    "pkt": pkt,
                    "first_send_ms": timestamp_ms,
                    "last_send_ms": timestamp_ms,
                    "acked": False,
                    "retrans_count": 0,
                }
        else:
            self.metrics["sent_unreliable"] += 1

        logging.debug(f"sent seq={seq} chan={'R' if reliable else 'U'} len={len(payload)}")
        return seq

    def _pack_data(self, channel, seq, timestamp_ms, payload: bytes):
        hdr = struct.pack(DATA_HDR_FMT, channel, seq & 0xFFFF, timestamp_ms & 0xFFFFFFFF)
        return hdr + payload

    def _unpack_data(self, data: bytes):
        if len(data) < DATA_HDR_LEN:
            return None
        hdr = data[:DATA_HDR_LEN]
        channel, seq, timestamp = struct.unpack(DATA_HDR_FMT, hdr)
        payload = data[DATA_HDR_LEN:]
        return channel, seq, timestamp, payload

    def _pack_ack(self, seq, timestamp_ms):
        return struct.pack(
            ACK_HDR_FMT,
            CHANNEL_RELIABLE,
            ACK_FLAG,
            seq & 0xFFFF,
            timestamp_ms & 0xFFFFFFFF,
        )

    def _unpack_ack(self, data: bytes):
        if len(data) < ACK_HDR_LEN:
            return None
        ch, flag, seq, timestamp = struct.unpack(ACK_HDR_FMT, data[:ACK_HDR_LEN])
        if flag != ACK_FLAG:
            return None
        return seq, timestamp

    # --------------------------
    # Receiving
    # --------------------------
    def _recv_loop(self):
        self.sock.settimeout(0.5)
        while self.running:
            try:
                data, addr = self.sock.recvfrom(65536)
            except socket.timeout:
                continue
            except ConnectionResetError:
                logging.warning("Ignored ICMP Port Unreachable")
                continue

            # Handle ACKs
            ack_unpacked = self._unpack_ack(data)
            if ack_unpacked is not None:
                seq, ts = ack_unpacked
                self._handle_ack(seq, ts)
                continue

            parsed = self._unpack_data(data)
            if parsed is None:
                continue
            channel, seq, ts, payload = parsed
            arrival_ms = now_ms()

            # Handle registration packet
            if channel == CHANNEL_REGISTER:
                if addr not in self.registered_peers:
                    self.registered_peers.add(addr)
                    self.metrics["registrations"] += 1
                    logging.info(f"[REGISTERED] New peer {addr}")
                continue

            # Handle deregistration packet
            if channel == CHANNEL_DEREGISTER:
                if addr in self.sessions:
                    del self.sessions[addr]
                    logging.info(f"[DEREGISTERED] Session removed for {addr}")
                if addr in self.registered_peers:
                    self.registered_peers.remove(addr)
                continue

            # Drop if unregistered
            if addr not in self.registered_peers:
                logging.warning(f"[DROP] Ignored unregistered packet from {addr}")
                continue

            # Handle unreliable packets
            if channel == CHANNEL_UNRELIABLE:
                self.metrics["recv_unreliable"] += 1
                rtt = arrival_ms - ts
                logging.info(f"[RECV U] {addr} seq={seq} rtt={rtt}ms")
                try:
                    self.on_receive(CHANNEL_UNRELIABLE, seq, ts, payload)
                except Exception:
                    logging.exception("on_receive failed")
                continue

            # Reliable channel
            sess = self.sessions.get(addr)
            if not sess:
                sess = {
                    "recv_next_expected": 0,
                    "recv_buffer": {},
                    "last_seen": arrival_ms,
                }
                self.sessions[addr] = sess
                logging.info(f"[SESSION] New session created for {addr}")

            sess["last_seen"] = arrival_ms

            # Send ACK
            ackpkt = self._pack_ack(seq, ts)
            self.sock.sendto(ackpkt, addr)

            recv_buf = sess["recv_buffer"]
            if len(recv_buf) < self.max_buffered:
                recv_buf[seq] = (ts, payload, arrival_ms)

            self._deliver_in_order_locked(sess)

            logging.info(
                f"[RECV R] {addr} seq={seq} ts={ts} arrival={arrival_ms} buffered={len(recv_buf)}"
            )

    def _handle_ack(self, seq, ts):
        now = now_ms()
        with self.sent_lock:
            info = self.sent_buffer.get(seq)
            if info:
                info["acked"] = True
                rtt = now - info["first_send_ms"]
                self.metrics["acks_received"] += 1
                logging.info(f"[ACK] seq={seq} rtt_ms={rtt} retrans={info['retrans_count']}")
                del self.sent_buffer[seq]

    def _deliver_in_order_locked(self, sess):
        progressed = True
        while progressed:
            progressed = False
            expected = sess["recv_next_expected"]
            recv_buf = sess["recv_buffer"]
            info = recv_buf.get(expected)
            if info:
                ts, payload, arrival_ms = info
                self.metrics["recv_reliable"] += 1
                try:
                    self.on_receive(CHANNEL_RELIABLE, expected, ts, payload)
                except Exception:
                    logging.exception("on_receive callback error")
                del recv_buf[expected]
                sess["recv_next_expected"] = (expected + 1) & 0xFFFF
                progressed = True
            else:
                if recv_buf:
                    earliest_seq = sorted(recv_buf.keys())[0]
                    earliest_info = recv_buf[earliest_seq]
                    arrival_time = earliest_info[2]
                    if now_ms() - arrival_time >= self.skip_threshold_ms:
                        logging.warning(
                            f"[SKIP R] missing seq={expected}, skipping after {now_ms()-arrival_time}ms"
                        )
                        sess["recv_next_expected"] = (expected + 1) & 0xFFFF
                        self.metrics["lost_marked"] += 1
                        progressed = True

    def _retrans_loop(self):
        while self.running:
            now = now_ms()
            with self.sent_lock:
                to_remove = []
                for seq, info in list(self.sent_buffer.items()):
                    if info["acked"]:
                        to_remove.append(seq)
                        continue
                    age = now - info["first_send_ms"]
                    if age >= self.skip_threshold_ms:
                        logging.warning(
                            f"[SENDER SKIP] seq={seq} exceeded skip threshold {age} ms -> drop"
                        )
                        to_remove.append(seq)
                        self.metrics["lost_marked"] += 1
                        continue
                    if now - info["last_send_ms"] >= RETX_INTERVAL_MS:
                        try:
                            self.sock.sendto(info["pkt"], self.peer_addr)
                            info["last_send_ms"] = now
                            info["retrans_count"] += 1
                            self.metrics["retransmissions"] += 1
                            logging.info(f"[RETX] seq={seq} count={info['retrans_count']}")
                        except Exception:
                            logging.exception("retransmit failed")
                for s in to_remove:
                    if s in self.sent_buffer:
                        del self.sent_buffer[s]
            time.sleep(RETX_INTERVAL_MS / 1000.0)

    def stop(self):
        # If sender, send deregister
        if self.peer_addr:
            try:
                pkt = struct.pack(DATA_HDR_FMT, CHANNEL_DEREGISTER, 0, now_ms())
                self.sock.sendto(pkt, self.peer_addr)
                logging.info(f"[DEREGISTER] Sent deregister to {self.peer_addr}")
            except Exception:
                logging.exception("Failed to send deregister packet")

        # Stop threads
        self.running = False
        self.receiver_thread.join(timeout=1.0)
        self.retrans_thread.join(timeout=1.0)
        self.sock.close()

        # Clear sessions if receiver
        if not self.peer_addr:
            removed = len(self.sessions)
            self.sessions.clear()
            logging.info(f"[SESSION] Cleared {removed} sessions")

    def get_metrics(self):
        return self.metrics.copy()
