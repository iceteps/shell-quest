"""A small but honest POSIX-ish shell, for the 🐧 Linux missions.

The engine's host world is deliberately ONE flat folder with no cwd, no
permissions and no processes — right for the docker/k8s missions, wrong for
teaching Linux. Rather than reshape a world sixteen missions depend on, the
Linux missions register a catch-all handler and run this instead.

The bar here is **behavioural honesty**, not feature count: anything this shell
answers must answer the way bash on a real box would, because a student who
learns a wrong default here gets marked down for it later. Where we can't
simulate something faithfully (a command that would block, a tool that isn't
installed), we say so in the shell's own voice rather than faking success.

Command substitution `$(…)` is supported for the read-only informational commands
(date, whoami, hostname, pwd, uname, id) and honours quoting: it expands inside
double quotes, stays literal inside single quotes. That asymmetry IS the cron
lesson — write the crontab with "$(date)" and the timestamp freezes at write time,
which the student can then see in `crontab -l`.

Deliberate omissions, teaching decisions rather than shortcuts:
  * `$(…)` of state-changing commands is refused rather than half-simulated;
  * no `awk`/`sed` programs — out of scope for class 1, and a half-working awk
    teaches worse than an honest "not here";
  * `sleep` without `&` does not silently background (real bash blocks);
  * `&` only backgrounds at the END of a line — bash also lets it separate
    mid-line, and the shell says so instead of guessing.

Structure: tokenize once (keeping quote state, because that decides globbing),
check the parser rules, split on `;` `&&` `||`, then on `|`, then peel
redirections off each stage. Every builtin returns a Res(out, err, code, nonl)
so pipes, redirection, exit codes and `&&` all compose the way they do in bash;
`nonl` carries whether the stream ended with a newline, which is the difference
`wc -c` measures between `echo a > f` and `printf a > f`.

A script is not a separate dialect: `./s.sh` runs its lines through this same
run_line(), so `exit 3`, `$1`, pipes and redirection inside a script behave
exactly as they do at the prompt.

Behaving like bash is only half of it — it has to FEEL like a terminal, or the
student spends their attention on the simulation instead of on Linux. So the
prompt carries the working directory (every relative path depends on it), the
missions hand the engine `prompt`/`complete` so arrow keys, history and Tab
work through readline, `clear` really clears, and every command answers
`--help` with a page from `linux_help.py` — the same page `help ls` and
`man ls` print.
"""
import difflib
import json
import re
from datetime import datetime

from engine import (c, fit, fit_columns, grid, heading, in_real_world, menu_width,
                    pad, pick, prompt_theme, real_world_entry)
from missions import linux_help

HOME = "/root"
USER = "root"
HOSTNAME = "quest-host"
KERNEL = "6.8.0-quest"


class Res:
    """One command's result: stdout, stderr, exit code — like a real process.

    `out` is the stream MINUS at most one trailing newline (so printing it is
    just io.print), and `nonl` records whether that newline was there. Only
    `echo -n` and a `printf` whose format doesn't end in \\n set it — but they
    are exactly the commands people use to write a file with no trailing
    newline, so `wc -c` can only be honest if the difference is carried.
    """

    __slots__ = ("out", "err", "code", "nonl")

    def __init__(self, out="", err="", code=0, nonl=False):
        self.out, self.err, self.code, self.nonl = out, err, code, nonl


def ok(out=""):
    return Res(out=out)


def stream(text):
    """A Res from a raw stream: keeps whether it ended with a newline."""
    if text.endswith("\n"):
        return Res(out=text[:-1])
    return Res(out=text, nonl=True)


def fail(err, code=1):
    return Res(err=err, code=code)


FALLTHROUGH = object()          # "not my command" — hand back to the engine atlas


# The files class 1 actually opens. /etc/passwd is the canonical "one record per
# line, colon-separated" file — it is the example every cut/grep exercise uses.
SYSTEM_FILES = {
    "/etc/passwd": (
        "root:x:0:0:root:/root:/bin/bash\n"
        "bin:x:1:1:bin:/bin:/sbin/nologin\n"
        "daemon:x:2:2:daemon:/sbin:/sbin/nologin\n"
        "nobody:x:65534:65534:Kernel Overflow User:/:/sbin/nologin\n"
        "systemd-network:x:192:192:systemd Network Management:/:/usr/sbin/nologin\n"
        "sshd:x:74:74:Privilege-separated SSH:/usr/share/empty.sshd:/sbin/nologin\n"
        "student:x:1000:1000:Student:/home/student:/bin/bash\n"),
    "/etc/group": (
        "root:x:0:\n"
        "wheel:x:10:student\n"
        "docker:x:989:student\n"
        "student:x:1000:\n"),
    "/etc/hostname": HOSTNAME + "\n",
    "/etc/os-release": (
        'NAME="Fedora Linux"\n'
        "VERSION=\"40 (Workstation Edition)\"\n"
        "ID=fedora\n"
        "VERSION_ID=40\n"
        'PRETTY_NAME="Fedora Linux 40 (Workstation Edition)"\n'),
    "/etc/hosts": ("127.0.0.1   localhost localhost.localdomain\n"
                   "::1         localhost localhost.localdomain\n"
                   "10.0.2.15   " + HOSTNAME + "\n"),
    "/var/log/syslog": (
        "Aug 15 09:14:02 quest-host systemd[1]: Started Daily apt upgrade.\n"
        "Aug 15 09:14:05 quest-host kernel: [  0.000000] Linux version 6.8.0-quest\n"
        "Aug 15 10:02:11 quest-host sshd[1841]: Accepted publickey for root from 10.0.2.2\n"
        "Aug 15 10:31:44 quest-host CRON[2210]: (root) CMD (/root/backup.sh)\n"
        "Aug 15 11:05:09 quest-host kernel: [ 3241.882] ERROR out of memory: killed process 2318\n"),
}


# ------------------------------------------------------------------- state --
def st(world):
    """Lazily attach the Linux-ish state (cwd, dirs, modes, processes)."""
    f = world.flags
    if "cwd" not in f:
        f["cwd"] = HOME
        # A mission may arrive with state already in flags (a workspace the
        # previous mission built), so merge rather than overwrite.
        f["dirs"] = set(f.get("dirs", ())) | {"/", "/root", "/tmp", "/etc", "/usr",
                                              "/usr/bin", "/var", "/var/log"}
        f.setdefault("modes", {})
        f.setdefault("procs", {})
        f.setdefault("next_pid", 4821)
        f.setdefault("cron", [])
        f.setdefault("last_code", 0)
        for path, body in SYSTEM_FILES.items():
            world.files.setdefault(path, body)
        for name in list(world.files):          # seed files land in HOME
            if not name.startswith("/"):
                world.files[f"{HOME}/{name}"] = world.files.pop(name)
        # A file cannot exist without the directories above it. Deriving them
        # keeps `ls` and `cd` telling the same story about a seeded workspace —
        # a listing that shows a folder `cd` then denies is the worst kind of lie.
        for path in list(world.files):
            parent = path.rsplit("/", 1)[0]
            while parent and parent not in f["dirs"]:
                f["dirs"].add(parent)
                parent = parent.rsplit("/", 1)[0]
    return f


def abspath(world, p):
    f = st(world)
    p = (p or "").strip()
    if p == "~" or p.startswith("~/"):
        p = HOME + p[1:]
    if not p.startswith("/"):
        p = f["cwd"].rstrip("/") + "/" + p
    parts = []
    for seg in p.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if parts:
                parts.pop()
        else:
            parts.append(seg)
    return "/" + "/".join(parts)


def pretty(world, p):
    """Absolute path shown the way a shell would: ~ for home."""
    return "~" + p[len(HOME):] if p == HOME or p.startswith(HOME + "/") else p


def isdir(world, p):
    return p in st(world)["dirs"]


def isfile(world, p):
    return p in world.files


def exists(world, p):
    return isdir(world, p) or isfile(world, p)


def mode_of(world, p):
    return st(world)["modes"].get(p, 0o755 if isdir(world, p) else 0o644)


def readable(world, p):
    """The player owns everything here, so the OWNER triad is what applies.
    Bits that don't stop anything teach nothing — `chmod 000 f` must make
    `cat f` fail, or the whole permissions lesson is decoration."""
    return bool(mode_of(world, p) & 0o400)


def writable(world, p):
    return bool(mode_of(world, p) & 0o200)


def searchable(world, p):
    return bool(mode_of(world, p) & 0o100)


def mode_str(world, p):
    m = mode_of(world, p)
    out = "d" if isdir(world, p) else "-"
    # setuid/setgid/sticky ride in the x column as s/s/t — chmod 4755 vs 755 is
    # invisible in the number but very visible here.
    special = [(m >> 11) & 1, (m >> 10) & 1, (m >> 9) & 1]
    for i, shift in enumerate((6, 3, 0)):
        bits = (m >> shift) & 7
        third = "x" if bits & 1 else "-"
        if special[i]:
            marker = "t" if i == 2 else "s"
            third = marker if bits & 1 else marker.upper()
        out += ("r" if bits & 4 else "-") + ("w" if bits & 2 else "-") + third
    return out


def children(world, d):
    out = set()
    prefix = d.rstrip("/") + "/"
    for p in list(world.files) + list(st(world)["dirs"]):
        if p.startswith(prefix) and p != d:
            out.add(p[len(prefix):].split("/")[0])
    return sorted(out, key=collate)          # `ls` collates like `sort` does


def walk(world, start):
    """Every path at or under start, directories included, sorted."""
    f = st(world)
    pre = start.rstrip("/") + "/"
    hits = [p for p in list(world.files) + list(f["dirs"])
            if p == start or p.startswith(pre)]
    return sorted(set(hits), key=collate)


def mkdir_one(world, p, parents=False, shown=None):
    """`shown` is the path the user actually typed — real tools echo that back,
    not the resolved absolute path, and a student compares the two."""
    f = st(world)
    shown = shown or p
    parent = p.rsplit("/", 1)[0] or "/"
    if isfile(world, p):
        return f"mkdir: cannot create directory '{shown}': File exists"
    if not parents and not isdir(world, parent):
        hint = enoent_hint(world, shown)
        return (f"mkdir: cannot create directory '{shown}': No such file or directory"
                + ("\n" + hint if hint else ""))
    if isdir(world, parent) and not (writable(world, parent) and searchable(world, parent)):
        return f"mkdir: cannot create directory '{shown}': Permission denied"
    if isdir(world, p):
        return None if parents else f"mkdir: cannot create directory '{shown}': File exists"
    acc = ""
    for seg in p.strip("/").split("/"):
        acc += "/" + seg
        if isfile(world, acc):
            return f"mkdir: cannot create directory '{shown}': Not a directory"
        f["dirs"].add(acc)
    return None


def write_file(world, p, content, append=False):
    parent = p.rsplit("/", 1)[0] or "/"
    if isdir(world, p):
        return f"bash: {p}: Is a directory"
    if not isdir(world, parent):
        return f"bash: {p}: No such file or directory"
    # Creating a file is a WRITE to its directory. A directory you can't write
    # (or can't enter) refuses it — otherwise `chmod` on a directory teaches
    # nothing, because nothing it takes away is ever missed.
    if p not in world.files and not (writable(world, parent) and searchable(world, parent)):
        return f"bash: {p}: Permission denied"
    if p in world.files and not writable(world, p):
        return f"bash: {p}: Permission denied"
    if append and p in world.files:
        # `>>` appends bytes, nothing more — it does NOT insert a separator.
        world.files[p] = world.files[p] + content
    else:
        world.files[p] = content
    return None


# ------------------------------------------------------------- tokenizing --
# A backslash-escaped $ or ` must survive expansion as a literal. Standing them
# in as characters no expander looks at keeps the protection per-character.
ESCAPED = {"$": "\x01", "`": "\x02", "*": "\x03", "?": "\x04", "[": "\x05"}
UNESCAPE = {v: k for k, v in ESCAPED.items()}


def unsentinel(text):
    for sentinel, ch in UNESCAPE.items():
        text = text.replace(sentinel, ch)
    return text


