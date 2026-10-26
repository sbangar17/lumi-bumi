# Lumi Bumi - main.py
# Runs automatically every time the board gets power.
#
# What it does
#   * joins Wi-Fi (or opens a setup hotspot if it has no Wi-Fi to join)
#   * keeps a secure MQTT connection to EMQX Cloud
#   * shows messages from the other cube / phone page on the OLED and beeps
#   * two buttons send quick messages (short press and long press = 4 messages)

import binascii
import gc
import json
import machine
import network
import socket
import sys
import time
import framebuf
from machine import Pin, I2C, PWM
from umqtt.simple import MQTTClient

import config
import ssd1306

# ---------------------------------------------------------------- identity ---
NAME = config.DEVICE_NAME.lower()
if NAME not in ("lumi", "bumi"):
    raise ValueError("DEVICE_NAME in config.py must be 'lumi' or 'bumi'")
PEER = "bumi" if NAME == "lumi" else "lumi"
# MicroPython strings have no .capitalize(), so build the display names by hand.
NAME_CAP = NAME[:1].upper() + NAME[1:]
PEER_CAP = PEER[:1].upper() + PEER[1:]

T_IN = config.TOPIC_PREFIX + "/to/" + NAME              # messages for me
T_OUT = config.TOPIC_PREFIX + "/to/" + PEER             # messages for the other cube
T_STATUS = config.TOPIC_PREFIX + "/status/" + NAME      # "online" / "offline"
T_PEER_STATUS = config.TOPIC_PREFIX + "/status/" + PEER

ticks_ms = time.ticks_ms
ticks_diff = time.ticks_diff
ticks_add = time.ticks_add

# ---------------------------------------------------------------- hardware ---
i2c = I2C(0, sda=Pin(config.PIN_SDA), scl=Pin(config.PIN_SCL), freq=400000)
oled = ssd1306.SSD1306_I2C(128, 64, i2c)
buz = PWM(Pin(config.PIN_BUZZER), freq=1000, duty_u16=0)
buttons = [Pin(config.PIN_BTN_A, Pin.IN, Pin.PULL_UP),
           Pin(config.PIN_BTN_B, Pin.IN, Pin.PULL_UP)]
led = Pin(8, Pin.OUT, value=1)          # on-board LED, 0 = on
wlan = network.WLAN(network.STA_IF)

# ------------------------------------------------------------------- sound ---
muted = not config.SOUND_ON

MELODY_TEXT = ((880, 80), (1175, 140))
MELODY_PRESET = ((784, 80), (988, 80), (1175, 80), (1568, 220))
MELODY_IMAGE = ((660, 80), (880, 80), (1319, 180))
MELODY_SENT = ((1047, 70), (1568, 90))
MELODY_ERR = ((300, 200), (200, 300))
MELODY_ONLINE = ((523, 70), (659, 70), (784, 70), (1047, 120))


def tone(freq, ms):
    if freq:
        buz.freq(freq)
        buz.duty_u16(32768)
    time.sleep_ms(ms)
    buz.duty_u16(0)


def play(notes):
    if muted:
        return
    for freq, ms in notes:
        tone(freq, ms)
        time.sleep_ms(30)


# ----------------------------------------------------------------- drawing ---
# 8x8 pixel pictures for the quick messages (one byte per row).
ICONS = {
    "smile": (0x3C, 0x42, 0xA5, 0x81, 0xA5, 0x99, 0x42, 0x3C),
    "heart": (0x66, 0xFF, 0xFF, 0xFF, 0x7E, 0x3C, 0x18, 0x00),
    "star": (0x18, 0x18, 0xFF, 0x7E, 0x3C, 0x7E, 0x66, 0x00),
    "moon": (0x38, 0x70, 0xE0, 0xE0, 0xE0, 0x70, 0x38, 0x00),
}
PRESETS = {
    "hi": ("smile", "Hi!"),
    "love": ("heart", "Love you!"),
    "miss": ("star", "Miss you!"),
    "night": ("moon", "Good night!"),
}


