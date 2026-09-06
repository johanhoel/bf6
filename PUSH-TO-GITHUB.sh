#!/usr/bin/env bash
# One-shot: push this project to https://github.com/johanhoel/bf6
set -euo pipefail
cd "$(dirname "$0")"
REPO="https://github.com/johanhoel/bf6.git"
if [ ! -d .git ]; then
    git init -b main; git add -A
    git commit -m "Add BF6 Tuner: hardware-aware Battlefield 6 settings configurator"
fi
git remote remove origin 2>/dev/null || true
git remote add origin "$REPO"
git push -u origin main
echo "Done: https://github.com/johanhoel/bf6"
