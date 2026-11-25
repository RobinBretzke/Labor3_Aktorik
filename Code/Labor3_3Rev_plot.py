#python3.12 /Users/robin/Documents/Arbeit/Labor3_Aktorik/Code/Labor3_3Rev.py


import serial
import serial.tools.list_ports
import threading
import queue
import time
from collections import deque
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
TARGET_REVS    = 2.5

# Strom aus UART-Rohwert: 3,3 V / 4096 * Rohwert * CURRENT_GAIN  -> Ergebnis in A
ADC_VREF       = 3.3
ADC_STEPS      = 4096
CURRENT_GAIN   = 100      # per Multimeter bestätigt: 120 mA ≈ 1,5 ADC-Schritte


def raw_to_ma(raw: int) -> float:
    amps = ADC_VREF / ADC_STEPS * raw * CURRENT_GAIN
    return amps * 1000.0

KEEPALIVE_MS   = 500      # Fahrkommando wird in diesem Abstand wiederholt (wie im Original)
TX_BYTE_GAP_MS = 0        # >0: Kommandos Byte für Byte mit Pause senden (Test gegen UART-Overrun im µC)
RESYNC_ZERO    = False    # True: vor jedem Kommando ein 0x00 senden (COBS-Resync)
DEBUG_LOG      = True     # gesendete Kommandos + Drehzahl in der Konsole protokollieren

RPM_INTERVAL_MS = 50      # Meldeintervall der Drehzahl in ms (steckt im INIT-Kommando; Original war 400)
RPM_SMOOTH_N    = 5       # Anzeige/Max-Drehzahl: Mittel über so viele Frames (5 x 40 ms = 200 ms)
RPM_TIMEOUT_S   = 2.5 * RPM_INTERVAL_MS / 1000   # so lange ohne Drehzahl-Frame = Motor steht


def cobs_encode(data: bytes) -> bytes:
    out = bytearray()
    block = bytearray()
    for b in data:
        if b == 0:
            out.append(len(block) + 1); out += block; block.clear()
        else:
            block.append(b)
            if len(block) == 254:
                out.append(255); out += block; block.clear()
    out.append(len(block) + 1); out += block
    return bytes(out) + b"\x00"


def make_cmd(payload: bytes) -> bytes:
    """Payload + Prüfsumme (Summe der Bytes, low byte), COBS-kodiert."""
    return cobs_encode(payload + bytes([sum(payload) & 0xFF]))


CMD_INIT_SPEED = make_cmd(bytes([0x63, 0x00]) + RPM_INTERVAL_MS.to_bytes(2, "little"))
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

    def __init__(self, parent, target: float, **kwargs):
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


