import serial
import serial.tools.list_ports

BAUD = 115200

CMD_INIT = b"\x02\x63\x04\x90\x01\xF4\x00"

def list_ports():
    ports = [p for p in serial.tools.list_ports.comports() if p.vid is not None]
    return ports

def main():
    ports = list_ports()
    if not ports:
        print("Keine USB-Geräte gefunden!")
        return

    print("Verfügbare Ports:")
    for i, p in enumerate(ports):
        print(f"  [{i}] {p.device}  ({p.description})")

    auswahl = input("Port-Nummer oder Name wählen: ").strip()
    if auswahl.startswith("/dev") or auswahl.startswith("COM"):
        port = auswahl
    else:
        port = ports[int(auswahl)].device

    print(f"\nVerbinde mit {port} @ {BAUD} ...")
    ser = serial.Serial(port=port, baudrate=BAUD, timeout=2)
    print("Verbunden.\n")

    print("Sende CMD_INIT ...")
    ser.write(CMD_INIT)
    print("CMD_INIT gesendet.\n")

    print("Warte auf Daten (Strg+C zum Beenden)...\n")
    print("Ändere die Drehzahl am Netzteil während das Programm läuft!\n")

    buf = bytearray()
    frame_count = 0
    last_decoded = None

    def cobs_decode(data):
        output = bytearray()
        idx = 0
        while idx < len(data):
            code = data[idx]; idx += 1
            if code == 0: raise ValueError()
            for _ in range(code - 1):
                output.append(data[idx]); idx += 1
            if code < 0xFF and idx < len(data):
                output.append(0)
        return bytes(output)

    try:
        while True:
            byte = ser.read(1)
            if not byte:
                print("[Timeout – keine Daten]")
                continue

            b = byte[0]
            if b == 0x00:
                if buf:
                    frame_count += 1
                    raw = bytes(buf)
                    buf.clear()

                    try:
                        decoded = cobs_decode(raw)
                    except Exception:
                        print(f"Frame #{frame_count:>3}: [COBS-Fehler] raw={raw.hex(' ')}")
                        continue

                    code = int.from_bytes(decoded[0:2], "little")
                    hex_str = decoded.hex(' ')

                    # Vergleich mit letztem Frame – geänderte Bytes markieren
                    diff = ""
                    if last_decoded and len(last_decoded) == len(decoded):
                        changed = [i for i in range(len(decoded)) if decoded[i] != last_decoded[i]]
                        diff = f"  ← Byte(s) geändert: {changed}"

                    print(f"Frame #{frame_count:>3} | code=0x{code:04X} | {hex_str}{diff}")

                    # Alle 32-bit Werte an jeder Position ausgeben
                    if frame_count % 10 == 1:
                        print("  [int32 an jeder Position:", end="")
                        for i in range(len(decoded) - 3):
                            v = int.from_bytes(decoded[i:i+4], "little", signed=True)
                            print(f" [{i}]={v}", end="")
                        print("]")

                    last_decoded = decoded
            else:
                buf.append(b)

    except KeyboardInterrupt:
        print(f"\n\nBeendet. {frame_count} Frames empfangen.")
        ser.close()

if __name__ == "__main__":
    main()
