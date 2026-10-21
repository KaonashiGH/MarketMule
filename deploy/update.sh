#!/usr/bin/env bash
# Pull the latest code, install any new packages and restart the bot.
# Run on the server: bash /home/marketmule/deploy/update.sh
set -euo pipefail
cd /home/marketmule
git pull --ff-only
.venv/bin/pip install --quiet -r requirements.txt
supervisorctl restart marketmule
supervisorctl status marketmule
