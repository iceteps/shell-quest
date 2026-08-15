"""Linux missions — the course's Class 1, and the one class the game never had.

The teacher's real graded sheet ("Home Assignments for Linux Class") is ten parts:
filesystem basics, permissions, find, grep, processes, disk usage, networking,
shell scripting, cron, and tar/gzip. These three missions mirror all ten so the
graded work is a re-run, not a first attempt.

Why this module carries its own shell: the engine's host world is deliberately ONE
flat folder with no cwd, no permissions and no processes (`cd` is even rebuffed as
unnecessary) — exactly right for the docker/k8s missions, and exactly wrong for
teaching Linux. Rather than reshape the world every other mission depends on, these
missions register a catch-all handler and run a small POSIX-ish shell of their own.
Handlers dispatch before the generic engine (engine.dispatch), so this is a clean
override; anything the little shell doesn't know falls through to the engine's own
🌍 real-world atlas, so unknown commands still teach instead of scolding.
"""
import re
import shlex

from engine import c, in_real_world, real_world_entry

HOME = "/root"


# ------------------------------------------------------------------- state --
def _st(world):
    """Lazily attach the Linux-ish state (cwd, dirs, modes, processes)."""
    f = world.flags
    if "cwd" not in f:
        f["cwd"] = HOME
        f["dirs"] = {"/", HOME}
        f["modes"] = {}
        f["procs"] = {}
        f["next_pid"] = 4821
        f["cron"] = []
        # seed files come in as plain names; move them under HOME
        for name in list(world.files):
            if not name.startswith("/"):
                world.files[f"{HOME}/{name}"] = world.files.pop(name)
    return f


def _abspath(world, p):
    f = _st(world)
    p = p.strip()
    if p.startswith("~"):
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


def _isdir(world, p):
    return p in _st(world)["dirs"]


def _isfile(world, p):
    return p in world.files


def _exists(world, p):
    return _isdir(world, p) or _isfile(world, p)


def _mode(world, p):
    f = _st(world)
    return f["modes"].get(p, 0o755 if _isdir(world, p) else 0o644)


def _mode_str(world, p):
    m = _mode(world, p)
    out = "d" if _isdir(world, p) else "-"
    for shift in (6, 3, 0):
        bits = (m >> shift) & 7
        out += ("r" if bits & 4 else "-") + ("w" if bits & 2 else "-") + ("x" if bits & 1 else "-")
    return out


def _children(world, d):
    """Immediate names inside directory d."""
    out = set()
    prefix = d.rstrip("/") + "/"
    for p in list(world.files) + list(_st(world)["dirs"]):
        if p.startswith(prefix) and p != d:
            out.add(p[len(prefix):].split("/")[0])
    return sorted(out)


def _mkdir(world, p, parents=False):
    f = _st(world)
    parent = p.rsplit("/", 1)[0] or "/"
    if not parents and not _isdir(world, parent):
        return f"mkdir: cannot create directory '{p}': No such file or directory"
    if _exists(world, p):
        return None if parents else f"mkdir: cannot create directory '{p}': File exists"
    if parents:
        acc = ""
        for seg in p.strip("/").split("/"):
            acc += "/" + seg
            f["dirs"].add(acc)
    else:
        f["dirs"].add(p)
    return None


def _write(world, p, content, append=False):
    parent = p.rsplit("/", 1)[0] or "/"
    if not _isdir(world, parent):
        return f"bash: {p}: No such file or directory"
    if append and p in world.files:
        world.files[p] = world.files[p] + ("\n" if world.files[p] else "") + content
    else:
        world.files[p] = content
    return None


# --------------------------------------------------------------- the shell --
_DF = ("Filesystem      Size  Used Avail Use% Mounted on\n"
       "/dev/nvme0n1p6  220G   16G  204G   8% /\n"
       "tmpfs           7.7G   18M  7.7G   1% /dev/shm\n"
       "/dev/nvme0n1p5  2.0G  843M  985M  47% /boot")

_IP_A = ("1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue state UNKNOWN\n"
         "    inet 127.0.0.1/8 scope host lo\n"
         "2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc fq_codel state UP\n"
         "    inet 10.0.2.15/24 brd 10.0.2.255 scope global dynamic eth0\n"
         "    inet6 fe80::a00:27ff:fe4e:66a1/64 scope link")

