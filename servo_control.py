import tkinter as tk
from tkinter import ttk
import serial
import serial.tools.list_ports
import time

ser = None
relay_state = False

SERVO_TYPES = {
    "180° Robotic": 65,
    "DIGI HI TORQUE": 45,
}

MAX_DIFF = {
    "180° Robotic": 80,
    "DIGI HI TORQUE": 70,
}

def get_ports():
    ports = serial.tools.list_ports.comports()
    return [
        p.device for p in ports
        if p.vid is not None
    ]

def refresh_ports():
    ports = get_ports()
    port_combo["values"] = ports
    if ports:
        port_combo.set(ports[0])
    else:
        port_combo.set("")

def open_serial(port):
    global ser
    if ser and ser.is_open:
        ser.close()
    ser = serial.Serial(port=port, baudrate=115200, timeout=1)

def angle_to_value(new_angle, max_angle):
    # new_angle: 0°=physical min, 90°=neutral, 180°=physical max
    # shift to old centered system: old_angle = new_angle - 90
    old_angle = new_angle - 90
    return int(150 + (old_angle / max_angle) * 50)

def build_packet(addr, value):
    if addr == 0x10:
        checksum = (value + 0x12) & 0xFF
    elif addr == 0x20:
        checksum = (value + 0x22) & 0xFF
    else:
        raise ValueError("Unbekannte Adresse")
    return bytes([0x05, addr, 0x02, value, checksum, 0x00])

def on_type_change(*args):
    servo_type = type_combo.get()
    max_angle = SERVO_TYPES[servo_type]
    min_new = 90 - max_angle
    max_new = 90 + max_angle
    label1.config(text=f"Servo links ({min_new}° bis {max_new}°)")
    label2.config(text=f"Servo rechts ({min_new}° bis {max_new}°)")

    for entry in (entry1, entry2):
        try:
            current = float(entry.get())
            clamped = max(min_new, min(max_new, current))
            if clamped != current:
                entry.delete(0, tk.END)
                entry.insert(0, str(clamped))
        except ValueError:
            pass

def send_servo_links_preset():
    port = port_combo.get()
    if not port:
        status_label.config(text="Kein Port ausgewählt")
        return
    try:
        max_angle = SERVO_TYPES[type_combo.get()]
        # Old -34° → new 90 + (-34) = 56°
        val = angle_to_value(56, max_angle)
        if ser is None or not ser.is_open or ser.port != port:
            open_serial(port)
        ser.write(build_packet(0x10, val))
        status_label.config(text="Aufgabe 3 gestartet")
    except serial.SerialException as e:
        status_label.config(text=f"Serieller Fehler: {e}")

def send_values():
    port = port_combo.get()
    if not port:
        status_label.config(text="Kein Port ausgewählt")
        return

    try:
        angle1 = float(entry1.get())
        angle2 = float(entry2.get())
        servo_type = type_combo.get()

        max_angle = SERVO_TYPES[servo_type]
        max_diff = MAX_DIFF[servo_type]
        min_new = 90 - max_angle
        max_new = 90 + max_angle

        if not (min_new <= angle1 <= max_new):
            status_label.config(text=f"Servo links: {min_new}° bis {max_new}°")
            return

        if not (min_new <= angle2 <= max_new):
            status_label.config(text=f"Servo rechts: {min_new}° bis {max_new}°")
            return

        # Kollisionsprüfung NUR wenn rechter Servo über Neutralstellung (>90°)
        if angle2 > 90:
            if abs(angle1 - angle2) > max_diff:
                status_label.config(text="Kollisionsgefahr")
                return

        val1 = angle_to_value(angle1, max_angle)
        val2 = angle_to_value(angle2, max_angle)

        if ser is None or not ser.is_open or ser.port != port:
            open_serial(port)

        ser.write(build_packet(0x10, val1))
        time.sleep(0.05)
        ser.write(build_packet(0x20, val2))

        status_label.config(
            text=f"Gesendet: links={angle1}°, rechts={angle2}°"
        )

    except ValueError:
        status_label.config(text="Ungültige Eingabe")
    except serial.SerialException as e:
        status_label.config(text=f"Serieller Fehler: {e}")

def toggle_relay():
    global relay_state
    port = port_combo.get()
    if not port:
        status_label.config(text="Kein Port ausgewählt")
        return

    try:
        if ser is None or not ser.is_open or ser.port != port:
            open_serial(port)

        relay_state = not relay_state
        if relay_state:
            ser.write(b"\x05\x40\x02\xAA\xEC\x00")
            relay_btn.config(text="Relais: EIN", bg="green", fg="white")
        else:
            ser.write(b"\x05\x40\x02\xEE\x30\x00")
            relay_btn.config(text="Relais: AUS", bg="lightgray", fg="black")

        status_label.config(text=f"Relais {'ein' if relay_state else 'aus'}")

    except serial.SerialException as e:
        status_label.config(text=f"Serieller Fehler: {e}")

def on_closing():
    if ser and ser.is_open:
        ser.close()
    root.destroy()

root = tk.Tk()
root.title("Servo Steuerung")
root.geometry("420x430")

port_frame = tk.Frame(root)
port_frame.pack(pady=8, padx=10, fill="x")

tk.Label(port_frame, text="USB-Port:").pack(side="left")
port_combo = ttk.Combobox(port_frame, state="readonly", width=20)
port_combo.pack(side="left", padx=5)
tk.Button(port_frame, text="↻", command=refresh_ports).pack(side="left")
refresh_ports()

type_frame = tk.Frame(root)
type_frame.pack(pady=4, padx=10, fill="x")

tk.Label(type_frame, text="Servo-Typ:").pack(side="left")
type_combo = ttk.Combobox(type_frame, state="readonly",
                          values=list(SERVO_TYPES.keys()), width=20)
type_combo.set("180° Robotic")
type_combo.pack(side="left", padx=6)
type_combo.bind("<<ComboboxSelected>>", on_type_change)

label1 = tk.Label(root, text="Servo links (25° bis 155°)")
label1.pack(pady=(10, 2))
entry1 = tk.Entry(root, justify="center")
entry1.insert(0, "90")
entry1.pack()

label2 = tk.Label(root, text="Servo rechts (25° bis 155°)")
label2.pack(pady=(10, 2))
entry2 = tk.Entry(root, justify="center")
entry2.insert(0, "90")
entry2.pack()

tk.Button(root, text="Start", command=send_values).pack(pady=10)

relay_btn = tk.Label(root, text="Relais: AUS", width=18,
                     bg="lightgray", fg="black",
                     relief="raised", cursor="hand2")
relay_btn.bind("<Button-1>", lambda e: toggle_relay())
relay_btn.pack(pady=5)

tk.Button(root, text="Aufgabe 3", command=send_servo_links_preset).pack(pady=2)

root.bind('<Return>', lambda event: send_values())

status_label = tk.Label(root, text="")
status_label.pack(pady=5)

root.protocol("WM_DELETE_WINDOW", on_closing)

root.mainloop()
