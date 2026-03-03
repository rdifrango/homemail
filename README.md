# HomeMail

AI-powered mail scanning and organization pipeline.

Leave a scanner by your mail pile, feed in your documents, and walk away. It
automatically splits, names, and organizes everything, then gives you a dashboard
summarizing what needs your attention — with the original PDFs a click away.

Works with any scanner that uploads to a Samba share. Uses Claude AI for
classification and includes a web portal for reviewing everything.

```
Epson RR-600W ──SMB──▶ RPi5 /opt/homemail/Raw/
                          │
                     pipeline.py (systemd)
                          │
                          ▼
                    /opt/homemail/Organized/  +  Reports/TODO.md
                          │
                    OwnCloud sync (cron)
                          │
                          ▼
                    OwnCloud server ──▶ all devices
```

## Directory Layout

```
/opt/homemail/
├── Raw/              # Bronze layer — pristine scanner uploads (read-only)
├── Organized/        # Silver layer — AI-classified copies with smart filenames
├── Reports/          # TODO.md, document_index.csv, processing ledger, dashboard
└── _pipeline/        # Application code, config, installer
    ├── pipeline.py   # Main processing engine
    ├── config.toml   # User-editable settings (folders, thresholds, categories)
    ├── install.sh    # Automated installer
    ├── sync.sh       # OwnCloud sync script
    ├── setup.md      # Detailed manual setup guide
    └── .env          # ANTHROPIC_API_KEY (not committed)
```

## Quick Start

```bash
git clone git@github.com:rbrenton/homemail.git /opt/homemail
cd /opt/homemail
sudo make install        # runs install.sh (sets up users, samba, deps, systemd)
```

After install, complete these steps:

1. **Set your API key:**
   ```bash
   echo "ANTHROPIC_API_KEY=sk-ant-..." > /opt/homemail/_pipeline/.env
   ```

2. **Start the service:**
   ```bash
   sudo systemctl start homemail
   ```

3. **Configure the Epson RR-600W** (scanner web UI):
   - SMB path: `\\<PI_IP>\HomeMail`
   - Username: `scanner`
   - Password: (set during install, max 20 characters)

4. **Open the dashboard:**
   ```
   http://<PI_IP>:8080/Reports/
   ```

## Docker Quick Start

Run on any machine with Docker Desktop, Podman, or Rancher Desktop — no host-level
dependencies to install.

```bash
git clone git@github.com:rbrenton/homemail.git
cd homemail

# Create data directory and set your API key
mkdir -p ~/homemail/Raw ~/homemail/Organized ~/homemail/Reports
echo "ANTHROPIC_API_KEY=sk-ant-..." > ~/homemail/.env

# Build and start
make docker-up          # or: docker compose up -d --build
```

Dashboard at `http://localhost:8080/Reports/`

The container bind-mounts `~/homemail/Raw/`, `~/homemail/Organized/`, and
`~/homemail/Reports/` from the host, so all data stays outside the repo. Drop PDFs
into `~/homemail/Raw/` and the pipeline picks them up automatically.

To customize settings, copy `_pipeline/config.toml` and uncomment the volume mount
in `docker/docker-compose.yml`:

```yaml
- ~/homemail/my-config.toml:/opt/homemail/_pipeline/config.toml:ro
```

### Auto-start on boot

| Runtime | Auto-start |
|---------|------------|
| Docker Desktop | Enable "Start Docker Desktop when you sign in" in settings |
| Podman Desktop | Enable "Start Podman Desktop on login" in preferences |
| Rancher Desktop | Enable "Start at login" in preferences |
| Linux dockerd | Enabled by default (`systemctl enable docker`) |

The container uses `restart: unless-stopped`, so it starts automatically whenever
the container runtime is running.

### Podman compatibility

`podman compose` works natively — no changes needed. If using Podman 4.7+, the
`docker compose` V2 syntax is also supported via the podman-docker compatibility
package.

### Bind mount ownership (Linux)

On Linux, files created by the container are owned by root on the host. If you need
a specific UID/GID, run the container with `--user $(id -u):$(id -g)` or add
`user: "1000:1000"` to `docker/docker-compose.yml`.

## Configuration

Settings live in `_pipeline/config.toml`. The installer creates this file on first install and **never overwrites it** — your edits are safe across upgrades.

Settings are loaded in three layers (last wins):

1. **Built-in defaults** (hardcoded in `pipeline.py`)
2. **config.toml** (overrides defaults)
3. **CLI arguments** (override everything)

```toml
# _pipeline/config.toml

[folders]
bronze   = "/opt/homemail/Raw"
silver   = "/opt/homemail/Organized"
tracking = "/opt/homemail/Reports"

[processing]
poll_interval  = 15       # seconds between folder scans
ocr_if_needed  = true
verify_copies  = true

[ai]
enabled = true

[blank_detection]
threshold       = 0.98    # 0-1, higher = more lenient
min_text_length = 10
```

To customize categories, uncomment the `[categories.*]` sections in the file. When present, they **fully replace** the built-in list — only the categories you define will be used:

```toml
[categories.bill]
label       = "Bill"
description = "Any bill, invoice, or payment request"

[categories.medical]
label       = "Medical"
description = "Medical records, lab results, prescriptions"
```

## Usage

```bash
# Watch mode (default) — polls for new scans every 15s
uv run _pipeline/pipeline.py

# Process existing files and exit (or: make batch)
uv run _pipeline/pipeline.py --batch

# Skip AI classification (date-based filenames only)
uv run _pipeline/pipeline.py --no-ai

# Use a custom config file
uv run _pipeline/pipeline.py --config /path/to/config.toml

# Custom dashboard port (0 to disable)
uv run _pipeline/pipeline.py --port 9090

# Verbose logging
uv run _pipeline/pipeline.py -v
```

## Make Targets

```
make install      # Full install (requires sudo)
make start        # Start the systemd service
make stop         # Stop the service
make restart      # Restart the service
make status       # Show service status
make logs         # Tail live journal logs
make batch        # One-shot batch processing
make sync         # Run OwnCloud sync manually
make test-smb     # Verify Samba share is accessible
make docker-build # Build the Docker image
make docker-up    # Start the container (builds if needed)
make docker-down  # Stop and remove the container
make docker-logs  # Tail container logs
```

## Dependencies

**Docker:** Just Docker Desktop, Podman, or Rancher Desktop. All other deps are
included in the container image.

**Bare-metal (RPi):**

- **System:** Python 3.11+, Tesseract OCR, Samba, [uv](https://docs.astral.sh/uv/)
- **Python:** pymupdf, anthropic, Pillow, pytesseract (declared inline via PEP 723 — `uv run` installs them automatically)

Install system deps manually with:
```bash
sudo apt install -y samba tesseract-ocr
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## Service Management

```bash
sudo systemctl status homemail        # Check status
sudo systemctl restart homemail       # Restart after config changes
journalctl -u homemail -f             # Tail live logs
journalctl -u homemail --since "1h"   # Recent logs
```

## Troubleshooting

| Problem | Check |
|---------|-------|
| Scanner can't connect | `smbclient //localhost/HomeMail -U scanner` |
| Pipeline not detecting files | `systemctl status homemail` and `ls -la Raw/` |
| OCR not working | `tesseract --version` |
| All files named "Unsorted" | Verify `ANTHROPIC_API_KEY` in `_pipeline/.env` |
| Dashboard not loading | `curl http://localhost:8080/Reports/` |
| OwnCloud sync issues | `bash _pipeline/sync.sh` and check `Reports/sync.log` |

See [_pipeline/setup.md](_pipeline/setup.md) for the full manual installation guide.
