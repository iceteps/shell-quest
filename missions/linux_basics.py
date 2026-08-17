"""Linux missions — the course's Class 1, and the one class the game never had.

The teacher's real graded sheet ("Home Assignments for Linux Class") is ten parts:
filesystem basics, permissions, find, grep, processes, disk usage, networking,
shell scripting, cron, and tar/gzip. Missions 1–3 mirror all ten, so the graded
work is a re-run rather than a first attempt. Mission 4 is the note's own drills
plus its "extra terminal exercises" (the Music-folder copy) and its boss
challenge — the dry run in /tmp, where nothing you break is graded.

The shell these missions run on lives in `linux_shell.py` — see its docstring for
why the Linux missions get their own instead of using the engine's flat world.
"""
import re

from missions.linux_shell import HANDLERS, complete, print_help_index, prompt

# Missions 2 and 3 continue mission 1's story, so they start with mission 1's
# workspace already built — a student who made ~/linux_course/week2 an hour ago
# should not be told it doesn't exist. Directories are derived from these paths,
# so listing one and entering it always agree.
WORKSPACE = {
    "linux_course/week1/intro.txt": "Welcome to Linux!\n",
    "linux_course/week1/private_data": "",
    "linux_course/week2/file1.txt": "",
    "linux_course/week2/file2.txt": "",
    "linux_course/week2/file3.txt": "",
}
WORKSPACE_MODES = {"/root/linux_course/week1/private_data": 0o600}

# A real home is never empty, and the emptiness used to cost a lesson: `ls`
# hides every name that starts with a dot, so with nothing hidden there was
# nothing for `ls -a` to reveal. These are the two dotfiles the note asks the
# student to name, plus the history file everyone eventually greps.
HOME_EXTRAS = {
    ".bashrc": ("# ~/.bashrc — sourced by every interactive shell\n"
                "alias ll='ls -alF'\n"
                "alias ..='cd ..'\n"
                'export PATH="$HOME/bin:$PATH"\n'),
    ".profile": "# ~/.profile — read once, at login\numask 022\n",
    ".bash_history": "ls\ncd /var/log\ntail -n 20 syslog\nexit\n",
}
# The folders a desktop home ships with. Extra 1C says "copy the file to the
# Music folder" and the note's fine print is that it assumes Music exists —
# so it does, here, exactly as it would on the student's own machine.
HOME_DIRS = {"/root/Documents", "/root/Downloads", "/root/Music", "/root/Pictures"}

# `date` prints "Sun Aug 17 14:32:01 UTC 2026". That shape sitting INSIDE a
# crontab line means the shell expanded $(date) at install time and cron will
# append one frozen moment forever — the classic bug in this exact assignment.
# Mirrors the detector in linux_shell.py's crontab handler on purpose: the shell
# warns about it, and these objectives make the player produce it and then fix it.
FROZEN_DATE = re.compile(r"\b\w{3} \w{3} +\d+ \d\d:\d\d:\d\d\b")


def cron_lines(w):
    return w.flags.get("cron", [])


