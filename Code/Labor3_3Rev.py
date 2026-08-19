import serial
import serial.tools.list_ports
import threading
import time
import tkinter as tk
from tkinter import ttk


def cobs_decode(data: bytes) -> bytes:
    output = bytearray()
    idx = 0
    while idx < len(data):
        code = data[idx]
        idx += 1
        if code == 0 or idx + code - 1 > len(data) + 1:
            raise ValueError("Ungültiges COBS-Frame")
        for i in range(code - 1):
            if idx >= len(data):
                raise ValueError("Ungültiges COBS-Frame wegen Range")
            output.append(data[idx])
            idx += 1
        if code < 0xFF and idx < len(data):
            output.append(0)
    return bytes(output)


BAUD           = 115200
MAX_RPM        = 200
TARGET_REVS    = 3

CMD_INIT_SPEED = b"\x02\x63\x04\x90\x01\xF4\x00"
CMD_MAX_SPEED  = b"\x05\x01\x02\xFF\x02\x00"
CMD_STOP       = b"\x05\x01\x02\x80\x83\x00"


def list_ports() -> list[str]:
    return [p.device for p in serial.tools.list_ports.comports()
            if p.vid is not None]


class SpeedGauge(tk.Canvas):

    def __init__(self, parent, label: str, max_rpm: int, **kwargs):
        super().__init__(parent, height=120, bg="#1e1e2e", highlightthickness=0, **kwargs)
        self.max_rpm = max_rpm
        self.label = label
        self._value = 0
        self.bind("<Configure>", lambda _: self._draw())

    def set_value(self, rpm: int):
        self._value = max(0, rpm)
        self._draw()

    def _draw(self):
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 10:
            return
        pad = 16
        bar_h = 28
        bar_y = h // 2 - bar_h // 2 + 10
        self.create_rectangle(pad, bar_y, w - pad, bar_y + bar_h,
                               fill="#313244", outline="", width=0)
        ratio = min(self._value / self.max_rpm, 1.0)
        bar_w = int((w - 2 * pad) * ratio)
        if bar_w > 0:
            color = "#a6e3a1" if ratio < 0.5 else "#f9e2af" if ratio < 0.8 else "#f38ba8"
            self.create_rectangle(pad, bar_y, pad + bar_w, bar_y + bar_h,
                                   fill=color, outline="", width=0)
        self.create_text(pad, bar_y - 8, anchor="sw", text=self.label,
                         fill="#cdd6f4", font=("Helvetica", 11, "bold"))
        self.create_text(w - pad, bar_y - 8, anchor="se",
                         text=f"{self._value} rpm", fill="#cdd6f4",
                         font=("Helvetica", 11))


