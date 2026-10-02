#!/usr/bin/env bash
# Copy the locally built site to the GitHub Pages repo (main branch of TheYoungCrews/neutrl-redemption-tracker) and push.
# The hourly GitHub Actions run normally does this; this script is the local/manual path, so it is careful:
#   - pulls (rebase) first, so it never overwrites newer commits from Actions
#   - publishes only if the local data (blocks.as_of embedded in site/index.html) is NEWER than the live page,
#     unless --force is given (e.g. to ship a template change immediately; but also push the template to the
#     tracker branch, or the next hourly run will rebuild with the old one).
set -euo pipefail
cd "$(dirname "$0")"
FORCE=0; [ "${1:-}" = "--force" ] && FORCE=1
ASSETS=(index.html og.png favicon.svg apple-touch-icon.png llms.txt)   # keep in sync with SITE_ASSETS in build.py
DEST=/workspace/neutrl-pages
asof() { grep -o '"as_of":[0-9]*' "$1" 2>/dev/null | head -1 | cut -d: -f2; }
git -C "$DEST" pull -q --rebase --autostash origin main
L=$(asof site/index.html); R=$(asof "$DEST/index.html")
if [ "$FORCE" = 0 ] && [ "${L:-0}" -le "${R:-0}" ]; then
  echo "live site already has data as of block ${R:-?} (local build: ${L:-?}); not publishing (use --force to override)"; exit 0
fi
for f in "${ASSETS[@]}"; do
  if [ -f "site/$f" ]; then cp "site/$f" "$DEST/$f"; else echo "warning: site/$f missing (not built?)" >&2; fi
done
cd "$DEST"
git add -- "${ASSETS[@]}"
if git diff --cached --quiet; then echo "no changes"; exit 0; fi
git commit -qm "Refresh data $(date '+%Y-%m-%d %H:%M %Z') (block ${L:-?}, local)"
for i in 1 2 3; do
  if git push -q origin main; then echo "pushed"; exit 0; fi
  git pull -q --rebase origin main || { echo "rebase conflict; aborting" >&2; git rebase --abort; exit 1; }
done
echo "push failed" >&2; exit 1
