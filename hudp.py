import socket, threading, struct, time, logging, random
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
CHANNEL_DEREGISTER = 3

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
            "reliable_acks_recv": 0,
            "retransmissions": 0,
            "lost_marked": 0,
            # Registration-specific metrics
            "sent_reg": 0,           # number of registration packets sent
            "recv_reg": 0,           # number of registration packets received (including retransmissions)
            "reg_acks_recv": 0,      # number of registration ACKs received by sender
            "registrations": 0,      # actual successful registrations
        }

        self.running = True
        self.receiver_thread = threading.Thread(target=self._recv_loop, daemon=True)
        self.retrans_thread = threading.Thread(target=self._retrans_loop, daemon=True)
        self.receiver_thread.start()
        self.retrans_thread.start()

    # --------------------------
    # Registration (Sender-side)
    # --------------------------
    def register_peer(self, addr, timeout_ms=5000):
        # Random init seq for reliable channel
        init_seq = random.randint(0, 0xFFFF)
        with self.seq_lock:
            self.next_seq_reliable = (init_seq + 1) & 0xFFFF
        logging.info(f"[REGISTER] init seq={init_seq}, next_seq_reliable={self.next_seq_reliable}")

        timestamp_ms = now_ms()
        pkt = struct.pack(DATA_HDR_FMT, CHANNEL_REGISTER, init_seq, timestamp_ms)
        ev = threading.Event()

        # Track registration in sent buffer (keyed by seq)
        with self.sent_lock:
            self.sent_buffer[init_seq] = {
                "pkt": pkt,
                "first_send_ms": timestamp_ms,
                "last_send_ms": timestamp_ms,
                "acked": False,
                "retrans_count": 0,
                "is_registration": True,
                "event": ev,
                "addr": addr,  # track exact destination
            }

        self.sock.sendto(pkt, addr)
        self.metrics["sent_reg"] += 1
        logging.info(f"[REGISTER] Sent registration to {addr} seq={init_seq}")

        # Wait for ACK
        waited = ev.wait(timeout_ms / 1000.0)

        # Clean up
        with self.sent_lock:
            info = self.sent_buffer.pop(init_seq, None)

        if waited:
            self.registered_peers.add(addr)
            self.metrics["registrations"] += 1
            logging.info(f"[REGISTER COMPLETE] Peer {addr} successfully registered")
        else:
            logging.warning(f"[REGISTER TIMEOUT] No ACK from {addr}, unreliable packets still allowed")             


    # --------------------------
    # Sending
    # --------------------------
    def send(self, payload: bytes, reliable: int = 1):
        if self.peer_addr is None:
            raise RuntimeError("peer_addr not set")

        if reliable == CHANNEL_RELIABLE and self.peer_addr not in self.registered_peers:
            logging.warning(f"[SEND BLOCKED] Reliable send requires registration for {self.peer_addr}")
            return None

        with self.seq_lock:
            if reliable == CHANNEL_RELIABLE:
                seq = self.next_seq_reliable
                self.next_seq_reliable = (self.next_seq_reliable + 1) & 0xFFFF
                channel = CHANNEL_RELIABLE
            else:
                seq = self.next_seq_unreliable
                self.next_seq_unreliable = (self.next_seq_unreliable + 1) & 0xFFFF
                channel = CHANNEL_UNRELIABLE

        timestamp_ms = now_ms()
        pkt = self._pack_data(channel, seq, timestamp_ms, payload)
        self.sock.sendto(pkt, self.peer_addr)

        if reliable == CHANNEL_RELIABLE:
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
        return seq, timestamp_ms

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
        return struct.pack(ACK_HDR_FMT, CHANNEL_RELIABLE, ACK_FLAG, seq & 0xFFFF, timestamp_ms & 0xFFFFFFFF)

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
                # ignore ICMP "port unreachable" errors on Windows
                continue

            # --- Handle ACKs ---
            ack_unpacked = self._unpack_ack(data)
            if ack_unpacked is not None:
                seq, ts = ack_unpacked
                self._handle_ack(seq, ts)
                continue

            # --- Parse Data Packet ---
            parsed = self._unpack_data(data)
            if parsed is None:
                continue

            channel, seq, ts, payload = parsed
            arrival_ms = now_ms()

            # --- Channel-specific handling ---
            if channel == CHANNEL_REGISTER:
                # Registration handshake (reliable setup)
                self._handle_registration(addr, seq, ts)
                continue

            elif channel == CHANNEL_DEREGISTER:
                # Peer tear-down
                self._handle_deregistration(addr, seq, ts)
                continue

            elif channel == CHANNEL_UNRELIABLE:
                # Always accept, even if unregistered
                self.metrics["recv_unreliable"] += 1
                try:
                    self.on_receive(CHANNEL_UNRELIABLE, seq, ts, payload)
                except Exception:
                    logging.exception("on_receive failed (unreliable)")
                continue

            elif channel == CHANNEL_RELIABLE:
                # Drop if peer not registered
                if addr not in self.registered_peers:
                    logging.warning(f"[DROP] Ignored reliable packet from unregistered peer {addr}")
                    continue

                # Handle reliable data packet
                self._handle_reliable(addr, seq, ts, payload, arrival_ms)
                continue

            # --- Unknown channel ---
            else:
                logging.error(f"[INVALID] Unknown channel {channel} from {addr}")
                self.metrics.setdefault("invalid_packets", 0)
                self.metrics["invalid_packets"] += 1
                continue


    # --------------------------
    # Registration (Receiver-side)
    # --------------------------
    def _handle_registration(self, addr, seq, ts):
        self.metrics["recv_reg"] += 1

        # Ensure session exists
        sess = self.sessions.get(addr)
        if not sess:
            sess = {
                "recv_next_expected": (seq + 1) & 0xFFFF,
                "recv_buffer": {},
                "last_seen": now_ms(),
            }
            self.sessions[addr] = sess
        else:
            sess["last_seen"] = now_ms()

        # Add peer to registered_peers if not already
        if addr not in self.registered_peers:
            self.registered_peers.add(addr)
            self.metrics["registrations"] += 1
            logging.info(f"[REGISTERED] Peer {addr} added to registered_peers")

        # Always send ACK
        ackpkt = self._pack_ack(seq, ts)
        try:
            self.sock.sendto(ackpkt, addr)
            logging.info(f"[REGISTER ACK SENT] seq={seq} to {addr}")
        except Exception:
            logging.exception("failed to send ACK for registration")


    def _handle_deregistration(self, addr, seq, ts):
        self.sessions.pop(addr, None)
        self.registered_peers.discard(addr)
        logging.info(f"[DEREGISTERED] {addr}")

        ackpkt = self._pack_ack(seq, ts)
        try:
            self.sock.sendto(ackpkt, addr)
        except Exception:
            logging.exception("failed to send ACK for deregister")

    def _handle_reliable(self, addr, seq, ts, payload, arrival_ms):
        sess = self.sessions.get(addr)
        if not sess:
            sess = {"recv_next_expected": (seq + 1) & 0xFFFF, "recv_buffer": {}, "last_seen": arrival_ms}
            self.sessions[addr] = sess
            logging.info(f"[SESSION] Created session for {addr} on-the-fly")

        sess["last_seen"] = arrival_ms

        # Send ACK
        ackpkt = self._pack_ack(seq, ts)
        try:
            self.sock.sendto(ackpkt, addr)
        except Exception:
            logging.exception("failed to send ACK")

        recv_buf = sess["recv_buffer"]
        expected = sess["recv_next_expected"]
        dist_forward = (seq - expected) & 0xFFFF
        if dist_forward < self.max_buffered:
            recv_buf[seq] = (ts, payload, arrival_ms)
        else:
            logging.debug(f"[BUFFER IGNORE] {addr} seq={seq} dist_forward={dist_forward}")

        self._deliver_in_order_locked(sess)
        logging.info(f"[RECV R] {addr} seq={seq} buffered={len(recv_buf)}")

    # --------------------------
    # ACK Handling & Delivery
    # --------------------------
    def _handle_ack(self, seq, ts):
        now = now_ms()
        with self.sent_lock:
            info = self.sent_buffer.get(seq)
            if not info:
                return
            info["acked"] = True
            ev = info.get("event")
            if ev and isinstance(ev, threading.Event):
                try:
                    ev.set()
                except Exception:
                    logging.exception("failed to set ack event")

            rtt = now - info["first_send_ms"]

            # Separate registration ACKs vs normal reliable ACKs
            if info.get("is_registration"):
                logging.info(f"[REGISTER ACK] seq={seq} RTT={rtt}ms")
                self.metrics["reg_acks_recv"] += 1
            elif info.get("is_deregister"):
                logging.info(f"[DEREGISTER ACK] seq={seq} RTT={rtt}ms")
            else:
                logging.info(f"[ACK] seq={seq} rtt_ms={rtt} retrans={info['retrans_count']}")
                self.metrics["reliable_acks_recv"] += 1

            # Remove from sent buffer
            self.sent_buffer.pop(seq, None)


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
                    earliest_seq = min(recv_buf.keys(), key=lambda s: (s - expected) & 0xFFFF)
                    earliest_info = recv_buf[earliest_seq]
                    wait_time = now_ms() - earliest_info[2]
                    if wait_time >= self.skip_threshold_ms:
                        logging.warning(f"[SKIP R] missing seq={expected}, skipping after {wait_time}ms")
                        sess["recv_next_expected"] = earliest_seq
                        self.metrics["lost_marked"] += 1
                        progressed = True
                else:
                    break

    # --------------------------
    # Retransmission
    # --------------------------
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

                    # Drop if exceeded skip threshold
                    if age >= self.skip_threshold_ms:
                        logging.warning(f"[SENDER SKIP] seq={seq} exceeded skip threshold {age} ms -> drop")
                        self.metrics["lost_marked"] += 1
                        to_remove.append(seq)
                        continue

                    # Retransmit if interval passed
                    if now - info["last_send_ms"] >= RETX_INTERVAL_MS:
                        try:
                            dest_addr = info.get("addr", self.peer_addr)
                            if dest_addr:
                                self.sock.sendto(info["pkt"], dest_addr)
                                info["last_send_ms"] = now
                                info["retrans_count"] += 1
                                self.metrics["retransmissions"] += 1

                                if info.get("is_registration"):
                                    logging.info(f"[RETX REGISTER] seq={seq} to {dest_addr}, retx_count={info["retrans_count"]}")
                                else:
                                    logging.info(f"[RETX RELIABLE] seq={seq} to {dest_addr}, retx_count={info["retrans_count"]}")
                        except Exception:
                            logging.exception("retransmit failed")

                # Remove acked or skipped packets from buffer
                for s in to_remove:
                    self.sent_buffer.pop(s, None)

            time.sleep(RETX_INTERVAL_MS / 1000.0)


    # def stop(self):
    #     self.running = False
    #     self.receiver_thread.join(timeout=1.0)
    #     self.retrans_thread.join(timeout=1.0)
    #     self.sock.close()
    #     self.sessions.clear()
    #     self.registered_peers.clear()
    #     logging.info("[STOP] GameNetAPI stopped")

    def stop(self):
        # If sender, send reliable deregister
        if self.peer_addr and self.peer_addr in self.registered_peers:
            try:
                seq = random.randint(0, 0xFFFF)
                timestamp_ms = now_ms()
                pkt = struct.pack(DATA_HDR_FMT, CHANNEL_DEREGISTER, seq, timestamp_ms)
                logging.info(f"[DEREGISTER] Sending deregister seq={seq} to {self.peer_addr}")
                ev = threading.Event()

                # Store in sent_buffer for retransmission until ACK
                with self.sent_lock:
                    self.sent_buffer[seq] = {
                        "pkt": pkt,
                        "first_send_ms": timestamp_ms,
                        "last_send_ms": timestamp_ms,
                        "acked": False,
                        "retrans_count": 0,
                        "is_deregister": True,
                        "event": ev
                    }

                self.sock.sendto(pkt, self.peer_addr)
        
                # Wait for ACK or timeout
                timeout_ms = 5000
                waited = ev.wait(timeout_ms / 1000.0)

                # Clean up
                with self.sent_lock:
                    info = self.sent_buffer.clear()

                if waited:
                    logging.info(f"[DEREGISTER COMPLETE] Session closed with {self.peer_addr}")
                else:
                    logging.warning(f"[DEREGISTER] No ACK from {self.peer_addr} after {timeout_ms}ms")
            except Exception:
                logging.exception("Failed to send deregister packet")
        
        # Clear sessions if receiver
        if not self.peer_addr:
            removed = len(self.sessions)
            if removed == 0:
                self.sessions.clear()
                logging.info(f"[SESSION] Cleared {removed} sessions")
            else:
                logging.warning(f"[SESSION] Stopped while {removed} active session(s) remain")

        # Stop threads
        self.running = False
        self.receiver_thread.join(timeout=1.0)
        self.retrans_thread.join(timeout=1.0)
        self.registered_peers.clear()
        self.sock.close()
        
        logging.info("[STOP] GameNetAPI stopped")


    def get_metrics(self):
        return self.metrics.copy()
