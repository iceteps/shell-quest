"""Linux missions — the course's Class 1, and the one class the game never had.

The teacher's real graded sheet ("Home Assignments for Linux Class") is ten parts:
filesystem basics, permissions, find, grep, processes, disk usage, networking,
shell scripting, cron, and tar/gzip. These three missions mirror all ten, so the
graded work is a re-run rather than a first attempt.

The shell these missions run on lives in `linux_shell.py` — see its docstring for
why the Linux missions get their own instead of using the engine's flat world.
"""
from missions.linux_shell import HANDLERS, HELP_LINES

# ---------------------------------------------------------------- missions --
MISSIONS = [
    {
        "id": "linux-01",
        "topic": "linux",
        "title": "First Contact 🐧 — files, trees and permissions",
        "vault_note": "Linux Fundamentals",
        "brief": ("Your first hour on a Linux box. Everything is a file, everything hangs off\n"
                  "one tree at /, and who may read what is decided by three little digits.\n\n"
                  "Build the workspace the course assignment uses, then lock a file down so\n"
                  "only you can read it. (This is Assignments 1–3 of the REAL graded sheet.)\n\n"
                  "🌍 You're on a real Linux box now — `setup` shows how the tools install there."),
        "world": {},
        "handlers": HANDLERS,
        "help_lines": HELP_LINES,
        "objectives": [
            {"desc": "Create ~/linux_course with week1 and week2 inside it", "xp": 10,
             "hint": "mkdir makes directories; -p creates a whole path at once and never complains.",
             "check": lambda w: all(d in w.flags.get("dirs", set())
                                    for d in ("/root/linux_course", "/root/linux_course/week1",
                                              "/root/linux_course/week2"))},
            {"desc": "Put 'Welcome to Linux!' into week1/intro.txt", "xp": 10,
             "hint": "echo writes to the screen — `>` sends that output into a file instead.",
             "check": lambda w: "Welcome to Linux!" in w.files.get(
                 "/root/linux_course/week1/intro.txt", "")},
            {"desc": "Create the file week1/private_data", "xp": 5,
             "hint": "touch creates an empty file (and is how you'd bump a timestamp).",
             "check": lambda w: "/root/linux_course/week1/private_data" in w.files},
            {"desc": "Lock private_data so ONLY its owner can read and write it", "xp": 15,
             "hint": "Three digits: owner, group, others. Read=4, write=2, execute=1. "
                     "Owner needs read+write; everyone else gets nothing.",
             "check": lambda w: w.flags.get("modes", {}).get(
                 "/root/linux_course/week1/private_data") == 0o600},
            {"desc": "Prove the permissions changed with a long listing", "xp": 10,
             "hint": "ls has a flag that shows the permission triads, owner, size and date.",
             "check": lambda w: w.flags.get("saw_perms")},
            {"desc": "Create file1.txt, file2.txt and file3.txt in week2", "xp": 10,
             "hint": "touch takes more than one filename at a time.",
             "check": lambda w: all(f"/root/linux_course/week2/file{i}.txt" in w.files
                                    for i in (1, 2, 3))},
            {"desc": "Find every .txt file under linux_course", "xp": 15,
             "hint": 'find <where> -name "<pattern>" — quote the pattern so the shell '
                     "doesn't expand it first.",
             "check": lambda w: w.flags.get("found_txt")},
        ],
        "teach": [
            "Linux has no drive letters — one tree from `/`, and `~` is your home. `mkdir -p` builds "
            "a whole branch in one go and stays quiet if it already exists, which is why scripts use it.",
            "`>` redirects stdout into a file, replacing its contents. `>>` appends instead — mixing "
            "those two up is how people erase files they meant to add to.",
            "`touch` creates an empty file or updates its timestamp. Empty is a perfectly valid file.",
            "600 = owner rw, group none, others none. Read 4 + write 2 = 6. The reflex to resist is "
            "`chmod 777` — that hands write access to everyone on the box, and it's never the fix.",
            "`ls -l`'s first column is the permission triad: type, owner, group, others. Reading it "
            "fluently is a genuine day-one Linux skill.",
            "Most commands take many arguments — one `touch` call, three files. Fewer round trips.",
            "`find` walks the tree; `-name` matches the filename. Quote the pattern or the shell "
            "expands `*.txt` against the CURRENT directory before find ever sees it.",
        ],
        "solution": [
            "pwd",
            "mkdir -p ~/linux_course/week1 ~/linux_course/week2",
            "cd ~/linux_course",
            "ls",
            'echo "Welcome to Linux!" > week1/intro.txt',
            "cat week1/intro.txt",
            "touch week1/private_data",
            "chmod 600 week1/private_data",
            "ls -l week1",
            "touch week2/file1.txt week2/file2.txt week2/file3.txt",
            'find ~/linux_course -name "*.txt"',
        ],
    },
    {
        "id": "linux-02",
        "topic": "linux",
        "title": "Read the Logs 🔎 — grep, processes and disk",
        "vault_note": "Linux Fundamentals",
        "brief": ("Something is filling the disk and a runaway process won't die. This is the\n"
                  "shape of every real incident: filter the noise, find the process, kill it,\n"
                  "and write down what you saw.\n\n"
                  "(Assignments 4–6 of the REAL graded sheet.)"),
        "world": {
            "files": {},
        },
        "handlers": HANDLERS,
        "help_lines": HELP_LINES,
        "objectives": [
            {"desc": "Create week2/log.txt with the three lines from the assignment", "xp": 10,
             "hint": 'echo -e lets \\n mean "new line": echo -e "a\\nb\\nc" > file',
             "check": lambda w: all(
                 k in w.files.get("/root/linux_course/week2/log.txt", "")
                 for k in ("error:", "info:", "warning:"))},
            {"desc": "Save just the error line(s) into week2/error.log", "xp": 15,
             "hint": "grep prints matching lines — send that output somewhere with `>`.",
             "check": lambda w: "error" in w.files.get(
                 "/root/linux_course/week2/error.log", "").lower()
             and "info" not in w.files.get("/root/linux_course/week2/error.log", "").lower()},
            {"desc": "Start a background process with sleep 300", "xp": 10,
             "hint": "A trailing & sends a command to the background and prints its PID.",
             "check": lambda w: any("sleep" in v for v in w.flags.get("procs", {}).values())},
            {"desc": "List processes and locate the sleep", "xp": 10,
             "hint": "ps aux shows every process; pipe it into grep to filter.",
             "check": lambda w: w.flags.get("saw_ps")},
            {"desc": "Terminate the sleep process by its PID", "xp": 15,
             "hint": "kill <PID> — the number ps printed in the PID column.",
             "check": lambda w: w.flags.get("killed_sleep")},
            {"desc": "Write df output to week2/disk_report.txt, then APPEND du of linux_course",
             "xp": 15,
             "hint": "`>` creates/overwrites, `>>` appends. You need one of each, in that order.",
             "check": lambda w: "Filesystem" in w.files.get(
                 "/root/linux_course/week2/disk_report.txt", "")
             and "linux_course" in w.files.get(
                 "/root/linux_course/week2/disk_report.txt", "")},
        ],
        "teach": [
            "`echo -e` turns \\n into real newlines. Without -e you get the two characters, literally — "
            "a small thing that silently ruins generated files.",
            "grep filters lines; redirection decides where they land. `grep error log.txt > error.log` "
            "is the whole pattern behind most log triage.",
            "`&` backgrounds a job and prints its PID. The shell hands you the prompt back immediately.",
            "`ps aux | grep sleep` is the classic hunt. The pipe feeds one command's stdout into the "
            "next — the single most important idea in the Unix shell.",
            "`kill` sends SIGTERM: 'please stop'. `kill -9` is SIGKILL, unstoppable and un-cleanable-"
            "up-after — reach for it only when TERM has already failed.",
            "`>` truncates, `>>` appends. Building a report is exactly this: one `>` to start it, then "
            "`>>` for every line after.",
        ],
        "solution": [
            "mkdir -p ~/linux_course/week2",
            "cd ~/linux_course/week2",
            'echo -e "error: Disk space low\\ninfo: System rebooted\\nwarning: High memory usage" > log.txt',
            "cat log.txt",
            'grep "error" log.txt > error.log',
            "cat error.log",
            "sleep 300 &",
            "ps aux",
            "kill 4821",
            "df -h > disk_report.txt",
            "du -sh ~/linux_course >> disk_report.txt",
            "cat disk_report.txt",
        ],
    },
    {
        "id": "linux-03",
        "topic": "linux",
        "title": "Ship the Script 📜 — networking, cron and archives",
        "vault_note": "Linux Fundamentals",
        "brief": ("The last four parts of the graded sheet, and the ones that turn a user into\n"
                  "an operator: prove the box has a network, write a script and make it run,\n"
                  "schedule it, then pack the whole workspace into one file.\n\n"
                  "(Assignments 7–10 of the REAL graded sheet.)"),
        "world": {},
        "handlers": HANDLERS,
        "help_lines": HELP_LINES,
        "objectives": [
            {"desc": "Check the machine's IP addresses", "xp": 10,
             "hint": "The modern command is two letters and a subcommand. `ifconfig` is the retired one.",
             "check": lambda w: w.flags.get("saw_ip")},
            {"desc": "Ping google.com exactly 4 times, saving output to week2/ping_output.txt",
             "xp": 15,
             "hint": "ping -c 4 <host> — without -c it runs until you Ctrl+C it.",
             "check": lambda w: "packets transmitted" in w.files.get(
                 "/root/linux_course/week2/ping_output.txt", "")},
            {"desc": "Write ~/linux_course/hello.sh that prints 'Hello, Linux!'", "xp": 15,
             "hint": "Two lines: a #!/bin/bash shebang, then the echo. `edit` or `echo -e` both work.",
             "check": lambda w: "Hello, Linux!" in w.files.get("/root/linux_course/hello.sh", "")},
            {"desc": "Make hello.sh executable and run it", "xp": 15,
             "hint": "chmod +x adds the execute bit; then run it by path: ./hello.sh",
             "check": lambda w: w.flags.get("ran_script", "").endswith("hello.sh")},
            {"desc": "Schedule a cron job that appends the date to timestamp.log every minute",
             "xp": 15,
             "hint": "Five stars = every minute. Pipe the line into `crontab -`.",
             "check": lambda w: any("* * * * *" in ln and "timestamp.log" in ln
                                    for ln in w.flags.get("cron", []))},
            {"desc": "Archive linux_course into linux_course.tar, then gzip it", "xp": 15,
             "hint": "tar -cvf <archive> <dir> creates it; gzip <archive> compresses in place.",
             "check": lambda w: "/root/linux_course.tar.gz" in w.files},
            {"desc": "List the archive's contents WITHOUT extracting it", "xp": 10,
             "hint": "tar's -t flag lists. Add -v for detail, -f for the filename.",
             "check": lambda w: w.flags.get("listed_archive")},
        ],
        "teach": [
            "`ip a` replaced `ifconfig` — on a modern Fedora, net-tools isn't even installed. Knowing "
            "which command is current is half of not looking lost on someone else's server.",
            "`-c 4` bounds the ping. Unbounded commands in a script are how a job hangs forever.",
            "The `#!` shebang tells the kernel which interpreter to run the file with. Without it, a "
            "script is just text — the line is not a comment, it's the loading instruction.",
            "A file needs the x bit to run. `chmod +x` is the second half of 'I wrote a script' — "
            "forget it and you get Permission denied on a file you own.",
            "Cron's five fields are minute, hour, day-of-month, month, day-of-week. Watch the quoting: "
            "in double quotes `$(date)` expands ONCE, when you write the crontab — the job then logs "
            "the same frozen timestamp forever. Single-quote it so cron expands it each run.",
            "tar bundles many files into one; gzip compresses one file. `.tar.gz` is literally both "
            "steps, which is why the extension has two parts.",
            "`tar -t` inspects an archive without unpacking. Always look before you extract — an "
            "archive can write anywhere its paths point.",
        ],
        "solution": [
            "mkdir -p ~/linux_course/week2",
            "cd ~/linux_course",
            "ip a",
            "ping -c 4 google.com > week2/ping_output.txt",
            "cat week2/ping_output.txt",
            'echo -e "#!/bin/bash\\necho \\"Hello, Linux!\\"" > hello.sh',
            "cat hello.sh",
            "chmod +x hello.sh",
            "./hello.sh",
            "echo '* * * * * date >> ~/linux_course/timestamp.log' | crontab -",
            "crontab -l",
            "cd ~",
            "tar -cvf linux_course.tar linux_course",
            "gzip linux_course.tar",
            "tar -tvf linux_course.tar.gz",
        ],
    },
]