def tokenize(line):
    """-> [(word, quoted, single)]  — quoting decides globbing and $-expansion.

    Backslash follows bash: outside quotes it escapes the next character; inside
    DOUBLE quotes it only escapes " \\ $ and ` — which is precisely why "a\\nb" keeps
    its backslash-n for `echo -e` to interpret later, while \\" becomes a literal
    quote; inside SINGLE quotes it is an ordinary character.
    """
    words, buf, seen, sq = [], "", False, False
    q = None
    i, n = 0, len(line)
    while i < n:
        ch = line[i]
        if ch == "\\" and q != "'":
            nxt = line[i + 1] if i + 1 < n else ""
            if q is None:
                if nxt:
                    # `echo \$HOME` must print $HOME, not your home directory: a
                    # backslash quotes the next character as surely as '…' does.
                    # Only the ONE character is protected, so a sentinel beats
                    # marking the whole word quoted (`\$x*` still globs).
                    buf += ESCAPED.get(nxt, nxt)
                    i += 2
                    continue
                i += 1
                continue
            if nxt in '"\\$`':                       # inside "…"
                buf += nxt
                i += 2
                continue
            buf += ch                                 # keep the backslash verbatim
            i += 1
            continue
        if q:
            if ch == q:
                q = None
            else:
                buf += ch
            i += 1
            continue
        if ch in "\"'":
            q = ch
            seen = True
            sq = sq or ch == "'"
            i += 1
            continue
        if ch.isspace():
            if buf or seen:
                words.append((buf, seen, sq))
                buf, seen, sq = "", False, False
            i += 1
            continue
        # Operators are self-delimiting in bash: `echo a;echo b` and `2>/dev/null`
        # need no spaces, so recognise them mid-word rather than only as lone tokens.
        # `2>&1` duplicates a descriptor — one operator, not `2>` `&` `1`.
        dup = re.match(r">&(\d)", line[i:])
        if dup:
            fd = buf if re.fullmatch(r"\d", buf) else "1"
            if buf and not re.fullmatch(r"\d", buf):
                words.append((buf, seen, sq))
            words.append((f"{fd}>&{dup.group(1)}", False, False))
            buf, seen, sq = "", False, False
            i += dup.end()
            continue
        two = line[i:i + 2]
        if two in ("&&", "||", ">>", ";;", "|&"):
            if buf or seen:
                words.append((buf, seen, sq))
                buf, seen, sq = "", False, False
            words.append((two, False, False))
            i += 2
            continue
        if ch in ";|<&" or (ch == ">" and not re.fullmatch(r"\d", buf)):
            if buf or seen:
                words.append((buf, seen, sq))
                buf, seen, sq = "", False, False
            words.append((ch, False, False))
            i += 1
            continue
        if ch == ">" and re.fullmatch(r"\d", buf):    # 2>file — fd-qualified
            words.append((buf + ">", False, False))
            buf, seen, sq = "", False, False
            i += 1
            continue
        buf += ch
        i += 1
    if q:
        return None                                   # unmatched quote
    if buf or seen:
        words.append((buf, seen, sq))
    return words


# Only side-effect-free commands may be substituted: enough for the lesson,
# and nothing that could surprise a student by running twice.
SUBSTITUTABLE = {"date", "whoami", "hostname", "pwd", "uname", "id", "basename"}


def expand_subst(world, io, text):
    """$(cmd) and `cmd` — expanded only where bash would expand them."""
    def one(match):
        inner = (match.group(1) or match.group(2) or "").strip()
        if not inner:
            return ""
        argv = [w for w, _q, _s in (tokenize(inner) or [])]
        if not argv or argv[0] not in SUBSTITUTABLE:
            return match.group(0)          # leave anything else untouched
        res = run_cmd(world, io, argv)
        return res.out if isinstance(res, Res) else match.group(0)
    prev = None
    while prev != text:                     # nested $( $( ) ) resolves inside-out
        prev = text
        text = re.sub(r"\$\(([^()]*)\)|`([^`]*)`", one, text)
    return text


def expand_vars(world, s):
    f = st(world)
    # $1..$9, $@, $# — a script's arguments. Outside a script there are none,
    # which is exactly what bash says too (empty, and $# is 0).
    argv = f.get("script_args") or []
    s = re.sub(r"\$\{?([1-9])\}?",
               lambda m: argv[int(m.group(1)) - 1] if int(m.group(1)) <= len(argv) else "", s)
    for var, val in (("$HOME", HOME), ("${HOME}", HOME), ("$PWD", f["cwd"]),
                     ("${PWD}", f["cwd"]), ("$USER", USER), ("${USER}", USER),
                     ("$HOSTNAME", HOSTNAME), ("$#", str(len(argv))),
                     ("$@", " ".join(argv)), ("$*", " ".join(argv)),
                     ("$0", f.get("script_name") or "bash"), ("$?", str(f["last_code"]))):
        s = s.replace(var, val)
    return s


def expand_braces(word):
    """file{1,2,3}.txt and {1..3} — students type these; bash expands them."""
    m = re.search(r"\{([^{}]*)\}", word)
    if not m:
        return [word]
    body, pre, post = m.group(1), word[:m.start()], word[m.end():]
    if ".." in body:
        a, _, b = body.partition("..")
        try:
            lo, hi = int(a), int(b)
            items = [str(i) for i in (range(lo, hi + 1) if lo <= hi else range(lo, hi - 1, -1))]
        except ValueError:
            if len(a) == 1 and len(b) == 1 and a.isalpha() and b.isalpha():
                items = [chr(i) for i in range(ord(a), ord(b) + 1)]
            else:
                return [word]
    elif "," in body:
        items = body.split(",")
    else:
        return [word]
    out = []
    for it in items:
        out.extend(expand_braces(pre + it + post))
    return out


def glob_word(world, word):
    """*, ? and [..] against the simulated tree — unmatched patterns stay literal,
    exactly as bash does when nullglob is off."""
    if not any(ch in word for ch in "*?["):
        return [word]
    # A trailing slash is part of the pattern's meaning: `*/` matches ONLY
    # directories, and the slash stays in the result.
    dirs_only = word.endswith("/")
    stem = word[:-1] if dirs_only else word
    target = abspath(world, stem)
    d, _, pat = target.rpartition("/")
    d = d or "/"
    if not isdir(world, d):
        return [word]
    try:
        rx = re.compile("^" + re.escape(pat).replace(r"\*", "[^/]*").replace(r"\?", "[^/]")
                        .replace(r"\[", "[").replace(r"\]", "]") + "$")
    except re.error:
        return [word]          # an unbalanced [ is a literal to bash, not an error
    # Dotfiles hide from globs unless the pattern asks for them by leading dot —
    # which is why `rm *` leaves `.bashrc` alone and `ls` looks empty in $HOME.
    want_dots = pat.startswith(".")
    hits = [n for n in children(world, d)
            if rx.match(n) and (want_dots or not n.startswith("."))]
    if dirs_only:
        hits = [n for n in hits if isdir(world, d.rstrip("/") + "/" + n)]
    if not hits:
        return [word]
    keep_dir = "/" in stem
    base = stem.rsplit("/", 1)[0] if keep_dir else ""
    tail = "/" if dirs_only else ""
    return [((base + "/" + h) if keep_dir else h) + tail
            for h in sorted(hits, key=collate)]


def expand_argv(world, words, io=None):
    """(word, quoted, single) triples -> flat argv, plus the redirection plan."""
    argv, redirs = [], []
    i = 0
    while i < len(words):
        w, quoted, single = words[i]
        if not quoted and re.fullmatch(r"\d>&\d", w):   # 2>&1 — no target to read
            redirs.append((w, None))
            i += 1
            continue
        if not quoted and (w in (">", ">>", "<") or re.fullmatch(r"\d>>?", w)):
            if i + 1 < len(words):
                nxt, nxt_quoted, _ = words[i + 1]
                # `ls > > f` — the target of a redirect can't be another operator.
                if not nxt_quoted and (nxt in CTRL_OPS or nxt in (">", ">>", "<")
                                       or re.fullmatch(r"\d>>?", nxt)):
                    return argv, redirs, f"bash: syntax error near unexpected token `{nxt}'"
                redirs.append((w, unsentinel(expand_vars(world, nxt))))
                i += 2
                continue
            return argv, redirs, "bash: syntax error near unexpected token `newline'"

        if single:
            argv.append(w)                       # single quotes: literal, full stop
        else:
            v = expand_vars(world, expand_subst(world, io, w))
            for b in ([v] if quoted else expand_braces(v)):
                argv.extend([b] if quoted else glob_word(world, b))
        i += 1
    return [unsentinel(a) for a in argv], redirs, None


# Control operators, in bash's sense: they separate commands rather than being
# arguments to one. `;` and `&` may legally end a line; the others may not.
CTRL_OPS = {";", "&&", "||", "|", "|&", "&", ";;"}
TERMINATORS = {";", "&"}


def check_syntax(words):
    """bash's parser rules for control operators. -> error string, or None.

    Real bash is a parser, not a splitter: it rejects `| echo hi`, `;;` and a
    trailing `&&` outright. Silently dropping the empty command instead would
    teach that those lines are fine — they are the classic beginner typos.
    """
    prev_was_op = True                      # start of line == "after a separator"
    for w, quoted, _single in words:
        if quoted or w not in CTRL_OPS:
            prev_was_op = False
            continue
        # `;;` only means anything inside a `case`, which this shell has none of.
        if prev_was_op or w == ";;":
            return f"bash: syntax error near unexpected token `{w}'"
        prev_was_op = True
    if prev_was_op and words:
        last = words[-1][0]
        if last not in TERMINATORS:
            return "bash: syntax error: unexpected end of file"
    return None


def split_on(words, seps):
    """Split a token list on the given unquoted operator words."""
    groups, cur, ops = [], [], []
    for w, quoted, single in words:
        if not quoted and w in seps:
            groups.append(cur)
            ops.append(w)
            cur = []
        else:
            cur.append((w, quoted, single))
    groups.append(cur)
    return groups, ops


# ---------------------------------------------------------- fake system data --
DF_H = ("Filesystem      Size  Used Avail Use% Mounted on\n"
        "/dev/nvme0n1p6  220G   16G  204G   8% /\n"
        "tmpfs           7.7G   18M  7.7G   1% /dev/shm\n"
        "/dev/nvme0n1p5  2.0G  843M  985M  47% /boot")
DF_RAW = ("Filesystem     1K-blocks     Used Available Use% Mounted on\n"
          "/dev/nvme0n1p6 230686720 16777216 213909504   8% /\n"
          "tmpfs            8074240    18432   8055808   1% /dev/shm\n"
          "/dev/nvme0n1p5   2031440   863232   1008208  47% /boot")
IP_A = ("1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue state UNKNOWN group default\n"
        "    inet 127.0.0.1/8 scope host lo\n"
        "2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc fq_codel state UP\n"
        "    inet 10.0.2.15/24 brd 10.0.2.255 scope global dynamic eth0\n"
        "    inet6 fe80::a00:27ff:fe4e:66a1/64 scope link")

# The DevOps tools ARE installed on this host — `which docker` says so, and `setup`
# tells the player to version-check them. They're just taught in other missions.
TOOL_VERSIONS = {
    "docker": "Docker version 26.1.4, build 5650f9b",
    "podman": "podman version 5.1.1",
    "git": "git version 2.45.2",
    "kubectl": "Client Version: v1.30.2\nKustomize Version: v5.0.4",
    "minikube": "minikube version: v1.33.1",
    "helm": 'version.BuildInfo{Version:"v3.15.2", GitTreeState:"clean", GoVersion:"go1.22.4"}',
    "terraform": "Terraform v1.9.0\non linux_amd64",
    "ansible": "ansible [core 2.17.1]",
    "python3": "Python 3.12.4",
    "python": "Python 3.12.4",
}
TOOL_HOME = {
    "docker": "the 🐳 Docker missions", "podman": "the 🐳 Docker missions",
    "git": "the 🌿 Git missions", "kubectl": "the ☸️ Kubernetes missions",
    "minikube": "the ☸️ Kubernetes missions", "helm": "the ⎈ Helm missions",
    "terraform": "the 🏗️ Terraform missions", "ansible": "the 📜 Ansible missions",
    "python3": "the 📨 RabbitMQ mission", "python": "the 📨 RabbitMQ mission",
}
# Only what this shell can ACTUALLY run. `which X` printing a path is a promise
# that X works — listing something we then refuse would break the very
# check-before-you-install habit the game teaches.
ON_PATH = {
    "bash", "sh", "ls", "cat", "cp", "mv", "rm", "rmdir", "mkdir", "touch", "chmod",
    "chown", "grep", "find", "head", "tail", "wc", "sort", "uniq", "tac", "seq", "cut",
    "echo", "printf", "pwd", "cd", "date", "df", "du", "ps", "kill", "sleep", "ip",
    "ping", "tar", "gzip", "gunzip", "crontab", "whoami", "id", "basename", "dirname",
    "groups", "hostname", "uname", "which", "less", "more", "true", "false", "sudo",
} | set(TOOL_VERSIONS)

