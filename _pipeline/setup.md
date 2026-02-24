# RPi5 DietPi Mail Pipeline Setup

Complete setup for running the mail scanning pipeline on a Raspberry Pi 5 running DietPi.

**Architecture:**
```
Epson RR-600W ──SMB──▶ RPi5 /opt/homemail/Raw/
                          │
                     mail_pipeline.py (systemd)
                          │
                          ▼
                    /opt/homemail/Organized/  +  TODO.md
                          │
                    OwnCloud desktop client
                          │
                          ▼
                    OwnCloud server ──▶ all devices
```

---

## 1. Create the directory structure

```bash
sudo mkdir -p /opt/homemail/{Raw,Organized,Reports}
sudo chown -R dietpi:dietpi /opt/homemail
```

## 2. Install Samba (scanner upload target)

```bash
sudo apt update
sudo apt install -y samba
```

Add the share config:

```bash
sudo tee -a /etc/samba/smb.conf > /dev/null << 'EOF'

[HomeMail]
   path = /opt/homemail/Raw
   browseable = yes
   writable = yes
   guest ok = no
   valid users = scanner
   create mask = 0644
   directory mask = 0755
   force user = dietpi
   force group = dietpi
EOF
```

Create the scanner user (this is the login the Epson will use):

```bash
sudo useradd -M -s /usr/sbin/nologin scanner
sudo smbpasswd -a scanner
# Pick a password — must be ≤20 characters for the Epson
```

Restart Samba:

```bash
sudo systemctl restart smbd
sudo systemctl enable smbd
```

### Test from another machine

```bash
# From Windows (in File Explorer address bar):
\\<PI_IP>\HomeMail

# From Linux:
smbclient //PI_IP/HomeMail -U scanner
```

### Configure the Epson RR-600W

In the scanner's web interface (http://SCANNER_IP):

1. **Network Folder / SMB** settings
2. Save location: `\\PI_IP\HomeMail`
3. Username: `scanner`
4. Password: (what you set above)
5. Test the connection

---

## 3. Install pipeline dependencies

```bash
sudo apt install -y python3-pip tesseract-ocr

pip install pymupdf anthropic Pillow pytesseract --break-system-packages
```

Verify Tesseract:

```bash
tesseract --version
```

## 4. Deploy the pipeline script

Copy `mail_pipeline.py` and `TODO.html` to the Pi:

```bash
# From your local machine:
scp mail_pipeline.py dietpi@PI_IP:/opt/homemail/
scp TODO.html dietpi@PI_IP:/opt/homemail/

# On the Pi, make it executable:
chmod +x /opt/homemail/mail_pipeline.py
```

### Update paths in the script

Edit the CONFIG block near the top of `mail_pipeline.py`:

```python
CONFIG = {
    "bronze_folder": "/opt/homemail/Raw",
    "silver_folder": "/opt/homemail/Organized",
    "tracking_folder": "/opt/homemail/Reports",
    # ... rest stays the same
}
```

### Quick test

```bash
export ANTHROPIC_API_KEY="sk-ant-your-key-here"
cd /opt/homemail
python3 mail_pipeline.py --batch -v
```

---

## 5. Set up the systemd service

Create the service file:

```bash
sudo tee /etc/systemd/system/homemail.service > /dev/null << 'EOF'
[Unit]
Description=HomeMail Scanner Pipeline
After=network-online.target smbd.service
Wants=network-online.target

[Service]
Type=simple
User=dietpi
Group=dietpi
WorkingDirectory=/opt/homemail
ExecStart=/usr/bin/python3 /opt/homemail/mail_pipeline.py
Restart=on-failure
RestartSec=30

# API key for AI classification
Environment=ANTHROPIC_API_KEY=sk-ant-your-key-here

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=homemail

[Install]
WantedBy=multi-user.target
EOF
```

> **Security note:** For production, use a credentials file instead of
> putting the API key directly in the unit file:
>
> ```bash
> echo "ANTHROPIC_API_KEY=sk-ant-your-key-here" | sudo tee /opt/homemail/.env
> sudo chmod 600 /opt/homemail/.env
> sudo chown dietpi:dietpi /opt/homemail/.env
> ```
>
> Then replace the `Environment=` line with:
> ```ini
> EnvironmentFile=/opt/homemail/.env
> ```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable homemail
sudo systemctl start homemail
```

### Useful commands

```bash
# Check status
sudo systemctl status homemail

