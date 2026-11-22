#!/usr/bin/env bash
# Run on the Mac. The repo is public, so the server pulls it from GitHub.
#
#   TRAWL_DEPLOY_HOST=user@1.2.3.4 deploy/deploy.sh        # pull, start the db, install the timer
#   TRAWL_DEPLOY_HOST=user@1.2.3.4 deploy/deploy.sh run    # run collect + digest now
#   TRAWL_DEPLOY_HOST=user@1.2.3.4 deploy/deploy.sh logs   # recent runs
#   TRAWL_DEPLOY_HOST=user@1.2.3.4 deploy/deploy.sh profile  # copy résumé, bank, criteria to ~/.trawl
#
# Personal files stay out of this public repo. `profile` copies ~/.trawl/criteria.toml, and from
# the private résumé repo's origin/main (merged = approved; local drafts never leave the Mac):
# resume-en.tex, bank.toml if there is one, and the class and fonts that compile tailored
# résumés. It also copies ~/.trawl/profile.enc, and its key from .env into the server's .env. TRAWL_RESUME_REPO points at that clone (default: ../resume). With TRAWL_DEPLOY_HOST=local
# it fills this Mac's ~/.trawl the same way.
set -euo pipefail
host="${TRAWL_DEPLOY_HOST:?set TRAWL_DEPLOY_HOST=user@host (or local, for profile)}"

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
    on() { if [ "$host" = local ]; then bash -c "$1"; else ssh "$host" "$1"; fi; }
    git -C "$repo" fetch -q origin
    git -C "$repo" show origin/main:resume-en.tex | on 'umask 077 && mkdir -p ~/.trawl && cat > ~/.trawl/resume.tex'
    if git -C "$repo" cat-file -e origin/main:bank.toml 2>/dev/null; then
        git -C "$repo" show origin/main:bank.toml | on 'umask 077 && cat > ~/.trawl/bank.toml'
    else
        on 'rm -f ~/.trawl/bank.toml'
    fi
    git -C "$repo" archive origin/main resume-en.cls fontawesome.sty fonts \
        | on 'rm -rf ~/.trawl/resume-class && mkdir -p ~/.trawl/resume-class && tar -x -C ~/.trawl/resume-class'
    [ "$host" = local ] || ssh "$host" 'umask 077 && cat > ~/.trawl/criteria.toml' < ~/.trawl/criteria.toml
    echo "copied résumé, bank, class and criteria from origin/main ($(git -C "$repo" rev-parse --short origin/main))"
    # The encrypted profile goes to ~/.trawl, its key to the server's .env: never side by side
    if [ "$host" != local ] && [ -f ~/.trawl/profile.enc ]; then
        ssh "$host" 'umask 077 && cat > ~/.trawl/profile.enc' < ~/.trawl/profile.enc
        key=$(grep -E '^TRAWL_PROFILE_KEY=' "$(dirname "$0")/../.env" || true)
        if [ -n "$key" ]; then
            echo "$key" | ssh "$host" 'cd ~/trawl && umask 077 && touch .env &&
                grep -v "^TRAWL_PROFILE_KEY=" .env > .env.new; cat >> .env.new; mv .env.new .env'
        fi
        echo "copied the encrypted profile${key:+ and its key}"
    fi
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