def clean(s):
    """The OLED font only has plain ASCII, so swap anything else for '?'."""
    return "".join(c if (32 <= ord(c) < 127 or c == "\n") else "?" for c in s)


def wrap(text, width=16):
    lines = []
    for para in text.split("\n"):
        line = ""
        for word in para.split(" "):
            while len(word) > width:
                if line:
                    lines.append(line)
                    line = ""
                lines.append(word[:width])
                word = word[width:]
            if not line:
                line = word
            elif len(line) + 1 + len(word) <= width:
                line += " " + word
            else:
                lines.append(line)
                line = word
        lines.append(line)
    return lines


def draw_icon(name, x, y, s):
    for r, row in enumerate(ICONS[name]):
        for c in range(8):
            if row & (0x80 >> c):
                oled.fill_rect(x + c * s, y + r * s, s, s, 1)


def draw_big(text, y, s):
    w = len(text) * 8
    fb = framebuf.FrameBuffer(bytearray(w), w, 8, framebuf.MONO_VLSB)
    fb.text(text, 0, 0, 1)
    x0 = (128 - w * s) // 2
    for x in range(w):
        for r in range(8):
            if fb.pixel(x, r):
                oled.fill_rect(x0 + x * s, y + r * s, s, s, 1)


def center(text, y):
    oled.text(text, (128 - 8 * len(text)) // 2, y)


def status(*lines):
    """Show a few centred lines (used for 'connecting...' style screens)."""
    oled.fill(0)
    y = (64 - (12 * len(lines) - 4)) // 2
    for line in lines:
        center(line[:16], y)
        y += 12
    oled.show()


def flash(*lines):
    status(*lines)
    time.sleep_ms(900)
    redraw()


# ------------------------------------------------------------------- views ---
view = {"kind": "idle"}
view_since = 0
beat = False
last_beat = 0
peer_online = False
asleep = False
last_activity = 0
unread = False
link_bad = False
last_send = ticks_add(ticks_ms(), -10000)
inbox = []


def draw_idle():
    draw_big(NAME.upper(), 4, 3)
    s = 2 if beat else 1
    draw_icon("heart", 64 - 4 * s, 42 - 4 * s, s)
    center(PEER_CAP + (" online" if peer_online else " away"), 55)
    if muted:
        oled.text("x", 120, 0)


def draw_text():
    oled.text("From " + view["sender"], 0, 0)
    oled.hline(0, 9, 128, 1)
    pages = view["pages"]
    for n, line in enumerate(pages[view["i"]]):
        oled.text(line, 0, 12 + n * 8)
    if len(pages) > 1:
        oled.text("%d/%d" % (view["i"] + 1, len(pages)), 0, 56)


def draw_preset():
    if view["id"] in PRESETS:
        icon, label = PRESETS[view["id"]]
        lines = [label]
    else:                                   # a custom message typed on the phone page
        icon, lines = view["icon"], wrap(view["label"])[:2]
    if len(lines) > 1:                      # two-line label: smaller picture
        draw_icon(icon, 52, 0, 3)
        for n, line in enumerate(lines):
            center(line, 28 + n * 10)
    else:
        draw_icon(icon, 48, 2, 4)
        center(lines[0], 40)
    center("from " + view["sender"], 54)


def redraw():
    oled.fill(0)
    kind = view["kind"]
    if kind == "idle":
        draw_idle()
    elif kind == "text":
        draw_text()
    elif kind == "preset":
        draw_preset()
    elif kind == "image":
        oled.buffer[:] = view["data"]
    oled.show()


def set_view(**kw):
    global view, view_since
    view = kw
    view_since = ticks_ms()
    redraw()


def wake():
    """Mark activity. Returns True if the screen had been asleep."""
    global asleep, last_activity
    last_activity = ticks_ms()
    if asleep:
        oled.poweron()
        asleep = False
        return True
    return False


# ------------------------------------------------------------ Wi-Fi + portal ---
def load_wifi():
    try:
        with open("wifi.json") as f:
            d = json.load(f)
        return d["ssid"], d["password"]
    except (OSError, ValueError, KeyError):
        return config.WIFI_SSID, config.WIFI_PASSWORD


def wifi_connect(ssid, password, timeout_s=20):
    wlan.active(True)
    txp = getattr(config, "WIFI_TXPOWER", None)
    if txp:
        wlan.config(txpower=txp)
    wlan.connect(ssid, password)
    t0 = ticks_ms()
    while not wlan.isconnected():
        if ticks_diff(ticks_ms(), t0) > timeout_s * 1000:
            return False
        time.sleep_ms(200)
    return True


def unquote(s):
    b = s.replace("+", " ").encode()
    out = bytearray()
    i = 0
    while i < len(b):
        if b[i] == 37 and i + 2 < len(b):
            try:
                out.append(int(b[i + 1:i + 3].decode(), 16))
                i += 3
                continue
            except ValueError:
                pass
        out.append(b[i])
        i += 1
    return out.decode()


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace('"', "&quot;")


PORTAL_HTML = """<!DOCTYPE html><html><head>
<meta name=viewport content="width=device-width,initial-scale=1"><title>@NAME@ WiFi</title>
<style>body{font-family:sans-serif;max-width:380px;margin:24px auto;padding:0 14px}
input,button{width:100%;font-size:18px;padding:12px;margin:6px 0;box-sizing:border-box}
button{background:#2563eb;color:#fff;border:0;border-radius:8px}</style></head><body>
<h2>@NAME@ WiFi setup</h2>
<form method=POST action=/save>
<label>WiFi name (2.4 GHz only)</label>
<input name=ssid list=nets required autocapitalize=off autocorrect=off>
<datalist id=nets>@NETS@</datalist>
<label>WiFi password</label>
<input name=pw type=password>
<button>Save and restart</button>
</form></body></html>"""


def read_request(cl):
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = cl.recv(512)
        if not chunk:
            break
        data += chunk
    head, _, body = data.partition(b"\r\n\r\n")
    need = 0
    for line in head.split(b"\r\n"):
        if line.lower().startswith(b"content-length:"):
            need = int(line.split(b":")[1])
    while len(body) < need:
        chunk = cl.recv(512)
        if not chunk:
            break
        body += chunk
    return head, body


def portal():
    """Open a hotspot + tiny web page so Wi-Fi details can be typed on a phone."""
    ssids = []
    try:
        wlan.active(True)
        for r in wlan.scan():
            n = r[0].decode("utf-8", "ignore")
            if n and n not in ssids:
                ssids.append(n)
    except Exception as e:
        print("scan failed:", e)
    nets = "".join('<option value="%s">' % esc(n) for n in ssids[:15])

    ap_name = "LumiBumi-Setup"
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    try:
        ap.config(essid=ap_name, security=0)
    except Exception:
        ap.config(essid=ap_name, authmode=0)
    status("WiFi setup", "Join WiFi:", ap_name, "Open 192.168.4.1")

    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", 80))
    srv.listen(2)
    page = PORTAL_HTML.replace("@NAME@", NAME_CAP).replace("@NETS@", nets)
    while True:
        cl, _ = srv.accept()
        try:
            head, body = read_request(cl)
            if head.startswith(b"POST /save"):
                form = {}
                for pair in body.decode().split("&"):
                    k, _, v = pair.partition("=")
                    form[k] = unquote(v)
                with open("wifi.json", "w") as f:
                    json.dump({"ssid": form.get("ssid", ""), "password": form.get("pw", "")}, f)
                cl.sendall(b"HTTP/1.0 200 OK\r\nContent-Type: text/html\r\nConnection: close\r\n\r\n"
                           b"<h2>Saved! Restarting...</h2>")
                cl.close()
                status("Saved!", "Restarting...")
                time.sleep(1)
                machine.reset()
            cl.sendall(b"HTTP/1.0 200 OK\r\nContent-Type: text/html\r\nConnection: close\r\n\r\n"
                       + page.encode())
        except Exception as e:
            print("portal error:", e)
        finally:
            try:
                cl.close()
            except Exception:
                pass


# -------------------------------------------------------------------- MQTT ---
client = None


def make_client():
    cid = "lumibumi-" + NAME
    args = dict(user=config.MQTT_USER, password=config.MQTT_PASSWORD, keepalive=60)
    try:
        # older MicroPython: ssl=True plus ssl_params
        return MQTTClient(cid, config.MQTT_HOST, config.MQTT_PORT, ssl=True,
                          ssl_params={"server_hostname": config.MQTT_HOST}, **args)
    except TypeError:
        # newer MicroPython: pass an SSLContext
        import ssl
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.verify_mode = ssl.CERT_NONE   # encrypted, but server identity not checked
        return MQTTClient(cid, config.MQTT_HOST, config.MQTT_PORT, ssl=ctx, **args)


def on_message(topic, msg):
    global peer_online
    if topic.decode() == T_PEER_STATUS:
        peer_online = (msg == b"online")
        return
    if len(inbox) < 5:
        inbox.append(msg)


def connect_all():
    """Block until Wi-Fi and MQTT are both up. Retries forever."""
    global client, link_bad
    if client:
        try:
            client.sock.close()
        except Exception:
            pass
        client = None
    ssid, password = load_wifi()
    while True:
        if not wlan.isconnected():
            status("Joining WiFi", ssid[:16])
            if not wifi_connect(ssid, password):
                status("WiFi not found", "retrying...")
                time.sleep(5)
                continue
        status("Connecting", "to the cloud")
        try:
            c = make_client()
            c.set_callback(on_message)
            c.set_last_will(T_STATUS, b"offline", retain=True)
            c.connect(clean_session=False)
            c.subscribe(T_IN, qos=1)
            c.subscribe(T_PEER_STATUS)
            c.publish(T_STATUS, b"online", retain=True)
            client = c
            link_bad = False
            return
        except Exception as e:
            print("cloud connect failed:", e)
            status("Cloud failed", "retrying...")
            time.sleep(5)


# ---------------------------------------------------------------- messages ---
def handle_inbox():
    global unread
    while inbox:
        raw = inbox.pop(0)
        try:
            m = json.loads(raw)
            kind = m.get("type")
            sender = clean(str(m.get("from", PEER_CAP)))[:11] or PEER_CAP
            if kind == "text":
                text = clean(str(m.get("text", "")))[:300]
                if not text.strip():
                    continue
                lines = wrap(text)
                pages = [lines[i:i + 5] for i in range(0, len(lines), 5)]
                wake()
                set_view(kind="text", sender=sender, pages=pages, i=0,
                         flip=ticks_add(ticks_ms(), config.PAGE_MS))
                play(MELODY_TEXT)
            elif kind == "preset":
                pid = m.get("id")
                icon, label = "heart", ""
                if pid == "custom":
                    label = clean(str(m.get("label", "")))[:32].strip()
                    icon = m.get("icon") if m.get("icon") in ICONS else "heart"
                    if not label:
                        continue
                elif pid not in PRESETS:
                    continue
                wake()
                set_view(kind="preset", id=pid, sender=sender, icon=icon, label=label)
                play(MELODY_PRESET)
            elif kind == "img":
                data = binascii.a2b_base64(m["data"])
                if len(data) != 1024:
                    continue
                wake()
                set_view(kind="image", data=data)
                play(MELODY_IMAGE)
            else:
                continue
            unread = True
        except Exception as e:
            print("bad message:", e)
        gc.collect()


def send_preset(pid):
    global last_send, link_bad
    now = ticks_ms()
    if ticks_diff(now, last_send) < config.SEND_COOLDOWN_MS:
        return
    last_send = now
    msg = json.dumps({"type": "preset", "id": pid, "from": NAME_CAP})
    try:
        client.publish(T_OUT, msg, qos=1)
    except Exception as e:
        print("send failed:", e)
        link_bad = True
        flash("Not sent :(", "no connection")
        play(MELODY_ERR)
        return
    status("Sent!", PRESETS[pid][1])        # screen and chime together
    play(MELODY_SENT)
    time.sleep_ms(1300)
    redraw()


def toggle_mute():
    global muted
    muted = not muted
    flash("Sound", "OFF" if muted else "ON")
    if not muted:
        tone(1047, 80)


# ----------------------------------------------------------------- buttons ---
held = [None, None]
both_since = None
swallow = False


def poll_buttons():
    """Short press = preset, long press (~1 s) = second preset, both held = mute."""
    global both_since, swallow, unread
    now = ticks_ms()
    down = [buttons[0].value() == 0, buttons[1].value() == 0]

    if down[0] and down[1]:
        if both_since is None:
            both_since = now
        elif not swallow and ticks_diff(now, both_since) > 1500:
            swallow = True
            wake()
            toggle_mute()
        for i in (0, 1):
            if held[i] is None:
                held[i] = now
        return
    both_since = None

    for i in (0, 1):
        if down[i]:
            if held[i] is None:
                held[i] = now
        elif held[i] is not None:
            dur = ticks_diff(now, held[i])
            held[i] = None
            if swallow or dur < 40:
                continue
            if wake():          # first press only wakes the screen
                continue
            unread = False
            long_press = dur >= 800
            if i == 0:
                send_preset(config.PRESET_A_LONG if long_press else config.PRESET_A)
            else:
                send_preset(config.PRESET_B_LONG if long_press else config.PRESET_B)
    if not down[0] and not down[1]:
        swallow = False


# --------------------------------------------------------------- main loop ---
def housekeeping(now):
    global beat, last_beat, unread, asleep
    kind = view["kind"]
    if asleep:
        return
    if kind == "text" and len(view["pages"]) > 1 and ticks_diff(now, view["flip"]) >= 0:
        view["i"] = (view["i"] + 1) % len(view["pages"])
        view["flip"] = ticks_add(now, config.PAGE_MS)
        redraw()
    elif kind == "idle":
        if ticks_diff(now, last_beat) > 1000:
            beat = not beat
            last_beat = now
            redraw()
    elif ticks_diff(now, view_since) > config.MSG_HOLD_MS:
        unread = False
        set_view(kind="idle")
    if ticks_diff(now, last_activity) > config.SLEEP_AFTER_MS:
        oled.poweroff()
        asleep = True


def main():
    global peer_online, last_activity
    status("Lumi Bumi", NAME_CAP + " cube")
    time.sleep_ms(1500)

    setup_wanted = buttons[0].value() == 0 and buttons[1].value() == 0
    ssid, _ = load_wifi()
    if setup_wanted or not ssid:
        portal()                                  # never returns

    connect_all()
    play(MELODY_ONLINE)
    last_activity = ticks_ms()
    set_view(kind="idle")
    last_ping = ticks_ms()

    while True:
        now = ticks_ms()
        try:
            if link_bad:
                raise OSError("link marked bad")
            client.check_msg()
            if ticks_diff(now, last_ping) > 30000:
                client.ping()
                last_ping = now
        except Exception as e:
            print("link lost:", e)
            peer_online = False
            connect_all()
            last_ping = ticks_ms()
            redraw()
            continue

        poll_buttons()
        handle_inbox()
        housekeeping(now)
        led.value(0 if (unread and (now // 500) % 2 == 0) else 1)
        time.sleep_ms(20)


try:
    main()
except Exception as e:
    sys.print_exception(e)
    try:
        status("Oops!", "Restarting...")
    except Exception:
        pass
    time.sleep(10)
    machine.reset()
