#!/bin/bash
# ============================================================
# HomeMail Pipeline — RPi5 DietPi Installer
# ============================================================
#
# Sets up:
#   - homemail system user
#   - scanner SMB user + Samba share
#   - directory structure at /opt/homemail
#   - Python dependencies + Tesseract OCR
#   - systemd service (auto-start on boot)
#
# Usage:
#   sudo bash install.sh
#
# After install:
#   1. Set your API key:  echo "ANTHROPIC_API_KEY=sk-ant-..." > /opt/homemail/_pipeline/.env
#   2. Start the service: sudo systemctl start homemail
#   3. Configure scanner SMB → <PI_IP>\HomeMail, user: scanner
#   4. Dashboard: http://<PI_IP>:8080/TODO.html
#
# ============================================================

set -euo pipefail

# ---- Config ----
INSTALL_DIR="/opt/homemail"
PIPELINE_DIR="${INSTALL_DIR}/_pipeline"
SERVICE_USER="homemail"
SCANNER_USER="scanner"
SERVICE_NAME="homemail"

# ---- Preflight ----
if [ "$(id -u)" -ne 0 ]; then
    echo "Error: run as root (sudo bash install.sh)"
    exit 1
fi

echo ""
echo "============================================================"
echo "  HomeMail Pipeline Installer"
echo "============================================================"
echo ""

# ---- System users ----
echo "[1/7] Creating users..."

if ! id -u "${SERVICE_USER}" &>/dev/null; then
    useradd -r -s /usr/sbin/nologin -d "${INSTALL_DIR}" "${SERVICE_USER}"
    echo "  Created system user: ${SERVICE_USER}"
else
    echo "  User ${SERVICE_USER} already exists — skipping"
fi

if ! id -u "${SCANNER_USER}" &>/dev/null; then
    useradd -M -s /usr/sbin/nologin "${SCANNER_USER}"
    echo "  Created user: ${SCANNER_USER}"
else
    echo "  User ${SCANNER_USER} already exists — skipping"
fi

# Add scanner to homemail group
usermod -aG "${SERVICE_USER}" "${SCANNER_USER}"
echo "  Added ${SCANNER_USER} to ${SERVICE_USER} group"

# ---- Directory structure ----
echo ""
echo "[2/7] Creating directories..."

mkdir -p "${INSTALL_DIR}"/{Raw,Organized,Reports}
mkdir -p "${PIPELINE_DIR}"

chown -R "${SERVICE_USER}:${SERVICE_USER}" "${INSTALL_DIR}"
chmod 750 "${INSTALL_DIR}"
chmod 775 "${INSTALL_DIR}/Raw"
chown "${SCANNER_USER}:${SERVICE_USER}" "${INSTALL_DIR}/Raw"

echo "  ${INSTALL_DIR}/"
echo "  ├── Raw/          (scanner uploads)"
echo "  ├── Organized/    (pipeline output)"
echo "  ├── Reports/      (ledger, index)"
echo "  └── _pipeline/    (code, config)"

# ---- Copy pipeline files ----
echo ""
echo "[3/7] Deploying pipeline..."

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

for f in pipeline.py setup.md; do
    if [ -f "${SCRIPT_DIR}/${f}" ]; then
        cp "${SCRIPT_DIR}/${f}" "${PIPELINE_DIR}/"
        echo "  Copied ${f} → _pipeline/"
    else
        echo "  Warning: ${f} not found in ${SCRIPT_DIR} — skipping"
    fi
done

# Dashboard goes in Reports/
if [ -f "${SCRIPT_DIR}/index.html" ]; then
    cp "${SCRIPT_DIR}/index.html" "${INSTALL_DIR}/Reports/"
    chown "${SERVICE_USER}:${SERVICE_USER}" "${INSTALL_DIR}/Reports/index.html"
    echo "  Copied index.html → Reports/"
fi

chmod +x "${PIPELINE_DIR}/pipeline.py" 2>/dev/null || true

# Create .env placeholder if it doesn't exist
if [ ! -f "${PIPELINE_DIR}/.env" ]; then
    echo "ANTHROPIC_API_KEY=your-key-here" > "${PIPELINE_DIR}/.env"
    echo "  Created .env placeholder — edit with your actual API key"
else
    echo "  .env already exists — not overwriting"
fi

chown -R "${SERVICE_USER}:${SERVICE_USER}" "${PIPELINE_DIR}"
chmod 600 "${PIPELINE_DIR}/.env"