class CurrentPlot(tk.Canvas):
    """Plot des Stroms über einen Lauf: Start bei Motorstart, Ende bei Stillstand."""

    BG, GRID, TXT, LINE, AVG = "#1e1e2e", "#313244", "#6c7086", "#fab387", "#89dceb"

    def __init__(self, parent, **kwargs):
        super().__init__(parent, bg=self.BG, highlightthickness=0, **kwargs)
        self._data = []             # (Zeit seit Start [s], mA)
        self._t0 = None
        self._avg = None
        self.recording = False
        self._dirty = True
        self.bind("<Configure>", lambda _: self.redraw())

    def start(self, t0: float):
        self._data = []
        self._t0 = t0
        self._avg = None
        self.recording = True
        self._dirty = True

    def stop(self):
        self.recording = False
        self._dirty = True

    def add(self, t: float, ma: float):
        if not self.recording:
            return
        self._data.append((t - self._t0, ma))
        self._dirty = True

    def set_avg(self, avg):
        self._avg = avg
        self._dirty = True

    def clear(self):
        self._data = []
        self._avg = None
        self.recording = False
        self._dirty = True

    @staticmethod
    def _nice_step(span: float, n: int = 5) -> float:
        import math
        raw = span / n
        mag = 10 ** math.floor(math.log10(raw))
        for f in (1, 2, 2.5, 5, 10):
            if raw <= f * mag:
                return f * mag
        return 10 * mag

    def redraw(self, force: bool = True):
        if not force and not self._dirty:
            return
        self._dirty = False
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 50 or h < 50:
            return
        l, r, t, b = 60, 16, 36, 30          # Ränder
        pw, ph = w - l - r, h - t - b

        state = "läuft …" if self.recording else ("fertig" if self._data else "wartet auf Start")
        self.create_text(l, 14, anchor="w", text="Strom – Verlauf",
                         fill="#cdd6f4", font=("Helvetica", 11, "bold"))
        info = state
        if self._data:
            dur = self._data[-1][0]
            info += f"   ·   {len(self._data)} Werte in {dur:.2f} s"
        self.create_text(l + 150, 14, anchor="w", text=info,
                         fill=self.TXT, font=("Helvetica", 9))

        # Achsenbereiche
        vals = [v for _, v in self._data]
        y_max = max(max(vals, default=0) * 1.15, 100)
        y_min = min(min(vals, default=0) * 1.15, 0)
        if y_min > -0.05 * y_max:      # kleines Rauschen um 0 -> Achse bei 0 lassen
            y_min = 0
        y_step = self._nice_step(y_max - y_min)
        y_max = y_step * -(-y_max // y_step)
        y_min = y_step * (y_min // y_step)

        x_end = max(self._data[-1][0] if self._data else 0, 0.5)
        x_step = self._nice_step(x_end, 6)
        x_max = x_step * -(-x_end // x_step)

        def ypix(v):
            return t + ph - (v - y_min) / (y_max - y_min) * ph

        def xpix(ts):
            return l + ts / x_max * pw

        # Gitter + Beschriftung
        v = y_min
        while v <= y_max + 1e-9:
            y = ypix(v)
            self.create_line(l, y, l + pw, y, fill=self.GRID)
            self.create_text(l - 6, y, anchor="e", text=f"{v:.0f}",
                             fill=self.TXT, font=("Helvetica", 9))
            v += y_step
        self.create_text(14, t + ph / 2, text="mA", angle=90,
                         fill=self.TXT, font=("Helvetica", 9))
        xs = 0.0
        while xs <= x_max + 1e-9:
            x = xpix(xs)
            self.create_line(x, t, x, t + ph, fill=self.GRID)
            self.create_text(x, t + ph + 12, text=f"{xs:g} s",
                             fill=self.TXT, font=("Helvetica", 9))
            xs += x_step

        # Mittelwert-Linie
        if self._avg is not None:
            y = ypix(self._avg)
            self.create_line(l, y, l + pw, y, fill=self.AVG, dash=(4, 3))
            self.create_text(l + pw - 4, y - 4, anchor="se",
                             text=f"Ø {self._avg:.0f} mA",
                             fill=self.AVG, font=("Helvetica", 9))

        # Messkurve + Messpunkte
        if len(self._data) >= 2:
            pts = []
            for ts, val in self._data:
                pts += (xpix(ts), ypix(val))
            self.create_line(*pts, fill=self.LINE, width=2)
        for ts, val in self._data:
            x, y = xpix(ts), ypix(val)
            self.create_oval(x - 2, y - 2, x + 2, y + 2, fill=self.LINE, outline="")

        # aktueller Wert
        if self._data:
            self.create_text(l + pw, 14, anchor="e",
                             text=f"{self._data[-1][1]:.0f} mA",
                             fill="#cdd6f4", font=("Helvetica", 11))


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Labor3")
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

        self._curr_sum = 0.0
        self._curr_n = 0

        self._last_rpm = 0
        self._last_rpm_rx = 0.0              # Zeitpunkt des letzten Drehzahl-Frames
        self._rpm_hist = deque(maxlen=RPM_SMOOTH_N)
        self._rpm_rx_times = deque(maxlen=100)
        self._run_id = 0
        self._rx_stats = {"rpm": 0, "current": 0, "fehler": 0, "rpm_last": None,
                          "raw_min": None, "raw_max": None}
        self._stats_t = time.time()
        self._log_t0 = time.time()
        self._curr_offset_raw = 0.0          # Nullpunkt-Offset des Stromsensors (Rohwert)
        self._idle_raw = deque()             # Rohwerte bei stehendem Motor (t, raw)
        self._rx_queue = queue.Queue()   # Lese-Thread -> Hauptthread

        self._build_ui()
        self._poll_queue()

    def _build_ui(self):
        outer = tk.Frame(self.root, bg="#1e1e2e", padx=24, pady=20, width=480)
        outer.pack(side=tk.LEFT, fill=tk.Y)
        outer.pack_propagate(True)

        # --- Live-Plot Strom (rechte Seite) ---
        plot_frame = tk.Frame(self.root, bg="#1e1e2e", padx=0, pady=20)
        plot_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 24))
        self.plot_curr = CurrentPlot(plot_frame, width=560, height=600)
        self.plot_curr.pack(fill=tk.BOTH, expand=True)

        tk.Label(outer, text="", bg="#1e1e2e", fg="#cba6f7",
                 font=("Helvetica", 18, "bold")).pack(pady=(0, 16))

        # --- Port-Auswahl ---
        port_frame = tk.Frame(outer, bg="#313244", padx=12, pady=10)
        port_frame.pack(fill=tk.X, pady=(0, 16))

        tk.Label(port_frame, text="USB-Port:", bg="#313244", fg="#cdd6f4",
                 font=("Helvetica", 10)).grid(row=0, column=0, sticky="w", padx=(0, 8))

        self._port_var = tk.StringVar()
        self._port_cb = ttk.Combobox(port_frame, textvariable=self._port_var,
                                     state="readonly", width=22,
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

        # --- Drehzahl-Gauge ---
        self.gauge_rpm = SpeedGauge(outer, "Aktuelle Drehzahl", MAX_RPM, width=432)
        self.gauge_rpm.pack(fill=tk.X, pady=4)

        # --- Umdrehungs-Gauge ---
        self.gauge_rev = RevGauge(outer, TARGET_REVS, width=432)
        self.gauge_rev.pack(fill=tk.X, pady=4)

      
        

        # --- Aktuelle Drehzahl (numerisch) ---
        f1 = tk.Frame(outer, bg="#313244", padx=16, pady=10)
        f1.pack(fill=tk.X, pady=(4, 0))
        tk.Label(f1, text="Aktuelle Drehzahl", bg="#313244", fg="#6c7086",
                 font=("Helvetica", 10)).pack()
        self._lbl_rpm = tk.StringVar(value="0 rpm")
        tk.Label(f1, textvariable=self._lbl_rpm, bg="#313244", fg="#89dceb",
                 font=("Helvetica", 20, "bold")).pack()

        # --- Max Drehzahl (numerisch) ---
        f2 = tk.Frame(outer, bg="#313244", padx=16, pady=10)
        f2.pack(fill=tk.X, pady=(4, 0))
        tk.Label(f2, text="Max. Drehzahl", bg="#313244", fg="#6c7086",
                 font=("Helvetica", 10)).pack()
        self._lbl_max = tk.StringVar(value="– rpm")
        tk.Label(f2, textvariable=self._lbl_max, bg="#313244", fg="#89dceb",
                 font=("Helvetica", 20, "bold")).pack()

        # ---Avg current 
        f3 = tk.Frame(outer, bg="#313244", padx=16, pady=10)
        f3.pack(fill=tk.X, pady=(4, 0))
        tk.Label(f3, text="Strom", bg="#313244", fg="#6c7086",
                    font=("Helvetica", 10)).pack()
        self._current_avg = tk.StringVar(value="– mA")
        tk.Label(f3, textvariable=self._current_avg, bg="#313244", fg="#89dceb",
                    font=("Helvetica", 20, "bold")).pack()

        # --- Start-Button ---
        self._btn_start = tk.Button(
            outer, text="▶  Start",
            bg="#a6e3a1", fg="#1e1e2e", activebackground="#94d19a",
            font=("Helvetica", 13, "bold"), relief="flat",
            padx=20, pady=14, cursor="hand2",
            command=self._start_run, state="disabled")
        self._btn_start.pack(fill=tk.X, pady=(20, 4))

        self._btn_abort = tk.Button(
            outer, text="✕  Abbrechen",
            bg="#f38ba8", fg="#1e1e2e", activebackground="#d97b96",
            font=("Helvetica", 11, "bold"), relief="flat",
            padx=20, pady=10, cursor="hand2",
            command=self._abort_run, state="disabled")
        self._btn_abort.pack(fill=tk.X)

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
            # exclusive=True: Port kann nicht gleichzeitig von einem zweiten
            # Programm (z. B. einer noch offenen Skript-Instanz) geöffnet werden
            self._ser = serial.Serial(port=port, baudrate=BAUD, timeout=1,
                                      exclusive=True)
            self._ser.reset_input_buffer()   # alte/halbe Bytes im Puffer verwerfen
            self._running = True
            self._thread = threading.Thread(target=self._read_loop, daemon=True)
            self._thread.start()
            self._send(CMD_INIT_SPEED, "INIT")
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
                for _ in range(3):            # STOP mehrfach, Port ist danach zu
                    self._send(CMD_STOP, "STOP (Trennen)")
                    time.sleep(0.02)
                self._ser.flush()             # warten, bis alles rausgeschrieben ist
                self._ser.close()
            except Exception:
                pass
        self._ser = None
        self._lbl_rpm.set("0 rpm")
        self._lbl_max.set("– rpm")
        self._current_avg.set("– mA")
        self.gauge_rpm.set_value(0)
        self.gauge_rev.set_value(0)
        self.plot_curr.clear()
        self.plot_curr.redraw()
        self._btn_disconnect.config(state="disabled")
        self._btn_start.config(state="disabled")
        self._btn_abort.config(state="disabled")
        self._port_cb.config(state="readonly")
        self._status.set("Getrennt.")

    # ---------------------------------------------------------------- Motor --

    def _start_run(self):
        if not self._ser or not self._ser.is_open:
            return
        self._total_revs = 0.0
        self._max_rpm = 0
        self._rpm_hist.clear()
        self._curr_sum = 0.0      # Strom-Mittelwert bei neuem Start zurücksetzen
        self._curr_n = 0
        self._tare_current()
        self._last_rpm_time = time.time()
        self._counting = True
        self._at_max = True
        self._btn_start.config(state="disabled")
        self._btn_abort.config(state="normal")
        self._lbl_max.set("– rpm")
        self._current_avg.set("– mA")
        self.gauge_rev.set_value(0)
        self.plot_curr.start(time.time())   # Aufzeichnung beginnt mit Motorstart
        self._status.set("Motor läuft – zähle Umdrehungen …")
        self._run_id += 1
        self._send(CMD_INIT_SPEED, "INIT")
        # kleine Pause, damit die Firmware das erste Frame verarbeiten kann
        self.root.after(30, self._keep_alive, self._run_id)
        self.root.after(1000, self._check_started, self._run_id)

    def _stop_run(self):
        if not self._counting:
            return  # bereits gestoppt
        self._counting = False
        self._at_max = False
        self._send_stop()
        self.gauge_rev.set_value(TARGET_REVS)
        self._lbl_max.set(f"{self._max_rpm} rpm")
        if self._curr_n:
            self._current_avg.set(f"{self._curr_sum / self._curr_n:.0f} mA")
        else:
            self._current_avg.set("– mA")
        self._status.set(f"Fertig – Max. Drehzahl: {self._max_rpm} rpm")
        self._btn_start.config(state="normal")
        self._btn_abort.config(state="disabled")

    def _abort_run(self):
        self._counting = False
        self._at_max = False
        self._send_stop()
        self.gauge_rev.set_value(0)
        self._lbl_rpm.set("0 rpm")
        self._status.set("Abgebrochen.")
        self._btn_start.config(state="normal")
        self._btn_abort.config(state="disabled")

    def _send_stop(self, tries: int = 20):
        """STOP alle 100 ms senden, bis Drehzahl 0 gemeldet wird (max. 2 s)."""
        if self._at_max or not self._ser or not self._ser.is_open:
            return  # neuer Lauf gestartet oder getrennt
        try:
            self._send(CMD_STOP, f"STOP (noch {tries}x)")
        except Exception as e:
            print(f"STOP-Schreibfehler: {e}")
        # mindestens 3x senden, danach weiter, solange noch Drehzahl gemeldet wird
        if tries > 1 and (not self._motor_stopped() or tries > 18):
            self.root.after(100, self._send_stop, tries - 1)
        elif not self._motor_stopped():
            self._status.set("Warnung: Motor meldet nach STOP noch Drehzahl!")
            self.plot_curr.stop()   # Aufzeichnung nicht endlos weiterlaufen lassen
            self._diagnose_rx()

    def _diagnose_rx(self):
        """Prüft, ob der Controller überhaupt noch Kommandos empfängt:
        INIT mit anderem Meldeintervall (400 ms) senden. Ändert sich die Rate der
        Drehzahl-Frames nicht, ist der Empfang im Controller tot."""
        print("DIAGNOSE: sende INIT mit 400 ms Intervall – ändert sich die Frame-Rate?")
        self._send(make_cmd(bytes([0x63, 0x00]) + (400).to_bytes(2, "little")),
                   "INIT (Diagnose 400ms)")
        self.root.after(1500, self._diagnose_rx_result)

    def _diagnose_rx_result(self):
        now = time.time()
        n = sum(1 for t in self._rpm_rx_times if t >= now - 1.0)
        if n > 6:
            msg = (f"Controller EMPFÄNGT NICHT MEHR ({n} Drehzahl-Frames/s statt ~2,5). "
                   "UART-Empfang im µC hängt – Controller zurücksetzen!")
        else:
            msg = (f"Controller empfängt noch (Rate jetzt {n}/s), ignoriert aber STOP – "
                   "Ursache in der Firmware-Logik.")
            self._send(CMD_INIT_SPEED, "INIT (zurück)")
        print("DIAGNOSE: " + msg)
        self._status.set("µC empfängt nicht mehr – Controller zurücksetzen!" if n > 6
                         else "µC empfängt, ignoriert aber STOP (Firmware?)")

    def _send(self, cmd: bytes, name: str):
        """Zentrale Sendefunktion – alle Kommandos laufen hier durch."""
        if not self._ser or not self._ser.is_open:
            return
        data = (b"\x00" + cmd) if RESYNC_ZERO else cmd
        if TX_BYTE_GAP_MS > 0:
            for b in data:
                self._ser.write(bytes([b]))
                self._ser.flush()
                time.sleep(TX_BYTE_GAP_MS / 1000)
        else:
            self._ser.write(data)
        if DEBUG_LOG:
            print(f"{time.time() - self._log_t0:8.3f}  TX {name:<16} {data.hex(' ')}")

    def _keep_alive(self, run_id: int):
        # run_id verhindert, dass alte Keep-Alive-Ketten nach Stop/Start weiterlaufen
        if run_id != self._run_id or not self._at_max or not self._running:
            return
        try:
            self._send(CMD_MAX_SPEED, "MAX_SPEED")
        except Exception as e:
            print(f"Schreibfehler: {e}")
        self.root.after(KEEPALIVE_MS, self._keep_alive, run_id)

    def _motor_stopped(self) -> bool:
        """Stillstand: 0 rpm gemeldet ODER länger keine Drehzahl-Frames mehr
        (die Firmware sendet bei stehendem Motor offenbar keine)."""
        return (self._last_rpm == 0
                or time.time() - self._last_rpm_rx > RPM_TIMEOUT_S)

    def _check_started(self, run_id: int):
        if run_id == self._run_id and self._at_max and self._last_rpm == 0:
            self._status.set("Warnung: Motor meldet 1 s nach Start keine Drehzahl!")
            print("WARNUNG: keine Drehzahl 1 s nach Start")

    # ---------------------------------------------------------------- Serial --

    def _read_loop(self):
        # Läuft im eigenen Thread: nur lesen und dekodieren, KEINE Tkinter-Aufrufe.
        buf = bytearray()
        synced = False   # erstes (evtl. angeschnittenes) Frame verwerfen
        while self._running:
            try:
                ser = self._ser
                if ser is None:
                    break
                byte = ser.read(1)
                if not byte:
                    continue
                if byte == b'\x00':
                    if buf and synced:
                        self._process_frame(bytes(buf))
                    synced = True
                    buf.clear()
                else:
                    buf.extend(byte)
            except Exception as e:
                if self._running:
                    print(f"Lesefehler: {e}")

    def _process_frame(self, raw: bytes):
        try:
            decoded = cobs_decode(raw)
            code = int.from_bytes(decoded[0:2], "little", signed=False)
            value = int.from_bytes(decoded[6:10], "little", signed=True)
            if code == 0x0630:
                self._rx_queue.put(("rpm", value, time.time()))
            elif code == 0x0640:
                self._rx_queue.put(("current", value, time.time()))
        except Exception as e:
            self._rx_stats["fehler"] += 1
            print(f"Frame-Fehler: {e} | {len(raw)} Bytes: {raw.hex(' ')}")

    def _poll_queue(self):
        # Läuft im Hauptthread: verarbeitet alle empfangenen Werte.
        try:
            while True:
                name, value, now = self._rx_queue.get_nowait()
                if name in ("rpm", "current"):
                    self._rx_stats[name] += 1
                if name == "rpm":
                    self._rx_stats["rpm_last"] = value
                    self._handle_rpm(value, now)
                elif name == "current":
                    self._handle_current(value, now)
        except queue.Empty:
            pass
        if DEBUG_LOG and time.time() - self._stats_t >= 1.0:
            st = self._rx_stats
            print(f"{time.time() - self._log_t0:8.3f}  RX/s: rpm-Frames {st['rpm']}"
                  f" (letzte rpm {st['rpm_last']}), Strom-Frames {st['current']},"
                  f" Frame-Fehler {st['fehler']}, Strom roh {st['raw_min']}..{st['raw_max']}"
                  f" (Nullpunkt {self._curr_offset_raw:.1f})")
            self._rx_stats = {"rpm": 0, "current": 0, "fehler": 0, "rpm_last": st["rpm_last"],
                              "raw_min": None, "raw_max": None}
            self._stats_t = time.time()
        # Firmware meldet bei Stillstand keine Drehzahl mehr -> Anzeige auf 0 setzen
        if self._last_rpm != 0 and self._motor_stopped():
            self._last_rpm = 0
            self._rpm_hist.clear()
            self._lbl_rpm.set("0 rpm")
            self.gauge_rpm.set_value(0)
        if self.plot_curr.recording and not self._at_max and self._motor_stopped():
            self.plot_curr.stop()            # Aufzeichnung endet bei Stillstand
        self.plot_curr.redraw(force=False)   # max. ~50x pro Sekunde neu zeichnen
        self.root.after(20, self._poll_queue)

    def _handle_rpm(self, rpm: int, now: float):
        if DEBUG_LOG and rpm != self._last_rpm:
            print(f"{now - self._log_t0:8.3f}  RX rpm {rpm}")
        self._last_rpm = rpm
        self._last_rpm_rx = now
        self._rpm_rx_times.append(now)
        # Aufzeichnung beenden, sobald der Motor nach dem Stopp steht
        if self.plot_curr.recording and not self._at_max and rpm == 0:
            self.plot_curr.stop()
        # Einzelwerte springen (z. B. 83 / 166 / 250) wegen der kurzen Messfenster
        # -> für Anzeige und Max-Drehzahl gleitend mitteln.
        # Für die Umdrehungszählung werden die Rohwerte integriert; dort heben
        # sich die Sprünge gegenseitig auf.
        self._rpm_hist.append(rpm)
        rpm_avg = round(sum(self._rpm_hist) / len(self._rpm_hist))
        if self._counting:
            dt = now - self._last_rpm_time
            self._total_revs += max(0, rpm) * dt / 60.0
            if len(self._rpm_hist) == RPM_SMOOTH_N and rpm_avg > self._max_rpm:
                self._max_rpm = rpm_avg
        self._last_rpm_time = now
        self._lbl_rpm.set(f"{rpm_avg} rpm")
        self.gauge_rpm.set_value(rpm_avg)
        if self._counting:
            self.gauge_rev.set_value(self._total_revs)
            if self._total_revs >= TARGET_REVS:
                self._stop_run()

    def _tare_current(self):
        """Nullpunkt: Mittel der Rohwerte der letzten Sekunde bei stehendem Motor."""
        now = time.time()
        vals = [r for t, r in self._idle_raw if t >= now - 1.0]
        if len(vals) >= 3:
            self._curr_offset_raw = sum(vals) / len(vals)
            print(f"Strom-Nullpunkt: {self._curr_offset_raw:.1f} (Rohwert) "
                  f"= {raw_to_ma(self._curr_offset_raw):.0f} mA")

    def _handle_current(self, raw: int, now: float):
        st = self._rx_stats
        st["raw_min"] = raw if st["raw_min"] is None else min(st["raw_min"], raw)
        st["raw_max"] = raw if st["raw_max"] is None else max(st["raw_max"], raw)
        # Rohwerte bei stehendem Motor für den Nullabgleich merken
        if not self._counting and not self._at_max and self._last_rpm == 0:
            self._idle_raw.append((now, raw))
        while self._idle_raw and self._idle_raw[0][0] < now - 2.0:
            self._idle_raw.popleft()
        current = raw_to_ma(raw - self._curr_offset_raw)
        self.plot_curr.add(now, current)
        if self._counting:
            self._curr_sum += current
            self._curr_n += 1
            avg = self._curr_sum / self._curr_n
            self._current_avg.set(f"{avg:.0f} mA")
            self.plot_curr.set_avg(avg)

    def _on_close(self):
        self._disconnect()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()