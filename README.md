# Lumi Bumi

Two glass cubes that send each other messages over the internet. Each cube has an ESP32-C3 Super Mini, a 0.96" SSD1306 OLED, a passive buzzer and two buttons. Messages travel through a free EMQX Cloud Serverless MQTT broker. A phone web page sends text, drawings and photos.

- `firmware/` MicroPython program for the cubes. Copy `config.example.py` to `config.py`, fill it in, and upload `main.py`, `ssd1306.py` and `config.py` to the board.
- `phone/index.html` (also served as the Pages site root) the phone page. Enter your broker settings in its Settings panel; nothing is stored in this repo.
- `docs/` build guide, housing ideas, cute bases and the gift setup guide.

Never commit `config.py`: it holds Wi-Fi and broker passwords.
