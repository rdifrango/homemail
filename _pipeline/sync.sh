#!/bin/bash
PW="$(cat /opt/homemail/_pipeline/.ocpw)"
EXCLUDE="/opt/homemail/_pipeline/sync-exclude.lst"
SERVER="https://owncloud.example.com"

owncloudcmd -u brenton -p "$PW" --trust --exclude "$EXCLUDE" /opt/homemail/Raw        "$SERVER" /HomeMail/Raw
owncloudcmd -u brenton -p "$PW" --trust --exclude "$EXCLUDE" /opt/homemail/Organized   "$SERVER" /HomeMail/Organized
owncloudcmd -u brenton -p "$PW" --trust --exclude "$EXCLUDE" /opt/homemail/Reports     "$SERVER" /HomeMail/Reports
owncloudcmd -u brenton -p "$PW" --trust --exclude "$EXCLUDE" /opt/homemail/_pipeline   "$SERVER" /HomeMail/_pipeline
