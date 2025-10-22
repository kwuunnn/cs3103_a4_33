import socket, threading, struct, time, random, collections, logging
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

# Default params
DEFAULT_SKIP_MS = 200
RETX_INTERVAL_MS = 50  # retransmit every 50ms until ack or skip

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def now_ms():
    return int(time.time() * 1000)


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
        self.next_seq = 0  # sender seq
        # Sender reliable buffer: seq -> {data, send_time, last_send, acked, retrans_count}
        self.sent_buffer = {}
        self.sent_lock = threading.Lock()

        # Receiver buffering for reliable channel
        self.recv_next_expected = 0
        self.recv_buffer = {}  # seq -> (timestamp, payload, arrival_time)
        self.recv_lock = threading.Lock()
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
        }

        # Threading
        self.running = True
        self.receiver_thread = threading.Thread(target=self._recv_loop, daemon=True)
        self.retrans_thread = threading.Thread(target=self._retrans_loop, daemon=True)
        self.receiver_thread.start()
        self.retrans_thread.start()

    def set_peer(self, addr):
        self.peer_addr = addr

    def _pack_data(self, channel, seq, timestamp_ms, payload: bytes):
        hdr = struct.pack(
            DATA_HDR_FMT, channel, seq & 0xFFFF, timestamp_ms & 0xFFFFFFFF
        )
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

    def send(self, payload: bytes, reliable: bool = True):
        """API for application to send data."""
        with self.seq_lock:
            seq = self.next_seq
            self.next_seq = (self.next_seq + 1) & 0xFFFF
        timestamp = now_ms()
        channel = CHANNEL_RELIABLE if reliable else CHANNEL_UNRELIABLE
        pkt = self._pack_data(channel, seq, timestamp, payload)
        if self.peer_addr is None:
            raise RuntimeError("peer_addr not set")

        # Send the datagram
        self.sock.sendto(pkt, self.peer_addr)
        if reliable:
            self.metrics["sent_reliable"] += 1
            with self.sent_lock:
                self.sent_buffer[seq] = {
                    "pkt": pkt,
                    "first_send": timestamp,
                    "last_send": timestamp,
                    "acked": False,
                    "retrans_count": 0,
                }
        else:
            self.metrics["sent_unreliable"] += 1

        logging.debug(
            f"sent seq={seq} chan={'R' if reliable else 'U'} len={len(payload)}"
        )
        return seq

    def _recv_loop(self):
        self.sock.settimeout(0.5)
        while self.running:
            try:
                data, addr = self.sock.recvfrom(65536)
            except socket.timeout:
                continue
            # If peer not set, set it to first sender
            if self.peer_addr is None:
                self.peer_addr = addr

            # Try parse as ACK first
            ack_unpacked = self._unpack_ack(data)
            if ack_unpacked is not None:
                seq, ts = ack_unpacked
                self._handle_ack(seq, ts)
                continue

            # else parse as data
            parsed = self._unpack_data(data)
            if parsed is None:
                continue
            channel, seq, ts, payload = parsed
            arrival = now_ms()
            if channel == CHANNEL_UNRELIABLE:
                self.metrics["recv_unreliable"] += 1
                # deliver immediately
                logging.info(
                    f"[RECV U ] seq={seq} ts={ts} arrival={arrival} len={len(payload)}"
                )
                try:
                    self.on_receive(CHANNEL_UNRELIABLE, seq, ts, payload)
                except Exception as e:
                    logging.exception("on_receive failed")
            else:
                # reliable channel: send ACK and buffer/reorder
                # send ack
                ackpkt = self._pack_ack(seq, ts)
                self.sock.sendto(ackpkt, addr)
                arrival_info = (ts, payload, arrival)
                with self.recv_lock:
                    if len(self.recv_buffer) < self.max_buffered:
                        self.recv_buffer[seq] = arrival_info
                    # attempt in-order delivery
                    self._deliver_in_order_locked()
                logging.info(
                    f"[RECV R ] seq={seq} ts={ts} arrival={arrival} buffered={len(self.recv_buffer)}"
                )

    def _handle_ack(self, seq, ts):
        now = now_ms()
        with self.sent_lock:
            info = self.sent_buffer.get(seq)
            if info:
                info["acked"] = True
                info["ack_time"] = now
                rtt = now - info["first_send"]
                self.metrics["acks_received"] += 1
                logging.info(
                    f"[ACK] seq={seq} rtt_ms={rtt} retrans={info['retrans_count']}"
                )
                # remove from buffer
                del self.sent_buffer[seq]

    def _deliver_in_order_locked(self):
        # caller must hold recv_lock
        # deliver next_expected and advance
        progressed = True
        while progressed:
            progressed = False
            info = self.recv_buffer.get(self.recv_next_expected)
            if info:
                ts, payload, arrival = info
                # deliver to application
                self.metrics["recv_reliable"] += 1
                try:
                    self.on_receive(
                        CHANNEL_RELIABLE, self.recv_next_expected, ts, payload
                    )
                except Exception:
                    logging.exception("on_receive callback error")
                del self.recv_buffer[self.recv_next_expected]
                self.recv_next_expected = (self.recv_next_expected + 1) & 0xFFFF
                progressed = True
            else:
                # check skip threshold: if we have later packets and oldest later packet arrival > skip_threshold -> skip
                if self.recv_buffer:
                    # find smallest seq > next_expected that is present
                    # because seq wraps, use simple comparison by scanning small window
                    candidates = sorted(self.recv_buffer.keys())
                    # pick earliest arrival among candidates
                    earliest_seq = candidates[0]
                    ev = self.recv_buffer[earliest_seq]
                    arrival_time = ev[2]
                    if now_ms() - arrival_time >= self.skip_threshold_ms:
                        logging.warning(
                            f"[SKIP R] skipping missing seq={self.recv_next_expected} after {now_ms()-arrival_time} ms"
                        )
                        self.recv_next_expected = (self.recv_next_expected + 1) & 0xFFFF
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
                    age = now - info["first_send"]
                    if age >= self.skip_threshold_ms:
                        # mark lost, stop retransmitting
                        logging.warning(
                            f"[SENDER SKIP] seq={seq} exceeded skip threshold {age} ms -> drop"
                        )
                        to_remove.append(seq)
                        self.metrics["lost_marked"] += 1
                        continue
                    # time since last sent?
                    if now - info["last_send"] >= RETX_INTERVAL_MS:
                        # retransmit
                        try:
                            self.sock.sendto(info["pkt"], self.peer_addr)
                            info["last_send"] = now
                            info["retrans_count"] += 1
                            self.metrics["retransmissions"] += 1
                            logging.info(
                                f"[RETX] seq={seq} count={info['retrans_count']}"
                            )
                        except Exception:
                            logging.exception("retransmit failed")
                for s in to_remove:
                    if s in self.sent_buffer:
                        del self.sent_buffer[s]
            time.sleep(RETX_INTERVAL_MS / 1000.0)

    def stop(self):
        self.running = False
        self.receiver_thread.join(timeout=1.0)
        self.retrans_thread.join(timeout=1.0)
        self.sock.close()

    def get_metrics(self):
        return self.metrics.copy()
