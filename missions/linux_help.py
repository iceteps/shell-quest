"""Manual pages for the 🐧 Linux shell — `ls --help`, `help grep`, `man find`.

Why hand-written pages instead of shelling out to the player's real `--help`?
Two reasons, both about honesty. The game must run identically on Windows and
macOS, where GNU coreutils either aren't there or answer differently — and
printing the real GNU page would advertise forty flags this shell doesn't
implement, which teaches a wrong model of the world the student is standing in.

So each page says three things: the real usage line (taken verbatim from GNU
coreutils / util-linux), the flags THIS shell actually honours, and — the part
that keeps it honest — which real flags exist that aren't simulated here. A
student who reads a page here can walk to a real terminal and recognise it.

Pages are plain text on purpose: `ls --help | grep -i sort` should work, and
colour codes inside a pipe would break the match.
"""
import textwrap

# usage:    the real tool's SYNOPSIS line
# summary:  one line, also used for the `help` index
# opts:     [(flag, what it does)] — ONLY flags this shell implements
# notes:    free lines under the options
# examples: [(command, what it teaches)]
# real:     flags the real tool has that this shell does not simulate
PAGES = {
    # ---- getting around -------------------------------------------------
    "pwd": {
        "usage": "pwd [OPTION]...",
        "summary": "Print the full path of the directory you are standing in.",
        "opts": [],
        "notes": ["There is one tree, rooted at /. Every path either starts at / "
                  "(absolute) or at where you are (relative)."],
        "examples": [("pwd", "answers 'where am I' — the first thing to type when lost")],
        "real": "-L (logical, keep symlinks) · -P (physical)",
    },
    "cd": {
        "usage": "cd [DIRECTORY]",
        "summary": "Change the working directory.",
        "opts": [],
        "notes": ["cd is a shell BUILTIN, not a program — it has to be, since a child "
                  "process could never move its parent.",
                  "With no argument it goes home. `-` goes back to where you just were."],
        "examples": [("cd /etc", "absolute: from the root of the tree"),
                     ("cd week1", "relative: from where you are now"),
                     ("cd ..", "up one level"),
                     ("cd ~/linux_course", "~ is your home directory, /root here"),
                     ("cd -", "back to the previous directory")],
        "real": "-L · -P · CDPATH",
    },
    "ls": {
        "usage": "ls [OPTION]... [FILE]...",
        "summary": "List directory contents.",
        "opts": [("-l", "long listing: permissions, owner, size, date"),
                 ("-a", "show entries starting with . (the hidden ones)"),
                 ("-R", "list subdirectories recursively"),
                 ("-d", "list the DIRECTORY itself, not what is inside it")],
        "notes": ["Flags combine: `ls -la` is the same as `ls -l -a`.",
                  "`ls -ld dir` is how you read a directory's own permission bits."],
        "examples": [("ls -l", "read the permission triads"),
                     ("ls -la ~", "everything in your home, dotfiles included"),
                     ("ls -R linux_course", "the whole tree below a directory")],
        "real": "-h (human sizes) · -t (by time) · -S (by size) · -r (reverse) · --color",
    },

    # ---- files and folders ----------------------------------------------
    "mkdir": {
        "usage": "mkdir [OPTION]... DIRECTORY...",
        "summary": "Create directories.",
        "opts": [("-p", "create parents as needed, and never complain if it exists")],
        "notes": ["Without -p the parent must already exist — that is the error people "
                  "hit first.",
                  "-p is what scripts use, because running them twice must not fail."],
        "examples": [("mkdir week1", "one directory, here"),
                     ("mkdir -p ~/linux_course/week1", "the whole branch in one go"),
                     ("mkdir a b c", "three at once")],
        "real": "-m MODE (set permissions at creation) · -v",
    },
    "rmdir": {
        "usage": "rmdir [OPTION]... DIRECTORY...",
        "summary": "Remove EMPTY directories.",
        "opts": [],
        "notes": ["rmdir refuses a directory with anything in it — that refusal is a "
                  "safety feature. `rm -r` is the one that clears a tree, and it does "
                  "not ask twice."],
        "examples": [("rmdir old_notes", "works only if old_notes is empty")],
        "real": "-p (remove parents too) · --ignore-fail-on-non-empty",
    },
    "touch": {
        "usage": "touch [OPTION]... FILE...",
        "summary": "Create an empty file, or update an existing file's timestamp.",
        "opts": [],
        "notes": ["An empty file is a perfectly valid file.",
                  "touch takes many names at once — one call, three files."],
        "examples": [("touch notes.txt", "create it empty"),
                     ("touch file1.txt file2.txt file3.txt", "three in one call")],
        "real": "-a · -m · -t STAMP · -r REFERENCE · -c (don't create)",
    },
    "cat": {
        "usage": "cat [OPTION]... [FILE]...",
        "summary": "Print a file to the screen (concatenate, really).",
        "opts": [("-n", "number the output lines")],
        "notes": ["cat prints the WHOLE file — for a long one, `head`, `tail` or `less` "
                  "are kinder.",
                  "Given several files it joins them, which is where the name comes from."],
        "examples": [("cat /etc/passwd", "one account per line, fields split by :"),
                     ("cat a.txt b.txt > both.txt", "join two files into a third")],
        "real": "-A · -b · -s · -E · -T",
    },
    "cp": {
        "usage": "cp [OPTION]... SOURCE... DEST",
        "summary": "Copy files and directories.",
        "opts": [("-r", "copy a directory and everything inside it (also -R)")],
        "notes": ["Copying a directory without -r is refused: cp will not silently "
                  "recurse.",
                  "If DEST is an existing directory, the sources are copied INTO it."],
        "examples": [("cp notes.txt backup.txt", "duplicate a file"),
                     ("cp -r week1 week1_backup", "duplicate a whole tree"),
                     ("cp a.txt b.txt archive/", "several files into a directory")],
        "real": "-i (ask first) · -u · -p (preserve mode/time) · -v · -a",
    },
    "mv": {
        "usage": "mv [OPTION]... SOURCE... DEST",
        "summary": "Move or rename files and directories.",
        "opts": [],
        "notes": ["Rename and move are the same operation — a name IS a location.",
                  "mv overwrites the destination without asking. That is why `-i` exists "
                  "on real boxes."],
        "examples": [("mv draft.txt final.txt", "rename"),
                     ("mv final.txt ~/linux_course/", "move into a directory")],
        "real": "-i · -n (never overwrite) · -u · -v · -b (backup)",
    },
    "rm": {
        "usage": "rm [OPTION]... [FILE]...",
        "summary": "Remove files, and with -r whole directories.",
        "opts": [("-r", "remove a directory and its contents, recursively"),
                 ("-f", "never prompt, never complain about missing files"),
                 ("-d", "remove an empty directory")],
        "notes": ["There is no undelete and no recycle bin. `rm -rf` on the wrong path "
                  "is the classic career-shortening command — read the line before you "
                  "press Enter.",
                  "A leading `-` in a filename looks like a flag; `rm ./-weird` is the "
                  "way out."],
        "examples": [("rm notes.txt", "one file"),
                     ("rm -r old_project", "a whole directory tree"),
                     ("rm -f maybe_missing.txt", "no error if it isn't there")],
        "real": "-i (ask per file) · -I · -v · --preserve-root · --one-file-system",
    },
    "find": {
        "usage": "find [PATH...] [EXPRESSION]",
        "summary": "Walk a directory tree looking for files that match a test.",
        "opts": [("-name PATTERN", "filename matches the glob (quote it!)"),
                 ("-iname PATTERN", "same, ignoring case"),
                 ("-type f|d", "only files / only directories"),
                 ("-maxdepth N", "don't descend more than N levels")],
        "notes": ['Quote the pattern: unquoted, the SHELL expands *.txt against the '
                  'current directory before find ever sees it.',
                  "find prints paths, so it pipes beautifully: `find . -name '*.log' | wc -l`."],
        "examples": [('find ~/linux_course -name "*.txt"', "every .txt under a tree"),
                     ("find . -type d", "just the directories"),
                     ("find . -maxdepth 1 -type f", "files here, no recursion")],
        "real": "-size · -mtime · -perm · -user · -exec CMD {} \\; · -delete · -newer",
    },
    "edit": {
        "usage": "edit FILE",
        "summary": "This world's tiny text editor (there is no vim or nano here).",
        "opts": [],
        "notes": ["Type your lines, then a single `.` on its own line to save.",
                  "Anything you type REPLACES the file's contents.",
                  "On a real box you would use nano (friendly), vim or emacs. Learning "
                  "one of them properly is a real day-one skill."],
        "examples": [("edit hello.sh", "write a script by hand")],
        "real": "n/a — this is the game's stand-in for a real editor",
    },
    "less": {
        "usage": "less [FILE]...",
        "summary": "A pager: read a long file a screen at a time.",
        "opts": [],
        "notes": ["This shell cannot paint a full-screen pager, so it prints the file "
                  "and says so.",
                  "On a real box: Space pages down, `/word` searches, `q` quits. `man` "
                  "uses less to show you pages."],
        "examples": [("less /var/log/syslog", "read a long log without flooding the screen")],
        "real": "everything interactive — /search, ?back, G, g, -N line numbers",
    },

    # ---- text -----------------------------------------------------------
    "echo": {
        "usage": "echo [-neE] [STRING]...",
        "summary": "Print its arguments.",
        "opts": [("-n", "do not output the trailing newline"),
                 ("-e", "interpret backslash escapes like \\n and \\t"),
                 ("-E", "do NOT interpret them (the default)")],
        "notes": ["Without -e, \\n is two literal characters — a small thing that "
                  "silently ruins generated files.",
                  "Quoting decides expansion: \"$HOME\" becomes /root, '$HOME' stays "
                  "the four characters."],
        "examples": [('echo "Welcome to Linux!" > intro.txt', "write a one-line file"),
                     ('echo -e "a\\nb" > two_lines.txt', "two real lines"),
                     ("echo $?", "the exit code of the last command")],
        "real": "as a /usr/bin/echo binary it also takes --help/--version; the builtin does not",
    },
    "printf": {
        "usage": "printf FORMAT [ARGUMENT]...",
        "summary": "Print with a format string — the precise cousin of echo.",
        "opts": [],
        "notes": ["Escapes are ALWAYS interpreted (no -e needed) and no newline is "
                  "added unless the format has one.",
                  "The format is REUSED until the arguments run out, which is why "
                  "`printf '%s\\n' a b c` prints three lines."],
        "examples": [("printf 'name: %s\\n' student", "one formatted line"),
                     ("printf 'a'", "one byte, no newline — compare `wc -c` with echo")],
        "real": "%b · %q · -v VAR",
    },
    "grep": {
        "usage": "grep [OPTION]... PATTERN [FILE]...",
        "summary": "Print the lines that match a pattern.",
        "opts": [("-i", "ignore case"), ("-v", "invert: print lines that do NOT match"),
                 ("-n", "prefix each line with its line number"),
                 ("-c", "print only the count of matching lines"),
                 ("-l", "print only the names of files that matched"),
                 ("-w", "match whole words only"),
                 ("-q", "quiet: print nothing, just set the exit code"),
                 ("-r", "search a directory tree recursively"),
                 ("-E", "extended regular expressions (grep -E == egrep)"),
                 ("-F", "fixed strings: treat the pattern literally")],
        "notes": ["Exit code 0 = something matched, 1 = nothing did. That is what makes "
                  "`grep -q` the standard test inside scripts.",
                  "Quote the pattern so the shell doesn't glob it first."],
        "examples": [('grep "error" log.txt', "the classic log filter"),
                     ('grep -i error log.txt > error.log', "case-insensitive, saved to a file"),
                     ("ps aux | grep sleep", "filter another command's output")],
        "real": "-A/-B/-C (context) · -o · --color · -f FILE · -P (perl regex)",
    },
    "head": {
        "usage": "head [OPTION]... [FILE]...",
        "summary": "Print the FIRST lines of a file (10 by default).",
        "opts": [("-n N", "print N lines"), ("-N", "shorthand for -n N")],
        "notes": ["`head` and `tail` are how you look at a big file without printing "
                  "megabytes into your terminal."],
        "examples": [("head -n 5 /etc/passwd", "the first five accounts"),
                     ("sort scores.txt | head -3", "top three after sorting")],
        "real": "-c BYTES · -q · -v",
    },
    "tail": {
        "usage": "tail [OPTION]... [FILE]...",
        "summary": "Print the LAST lines of a file (10 by default).",
        "opts": [("-n N", "print N lines"), ("-N", "shorthand for -n N")],
        "notes": ["On a real box `tail -f` follows a log as it grows — the single most "
                  "used debugging command there is. It would never end here, so it "
                  "isn't simulated."],
        "examples": [("tail -n 20 /var/log/syslog", "the newest lines of a log")],
        "real": "-f / -F (follow) · -c BYTES · --pid",
    },
    "wc": {
        "usage": "wc [OPTION]... [FILE]...",
        "summary": "Count lines, words and bytes.",
        "opts": [("-l", "lines"), ("-w", "words"), ("-c", "bytes"), ("-m", "characters")],
        "notes": ["With no flags it prints all three: lines, words, bytes.",
                  "`wc -c` counts the trailing newline too — `echo a > f` is 2 bytes, "
                  "`printf a > f` is 1."],
        "examples": [("wc -l /etc/passwd", "how many accounts"),
                     ("ls | wc -l", "how many entries here"),
                     ("wc -l < f", "no filename in the output, because wc read stdin")],
        "real": "-L (longest line) · --files0-from",
    },
    "sort": {
        "usage": "sort [OPTION]... [FILE]...",
        "summary": "Sort lines of text.",
        "opts": [("-n", "numeric sort (2 before 10)"), ("-r", "reverse"),
                 ("-u", "unique: drop duplicate lines")],
        "notes": ["Without -n, sorting is textual: '10' comes before '2'.",
                  "Dictionary order here, like a desktop Linux: `alpha` before `Beta`."],
        "examples": [("sort names.txt", "alphabetical"),
                     ("sort -nr sizes.txt | head -3", "three biggest numbers")],
        "real": "-k FIELD · -t SEP · -h · -o FILE · -R (random)",
    },
    "uniq": {
        "usage": "uniq [OPTION]... [INPUT]",
        "summary": "Collapse ADJACENT duplicate lines.",
        "opts": [("-c", "prefix each line with how many times it occurred")],
        "notes": ["Adjacent is the catch: uniq only sees neighbours, so it is almost "
                  "always used as `sort | uniq` (or `sort -u`).",
                  "`sort file | uniq -c | sort -nr` is the standard 'count the top "
                  "offenders' one-liner."],
        "examples": [("sort log.txt | uniq -c", "how many times each line appears")],
        "real": "-d (only duplicates) · -u (only uniques) · -i · -f N",
    },
    "tac": {
        "usage": "tac [FILE]...",
        "summary": "Print a file backwards, last line first (cat, reversed).",
        "opts": [],
        "notes": ["Handy on logs, where the newest line is at the bottom."],
        "examples": [("tac /var/log/syslog | head -5", "the five newest lines")],
        "real": "-s SEP · -b · -r",
    },
    "cut": {
        "usage": "cut OPTION... [FILE]...",
        "summary": "Cut selected fields or characters out of each line.",
        "opts": [("-d DELIM", "the field delimiter (TAB by default)"),
                 ("-f LIST", "which fields to keep: 1, 1,3, 1-3"),
                 ("-c LIST", "which characters to keep, by position")],
        "notes": ["/etc/passwd is the canonical example: colon-separated records, one "
                  "per line.",
                  "cut splits on a SINGLE character — for whitespace-aligned columns, "
                  "awk is the real answer."],
        "examples": [("cut -d: -f1 /etc/passwd", "just the usernames"),
                     ("cut -d: -f1,7 /etc/passwd", "username and login shell")],
        "real": "--complement · -s (skip lines with no delimiter) · --output-delimiter",
    },
    "seq": {
        "usage": "seq [FIRST [INCREMENT]] LAST",
        "summary": "Print a sequence of numbers, one per line.",
        "opts": [],
        "notes": ["Useful for generating test data and for loops on a real box."],
        "examples": [("seq 5", "1 to 5"), ("seq 2 2 10", "even numbers up to 10")],
        "real": "-s SEP · -w (equal width) · -f FORMAT",
    },
    "basename": {
        "usage": "basename NAME [SUFFIX]",
        "summary": "Strip the directory (and optionally a suffix) off a path.",
        "opts": [],
        "notes": ["basename and dirname split a path into its two halves — scripts use "
                  "them constantly."],
        "examples": [("basename /root/linux_course/intro.txt", "→ intro.txt"),
                     ("basename intro.txt .txt", "→ intro")],
        "real": "-a · -s SUFFIX · -z",
    },
    "dirname": {
        "usage": "dirname NAME...",
        "summary": "Print the directory part of a path.",
        "opts": [],
        "notes": [],
        "examples": [("dirname /root/linux_course/intro.txt", "→ /root/linux_course")],
        "real": "-z",
    },

    # ---- permissions ----------------------------------------------------
    "chmod": {
        "usage": "chmod [OPTION]... MODE[,MODE]... FILE...",
        "summary": "Change the permission bits of files and directories.",
        "opts": [("-R", "apply to a directory and everything inside it")],
        "notes": ["MODE is octal (600, 755, 4755) or symbolic (u+x, go-w, a=r); "
                  "symbolic clauses join with commas: u+x,g+w.",
                  "Three digits = owner, group, others. read 4 + write 2 + execute 1.",
                  "  600 = rw------- (only you)      644 = rw-r--r-- (world-readable)",
                  "  755 = rwxr-xr-x (a program)     700 = rwx------ (a private dir)",
                  "On a directory, x means 'may enter', not 'may run'.",
                  "The reflex to resist is chmod 777 — it hands write access to every "
                  "account on the box, and it is never the fix."],
        "examples": [("chmod 600 private_data", "only the owner may read or write"),
                     ("chmod +x hello.sh", "make a script runnable"),
                     ("chmod -R 755 ~/site", "a whole tree"),
                     ("ls -l private_data", "read the triads back")],
        "real": "--reference=FILE · -c · -v · --preserve-root",
    },
    "chown": {
        "usage": "chown [OPTION]... [OWNER][:[GROUP]] FILE...",
        "summary": "Change which user and group own a file.",
        "opts": [("-R", "recursive")],
        "notes": ["You are root in this world and own everything, so chown has little "
                  "to bite on here — but on a real box, 'Permission denied on a file I "
                  "own' is usually an ownership question, not a mode question."],
        "examples": [("chown student:student notes.txt", "hand a file to another user")],
        "real": "--from=CURRENT · --reference · -h (symlinks)",
    },
    "sudo": {
        "usage": "sudo [-E] [-H] COMMAND [ARG]...",
        "summary": "Run one command as another user, normally root.",
        "opts": [],
        "notes": ["You are already root here, so sudo changes nothing — but it still "
                  "RUNS the command, because the muscle memory matters.",
                  "On your own box, sudo is what stands between a typo and a broken "
                  "system. Prefer it over logging in as root."],
        "examples": [("sudo dnf install tree", "the real-box shape of an install")],
        "real": "-u USER · -i · -s · sudoers rules · a password prompt",
    },
    "whoami": {
        "usage": "whoami [OPTION]...",
        "summary": "Print the current user's name.",
        "opts": [], "notes": [],
        "examples": [("whoami", "'am I root or myself right now?'")],
        "real": "--version",
    },
    "id": {
        "usage": "id [OPTION]... [USER]",
        "summary": "Print user and group IDs.",
        "opts": [], "notes": ["uid 0 IS root — the name is a convention, the number is the power."],
        "examples": [("id", "your uid, gid and groups")],
        "real": "-u · -g · -G · -n",
    },
    "groups": {
        "usage": "groups [USERNAME]...",
        "summary": "Print the groups a user belongs to.",
        "opts": [], "notes": ["Group membership is how a real box grants access to docker, "
                              "sudo or a shared folder."],
        "examples": [("groups", "which groups you are in")],
        "real": "--version",
    },

    # ---- processes and time ---------------------------------------------
    "ps": {
        "usage": "ps [OPTIONS]",
        "summary": "List processes.",
        "opts": [("aux", "BSD style: every process, with user and CPU columns"),
                 ("-ef", "System V style: the same idea, different columns")],
        "notes": ["The PID column is what `kill` needs.",
                  "`ps aux | grep <name>` is the standard hunt — and the grep itself "
                  "shows up in the list on a real box."],
        "examples": [("ps aux", "everything"), ("ps aux | grep sleep", "just the sleeps")],
        "real": "-o (choose columns) · --sort · -u USER · ps -p PID",
    },
    "kill": {
        "usage": "kill [-SIGNAL] PID...",
        "summary": "Send a signal to a process — by default 'please terminate'.",
        "opts": [("-9", "SIGKILL: unstoppable, un-cleanable-up-after"),
                 ("-15", "SIGTERM: the polite default")],
        "notes": ["kill does not mean kill, it means signal. TERM asks a process to shut "
                  "down cleanly; KILL removes it without letting it save anything.",
                  "Reach for -9 only after TERM has already failed."],
        "examples": [("kill 4821", "ask it to stop"), ("kill -9 4821", "force it")],
        "real": "kill -l · killall · pkill · kill %1 (job specs)",
    },
    "sleep": {
        "usage": "sleep NUMBER[SUFFIX]",
        "summary": "Do nothing for N seconds.",
        "opts": [],
        "notes": ["Useless alone, perfect for practising jobs: `sleep 300 &` gives you a "
                  "process to find and kill.",
                  "Without `&` a real shell BLOCKS until it finishes — this one says so "
                  "rather than pretending."],
        "examples": [("sleep 300 &", "background it and get the prompt back"),
                     ("jobs", "what is running in the background")],
        "real": "suffixes m/h/d · multiple arguments",
    },
    "jobs": {
        "usage": "jobs",
        "summary": "List the background jobs of this shell.",
        "opts": [],
        "notes": ["`&` backgrounds a job and prints its PID. jobs shows what is still "
                  "running; `fg` would bring one back to the foreground on a real box."],
        "examples": [("jobs", "after `sleep 300 &`")],
        "real": "fg · bg · Ctrl-Z · %1 job specs",
    },
    "date": {
        "usage": "date [OPTION]... [+FORMAT]",
        "summary": "Print the date and time.",
        "opts": [("+FORMAT", "print in your own format: +%Y-%m-%d")],
        "notes": ["Inside $( ) it becomes a timestamp you can bake into a filename or a "
                  "log line.",
                  "Quoting matters in crontab: \"$(date)\" freezes NOW into the line, "
                  "'$(date)' hands the job to cron."],
        "examples": [("date", "now"), ("date +%Y-%m-%d", "just the date"),
                     ('echo "backup $(date)" >> log.txt', "a timestamped log line")],
        "real": "-d STRING · -u · -s (set the clock) · -R",
    },
    "crontab": {
        "usage": "crontab [-l | -e | -r | FILE | -]",
        "summary": "Manage the scheduled jobs of the current user.",
        "opts": [("-l", "list the current crontab"),
                 ("-e", "edit it (this world's tiny editor)"),
                 ("-r", "remove it entirely"),
                 ("-", "read the new crontab from stdin — how scripts install one")],
        "notes": ["Five fields, then the command:  minute hour day-of-month month day-of-week",
                  "  * * * * *   every minute        0 3 * * *   03:00 every day",
                  "  */5 * * * * every five minutes  0 0 * * 0   midnight on Sundays",
                  "cron runs with a bare environment and a different cwd — always use "
                  "ABSOLUTE paths in a cron line.",
                  "Redirect the output somewhere, or cron will try to mail it to you."],
        "examples": [("crontab -l", "what is scheduled"),
                     ("echo '* * * * * date >> ~/timestamp.log' | crontab -",
                      "install a one-line crontab")],
        "real": "-u USER · /etc/cron.d · @reboot · systemd timers",
    },
    "history": {
        "usage": "history",
        "summary": "List the commands you have typed this session.",
        "opts": [],
        "notes": ["The arrow keys walk the same list; Ctrl-R searches it on a real bash.",
                  "On a real box it persists in ~/.bash_history — which is also why you "
                  "never type a password on a command line."],
        "examples": [("history", "what did I just run?"), ("history | grep chmod", "find that one command")],
        "real": "!! · !42 · Ctrl-R · HISTSIZE · history -c",
    },

    # ---- system and network ---------------------------------------------
    "df": {
        "usage": "df [OPTION]... [FILE]...",
        "summary": "Report free disk space per filesystem.",
        "opts": [("-h", "human-readable sizes (220G instead of 230686720)")],
        "notes": ["df answers 'is the DISK full'. du answers 'what is using it'. That "
                  "pair is the whole disk-space investigation."],
        "examples": [("df -h", "the readable version"),
                     ("df -h > disk_report.txt", "start a report")],
        "real": "-i (inodes) · -T (fs type) · --total",
    },
    "du": {
        "usage": "du [OPTION]... [FILE]...",
        "summary": "Report how much space files and directories use.",
        "opts": [("-s", "summarise: one total per argument"),
                 ("-h", "human-readable sizes")],
        "notes": ["Without -s, du prints every subdirectory — informative and enormous.",
                  "`du -sh *` is the standard 'who ate my disk' one-liner."],
        "examples": [("du -sh ~/linux_course", "one number for a whole tree"),
                     ("du -sh ~/linux_course >> disk_report.txt", "append to a report")],
        "real": "-a · -d N (depth) · --max-depth · --exclude · -c",
    },
    "uname": {
        "usage": "uname [OPTION]...",
        "summary": "Print system information — kernel, host, architecture.",
        "opts": [("-a", "everything"), ("-r", "kernel release"), ("-s", "kernel name"),
                 ("-n", "hostname"), ("-m", "machine architecture")],
        "notes": ["`uname -r` is the first thing to check when a driver or a container "
                  "image complains about the kernel."],
        "examples": [("uname -a", "the whole line"), ("uname -r", "just the kernel version")],
        "real": "-v · -p · -i · -o",
    },
    "hostname": {
        "usage": "hostname",
        "summary": "Print the machine's name.",
        "opts": [], "notes": ["/etc/hostname holds it; `hostnamectl` sets it on Fedora."],
        "examples": [("hostname", "who am I talking to?")],
        "real": "-I (addresses) · -f (FQDN) · setting it as root",
    },
    "ip": {
        "usage": "ip [OPTIONS] OBJECT {COMMAND}",
        "summary": "Show and configure network interfaces, addresses and routes.",
        "opts": [("a / addr", "addresses per interface (`ip a` is the everyday form)"),
                 ("link", "the interfaces themselves"),
                 ("r / route", "the routing table")],
        "notes": ["`ip` replaced `ifconfig` — on a modern Fedora net-tools isn't even "
                  "installed. Knowing which command is current is half of not looking "
                  "lost on someone else's server.",
                  "lo is loopback (127.0.0.1, the machine talking to itself); eth0 is "
                  "the real interface."],
        "examples": [("ip a", "every address on the box"), ("ip r", "where packets go")],
        "real": "ip addr add/del · ip link set · ip -br · netns",
    },
    "ping": {
        "usage": "ping [-c COUNT] DESTINATION",
        "summary": "Send ICMP echo requests — 'is that host reachable?'",
        "opts": [("-c N", "stop after N packets")],
        "notes": ["Without -c it runs until you press Ctrl-C. In a script, an unbounded "
                  "ping is a job that hangs forever.",
                  "A ping that fails does not always mean 'down' — plenty of hosts drop "
                  "ICMP on purpose."],
        "examples": [("ping -c 4 google.com", "four packets and a summary"),
                     ("ping -c 4 google.com > ping_output.txt", "save the evidence")],
        "real": "-i INTERVAL · -s SIZE · -W TIMEOUT · ping6",
    },
    "which": {
        "usage": "which COMMAND...",
        "summary": "Show which file on $PATH would run for a command name.",
        "opts": [],
        "notes": ["On PATH = installed. This is the check to run BEFORE any install step.",
                  "`command -v` is the portable version scripts use; `type` also knows "
                  "about builtins and aliases.",
                  "On Windows the equivalent is `where`."],
        "examples": [("which docker", "is docker installed?"),
                     ("which python3 git", "several at once")],
        "real": "-a (all matches) · exit codes for scripting",
    },
    "type": {
        "usage": "type NAME...",
        "summary": "Say what a name is: a builtin, a file on PATH, or an alias.",
        "opts": [],
        "notes": ["`type cd` proving cd is a shell builtin is the cleanest way to "
                  "understand why cd cannot be a program."],
        "examples": [("type ls", "where ls comes from")],
        "real": "-a · -t (type only) · -P",
    },
    "clear": {
        "usage": "clear",
        "summary": "Clear the terminal screen and its scrollback.",
        "opts": [],
        "notes": ["Ctrl-L does the same thing without typing anything.",
                  "It clears the DISPLAY, not your history — `history` still has everything."],
        "examples": [("clear", "start from a clean screen")],
        "real": "-x (keep scrollback) · reset (a harder terminal fix)",
    },

    # ---- archives -------------------------------------------------------
    "tar": {
        "usage": "tar [OPTION...] [FILE]...",
        "summary": "Bundle many files into one archive (and unpack them again).",
        "opts": [("-c", "create an archive"), ("-t", "list what is inside one"),
                 ("-x", "extract"), ("-v", "verbose: name every file"),
                 ("-f FILE", "the archive filename — it must come last in a bundle"),
                 ("-z", "compress/decompress with gzip in the same step")],
        "notes": ["tar bundles, gzip compresses. `.tar.gz` is literally both steps, "
                  "which is why the extension has two parts.",
                  "Always LIST before you extract: an archive can write anywhere its "
                  "paths point.",
                  "Mnemonics: -cvf create, -tvf test/list, -xvf extract."],
        "examples": [("tar -cvf linux_course.tar linux_course", "bundle a directory"),
                     ("tar -tvf linux_course.tar.gz", "look inside without unpacking"),
                     ("tar -xvf linux_course.tar", "unpack it here"),
                     ("tar -czvf backup.tar.gz linux_course", "bundle and compress in one go")],
        "real": "-C DIR · --exclude · -j (bzip2) · -J (xz) · --strip-components",
    },
    "gzip": {
        "usage": "gzip [OPTION]... [FILE]...",
        "summary": "Compress a single file, replacing it with FILE.gz.",
        "opts": [("-d", "decompress (same as gunzip)")],
        "notes": ["gzip replaces the original — that surprises people once, and only "
                  "once. `-k` keeps it on a real box.",
                  "It compresses ONE file. To compress many, tar them together first."],
        "examples": [("gzip linux_course.tar", "→ linux_course.tar.gz")],
        "real": "-k (keep) · -1..-9 (level) · -l (list) · -c (to stdout)",
    },
    "gunzip": {
        "usage": "gunzip [OPTION]... [FILE]...",
        "summary": "Decompress a .gz file, restoring the original name.",
        "opts": [],
        "notes": ["Exactly `gzip -d`."],
        "examples": [("gunzip linux_course.tar.gz", "→ linux_course.tar")],
        "real": "-k · -c · -t (test integrity)",
    },

    # ---- shell topics (not commands — concepts worth a page) ------------
    "redirection": {
        "usage": "command > FILE   command >> FILE   command < FILE   command 2> FILE",
        "summary": "Send a command's output into a file instead of the screen.",
        "opts": [(">", "stdout into a file, REPLACING its contents"),
                 (">>", "stdout appended to the end of a file"),
                 ("<", "read stdin FROM a file"),
                 ("2>", "stderr (the error stream) into a file"),
                 ("2>&1", "send stderr to wherever stdout is currently going"),
                 ("> /dev/null", "throw it away")],
        "notes": ["Every process has three streams: stdin (0), stdout (1), stderr (2). "
                  "Errors are a SEPARATE stream, which is why `cmd > f` still shows "
                  "them on screen.",
                  "`>` truncates the file the moment the command starts — mixing up > "
                  "and >> is how people erase files they meant to add to."],
        "examples": [("df -h > disk_report.txt", "start a report"),
                     ("du -sh ~ >> disk_report.txt", "add to it"),
                     ("cat nope 2> errors.log", "keep the error, not the noise"),
                     ("cat nope > out.txt 2>&1", "both streams into one file")],
        "real": "here-docs (<<EOF) · &> · exec redirections · process substitution",
    },
    "pipes": {
        "usage": "command1 | command2",
        "summary": "Feed one command's output straight into the next one's input.",
        "opts": [("|", "stdout of the left becomes stdin of the right"),
                 ("|&", "stdout AND stderr of the left")],
        "notes": ["This is the single most important idea in the Unix shell: small "
                  "tools, each doing one thing, chained.",
                  "The exit code of a pipeline is the code of the LAST command."],
        "examples": [("ps aux | grep sleep", "filter a listing"),
                     ("cat log.txt | grep error | wc -l", "how many errors"),
                     ("sort names.txt | uniq -c | sort -nr | head -3", "top three")],
        "real": "|& shorthand · pipefail · tee · named pipes (mkfifo)",
    },
    "globs": {
        "usage": "*.txt   file?.log   [abc]*   {1,2,3}   {1..5}",
        "summary": "Patterns the SHELL expands into filenames before the command runs.",
        "opts": [("*", "any run of characters (never a leading dot)"),
                 ("?", "exactly one character"),
                 ("[abc]", "one character from the set"),
                 ("{a,b}", "brace expansion — text, not filenames"),
                 ("{1..5}", "a range")],
        "notes": ["The command never sees the pattern: the shell replaces it first. "
                  "That is why `find . -name *.txt` needs quotes and `find . -name "
                  "'*.txt'` works.",
                  "A pattern that matches nothing is passed through literally.",
                  "Dotfiles hide from * on purpose — which is why `rm *` leaves "
                  ".bashrc alone."],
        "examples": [("ls *.txt", "every .txt here"),
                     ("touch file{1,2,3}.txt", "three files, no loop"),
                     ("rm week?/tmp.log", "one character wildcard")],
        "real": "extglob · globstar (**) · nullglob · character classes [[:digit:]]",
    },
    "variables": {
        "usage": "$HOME  $PWD  $USER  $?  \"$VAR\"  '$VAR'",
        "summary": "Values the shell expands before running the command.",
        "opts": [("$HOME", "your home directory (/root here)"),
                 ("$PWD", "the current directory"), ("$USER", "your username"),
                 ("$?", "the exit code of the last command — 0 means success"),
                 ("$1 $2 $#", "a script's arguments, and how many there are")],
        "notes": ["Double quotes expand, single quotes do not: \"$HOME\" becomes /root, "
                  "'$HOME' stays literal. That asymmetry is the whole cron-quoting trap.",
                  "This world keeps no environment between commands, so `export` is "
                  "explained rather than simulated."],
        "examples": [('echo "home is $HOME"', "expanded"), ("echo '$HOME'", "literal"),
                     ("grep zzz f; echo $?", "1 — grep found nothing")],
        "real": "export · env · unset · ${VAR:-default} · arrays",
    },
    "scripts": {
        "usage": "#!/bin/bash  … then chmod +x file … then ./file",
        "summary": "Put commands in a file and run it as a program.",
        "opts": [("#!/bin/bash", "the shebang: which interpreter runs this file"),
                 ("chmod +x file", "add the execute bit"),
                 ("./file", "run it by path — a bare name only works if it is on $PATH"),
                 ("$1 $2 $#", "arguments passed in"),
                 ("exit N", "the script's exit code")],
        "notes": ["The shebang is not a comment — it is the loading instruction. Without "
                  "it the file is just text.",
                  "Forgetting chmod +x gives 'Permission denied' on a file you own, "
                  "which is the most common first-script error.",
                  "`./` is required because `.` is not on $PATH — deliberately, for "
                  "security."],
        "examples": [('echo -e "#!/bin/bash\\necho hello" > hi.sh', "write it"),
                     ("chmod +x hi.sh", "make it runnable"),
                     ("./hi.sh", "run it"), ("bash hi.sh", "run it without the x bit")],
        "real": "set -euo pipefail · functions · if/for/while · trap · getopts",
    },
    "permissions": {
        "usage": "ls -l   chmod MODE FILE   chown USER FILE",
        "summary": "Who may read, write and execute — the three triads in ls -l.",
        "opts": [("-rw-r--r--", "type, then owner, group, others"),
                 ("r = 4", "read"), ("w = 2", "write"), ("x = 1", "execute / enter a directory")],
        "notes": ["First character is the TYPE: - a file, d a directory, l a symlink.",
                  "Then three triads: owner, group, everyone else.",
                  "  600 rw------- private     644 rw-r--r-- readable by all",
                  "  755 rwxr-xr-x a program   700 rwx------ a private directory",
                  "On a directory x means 'may enter' and r means 'may list' — a "
                  "directory you cannot enter hides everything inside it."],
        "examples": [("ls -l private_data", "read the triads"),
                     ("chmod 600 private_data", "lock it to the owner"),
                     ("ls -ld week1", "the DIRECTORY's own bits, not its contents")],
        "real": "umask · setuid/setgid/sticky · ACLs (getfacl) · SELinux contexts",
    },
}

