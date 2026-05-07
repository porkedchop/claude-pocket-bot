"""Audio capture + TCP streaming to the laptop companion.

Capture path: M5.Mic at 16 kHz mono 16-bit, 64 ms chunks (2048 bytes).
Each chunk is recorded synchronously into a small bytearray and
immediately written to a TCP socket the device opened to the laptop.
At ~64 ms cadence, audio is gapless as long as the send completes
within a few ms — which it does over WiFi for 2 KB chunks.

Why M5.Mic.record() and not machine.I2S directly: M5.Mic.begin()
configures the ES8311 codec (gain, mute pin, mic bias) over I2C, and
replicating that by hand is error-prone. Using a small buffer with
M5.Mic.record() is functionally streaming — the buffer fills in
~64 ms, returns, the loop sends the chunk, then re-records into the
same buffer.

If a future UIFlow build's M5.Mic.record() is non-blocking (queues
the recording rather than blocking until full), `_record_chunk` polls
M5.Mic.isRecording() with a 200 ms safety bound. Both styles work.

Termination: caller closes the write half via `end_stream(sock)`,
which signals EOF to the laptop. The laptop then writes a single
'\\n'-terminated JSON line back, which `end_stream` reads and returns.
"""

import socket
import time

import M5

_SAMPLE_RATE = 16000
_CHUNK_BYTES = 2048   # 1024 samples = 64 ms at 16 kHz mono 16-bit


def _record_chunk(buf, rate=_SAMPLE_RATE):
    """Capture one chunk of audio into `buf`.

    Handles both blocking and queued M5.Mic.record() implementations.
    Caller-supplied buffer must be `_CHUNK_BYTES` long for the cadence
    math above to hold.
    """
    M5.Mic.record(buf, rate, False)
    deadline = time.ticks_add(time.ticks_ms(), 200)
    while True:
        try:
            status = M5.Mic.isRecording()
        except AttributeError:
            return
        if status == 0:
            return
        if time.ticks_diff(time.ticks_ms(), deadline) > 0:
            print("briefbot_audio: record timeout (status=", status, ")")
            return
        time.sleep_ms(5)


def open_stream(ip, port, connect_timeout_s=3):
    """Connect to the laptop and prepare the mic.

    Returns (sock, buf) on success; raises OSError on connect failure.
    M5.Mic.begin() is idempotent so calling it on every voice cycle is
    fine — cheaper than tracking init state across cycles.
    """
    M5.Mic.begin()
    addr = socket.getaddrinfo(ip, port)[0][-1]
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(connect_timeout_s)
    sock.connect(addr)
    # Reset to a longer timeout for the streaming phase. send/write are
    # bounded by audio cadence, not the network — but a hung WiFi link
    # shouldn't deadlock us.
    sock.settimeout(5)
    buf = bytearray(_CHUNK_BYTES)
    return sock, buf


def stream_chunk(sock, buf):
    """Capture one chunk and write it to the socket.

    Blocks for ~64 ms (recording) plus a few ms (network write).
    Raises OSError if the socket fails — caller should close the sock
    and surface an error to the UI.
    """
    _record_chunk(buf)
    sock.write(buf)


def end_stream(sock, response_timeout_s=8):
    """Signal EOF to the laptop and read the JSON-line response.

    The laptop is expected to reply with a single '\\n'-terminated UTF-8
    JSON line containing at minimum {"ok": bool, ...}. Returns the
    bytes up to (but not including) the newline. Closes `sock`.
    """
    try:
        sock.shutdown(socket.SHUT_WR)
    except (OSError, AttributeError):
        # Some builds drop SHUT_WR; close() will tear both sides down
        # at the cost of dropping any in-flight reply. Acceptable
        # fallback — the laptop typically responds within ~2 s of the
        # last byte we send, and shutdown() works on the ESP32 port we
        # target so this branch is the safety net.
        pass

    sock.settimeout(response_timeout_s)
    line = b""
    while True:
        try:
            chunk = sock.recv(256)
        except OSError:
            sock.close()
            raise
        if not chunk:
            break
        line += chunk
        if b"\n" in line:
            line = line.split(b"\n", 1)[0]
            break
    sock.close()
    return line
