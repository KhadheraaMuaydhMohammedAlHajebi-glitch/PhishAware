#!/usr/bin/env bash
# Rehearse section 8 of docs/deployment.md on a running Docker host: upgrade from
# the previous release to this build, roll back, and upgrade again.
#
#   system_tests/upgrade_rehearsal.sh            (needs Docker and a throwaway .env)
#
# The previous release is the newest release/x.y.z branch on the remote that is
# not this build itself and that can be built as an image. The script
#
#   1. starts the previous release on empty volumes and lets six scripted
#      participants complete the journey on it (with that release's own load test),
#   2. records the statistics that release reports,
#   3. upgrades to this build as the guide says (backup, then start the new image)
#      and checks that the statistics are unchanged,
#   4. rolls back to the previous release and checks them again,
#   5. upgrades once more, signs in with the administrator account that the
#      previous release created, and completes one more journey.
#
# Everything it creates is scripted test data, and it deletes the stack's volumes
# before and after. Never run it on a host that holds the data of a study.
set -euo pipefail

work="${RUNNER_TEMP:-$(mktemp -d)}/upgrade-rehearsal"
build_tag="${PHISHAWARE_VERSION:-ci}"
this_version="$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' src/__init__.py)"
password="$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')"

older_or_equal() {   # true when version $1 is not newer than version $2
  [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | tail -n 1)" = "$2" ]
}

previous=""
for candidate in $(git ls-remote --heads origin 'release/*' | sed 's|.*refs/heads/release/||' | sort -rV); do
  older_or_equal "$candidate" "$this_version" || continue
  git fetch --quiet --depth 1 origin "release/$candidate"
  [ "$(git rev-parse 'FETCH_HEAD^{tree}')" != "$(git rev-parse 'HEAD^{tree}')" ] || continue
  git cat-file -e FETCH_HEAD:Dockerfile 2> /dev/null || continue
  previous="$candidate"
  break
done
if [ -z "$previous" ]; then
  echo "No earlier release with a container image was found, so there is nothing to rehearse."
  exit 0
fi

rm -rf "$work"
mkdir -p "$work"
git worktree add --quiet --detach "$work/previous" FETCH_HEAD
trap 'git worktree remove --force "$work/previous" > /dev/null 2>&1 || true' EXIT
docker build --quiet --tag "phishaware:$previous" "$work/previous" > /dev/null
echo "Previous release: $previous (branch release/$previous). This build: $this_version."

trust_proxy() {      # the proxy issues a new local certificate authority on new volumes
  for _ in $(seq 1 30); do
    docker compose cp proxy:/data/caddy/pki/authorities/local/root.crt "$work/root.crt" \
      > /dev/null 2>&1 && return 0
    sleep 1
  done
  return 1
}

start() {            # start the stack with the image tag $1; prints how long it took
  local started=$SECONDS
  PHISHAWARE_VERSION="$1" docker compose up --detach --wait --wait-timeout 180 > "$work/up.log" 2>&1 \
    || { cat "$work/up.log"; return 1; }
  trust_proxy
  curl --fail --silent --show-error --retry 15 --retry-delay 1 --retry-all-errors \
    --cacert "$work/root.crt" --output "$work/health.json" https://localhost/healthz
  echo "$((SECONDS - started))"
}

running_version() {
  python3 -c "import json, sys; print(json.load(open(sys.argv[1]))['version'])" "$work/health.json"
}

statistics() {       # what the release reports about the stored records
  docker compose exec -T app flask analytics
}

docker compose down --volumes > /dev/null 2>&1

# 1 and 2. The previous release, with six completed journeys.
seconds="$(start "$previous")"
[ "$(running_version)" = "$previous" ]
docker compose exec -T app flask create-admin --username upgrade --password "$password" > /dev/null
python3 "$work/previous/evaluation/loadtest.py" --base-url https://localhost \
  --cacert "$work/root.crt" --levels 3 --min-journeys 6 \
  --admin-user upgrade --admin-password "$password" > "$work/journeys.log" \
  || { tail -n 20 "$work/journeys.log"; exit 1; }
statistics > "$work/before.txt"
echo "Release $previous started in $seconds s; six journeys completed on it:"
sed 's/^/    /' "$work/before.txt"

# 3. Upgrade as the guide says: a backup first, then the new image.
docker compose exec -T app flask backup-db > /dev/null
seconds="$(start "$build_tag")"
[ "$(running_version)" = "$this_version" ]
statistics > "$work/after-upgrade.txt"
diff "$work/before.txt" "$work/after-upgrade.txt"
echo "Upgrade to $this_version: healthy after $seconds s; the statistics are unchanged."

# 4. Roll back to the previous image on the same data.
seconds="$(start "$previous")"
[ "$(running_version)" = "$previous" ]
statistics > "$work/after-rollback.txt"
diff "$work/before.txt" "$work/after-rollback.txt"
echo "Rollback to $previous: healthy after $seconds s; the statistics are unchanged."

# 5. Upgrade again; the earlier administrator signs in, and a new journey completes.
seconds="$(start "$build_tag")"
[ "$(running_version)" = "$this_version" ]
PHISHAWARE_BASE_URL=https://localhost PHISHAWARE_CACERT="$work/root.crt" \
PHISHAWARE_ADMIN_USER=upgrade PHISHAWARE_ADMIN_PASSWORD="$password" python3 - << 'PYTHON'
from system_tests.harness import Administrator, Participant

administrator = Administrator()
reply = administrator.sign_in()
assert reply.path == "/admin", f"the administrator of the earlier release cannot sign in: {reply}"
before = administrator.counts()
participant = Participant()
participant.journey(pre_wrong={0, 1, 2}, post_wrong={0})
after = administrator.counts()
assert after == {name: count + 1 for name, count in before.items()}, (before, after)
print("Upgrade again: the administrator account of the earlier release signed in, and a new "
      f"journey completed ({after['complete']} participants have now finished both assessments).")
PYTHON
docker compose down --volumes > /dev/null 2>&1
echo "Result: PASS"
