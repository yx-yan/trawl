#!/usr/bin/env bash
# Run on the Mac. The repo is public, so the server pulls it from GitHub.
#
#   TRAWL_DEPLOY_HOST=user@1.2.3.4 deploy/deploy.sh        # pull, start the db, install the timer
#   TRAWL_DEPLOY_HOST=user@1.2.3.4 deploy/deploy.sh run    # run collect + digest now
#   TRAWL_DEPLOY_HOST=user@1.2.3.4 deploy/deploy.sh logs   # recent runs
#   TRAWL_DEPLOY_HOST=user@1.2.3.4 deploy/deploy.sh profile  # copy résumé + criteria to ~/.trawl
#
# Personal files stay out of this public repo. `profile` copies ~/.trawl/criteria.toml, and
# resume-en.tex as it is on the private résumé repo's origin/main (merged = approved; local
# drafts never leave the Mac). TRAWL_RESUME_REPO points at that clone (default: ../resume).
set -euo pipefail
host="${TRAWL_DEPLOY_HOST:?set TRAWL_DEPLOY_HOST=user@host}"

case "${1:-deploy}" in
deploy)
    ssh "$host" 'set -e
        [ -d ~/trawl ] || git clone -q https://github.com/yx-yan/trawl.git ~/trawl
        cd ~/trawl && git pull -q --ff-only
        [ -f .env ] || { umask 077; : > .env; }
        docker compose up -d --wait db
        mkdir -p ~/.trawl
        for f in trawl.service trawl.timer trawl-gaps.service trawl-gaps.timer; do
            sed "s/USER/$(whoami)/g" deploy/$f | sudo tee /etc/systemd/system/$f >/dev/null
        done
        sudo systemctl daemon-reload
        sudo systemctl enable -q --now trawl.timer trawl-gaps.timer
        systemctl list-timers "trawl*" --no-pager | head -3'
    ;;
profile)
    repo="${TRAWL_RESUME_REPO:-$(dirname "$0")/../../resume}"
    git -C "$repo" fetch -q origin
    git -C "$repo" show origin/main:resume-en.tex | ssh "$host" 'umask 077 && mkdir -p ~/.trawl && cat > ~/.trawl/resume.tex'
    ssh "$host" 'umask 077 && cat > ~/.trawl/criteria.toml' < ~/.trawl/criteria.toml
    echo "copied criteria.toml and resume-en.tex from origin/main ($(git -C "$repo" rev-parse --short origin/main))"
    ;;
run)
    ssh "$host" 'sudo systemctl start trawl.service; journalctl -u trawl -n 60 --no-pager'
    ;;
logs)
    ssh "$host" 'journalctl -u trawl -n 200 --no-pager'
    ;;
*)
    echo "usage: deploy.sh [deploy|run|logs|profile]" >&2
    exit 2
    ;;
esac