def print_help_index(io):
    """What `help` prints: every command this shell has, with what it DOES.

    The old version was a flag cheat-sheet with no way to go deeper, which is a
    dead end the moment you need the third flag. Each name here has a real page
    behind it — `help ls`, `ls --help` and `man ls` all reach it.
    """
    w = menu_width()
    io.print(c("🐧 This is a Linux shell. Everything below really runs here.", "bold"))
    io.print(c("   help <name>  ·  <name> --help  ·  man <name>   → the full page for one of them",
               "cyan"))
    # Fifty-odd commands one-per-line scrolls the answer off the top of the
    # screen. Wide window → columns; narrow one → the plain list it always was.
    names = [n for _t, group in linux_help.GROUPS for n in group]
    name_w = max(len(n) for n in names)
    # Size the column for the summaries most of them have, not for the longest
    # one — three commands ending in `…` is a better index than every command
    # in a single column. The full sentence is one `help <name>` away.
    lengths = sorted(len(linux_help.summary(n)) for n in names)
    natural = name_w + 2 + lengths[int(len(lengths) * 0.75)]
    ncols, widths = fit_columns(natural, w, gutter=3, indent=3, max_cols=3)
    for title, group in linux_help.GROUPS:
        io.print("")
        io.print(heading(c(f"  {title}", "bold"), cols=w))
        cells = [pad(fit(f"{c(n.ljust(name_w), 'cyan')}  {c(linux_help.summary(n), 'dim')}",
                         widths[i % ncols]), widths[i % ncols])
                 for i, n in enumerate(group)]
        for line in grid(cells, ncols, gutter=3, indent=3):
            io.print(line)
    io.print(c("\n   the tools taught in other missions (docker · git · kubectl · helm · "
               "terraform · ansible) are installed here but not wired up", "dim"))


def real_tool_note(name):
    """Every page ends by pointing at the real tool. The pages here describe what
    THIS shell implements — the authority on the rest is the binary on the
    player's own machine, and saying so is the difference between a manual and a
    walled garden."""
    if name not in ON_PATH or name in TOOL_VERSIONS:
        return ""                       # topics and other missions' tools have no binary
    return "\n" + pick({
        "windows": f"\nThe full GNU page is one WSL or Git Bash away: `{name} --help` there.",
        "*": f"\nYour own machine has the real one: `{name} --help` in a terminal prints "
             "every flag.",
    })


def prompt(world):
    """The shell's own prompt — Fedora's default for root, cwd and all.

    A prompt with no cwd in it is the single most disorienting thing about a
    simulated shell: every path you type is relative to a place you can't see,
    so `touch week1/notes.txt` fails from the wrong directory and looks like a
    bug in the game. Real bash tells you where you are; so does this.

    `theme` swaps in the powerlevel10k-style prompts. The classic one stays the
    default deliberately: it is the prompt waiting on the student's first real
    server, and a game that never shows it hasn't prepared them for that.
    """
    f = st(world)
    here = pretty(world, f["cwd"])
    if prompt_theme()[0] == "classic":
        return c(f"[{USER}@{HOSTNAME} {here}]# ", "cyan")
    from missions import prompt_theme as theme    # local: engine imports us first
    code = f.get("last_code", 0)
    jobs = len(f.get("procs", {}))
    assist = world.flags.get("_assist_xp", 0)
    return theme.render(
        [("host", f"{USER}@{HOSTNAME}", "yellow"),
         ("folder", here, "cyan"),
         # p10k's habit, and the useful one: the segments that say nothing stay
         # off the prompt entirely.
         ("err", str(code) if code else "", "red"),
         ("jobs", str(jobs) if jobs else "", "magenta")],
        [("kbd", f"−{assist} XP" if assist else "", "magenta"),
         ("clock", datetime.now().strftime("%H:%M"), "dim")])


def complete(world, text):
    """Tab-completion candidates for `text`: command names and paths.

    Returned bare, with no trailing `/` or space: readline appends its own
    separator after a unique match, and a marker of ours would land on the
    wrong side of it.
    """
    f = st(world)
    names = ON_PATH | set(linux_help.PAGES) | {"help", "man", "edit", "jobs",
                                               "history", "clear", "type", "exit"}
    out = [n for n in names if n.startswith(text)] if "/" not in text else []
    head, sep, tail = text.rpartition("/")
    if sep:
        # `/et` splits into head="" — which is the ROOT directory, not "no
        # directory given". Reading it as the latter completed `/et<TAB>`
        # against the cwd and found nothing, in the missions whose whole
        # subject is absolute vs relative paths.
        base, prefix = abspath(world, head or "/"), head + "/"
    else:
        base, prefix = f["cwd"], ""
    if isdir(world, base):
        out += [prefix + n for n in children(world, base)
                if n.startswith(tail) and (tail.startswith(".") or not n.startswith("."))]
    return sorted(set(out))


def enoent_hint(world, typed, whole=False):
    """'No such file or directory' is true but rarely useful on its own.

    The overwhelmingly common cause is a relative path typed from the wrong
    directory — the folder DOES exist, just not under the cwd. Say so, and say
    where it is, because that is the lesson (paths are relative to `pwd`).
    `whole` asks about the path itself (cd) rather than its parent (touch).
    """
    f = st(world)
    if typed.startswith(("/", "~", "$")):
        return None
    missing = abspath(world, typed)
    if not whole:
        missing = missing.rsplit("/", 1)[0]
        if isdir(world, missing):
            return None                 # the parent is fine; something else failed
    wanted = missing.rsplit("/", 1)[-1]
    elsewhere = [d for d in sorted(f["dirs"]) if d.rsplit("/", 1)[-1] == wanted
                 and d != missing]
    if not elsewhere:
        return None
    tail = "" if whole else "/" + typed.rsplit("/", 1)[-1]
    # `touch week1/notes.txt` while standing IN week1: the directory isn't
    # somewhere else, it's underfoot. Naming it again is the whole mistake.
    if f["cwd"] in elsewhere:
        return c(f"   (you are already inside {wanted} — from here the path is just "
                 f"{tail.lstrip('/') or '.'}, with no {wanted}/ in front of it)", "dim")
    where = pretty(world, elsewhere[0])
    return c(f"   (paths are relative to where you stand, and you are in "
             f"{pretty(world, f['cwd'])} — {wanted} is at {where}, so try {where}{tail})",
             "dim")


# ---------------------------------------------------------------- builtins --
def _ls(world, args, f, tty=True):
    long = any(a.startswith("-") and not a.startswith("--") and "l" in a for a in args)
    all_ = any(a.startswith("-") and not a.startswith("--") and "a" in a for a in args)
    rec = any(a.startswith("-") and not a.startswith("--") and "R" in a for a in args)
    # -d: the DIRECTORY itself, not its contents. `ls -ld dir` is how you read a
    # directory's own permission bits — the whole point of the chmod exercise.
    dironly = any(a.startswith("-") and not a.startswith("--") and "d" in a for a in args)
    targets = [a for a in args if not a.startswith("-")] or ["."]
    out, errs = [], []

    def saw(path):
        """Remember WHICH path's mode string was printed. `ls -l` somewhere else
        is not proof that the student read the bits on THIS file, and the octal
        drill is exactly 'set it, then read the string back'."""
        f["saw_perms"] = True
        f.setdefault("perms_seen", set()).add(path)

    def listing(d, label=None):
        names = children(world, d)
        if not all_:
            names = [n for n in names if not n.startswith(".")]
        else:
            names = [".", ".."] + names
        block = []
        if label:
            block.append(f"{label}:")
        if long:
            block.append(f"total {max(len(names) * 4, 4)}")
            for n in names:
                full = d.rstrip("/") + "/" + n if n not in (".", "..") else d
                if full in f["modes"]:
                    saw(full)
                size = len(world.files.get(full, "")) if isfile(world, full) else 4096
                block.append(f"{mode_str(world, full)} 1 {USER} {USER} {size:>6} Aug 15 14:32 {n}")
        elif names:
            block.append(("  " if tty else "\n").join(names))
        return "\n".join(block)

    for t in targets:
        p = abspath(world, t)
        if isfile(world, p):
            if long:
                if p in f["modes"]:
                    saw(p)
                out.append(f"{mode_str(world, p)} 1 {USER} {USER} "
                           f"{len(world.files[p]):>6} Aug 15 14:32 {t}")
            else:
                out.append(t)  # a named file prints alone either way
        elif isdir(world, p) and dironly:
            if long:
                if p in f["modes"]:
                    saw(p)
                out.append(f"{mode_str(world, p)} 1 {USER} {USER} "
                           f"{4096:>6} Aug 15 14:32 {t}")
            else:
                out.append(t)
        elif isdir(world, p):
            multi = len(targets) > 1 or rec
            out.append(listing(p, t.rstrip("/") if multi else None))
            if rec:
                for kid in walk(world, p):
                    if isdir(world, kid) and kid != p:
                        out.append("\n" + listing(kid, t.rstrip("/") + kid[len(p):]))
        else:
            errs.append(f"ls: cannot access '{t}': No such file or directory")
    return Res("\n".join(x for x in out if x != ""), "\n".join(errs), 2 if errs else 0)


def _cp_mv(world, prog, args, f):
    rec = any(a.startswith("-") and ("r" in a.lower()) for a in args)
    names = [a for a in args if not a.startswith("-")]
    if len(names) < 2:
        got = f" after '{names[0]}'" if names else ""
        return fail(f"{prog}: missing destination file operand{got}\n"
                    f"Try '{prog} --help' for more information.")
    *srcs, dst_raw = names
    dst = abspath(world, dst_raw)
    # A trailing slash is a hard assertion that the target is a directory.
    if dst_raw.endswith("/") and not isdir(world, dst):
        # A missing path and an existing FILE are different complaints, and with
        # several sources the target is described as a target, not a file.
        gone = not exists(world, dst)
        if len(srcs) > 1:
            return fail(f"{prog}: target '{dst_raw}': "
                        + ("No such file or directory" if gone else "Not a directory"))
        if prog == "mv":
            return fail(f"mv: cannot move '{srcs[0]}' to '{dst_raw}': "
                        + ("No such file or directory" if gone else "Not a directory"))
        return fail(f"cp: cannot create regular file '{dst_raw}': "
                    + ("No such file or directory" if gone else "Not a directory"))
    if len(srcs) > 1 and not isdir(world, dst):
        return fail(f"{prog}: target '{dst_raw}': "
                    + ("No such file or directory" if not exists(world, dst)
                       else "Not a directory"))
    errs = []
    for s_raw in srcs:
        src = abspath(world, s_raw)
        target = dst.rstrip("/") + "/" + src.rsplit("/", 1)[1] if isdir(world, dst) else dst
        # `mv f .` resolves to the file itself. Writing then unlinking the same key
        # would delete the file outright — real mv refuses every same-file spelling.
        if src == target:
            errs.append(f"{prog}: '{s_raw}' and '{dst_raw}"
                        f"{'' if dst_raw.endswith('/') or not isdir(world, dst) else '/' + src.rsplit('/', 1)[1]}"
                        f"' are the same file")
            continue
        if isdir(world, src):
            if prog == "cp" and not rec:
                errs.append(f"cp: -r not specified; omitting directory '{s_raw}'")
                continue
            # Copying a tree into a spot underneath itself would recurse forever.
            if target == src or target.startswith(src.rstrip("/") + "/"):
                errs.append(f"{prog}: cannot copy a directory, '{s_raw}', "
                            f"into itself, '{dst_raw}'")
                continue
            if isfile(world, target):
                errs.append(f"{prog}: cannot overwrite non-directory '{dst_raw}' "
                            f"with directory '{s_raw}'")
                continue
            for p in walk(world, src):
                new = target + p[len(src):]
                if isdir(world, p):
                    f["dirs"].add(new)
                else:
                    world.files[new] = world.files[p]
                    if p in f["modes"]:
                        f["modes"][new] = f["modes"][p]
            f["dirs"].add(target)
            if prog == "mv":
                for p in walk(world, src):
                    world.files.pop(p, None)
                    f["dirs"].discard(p)
                    f["modes"].pop(p, None)
        elif isfile(world, src):
            if isdir(world, target):
                errs.append(f"{prog}: cannot overwrite directory '{dst_raw}' with non-directory")
                continue
            # cp READS the source; mv only relinks it, so it needs no read bit.
            if prog == "cp" and not readable(world, src):
                errs.append(f"cp: cannot open '{s_raw}' for reading: Permission denied")
                continue
            err = write_file(world, target, world.files[src])
            if err:
                # write_file speaks in bash's voice for `>`; here the tool is cp/mv.
                verb = "copy" if prog == "cp" else "move"
                errs.append(f"{prog}: cannot {verb} '{s_raw}' to '{dst_raw}': "
                            "No such file or directory")
                continue
            f["modes"][target] = mode_of(world, src)
            if prog == "mv":
                world.files.pop(src, None)
                f["modes"].pop(src, None)
        else:
            errs.append(f"{prog}: cannot stat '{s_raw}': No such file or directory")
    return Res("", "\n".join(errs), 1 if errs else 0)


