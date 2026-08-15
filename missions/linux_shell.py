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
  * `sleep` without `&` does not silently background (real bash blocks).

Structure: tokenize once (keeping quote state, because that decides globbing),
split on `;` `&&` `||`, then on `|`, then peel redirections off each stage.
Every builtin returns a Res(out, err, code) so pipes, redirection, exit codes
and `&&` all compose the way they do in bash.
"""
import json
import re
import shlex  # noqa: F401  (kept: mission solutions may still be shlex-shaped)
from datetime import datetime

from engine import c, in_real_world, real_world_entry

HOME = "/root"
USER = "root"
HOSTNAME = "quest-host"
KERNEL = "6.8.0-quest"


class Res:
    """One command's result: stdout, stderr, exit code — like a real process."""

    __slots__ = ("out", "err", "code")

    def __init__(self, out="", err="", code=0):
        self.out, self.err, self.code = out, err, code


def ok(out=""):
    return Res(out=out)


def fail(err, code=1):
    return Res(err=err, code=code)


FALLTHROUGH = object()          # "not my command" — hand back to the engine atlas


# ------------------------------------------------------------------- state --
def st(world):
    """Lazily attach the Linux-ish state (cwd, dirs, modes, processes)."""
    f = world.flags
    if "cwd" not in f:
        f["cwd"] = HOME
        f["dirs"] = {"/", "/root", "/tmp", "/etc", "/usr", "/usr/bin", "/var", "/var/log"}
        f["modes"] = {}
        f["procs"] = {}
        f["next_pid"] = 4821
        f["cron"] = []
        f["last_code"] = 0
        for name in list(world.files):          # seed files land in HOME
            if not name.startswith("/"):
                world.files[f"{HOME}/{name}"] = world.files.pop(name)
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


def mode_str(world, p):
    m = mode_of(world, p)
    out = "d" if isdir(world, p) else "-"
    for shift in (6, 3, 0):
        bits = (m >> shift) & 7
        out += ("r" if bits & 4 else "-") + ("w" if bits & 2 else "-") + ("x" if bits & 1 else "-")
    return out


def children(world, d):
    out = set()
    prefix = d.rstrip("/") + "/"
    for p in list(world.files) + list(st(world)["dirs"]):
        if p.startswith(prefix) and p != d:
            out.add(p[len(prefix):].split("/")[0])
    return sorted(out)


def walk(world, start):
    """Every path at or under start, directories included, sorted."""
    f = st(world)
    pre = start.rstrip("/") + "/"
    hits = [p for p in list(world.files) + list(f["dirs"])
            if p == start or p.startswith(pre)]
    return sorted(set(hits))


def mkdir_one(world, p, parents=False, shown=None):
    """`shown` is the path the user actually typed — real tools echo that back,
    not the resolved absolute path, and a student compares the two."""
    f = st(world)
    shown = shown or p
    parent = p.rsplit("/", 1)[0] or "/"
    if isfile(world, p):
        return f"mkdir: cannot create directory '{shown}': File exists"
    if not parents and not isdir(world, parent):
        return f"mkdir: cannot create directory '{shown}': No such file or directory"
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
    if append and p in world.files:
        prev = world.files[p]
        world.files[p] = prev + ("\n" if prev and not prev.endswith("\n") else "") + content
    else:
        world.files[p] = content
    return None


# ------------------------------------------------------------- tokenizing --
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
                    buf += nxt
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
        two = line[i:i + 2]
        if two in ("&&", "||", ">>"):
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
    for var, val in (("$HOME", HOME), ("${HOME}", HOME), ("$PWD", f["cwd"]),
                     ("${PWD}", f["cwd"]), ("$USER", USER), ("${USER}", USER),
                     ("$HOSTNAME", HOSTNAME), ("$?", str(f["last_code"]))):
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
    target = abspath(world, word)
    d, _, pat = target.rpartition("/")
    d = d or "/"
    if not isdir(world, d):
        return [word]
    try:
        rx = re.compile("^" + re.escape(pat).replace(r"\*", "[^/]*").replace(r"\?", "[^/]")
                        .replace(r"\[", "[").replace(r"\]", "]") + "$")
    except re.error:
        return [word]          # an unbalanced [ is a literal to bash, not an error
    hits = [n for n in children(world, d) if rx.match(n) and not n.startswith(".")]
    if not hits:
        return [word]
    keep_dir = "/" in word
    base = word.rsplit("/", 1)[0] if keep_dir else ""
    return [(base + "/" + h) if keep_dir else h for h in sorted(hits)]


