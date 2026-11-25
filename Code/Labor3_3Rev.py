#python3.12 /Users/robin/Documents/Arbeit/Labor3_Aktorik/Code/Labor3_3Rev.py


import serial
import serial.tools.list_ports
import threading
import queue
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
TARGET_REVS    = 10

# Strom aus UART-Rohwert: 3,3 V / 4096 * Rohwert * 10  -> Ergebnis in A
ADC_VREF       = 3.3
ADC_STEPS      = 4096
CURRENT_GAIN   = 100


def raw_to_ma(raw: int) -> float:
    amps = (ADC_VREF / ADC_STEPS) * raw * CURRENT_GAIN
    return amps * 1000.0
    print(amps)

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


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Labor3")
        self.root.configure(bg="#1e1e2e")
        self.root.resizable(False, False)
        self.root.geometry("480x840")

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
        self._rx_queue = queue.Queue()   # Lese-Thread -> Hauptthread

        self._build_ui()
        self._poll_queue()

    def _build_ui(self):
        outer = tk.Frame(self.root, bg="#1e1e2e", padx=24, pady=20)
        outer.pack(fill=tk.BOTH, expand=True)

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
            self._ser = serial.Serial(port=port, baudrate=BAUD, timeout=1)
            self._ser.reset_input_buffer()   # alte/halbe Bytes im Puffer verwerfen
            self._running = True
            self._thread = threading.Thread(target=self._read_loop, daemon=True)
            self._thread.start()
            self._ser.write(CMD_INIT_SPEED)
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
        self._lbl_rpm.set("0 rpm")
        self._lbl_max.set("– rpm")
        self._current_avg.set("– mA")
        self.gauge_rpm.set_value(0)
        self.gauge_rev.set_value(0)
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
        self._curr_sum = 0.0      # Strom-Mittelwert bei neuem Start zurücksetzen
        self._curr_n = 0
        self._last_rpm_time = time.time()
        self._counting = True
        self._at_max = True
        self._btn_start.config(state="disabled")
        self._btn_abort.config(state="normal")
        self._lbl_max.set("– rpm")
        self._current_avg.set("– mA")
        self.gauge_rev.set_value(0)
        self._status.set("Motor läuft – zähle Umdrehungen …")
        self._ser.write(b"\x00" + CMD_INIT_SPEED)
        self._ser.write(b"\x00" + CMD_MAX_SPEED)
        self._keep_alive()

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
            self._ser.write(b"\x00" + CMD_STOP)   # führendes 0x00 = Resync
        except Exception as e:
            print(f"STOP-Schreibfehler: {e}")
        if tries > 1 and self._last_rpm != 0:
            self.root.after(100, self._send_stop, tries - 1)
        elif self._last_rpm != 0:
            self._status.set("Warnung: Motor meldet nach STOP noch Drehzahl!")

    def _keep_alive(self):
        if not self._at_max or not self._running:
            return
        if self._ser and self._ser.is_open:
            try:
                self._ser.write(b"\x00" + CMD_MAX_SPEED)
            except Exception as e:
                print(f"Schreibfehler: {e}")
        self.root.after(500, self._keep_alive)

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
            print(f"Frame-Fehler: {e} | {len(raw)} Bytes: {raw.hex(' ')}")

    def _poll_queue(self):
        # Läuft im Hauptthread: verarbeitet alle empfangenen Werte.
        try:
            while True:
                name, value, now = self._rx_queue.get_nowait()
                if name == "rpm":
                    self._handle_rpm(value, now)
                elif name == "current":
                    self._handle_current(value)
        except queue.Empty:
            pass
        self.root.after(20, self._poll_queue)

    def _handle_rpm(self, rpm: int, now: float):
        self._last_rpm = rpm
        if self._counting:
            dt = now - self._last_rpm_time
            self._total_revs += max(0, rpm) * dt / 60.0
            if rpm > self._max_rpm:
                self._max_rpm = rpm
        self._last_rpm_time = now
        self._lbl_rpm.set(f"{rpm} rpm")
        self.gauge_rpm.set_value(rpm)
        if self._counting:
            self.gauge_rev.set_value(self._total_revs)
            if self._total_revs >= TARGET_REVS:
                self._stop_run()

    def _handle_current(self, raw: int):
        current = raw_to_ma(raw)
        print(raw)
        if self._counting:
            self._curr_sum += current
            self._curr_n += 1
            self._current_avg.set(f"{self._curr_sum / self._curr_n:.0f} mA")

    def _on_close(self):
        self._disconnect()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()