_PING = ("PING google.com (142.250.185.78) 56(84) bytes of data.\n"
         "64 bytes from google.com: icmp_seq=1 ttl=117 time=12.4 ms\n"
         "64 bytes from google.com: icmp_seq=2 ttl=117 time=11.9 ms\n"
         "64 bytes from google.com: icmp_seq=3 ttl=117 time=12.7 ms\n"
         "64 bytes from google.com: icmp_seq=4 ttl=117 time=12.1 ms\n"
         "\n--- google.com ping statistics ---\n"
         "4 packets transmitted, 4 received, 0% packet loss, time 3005ms")


# The DevOps tools are installed on this host (so `which docker` telling you so isn't a
# lie) — they're just taught in other missions. Version checks must still answer like the
# real thing: `setup` sends players here to run exactly these, and the engine's own
# prerequisite-realism rule says a check-first command always gets a real reply.
_TOOL_VERSIONS = {
    "docker": "Docker version 26.1.4, build 5650f9b",
    "podman": "podman version 5.1.1",
    "git": "git version 2.45.2",
    "kubectl": 'Client Version: v1.30.2\nKustomize Version: v5.0.4',
    "minikube": "minikube version: v1.33.1",
    "helm": 'version.BuildInfo{Version:"v3.15.2", GitTreeState:"clean", GoVersion:"go1.22.4"}',
    "terraform": "Terraform v1.9.0\non linux_amd64",
    "ansible": "ansible [core 2.17.1]",
    "python3": "Python 3.12.4",
}
_TOOL_HOME = {
    "docker": "the 🐳 Docker missions", "podman": "the 🐳 Docker missions",
    "git": "the 🌿 Git missions",
    "kubectl": "the ☸️ Kubernetes missions", "minikube": "the ☸️ Kubernetes missions",
    "helm": "the ⎈ Helm missions", "terraform": "the 🏗️ Terraform missions",
    "ansible": "the 📜 Ansible missions", "python3": "the 📨 RabbitMQ mission",
}

# What this mission's shell actually understands — the generic engine `help` lists
# docker/git/kubectl, which is wrong here.
HELP_LINES = [
    "   files: ls (-l -a) · cd · pwd · mkdir (-p) · touch · cat · cp · mv · rm (-r) · edit <file>",
    "   text:  echo (-e, > and >>) · grep · find -name · head · tail · wc -l · | pipes",
    "   perms: chmod (600 / 755 / +x) · ls -l to read the triads",
    "   procs: sleep N & · ps (aux) · kill <PID>",
    "   system: df (-h) · du (-sh) · ip a · ping -c N · uname · whoami · history · which",
    "   archives & jobs: tar (-cvf/-tvf) · gzip · crontab (-l, | crontab -) · ./script.sh",
]


def _split_redirect(argv):
    """Pull a trailing > file / >> file off an argv list."""
    for i, a in enumerate(argv):
        if a in (">", ">>"):
            return argv[:i], argv[i + 1] if i + 1 < len(argv) else None, a == ">>"
    return argv, None, False


def _emit(world, io, text, target, append):
    """Redirect to a file, or hand the text back to be printed once by the caller.

    It must NOT print here: _shell prints whatever _cmd returns, so printing as well
    would double every un-redirected line.
    """
    if target is None:
        return text
    return _write(world, _abspath(world, target), text, append)   # None, or an error string


def _unescape(s):
    return s.replace("\\n", "\n").replace("\\t", "\t")


def _expand(world, s):
    """The handful of variables a first-hour shell actually uses. Deliberately NOT
    doing $(command) — that the crontab lesson stays honest about."""
    f = _st(world)
    for var, val in (("$HOME", HOME), ("${HOME}", HOME), ("$PWD", f["cwd"]),
                     ("${PWD}", f["cwd"]), ("$USER", "root"), ("${USER}", "root")):
        s = s.replace(var, val)
    return s