def expand_argv(world, words, io=None):
    """(word, quoted, single) triples -> flat argv, plus the redirection plan."""
    argv, redirs = [], []
    i = 0
    while i < len(words):
        w, quoted, single = words[i]
        if not quoted and (w in (">", ">>", "<") or re.fullmatch(r"\d>>?", w)):
            if i + 1 < len(words):
                redirs.append((w, expand_vars(world, words[i + 1][0])))
                i += 2
                continue
            return argv, redirs, f"bash: syntax error near unexpected token `newline'"

        if single:
            argv.append(w)                       # single quotes: literal, full stop
        else:
            v = expand_vars(world, expand_subst(world, io, w))
            for b in ([v] if quoted else expand_braces(v)):
                argv.extend([b] if quoted else glob_word(world, b))
        i += 1
    return argv, redirs, None


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
PING4 = ("PING google.com (142.250.185.78) 56(84) bytes of data.\n"
         "64 bytes from google.com: icmp_seq=1 ttl=117 time=12.4 ms\n"
         "64 bytes from google.com: icmp_seq=2 ttl=117 time=11.9 ms\n"
         "64 bytes from google.com: icmp_seq=3 ttl=117 time=12.7 ms\n"
         "64 bytes from google.com: icmp_seq=4 ttl=117 time=12.1 ms\n"
         "\n--- google.com ping statistics ---\n"
         "4 packets transmitted, 4 received, 0% packet loss, time 3005ms\n"
         "rtt min/avg/max/mdev = 11.902/12.275/12.700/0.301 ms")

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
ON_PATH = {
    "bash", "sh", "ls", "cat", "cp", "mv", "rm", "rmdir", "mkdir", "touch", "chmod",
    "chown", "grep", "find", "head", "tail", "wc", "sort", "uniq", "cut", "tr",
    "echo", "printf", "pwd", "cd", "date", "df", "du", "ps", "kill", "sleep", "ip",
    "ping", "tar", "gzip", "gunzip", "zip", "unzip", "crontab", "whoami", "id",
    "groups", "hostname", "uname", "which", "less", "more", "man", "vi", "vim",
    "nano", "ssh", "scp", "curl", "wget", "dnf", "rpm", "systemctl",
} | set(TOOL_VERSIONS)

HELP_LINES = [
    "   files: ls (-l -a -R) · cd · pwd · mkdir (-p) · touch · cat · cp (-r) · mv · rm (-r) · rmdir",
    "   text:  echo (-e) · > >> < · grep (-i -v -n -c -r) · find (-name -type) · head · tail · wc · sort · uniq",
    "   perms: chmod (600 · u+x · -R) · ls -l to read the triads · chown",
    "   procs: sleep N & · jobs · ps (aux) · kill (-9) · date",
    "   system: df (-h) · du (-sh) · ip a · ping -c N · uname (-r -a) · whoami · id · history · which",
    "   archives: tar (-cvf -tvf -xvf -z) · gzip · gunzip",
    "   also: | pipes · ; && || chaining · file{1,2,3}.txt braces · *.txt globs · edit <file> · $HOME",
]


# ---------------------------------------------------------------- builtins --
def _ls(world, args, f, tty=True):
    long = any(a.startswith("-") and not a.startswith("--") and "l" in a for a in args)
    all_ = any(a.startswith("-") and not a.startswith("--") and "a" in a for a in args)
    rec = any(a.startswith("-") and not a.startswith("--") and "R" in a for a in args)
    targets = [a for a in args if not a.startswith("-")] or ["."]
    out, errs = [], []

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
                    world.flags["saw_perms"] = True
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
                    world.flags["saw_perms"] = True
                out.append(f"{mode_str(world, p)} 1 {USER} {USER} "
                           f"{len(world.files[p]):>6} Aug 15 14:32 {t}")
            else:
                out.append(t)  # a named file prints alone either way
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
    if len(srcs) > 1 and not isdir(world, dst):
        return fail(f"{prog}: target '{dst_raw}': Not a directory")
    errs = []
    for s_raw in srcs:
        src = abspath(world, s_raw)
        target = dst.rstrip("/") + "/" + src.rsplit("/", 1)[1] if isdir(world, dst) else dst
        if isdir(world, src):
            if prog == "cp" and not rec:
                errs.append(f"cp: -r not specified; omitting directory '{s_raw}'")
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
            err = write_file(world, target, world.files[src])
            if err:
                errs.append(err)
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
            return int(spec[-3:], 8)
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
        for p in walk(world, s):
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