def _chmod(world, args, f):
    rec = any(a.startswith("-") and "R" in a for a in args)
    names = [a for a in args if not (a.startswith("-") and re.fullmatch(r"-[Rvc]+", a))]
    if len(names) < 2:
        return fail("chmod: missing operand\n"
                    "Try 'chmod --help' for more information.")
    spec, targets = names[0], names[1:]

    def apply(cur, spec):
        if re.fullmatch(r"[0-7]{3,4}", spec):
            # A 4-digit mode carries setuid/setgid/sticky in the leading digit;
            # dropping it would make `chmod 4755` look like plain 755.
            special = int(spec[-4], 8) if len(spec) == 4 else 0
            return (special << 9) | int(spec[-3:], 8)
        if "," in spec:                      # u+x,g+w — each clause in turn
            for clause in spec.split(","):
                cur = apply(cur, clause)
                if cur is None:
                    return None
            return cur
        m = re.fullmatch(r"([ugoa]*)([-+=])([rwx]*)", spec)
        if not m:
            return None
        who, op, what = m.group(1) or "a", m.group(2), m.group(3)
        mask = 0
        for w, shift in (("u", 6), ("g", 3), ("o", 0)):
            if w in who or "a" in who:
                for ch, bit in (("r", 4), ("w", 2), ("x", 1)):
                    if ch in what:
                        mask |= bit << shift
        if op == "+":
            return cur | mask
        if op == "-":
            return cur & ~mask
        keep = 0
        for w, shift in (("u", 6), ("g", 3), ("o", 0)):
            if not ("a" in who or w in who):
                keep |= (cur & (7 << shift))
        return keep | mask

    errs = []
    for t in targets:
        p = abspath(world, t)
        if not exists(world, p):
            errs.append(f"chmod: cannot access '{t}': No such file or directory")
            continue
        paths = walk(world, p) if (rec and isdir(world, p)) else [p]
        for q in paths:
            new = apply(mode_of(world, q), spec)
            if new is None:
                return fail(f"chmod: invalid mode: '{spec}'\n"
                            "Try 'chmod --help' for more information.")
            f["modes"][q] = new
    return Res("", "\n".join(errs), 1 if errs else 0)


def _find(world, args):
    starts = [a for a in args if not a.startswith("-")]
    opts = {}
    for i, a in enumerate(args):
        if a in ("-name", "-iname", "-type", "-maxdepth"):
            if i + 1 >= len(args):
                return fail(f"find: missing argument to `{a}'")
            opts[a] = args[i + 1]
            if args[i + 1] in starts:
                starts.remove(args[i + 1])
    starts = starts or ["."]
    out, errs = [], []
    for s_raw in starts:
        s = abspath(world, s_raw)
        if not exists(world, s):
            errs.append(f"find: '{s_raw}': No such file or directory")
            continue
        if opts.get("-type") not in (None, "f", "d"):
            return fail(f"find: Unknown argument to -type: {opts['-type']}")
        maxdepth = None
        if "-maxdepth" in opts:
            if not opts["-maxdepth"].isdigit():
                return fail(f"find: Expected a positive decimal integer argument to "
                            f"-maxdepth, but got `{opts['-maxdepth']}'")
            maxdepth = int(opts["-maxdepth"])
        for p in walk(world, s):
            if maxdepth is not None:
                depth = 0 if p == s else p[len(s.rstrip("/")):].strip("/").count("/") + 1
                if depth > maxdepth:
                    continue
            name = p.rsplit("/", 1)[1] or "/"
            if "-type" in opts:
                if opts["-type"] == "f" and not isfile(world, p):
                    continue
                if opts["-type"] == "d" and not isdir(world, p):
                    continue
            pat = opts.get("-name") or opts.get("-iname")
            if pat and not _fnmatch(pat, name, "-iname" in opts):
                continue
            out.append(p if s_raw.startswith(("/", "~"))
                       else s_raw.rstrip("/") + p[len(s):])
    if opts.get("-name", "").endswith(".txt") and any(o.endswith(".txt") for o in out):
        world.flags["found_txt"] = True
    return Res("\n".join(out), "\n".join(errs), 1 if errs else 0)


def _fnmatch(pattern, name, insensitive=False):
    rx = "^" + re.escape(pattern).replace(r"\*", ".*").replace(r"\?", ".") + "$"
    try:
        return re.match(rx, name, re.I if insensitive else 0) is not None
    except re.error:
        return pattern == name


def bre_to_python(pat):
    """POSIX basic regex -> Python. In BRE `+ ? | ( ) { }` are LITERAL and their
    backslashed forms are the operators — exactly backwards from every other
    regex dialect, which is why `grep -E` exists and why students get bitten."""
    out, i, n = "", 0, len(pat)
    while i < n:
        ch = pat[i]
        if ch == "[":                       # bracket expression: copy verbatim
            j = i + 1
            if j < n and pat[j] == "^":
                j += 1
            if j < n and pat[j] == "]":
                j += 1
            while j < n and pat[j] != "]":
                j += 1
            if j >= n:                      # unterminated — grep errors on this
                raise re.error("Invalid regular expression" if j == i + 1
                               else "Unmatched [, [^, [:, [., or [=")
            out += pat[i:j + 1]
            i = j + 1
            continue
        if ch == "\\" and i + 1 < n:
            nxt = pat[i + 1]
            out += nxt if nxt in "+?|(){}" else "\\" + nxt
            i += 2
            continue
        out += re.escape(ch) if ch in "+?|(){}" else ch
        i += 1
    return out


# Python's regex complaints name Python constructs; grep's name POSIX ones.
_REGEX_ERRORS = (
    ("unterminated subpattern", "Unmatched ( or \\("),
    ("unbalanced parenthesis", "Unmatched ) or \\)"),
    ("unterminated character set", "Unmatched [, [^, [:, [., or [="),
    ("nothing to repeat", "Invalid preceding regular expression"),
    ("bad character range", "Invalid range end"),
)


def _regex_error(exc):
    msg = str(exc).split(" at position")[0]
    if msg.startswith(("Unmatched", "Invalid")):
        return msg                          # already raised in grep's own words
    for needle, grep_says in _REGEX_ERRORS:
        if needle in msg:
            return grep_says
    return "Invalid regular expression"


def _grep(world, args, stdin):
    flags = "".join(a[1:] for a in args if a.startswith("-") and not a.startswith("--"))
    names = [a for a in args if not a.startswith("-")]
    if not names:
        return fail("usage: grep [OPTION]... PATTERN [FILE]...", 2)
    pattern, files = names[0], names[1:]

    # -F fixed string · -E extended regex · default is POSIX BRE, like real grep.
    try:
        if "F" in flags:
            rx = re.compile(re.escape(pattern), re.I if "i" in flags else 0)
        else:
            body = pattern if "E" in flags else bre_to_python(pattern)
            if "w" in flags:
                body = r"\b(?:" + body + r")\b"
            rx = re.compile(body, re.I if "i" in flags else 0)
    except re.error as e:
        return fail(f"grep: {_regex_error(e)}", 2)

    def selected(line):
        # real grep is CASE-SENSITIVE unless -i. Getting this wrong would teach
        # a habit that fails the moment it matters.
        return bool(rx.search(line)) != ("v" in flags)
    sources = []
    errs = []
    if files:
        for fn in files:
            p = abspath(world, fn)
            if isdir(world, p):
                if "r" in flags:
                    for q in walk(world, p):
                        if isfile(world, q):
                            sources.append((fn.rstrip("/") + q[len(p):], world.files[q]))
                else:
                    errs.append(f"grep: {fn}: Is a directory")
            elif isfile(world, p):
                if readable(world, p):
                    sources.append((fn, world.files[p]))
                else:
                    errs.append(f"grep: {fn}: Permission denied")
            else:
                sources.append((fn, None))
    elif stdin is not None:
        sources = [(None, stdin)]
    else:
        return fail("usage: grep [OPTION]... PATTERN [FILE]...", 2)

    def lines_of(body):
        ls = body.split("\n")
        if ls and ls[-1] == "":
            ls.pop()                        # a trailing newline is not a line
        return ls

    out, hits, matched_files = [], 0, []
    # real grep prefixes the filename whenever more than one file is in play —
    # and -r always is, even when it matches exactly one file.
    many = len(sources) > 1 or ("r" in flags and files)
    for label, body in sources:
        if body is None:
            errs.append(f"grep: {label}: No such file or directory")
            continue
        for i, line in enumerate(lines_of(body), 1):
            if selected(line):
                hits += 1
                if label is not None and label not in matched_files:
                    matched_files.append(label)
                prefix = f"{label}:" if many else ""
                out.append(f"{prefix}{i}:{line}" if "n" in flags else prefix + line)
    code = 2 if errs else (0 if hits else 1)
    if "q" in flags:                        # -q: exit status only, say nothing
        return Res("", "\n".join(errs), code)
    if "l" in flags:                        # -l: the FILE names, once each
        return Res("\n".join(matched_files), "\n".join(errs), code)
    if "c" in flags:
        rows = []
        for label, body in sources:
            if body is None:
                continue
            n = sum(1 for line in lines_of(body) if selected(line))
            rows.append(f"{label}:{n}" if many else str(n))
        return Res("\n".join(rows), "\n".join(errs), code)
    return Res("\n".join(out), "\n".join(errs), code)


def _read_input(world, args, stdin, prog):
    """File argument, else piped stdin."""
    names = [a for a in args if not a.startswith("-") and not re.fullmatch(r"\d+", a)]
    if names:
        p = abspath(world, names[-1])
        if isdir(world, p):
            return None, f"{prog}: error reading '{names[-1]}': Is a directory"
        if not isfile(world, p):
            return None, f"{prog}: cannot open '{names[-1]}' for reading: No such file or directory"
        if not readable(world, p):
            return None, f"{prog}: {names[-1]}: Permission denied"
        return world.files[p], None
    if stdin is not None:
        return stdin, None
    return None, f"{prog}: needs a file argument or piped input here"


def _tar(world, io, args, f):
    joined = [a for a in args if a.startswith("-")] + \
             [a for a in args if not a.startswith("-")]
    flags = "".join(a.lstrip("-") for a in joined if a.startswith("-"))
    if not flags and args and re.fullmatch(r"[cxtvzf]+", args[0]):
        flags = args[0]                                  # BSD-style `tar cvf`
    names = [a for a in args if not a.startswith("-") and a != flags]
    if not any(k in flags for k in "cxt"):
        return fail("tar: You must specify one of the '-Acdtrux', '--delete' or '--test-label' options\n"
                    "Try 'tar --help' or 'tar --usage' for more information.")
    if "f" not in flags:
        return fail("tar: Refusing to read archive contents from terminal (missing -f option?)", 2)
    if not names:
        return fail("tar: option requires an argument -- 'f'\n"
                    "Try 'tar --help' or 'tar --usage' for more information.", 2)

    if "c" in flags:
        if len(names) < 2:
            return fail("tar: Cowardly refusing to create an empty archive\n"
                        "Try 'tar --help' or 'tar --usage' for more information.", 2)
        archive, srcs = abspath(world, names[0]), names[1:]
        members = []
        for s_raw in srcs:
            s = abspath(world, s_raw)
            if not exists(world, s):
                return fail(f"tar: {s_raw}: Cannot stat: No such file or directory\n"
                            "tar: Exiting with failure status due to previous errors", 2)
            # tar stores member names as GIVEN: `tar -cf d.tar d` records "d/…",
            # not the absolute path. Storing "path\tstored" keeps both.
            base = s_raw.rstrip("/")
            for q in walk(world, s):
                stored = base + q[len(s):] if not s_raw.startswith(("/", "~")) \
                    else q.lstrip("/")
                members.append((q, stored))
        members = sorted(set(members), key=lambda t: t[1])
        if "z" in flags and not archive.endswith(".gz"):
            archive += ".gz" if archive.endswith(".tar") else ""
        payload = [{"name": stored, "dir": isdir(world, real),
                    "mode": mode_of(world, real),
                    "body": world.files.get(real, "")} for real, stored in members]
        world.files[archive] = json.dumps(payload)
        f["modes"][archive] = 0o644
        shown = "\n".join(stored + ("/" if isdir(world, real) else "")
                           for real, stored in members)
        note = c("tar: Removing leading `/' from member names", "dim") if any(
            s_raw.startswith(("/", "~")) for s_raw in srcs) else ""
        if note:
            io.print(note)
        return ok(shown if "v" in flags else "")

    archive = abspath(world, names[0]) if names else None
    if not archive or not isfile(world, archive):
        # With -z the decompressor is a CHILD process, so the complaint comes
        # from it first and tar reports the child's status afterwards.
        who = "tar (child)" if "z" in flags else "tar"
        msg = (f"{who}: {names[0] if names else '?'}: Cannot open: No such file or directory\n"
               f"{who}: Error is not recoverable: exiting now")
        if "z" in flags:
            msg += "\ntar: Child returned status 2\ntar: Error is not recoverable: exiting now"
        return fail(msg, 2)
    body = world.files[archive]
    if archive.endswith(".gz") and "z" not in flags:
        io.print(c("(GNU tar sniffed the gzip magic and decompressed anyway — `-z` is the "
                   "portable spelling, and the one to build the habit on)", "dim"))
    try:
        entries = json.loads(body)
    except ValueError:
        return fail(f"tar: {names[0]}: This does not look like a tar archive\n"
                    "tar: Exiting with failure status due to previous errors", 2)

    def _perm(mode, d):
        out = "d" if d else "-"
        for shift in (6, 3, 0):
            bits = (mode >> shift) & 7
            out += ("r" if bits & 4 else "-") + ("w" if bits & 2 else "-") + \
                   ("x" if bits & 1 else "-")
        return out

    if "t" in flags:
        world.flags["listed_archive"] = True
        if "v" in flags:
            return ok("\n".join(
                f"{_perm(e['mode'], e['dir'])} {USER}/{USER} "
                f"{0 if e['dir'] else len(e['body']):>9} "
                f"2026-08-15 14:32 {e['name']}{'/' if e['dir'] else ''}"
                for e in entries))
        return ok("\n".join(e["name"] + ("/" if e["dir"] else "") for e in entries))

    for e in entries:                                     # x
        dest = abspath(world, e["name"])
        if e["dir"]:
            mkdir_one(world, dest, parents=True)
        else:
            mkdir_one(world, dest.rsplit("/", 1)[0], parents=True)
            world.files[dest] = e["body"]
        f["modes"][dest] = e["mode"]
    world.flags["extracted_archive"] = True
    return ok("\n".join(e["name"] for e in entries) if "v" in flags else "")