# ---------------------------------------------------------------- missions --
MISSIONS = [
    {
        "id": "linux-01",
        "topic": "linux",
        "title": "First Contact 🐧 — files, trees and permissions",
        "vault_note": "Linux Fundamentals",
        "brief": ("Your first hour on a Linux box. Everything is a file, everything hangs off\n"
                  "one tree at /, and who may read what is decided by three little digits.\n\n"
                  "Build the workspace the course assignment uses, walk it, then lock a file\n"
                  "down so only you can read it. (Assignments 1–3 of the REAL graded sheet.)\n\n"
                  "🌍 You're on a real Linux box now — `setup` shows how the tools install there."),
        "world": {"files": dict(HOME_EXTRAS), "flags": {"dirs": set(HOME_DIRS)}},
        "handlers": HANDLERS,
        "help_fn": print_help_index,
        "prompt": prompt,
        "complete": complete,
        "objectives": [
            {"desc": "Create ~/linux_course with week1 and week2 inside it", "xp": 10,
             "hint": "mkdir makes directories; -p creates a whole path at once and never complains.",
             "check": lambda w: all(d in w.flags.get("dirs", set())
                                    for d in ("/root/linux_course", "/root/linux_course/week1",
                                              "/root/linux_course/week2"))},
            {"desc": "Walk into week2 with a RELATIVE path (no leading /), and pwd to confirm",
             "xp": 10,
             "hint": "From ~, the directions are linux_course/week2 — no slash in front. "
                     "`pwd` then tells you where that landed you.",
             "check": lambda w: w.flags.get("cwd") == "/root/linux_course/week2"},
            {"desc": "Now jump to /tmp with an ABSOLUTE path, then get home with a bare `cd`",
             "xp": 10,
             "hint": "An absolute path starts at the root: /tmp. `cd` with no argument at all "
                     "goes home from anywhere.",
             "check": lambda w: w.flags.get("cwd") == "/root" and w.flags.get("oldpwd") == "/tmp"},
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
            {"desc": "Your home hides files plain `ls` won't show. Copy ~/.bashrc to "
                     "week1/bashrc.bak", "xp": 10,
             "hint": "`ls -a` reveals the dotfiles. Then cp takes source first, destination "
                     "second — and leaves the original alone.",
             "check": lambda w: w.files.get("/root/linux_course/week1/bashrc.bak")
             and w.files.get("/root/linux_course/week1/bashrc.bak") == w.files.get("/root/.bashrc")},
        ],
        "teach": [
            "Linux has no drive letters — one tree from `/`, and `~` is your home. `mkdir -p` builds "
            "a whole branch in one go and stays quiet if it already exists, which is why scripts use it.",
            "A path with no leading `/` is RELATIVE: it starts from wherever `pwd` says you are, so "
            "the same words mean different places from different rooms. `pwd` before `cd` is how you "
            "stop guessing — and `cd` is case-sensitive, so Week2 is not week2.",
            "A path starting with `/` is ABSOLUTE — it means the same file from anywhere on the "
            "machine, which is why scripts and cron jobs are written that way. Bare `cd` goes home; "
            "`cd -` bounces back to where you just were.",
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
            "A leading dot hides a name from `ls` and from `*` globs — it's a convention, not a "
            "permission, and it's where every tool keeps its config. `cp` duplicates the file and "
            "leaves the original in place; that one word is the whole difference from `mv`.",
        ],
        "solution": [
            "pwd",
            "ls",
            "mkdir -p ~/linux_course/week1 ~/linux_course/week2",
            "cd linux_course/week2",
            "pwd",
            "cd /tmp",
            "pwd",
            "cd",
            "pwd",
            "cd ~/linux_course",
            "ls",
            'echo "Welcome to Linux!" > week1/intro.txt',
            "cat week1/intro.txt",
            "touch week1/private_data",
            "chmod 600 week1/private_data",
            "ls -l week1",
            "touch week2/file1.txt week2/file2.txt week2/file3.txt",
            'find ~/linux_course -name "*.txt"',
            "ls ~",
            "ls -a ~",
            "cp ~/.bashrc week1/bashrc.bak",
            "cat week1/bashrc.bak",
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
                  "The ~/linux_course workspace you built last mission is still here — `ls -R\n"
                  "~/linux_course` to see it.\n\n"
                  "(Assignments 4–6 of the REAL graded sheet.)"),
        "world": {"files": dict(HOME_EXTRAS, **WORKSPACE),
                  "flags": {"modes": dict(WORKSPACE_MODES), "dirs": set(HOME_DIRS)}},
        "handlers": HANDLERS,
        "help_fn": print_help_index,
        "prompt": prompt,
        "complete": complete,
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
            {"desc": "Save ONLY the sleep line to week2/procs.txt — ps piped through grep",
             "xp": 15,
             "hint": "ps aux | grep sleep > week2/procs.txt — the pipe filters, the > saves. "
                     "A plain `ps aux >` would drag every other process in with it.",
             "check": lambda w: "sleep" in w.files.get("/root/linux_course/week2/procs.txt", "")
             and "bash" not in w.files.get("/root/linux_course/week2/procs.txt", "")},
            {"desc": "Terminate the sleep process by its PID", "xp": 15,
             "hint": "kill <PID> — the number ps printed in the PID column.",
             "check": lambda w: w.flags.get("killed_sleep")},
            {"desc": "Start a SECOND background job, then leave the process table empty — "
                     "polite kill first, `kill -9` only if it won't go", "xp": 15,
             "hint": "sleep 120 & starts another one; `jobs` or `ps` gives you its PID; "
                     "kill -9 <PID> is the escalation.",
             "check": lambda w: w.flags.get("next_pid", 4821) >= 4823
             and not w.flags.get("procs")},
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
            "A pipeline is still one command, so `>` at the end saves what came out of the LAST stage: "
            "ps knows every process, grep throws away the ones you didn't ask about, and the file gets "
            "the answer. On a real box grep matches its own command line too — it was running as well.",
            "`kill` sends SIGTERM: 'please stop'. `kill -9` is SIGKILL, unstoppable and un-cleanable-"
            "up-after — reach for it only when TERM has already failed.",
            "Escalate in that order, always: TERM (15) lets a process flush its buffers and close its "
            "files; KILL (9) is the kernel ending it mid-sentence, which is how a database wakes up "
            "needing recovery. `jobs` lists what this shell backgrounded; `ps` sees the whole machine.",
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
            "ps aux | grep sleep > procs.txt",
            "cat procs.txt",
            "kill 4821",
            "sleep 120 &",
            "jobs",
            "kill -9 4822",
            "ps",
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
                  "That workspace is the ~/linux_course tree from the last two missions —\n"
                  "it's still here, log and all.\n\n"
                  "(Assignments 7–10 of the REAL graded sheet.)"),
        "world": {"files": dict(HOME_EXTRAS, **WORKSPACE,
                                **{"linux_course/week2/log.txt":
                                   "error: Disk space low\ninfo: System rebooted\n"
                                   "warning: High memory usage\n"}),
                  "flags": {"modes": dict(WORKSPACE_MODES), "dirs": set(HOME_DIRS)}},
        "handlers": HANDLERS,
        "help_fn": print_help_index,
        "prompt": prompt,
        "complete": complete,
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
            {"desc": "Spring the trap on purpose: install a crontab line with \"$(date)\" in "
                     "DOUBLE quotes, then read it back with crontab -l", "xp": 10,
             "hint": 'echo "* * * * * echo $(date) >> ~/linux_course/timestamp.log" | crontab - '
                     "— then look very closely at what `crontab -l` prints back.",
             # The desc asks for two things, so the check has to want both: the
             # frozen line, and the `crontab -l` that shows the player what they
             # actually installed. Reading your own crontab back IS the drill.
             "check": lambda w: (w.flags.get("crontab_listed")
                                 and any(FROZEN_DATE.search(ln) for ln in cron_lines(w)))},
            {"desc": "Now install it correctly: every minute, append the date to "
                     "~/linux_course/timestamp.log — with no timestamp baked into the line",
             "xp": 15,
             "hint": "Five stars = every minute. Single quotes stop YOUR shell expanding "
                     "$(date), so cron gets to do it — or just schedule `date` itself.",
             "check": lambda w: any("* * * * *" in ln and "timestamp.log" in ln
                                    and not FROZEN_DATE.search(ln) for ln in cron_lines(w))},
            {"desc": "Verify the job's OUTPUT, not just the schedule: get a real timestamp into "
                     "timestamp.log and read the file", "xp": 10,
             "hint": "This shell's clock doesn't tick between commands, so cron never fires here. "
                     "Run the job's own command by hand once — date >> ~/linux_course/timestamp.log "
                     "— then cat the file.",
             "check": lambda w: re.search(
                 r"\d\d:\d\d:\d\d", w.files.get("/root/linux_course/timestamp.log", ""))},
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
            "There it is, frozen: your shell ran `$(date)` the moment you pressed Enter, because "
            "double quotes don't stop substitution — so cron will append that one dead timestamp "
            "every minute forever. Whenever a command line is DATA for another program, ask which "
            "shell expands it and when.",
            "Cron's five fields are minute, hour, day-of-month, month, day-of-week. Single quotes "
            "hand `$(date)` to cron intact. Two more traps in that one line: `crontab -` REPLACES "
            "your whole table (crontab -e edits it), and cron reads no .bashrc, so write paths "
            "absolutely and escape any `%`.",
            "A cron job you have never seen output from is a guess, which is why the assignment asks "
            "you to check the FILE and not the crontab. Run the job's command by hand first: if it "
            "fails at the prompt it will fail silently at 03:00, and cron's mail is the only witness.",
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
            'echo "* * * * * echo $(date) >> ~/linux_course/timestamp.log" | crontab -',
            "crontab -l",
            "echo '* * * * * date >> ~/linux_course/timestamp.log' | crontab -",
            "crontab -l",
            "date >> ~/linux_course/timestamp.log",
            "cat ~/linux_course/timestamp.log",
            "cd ~",
            "tar -cvf linux_course.tar linux_course",
            "gzip linux_course.tar",
            "tar -tvf linux_course.tar.gz",
        ],
    },
    {
        "id": "linux-04",
        "topic": "linux",
        "title": "Dry Run 🧪 — the /tmp rehearsal",
        "vault_note": "Linux Fundamentals",
        "brief": ("The graded tree is built and archived. This is the rehearsal the note calls\n"
                  "the boss challenge: the extra terminal exercises, then every skill again\n"
                  "from memory in /tmp — where nothing you break is graded.\n\n"
                  "Copy a file without losing the original, watch `>` destroy a line, read a\n"
                  "permission string back off ls -l, unpack an archive somewhere new, and\n"
                  "then take the whole practice room apart with the right removal tool.\n\n"
                  "(The note's 🔬 drills + the 'more — extra terminal exercises' sheet.)"),
        "world": {"files": dict(HOME_EXTRAS, **WORKSPACE),
                  "flags": {"modes": dict(WORKSPACE_MODES), "dirs": set(HOME_DIRS)}},
        "handlers": HANDLERS,
        "help_fn": print_help_index,
        "prompt": prompt,
        "complete": complete,
        "objectives": [
            {"desc": "Extra 1A/1B: create ~/name.txt containing your name, then print it",
             "xp": 10,
             "hint": 'echo "Dana" > ~/name.txt writes it; cat ~/name.txt prints it back.',
             "check": lambda w: w.files.get("/root/name.txt", "").strip() != ""},
            {"desc": "Extra 1C: copy it into the Music folder — the original must stay in ~",
             "xp": 10,
             "hint": "cp <source> <destination>: cp ~/name.txt ~/Music/  — the trailing slash "
                     "says 'into that directory'.",
             "check": lambda w: w.files.get("/root/Music/name.txt") is not None
             and w.files.get("/root/Music/name.txt") == w.files.get("/root/name.txt")},
            {"desc": "Rename the COPY to your last name — with mv, and without touching ~/name.txt",
             "xp": 10,
             "hint": "Renaming IS moving: mv ~/Music/name.txt ~/Music/cohen.txt. Move the copy, "
                     "not the original.",
             # The original still in ~ AND the copy gone from under its old name
             # is exactly the difference between cp and mv, stated as world state.
             "check": lambda w: "/root/Music/name.txt" not in w.files
             and w.files.get("/root/name.txt") is not None
             and any(p.startswith("/root/Music/") and w.files[p] == w.files["/root/name.txt"]
                     for p in w.files)},
            {"desc": "The in-class exercise: in /tmp make ex1, and inside it Myfirstfile.txt and "
                     "Mysecondfile.txt, each with a line of text", "xp": 10,
             "hint": "cd /tmp, mkdir ex1, cd ex1, touch both names, then echo a line into each.",
             "check": lambda w: all(w.files.get(f"/tmp/ex1/My{n}file.txt", "").strip()
                                    for n in ("first", "second"))},
            {"desc": "Truncate drill: echo the SAME line into /tmp/ex1/count.txt twice with `>`, "
                     "then twice more with `>>` — end with exactly three identical lines", "xp": 10,
             "hint": "Two `>` runs leave one line, because each one empties the file first. "
                     "`wc -l count.txt` after every step is the whole lesson.",
             "check": lambda w: (lambda lines: len(lines) == 3 and lines[0].strip()
                                 and len(set(lines)) == 1)(
                 w.files.get("/tmp/ex1/count.txt", "").rstrip("\n").split("\n"))},
            {"desc": "Octal oracle: make ls -l read -rwxr-x--- for Myfirstfile.txt and "
                     "-rw------- for Mysecondfile.txt", "xp": 15,
             "hint": "rwx=4+2+1, r-x=4+1, ---=0. Set them with chmod, then read the strings back "
                     "with ls -l — the reading is the drill.",
             # Not the global "some long listing happened" flag: mission 1 seeds
             # a mode-tracked path, so any `ls -l` anywhere used to satisfy this.
             # Reading the string back off THESE two files is the half that
             # matters, and the teach line says so.
             "check": lambda w: w.flags.get("modes", {}).get("/tmp/ex1/Myfirstfile.txt") == 0o750
             and w.flags.get("modes", {}).get("/tmp/ex1/Mysecondfile.txt") == 0o600
             and {"/tmp/ex1/Myfirstfile.txt", "/tmp/ex1/Mysecondfile.txt"}
             <= w.flags.get("perms_seen", set())},
            {"desc": "Bundle both files into /tmp/ex1/ex1.tar and list it WITHOUT extracting",
             "xp": 15,
             "hint": "tar -cvf ex1.tar Myfirstfile.txt Mysecondfile.txt, then tar -tvf ex1.tar.",
             "check": lambda w: "/tmp/ex1/ex1.tar" in w.files and w.flags.get("listed_archive")},
            {"desc": "Round trip: extract the archive into a NEW folder and prove the contents "
                     "match the originals", "xp": 15,
             "hint": "mkdir /tmp/unpacked, cd into it, tar -xvf /tmp/ex1/ex1.tar — tar unpacks "
                     "into the current directory. Then cat both copies.",
             "check": lambda w: w.flags.get("extracted_archive") and any(
                 p != "/tmp/ex1/Myfirstfile.txt" and p.endswith("/Myfirstfile.txt")
                 and w.files[p] == w.files.get("/tmp/ex1/Myfirstfile.txt") for p in w.files)},
            {"desc": "Tear the practice room down: leave nothing at all under /tmp — files with "
                     "rm, the emptied directory with rmdir, the extracted tree with rm -r",
             "xp": 15,
             "hint": "rm ex1/*.txt ex1/ex1.tar clears the files; rmdir ex1 then works because it's "
                     "empty; rm -r unpacked takes the other one, contents and all.",
             # The flags are part of the check on purpose: /tmp starts empty, so
             # "nothing under /tmp" alone would tick the moment the mission began.
             "check": lambda w: w.flags.get("listed_archive") and w.flags.get("extracted_archive")
             and not any(p.startswith("/tmp/") for p in w.files)
             and not any(d.startswith("/tmp/") for d in w.flags.get("dirs", ()))},
        ],
        "teach": [
            "Creating a file and reading it back is the smallest complete loop in the shell: `echo` "
            "with `>` writes, `cat` proves it landed. Never trust a write you haven't read back.",
            "`cp` takes source first, destination second, and leaves the source exactly where it "
            "was. A trailing `/` on the destination is a real assertion — if that directory doesn't "
            "exist, cp refuses instead of silently creating a file with that name.",
            "`mv` is the same two arguments, but the original does NOT survive — which is why "
            "renaming and moving are the same command. cp then mv is how you copy-and-rename; mv "
            "alone on the original would have left ~ empty.",
            "This is the exercise from the class deck, and it's deliberately dull: make a room, put "
            "two files in it, put words in the files. Every later drill needs something to work on.",
            "`>` truncates the file to zero before writing, every single time — two runs still leave "
            "one line. `>>` appends, so two more runs make three. That is the entire difference, and "
            "it is how people erase a log they meant to add to.",
            "Read a triad at a time: rwx r-x --- is 7 5 0 — owner everything, group read and enter, "
            "others nothing. Writing the number is half the skill; reading the string back off "
            "`ls -l` is the half you actually use when something says Permission denied.",
            "`-c` create, `-t` lisT, `-x` eXtract, and `-f` names the file for all three. Listing "
            "before extracting is the habit: an archive is someone else's paths, and `tar -t` is how "
            "you look before you let them write into your directory.",
            "tar unpacks into the CURRENT directory, using the member names it stored — so cd into a "
            "fresh folder first and the extraction can't overwrite the original. Comparing the two "
            "copies is what proves the archive is really a backup and not just a file.",
            "`rmdir` removes only an EMPTY directory — that refusal is a safety feature, not an "
            "obstacle. `rm -r` removes a tree without asking twice, and there is no undo, no bin, no "
            "confirmation. Point it deliberately, and never at a path you built the graded work in.",
        ],
        "solution": [
            'echo "Dana" > ~/name.txt',
            "cat ~/name.txt",
            "ls ~",
            "cp ~/name.txt ~/Music/",
            "ls ~/Music",
            "mv ~/Music/name.txt ~/Music/cohen.txt",
            "ls ~/Music",
            "ls ~",
            "cd /tmp",
            "mkdir ex1",
            "cd ex1",
            "touch Myfirstfile.txt Mysecondfile.txt",
            'echo "first content" >> Myfirstfile.txt',
            'echo "second content" >> Mysecondfile.txt',
            "cat Myfirstfile.txt Mysecondfile.txt",
            'echo "practice line" > count.txt',
            'echo "practice line" > count.txt',
            "wc -l count.txt",
            'echo "practice line" >> count.txt',
            'echo "practice line" >> count.txt',
            "wc -l count.txt",
            "cat count.txt",
            "chmod 750 Myfirstfile.txt",
            "chmod 600 Mysecondfile.txt",
            "ls -l",
            "tar -cvf ex1.tar Myfirstfile.txt Mysecondfile.txt",
            "tar -tvf ex1.tar",
            "mkdir /tmp/unpacked",
            "cd /tmp/unpacked",
            "tar -xvf /tmp/ex1/ex1.tar",
            "ls -l",
            "cat Myfirstfile.txt",
            "cd /tmp",
            "rm ex1/Myfirstfile.txt ex1/Mysecondfile.txt ex1/count.txt ex1/ex1.tar",
            "rmdir ex1",
            "rm -r unpacked",
            "ls /tmp",
        ],
    },
]
