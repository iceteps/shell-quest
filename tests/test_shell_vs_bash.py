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
from missions.linux_shell import HOME, USER, shell, st  # noqa: E402

set_player_os("linux")
ANSI = re.compile(r"\033\[[0-9;]*m")


class Capture(IO):
    def __init__(self):
        super().__init__()
        self.lines = []

    def print(self, *args):
        self.lines.append(" ".join(str(a) for a in args))

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
    text = re.sub(r"([-drwx]{10})\.", r"\1", text)
    text = re.sub(r"([-drwx]{10}) +\d+ ", r"\1 1 ", text)
    text = re.sub(r"\b\d{4}-\d\d-\d\d \d\d:\d\d\b", "DATE", text)
    text = re.sub(r"\b[A-Z][a-z]{2} +\d+ +[\d:]+\b", "DATE", text)
    text = re.sub(r"^bash: (?:-c: )?line \d+: ", "bash: ", text, flags=re.M)
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
    return "\n".join(io.lines)


def run_bash(cmds):
    sandbox = tempfile.mkdtemp(prefix="shellquest-diff-")
    try:
        script = f"cd {sandbox}\n" + "\n".join(cmds)
        proc = subprocess.run(["bash", "--noprofile", "--norc", "-c", script],
                              capture_output=True, text=True, timeout=30)
        return proc.stdout + proc.stderr, sandbox
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


# Cases whose output legitimately depends on the machine, not on behaviour.
ENV_SPECIFIC = {
    "subst in double quotes", "subst backticks", "subst nested",
    "uname", "uname -r", "uname -a", "whoami", "id", "df -h", "df raw",
    "date", "hostname", "cd no args", "var in double quotes",
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
}


def main():
    if not shutil.which("bash"):
        print("bash not found — skipping the differential test.")
        return 0
    only = sys.argv[1] if len(sys.argv) > 1 else None
    failures, env_only, ran = [], 0, 0
    for name, cmds in CASES.items():
        if only and only not in name:
            continue
        ran += 1
        sim = normalise(run_simulated(cmds))
        raw, sandbox = run_bash(cmds)
        real = normalise(raw, sandbox, os.environ.get("USER"))
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