class _Sink:
    """Collects a script's output so it can be piped or redirected like any
    other command's, instead of escaping straight to the screen."""

    def __init__(self, io):
        self.lines, self._io = [], io

    def print(self, *args):
        self.lines.append(" ".join(str(a) for a in args))

    def input(self, prompt=""):
        return self._io.input(prompt)

    def write(self, text):
        self._io.write(text)          # `clear` inside a script still clears


def run_script(world, io, path, explicit=False, shown=None, script_args=()):
    """Run a shell script: its lines, through this same shell."""
    shown = shown or path
    if not isfile(world, path):
        return fail(f"bash: {shown}: No such file or directory", 127)
    if not explicit and not (mode_of(world, path) & 0o111):
        world.flags["_noop"] = True
        io.print(f"bash: {shown}: Permission denied")
        io.print(c("   (the file exists — it just isn't executable yet. `chmod +x` adds the x bit; "
                   "`bash <file>` runs it without one)", "dim"))
        return ok()
    # A script is not a special dialect — it is these same lines, run in order.
    # Running them for real is what makes `exit 3`, `$1` and a pipeline inside a
    # script behave the way the assignment expects.
    f = st(world)
    saved = (f.get("script_args"), f.get("script_name"))
    f["script_args"], f["script_name"] = list(script_args), shown
    sink = _Sink(io)
    code = 0
    try:
        for raw in world.files[path].split("\n"):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            bye = re.fullmatch(r"exit(?:\s+(\d+))?", line)
            if bye:
                code = int(bye.group(1)) if bye.group(1) else f.get("last_code", 0)
                break
            code = run_line(world, sink, line)
    finally:
        f["script_args"], f["script_name"] = saved
        f["last_code"] = code
    world.flags["ran_script"] = path
    return Res("\n".join(sink.lines), "", code)


# ------------------------------------------------------------------ dispatch --
PATH_TAKING = {"ls", "cd", "mkdir", "rmdir", "touch", "cat", "cp", "mv", "rm", "chmod",
               "find", "head", "tail", "wc", "sort", "uniq", "gzip", "gunzip", "edit",
               "less", "more", "du", "cut", "tac"}