# Pages that describe an IDEA rather than a binary — no `foo --help` behind them.
TOPICS = {"redirection", "pipes", "globs", "variables", "scripts", "permissions"}

# Aliases: different names, same page.
ALIASES = {"egrep": "grep", "fgrep": "grep", "more": "less", "R": "ls",
           "sh": "scripts", "bash": "scripts", "source": "scripts",
           "export": "variables", "env": "variables", "vars": "variables",
           "pipe": "pipes", "glob": "globs", "wildcards": "globs",
           "redirect": "redirection", "script": "scripts", "cron": "crontab",
           "perms": "permissions", "chmod-modes": "permissions", "true": "scripts",
           "false": "scripts"}

# The `help` index, in the order a class actually learns them.
GROUPS = [
    ("getting around", ["pwd", "cd", "ls"]),
    ("files & folders", ["mkdir", "rmdir", "touch", "cp", "mv", "rm", "cat", "less", "edit", "find"]),
    ("text & filtering", ["echo", "printf", "grep", "head", "tail", "wc", "sort",
                          "uniq", "tac", "cut", "seq", "basename", "dirname"]),
    ("permissions", ["chmod", "chown", "sudo", "whoami", "id", "groups"]),
    ("processes & time", ["ps", "kill", "sleep", "jobs", "date", "crontab", "history"]),
    ("system & network", ["df", "du", "uname", "hostname", "ip", "ping", "which", "type", "clear"]),
    ("archives", ["tar", "gzip", "gunzip"]),
    ("shell ideas", ["redirection", "pipes", "globs", "variables", "scripts", "permissions"]),
]


