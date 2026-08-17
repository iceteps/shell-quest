#!/usr/bin/env python3
"""Differential test: the 🐧 Linux missions' shell vs the REAL bash on this machine.

The Linux missions promise "the world reacts like the real tools would". The only
honest way to check that is to run the same commands both ways and compare, so
that is exactly what this does: each case runs in the simulated shell and in a
throwaway `/tmp` sandbox under real bash, and the outputs must match.

    python3 tests/test_shell_vs_bash.py            # all cases
    python3 tests/test_shell_vs_bash.py grep       # only cases whose name matches

Environment-specific differences are normalised away deliberately — the username,
the SELinux `.` in `ls -l`, the kernel string, `$HOME`, block counts, timestamps.
Everything else is a real divergence and fails the run.

Requires bash (skips cleanly without it). Pure standard library, like the game.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import IO, World, set_player_os          # noqa: E402
from missions import linux_help                      # noqa: E402
from missions.linux_shell import (HOME, ON_PATH, TOOL_VERSIONS, USER,  # noqa: E402
                                  shell, st)

set_player_os("linux")
ANSI = re.compile(r"\033\[[0-9;]*m")


class Capture(IO):
    def __init__(self):
        super().__init__()
        self.lines = []
        self._raw = ""

    def print(self, *args):
        self.lines.append(self._raw + " ".join(str(a) for a in args))
        self._raw = ""

    def write(self, text):
        """Raw stream bytes, with no newline of their own — `clear`'s escape is
        the only user. Whatever prints next has to land on the SAME line, or the
        harness invents a newline the terminal never saw."""
        self._raw += text

    def text(self):
        return "\n".join(self.lines + ([self._raw] if self._raw else []))

    def input(self, prompt=""):
        raise EOFError


class Match:
    """Stands in for the regex match object a handler normally receives."""

    def __init__(self, line):
        self._line = line

    def group(self, _):
        return self._line


def normalise(text, sandbox=None, real_user=None):
    text = ANSI.sub("", text)
    if sandbox:
        text = text.replace(sandbox, "~")
    text = text.replace(HOME, "~")
    if real_user:
        text = re.sub(rf"\b{re.escape(real_user)}\b", "USER", text)
    text = re.sub(rf"\b{re.escape(USER)}\b", "USER", text)
    # ls -l: SELinux context dot, link counts, block totals and timestamps are
    # machine facts, not behaviour.
    text = re.sub(r"^(total) \d+", r"\1 N", text, flags=re.M)
    text = re.sub(r"([-drwxsStT]{10})\.", r"\1", text)
    text = re.sub(r"([-drwxsStT]{10}) +\d+ ", r"\1 1 ", text)
    # A directory's byte size is the filesystem's business, not the shell's.
    text = re.sub(r"^(d[-rwxsStT]{9} 1 \S+ +\S+) +\d+ ", r"\1 SIZE ", text, flags=re.M)
    text = re.sub(r"\b\d{4}-\d\d-\d\d \d\d:\d\d\b", "DATE", text)
    text = re.sub(r"\b[A-Z][a-z]{2} +\d+ +[\d:]+\b", "DATE", text)
    text = re.sub(r"^bash: (?:-c: )?line \d+: ", "bash: ", text, flags=re.M)
    # `bash -c` echoes the offending line back after a syntax error; an
    # INTERACTIVE bash (which is what the game is) does not. Harness artifact.
    text = re.sub(r"^bash: `.*'$\n?", "", text, flags=re.M)
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    keep = []
    for line in text.split("\n"):
        s = line.strip()
        # the game's teaching asides are additions, not divergences
        if s.startswith(("(", "🌍", "⚠", "…")):
            continue
        keep.append(s)
    out = "\n".join(x for x in keep if x).strip()
    # `ls` columns on a TTY vs one-per-line when piped is a layout artifact
    return re.sub(r"\s+", " ", out).strip()


def run_simulated(cmds):
    world = World({})
    st(world)
    io = Capture()
    for line in cmds:
        try:
            shell(world, Match(line), io)
        except Exception as exc:                       # noqa: BLE001
            io.lines.append(f"!!! UNCAUGHT {type(exc).__name__}: {exc}")
    return io.text()


def run_bash(cmds):
    # The sandbox lives inside a parent whose mode we control, so `ls -la`'s
    # `..` row is a fact about the test and not about the machine's /tmp.
    parent = os.path.join(tempfile.gettempdir(), "shellquest-diff")
    os.makedirs(parent, exist_ok=True)
    os.chmod(parent, 0o755)
    sandbox = tempfile.mkdtemp(prefix="run-", dir=parent)
    os.chmod(sandbox, 0o755)          # mkdtemp gives 0700; the game's HOME is 755
    try:
        script = f"cd {sandbox}\n" + "\n".join(cmds)
        # stderr merged into stdout on the SAME fd, not concatenated after it:
        # the game prints one interleaved stream, so comparing two separate
        # streams would flag ordering that is an artifact of the harness.
        # HOME points at the sandbox so `$HOME` and a bare `cd` are comparable
        # with the game's own HOME rather than being unaskable questions.
        # TERM is pinned for the same reason: the game is a person sitting in a
        # terminal, and terminfo-driven commands answer a different question
        # without one — CI runs with TERM=dumb, where `clear` prints nothing at
        # all. Pinning it asks bash the question the game is actually modelling.
        env = dict(os.environ, HOME=sandbox, TERM="xterm-256color")
        # No stdin: a case that reads the keyboard must fail fast, not hang CI.
        proc = subprocess.run(["bash", "--noprofile", "--norc", "-c", script],
                              stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, timeout=30, env=env)
        return proc.stdout, sandbox
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


# `find` and `tar -t` walk in readdir order, which real filesystems do not
# define. Comparing those outputs as ordered text would be testing ext4, not the
# shell — so for these the LINES must match, in any order.
ORDER_FREE = {
    "find bare", "find -name", "find -type f", "find -type d", "find -iname",
    "find -maxdepth", "find -maxdepth -type f", "find piped to wc",
    "tar create list", "tar czf", "ls -R",
}

# Sorting order is a LOCALE fact. The game models a desktop, where glibc
# collates like a dictionary (`alpha` before `Beta`). A bare-C-locale box — a CI
# runner, typically — collates by codepoint instead. Same shell, different
# environment question, so these are waived there rather than "fixed" to match a
# locale no student is sitting in.
COLLATION_SENSITIVE = {
    "sort mixed case", "sort punctuation ignored", "ls collates like sort",
}


def collates_by_dictionary():
    out, _ = run_bash(["printf 'alpha\\nBeta\\n' | sort"])
    return out.split() == ["alpha", "Beta"]


# Cases whose output legitimately depends on the machine, not on behaviour.
ENV_SPECIFIC = {
    "subst in double quotes", "subst backticks", "subst nested",
    "uname", "uname -r", "whoami", "df -h",
    "cd no args", "var in double quotes",
    "echo double quotes var", "unmatched quote", "ls -la combined",
    "ls -l -a separate", "chain ||",
}

CASES = {
    # ---- navigation ----------------------------------------------------
    "pwd": ["pwd"],
    "ls empty": ["ls"],
    "mkdir + ls": ["mkdir a", "ls"],
    "mkdir nested no -p": ["mkdir x/y"],
    "mkdir -p nested": ["mkdir -p x/y", "ls x"],
    "mkdir existing": ["mkdir a", "mkdir a"],
    "mkdir -p existing": ["mkdir a", "mkdir -p a"],
    "mkdir over file": ["touch f", "mkdir f"],
    "mkdir multiple": ["mkdir a b c", "ls"],
    "mkdir no args": ["mkdir"],
    "cd relative": ["mkdir a", "cd a", "pwd"],
    "cd absolute": ["cd /tmp", "pwd"],
    "cd dotdot": ["mkdir -p a/b", "cd a/b", "cd ..", "pwd"],
    "cd dotdot twice": ["mkdir -p a/b", "cd a/b", "cd ../..", "pwd"],
    "cd dash": ["mkdir a", "cd a", "cd -", "pwd"],
    "cd no args": ["mkdir a", "cd a", "cd", "pwd"],
    "cd missing": ["cd nope"],
    "cd into file": ["touch f", "cd f"],
    "ls -la combined": ["touch a", "ls -la"],
    "ls -l -a separate": ["touch a", "ls -l -a"],
    "ls hides dotfiles": ["touch .hidden visible", "ls"],
    "ls -a shows them": ["touch .hidden", "ls -a"],
    "ls multiple targets": ["mkdir d", "touch f", "ls f d"],
    "ls trailing slash": ["mkdir d", "touch d/x", "ls d/"],
    "ls -R": ["mkdir -p d/e", "touch d/e/f", "ls -R d"],
    "ls missing": ["ls nope"],

    # ---- files ---------------------------------------------------------
    "rmdir empty": ["mkdir a", "rmdir a", "ls"],
    "rmdir non-empty": ["mkdir a", "touch a/f", "rmdir a"],
    "rmdir a file": ["touch f", "rmdir f"],
    "rmdir missing": ["rmdir nope"],
    "rm dir no -r": ["mkdir a", "rm a"],
    "rm -r dir": ["mkdir a", "touch a/f", "rm -r a", "ls"],
    "rm missing": ["rm nope"],
    "rm -f missing": ["rm -f nope"],
    "rm multiple": ["touch a b", "rm a b", "ls"],
    "rm -rf missing": ["rm -rf nope"],
    "touch no args": ["touch"],
    "touch multiple": ["touch a b c", "ls"],
    "touch in missing dir": ["touch nope/f"],
    "cat missing": ["cat nope"],
    "cat dir": ["mkdir a", "cat a"],
    "cat multiple": ["echo a > x", "echo b > y", "cat x y"],
    "cp file": ["echo x > a", "cp a b", "cat b"],
    "cp dir no -r": ["mkdir d", "cp d e"],
    "cp -r dir": ["mkdir d", "touch d/f", "cp -r d e", "ls e"],
    "cp file onto dir": ["mkdir d", "echo x > a", "cp a d", "cat d/a"],
    "cp multi into dir": ["mkdir d", "touch a b", "cp a b d", "ls d"],
    "cp multi no dir": ["touch a b c", "cp a b c"],
    "cp missing dest": ["echo x > a", "cp a"],
    "cp missing source": ["cp nope there"],
    "mv rename": ["echo x > a", "mv a b", "ls"],
    "mv into dir": ["mkdir d", "touch a", "mv a d", "ls d"],
    "mv dir": ["mkdir -p d/x", "mv d e", "ls e"],
    "mv missing": ["mv nope there"],

    # ---- text ----------------------------------------------------------
    "echo redirect": ["echo hi > f", "cat f"],
    "echo append": ["echo a > f", "echo b >> f", "cat f"],
    "echo -e newline": ['echo -e "a\\nb"'],
    "echo no -e": ['echo "a\\nb"'],
    "echo -e tab": ['echo -e "a\\tb"'],
    "echo empty": ["echo"],
    "echo many spaces": ["echo a     b"],
    "echo single quotes": ["echo '$HOME'"],
    "echo double quotes var": ['echo "$HOME"'],
    "var in double quotes": ['echo "user=$USER"'],
    "single quote literal dollar": ["echo '$USER'"],
    "printf": ["printf 'a\\nb\\n'"],
    "grep case sensitive": ["printf 'Error\\nerror\\n' > f", "grep error f"],
    "grep -i": ["printf 'Error\\nerror\\n' > f", "grep -i error f"],
    "grep -v": ["printf 'a\\nb\\n' > f", "grep -v a f"],
    "grep -c": ["printf 'a\\na\\nb\\n' > f", "grep -c a f"],
    "grep -n": ["printf 'a\\nb\\n' > f", "grep -n b f"],
    "grep -in combined": ["printf 'Abc\\n' > f", "grep -in abc f"],
    "grep -r dir": ["mkdir d", "echo hit > d/f", "grep -r hit d"],
    "grep multiple files": ["echo a > x", "echo a > y", "grep a x y"],
    "grep missing file": ["grep x nope"],
    "grep no match code": ["touch f", "grep zzz f", "echo $?"],
    "wc -l file": ["printf 'a\\nb\\n' > f", "wc -l f"],
    "wc -w": ["printf 'a b c\\n' > f", "wc -w f"],
    "head default 10": ["printf '1\\n2\\n3\\n4\\n5\\n6\\n7\\n8\\n9\\n10\\n11\\n' > f", "head f"],
    "head -n 2": ["printf '1\\n2\\n3\\n' > f", "head -n 2 f"],
    "head -3 shorthand": ["printf '1\\n2\\n3\\n4\\n' > f", "head -3 f"],
    "head invalid n": ["touch f", "head -n x f"],
    "tail -n 1": ["printf '1\\n2\\n3\\n' > f", "tail -n 1 f"],
    "tail missing file": ["tail nope"],
    "sort": ["printf 'b\\na\\n' > f", "sort f"],
    "sort -r": ["printf 'a\\nb\\n' > f", "sort -r f"],
    "sort -u": ["printf 'b\\na\\nb\\n' > f", "sort -u f"],
    "uniq": ["printf 'a\\na\\nb\\n' > f", "uniq f"],
    "uniq -c": ["printf 'a\\na\\nb\\n' > f", "uniq -c f"],

    # ---- find ----------------------------------------------------------
    "find bare": ["mkdir d", "touch d/f", "find d"],
    "find -name": ["mkdir -p d", "touch d/a.txt d/b.md", "find d -name '*.txt'"],
    "find -type f": ["mkdir -p d/e", "touch d/a.txt", "find d -type f"],
    "find -type d": ["mkdir -p d/e", "find d -type d"],
    "find -iname": ["touch A.TXT", "find . -iname '*.txt'"],
    "find -name no arg": ["find . -name"],
    "find missing dir": ["find nope"],
    "find -maxdepth": ["mkdir -p d/e", "touch a.txt d/b.txt", "find . -maxdepth 1"],
    "find -maxdepth -type f": ["mkdir -p d", "touch a.txt d/b.txt",
                               "find . -maxdepth 1 -type f"],
    "find piped to wc": ["mkdir -p d", "touch a.txt d/b.txt", "find . -name '*.txt' | wc -l"],

    # ---- permissions ---------------------------------------------------
    "chmod 600 then ls -l": ["touch f", "chmod 600 f", "ls -l f"],
    "chmod u+x": ["touch f", "chmod u+x f", "ls -l f"],
    "chmod a+x": ["touch f", "chmod a+x f", "ls -l f"],
    "chmod g-w": ["touch f", "chmod g-w f", "ls -l f"],
    "chmod symbolic multi": ["touch f", "chmod u+x,g+w f", "ls -l f"],
    "chmod -R": ["mkdir d", "touch d/f", "chmod -R 700 d", "ls -l d"],
    "chmod invalid": ["touch f", "chmod 999 f"],
    "chmod missing": ["chmod 600 nope"],
    "chmod no args": ["chmod"],

    # ---- pipes, redirection, chaining ----------------------------------
    "pipe grep": ["printf 'a\\nbb\\n' > f", "cat f | grep bb"],
    "pipe wc -l": ["printf 'a\\nb\\nc\\n' > f", "cat f | wc -l"],
    "pipe three stages": ["printf 'a\\nbb\\nccc\\n' > f", "cat f | grep c | wc -l"],
    "pipe into head": ["printf '1\\n2\\n3\\n' > f", "cat f | head -n 2"],
    "pipe into sort": ["printf 'b\\na\\n' > f", "cat f | sort"],
    "ls piped counts files": ["touch f1.txt f2.txt f3.txt", "ls | wc -l"],
    "input redirect wc": ["printf 'a\\nb\\n' > f", "wc -l < f"],
    "redirect overwrites": ["echo one > f", "echo two > f", "cat f"],
    "append to new file": ["echo a >> new.txt", "cat new.txt"],
    "redirect to missing dir": ["echo x > nope/f"],
    "redirect no space": ["echo hi>f", "cat f"],
    "semicolon no spaces": ["echo a;echo b"],
    "and-and no spaces": ["mkdir a&&echo yes"],
    "chain &&": ["mkdir a && echo made"],
    "chain && after failure": ["cd nope && echo made"],
    "chain ||": ["cd nope || echo fallback"],
    "chain ;": ["echo one ; echo two"],
    "stderr to devnull": ["cat nope 2>/dev/null; echo done"],
    "exit code after fail": ["cat nope 2>/dev/null; echo $?"],
    "exit code after ok": ["echo hi > /dev/null; echo $?"],
    "true false": ["true; echo $?", "false; echo $?"],

    # ---- expansion -----------------------------------------------------
    "brace expansion": ["touch file{1,2,3}.txt", "ls"],
    "brace range": ["touch f{1..3}", "ls"],
    "brace no comma": ["touch f{x}", "ls"],
    "glob ls": ["touch a.txt b.txt c.md", "ls *.txt"],
    "glob rm": ["touch a.txt b.txt c.md", "rm *.txt", "ls"],
    "glob no match": ["ls *.zzz"],
    "glob mid path": ["mkdir d", "touch d/a.txt d/b.txt", "ls d/*.txt"],
    "glob question mark": ["touch a.txt bb.txt", "ls ?.txt"],
    "quoted filename": ['touch "my file.txt"', "ls"],
    "quoted spaces roundtrip": ['echo hi > "my file.txt"', 'cat "my file.txt"'],
    "unmatched quote": ['echo "oops'],
    "empty line": [""],
    "only spaces": ["   "],


    # ---- command substitution (the cron single-vs-double quote lesson) ----
    "subst in double quotes": ['echo "u=$(whoami)"'],
    "subst in single quotes": ["echo 'u=$(whoami)'"],
    "subst backticks": ["echo u=`whoami`"],
    "subst nested": ['echo "$(basename $(pwd))"'],
    # ---- archives ------------------------------------------------------
    "tar create list": ["mkdir d", "touch d/f", "tar -cf d.tar d", "tar -tf d.tar"],
    "tar extract restores content": [
        "mkdir d", "echo hi > d/f", "tar -cf d.tar d", "rm -r d",
        "tar -xf d.tar", "cat d/f"],
    "tar czf": ["mkdir d", "touch d/f", "tar -czf d.tgz d", "tar -tzf d.tgz"],
    "tar missing archive": ["tar -tf nope.tar"],
    "gzip roundtrip": ["mkdir d", "tar -cf d.tar d", "gzip d.tar", "ls"],
    "gunzip": ["echo x > f", "gzip f", "gunzip f.gz", "cat f"],
    "gzip missing": ["gzip nope"],

    # ---- system --------------------------------------------------------
    "uname": ["uname"],
    "uname -r": ["uname -r"],
    "whoami": ["whoami"],
    "df -h": ["df -h"],
    "script needs +x": ["printf '#!/bin/bash\\necho hi\\n' > s.sh", "./s.sh"],
    "script with +x": ["printf '#!/bin/bash\\necho hi\\n' > s.sh", "chmod +x s.sh", "./s.sh"],
    "bash script no +x": ["printf '#!/bin/bash\\necho hi\\n' > s.sh", "bash s.sh"],

    # ---- the parser says no ------------------------------------------------
    # Silently dropping an empty command would teach that these lines are fine.
    # They are the classic beginner typos, and bash rejects every one of them.
    "pipe at start": ["| echo hi"],
    "pipe at end": ["echo hi |"],
    "double pipe empty middle": ["echo a | | cat"],
    "and at end": ["echo a &&"],
    "or at end": ["echo a ||"],
    "semicolon alone": [";"],
    "double semicolon": ["echo a ;;"],
    "and at start": ["&& echo a"],
    "semicolon then pipe": ["echo a ; | cat"],
    "trailing semicolon is legal": ["echo a ;"],
    "empty chain between semicolons": ["echo a ;; echo b"],
    "redirect target is operator": ["echo a > > b"],
    "redirect with no target": ["echo hi >"],
    "quoted pipe is not an operator": ["echo 'a | b'"],
    "quoted semicolon is not an operator": ["echo 'a ; b'"],
    "pipe stderr merge": ["ls nope |& wc -l"],
    "plain pipe does not carry stderr": ["ls nope | wc -l"],

    # ---- echo / printf are builtins with real flag rules -------------------
    "echo -n": ["echo -n hi"],
    "echo -e": ["echo -e 'a\\tb'"],
    "echo -ne": ["echo -ne 'a\\nb'"],
    "echo -E keeps backslashes": ["echo -E 'a\\nb'"],
    "echo flag stops at first word": ["echo hi -n"],
    "echo escaped dollar": ["echo \\$HOME"],
    "echo escaped star": ["touch a.txt", "echo \\*"],
    "echo escaped dollar keeps glob": ["touch x1", "echo \\$x*"],
    "printf format reuse": ["printf '%s\\n' a b c"],
    "printf two slots": ["printf '%s-%s\\n' a b c d"],
    "printf percent literal": ["printf '100%%\\n'"],
    # Not tested across two typed lines: `bash -c` runs a SCRIPT, so an
    # unterminated printf runs into the next command's output. An interactive
    # shell (which the game is) puts a prompt there instead. The byte count is
    # the part that is the same either way — and it's what actually bites.
    "printf writes no trailing newline": ["printf 'a' > f", "wc -c f"],
    "echo writes a trailing newline": ["echo a > f", "wc -c f"],
    "echo -n writes no trailing newline": ["echo -n a > f", "wc -c f"],
    "append does not insert a separator": ["printf 'a' > f", "printf 'b\\n' >> f",
                                           "cat f", "wc -c f"],
    "cat keeps the missing newline": ["printf 'a' > f", "cat f | wc -c"],
    "printf digit": ["printf '%d\\n' 42"],

    # ---- ls -d: the directory itself ---------------------------------------
    "ls -d dir": ["mkdir d", "touch d/f", "ls -d d"],
    "ls -ld dir": ["mkdir d", "chmod 755 d", "ls -ld d"],
    "ls -ld after chmod 700": ["mkdir d", "chmod 700 d", "ls -ld d"],
    "ls -d file": ["touch f", "ls -d f"],

    # ---- grep is a REGEX tool, not a substring test -------------------------
    "grep anchor start": ["printf 'alpha\\nbeta\\n' > f", "grep '^a' f"],
    "grep anchor end": ["printf 'alpha\\nbeta\\n' > f", "grep 'a$' f"],
    "grep dot": ["printf 'cat\\ncot\\ncart\\n' > f", "grep 'c.t' f"],
    "grep star": ["printf 'ct\\ncat\\ncaat\\n' > f", "grep 'ca*t' f"],
    "grep class": ["printf 'a1\\nb2\\n' > f", "grep '[0-9]' f"],
    "grep negated class": ["printf 'a1\\nbb\\n' > f", "grep '[^0-9]$' f"],
    "grep BRE plus is literal": ["printf 'a+b\\naab\\n' > f", "grep 'a+b' f"],
    "grep -E plus is an operator": ["printf 'a+b\\naab\\n' > f", "grep -E 'a+b' f"],
    "grep -F fixed": ["printf 'a.b\\naxb\\n' > f", "grep -F 'a.b' f"],
    "grep -l": ["echo hit > a", "echo no > b", "grep -l hit a b"],
    "grep -l no match": ["echo no > a", "grep -l hit a"],
    "grep -w": ["printf 'cat\\ncatalog\\n' > f", "grep -w cat f"],
    "grep -q sets status only": ["echo hi > f", "grep -q hi f", "echo $?"],
    "grep -q miss status": ["echo hi > f", "grep -q nope f", "echo $?"],
    "grep unterminated class": ["echo a > f", "grep '[' f"],
    "grep -v no trailing blank": ["printf 'a\\nb\\n' > f", "grep -v a f | wc -l"],

    # ---- sort collates like a dictionary, not like ASCII --------------------
    "sort mixed case": ["printf 'beta\\nAlpha\\ngamma\\n' > f", "sort f"],
    "sort punctuation ignored": ["printf '_x\\nb\\n-y\\n' > f", "sort f"],
    "sort piped to head": ["printf 'c\\na\\nb\\n' > f", "sort f | head -n 1"],
    "sort -r": ["printf 'beta\\nAlpha\\ngamma\\n' > f", "sort f | wc -l"],
    "ls collates like sort": ["touch Beta alpha Gamma", "ls"],

    # ---- basename / dirname ------------------------------------------------
    "basename": ["basename /a/b/c.txt"],
    "basename suffix": ["basename /a/b/c.txt .txt"],
    "basename trailing slash": ["basename /a/b/"],
    "dirname": ["dirname /a/b/c.txt"],
    "dirname bare": ["dirname c.txt"],

    # ---- sudo is a no-op here, but it must still RUN the command -----------
    # (no bash side: real sudo would prompt for a password)

    # ---- permission bits have to actually STOP things ----------------------
    "chmod 000 blocks cat": ["echo hi > f", "chmod 000 f", "cat f", "echo $?"],
    "chmod 000 blocks grep": ["echo hi > f", "chmod 000 f", "grep hi f"],
    "chmod 000 blocks wc": ["echo hi > f", "chmod 000 f", "wc -l f"],
    "chmod 000 blocks stdin redirect": ["echo hi > f", "chmod 000 f", "cat < f"],
    "chmod 400 still reads": ["echo hi > f", "chmod 400 f", "cat f"],
    # The octal drill reads the string back off ls -l, so every route to a mode
    # string has to print the same one: a named file, several named files, and a
    # directory listing.
    "ls -l two named files": ["touch a b", "chmod 750 a", "chmod 600 b", "ls -l a b"],
    "ls -l dir mixed modes": ["mkdir d", "touch d/a d/b", "chmod 750 d/a", "chmod 600 d/b",
                              "ls -l d"],
    "setuid shows as s": ["touch f", "chmod 4755 f", "ls -l f"],
    "setgid shows as s": ["touch f", "chmod 2755 f", "ls -l f"],
    "sticky shows as t": ["mkdir d", "chmod 1755 d", "ls -ld d"],
    "capital S when not executable": ["touch f", "chmod 4644 f", "ls -l f"],

    # ---- descriptor duplication --------------------------------------------
    "2>&1 to stdout": ["ls nope 2>&1"],
    "2>&1 into a pipe": ["ls nope 2>&1 | wc -l"],
    "2>&1 with a file": ["ls nope > out 2>&1", "cat out"],
    "1>&2 sends stdout to stderr": ["echo hi 1>&2"],

    # ---- globbing rules ----------------------------------------------------
    "glob skips dotfiles": ["touch .hidden a", "echo *"],
    "glob dot pattern finds them": ["touch .hidden", "echo .*"],
    "glob trailing slash is dirs only": ["mkdir d", "touch f", "echo */"],
    "ls -d trailing slash glob": ["mkdir d", "touch f", "ls -d */"],
    "glob nested dir slash": ["mkdir -p d/sub", "touch d/f", "echo d/*/"],
    "rm star spares dotfiles": ["touch .keep gone", "rm *", "ls -a"],

    # ---- rm -d, seq, tac ---------------------------------------------------
    "rm -d empty dir": ["mkdir d", "rm -d d", "ls"],
    "rm -d non-empty": ["mkdir d", "touch d/f", "rm -d d"],
    "seq": ["seq 3"],
    "seq from to": ["seq 2 5"],
    "seq with step": ["seq 1 2 7"],
    "seq backwards": ["seq 3 -1 1"],
    "seq bad arg": ["seq x"],
    "tac": ["printf 'a\\nb\\nc\\n' > f", "tac f"],
    "tac piped": ["printf 'a\\nb\\n' | tac"],
    "sort -n falls back to dictionary": ["printf 'beta\\nAlpha\\n' > f", "sort -n f"],
    "sort -n numbers": ["printf '10\\n9\\n2\\n' > f", "sort -n f"],

    # ---- a script is these same lines, run in order ------------------------
    "script exit code": ["printf '#!/bin/bash\\nexit 3\\n' > s.sh", "chmod +x s.sh",
                         "./s.sh", "echo $?"],
    "script positional args": ["printf '#!/bin/bash\\necho $1 $2\\n' > s.sh",
                               "chmod +x s.sh", "./s.sh alpha beta"],
    "script arg count": ["printf '#!/bin/bash\\necho $#\\n' > s.sh", "chmod +x s.sh",
                         "./s.sh a b c"],
    "script no args": ["printf '#!/bin/bash\\necho [$1] $#\\n' > s.sh", "chmod +x s.sh",
                       "./s.sh"],
    "script runs real commands": ["printf '#!/bin/bash\\nmkdir made\\nls\\n' > s.sh",
                                  "chmod +x s.sh", "./s.sh"],
    "script output can be piped": ["printf '#!/bin/bash\\necho a\\necho b\\n' > s.sh",
                                   "chmod +x s.sh", "./s.sh | wc -l"],
    "script output can be redirected": ["printf '#!/bin/bash\\necho hi\\n' > s.sh",
                                        "chmod +x s.sh", "./s.sh > out", "cat out"],
    "bash script missing": ["bash nope.sh"],
    "script exit code from last command": ["printf '#!/bin/bash\\nls nope\\n' > s.sh",
                                           "chmod +x s.sh", "./s.sh", "echo $?"],

    # ---- cp / mv say what actually went wrong ------------------------------
    "cp to missing dir": ["touch a b", "cp a b nope/"],
    "mv to missing dir": ["touch a", "mv a nope/x"],
    "cp unreadable source": ["echo hi > a", "chmod 000 a", "cp a b"],
    "cp dir into itself": ["mkdir -p d/sub", "cp -r d d/sub2"],
    "mv keeps working without read bit": ["echo hi > a", "chmod 000 a", "mv a b", "ls"],

    # ---- tar's exit status is 2, not 1 -------------------------------------
    "tar missing archive status": ["tar -tf nope", "echo $?"],
    "tar -f with no argument": ["tar -cf"],
    "tar no mode flag": ["tar -f x.tar"],
    "tar create missing source": ["tar -cf t.tar nope", "echo $?"],

    # ---- a pipe carries the newline too ------------------------------------
    "pipe carries the terminator": ["echo hi | wc -c"],
    "quoted spaces survive the pipe": ["echo 'x  y' | wc -c"],
    "printf into wc -c": ["printf 'ab' | wc -c"],

    # ---- cut: the class-1 /etc/passwd exercise -----------------------------
    "cut -d -f1": ["printf 'a:b:c\\nd:e:f\\n' > p", "cut -d: -f1 p"],
    "cut -f range": ["printf 'a:b:c:d\\n' > p", "cut -d: -f2-3 p"],
    "cut -f open range": ["printf 'a:b:c:d\\n' > p", "cut -d: -f2- p"],
    "cut -f list": ["printf 'a:b:c:d\\n' > p", "cut -d: -f1,3 p"],
    "cut -f separate arg": ["printf 'a:b\\n' > p", "cut -d : -f 2 p"],
    "cut -c": ["printf 'abcdef\\n' > p", "cut -c2-4 p"],
    "cut no delimiter in line": ["printf 'nodelim\\na:b\\n' > p", "cut -d: -f1 p"],
    "cut piped": ["printf 'a:b\\n' | cut -d: -f2"],
    "cut no list": ["printf 'a:b\\n' > p", "cut -d: p"],
    "cut bad list": ["printf 'a:b\\n' > p", "cut -d: -fx p"],
    "cut missing file": ["cut -d: -f1 nope"],

    # ---- a directory's own bits actually deny -------------------------------
    # Until these passed, `chmod` on a DIRECTORY took nothing away, and the
    # permissions lesson was decoration: the mode changed, the world didn't.
    "cd into unsearchable dir": ["mkdir d", "chmod 600 d", "cd d"],
    "cd into unreadable but searchable": ["mkdir d", "chmod 300 d", "cd d", "pwd"],
    "touch in unwritable dir": ["mkdir d", "chmod 500 d", "touch d/f"],
    "mkdir in unwritable dir": ["mkdir d", "chmod 500 d", "mkdir d/sub"],
    "redirect into unwritable dir": ["mkdir d", "chmod 500 d", "echo x > d/f"],
    "redirect onto read-only file": ["touch f", "chmod 400 f", "echo x > f"],
    "append onto read-only file": ["echo a > f", "chmod 400 f", "echo b >> f"],
    "touch existing read-only file": ["touch f", "chmod 400 f", "touch f", "echo $?"],
    "unwritable dir still readable": ["mkdir d", "touch d/f", "chmod 500 d", "ls d"],

    # ---- --help, where bash's own builtins are inconsistent ----------------
    # Most bash builtins print help for --help (so answering with the page is
    # right); `echo` just echoes it, and true/false say nothing.
    "echo --help is not a flag": ["echo --help"],
    "true --help says nothing": ["true --help", "echo $?"],
    "false --help says nothing": ["false --help", "echo $?"],

    # ---- a missing command fails ONE stage, not the whole line -------------
    # bash reports it, gives the next stage an empty stream and reports the
    # LAST stage's status — so `nosuchcmd | wc -l` prints 0 and exits 0. The
    # teaching aside after the error is parenthesised, so normalise drops it
    # and what is compared here is the real bash line.
    "unknown command": ["frobnicate"],
    "unknown command exit code": ["frobnicate", "echo $?"],
    "unknown command typo": ["pws"],
    "unknown command is a help topic": ["pipes", "echo $?"],
    "unknown in a pipeline": ["nosuchcmd | wc -l"],
    "unknown pipeline exit code": ["nosuchcmd | wc -l", "echo $?"],
    "unknown mid pipeline": ["echo hi | nosuchcmd | wc -l"],
    "unknown after &&": ["nosuchcmd && echo yes", "echo $?"],
    "unknown after ||": ["nosuchcmd || echo fallback"],

    # ---- clear is a program that writes bytes, not a magic screen wipe ------
    # On a terminal the escape has to go out raw (printed as a line, the extra
    # newline lands the prompt one row down); redirected or piped it is just
    # eleven bytes like any other output, and modelling only the first half is
    # what makes it look like magic.
    "clear": ["clear"],
    "clear then a command": ["touch a", "clear", "ls"],
    "clear redirected": ["clear > f", "wc -c f"],
    "clear piped": ["clear | wc -c"],

    # ---- the wrong-directory relative path (the hint is an addition) --------
    "relative path from the wrong dir": ["mkdir -p course/week1", "cd course/week1",
                                         "touch week1/notes.txt"],
    "cd to a sibling by bare name": ["mkdir -p course/week1 course/week2",
                                     "cd course/week1", "cd week2"],
}


def lint_help():
    """Every command the shell claims to run must have a page behind it.

    `chmod --help` answering "Try 'chmod --help' for more information." was the
    worst kind of bug: the shell told the student where to look and the place it
    pointed at was empty. A command on $PATH with no page is that bug waiting.
    """
    problems = []
    documented = set(linux_help.PAGES) | set(linux_help.ALIASES)
    for cmd in sorted(ON_PATH - set(TOOL_VERSIONS)):
        if cmd not in documented:
            problems.append(f"`{cmd}` is on $PATH but has no help page")
    for title, names in linux_help.GROUPS:
        for n in names:
            if not linux_help.known(n):
                problems.append(f"help index group '{title}' lists unknown '{n}'")
    for name, target in linux_help.ALIASES.items():
        if target not in linux_help.PAGES:
            problems.append(f"alias '{name}' points at missing page '{target}'")
    for name in linux_help.PAGES:
        if not linux_help.page(name):
            problems.append(f"page '{name}' renders empty")
    return problems


def main():
    if not shutil.which("bash"):
        print("bash not found — skipping the differential test.")
        return 0
    gaps = lint_help()
    if gaps:
        print("help coverage problems:")
        for g in gaps:
            print(f"  ✗ {g}")
        return 1
    only = sys.argv[1] if len(sys.argv) > 1 else None
    # An ENV_SPECIFIC name that no longer matches a case is a waiver for
    # nothing — and the next case renamed into that slot would be exempted
    # by accident. Fail loudly instead.
    stale = sorted((ENV_SPECIFIC | ORDER_FREE | COLLATION_SENSITIVE) - set(CASES))
    if stale:
        print("stale waiver entries (no such case): " + ", ".join(stale))
        return 1
    if not collates_by_dictionary():
        print("note: this machine's bash collates by codepoint (C locale), so the "
              f"{len(COLLATION_SENSITIVE)} sort-order cases are treated as "
              "environment-specific here.")
        ENV_SPECIFIC.update(COLLATION_SENSITIVE)
    failures, env_only, ran = [], 0, 0
    for name, cmds in CASES.items():
        if only and only not in name:
            continue
        ran += 1
        sim = normalise(run_simulated(cmds))
        raw, sandbox = run_bash(cmds)
        real = normalise(raw, sandbox, os.environ.get("USER"))
        if name in ORDER_FREE:
            sim = "\n".join(sorted(sim.split("\n")))
            real = "\n".join(sorted(real.split("\n")))
        if "UNCAUGHT" in sim:
            failures.append((name, cmds, sim, real, "CRASH"))
        elif sim == real:
            continue
        elif name in ENV_SPECIFIC:
            env_only += 1
        else:
            failures.append((name, cmds, sim, real, "DIFF"))

    for name, cmds, sim, real, kind in failures:
        print(f"\n--- {kind}: {name}")
        print(f"    commands : {cmds}")
        print(f"    simulated: {sim!r}")
        print(f"    real bash: {real!r}")

    print(f"\n{ran} cases · {len(failures)} divergence(s) · "
          f"{env_only} environment-specific (ignored)")
    if failures:
        print("FAIL — the simulated shell disagrees with bash above.")
        return 1
    print("PASS — the simulated shell matches bash on every case. ✔")
    return 0


if __name__ == "__main__":
    sys.exit(main())