def _grep(world, args, stdin):
    flags = "".join(a[1:] for a in args if a.startswith("-") and not a.startswith("--"))
    names = [a for a in args if not a.startswith("-")]
    if not names:
        return fail("usage: grep [OPTION]... PATTERN [FILE]...")
    pattern, files = names[0], names[1:]
    sources = []
    if files:
        for fn in files:
            p = abspath(world, fn)
            if isdir(world, p):
                if "r" in flags:
                    for q in walk(world, p):
                        if isfile(world, q):
                            sources.append((fn.rstrip("/") + q[len(p):], world.files[q]))
                else:
                    sources.append((fn, None))
            elif isfile(world, p):
                sources.append((fn, world.files[p]))
            else:
                sources.append((fn, None))
    elif stdin is not None:
        sources = [(None, stdin)]
    else:
        return fail("usage: grep [OPTION]... PATTERN [FILE]...")

    out, errs, hits = [], [], 0
    # real grep prefixes the filename whenever more than one file is in play —
    # and -r always is, even when it matches exactly one file.
    many = len(sources) > 1 or ("r" in flags and files)
    for label, body in sources:
        if body is None:
            errs.append(f"grep: {label}: No such file or directory")
            continue
        for i, line in enumerate(body.split("\n"), 1):
            # real grep is CASE-SENSITIVE unless -i. Getting this wrong would
            # teach a habit that fails the moment it matters.
            found = (pattern.lower() in line.lower()) if "i" in flags else (pattern in line)
            if found != ("v" in flags):
                hits += 1
                prefix = f"{label}:" if many else ""
                out.append(f"{prefix}{i}:{line}" if "n" in flags else prefix + line)
    if "c" in flags:
        return Res(str(hits), "\n".join(errs), 0 if hits else 1)
    return Res("\n".join(out), "\n".join(errs), 0 if hits else 1)


def _read_input(world, args, stdin, prog):
    """File argument, else piped stdin."""
    names = [a for a in args if not a.startswith("-") and not re.fullmatch(r"\d+", a)]
    if names:
        p = abspath(world, names[-1])
        if isdir(world, p):
            return None, f"{prog}: error reading '{names[-1]}': Is a directory"
        if not isfile(world, p):
            return None, f"{prog}: cannot open '{names[-1]}' for reading: No such file or directory"
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
        return fail("tar: Refusing to read archive contents from terminal (missing -f option?)")

    if "c" in flags:
        if len(names) < 2:
            return fail("tar: Cowardly refusing to create an empty archive")
        archive, srcs = abspath(world, names[0]), names[1:]
        members = []
        for s_raw in srcs:
            s = abspath(world, s_raw)
            if not exists(world, s):
                return fail(f"tar: {s_raw}: Cannot stat: No such file or directory\n"
                            "tar: Exiting with failure status due to previous errors")
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
        return fail(f"tar: {names[0] if names else '?'}: Cannot open: No such file or directory\n"
                    "tar: Error is not recoverable: exiting now")
    body = world.files[archive]
    if archive.endswith(".gz") and "z" not in flags:
        io.print(c("(GNU tar sniffed the gzip magic and decompressed anyway — `-z` is the "
                   "portable spelling, and the one to build the habit on)", "dim"))
    try:
        entries = json.loads(body)
    except ValueError:
        return fail(f"tar: {names[0]}: This does not look like a tar archive\n"
                    "tar: Exiting with failure status due to previous errors")

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


def run_script(world, io, path, explicit=False, shown=None):
    """Run a shell script: the shebang + echo lines a class-1 script contains."""
    shown = shown or path
    if not isfile(world, path):
        return fail(f"bash: {shown}: No such file or directory", 127)
    if not explicit and not (mode_of(world, path) & 0o111):
        world.flags["_noop"] = True
        io.print(f"bash: {shown}: Permission denied")
        io.print(c("   (the file exists — it just isn't executable yet. `chmod +x` adds the x bit; "
                   "`bash <file>` runs it without one)", "dim"))
        return ok()
    out = []
    for line in world.files[path].split("\n"):
        line = expand_vars(world, line.strip())
        if not line or line.startswith("#"):
            continue
        if line.startswith(("echo ", "printf ")):
            body = line.split(" ", 1)[1].strip()
            interp = body.startswith("-e ")
            if interp:
                body = body[3:].strip()
            text = body.strip("\"'")
            out.append(text.replace("\\n", "\n") if interp or line.startswith("printf") else text)
    world.flags["ran_script"] = path
    return ok("\n".join(out))


