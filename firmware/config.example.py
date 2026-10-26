# Lumi Bumi - settings. COPY this file to config.py and fill it in (config.py is git-ignored). This is the ONLY file you normally need to edit.
# Each cube gets its own copy: change DEVICE_NAME, MQTT_USER and MQTT_PASSWORD.

# --- Who is this cube? -------------------------------------------------------
DEVICE_NAME = "lumi"            # "lumi" or "bumi" (lower case)

# --- Wi-Fi (2.4 GHz only) ----------------------------------------------------
# Leave blank to use the setup hotspot instead (hold both buttons while
# plugging in, or boot with no Wi-Fi saved). Whatever the hotspot saves wins.
WIFI_SSID = ""
WIFI_PASSWORD = ""
WIFI_TXPOWER = None             # try 8.5 if the board struggles to join Wi-Fi

# --- EMQX Cloud Serverless (copy from your EMQX console) ---------------------
MQTT_HOST = "xxxxxxxx.ala.us-east-1.emqxsl.com"
MQTT_PORT = 8883                # MQTT over TLS
MQTT_USER = "Lumi"              # one EMQX user per device
MQTT_PASSWORD = "your-password-here"

# Both cubes AND the phone page must use exactly the same prefix.
# Make it long and random-ish; it works like a private room name.
TOPIC_PREFIX = "lumibumi/pick-your-own-random-words"

# --- Pins (see the wiring diagram) --------------------------------------------
PIN_SDA = 5
PIN_SCL = 6
PIN_BUZZER = 7
PIN_BTN_A = 3
PIN_BTN_B = 4

# --- Quick-send messages: short press / long press (hold ~1 second) ----------
# Choose from: "hi", "love", "miss", "night", "fam1", "fam2", "fam3"
PRESET_A = "hi"
PRESET_A_LONG = "miss"
PRESET_B = "love"
PRESET_B_LONG = "night"

# --- Behaviour ----------------------------------------------------------------
SOUND_ON = True                 # hold both buttons ~1.5 s to mute/unmute
SEND_COOLDOWN_MS = 2000         # minimum gap between two button sends
PAGE_MS = 3500                  # how long each page of a long message shows
MSG_HOLD_MS = 5 * 60 * 1000     # show a message this long, then back to idle
SLEEP_AFTER_MS = 15 * 60 * 1000 # turn the screen off after this much quiet