# Watch live logs
journalctl -u homemail -f

# Restart after config changes
sudo systemctl restart homemail

# View recent logs
journalctl -u homemail --since "1 hour ago"
```

---

## 6. Install OwnCloud desktop client

DietPi has a software installer for this:

```bash
sudo dietpi-software install 20
# If OwnCloud client isn't available, install manually:
# sudo apt install -y owncloud-client
```

If that doesn't work, install the AppImage or CLI sync tool:

```bash
# CLI option (headless-friendly):
sudo apt install -y owncloud-client-cmd

# Sync command (add to cron):
owncloudcmd -u USERNAME -p PASSWORD /opt/homemail https://your-owncloud-server.com/remote.php/dav
```

### Option A: GUI client (if desktop available)

```bash
owncloud &
# Configure: server URL, credentials, local folder = /opt/homemail
```

### Option B: Headless cron sync (recommended for DietPi)

Create a sync script:

```bash
tee /opt/homemail/sync.sh > /dev/null << 'SCRIPT'
#!/bin/bash
owncloudcmd \
  --user "YOUR_USERNAME" \
  --password "YOUR_PASSWORD" \
  --non-interactive \
  /opt/homemail \
  https://your-owncloud-server.com/remote.php/dav/files/YOUR_USERNAME/HomeMail
SCRIPT

chmod +x /opt/homemail/sync.sh
chmod 700 /opt/homemail/sync.sh  # protect credentials
```

Add to cron (sync every 5 minutes):

```bash
(crontab -l 2>/dev/null; echo "*/5 * * * * /opt/homemail/sync.sh >> /opt/homemail/Reports/sync.log 2>&1") | crontab -
```

---

## 7. Firewall (if applicable)

If you run `ufw` or `iptables`, open Samba and the dashboard:

```bash
sudo ufw allow from 192.168.1.0/24 to any port 445    # SMB
sudo ufw allow from 192.168.1.0/24 to any port 8080   # Dashboard
```

Adjust the subnet to match your network.

---

## 8. Verify the full flow

1. **Scan a document** on the Epson RR-600W
2. **Check it arrived:**
   ```bash
   ls -la /opt/homemail/Raw/
   ```
3. **Check pipeline picked it up:**
   ```bash
   journalctl -u homemail --since "5 minutes ago"
   ```
4. **Check output:**
   ```bash
   ls -la /opt/homemail/Organized/
   cat /opt/homemail/TODO.md
   ```
5. **Open dashboard:**
   Visit `http://PI_IP:8080/TODO.html` from any device on your network
6. **Check OwnCloud sync:**
   Wait for cron (or run `/opt/homemail/sync.sh` manually), then check OwnCloud web

---

## Troubleshooting

**Scanner can't connect to SMB share:**
- Verify Pi IP: `hostname -I`
- Test Samba: `smbclient //localhost/HomeMail -U scanner`
- Check Samba logs: `sudo journalctl -u smbd`
- Ensure scanner is on same network/VLAN as Pi
- Remember: password must be ≤20 characters

**Pipeline doesn't detect new files:**
- Check service is running: `systemctl status homemail`
- Watch logs: `journalctl -u homemail -f`
- Verify file permissions: `ls -la /opt/homemail/Raw/`

**OCR not working:**
- Verify: `tesseract --version`
- Check: `python3 -c "import pytesseract; print(pytesseract.get_tesseract_version())"`

**AI classification not working (all files named "Unsorted"):**
- Check API key: `journalctl -u homemail | grep "AI renaming"`
- Should show `AI renaming: ON (key: ...xxxx)`
- If not, check `.env` file and restart service

**OwnCloud sync issues:**
- Manual test: `/opt/homemail/sync.sh`
- Check sync log: `cat /opt/homemail/Reports/sync.log`
- Verify credentials and server URL

**Dashboard not loading:**
- Check port: `curl http://localhost:8080/TODO.html`
- Check if TODO.md exists: `cat /opt/homemail/TODO.md`
- Verify pipeline is running (dashboard server starts with pipeline)