# ---- Install dependencies ----
echo ""
echo "[4/7] Installing dependencies..."

apt-get update -qq
apt-get install -y -qq samba tesseract-ocr > /dev/null

# Install uv (fast Python package manager) system-wide
if ! command -v uv &>/dev/null; then
    echo "  Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
fi

echo "  Installed: samba, tesseract-ocr, uv"

# Install Claude Code for the homemail user
if [ ! -f "${INSTALL_DIR}/.local/bin/claude" ]; then
    echo "  Installing Claude Code for ${SERVICE_USER}..."
    sudo -u "${SERVICE_USER}" -s /bin/bash -c 'curl -fsSL https://claude.ai/install.sh | bash' || {
        echo "  Warning: Claude Code install failed — install manually later"
    }
else
    echo "  Claude Code already installed — skipping"
fi

# Verify
uv_ver=$(uv --version 2>&1)
echo "  ${uv_ver}"
tesseract_ver=$(tesseract --version 2>&1 | head -1)
echo "  ${tesseract_ver}"

# ---- Configure Samba ----
echo ""
echo "[5/7] Configuring Samba..."

# Remove any existing HomeMail share config
if grep -q "\[HomeMail\]" /etc/samba/smb.conf; then
    # Remove the block (from [HomeMail] to next section or EOF)
    sed -i '/^\[HomeMail\]/,/^\[/{/^\[HomeMail\]/d;/^\[/!d}' /etc/samba/smb.conf
    echo "  Removed existing [HomeMail] share config"
fi

cat >> /etc/samba/smb.conf << 'SAMBA'

[HomeMail]
   path = /opt/homemail/Raw
   browseable = yes
   writable = yes
   guest ok = no
   valid users = scanner
   create mask = 0644
   directory mask = 0755
   force user = scanner
   force group = homemail
SAMBA

echo "  Added [HomeMail] share"

# Set SMB password for scanner
echo ""
echo "  Set the Samba password for the scanner user."
echo "  (Must be 20 characters or fewer for Epson scanners)"
echo ""
smbpasswd -a "${SCANNER_USER}"

systemctl restart smbd
systemctl enable smbd
echo ""
echo "  Samba configured and running"

# ---- Create systemd service ----
echo ""
echo "[6/7] Creating systemd service..."

cat > /etc/systemd/system/${SERVICE_NAME}.service << EOF
[Unit]
Description=HomeMail Scanner Pipeline
After=network-online.target smbd.service
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_USER}
WorkingDirectory=${PIPELINE_DIR}
ExecStart=/usr/local/bin/uv run ${PIPELINE_DIR}/pipeline.py
Restart=on-failure
RestartSec=30
EnvironmentFile=${PIPELINE_DIR}/.env
Environment=PATH=/opt/homemail/.local/bin:/usr/local/bin:/usr/bin:/bin
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${SERVICE_NAME}

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
echo "  Service created and enabled (not started yet)"

# ---- Summary ----
PI_IP=$(hostname -I | awk '{print $1}')

echo ""
echo "============================================================"
echo "  Installation complete!"
echo "============================================================"
echo ""
echo "  Next steps:"
echo ""
echo "  1. Authenticate Claude Code (one-time):"
echo "     sudo -u ${SERVICE_USER} -s /bin/bash -c 'claude'"
echo "     (Copy the URL, open in browser, paste code back)"
echo ""
echo "  2. Set your Anthropic API key:"
echo "     echo 'ANTHROPIC_API_KEY=sk-ant-...' > ${PIPELINE_DIR}/.env"
echo ""
echo "  3. Start the pipeline:"
echo "     sudo systemctl start ${SERVICE_NAME}"
echo ""
echo "  4. Configure the Epson RR-600W:"
echo "     SMB path:  \\\\${PI_IP}\\HomeMail"
echo "     Username:  ${SCANNER_USER}"
echo "     Password:  (what you just set)"
echo ""
echo "  5. Open the dashboard:"
echo "     http://${PI_IP}:8080/Reports/"
echo ""
echo "  Useful commands:"
echo "     sudo systemctl status ${SERVICE_NAME}"
echo "     journalctl -u ${SERVICE_NAME} -f"
echo "     sudo systemctl restart ${SERVICE_NAME}"
echo "     sudo -u ${SERVICE_USER} -s /bin/bash   # shell as homemail"
echo ""
echo "============================================================"