def run_cmd(world, io, argv, stdin=None, tty=True):
    """One command. Returns Res, or FALLTHROUGH if this shell doesn't own it."""
    f = st(world)
    prog, args = argv[0], argv[1:]

    # You are root in this world, so `sudo` is a no-op wrapper — but it must still
    # RUN the command, or half the class-1 muscle memory would silently do nothing.
    if prog == "sudo":
        args = [a for a in args if a not in ("-E", "-H", "-i", "-s", "--")]
        if not args:
            return fail("usage: sudo [-E] [-H] command [args]", 1)
        io.print(c("(you're already root here, so sudo changes nothing — running it anyway. "
                   "On your own box it's what stands between a typo and a broken system)", "dim"))
        return run_cmd(world, io, args, stdin=stdin, tty=tty)

    # `--help` is a flag on every one of these tools, and it must answer with the
    # page — a student who types `chmod --help` after "Try 'chmod --help'" and
    # gets the same refusal back learns that the shell is lying to them. GNU
    # prints to stdout and exits 0, so this pipes: `ls --help | grep -i recurs`.
    # bash 5's builtins do answer --help (`pwd --help`, `kill --help`), so the
    # page is the honest response — except for the three that famously don't:
    # `echo --help` prints "--help", `true`/`false` print nothing at all.
    if "--help" in args and linux_help.known(prog) and prog not in ("echo", "true", "false", ":"):
        world.flags["_noop"] = True
        return ok(linux_help.page(prog) + real_tool_note(prog))

    if prog in ("help", "man", "info"):
        topic = next((a for a in args if not a.startswith("-")), "")
        if not topic:
            if prog == "man":
                return fail("What manual page do you want?\nFor example, try 'man man'.")
            print_help_index(io)
            world.flags["_noop"] = True
            return ok()
        world.flags["_noop"] = True
        text = linux_help.page(topic)
        if text:
            if prog == "man":
                io.print(c("(a real `man` page is longer and opens in a pager — press q to "
                           "leave one. This is the short version.)", "dim"))
            return ok(text + real_tool_note(topic))
        near = difflib.get_close_matches(topic, linux_help.PAGES, n=1, cutoff=0.6)
        return fail(f"No manual entry for {topic}"
                    + (f"  (did you mean: {near[0]}?)" if near else "")
                    + "\nType `help` for everything this shell knows.")

    # "" resolves to the cwd, which would make `rm ""` or `mkdir ""` do something
    # surprising. Every real tool rejects it outright.
    if prog in PATH_TAKING and any(a == "" for a in args):
        if prog == "cd":
            return fail("bash: cd: null directory")
        verb = {"ls": "cannot access", "cat": "", "rm": "cannot remove",
                "mkdir": "cannot create directory", "rmdir": "failed to remove",
                "touch": "cannot touch", "chmod": "cannot access"}.get(prog, "cannot access")
        return fail(f"{prog}: {verb + ' ' if verb else ''}'': No such file or directory")

    if prog == "pwd":
        return ok(f["cwd"])

    if prog == "cd":
        if args and args[0] == "-":
            prev = f.get("oldpwd")
            if not prev:
                return fail("bash: cd: OLDPWD not set")
            f["oldpwd"], f["cwd"] = f["cwd"], prev
            return ok(f["cwd"])
        raw = args[0] if args else "~"
        target = abspath(world, raw)
        if isfile(world, target):
            return fail(f"bash: cd: {raw}: Not a directory")
        if not isdir(world, target):
            hint = enoent_hint(world, raw, whole=True)
            return fail(f"bash: cd: {raw}: No such file or directory"
                        + ("\n" + hint if hint else ""))
        # x on a directory means "may enter" — without it, cd is denied even for
        # a directory you own. `chmod 600 dir` locking you out IS the lesson.
        if not searchable(world, target):
            return fail(f"bash: cd: {raw}: Permission denied")
        f["oldpwd"], f["cwd"] = f["cwd"], target
        return ok()

    if prog == "ls":
        return _ls(world, args, f, tty)

    if prog == "mkdir":
        parents = any(a.startswith("-") and "p" in a for a in args)
        names = [a for a in args if not a.startswith("-")]
        if not names:
            return fail("mkdir: missing operand\n"
                        "Try 'mkdir --help' for more information.")
        errs = [e for e in (mkdir_one(world, abspath(world, t), parents, t) for t in names) if e]
        return Res("", "\n".join(errs), 1 if errs else 0)

    if prog == "rmdir":
        names = [a for a in args if not a.startswith("-")]
        if not names:
            return fail("rmdir: missing operand\n"
                        "Try 'rmdir --help' for more information.")
        errs = []
        for t in names:
            p = abspath(world, t)
            if not isdir(world, p):
                errs.append(f"rmdir: failed to remove '{t}': "
                            + ("Not a directory" if isfile(world, p) else "No such file or directory"))
            elif children(world, p):
                errs.append(f"rmdir: failed to remove '{t}': Directory not empty")
            else:
                f["dirs"].discard(p)
        if errs:
            errs.append(c("(rmdir only removes EMPTY directories — `rm -r` is the one that "
                          "clears a tree, and it does not ask twice)", "dim"))
        return Res("", "\n".join(errs), 1 if errs else 0)

    if prog == "touch":
        names = [a for a in args if not a.startswith("-")]
        if not names:
            return fail("touch: missing file operand\n"
                        "Try 'touch --help' for more information.")
        errs = []
        for t in names:
            p = abspath(world, t)
            if isdir(world, p):
                continue
            if not isfile(world, p):
                e = write_file(world, p, "")
                if e:
                    reason = ("Permission denied" if "Permission denied" in e
                              else "No such file or directory")
                    errs.append(f"touch: cannot touch '{t}': {reason}")
                    hint = enoent_hint(world, t)
                    if hint and reason.startswith("No such"):
                        errs.append(hint)
        return Res("", "\n".join(errs), 1 if errs else 0)

    if prog == "echo":
        # bash's builtin: leading -n/-e/-E (and combos like -ne) are flags, and
        # flag parsing stops at the first word that isn't one.
        interp, nonl, i = False, False, 0
        while i < len(args) and re.fullmatch(r"-[neE]+", args[i]):
            if "n" in args[i]:
                nonl = True
            if "e" in args[i]:
                interp = True
            if "E" in args[i]:
                interp = False
            i += 1
        text = " ".join(args[i:])
        text = _unescape(text) if interp else text
        # `echo -n` is how you write a file with no trailing newline.
        return Res(out=text, nonl=nonl)

    if prog == "printf":
        if not args:
            return fail("printf: usage: printf [-v var] format [arguments]", 2)
        return stream(_printf(args[0], args[1:]))

    if prog == "seq":
        nums = [a for a in args if not a.startswith("-") or _num(a)]
        if not nums or not all(_num(x) for x in nums) or len(nums) > 3:
            return fail(f"seq: invalid floating point argument: '{(nums or args or [''])[0]}'\n"
                        "Try 'seq --help' for more information.")
        vals = [float(x) for x in nums]
        lo, step, hi = (1.0, 1.0, vals[0]) if len(vals) == 1 else \
            (vals[0], 1.0, vals[1]) if len(vals) == 2 else (vals[0], vals[1], vals[2])
        if step == 0:
            return fail("seq: invalid Zero increment value: '0'")
        out, cur = [], lo
        while (cur <= hi + 1e-9) if step > 0 else (cur >= hi - 1e-9):
            out.append(str(int(cur)) if cur == int(cur) else f"{cur:g}")
            cur += step
        return ok("\n".join(out))

    if prog == "cut":
        delim, fields, chars = "\t", None, None
        rest = []
        i = 0
        while i < len(args):
            a = args[i]
            if a.startswith("-d"):
                delim = a[2:] or (args[i + 1] if i + 1 < len(args) else "\t")
                i += 1 if a[2:] else 2
                continue
            if a.startswith("-f") or a.startswith("-c"):
                spec = a[2:] or (args[i + 1] if i + 1 < len(args) else "")
                if a[1] == "f":
                    fields = spec
                else:
                    chars = spec
                i += 1 if a[2:] else 2
                continue
            rest.append(a)
            i += 1
        if fields is None and chars is None:
            return fail("cut: you must specify a list of bytes, characters, or fields\n"
                        "Try 'cut --help' for more information.")
        spec = fields if fields is not None else chars
        try:
            wanted = _index_list(spec)
        except ValueError:
            kind = "field" if fields is not None else "byte/character"
            return fail(f"cut: invalid {kind} value '{spec}'\n"
                        "Try 'cut --help' for more information.", 1)
        body, err = _read_input(world, rest, stdin, "cut")
        if err:
            # cut names the file plainly, unlike head/tail's longer phrasing.
            return fail(re.sub(r"cut: cannot open '(.+)' for reading: ", r"cut: \1: ", err))
        lines = body.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        out = []
        for line in lines:
            if chars is not None:
                out.append("".join(line[i - 1] for i in wanted if i <= len(line)))
            elif delim not in line:
                out.append(line)          # a line with no delimiter passes through
            else:
                parts = line.split(delim)
                out.append(delim.join(parts[i - 1] for i in wanted if i <= len(parts)))
        return ok("\n".join(out))

    if prog == "tac":
        body, err = _read_input(world, args, stdin, "tac")
        if err:
            return fail(err)
        lines = body.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        return ok("\n".join(reversed(lines)))

    if prog in ("basename", "dirname"):
        if not args:
            return fail(f"{prog}: missing operand\nTry '{prog} --help' for more information.")
        p = args[0].rstrip("/") or "/"
        if prog == "basename":
            name = p.rsplit("/", 1)[-1] or "/"
            if len(args) > 1 and name.endswith(args[1]) and name != args[1]:
                name = name[:-len(args[1])]      # basename path .ext
            return ok(name)
        return ok(p.rsplit("/", 1)[0] or "/" if "/" in p else ".")

    if prog == "cat":
        names = [a for a in args if not a.startswith("-")]
        if not names:
            if stdin is not None:
                return stream(stdin)
            world.flags["_noop"] = True
            io.print(c("(cat with no file reads from the keyboard — in this shell, give it a "
                       "filename: cat notes.txt)", "dim"))
            return ok()
        # cat concatenates the raw bytes: whatever trailing newline each file
        # has (or doesn't) is exactly what comes out.
        blob, errs = "", []
        for t in names:
            p = abspath(world, t)
            if isfile(world, p):
                if not readable(world, p):
                    errs.append(f"cat: {t}: Permission denied")
                elif p.endswith((".tar", ".tar.gz", ".tgz", ".gz")):
                    blob += c(f"(binary archive — {len(world.files[p])} bytes. "
                              f"Look inside with: tar -tvf {t})", "dim") + "\n"
                else:
                    blob += world.files[p]
            elif isdir(world, p):
                errs.append(f"cat: {t}: Is a directory")
            else:
                errs.append(f"cat: {t}: No such file or directory")
        res = stream(blob)
        res.err, res.code = "\n".join(errs), 1 if errs else 0
        return res

    if prog in ("less", "more"):
        names = [a for a in args if not a.startswith("-")]
        if not names and stdin is None:
            return fail(f"{prog}: needs a file")
        world.flags["_noop"] = True
        io.print(c(f"({prog} pages a file one screen at a time — this shell has no pager, so "
                   "here's the whole thing. On a real box: q quits, / searches)", "dim"))
        if names:
            p = abspath(world, names[0])
            return ok(world.files[p]) if isfile(world, p) else fail(f"{prog}: {names[0]}: No such file or directory")
        return ok(stdin)

    if prog in ("cp", "mv"):
        return _cp_mv(world, prog, args, f)

    if prog == "rm":
        flags = "".join(a[1:] for a in args if a.startswith("-"))
        rec, force = ("r" in flags.lower()), ("f" in flags)
        names = [a for a in args if not a.startswith("-")]
        if not names:
            return fail("rm: missing operand\n"
                        "Try 'rm --help' for more information.")
        errs = []
        for t in names:
            p = abspath(world, t)
            if p == "/" and rec:
                io.print(c("   (this refusal is real, and it is the most important thing rm "
                           "does. --no-preserve-root removes it — never type that.)", "dim"))
                return fail("rm: it is dangerous to operate recursively on '/'\n"
                            "rm: use --no-preserve-root to override this failsafe")
            if isdir(world, p):
                if not rec:
                    # -d removes an EMPTY directory, like rmdir; without it, rm
                    # refuses directories outright.
                    if "d" in flags:
                        if len(walk(world, p)) > 1:
                            errs.append(f"rm: cannot remove '{t}': Directory not empty")
                        else:
                            f["dirs"].discard(p)
                            f["modes"].pop(p, None)
                        continue
                    errs.append(f"rm: cannot remove '{t}': Is a directory")
                    continue
                for q in walk(world, p):
                    world.files.pop(q, None)
                    f["dirs"].discard(q)
                    f["modes"].pop(q, None)
            elif isfile(world, p):
                world.files.pop(p, None)
                f["modes"].pop(p, None)
            elif not force:
                errs.append(f"rm: cannot remove '{t}': No such file or directory")
        return Res("", "\n".join(errs), 1 if errs else 0)

    if prog == "chmod":
        return _chmod(world, args, f)

    if prog == "chown":
        world.flags["_noop"] = True
        io.print(c("(this world has exactly one user — root — so there's nobody to give a file "
                   "TO. On a real box: sudo chown user:group <file>; `ls -l` shows the two "
                   "columns it changes)", "dim"))
        return ok()

    if prog == "find":
        return _find(world, args)

    if prog == "grep":
        return _grep(world, args, stdin)

    if prog == "wc":
        flags = "".join(a[1:] for a in args if a.startswith("-"))
        named = [a for a in args if not a.startswith("-")]

        def counts(text):
            ls = text.split("\n")
            return (len(ls) - (1 if ls and ls[-1] == "" else 0),
                    len(text.split()), len(text))

        def render(nl, nw, nc, label):
            tail = f" {label}" if label else ""
            if flags == "l":
                return f"{nl}{tail}"
            if flags == "w":
                return f"{nw}{tail}"
            if flags == "c":
                return f"{nc}{tail}"
            return f"{nl:>7} {nw:>7} {nc:>7}{tail}"

        if not named:
            if stdin is None:
                return fail("wc: needs a file argument or piped input here")
            return ok(render(*counts(stdin), ""))
        rows, errs, tot = [], [], [0, 0, 0]
        for name in named:
            p = abspath(world, name)
            if isdir(world, p):
                errs.append(f"wc: {name}: Is a directory")
                rows.append(render(0, 0, 0, name))
                continue
            if not isfile(world, p):
                errs.append(f"wc: {name}: No such file or directory")
                continue
            if not readable(world, p):
                errs.append(f"wc: {name}: Permission denied")
                continue
            nl, nw, nc = counts(world.files[p])
            tot = [tot[0] + nl, tot[1] + nw, tot[2] + nc]
            rows.append(render(nl, nw, nc, name))
        if len(named) > 1:
            rows.append(render(tot[0], tot[1], tot[2], "total"))
        return Res("\n".join(rows), "\n".join(errs), 1 if errs else 0)

    if prog in ("head", "tail"):
        n = 10
        for i, a in enumerate(args):
            if a == "-n":
                if i + 1 >= len(args):
                    return fail(f"{prog}: option requires an argument -- 'n'\n"
                                f"Try '{prog} --help' for more information.")
                raw = args[i + 1]
                if not raw.lstrip("+-").isdigit():
                    return fail(f"{prog}: invalid number of lines: '{raw}'")
                n = int(raw.lstrip("+-"))
            elif re.fullmatch(r"-\d+", a):
                n = int(a[1:])
        named = [a for a in args if not a.startswith("-") and not re.fullmatch(r"\d+", a)]

        def slice_of(text):
            lines = text.split("\n")
            if lines and lines[-1] == "":
                lines.pop()                   # a trailing newline terminates, not adds
            if n == 0:
                return ""                     # `tail -n 0` means zero lines, not all
            return "\n".join(lines[:n] if prog == "head" else lines[-n:])

        if not named:
            if stdin is None:
                return fail(f"{prog}: needs a file argument or piped input here")
            return ok(slice_of(stdin))
        blocks, errs = [], []
        for i, name in enumerate(named):
            p = abspath(world, name)
            if isdir(world, p):
                errs.append(f"{prog}: error reading '{name}': Is a directory")
                continue
            if not isfile(world, p):
                errs.append(f"{prog}: cannot open '{name}' for reading: "
                            "No such file or directory")
                continue
            if not readable(world, p):
                errs.append(f"{prog}: cannot open '{name}' for reading: Permission denied")
                continue
            if len(named) > 1:
                blocks.append(("\n" if i else "") + f"==> {name} <==")
            blocks.append(slice_of(world.files[p]))
        return Res("\n".join(blocks), "\n".join(errs), 1 if errs else 0)

    if prog == "sort":
        body, err = _read_input(world, args, stdin, "sort")
        if err:
            return fail(err)
        lines = body.split("\n")
        if lines and lines[-1] == "":
            lines.pop()                      # the trailing newline is not a line
        flags = "".join(a[1:] for a in args if a.startswith("-"))
        # -n compares the leading number; lines that aren't numbers all tie at 0
        # and then fall back to the same dictionary order as a plain sort.
        keyed = sorted(lines, key=lambda s: (float(s.split()[0]) if s.split()
                                             and _num(s.split()[0]) else 0, collate(s))) \
            if "n" in flags else sorted(lines, key=collate)
        if "r" in flags:
            keyed = list(reversed(keyed))
        if "u" in flags:
            seen, dedup = set(), []
            for x in keyed:
                if x not in seen:
                    seen.add(x)
                    dedup.append(x)
            keyed = dedup
        return ok("\n".join(keyed))

    if prog == "uniq":
        body, err = _read_input(world, args, stdin, "uniq")
        if err:
            return fail(err)
        count = any(a.startswith("-") and "c" in a for a in args)
        lines = body.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        out, prev, n = [], None, 0
        for line in lines:
            if prev is not None and line == prev:
                n += 1
                continue
            if prev is not None:
                out.append(f"{n:>7} {prev}" if count else prev)
            prev, n = line, 1
        if prev is not None:
            out.append(f"{n:>7} {prev}" if count else prev)
        return ok("\n".join(out))

    if prog == "date":
        now = datetime.now()
        fmt = next((a[1:] for a in args if a.startswith("+")), None)
        if fmt:
            try:
                return ok(now.strftime(fmt))
            except ValueError:
                return fail(f"date: invalid date format '{fmt}'")
        return ok(now.strftime("%a %b %d %H:%M:%S UTC %Y"))

    if prog == "sleep":
        if not args:
            return fail("sleep: missing operand")
        secs = args[0]
        if world.flags.pop("_bg_next", False):
            pid = f["next_pid"]
            f["next_pid"] += 1
            f["procs"][pid] = f"sleep {secs}"
            io.print(f"[{len(f['procs'])}] {pid}")
            return ok()
        # Real bash BLOCKS here. Pretending otherwise would teach the wrong lesson
        # about what `&` is for — so say what happened instead of faking a job.
        world.flags["_noop"] = True
        io.print(c(f"(…{secs}s later. `sleep` with no `&` BLOCKS the shell — you got no prompt "
                   "back until it finished, and Ctrl+C was your only way out.", "dim"))
        io.print(c(f"   To keep working — and to have a process you can find and kill — "
                   f"background it: sleep {secs} &", "dim"))
        return ok()

    if prog == "jobs":
        if not f["procs"]:
            return ok()
        return ok("\n".join(f"[{i}]+  Running                 {cmd} &"
                            for i, (_pid, cmd) in enumerate(sorted(f["procs"].items()), 1)))

    if prog in ("fg", "bg"):
        if not f["procs"]:
            return fail(f"bash: {prog}: current: no such job")
        pid, cmd = sorted(f["procs"].items())[0]
        world.flags["_noop"] = True
        io.print(cmd)
        io.print(c(f"({prog} would hand the terminal back to PID {pid}. In this shell it stays "
                   f"backgrounded — use `kill {pid}` to end it)", "dim"))
        return ok()

    if prog == "ps":
        world.flags["saw_ps"] = True
        wide = any(a in ("aux", "-ef", "-e", "auxww") or
                   (a.startswith("-") and ("e" in a or "a" in a)) for a in args)
        if wide:
            rows = ["USER         PID %CPU %MEM    VSZ   RSS TTY      STAT START   TIME COMMAND",
                    f"{USER:<8} 1042  0.0  0.1  10132  5120 pts/0    Ss   14:20   0:00 -bash"]
            for pid, cmd in sorted(f["procs"].items()):
                rows.append(f"{USER:<8} {pid}  0.0  0.0   8320   952 pts/0    S    14:31   0:00 {cmd}")
        else:
            rows = ["    PID TTY          TIME CMD", "   1042 pts/0    00:00:00 bash"]
            for pid, cmd in sorted(f["procs"].items()):
                rows.append(f"   {pid} pts/0    00:00:00 {cmd.split()[0]}")
        return ok("\n".join(rows))

    if prog in ("pgrep", "pidof"):
        pat = args[-1] if args else ""
        hits = [str(p) for p, cmd in sorted(f["procs"].items()) if pat in cmd]
        return ok("\n".join(hits)) if hits else Res(code=1)

    if prog == "kill":
        sig = "TERM"
        for a in args:
            if a.startswith("-"):
                sig = a[1:] or "TERM"
        targets = [a for a in args if not a.startswith("-")]
        if not targets:
            return fail("kill: usage: kill [-s sigspec | -n signum | -sigspec] pid | jobspec ...")
        errs = []
        for t in targets:
            if t.startswith("%"):
                jobs = sorted(f["procs"].items())
                idx = int(t[1:]) if t[1:].isdigit() else 1
                if 1 <= idx <= len(jobs):
                    pid = jobs[idx - 1][0]
                else:
                    errs.append(f"bash: kill: {t}: no such job")
                    continue
            elif t.isdigit():
                pid = int(t)
            else:
                errs.append(f"kill: {t}: arguments must be process or job IDs")
                continue
            if pid in f["procs"]:
                cmd = f["procs"].pop(pid)
                if "sleep" in cmd:
                    world.flags["killed_sleep"] = True
                io.print(f"[1]+  {'Killed' if sig in ('9', 'KILL', 'SIGKILL') else 'Terminated'}"
                         f"              {cmd}")
                if sig in ("9", "KILL", "SIGKILL"):
                    io.print(c("   (SIGKILL — the process could not catch it, so it cleaned up "
                               "nothing. Try plain `kill` first on anything that matters)", "dim"))
            elif pid == 1042:
                errs.append("bash: kill: (1042) - Operation not permitted")
            else:
                errs.append(f"kill: ({pid}) - No such process")
        return Res("", "\n".join(errs), 1 if errs else 0)

    if prog == "df":
        human = any(a.startswith("-") and "h" in a for a in args)
        return ok(DF_H if human else DF_RAW)

    if prog == "du":
        flags = "".join(a[1:] for a in args if a.startswith("-"))
        target = next((a for a in args if not a.startswith("-")), ".")
        p = abspath(world, target)
        if not exists(world, p):
            return fail(f"du: cannot access '{target}': No such file or directory")

        def kb(path):
            return sum(len(v) for k, v in world.files.items()
                       if k == path or k.startswith(path.rstrip("/") + "/")) // 1024 + 4

        if "s" in flags:
            return ok(f"{kb(p)}{'K' if 'h' in flags else ''}\t{target}")
        rows = [f"{kb(d)}{'K' if 'h' in flags else ''}\t"
                f"{target if d == p else target.rstrip('/') + d[len(p):]}"
                for d in walk(world, p) if isdir(world, d)]
        return ok("\n".join(rows or [f"{kb(p)}\t{target}"]))

    if prog == "ip":
        if args and args[0] in ("a", "addr", "address"):
            world.flags["saw_ip"] = True
            return ok(IP_A)
        if args and args[0] in ("r", "route"):
            return ok("default via 10.0.2.2 dev eth0 proto dhcp metric 100\n"
                      "10.0.2.0/24 dev eth0 proto kernel scope link src 10.0.2.15")
        return fail("Usage: ip [ OPTIONS ] OBJECT { COMMAND | help }\n"
                    "where  OBJECT := { address | route | link | ... }")

    if prog == "ifconfig":
        world.flags["_noop"] = True
        io.print("bash: ifconfig: command not found")
        io.print(c("   (net-tools isn't installed on modern Fedora — `ip a` replaced it. "
                   "`ip` is also the only one that shows everything a modern kernel does)", "dim"))
        return ok()

    if prog == "ping":
        # A flag's VALUE is not the hostname — skip it when picking the target.
        count, skip = None, set()
        for i, a in enumerate(args):
            if a == "-c" and i + 1 < len(args):
                count = args[i + 1]
                skip.add(i + 1)
            elif re.fullmatch(r"-c\d+", a):
                count = a[2:]
        host = next((a for i, a in enumerate(args)
                     if not a.startswith("-") and i not in skip), "google.com")
        if count is None:
            world.flags["_noop"] = True
            io.print(f"PING {host} (142.250.185.78) 56(84) bytes of data.")
            io.print("64 bytes from google.com: icmp_seq=1 ttl=117 time=12.4 ms")
            io.print("^C")
            io.print(c("   (no -c means ping runs FOREVER — you'd sit there until Ctrl+C. "
                       f"Bound it: ping -c 4 {host})", "dim"))
            return ok()
        try:
            n = max(1, int(count))
        except ValueError:
            return fail(f"ping: bad number of packets to transmit: '{count}'")
        rows = [f"PING {host} (142.250.185.78) 56(84) bytes of data."]
        times = [12.4, 11.9, 12.7, 12.1, 12.5, 11.8, 12.9, 12.2]
        for i in range(n):
            rows.append(f"64 bytes from {host}: icmp_seq={i + 1} ttl=117 "
                        f"time={times[i % len(times)]} ms")
        rows += ["", f"--- {host} ping statistics ---",
                 f"{n} packets transmitted, {n} received, 0% packet loss, "
                 f"time {n * 1001 + 2}ms"]
        return ok("\n".join(rows))

    if prog == "crontab":
        if any(a.startswith("-") and "l" in a for a in args):
            # An objective that says "read it back" has to be able to tell
            # whether the player did: installing a line is not seeing it.
            f["crontab_listed"] = True
            return ok("\n".join(f["cron"])) if f["cron"] else fail(f"no crontab for {USER}", 1)
        if any(a.startswith("-") and "r" in a for a in args):
            f["cron"] = []
            return ok()
        if any(a.startswith("-") and "e" in a for a in args):
            world.flags["_noop"] = True
            io.print(c("(crontab -e opens your editor on the live crontab — the safe way, since "
                       "it validates before installing. This shell has no editor for it: pipe a "
                       "line in instead, e.g.  echo '* * * * * date >> ~/t.log' | crontab -)", "dim"))
            return ok()
        if "-" in args:
            if stdin is None:
                return fail("crontab: no input read from stdin")
            f["cron"] = [ln for ln in stdin.split("\n") if ln.strip()]
            io.print(c("crontab: installing new crontab", "dim"))
            # If a timestamp is already baked into the line, the shell expanded it
            # at write time — cron will log that one frozen value forever. This is
            # the single-vs-double quote trap, caught the moment it happens.
            frozen = re.compile(r"\b\w{3} \w{3} +\d+ \d\d:\d\d:\d\d\b")
            for ln in f["cron"]:
                if frozen.search(ln):
                    io.print(c("   ⚠ look closely: a REAL timestamp is baked into that line.",
                               "yellow"))
                    io.print(c("     Your shell expanded $(date) when you pressed Enter, so cron "
                               "will append that one frozen moment every minute, forever.", "dim"))
                    io.print(c("     Single-quote it and cron does the expanding instead:",
                               "dim"))
                    io.print(c("       echo '* * * * * date >> ~/t.log' | crontab -", "green"))
                    break
            return ok()
        return fail("crontab: usage error: file name must be specified for replace\n"
                    "usage: crontab [-u user] file\n       crontab [-u user] [ -e | -l | -r ]")

    if prog == "tar":
        return _tar(world, io, args, f)

    if prog in ("gzip", "gunzip"):
        names = [a for a in args if not a.startswith("-")]
        decompress = prog == "gunzip" or any(a.startswith("-") and ("d" in a) for a in args)
        if not names:
            return fail(f"{prog}: needs a file")
        p = abspath(world, names[0])
        if decompress:
            src = p if p.endswith(".gz") else p + ".gz"
            if not isfile(world, src):
                return fail(f"{prog}: {names[0]}: No such file or directory")
            world.files[src[:-3]] = world.files.pop(src)
            return ok()
        if not isfile(world, p):
            return fail(f"gzip: {names[0]}: No such file or directory")
        if p.endswith(".gz"):
            return fail(f"gzip: {names[0]} already has .gz suffix -- unchanged")
        world.files[p + ".gz"] = world.files.pop(p)
        st(world)["modes"].pop(p, None)
        return ok()

    if prog in ("zip", "unzip"):
        world.flags["_noop"] = True
        io.print(c(f"({prog} works on a real box, but this world models the Unix pair instead: "
                   "`tar` bundles, `gzip` compresses. That's why archives are called .tar.gz)",
                   "dim"))
        return ok()

    if prog == "edit":
        if not args:
            return fail("edit: needs a filename  (edit notes.txt)")
        p = abspath(world, args[0])
        if isdir(world, p):
            return fail(f"edit: {args[0]}: Is a directory")
        io.print(c(f"— editing {args[0]}: type lines, then a single '.' on its own line to save —",
                   "dim"))
        if isfile(world, p) and world.files[p]:
            io.print(c("(current contents shown; anything you type REPLACES them)", "dim"))
            for ln in world.files[p].split("\n"):
                io.print(c("  | " + ln, "dim"))
        lines = []
        while True:
            try:
                ln = io.input("… ")
            except EOFError:
                io.print(c("(end of input — saving what you typed)", "dim"))
                break
            except KeyboardInterrupt:
                # Real `cat > file` aborts on Ctrl+C and hands the prompt back.
                # It must never take the whole session down with it.
                io.print(c("^C  (edit cancelled — nothing written)", "yellow"))
                world.flags["_noop"] = True
                return ok()
            if ln.strip() == ".":
                break
            lines.append(ln)
        err = write_file(world, p, "\n".join(lines))
        if err:
            return fail(err)
        io.print(c(f"saved {args[0]}", "dim"))
        return ok()

    if prog in ("true", ":"):
        return ok()

    if prog == "false":
        return Res(code=1)

    if prog == "whoami":
        return ok(USER)

    if prog == "id":
        return ok(f"uid=0({USER}) gid=0({USER}) groups=0({USER})")

    if prog == "groups":
        return ok(USER)

    if prog == "hostname":
        return ok(HOSTNAME)

    if prog == "uname":
        flags = "".join(a[1:] for a in args if a.startswith("-"))
        if not flags:
            return ok("Linux")
        if "a" in flags:
            return ok(f"Linux {HOSTNAME} {KERNEL} #1 SMP PREEMPT_DYNAMIC x86_64 "
                      "x86_64 x86_64 GNU/Linux")
        parts = []
        if "s" in flags:
            parts.append("Linux")
        if "n" in flags:
            parts.append(HOSTNAME)
        if "r" in flags:
            parts.append(KERNEL)
        if "m" in flags:
            parts.append("x86_64")
        return ok(" ".join(parts) if parts else "Linux")

    if prog == "history":
        return ok("\n".join(f"  {i}  {cmd}" for i, cmd in enumerate(world.history, 1)))

    if prog == "clear":
        # What the real `clear` sends, in this order: home the cursor, clear the
        # screen, clear the SCROLLBACK too (that last one is why a half-clear
        # leaves the old commands scrollable and the prompt in the wrong row).
        seq = "\033[H\033[2J\033[3J"
        if not tty:
            # `clear` is not magic, it is a program that writes bytes to stdout —
            # which is why `clear > f` fills a file with escapes and leaves the
            # screen alone. Modelling that is what stops it looking like magic.
            return stream(seq)
        # On a terminal it must go out RAW: printed as a normal line, the extra
        # newline is what pushes the prompt off row 1.
        io.write(seq)
        world.flags["_noop"] = True
        return ok()

    if prog in ("which", "command", "type", "whereis"):
        names = [a for a in args if not a.startswith("-")]
        if not names:
            return fail(f"usage: {prog} <command>")
        out, missing = [], False
        for t in names:
            if t in ON_PATH:
                out.append(f"{t} is /usr/bin/{t}" if prog == "type" else f"/usr/bin/{t}")
            else:
                missing = True
                # `command -v` and `type` say nothing on failure — that silence is
                # exactly why scripts test them instead of `which`.
                if prog == "which":
                    out.append(f"which: no {t} in (/usr/local/bin:/usr/bin:/bin)")
                elif prog == "type":
                    out.append(f"bash: type: {t}: not found")
        if not missing and prog == "which":
            io.print(c("(on PATH = installed. This is the check to run BEFORE any install step)",
                       "dim"))
        world.flags["_noop"] = True
        return Res("\n".join(x for x in out if x), code=1 if missing else 0)

    if prog == "tree":
        world.flags["_noop"] = True
        io.print("bash: tree: command not found")
        io.print(c("   (tree isn't installed by default on Fedora: sudo dnf install tree. "
                   "The always-there version is `ls -R`)", "dim"))
        return ok()

    if prog in ("export", "env", "set", "unset", "source", "alias"):
        world.flags["_noop"] = True
        io.print(c(f"(`{prog}` shapes your shell's ENVIRONMENT — real and worth learning, but "
                   "this world keeps no environment between commands. $HOME, $PWD, $USER and "
                   "$? do work here)", "dim"))
        return ok()

    if prog in TOOL_VERSIONS:
        world.flags["_noop"] = True
        if any(a in ("--version", "-v", "version", "-version", "--client") for a in args):
            io.print(TOOL_VERSIONS[prog])
            io.print(c("(it answered → it's installed. This is the check to run BEFORE any "
                       "install step — `setup` shows the install itself)", "dim"))
            return ok()
        io.print(f"🌍 `{prog}` IS on this host — but this is a 🐧 Linux mission, so it isn't "
                 f"wired up here. It's taught in {TOOL_HOME[prog]}.")
        io.print(c("   `task` shows what THIS mission needs · `quit` returns to the map", "dim"))
        return ok()

    if prog in ("bash", "sh") and args:
        return run_script(world, io, abspath(world, args[0]), explicit=True,
                          shown=args[0], script_args=args[1:])

    if prog.startswith(("./", "/", "~/")) or prog.startswith("../"):
        return run_script(world, io, abspath(world, prog), shown=prog, script_args=args)

    return FALLTHROUGH