# ------------------------------------------------------------------ dispatch --
def run_cmd(world, io, argv, stdin=None, tty=True):
    """One command. Returns Res, or FALLTHROUGH if this shell doesn't own it."""
    f = st(world)
    prog, args = argv[0], argv[1:]

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
            return fail(f"bash: cd: {raw}: No such file or directory")
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
                    errs.append(f"touch: cannot touch '{t}': No such file or directory")
        return Res("", "\n".join(errs), 1 if errs else 0)

    if prog in ("echo", "printf"):
        interp = bool(args) and args[0] == "-e" or prog == "printf"
        body = args[1:] if (args and args[0] == "-e") else args
        text = " ".join(body)
        if interp:
            text = text.replace("\\n", "\n").replace("\\t", "\t")
        return ok(text)

    if prog == "cat":
        names = [a for a in args if not a.startswith("-")]
        if not names:
            if stdin is not None:
                return ok(stdin)
            world.flags["_noop"] = True
            io.print(c("(cat with no file reads from the keyboard — in this shell, give it a "
                       "filename: cat notes.txt)", "dim"))
            return ok()
        out, errs = [], []
        for t in names:
            p = abspath(world, t)
            if isfile(world, p):
                if p.endswith((".tar", ".tar.gz", ".tgz", ".gz")):
                    out.append(c(f"(binary archive — {len(world.files[p])} bytes. "
                                 f"Look inside with: tar -tvf {t})", "dim"))
                else:
                    out.append(world.files[p])
            elif isdir(world, p):
                errs.append(f"cat: {t}: Is a directory")
            else:
                errs.append(f"cat: {t}: No such file or directory")
        return Res("\n".join(out), "\n".join(errs), 1 if errs else 0)

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
            if isdir(world, p):
                if not rec:
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
        body, err = _read_input(world, args, stdin, "wc")
        if err:
            return fail(err)
        lines = body.split("\n")
        nl = len(lines) - (1 if lines and lines[-1] == "" else 0)
        flags = "".join(a[1:] for a in args if a.startswith("-"))
        named = [a for a in args if not a.startswith("-")]
        tail = f" {named[-1]}" if named else ""
        if flags == "l":
            return ok(f"{nl}{tail}")
        if flags == "w":
            return ok(f"{len(body.split())}{tail}")
        if flags == "c":
            return ok(f"{len(body)}{tail}")
        return ok(f"{nl:>7} {len(body.split()):>7} {len(body):>7}{tail}")

    if prog in ("head", "tail"):
        body, err = _read_input(world, args, stdin, prog)
        if err:
            return fail(err)
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
        lines = body.split("\n")
        if lines and lines[-1] == "":
            lines.pop()                       # a trailing newline is a terminator,
        return ok("\n".join(lines[:n] if prog == "head" else lines[-n:]))

    if prog == "sort":
        body, err = _read_input(world, args, stdin, "sort")
        if err:
            return fail(err)
        lines = [x for x in body.split("\n")]
        flags = "".join(a[1:] for a in args if a.startswith("-"))
        keyed = sorted(lines, key=lambda s: (float(s.split()[0]) if "n" in flags and s.split()
                                             and _num(s.split()[0]) else 0, s)) \
            if "n" in flags else sorted(lines)
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
        host = next((a for a in args if not a.startswith("-")), "google.com")
        count = None
        for i, a in enumerate(args):
            if a == "-c" and i + 1 < len(args):
                count = args[i + 1]
            elif re.fullmatch(r"-c\d+", a):
                count = a[2:]
        if count is None:
            world.flags["_noop"] = True
            io.print(f"PING {host} (142.250.185.78) 56(84) bytes of data.")
            io.print("64 bytes from google.com: icmp_seq=1 ttl=117 time=12.4 ms")
            io.print("^C")
            io.print(c("   (no -c means ping runs FOREVER — you'd sit there until Ctrl+C. "
                       f"Bound it: ping -c 4 {host})", "dim"))
            return ok()
        return ok(PING4 if count == "4" else PING4.replace("4 packets", f"{count} packets"))

    if prog == "crontab":
        if any(a.startswith("-") and "l" in a for a in args):
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
                break
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
        return ok("\033[2J\033[H")

    if prog in ("which", "command", "type", "whereis"):
        names = [a for a in args if not a.startswith("-")]
        if not names:
            return fail(f"usage: {prog} <command>")
        out, missing = [], False
        for t in names:
            if t in ON_PATH:
                out.append(f"/usr/bin/{t}" if prog != "type" else f"{t} is /usr/bin/{t}")
            else:
                missing = True
                out.append(f"{prog}: no {t} in (/usr/local/bin:/usr/bin:/bin)"
                           if prog == "which" else "")
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
        return run_script(world, io, abspath(world, args[0]), explicit=True)

    if prog.startswith(("./", "/", "~/")) or prog.startswith("../"):
        return run_script(world, io, abspath(world, prog), shown=prog)

    return FALLTHROUGH


