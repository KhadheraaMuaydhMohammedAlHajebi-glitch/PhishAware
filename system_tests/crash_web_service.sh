#!/bin/sh
# Kill the web service of the Compose stack without warning, as a crash would.
#
#   system_tests/crash_web_service.sh
#
# Every process in the "app" container except its init process receives SIGKILL.
# The init process then exits, the container stops with status 137, and Docker's
# restart policy ("unless-stopped") starts it again. Case ST-17 runs this in the
# middle of a participant's journey and then checks that no answer was lost.
docker compose exec -T app python -c "
import os, signal
for entry in os.listdir('/proc'):
    if entry.isdigit() and int(entry) not in (1, os.getpid()):
        try:
            os.kill(int(entry), signal.SIGKILL)
        except ProcessLookupError:
            pass
" > /dev/null 2>&1
exit 0
