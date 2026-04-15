# Cita Previa Extranjería — Appointment Monitor

Automatically monitors the Spanish immigration appointment portal
(`icp.administracionelectronica.gob.es`) and sends a **Telegram alert**
the moment a slot opens for fingerprinting (Toma de Huellas) or any other
Policía Nacional procedure.

---

## How it works

The bot navigates the site like a human, step by step:

1. Selects the target **province** (e.g. Barcelona)
2. Selects the target **procedure** (e.g. TOMA DE HUELLAS)
3. Chooses **"Presentación sin Cl@ve"** (no digital certificate needed)
4. Fills in your **NIE, name and nationality**
5. Clicks **"Solicitar Cita"** and reads the result
6. If slots are found → fires a **Telegram message** with a direct link
7. Sleeps a random interval, then repeats

---

## Quick start

### 1 — Install dependencies

```bash
pip install -r requirements.txt
playwright install chromium
```

### 2 — Configure

```bash
cp .env.example .env
# Edit .env with your values (NIE, NAME, NATIONALITY, BOT_TOKEN, CHAT_ID …)
```

### 3 — Test a single run

```bash
RUN_ONCE=true DRY_RUN=true python main.py
```

`DRY_RUN=true` means availability is checked and logged but **no Telegram
message is sent**.  Use this to confirm the whole flow works before leaving
the bot running.

### 4 — Run continuously

```bash
python main.py
```

---

## Configuration reference

All settings live in `.env`.  See `.env.example` for every option with
descriptions.  The most important ones:

| Variable | Description |
| --- | --- |
| `BOT_TOKEN` | Telegram bot token from @BotFather |
| `CHAT_ID` | Your Telegram chat/user ID |
| `NIE` | Your NIE number (e.g. `Y1234567X`) |
| `NAME` | Full name as on your NIE |
| `NATIONALITY` | Country label **exactly as it appears** in the dropdown |
| `PROXIES_FILE` | Path to a file with one proxy URL per line (recommended for large proxy pools) |
| `TARGET_PROVINCE` | Province to monitor (default: `Barcelona`) |
| `TARGET_PROCEDURE_TEXT` | Partial procedure name (default: `TOMA DE HUELLAS`) |
| `HEADLESS` | `false` to watch the browser (debug), `true` for server use |
| `RUN_ONCE` | `true` = single run then exit (testing) |
| `DRY_RUN` | `true` = no Telegram alerts sent |

---

## Oxylabs proxy setup (recommended)

The Spanish government site aggressively rate-limits and blocks automated
traffic.  Using **Oxylabs Residential Proxies** with Spanish IPs (`ES`) gives
the best results.

```env
OXYLABS_ENABLED=true
OXYLABS_USERNAME=your_customer_username   # without "customer-" prefix
OXYLABS_PASSWORD=your_password
OXYLABS_COUNTRY=ES
OXYLABS_STICKY_SESSION=true
```

The bot automatically rotates the session ID every few runs to avoid
fingerprinting, and forces an immediate rotation whenever it detects a
block or CAPTCHA challenge.

## Using your own proxy list (10-20 proxies)

If you already have full proxy URLs, put them in a file (one per line), for example `proxies.txt`:

```txt
http://user1:pass1@host:port
http://user2:pass2@host:port
http://user3:pass3@host:port
```

Then set in `.env`:

```env
OXYLABS_ENABLED=false
PROXIES_FILE=proxies.txt
PROXY_ROTATE_EVERY=2
```

Notes:

- `PROXIES_FILE` is best for large lists.
- Inline `PROXIES` is still supported (comma/semicolon/newline separated).
- Keep `http://` scheme for Oxylabs residential endpoints.

---

## Telegram alert types

| Situation | Alert sent? | Message |
| --- | --- | --- |
| Open slots (sin Cl@ve) | ✅ Yes | Direct booking link |
| Slots via Cl@ve only | ✅ Yes | Note that Cl@ve login is required |
| No slots at all | ❌ No | — |
| Blocked / CAPTCHA | ❌ No | Logged to console |

---

## Debugging

Set `SAVE_DEBUG_ARTIFACTS=true` to save a screenshot and HTML snapshot to
`./artifacts/` whenever something goes wrong.  Set `HEADLESS=false` to
watch the browser in real time.

---

## Running as a service (Linux)

```ini
# /etc/systemd/system/citabot.service
[Unit]
Description=Cita Previa appointment monitor
After=network.target

[Service]
WorkingDirectory=/path/to/project
EnvironmentFile=/path/to/project/.env
ExecStart=/usr/bin/python3 main.py
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now citabot
sudo journalctl -u citabot -f
```