def _num(s):
    try:
        float(s)
        return True
    except ValueError:
        return False


_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b",
            "f": "\f", "v": "\v", "0": "\0", "\\": "\\"}


def _unescape(text):
    """The backslash escapes `echo -e` and `printf` interpret."""
    out, i = "", 0
    while i < len(text):
        if text[i] == "\\" and i + 1 < len(text):
            nxt = text[i + 1]
            if nxt in _ESCAPES:
                out += _ESCAPES[nxt]
                i += 2
                continue
        out += text[i]
        i += 1
    return out


def _printf(fmt, args):
    """printf's real contract: escapes are always interpreted, and the format is
    REUSED until the arguments run out — which is why `printf '%s\\n' a b c`
    prints three lines, not one."""
    fields = re.findall(r"%[-+ #0]*\d*(?:\.\d+)?[sdifgxXoc%]", fmt)
    slots = [f for f in fields if f != "%%"]
    if not slots:                            # `printf 'done\n'` — one pass only
        return _unescape(fmt.replace("%%", "%"))
    out, i = "", 0
    args = list(args) or [""]
    while True:
        chunk, used = fmt, 0
        def sub(m, _slots=slots):
            nonlocal used
            spec = m.group(0)
            if spec == "%%":
                return "%"
            val = args[i + used] if i + used < len(args) else ""
            used += 1
            if spec[-1] in "difxXoc":
                try:
                    val = int(float(val or 0))
                except ValueError:
                    val = 0
                return spec % val
            return spec % str(val)
        chunk = re.sub(r"%[-+ #0]*\d*(?:\.\d+)?[sdifgxXoc%]", sub, chunk)
        out += _unescape(chunk)
        i += used or len(slots)
        if i >= len(args):
            break
    return out