def _wrap(text, indent="  "):
    """Wrap a note to terminal width. A note that arrives already indented is a
    hand-aligned table (the chmod digits, cron's five fields) — leave it alone."""
    if text.startswith(" "):
        return [indent + text]
    return textwrap.wrap(text, width=78, initial_indent=indent,
                         subsequent_indent=indent) or [""]


def page(name):
    """The full help text for one command or topic — plain text, no colour."""
    key = ALIASES.get(name, name)
    p = PAGES.get(key)
    if not p:
        return None
    lines = [f"Usage: {p['usage']}"] + textwrap.wrap(p["summary"], width=78)
    if p["opts"]:
        width = max(len(f) for f, _ in p["opts"])
        lines.append("")
        for flag, what in p["opts"]:
            lines += textwrap.wrap(what, width=78, initial_indent=f"  {flag.ljust(width)}   ",
                                   subsequent_indent="  " + " " * (width + 3))
    if p["notes"]:
        lines.append("")
        for n in p["notes"]:
            lines += _wrap(n)
    if p["examples"]:
        lines.append("")
        lines.append("Examples:")
        # Comments line up in a column — but an example too long to fit gets its
        # comment underneath instead, and must not push everyone else's column
        # out with it (a wrapped example reads as two commands).
        fits = [len(cmd) for cmd, what in p["examples"] if 5 + len(cmd) + len(what) <= 78]
        width = max(fits or [0])
        for cmd, what in p["examples"]:
            if 5 + width + len(what) > 78 or len(cmd) > width:
                lines += [f"  {cmd}", f"      # {what}"]
            else:
                lines.append(f"  {cmd.ljust(width)}   # {what}")
    if p.get("real"):
        lines.append("")
        lead = (f"Bash has more, not simulated here: {p['real']}" if key in TOPICS
                else f"Not simulated here (the real {key} has them): {p['real']}")
        lines += textwrap.wrap(lead, width=78, subsequent_indent="  ")
    if key != name:
        lines.append(f"({name} → see {key})")
    return "\n".join(lines)


def known(name):
    return (ALIASES.get(name, name)) in PAGES


def summary(name):
    p = PAGES.get(ALIASES.get(name, name))
    return p["summary"] if p else ""