def _cmd(world, io, argv, piped_in=None):
    """Run ONE command. Returns its stdout as text (already-printed commands
    return None). Keeping stdout as a value is what makes pipes work."""
    f = _st(world)
    prog, args = argv[0], argv[1:]
    args, redir, append = _split_redirect(args)

    if prog == "pwd":
        return f["cwd"]

    if prog == "cd":
        target = _abspath(world, args[0]) if args else HOME
        if not _isdir(world, target):
            return f"bash: cd: {target}: No such file or directory"
        f["cwd"] = target
        return None

    if prog == "ls":
        long = any(a.startswith("-") and "l" in a for a in args)
        show_all = any(a.startswith("-") and "a" in a for a in args)
        targets = [a for a in args if not a.startswith("-")] or [f["cwd"]]
        chunks = []
        for t in targets:
            p = _abspath(world, t)
            if _isfile(world, p):
                names, base = [p.rsplit("/", 1)[1]], p.rsplit("/", 1)[0]
            elif _isdir(world, p):
                names, base = _children(world, p), p
            else:
                chunks.append(f"ls: cannot access '{t}': No such file or directory")
                continue
            if not long:
                chunks.append("  ".join(names) if names else "")
                continue
            rows = []
            for n in names:
                full = base.rstrip("/") + "/" + n
                # "I checked the permissions" only counts once there's a deliberate
                # mode to see — a long listing of untouched files proves nothing.
                if full in f["modes"]:
                    world.flags["saw_perms"] = True
                size = len(world.files.get(full, "")) if _isfile(world, full) else 4096
                rows.append(f"{_mode_str(world, full)} 1 root root {size:>6} Aug 15 14:32 {n}")
            chunks.append("\n".join(rows))
        return "\n".join(x for x in chunks if x != "") if chunks else ""

    if prog == "mkdir":
        parents = any(a.startswith("-") and "p" in a for a in args)
        errs = [_mkdir(world, _abspath(world, t), parents)
                for t in args if not t.startswith("-")]
        return "\n".join(e for e in errs if e) or None

    if prog == "touch":
        for t in [a for a in args if not a.startswith("-")]:
            p = _abspath(world, t)
            if not _isfile(world, p):
                err = _write(world, p, "")
                if err:
                    return err
        return None

    if prog == "echo":
        interpret = bool(args) and args[0] == "-e"
        body = args[1:] if interpret else args
        text = " ".join(body)
        if interpret:
            text = _unescape(text)
        return _emit(world, io, text, redir, append)

    if prog == "cat":
        outs = []
        for t in [a for a in args if not a.startswith("-")]:
            p = _abspath(world, t)
            if _isfile(world, p):
                outs.append(world.files[p])
            elif _isdir(world, p):
                outs.append(f"cat: {t}: Is a directory")
            else:
                outs.append(f"cat: {t}: No such file or directory")
        return "\n".join(outs)

    if prog in ("cp", "mv"):
        names = [a for a in args if not a.startswith("-")]
        if len(names) < 2:
            return f"{prog}: missing destination file operand after '{names[0] if names else ''}'"
        src, dst = _abspath(world, names[0]), _abspath(world, names[1])
        if not _isfile(world, src):
            return f"{prog}: cannot stat '{names[0]}': No such file or directory"
        if _isdir(world, dst):
            dst = dst.rstrip("/") + "/" + src.rsplit("/", 1)[1]
        world.files[dst] = world.files[src]
        f["modes"][dst] = _mode(world, src)
        if prog == "mv":
            world.files.pop(src, None)
            f["modes"].pop(src, None)
        return None

    if prog == "rm":
        recursive = any(a.startswith("-") and ("r" in a or "R" in a) for a in args)
        for t in [a for a in args if not a.startswith("-")]:
            p = _abspath(world, t)
            if _isdir(world, p):
                if not recursive:
                    return f"rm: cannot remove '{t}': Is a directory"
                for k in [k for k in world.files if k.startswith(p + "/")]:
                    world.files.pop(k)
                for d in [d for d in list(f["dirs"]) if d == p or d.startswith(p + "/")]:
                    f["dirs"].discard(d)
            elif _isfile(world, p):
                world.files.pop(p)
            else:
                return f"rm: cannot remove '{t}': No such file or directory"
        return None

    if prog == "chmod":
        names = [a for a in args if not a.startswith("--")]
        if len(names) < 2:
            return "chmod: missing operand"
        spec, targets = names[0], names[1:]
        for t in targets:
            p = _abspath(world, t)
            if not _exists(world, p):
                return f"chmod: cannot access '{t}': No such file or directory"
            if re.fullmatch(r"[0-7]{3,4}", spec):
                f["modes"][p] = int(spec[-3:], 8)
            elif re.fullmatch(r"[ugoa]*\+[rwx]+", spec):
                add = 0
                for ch, bit in (("r", 0o444), ("w", 0o222), ("x", 0o111)):
                    if ch in spec:
                        add |= bit
                f["modes"][p] = _mode(world, p) | add
            elif re.fullmatch(r"[ugoa]*-[rwx]+", spec):
                drop = 0
                for ch, bit in (("r", 0o444), ("w", 0o222), ("x", 0o111)):
                    if ch in spec:
                        drop |= bit
                f["modes"][p] = _mode(world, p) & ~drop
            else:
                return f"chmod: invalid mode: '{spec}'"
        return None

    if prog == "find":
        start = _abspath(world, args[0]) if args and not args[0].startswith("-") else f["cwd"]
        pattern = None
        if "-name" in args:
            pattern = args[args.index("-name") + 1].strip("\"'")
        hits = []
        for p in sorted(list(world.files) + sorted(f["dirs"])):
            if p == start or p.startswith(start.rstrip("/") + "/"):
                name = p.rsplit("/", 1)[1] if "/" in p else p
                if pattern is None or _glob(pattern, name):
                    hits.append(p)
        if pattern and any(h.endswith(".txt") for h in hits):
            world.flags["found_txt"] = True
        return _emit(world, io, "\n".join(hits), redir, append)

    if prog == "grep":
        names = [a for a in args if not a.startswith("-")]
        if not names:
            return "usage: grep PATTERN [FILE]"
        pattern = names[0].strip("\"'")
        if piped_in is not None:
            body = piped_in
        elif len(names) > 1:
            p = _abspath(world, names[1])
            if not _isfile(world, p):
                return f"grep: {names[1]}: No such file or directory"
            body = world.files[p]
        else:
            return "usage: grep PATTERN FILE"
        matched = [ln for ln in body.split("\n") if pattern.lower() in ln.lower()]
        return _emit(world, io, "\n".join(matched), redir, append)

    if prog == "wc":
        body = piped_in if piped_in is not None else _read_arg(world, args)
        if body is None:
            return "wc: needs a file or piped input"
        n = len([x for x in body.split("\n") if x != ""])
        return str(n) if "-l" in args else f"{n} {len(body.split())} {len(body)}"

    if prog in ("head", "tail"):
        body = piped_in if piped_in is not None else _read_arg(world, args)
        if body is None:
            return f"{prog}: needs a file"
        n = 10
        for i, a in enumerate(args):
            if a == "-n" and i + 1 < len(args):
                n = int(args[i + 1])
            elif re.fullmatch(r"-\d+", a):
                n = int(a[1:])
        lines = body.split("\n")
        return "\n".join(lines[:n] if prog == "head" else lines[-n:])

    if prog == "sleep":
        pid = f["next_pid"]
        f["next_pid"] += 1
        f["procs"][pid] = "sleep " + (args[0] if args else "1")
        io.print(f"[1] {pid}")
        return None

    if prog == "ps":
        world.flags["saw_ps"] = True
        rows = ["    PID TTY          TIME CMD",
                "   1042 pts/0    00:00:00 bash"]
        for pid, cmd in sorted(f["procs"].items()):
            rows.append(f"  {pid} pts/0    00:00:00 {cmd.split()[0]}")
        if any(a.startswith("-") and ("a" in a or "e" in a) for a in args) or "aux" in args:
            rows = ["USER   PID %CPU %MEM    VSZ   RSS TTY   STAT START   TIME COMMAND",
                    "root  1042  0.0  0.1  10132  5120 pts/0 Ss   14:20   0:00 bash"]
            for pid, cmd in sorted(f["procs"].items()):
                rows.append(f"root  {pid}  0.0  0.0   8320   952 pts/0 S    14:31   0:00 {cmd}")
        return "\n".join(rows)

    if prog == "kill":
        targets = [a for a in args if not a.startswith("-")]
        if not targets:
            return "kill: usage: kill [-s sigspec] pid"
        for t in targets:
            if not t.isdigit():
                return f"kill: {t}: arguments must be process or job IDs"
            pid = int(t)
            if pid in f["procs"]:
                cmd = f["procs"].pop(pid)
                if "sleep" in cmd:
                    world.flags["killed_sleep"] = True
                io.print(f"[1]+  Terminated              {cmd}")
            else:
                return f"kill: ({t}) - No such process"
        return None

    if prog == "df":
        return _emit(world, io, _DF, redir, append)

    if prog == "du":
        target = next((a for a in args if not a.startswith("-")), f["cwd"])
        p = _abspath(world, target)
        total = sum(len(v) for k, v in world.files.items() if k.startswith(p)) // 1024 + 16
        out = f"{total}K\t{target}" if any("s" in a for a in args if a.startswith("-")) \
            else f"{total}K\t{target}"
        return _emit(world, io, out, redir, append)

    if prog == "ip":
        ok = bool(args) and args[0] in ("a", "addr", "address")
        if ok:
            world.flags["saw_ip"] = True
        out = _IP_A if ok else "Usage: ip [ OPTIONS ] OBJECT { COMMAND | help }"
        return _emit(world, io, out, redir, append)

    if prog == "ifconfig":
        io.print(c("bash: ifconfig: command not found", "yellow"))
        io.print(c("   (net-tools isn't installed on modern Fedora — `ip a` is the command "
                   "that replaced it)", "dim"))
        world.flags["_noop"] = True
        return None

    if prog == "ping":
        return _emit(world, io, _PING, redir, append)

    if prog == "crontab":
        if "-l" in args:
            return "\n".join(f["cron"]) if f["cron"] else "no crontab for root"
        if "-" in args:
            if piped_in:
                f["cron"] = [ln for ln in piped_in.split("\n") if ln.strip()]
                io.print(c("(crontab installed — cron will run it on schedule)", "dim"))
            return None
        return "crontab: usage: crontab [-l] [-r] [-e] [file]"

    if prog == "tar":
        flags = "".join(a.lstrip("-") for a in args if a.startswith("-"))
        names = [a for a in args if not a.startswith("-")]
        if "c" in flags:
            if len(names) < 2:
                return "tar: Cowardly refusing to create an empty archive"
            archive, src = _abspath(world, names[0]), _abspath(world, names[1])
            members = sorted(k for k in world.files if k.startswith(src))
            members += sorted(d for d in f["dirs"] if d.startswith(src))
            world.files[archive] = "\n".join(sorted(set(members)))
            world.flags["tar_of"] = src
            if "v" in flags:
                io.print("\n".join(m.lstrip("/") + ("/" if _isdir(world, m) else "")
                                   for m in sorted(set(members))))
            return None
        if "t" in flags:
            if not names:
                return "tar: no archive named"
            archive = _abspath(world, names[0])
            if not _isfile(world, archive):
                return f"tar: {names[0]}: Cannot open: No such file or directory"
            body = world.files[archive]
            world.flags["listed_archive"] = True
            if archive.endswith(".gz") and "z" not in flags:
                io.print(c("(GNU tar sniffed the gzip magic and decompressed anyway — "
                           "`-z` is the portable spelling)", "dim"))
            return "\n".join(f"-rw-r--r-- root/root  {len(ln)} 2026-08-15 14:32 {ln.lstrip('/')}"
                             for ln in body.split("\n") if ln)
        if "x" in flags:
            return None
        return "tar: You must specify one of the '-Acdtrux' options"

    if prog == "gzip":
        names = [a for a in args if not a.startswith("-")]
        if not names:
            return "gzip: needs a file"
        p = _abspath(world, names[0])
        if not _isfile(world, p):
            return f"gzip: {names[0]}: No such file or directory"
        world.files[p + ".gz"] = world.files.pop(p)
        return None

    if prog in ("bash", "sh") and args:
        return _run_script(world, io, _abspath(world, args[0]), explicit=True)

    if prog.startswith("./") or prog.startswith("/") or prog.startswith("~"):
        return _run_script(world, io, _abspath(world, prog))

    if prog == "whoami":
        return "root"

    if prog == "hostname":
        return "quest-host"

    if prog == "uname":
        return "Linux quest-host 6.8.0-quest x86_64 GNU/Linux" if args else "Linux"

    if prog == "history":
        return "\n".join(f"  {i}  {cmd}" for i, cmd in enumerate(world.history, 1))

    if prog == "clear":
        return "\033[2J\033[H"

    if prog in _TOOL_VERSIONS:
        world.flags["_noop"] = True          # pure inspection / teach-only, never a "move"
        asked_version = any(a in ("--version", "-v", "version", "-version") for a in args)
        if asked_version:
            io.print(_TOOL_VERSIONS[prog])
            io.print(c("(it answered → it's installed. This is the check to run BEFORE any "
                       "install step — `setup` shows the install itself)", "dim"))
            return None
        io.print(f"🌍 `{prog}` IS on this host — but this is a 🐧 Linux mission. "
                 f"It's taught in {_TOOL_HOME[prog]}.")
        io.print(c("   `task` shows what THIS mission needs · `quit` returns to the map", "dim"))
        return None

    if prog == "which":
        t = args[0] if args else ""
        known = {"bash", "ls", "cat", "grep", "find", "tar", "gzip", "ping", "ps", "kill",
                 "chmod", "cron", "crontab", "ip", "vim", "git", "docker", "python3"}
        if t in known:
            return f"/usr/bin/{t}"
        return f"which: no {t} in (/usr/local/bin:/usr/bin:/bin)"

    return _FALLTHROUGH


