#!/usr/bin/env bash
# Run on the Mac. The repo is public, so the server pulls it from GitHub.
#
#   TRAWL_DEPLOY_HOST=user@1.2.3.4 deploy/deploy.sh        # pull, start the db, install the timer
#   TRAWL_DEPLOY_HOST=user@1.2.3.4 deploy/deploy.sh run    # run collect + digest now
#   TRAWL_DEPLOY_HOST=user@1.2.3.4 deploy/deploy.sh logs   # recent runs
set -euo pipefail
host="${TRAWL_DEPLOY_HOST:?set TRAWL_DEPLOY_HOST=user@host}"

case "${1:-deploy}" in
deploy)
    ssh "$host" 'set -e
        [ -d ~/trawl ] || git clone -q https://github.com/yx-yan/trawl.git ~/trawl
        cd ~/trawl && git pull -q --ff-only
        [ -f .env ] || { umask 077; : > .env; }
        docker compose up -d --wait db
        for f in trawl.service trawl.timer; do
            sed "s/USER/$(whoami)/g" deploy/$f | sudo tee /etc/systemd/system/$f >/dev/null
        done
        sudo systemctl daemon-reload
        sudo systemctl enable -q --now trawl.timer
        systemctl list-timers trawl.timer --no-pager | head -2'
    ;;
run)
    ssh "$host" 'sudo systemctl start trawl.service; journalctl -u trawl -n 60 --no-pager'
    ;;
logs)
    ssh "$host" 'journalctl -u trawl -n 200 --no-pager'
    ;;
*)
    echo "usage: deploy.sh [deploy|run|logs]" >&2
    exit 2
    ;;
esac