def _index_list(spec):
    """cut's `1`, `1,3`, `2-4`, `3-` — 1-based, in ascending order like cut's."""
    want = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            raise ValueError(spec)
        if "-" in part:
            lo, _, hi = part.partition("-")
            lo = int(lo) if lo else 1
            hi = int(hi) if hi else 4096
            if lo < 1 or hi < lo:
                raise ValueError(spec)
            want.update(range(lo, hi + 1))
        else:
            n = int(part)
            if n < 1:
                raise ValueError(spec)
            want.add(n)
    return sorted(want)


def collate(s):
    """glibc's dictionary collation, which is what `sort` actually does on a
    UTF-8 desktop: punctuation is ignored first, then case breaks the tie. Plain
    codepoint order would put `Gamma` before `alpha` — students see the opposite.
    """
    primary = [ch.lower() for ch in s if ch.isalnum()]
    case = [0 if ch.islower() else 1 for ch in s if ch.isalpha()]
    return (primary, case, s)


# ------------------------------------------------------------------- shell --
def _exec_pipeline(world, io, stages, background, pipe_ops=()):
    """Run one pipeline (already split on | / |&). Returns the exit code."""
    piped, code = None, 0
    for i, words in enumerate(stages):
        # `a |& b` pipes stderr into b as well as stdout; plain `|` does not.
        merges_err = i < len(pipe_ops) and pipe_ops[i] == "|&"
        argv, redirs, syn = expand_argv(world, words, io)
        if syn:
            io.print(syn)
            return 2
        if not argv:
            continue
        stdin = piped
        for op, target in redirs:
            if op == "<":
                p = abspath(world, target)
                if not isfile(world, p):
                    io.print(f"bash: {target}: No such file or directory")
                    return 1
                if not readable(world, p):
                    io.print(f"bash: {target}: Permission denied")
                    return 1
                stdin = world.files[p]
        # 2>&1 / 1>&2 — duplicating a descriptor, not opening a file.
        dup_err_to_out = any(op == "2>&1" for op, _ in redirs)
        dup_out_to_err = any(op == "1>&2" for op, _ in redirs)
        if background and argv[0] == "sleep":
            world.flags["_bg_next"] = True
        is_last = i == len(stages) - 1
        to_tty = is_last and not any(op in (">", ">>") for op, _ in redirs)
        res = run_cmd(world, io, argv, stdin=stdin, tty=to_tty)
        if res is FALLTHROUGH:
            # bash reports the missing command and carries on: the rest of the
            # pipeline still runs, on an empty stream, and the pipeline's status
            # is the LAST stage's — `nosuchcmd | wc -l` prints 0 and exits 0.
            teach_unknown(world, io, argv[0])
            res = Res("", "", 127)
        code = res.code
        err_target = next((t for op, t in redirs if re.fullmatch(r"\d>>?", op)
                           and op[0] == "2"), None)
        out = res.out
        if dup_err_to_out and res.err:      # stderr joins stdout, in stream order
            out = (res.err + "\n" + out) if out else res.err
            res = Res(out, "", res.code)
        elif dup_out_to_err and out:
            res = Res("", (res.err + "\n" + out) if res.err else out, res.code)
            out = ""
        if res.err:
            if err_target:
                if err_target not in ("/dev/null", "/dev/zero"):
                    write_file(world, abspath(world, err_target), res.err + "\n")
            elif merges_err:
                out = (res.err + "\n" + out) if out else res.err
            else:
                io.print(res.err)
        wrote = False
        # A file records the stream verbatim: `echo a > f` is two bytes,
        # `printf a > f` is one. That difference is what `wc -c` measures.
        blob = out + ("" if res.nonl else "\n") if (out or res.nonl) else ""
        for op, target in redirs:
            if op in (">", ">>"):
                if target in ("/dev/null", "/dev/zero"):
                    wrote = True
                    continue
                err = write_file(world, abspath(world, target), blob, append=(op == ">>"))
                if err:
                    io.print(re.sub(r"bash: \S+:", f"bash: {target}:", err))
                    hint = enoent_hint(world, target) if "No such" in err else None
                    if hint:
                        io.print(hint)
                    return 1
                wrote = True
        if is_last:
            if out and not wrote:
                io.print(out)
            piped = None
        else:
            # A pipe carries the stream, terminator and all — which is why
            # `echo hi | wc -c` counts 3, not 2.
            piped = blob
    return code


def run_line(world, io, line):
    """One command line, start to finish. Returns its exit code.

    Typed at the prompt or read from a script — same code path, which is the
    only way a script can behave like the commands it is made of.
    """
    f = st(world)
    line = line.strip()
    if not line:
        return f.get("last_code", 0)

    words = tokenize(line)
    if words is None:
        io.print("bash: unexpected EOF while looking for matching quote")
        world.flags["_noop"] = True
        return 2

    syn = check_syntax(words)
    if syn:
        io.print(syn)
        world.flags["_noop"] = True
        return 2

    background = bool(words) and words[-1][0] == "&" and not words[-1][1]
    if background:
        words = words[:-1]
    # `sleep 5 & ls` is legal bash — & separates as well as backgrounds. This
    # shell only models the trailing form, so say so instead of guessing.
    if any(w == "&" and not qd for w, qd, _ in words):
        io.print(c("(this shell backgrounds with a TRAILING `&` only — `cmd &` on its own. "
                   "Real bash also lets `&` separate mid-line: `sleep 5 & ls`)", "dim"))
        world.flags["_noop"] = True
        return f.get("last_code", 0)

    chains, ops = split_on(words, {";", "&&", "||"})
    code = f.get("last_code", 0)
    for i, chain in enumerate(chains):
        if i:
            prev_op = ops[i - 1]
            if prev_op == "&&" and code != 0:
                continue
            if prev_op == "||" and code == 0:
                continue
        stages, pipe_ops = split_on(chain, {"|", "|&"})
        if not any(stages):
            continue
        code = _exec_pipeline(world, io, stages, background, pipe_ops)
        f["last_code"] = code
    return code


def shell(world, m, io):
    """Catch-all handler: the Linux missions' shell. One typed line in."""
    run_line(world, io, m.group(0).strip())


# The engine's atlas answers in docker's dialect ("top? — that's docker ps"), which
# is right in a container mission and wrong in this one. These come first here.
LINUX_LESSONS = {
    "top": ("🌍 `top` is a live, full-screen process viewer — this shell can't paint one.",
            "the non-interactive equivalent works here: ps aux  (add `| grep <name>` to filter)"),
    "htop": ("🌍 `htop` is `top` with colours — not installed by default on Fedora.",
             "sudo dnf install htop on a real box; here: ps aux"),
    "ssh": ("🌍 `ssh` opens a shell on ANOTHER machine — this world is a single host.",
            "it's how the Class 1 extra exercise reaches tty.sdf.org: ssh user@host"),
    "scp": ("🌍 `scp` copies files BETWEEN machines over ssh.",
            "scp file user@host:/path  — locally, `cp` is the one you want"),
    "curl": ("🌍 `curl` fetches a URL — there's no network service in this mission.",
             "the Docker missions publish a port and then curl it"),
    "wget": ("🌍 `wget` downloads a URL to a file.",
             "no network here; the Docker missions are where fetching happens"),
    "awk": ("🌍 `awk` is a whole text-processing language — deliberately not simulated.",
            "for class 1, grep + cut + sort cover the same ground; learn awk properly later"),
    "sed": ("🌍 `sed` edits streams with a mini-language — deliberately not simulated.",
            "here: `edit <file>` to change a file by hand, grep to filter"),
    "tr": ("🌍 `tr` translates or deletes characters — not simulated here.",
           "e.g. tr 'a-z' 'A-Z' upper-cases a stream"),
    "nano": ("🌍 `nano` is a real editor — this world ships a tiny one: edit <file>",
             "type your lines, then a single `.` on its own line to save"),
    "man": ("🌍 `man` — reading the manual — is exactly the right instinct.",
            "here: `help` lists what works · `learn` opens the study note · `hint` nudges"),
}


def teach_unknown(world, io, prog):
    """Unknown here → the engine's own real-world atlas, so the teaching voice
    stays identical to every other mission."""
    world.flags["_noop"] = True
    if prog in LINUX_LESSONS:
        head, follow = LINUX_LESSONS[prog]
        io.print(head)
        io.print(c("   " + follow, "dim"))
        return
    if in_real_world(prog):
        head, follow = real_world_entry(prog)
        io.print(head.format(cmd=prog))
        io.print(c("   " + follow, "dim"))
        return
    io.print(f"bash: {prog}: command not found")
    # `pipes`, `globs`, `permissions` … are pages about IDEAS, not programs.
    # Typing one is a fair thing to try, and answering "did you mean: pipes?" to
    # somebody who just typed `pipes` is the least useful sentence in the game.
    if prog in linux_help.PAGES and prog not in ON_PATH:
        io.print(c(f"   ({prog} is a shell concept, not a program — `help {prog}` "
                   "is the page about it)", "dim"))
        return
    # A one-character typo is the likeliest reason a real command isn't found,
    # and `pws` deserves "did you mean pwd?" rather than a reading assignment.
    # difflib alone ranks the SHORTER `ps` above `pwd` there, so same-length
    # candidates (one wrong key, not a missing one) come first.
    near = [m for m in difflib.get_close_matches(
        prog, sorted(ON_PATH | set(linux_help.PAGES)), n=3, cutoff=0.6) if m != prog]
    near.sort(key=lambda m: abs(len(m) - len(prog)))
    if near:
        # `pick` is engine.pick at module scope — shadow it only locally, and
        # only for the two words that go in the message.
        io.print(c(f"   (did you mean: {' or '.join(near[:2])}?  "
                   f"`{near[0]} --help` explains it)", "dim"))
    else:
        # Parenthesised like every other teaching aside in the game: the house
        # style, and what tells the bash differential test an addition from a
        # divergence.
        io.print(c("   (this mission is a Linux shell — `help` lists everything it "
                   "understands, `help <name>` explains one)", "dim"))


# Catch-all, but `quit`/`exit` must fall THROUGH to the engine: handlers dispatch
# first, so swallowing them would trap the player in the mission with no way out.
HANDLERS = [(r"(?!(?:quit|exit)\s*$).+", shell)]