_FALLTHROUGH = object()


def _read_arg(world, args):
    names = [a for a in args if not a.startswith("-") and not a.isdigit()]
    if not names:
        return None
    p = _abspath(world, names[-1])
    return world.files.get(p)


def _glob(pattern, name):
    rx = "^" + re.escape(pattern).replace(r"\*", ".*").replace(r"\?", ".") + "$"
    return re.match(rx, name) is not None


def _run_script(world, io, path, explicit=False):
    if not _isfile(world, path):
        return f"bash: {path}: No such file or directory"
    if not explicit and not (_mode(world, path) & 0o111):
        io.print(f"bash: {path}: Permission denied")
        io.print(c("   (the file exists — it just isn't executable yet. chmod +x adds the x bit)", "dim"))
        world.flags["_noop"] = True
        return None
    body = world.files[path]
    out = []
    for line in body.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("echo "):
            out.append(_unescape(line[5:].strip().strip("\"'")))
    world.flags["ran_script"] = path
    return "\n".join(out)


def _shell(world, m, io):
    """Catch-all handler: the Linux missions' own little shell."""
    line = _expand(world, m.group(0).strip())
    _st(world)
    background = line.endswith("&")
    if background:
        line = line[:-1].strip()

    try:
        stages = [shlex.split(seg) for seg in _split_pipes(line)]
    except ValueError as e:
        io.print(f"bash: {e}")
        world.flags["_noop"] = True
        return
    stages = [s for s in stages if s]
    if not stages:
        return

    piped = None
    for i, argv in enumerate(stages):
        out = _cmd(world, io, argv, piped_in=piped)
        if out is _FALLTHROUGH:
            _teach_unknown(world, io, argv[0])
            return
        piped = out if isinstance(out, str) else None
        if i < len(stages) - 1 and piped is None:
            piped = ""
    if piped:
        io.print(piped)


def _split_pipes(line):
    """Split on | but not inside quotes."""
    out, buf, quote = [], "", None
    for ch in line:
        if quote:
            buf += ch
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            buf += ch
        elif ch == "|":
            out.append(buf)
            buf = ""
        else:
            buf += ch
    out.append(buf)
    return out


def _teach_unknown(world, io, prog):
    """Unknown here → hand back to the engine's real-world atlas so the teaching
    stays identical to every other mission."""
    world.flags["_noop"] = True
    if in_real_world(prog):
        head, follow = real_world_entry(prog)
        io.print(head.format(cmd=prog))
        io.print(c("   " + follow, "dim"))
        return
    io.print(f"bash: {prog}: command not found")
    io.print(c("   this mission is a Linux shell — `help` lists what it understands", "dim"))


# Catch-all, but `quit`/`exit` must fall THROUGH to the engine — handlers dispatch
# first, so swallowing them would trap the player in the mission with no way out.
_HANDLERS = [(r"(?!(?:quit|exit)\s*$).+", _shell)]


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
        "handlers": _HANDLERS,
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
        "handlers": _HANDLERS,
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
        "handlers": _HANDLERS,
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