def _num(s):
    try:
        float(s)
        return True
    except ValueError:
        return False


# ------------------------------------------------------------------- shell --
def _exec_pipeline(world, io, stages, background):
    """Run one pipeline (already split on |). Returns the exit code."""
    piped, code = None, 0
    for i, words in enumerate(stages):
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
                stdin = world.files[p]
        if background and argv[0] == "sleep":
            world.flags["_bg_next"] = True
        is_last = i == len(stages) - 1
        to_tty = is_last and not any(op in (">", ">>") for op, _ in redirs)
        res = run_cmd(world, io, argv, stdin=stdin, tty=to_tty)
        if res is FALLTHROUGH:
            teach_unknown(world, io, argv[0])
            return 127
        code = res.code
        err_target = next((t for op, t in redirs if re.fullmatch(r"\d>>?", op)
                           and op[0] == "2"), None)
        if res.err:
            if err_target:
                if err_target not in ("/dev/null", "/dev/zero"):
                    write_file(world, abspath(world, err_target), res.err)
            else:
                io.print(res.err)
        out = res.out
        wrote = False
        for op, target in redirs:
            if op in (">", ">>"):
                if target in ("/dev/null", "/dev/zero"):
                    wrote = True
                    continue
                err = write_file(world, abspath(world, target), out, append=(op == ">>"))
                if err:
                    io.print(re.sub(r"bash: \S+:", f"bash: {target}:", err))
                    return 1
                wrote = True
        if is_last:
            if out and not wrote:
                io.print(out)
            piped = None
        else:
            piped = out
    return code


def shell(world, m, io):
    """Catch-all handler: the Linux missions' shell. One typed line in."""
    f = st(world)
    line = m.group(0).strip()
    if not line:
        return

    words = tokenize(line)
    if words is None:
        io.print("bash: unexpected EOF while looking for matching quote")
        world.flags["_noop"] = True
        return

    background = bool(words) and words[-1][0] == "&" and not words[-1][1]
    if background:
        words = words[:-1]
    if any(w == "&" and not qd for w, qd, _ in words):
        io.print("bash: syntax error near unexpected token `&'")
        world.flags["_noop"] = True
        return

    chains, ops = split_on(words, {";", "&&", "||"})
    code = f.get("last_code", 0)
    for i, chain in enumerate(chains):
        if i:
            prev_op = ops[i - 1]
            if prev_op == "&&" and code != 0:
                continue
            if prev_op == "||" and code == 0:
                continue
        stages, _ = split_on(chain, {"|"})
        stages = [s for s in stages if s]
        if not stages:
            continue
        code = _exec_pipeline(world, io, stages, background)
        f["last_code"] = code


def teach_unknown(world, io, prog):
    """Unknown here → the engine's own real-world atlas, so the teaching voice
    stays identical to every other mission."""
    world.flags["_noop"] = True
    if in_real_world(prog):
        head, follow = real_world_entry(prog)
        io.print(head.format(cmd=prog))
        io.print(c("   " + follow, "dim"))
        return
    io.print(f"bash: {prog}: command not found")
    io.print(c("   this mission is a Linux shell — `help` lists everything it understands", "dim"))


# Catch-all, but `quit`/`exit` must fall THROUGH to the engine: handlers dispatch
# first, so swallowing them would trap the player in the mission with no way out.
HANDLERS = [(r"(?!(?:quit|exit)\s*$).+", shell)]