class RevGauge(tk.Canvas):

    def __init__(self, parent, target: int, **kwargs):
        super().__init__(parent, height=120, bg="#1e1e2e", highlightthickness=0, **kwargs)
        self.target = target
        self._value = 0.0
        self.bind("<Configure>", lambda _: self._draw())

    def set_value(self, revs: float):
        self._value = max(0.0, min(revs, float(self.target)))
        self._draw()

    def _draw(self):
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 10:
            return
        pad = 16
        bar_h = 28
        bar_y = h // 2 - bar_h // 2 + 10
        self.create_rectangle(pad, bar_y, w - pad, bar_y + bar_h,
                               fill="#313244", outline="", width=0)
        ratio = self._value / self.target
        bar_w = int((w - 2 * pad) * ratio)
        if bar_w > 0:
            self.create_rectangle(pad, bar_y, pad + bar_w, bar_y + bar_h,
                                   fill="#89b4fa", outline="", width=0)
        self.create_text(pad, bar_y - 8, anchor="sw", text="Umdrehungen",
                         fill="#cdd6f4", font=("Helvetica", 11, "bold"))
        self.create_text(w - pad, bar_y - 8, anchor="se",
                         text=f"{self._value:.2f} / {self.target}",
                         fill="#cdd6f4", font=("Helvetica", 11))


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Labor3 – 3 Umdrehungen")
        self.root.configure(bg="#1e1e2e")
        self.root.resizable(False, False)

        self._running = False
        self._ser = None
        self._thread = None
        self._at_max = False

        self._counting = False
        self._total_revs = 0.0
        self._max_rpm = 0
        self._last_rpm_time = 0.0

        self._build_ui()

    def _build_ui(self):
        outer = tk.Frame(self.root, bg="#1e1e2e", padx=24, pady=20)
        outer.pack(fill=tk.BOTH, expand=True)

        tk.Label(outer, text="3-Umdrehungen Test", bg="#1e1e2e", fg="#cba6f7",
                 font=("Helvetica", 18, "bold")).pack(pady=(0, 16))

        # --- Port-Auswahl (identisch mit Labor3_Drehzahl) ---
        port_frame = tk.Frame(outer, bg="#313244", padx=12, pady=10)
        port_frame.pack(fill=tk.X, pady=(0, 16))

        tk.Label(port_frame, text="USB-Port:", bg="#313244", fg="#cdd6f4",
                 font=("Helvetica", 10)).grid(row=0, column=0, sticky="w", padx=(0, 8))

        self._port_var = tk.StringVar()
        self._port_cb = ttk.Combobox(port_frame, textvariable=self._port_var,
                                     state="readonly", width=30,
                                     font=("Helvetica", 10))
        self._port_cb.grid(row=0, column=1, padx=(0, 8))
        self._port_cb.bind("<<ComboboxSelected>>", lambda _: self._connect())

        tk.Button(port_frame, text="⟳", bg="#45475a", fg="#cdd6f4",
                  activebackground="#585b70", relief="flat", font=("Helvetica", 12),
                  cursor="hand2", padx=6,
                  command=self._refresh_ports).grid(row=0, column=2, padx=(0, 8))

        self._btn_disconnect = tk.Button(
            port_frame, text="Trennen",
            bg="#f38ba8", fg="#1e1e2e", activebackground="#d97b96",
            relief="flat", font=("Helvetica", 10, "bold"),
            cursor="hand2", padx=10, pady=4,
            command=self._disconnect, state="disabled")
        self._btn_disconnect.grid(row=0, column=3)

        # --- Gauges ---
        self.gauge_rpm = SpeedGauge(outer, "Aktuelle Drehzahl", MAX_RPM, width=420)
        self.gauge_rpm.pack(fill=tk.X, pady=4)

        self.gauge_rev = RevGauge(outer, TARGET_REVS, width=420)
        self.gauge_rev.pack(fill=tk.X, pady=4)

        # --- Numerische Anzeigen ---
        num_frame = tk.Frame(outer, bg="#1e1e2e")
        num_frame.pack(fill=tk.X, pady=(12, 0))

        self._lbl_rpm = tk.StringVar(value="0 rpm")
        self._lbl_max = tk.StringVar(value="– rpm")

        for col, (txt, var) in enumerate([
                ("Aktuelle Drehzahl", self._lbl_rpm),
                ("Max. Drehzahl", self._lbl_max)]):
            f = tk.Frame(num_frame, bg="#313244", padx=16, pady=10)
            f.grid(row=0, column=col, padx=6, sticky="nsew")
            num_frame.columnconfigure(col, weight=1)
            tk.Label(f, text=txt, bg="#313244", fg="#6c7086",
                     font=("Helvetica", 10)).pack()
            tk.Label(f, textvariable=var, bg="#313244", fg="#89dceb",
                     font=("Helvetica", 20, "bold")).pack()

        # --- Start-Button ---
        self._btn_start = tk.Button(
            outer, text="▶  Start  (3 Umdrehungen)",
            bg="#a6e3a1", fg="#1e1e2e", activebackground="#94d19a",
            font=("Helvetica", 13, "bold"), relief="flat",
            padx=20, pady=14, cursor="hand2",
            command=self._start_run, state="disabled")
        self._btn_start.pack(fill=tk.X, pady=(20, 0))

        # --- Statuszeile ---
        self._status = tk.StringVar(value="Kein Gerät verbunden.")
        tk.Label(outer, textvariable=self._status, bg="#1e1e2e", fg="#6c7086",
                 font=("Helvetica", 9)).pack(pady=(14, 0))

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._refresh_ports()
        self._connect()

    def _refresh_ports(self):
        ports = list_ports()
        self._port_cb["values"] = ports
        if ports:
            if self._port_var.get() not in ports:
                self._port_var.set(ports[0])
        else:
            self._port_var.set("")
            self._status.set("Keine USB-Geräte gefunden.")

    def _connect(self):
        port = self._port_var.get()
        if not port:
            return
        if self._ser and self._ser.is_open:
            self._disconnect()
        try:
            self._ser = serial.Serial(port=port, baudrate=BAUD, timeout=1)
            self._running = True
            self._thread = threading.Thread(target=self._read_loop, daemon=True)
            self._thread.start()
            self._ser.write(CMD_INIT_SPEED)  # Gerät in Reporting-Modus versetzen
            self._status.set(f"Verbunden: {port} @ {BAUD}")
            self._port_cb.config(state="disabled")
            self._btn_disconnect.config(state="normal")
            self._btn_start.config(state="normal")
        except serial.SerialException as e:
            self._status.set(f"Verbindungsfehler: {e}")

    def _disconnect(self):
        self._counting = False
        self._at_max = False
        self._running = False
        if self._ser and self._ser.is_open:
            try:
                self._ser.write(CMD_STOP)
                self._ser.close()
            except Exception:
                pass
        self._ser = None
        self._btn_disconnect.config(state="disabled")
        self._btn_start.config(state="disabled")
        self._port_cb.config(state="readonly")
        self._status.set("Getrennt.")
        self._lbl_rpm.set("0 rpm")
        self._lbl_max.set("– rpm")
        self.gauge_rpm.set_value(0)
        self.gauge_rev.set_value(0)

    # ---------------------------------------------------------------- Motor --

    def _start_run(self):
        if not self._ser or not self._ser.is_open:
            return
        self._total_revs = 0.0
        self._max_rpm = 0
        self._last_rpm_time = time.time()
        self._counting = True
        self._at_max = True
        self._btn_start.config(state="disabled")
        self._lbl_max.set("– rpm")
        self.gauge_rev.set_value(0)
        self._status.set("Motor läuft – zähle Umdrehungen …")
        self._ser.write(CMD_INIT_SPEED)
        self._ser.write(CMD_MAX_SPEED)
        self._keep_alive()

    def _stop_run(self):
        self._counting = False
        self._at_max = False
        if self._ser and self._ser.is_open:
            try:
                self._ser.write(CMD_STOP)
            except Exception:
                pass
        self.gauge_rev.set_value(TARGET_REVS)
        self._lbl_max.set(f"{self._max_rpm} rpm")
        self._status.set(f"Fertig – Max. Drehzahl: {self._max_rpm} rpm")
        self._btn_start.config(state="normal")

    def _keep_alive(self):
        if not self._at_max or not self._running:
            return
        if self._ser and self._ser.is_open:
            try:
                self._ser.write(CMD_MAX_SPEED)
            except Exception:
                pass
        self.root.after(500, self._keep_alive)

    # ---------------------------------------------------------------- Serial --

    def _read_loop(self):
        buf = bytearray()
        while self._running:
            try:
                byte = self._ser.read(1)
                if not byte:
                    continue
                if byte == b'\x00':
                    if buf:
                        self._process_frame(bytes(buf))
                    buf.clear()
                else:
                    buf.extend(byte)
            except Exception:
                pass

    def _process_frame(self, raw: bytes):
        try:
            decoded = cobs_decode(raw)
            code = int.from_bytes(decoded[0:2], "little", signed=False)
            if code == 0x0630:
                rpm = int.from_bytes(decoded[6:10], "little", signed=True)
                now = time.time()

                if self._counting:
                    dt = now - self._last_rpm_time
                    self._total_revs += max(0, rpm) * dt / 60.0
                    if rpm > self._max_rpm:
                        self._max_rpm = rpm
                    if self._total_revs >= TARGET_REVS:
                        self.root.after(0, self._stop_run)

                self._last_rpm_time = now
                self.root.after(0, self._update_display, rpm)
        except Exception:
            pass

    def _update_display(self, rpm: int):
        self._lbl_rpm.set(f"{rpm} rpm")
        self.gauge_rpm.set_value(rpm)
        if self._counting:
            self.gauge_rev.set_value(self._total_revs)

    def _on_close(self):
        self._disconnect()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
