"""Print the memory of every process in a container, and how much of it is shared.

    docker compose exec -T app python - < scripts/process_memory.py

The endurance test judges the memory that the control group reports for the
whole web service. This script shows where it is: one line for each process,
read from /proc. "private" is memory that only this process uses; "shared" is
memory that it still shares with the process it was forked from. Gunicorn
loads the application once and forks its workers, so a worker starts with
almost everything shared, and each page becomes private when the worker first
writes to it. The script uses the standard library only, because it runs inside
the image.
"""

import os


def lines_of(path):
    """The "Name: value ..." lines of a /proc file as {name: [words of the value]}."""
    values = {}
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                name, _, rest = line.partition(":")
                values[name] = rest.split()
    except OSError:
        pass
    return values


def mebibytes(values, *names):
    """The sum of the named "123 kB" entries in MiB."""
    return sum(int(values[name][0]) for name in names if values.get(name)) / 1024


def command(pid):
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as handle:
            words = handle.read().decode("utf-8", "replace").split("\0")
    except OSError:
        return "?"
    return " ".join(word for word in words if word)[:44]


def main():
    print(f"{'pid':>6}{'parent':>8}{'threads':>9}{'anonymous':>11}{'private':>9}{'shared':>8}"
          "  command (MiB)")
    totals = [0.0, 0.0, 0.0]
    for entry in sorted((name for name in os.listdir("/proc") if name.isdigit()), key=int):
        status = lines_of(f"/proc/{entry}/status")
        rollup = lines_of(f"/proc/{entry}/smaps_rollup")
        if not rollup or int(entry) == os.getpid():
            continue    # a kernel thread, a process that has just ended, or this script
        parent = (status.get("PPid") or ["?"])[0]
        threads = (status.get("Threads") or ["?"])[0]
        anonymous = mebibytes(status, "RssAnon")
        private = mebibytes(rollup, "Private_Dirty", "Private_Clean")
        shared = mebibytes(rollup, "Shared_Dirty", "Shared_Clean")
        for index, value in enumerate((anonymous, private, shared)):
            totals[index] += value
        print(f"{entry:>6}{parent:>8}{threads:>9}{anonymous:>11.1f}{private:>9.1f}{shared:>8.1f}"
              f"  {command(entry)}")
    print(f"{'sum':>6}{'':>8}{'':>9}{totals[0]:>11.1f}{totals[1]:>9.1f}{totals[2]:>8.1f}"
          "  (shared memory is counted once for each process that maps it)")


if __name__ == "__main__":
    main()
