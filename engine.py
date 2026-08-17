"""Shell Quest engine — a tiny simulated DevOps world.

Simulates just enough docker / git / shell for the missions to feel real:
state lives in a World object, commands are parsed and mutate it, and
missions win by checking that state (never by matching your exact keystrokes —
any correct route works).
"""
import base64
import difflib
import fnmatch
import json
import os
import random
import re
import shlex
import sys

# ---------------------------------------------------------------- terminal --
if os.name == "nt":
    os.system("")  # enable ANSI on Windows consoles
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdin.reconfigure(encoding="utf-8")
except Exception:
    pass

# Importing readline is what gives input() a real line editor: ← → to move,
# ↑ ↓ through history, Ctrl-A/E/W, Ctrl-R search. Without it the arrow keys
# arrive as raw escape bytes (^[[A) that the terminal echoes but Python keeps
# in the string — which is how a line you SEE as `private_data` gets submitted
# as `priivate_data`. It ships with CPython on Linux/macOS; on Windows there is
# no stdlib readline, so the console's own editing is the fallback.
try:
    import readline as _readline
except ImportError:                       # pragma: no cover — Windows
    _readline = None

COLORS = {
    "reset": "\033[0m", "bold": "\033[1m", "dim": "\033[2m",
    "green": "\033[92m", "red": "\033[91m", "yellow": "\033[93m",
    "cyan": "\033[96m", "magenta": "\033[95m", "blue": "\033[94m",
}


def c(text, color):
    return f"{COLORS[color]}{text}{COLORS['reset']}"


# --------------------------------------------------------------- player OS --
# The simulated host is always Linux-ish — that never changes. This is about the
# REAL machine the player is sitting at. Install steps, whether `sudo` exists,
# `which` vs `where`, whether WSL is a thing: all of it differs per OS, and
# teaching the wrong one is worse than teaching nothing.
OS_NAMES = {"linux": "Linux", "mac": "macOS", "windows": "Windows"}

# Linux family -> (package manager, install verb) for the distro we're on.
DISTRO_PKG = {
    "fedora": ("dnf", "sudo dnf install"),
    "debian": ("apt", "sudo apt install"),
    "arch": ("pacman", "sudo pacman -S"),
    "suse": ("zypper", "sudo zypper install"),
}


def detect_os():
    """Best guess at the player's real OS — the default before they choose."""
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "mac"
    return "linux"


def detect_distro():
    """Fedora-family vs Debian-family etc. Decides which package manager we teach."""
    try:
        with open("/etc/os-release", encoding="utf-8") as f:
            data = f.read().lower()
    except OSError:
        return None
    for key, family in (("fedora", "fedora"), ("rhel", "fedora"), ("centos", "fedora"),
                        ("rocky", "fedora"), ("alma", "fedora"),
                        ("ubuntu", "debian"), ("debian", "debian"), ("mint", "debian"),
                        ("arch", "arch"), ("manjaro", "arch"),
                        ("suse", "suse"), ("opensuse", "suse")):
        if key in data:
            return family
    return None


PLAYER_OS = detect_os()
PLAYER_DISTRO = detect_distro() if PLAYER_OS == "linux" else None


def set_player_os(os_name, distro=None):
    """Point the 🌍 teaching layer at the player's actual machine."""
    global PLAYER_OS, PLAYER_DISTRO
    if os_name in OS_NAMES:
        PLAYER_OS = os_name
    PLAYER_DISTRO = (distro or (detect_distro() if PLAYER_OS == "linux" else None))
    return PLAYER_OS


def os_label():
    if PLAYER_OS == "linux" and PLAYER_DISTRO:
        return f"Linux ({PLAYER_DISTRO}-family)"
    return OS_NAMES[PLAYER_OS]


def pkg_mgr():
    """The package manager to name when teaching an install on THIS machine."""
    if PLAYER_OS == "windows":
        return "winget", "winget install"
    if PLAYER_OS == "mac":
        return "brew", "brew install"
    return DISTRO_PKG.get(PLAYER_DISTRO, ("your package manager", "sudo <pkg-mgr> install"))


def pick(per_os):
    """Resolve an OS-keyed dict to this player's entry ('*' = fallback)."""
    return per_os.get(PLAYER_OS, per_os.get("*"))


def _suggest(word, options):
    """Typo helper: '(did you mean: X?)' or '' if nothing is close.

    The cutoff is deliberately high. At difflib's default 0.6 this offered
    `exec` for `expose` and `secrets` for `events` — real commands the player
    typed on purpose, answered with a suggestion that is simply wrong. A
    suggester that fires on words which merely share letters is worse than
    silence; 0.75 still catches the actual typos (`deploymnet`, `servcie`,
    `pdo`, `comit`)."""
    close = difflib.get_close_matches(word, list(options), n=1, cutoff=0.75)
    return c(f"  (did you mean: {close[0]}?)", "dim") if close else ""


_COLOR_SEQ = re.compile(r"(\033\[[0-9;]*m)")


def rl_prompt(text):
    """Fence the colour codes in a prompt so readline doesn't count them.

    readline measures the prompt to know where the line starts. Escape
    sequences are invisible but not zero-width to it, so an uncounted colour
    code makes every redraw — arrow keys, history, a long line wrapping — put
    the cursor in the wrong column. \\001…\\002 is how you say "this part
    prints nothing".

    The exception is a prompt carrying a non-ASCII glyph (⏎, ·, an emoji):
    fenced, readline measures the rest in BYTES, decides the cursor is in the
    wrong column and redraws the whole prompt — you see it twice. Those prompts
    take a single keypress and never need editing, so they trade the colour for
    a prompt that appears once.
    """
    if text.isascii():
        return _COLOR_SEQ.sub("\001\\1\002", text)
    return _COLOR_SEQ.sub("", text)


ESC_KEYS = {"[A": "↑", "[B": "↓", "[C": "→", "[D": "←", "[3~": "Delete",
            "[H": "Home", "[F": "End", "OA": "↑", "OB": "↓", "OC": "→", "OD": "←"}
ESC_SEQ = re.compile(r"\x1b(\[[0-9;]*[A-Za-z~]|O[A-Z])")


def edit_keys(line):
    """Apply the raw editing bytes a readline-less terminal leaves in the line.

    Backspace arrives as \\x7f and the arrows as escape sequences. The terminal
    DRAWS them (so the line looks right on screen) but hands Python the bytes,
    and a shell that tokenised them would report a typo the player never made.
    Returns (clean_line, keys_seen).
    """
    keys = [ESC_KEYS.get(m.group(1), "a key") for m in ESC_SEQ.finditer(line)]
    line = ESC_SEQ.sub("", line).replace("\x1b", "")
    out = []
    for ch in line:
        if ch in "\x7f\x08":
            if out:
                out.pop()
        else:
            out.append(ch)
    return "".join(out), keys


class IO:
    """Interactive by default; missions can be driven by a script (selftest)."""

    def __init__(self, script=None, echo_script=False):
        self.script = list(script) if script else None
        self.echo = echo_script

    def input(self, prompt=""):
        if self.script is not None:
            if not self.script:
                raise EOFError("script exhausted")
            line = self.script.pop(0)
            if self.echo:
                print(prompt + line)
            return line
        return input(rl_prompt(prompt) if _readline else prompt)

    def print(self, *args):
        print(*args)

    def write(self, text):
        """Raw bytes to the terminal — no newline, no colour, no buffering.
        `clear` is the whole reason this exists: printed as a line, its escape
        sequence lands one row below where the cursor should be."""
        if self.script is not None:
            return                        # a scripted run has no screen to clear
        sys.stdout.write(text)
        sys.stdout.flush()


# ------------------------------------------------------------------- world --
ADJ = ["brave", "sleepy", "witty", "cosmic", "mellow", "rusty"]
NOUN = ["panda", "otter", "falcon", "moose", "cactus", "walrus"]


def _rand_name():
    return f"{random.choice(ADJ)}_{random.choice(NOUN)}"


def _stable_id(seed):
    """A commit's hash must not change between `git commit` and `git log`.

    The polynomial loop alone is not enough. It folds each character into the
    LOW bits, so seeds that differ only near the end — `nginx:1.24` vs
    `nginx:1.25`, `…:0.3.0` vs `…:0.3.1` — agree on every high digit. Every
    caller slices from the FRONT (`[:7]` for a short sha, `[:9]` for a
    pod-template-hash), so without a finishing mix a tag bump produced two
    ReplicaSets with the *same name*, which real Kubernetes cannot do and which
    silently contradicted the rollback lesson. Avalanche it: one changed
    character must change every digit."""
    h = 0
    for ch in str(seed):
        h = (h * 131 + ord(ch)) & 0xFFFFFFFFFFFF
    h ^= h >> 23
    h = (h * 0x2127599BF4325C37) & 0xFFFFFFFFFFFF
    h ^= h >> 27
    return f"{h:012x}" + f"{h * 7 & 0xFFFFFFFF:08x}"


def _rand_id():
    return "".join(random.choice("0123456789abcdef") for _ in range(12))


class World:
    def __init__(self, spec=None):
        spec = spec or {}
        self.images = set(spec.get("images", []))
        self.networks = set(spec.get("networks", [])) | {"bridge"}
        self.containers = {}
        for cd in spec.get("containers", []):
            self.containers[cd["name"]] = {
                "id": _rand_id(), "image": cd["image"],
                "status": cd.get("status", "running"),
                "exit_code": cd.get("exit_code", 0),
                "logs": cd.get("logs", ""),
                "network": cd.get("network", "bridge"),
                "ports": cd.get("ports", []),
                "files": dict(cd.get("files", {})),
            }
        self.files = dict(spec.get("files", {}))       # host cwd files
        self.inside = None                              # container we're exec'd into
        # Mission scratch space — pre-seeded when a mission continues where the
        # last one stopped (the Linux missions hand each other a workspace).
        self.flags = dict(spec.get("flags", {}))
        self.history = []                               # commands the player typed
        g = spec.get("git")
        self.git = None
        if g is not None:
            self.git = {
                "branch": g.get("branch", "main"),
                "branches": set(g.get("branches", ["main"])),
                # {branch, msg, sha?, date?, files?, prev?} — `files`/`prev` are the
                # snapshot a commit took and the content it replaced, which is what
                # lets `show`, `restore`, `revert` and `reset --hard` be honest.
                "commits": list(g.get("commits", [])),
                "tracked": set(g.get("tracked", [])),
                "staged": set(), "modified": set(),
                "untracked": set(g.get("untracked", [])),
                "branch_files": {k: dict(v) for k, v in g.get("branch_files", {}).items()},
                "conflict": None, "merged": set(),
                "pushed": set(g.get("pushed", [])),
                # branch -> commit count at its last push; what "ahead by 2" counts
                "pushed_at": {b: len(g.get("commits", [])) for b in g.get("pushed", [])},
                # The working tree as of HEAD. Everything that "puts a file back"
                # reads from here, so a mission's starting files count as committed.
                "head_files": {f: self.files[f] for f in g.get("tracked", []) if f in self.files},
                "tags": dict(g.get("tags", {})), "pushed_tags": set(),
                "stash": [],
                "merge_backup": {},           # pre-merge content, for `git merge --abort`
                "rewritten": False,           # history rewritten since the last push?
                # Commits sitting on origin that this clone hasn't got — what makes
                # `fetch` and `pull` say something different from each other.
                "remote_new": list(g.get("remote_new", [])),
                "fetched": False,
            }
        k = spec.get("k8s")
        self.k8s = None
        if k is not None:
            self.k8s = {
                "started": k.get("started", False),
                "nodes": list(k.get("nodes", ["minikube"])),
                "namespaces": set(k.get("namespaces", [])) | {"default", "kube-system"},
                # deployments: name -> {ns, replicas, image, revision, history, app,
                # containerPort, probes, resources, strategy}. Missions build these
                # dicts by hand too (helm, argocd), so the defaults live in
                # _norm_deploy rather than here.
                "deployments": {n: dict(d) for n, d in k.get("deployments", {}).items()},
                # name -> {ns, deploy, status, image, restarts, labels, ip, ready}
                "pods": {},
                "services": {n: dict(s) for n, s in k.get("services", {}).items()},
                # objects: plain kinds -> {(name, ns), ...}
                "objects": {kind: set(map(tuple, v)) for kind, v in k.get("objects", {}).items()},
                "rbac": {"sa": set(), "roles": {}, "bindings": {}},  # bindings: name -> (role, sa, ns)
                # (kind, name, ns) -> the parsed manifest. The tables above hold what
                # the cluster DOES; this holds what the author WROTE — which is what
                # `describe` and `-o jsonpath` have to hand back, and the only way
                # `apply` can tell "unchanged" from "configured".
                "spec": {},
            }
            for dname, d in self.k8s["deployments"].items():
                _norm_deploy(dname, d)
            _reconcile(self)

    # -- helpers ------------------------------------------------------------
    def running(self):
        return {n: d for n, d in self.containers.items() if d["status"] == "running"}

    def norm_image(self, img):
        return img if ":" in img else img + ":latest"


# --------------------------------------------------------------- rendering --
def _ps_table(io, conts):
    io.print(f"{'CONTAINER ID':<14}{'IMAGE':<24}{'STATUS':<22}{'PORTS':<18}NAMES")
    for n, d in conts.items():
        status = "Up 2 minutes" if d["status"] == "running" else f"Exited ({d['exit_code']}) 2 minutes ago"
        ports = ", ".join(d["ports"])
        io.print(f"{d['id']:<14}{d['image']:<24}{status:<22}{ports:<18}{n}")


# ------------------------------------------------------------------ docker --
# `docker images` SIZE, in MB — real figures, and STABLE per image name. These
# used to be a random.randint per call, which quietly killed Class 02's whole
# slim-vs-full lesson: you cannot compare two numbers that move every time you
# look at them. Keyed (repository, variant) because a variant is its own image
# and not a fraction of one — nginx:alpine is 43MB, python:3-alpine is 52MB.
IMAGE_MB = {
    ("python", ""): 1020, ("python", "slim"): 150, ("python", "alpine"): 52,
    ("node", ""): 1100, ("node", "slim"): 245, ("node", "alpine"): 135,
    ("golang", ""): 838, ("golang", "alpine"): 251,
    ("openjdk", ""): 471, ("openjdk", "slim"): 214, ("openjdk", "alpine"): 326,
    ("ubuntu", ""): 78.1, ("debian", ""): 117, ("debian", "slim"): 74.8,
    ("alpine", ""): 7.8, ("busybox", ""): 4.26, ("hello-world", ""): 0.0133,
    ("nginx", ""): 192, ("nginx", "alpine"): 43.2,
    ("httpd", ""): 148, ("httpd", "alpine"): 61.5,
    ("redis", ""): 138, ("redis", "alpine"): 41.4,
    ("postgres", ""): 438, ("postgres", "alpine"): 247,
    ("mysql", ""): 586, ("mongo", ""): 795,
    ("rabbitmq", ""): 231, ("rabbitmq", "alpine"): 96.7,
    ("traefik", ""): 168, ("haproxy", ""): 104, ("memcached", ""): 87.4,
}

# Base images whose default command is a shell or a REPL: they exit the instant
# they start unless a terminal is kept on them. That is exactly why the class
# types `docker run -dit … bash` and not `docker run -d … bash`.
SHELL_IMAGES = {"ubuntu", "debian", "alpine", "busybox", "fedora", "centos",
                "rockylinux", "almalinux", "python", "node", "golang", "openjdk",
                "hello-world"}

# What a server image says on startup — `docker logs` has to have something true
# to show, and a foreground `docker run` is nothing but this text scrolling by.
STARTUP_LOGS = {
    "nginx": ("/docker-entrypoint.sh: Configuration complete; ready for start up\n"
              "2026/08/17 09:12:44 [notice] 1#1: using the \"epoll\" event method\n"
              "2026/08/17 09:12:44 [notice] 1#1: start worker processes"),
    "httpd": "AH00558: httpd: Could not reliably determine the server's fully qualified domain name",
    "redis": ("1:C 17 Aug 2026 09:12:44.482 * Redis version=7.2.5, bits=64, just started\n"
              "1:M 17 Aug 2026 09:12:44.483 * Ready to accept connections tcp"),
    "postgres": "database system is ready to accept connections",
    "rabbitmq": ("Starting RabbitMQ 3.13 on Erlang 26\n"
                 "started TCP listener on [::]:5672\n"
                 "Server startup complete; 4 plugins started."),
}

# Image-specific environment: the vars a student actually sees in `env`, and the
# proof that an image carries configuration of its own.
IMAGE_ENV = {
    "python": ["LANG=C.UTF-8", "PYTHON_VERSION=3.12.4", "PYTHON_PIP_VERSION=24.0"],
    "node": ["NODE_VERSION=20.15.0", "YARN_VERSION=1.22.22"],
    "nginx": ["NGINX_VERSION=1.27.0", "PKG_RELEASE=1"],
    "redis": ["REDIS_VERSION=7.2.5"],
}

NGINX_HTML = """<!DOCTYPE html>
<html>
<head>
<title>Welcome to nginx!</title>
</head>
<body>
<h1>Welcome to nginx!</h1>
<p>If you see this page, the nginx web server is successfully installed and
working. Further configuration is required.</p>
<p><em>Thank you for using nginx.</em></p>
</body>
</html>"""


def _img_parts(img):
    """(repository, variant) — the two things an image's size depends on."""
    repo, tag = img.rsplit(":", 1) if ":" in img else (img, "latest")
    repo = repo.rsplit("/", 1)[-1]          # a namespaced copy is the same image
    variant = next((v for v in ("alpine", "slim") if v in tag), "")
    return repo, variant


def _stable_frac(seed):
    """0.0–1.0, the same value every time for the same string."""
    return (int(_stable_id(seed)[:6], 16) % 1000) / 1000.0


def image_mb(world, img):
    """SIZE for `docker images`. Same image name → same number, always."""
    known = world.flags.get("_img_mb", {})
    if img in known:
        return known[img]
    repo, variant = _img_parts(img)
    if (repo, variant) in IMAGE_MB:
        return IMAGE_MB[(repo, variant)]
    base = IMAGE_MB.get((repo, ""), round(80 + _stable_frac(repo) * 320, 1))
    # An unknown variant still has to obey the lesson: slim/alpine are smaller.
    return round(base * {"slim": 0.18, "alpine": 0.07}.get(variant, 1.0), 1)


def _fmt_mb(mb):
    """Docker's own size formatting: GB / MB / kB, three significant digits."""
    if mb <= 0:
        return "0B"
    if mb >= 1000:
        return f"{mb / 1000:.3g}GB"
    if mb >= 1:
        return f"{mb:.3g}MB"
    return f"{mb * 1000:.3g}kB"


def image_id(world, img):
    """A short image ID that survives across commands — and across `docker tag`,
    because two tags on one image really are the same ID."""
    return world.flags.get("_img_id", {}).get(img, _stable_id("img:" + img)[:12])


def _long_digest(seed):
    """64 hex chars that look like a real sha256 — four differently-salted hashes,
    because repeating one four times is visibly periodic."""
    return "".join(_stable_id(f"{i}:{seed}")[:16] for i in range(4))


def _img_age(world, img):
    return "About a minute ago" if img in world.flags.get("_img_new", set()) else "3 weeks ago"


def _images_table(world, io, filt=None, quiet=False):
    rows = []
    for img in sorted(world.images):
        repo, tag = img.rsplit(":", 1)
        if filt and filt not in (repo, img, repo.rsplit("/", 1)[-1]):
            continue
        rows.append((repo, tag, image_id(world, img), _img_age(world, img), image_mb(world, img)))
    if not filt:
        # Dangling images (`<none>`) are the reason `docker image prune` exists —
        # they appear on their own, every time a rebuild moves a tag off one.
        rows += [("<none>", "<none>", d["id"], "About a minute ago", d["mb"])
                 for d in world.flags.get("_dangling", [])]
    if quiet:
        for r in rows:
            io.print(r[2])
        return
    io.print(f"{'REPOSITORY':<26}{'TAG':<14}{'IMAGE ID':<15}{'CREATED':<20}SIZE")
    for repo, tag, iid, age, mb in rows:
        io.print(f"{repo:<26}{tag:<14}{iid:<15}{age:<20}{_fmt_mb(mb)}")


# ---------------------------------------------------------- docker networks --
def _nets(ctr):
    """Every network a container is attached to. Compose services and containers
    born before `docker network connect` existed only ever have the one."""
    return list(ctr.get("networks") or [ctr.get("network", "bridge")])


def _net_id(name):
    return _stable_id("net:" + name)[:12]


def _net_subnet(name):
    """The default bridge is always 172.17; user-defined networks get their own
    /16 — derived from the name so it never moves between two inspects."""
    if name == "bridge":
        return "172.17"
    return f"172.{18 + int(_stable_id('net:' + name)[:4], 16) % 12}"


def _net_ip(world, net, cname):
    """Docker hands out .2, .3, .4 … in attach order. Stable is what matters:
    the IP `ping` prints must be the IP `docker network inspect` shows."""
    members = [n for n, d in world.containers.items() if net in _nets(d)]
    idx = members.index(cname) + 2 if cname in members else 2
    return f"{_net_subnet(net)}.0.{idx}"


def _busybox(image):
    """alpine-based images ship busybox, whose ping/wget wording differs from
    glibc's — and the note quotes busybox's, because the lab uses nginx:alpine.
    Getting `bad address 'nginx2'` character-for-character is the drill."""
    repo, variant = _img_parts(image)
    return variant == "alpine" or repo in ("alpine", "busybox")


def _parse_run_flags(args):
    """Returns (flags, bad_flag, cmd). A flag missing its value reports itself
    rather than walking off the end of the list."""
    f = {"d": False, "it": False, "rm": False, "name": None, "network": "bridge",
         "ports": [], "env": [], "volumes": []}
    pos, i = [], 0

    def need(flag):
        """The value after a flag — or None, which the caller reports like docker does."""
        return args[i + 1] if i + 1 < len(args) else None

    while i < len(args):
        a = args[i]
        if a == "--rm":
            f["rm"] = True
        elif a == "--name":
            v = need(a)
            if v is None:
                return None, ("MISSINGVAL", a), None
            i += 1; f["name"] = v
        elif a == "--network":
            v = need(a)
            if v is None:
                return None, ("MISSINGVAL", a), None
            i += 1; f["network"] = v
        elif a in ("-p", "--publish"):
            v = need(a)
            if v is None:
                return None, ("MISSINGVAL", a), None
            i += 1; f["ports"].append(v)
        elif a in ("-e", "--env"):
            v = need(a)
            if v is None:
                return None, ("MISSINGVAL", a), None
            i += 1; f["env"].append(v)
        elif a in ("-v", "--volume"):
            v = need(a)
            if v is None:
                return None, ("MISSINGVAL", a), None
            i += 1; f["volumes"].append(v)
        elif a in ("--detach",):
            f["d"] = True
        elif a.startswith("-") and not a.startswith("--") and set(a[1:]) <= set("dit") and a[1:]:
            if "d" in a: f["d"] = True
            if "i" in a or "t" in a: f["it"] = True
        elif a.startswith("-"):
            return None, a, None
        else:
            # Everything after the IMAGE is the container's command, flags and
            # all — `docker run alpine ls -la` passes -la to ls, not to docker.
            pos.extend(args[i:])
            break
        i += 1
    return f, None, pos


# ------------------------------------------------------------- docker --help --
# Both Docker classes headline a "Learn to fish / ask the tool first" section:
# `docker --help`, `docker run --help`, `man docker`. A world where --help is an
# unknown flag teaches the opposite reflex. These two tables are also the single
# source of truth for the top-level listing and the did-you-mean suggestions, so
# there is exactly one list that can go stale.
DOCKER_SUBS = {
    "run": "Create and run a new container from an image",
    "exec": "Execute a command in a running container",
    "ps": "List containers",
    "build": "Build an image from a Dockerfile",
    "pull": "Download an image from a registry",
    "push": "Upload an image to a registry",
    "images": "List images",
    "logs": "Fetch the logs of a container",
    "start": "Start one or more stopped containers",
    "stop": "Stop one or more running containers",
    "rm": "Remove one or more containers",
    "rmi": "Remove one or more images",
    "tag": "Create a tag TARGET_IMAGE that refers to SOURCE_IMAGE",
    "login": "Log in to a registry",
    "version": "Show the Docker version information",
}
DOCKER_MGMT = {
    "compose": "Docker Compose  (up · ps · logs · down)",
    "image": "Manage images  (ls · rm · prune)",
    "container": "Manage containers  (ls · rm · prune)",
    "network": "Manage networks  (create · ls · inspect · connect · rm)",
    "system": "Manage Docker  (prune)",
}
# Real docker commands this world does NOT simulate. Naming them honestly beats
# "not a docker command" — several are the Class 01 extra-credit drills, and a
# student who types one deserves to hear what it really does.
DOCKER_UNSIMULATED = {
    "volume": "named volumes: docker volume create mydata, then run with -v mydata:/root/keep — "
              "storage that outlives the container",
    "inspect": "the full JSON of a container: docker inspect -f '{{.NetworkSettings.IPAddress}}' <name>",
    "stats": "live CPU/memory per container — how you'd check a --memory limit is doing its job",
    "cp": "copies files between the host and a container: docker cp <name>:/path ./here",
    "attach": "reattaches your terminal to a container's MAIN process (exec starts a new one)",
    "restart": "stop + start in one step",
    "kill": "SIGKILL instead of stop's polite SIGTERM",
    "top": "the processes running inside a container",
    "save": "writes an image to a tar file (a registry is the usual way to move images)",
    "load": "reads an image back from a tar file",
    "history": "the layers an image is made of, with the instruction that created each",
    "diff": "what changed in a container's writable layer since it started",
    "wait": "blocks until a container exits, then prints its exit code",
    "info": "the daemon's own configuration and storage driver",
    "buildx": "the modern builder — docker build already uses it under the hood",
}
# usage · summary · the flags THIS world simulates · the sentence worth keeping
DOCKER_HELP = {
    "run": ("docker run [OPTIONS] IMAGE [COMMAND] [ARG...]",
            "Create and run a new container from an image",
            [("-d, --detach", "Run container in background and print container ID"),
             ("-i, --interactive", "Keep STDIN open even if not attached"),
             ("-t, --tty", "Allocate a pseudo-TTY   (-dit = all three: the class's default)"),
             ("--name string", "Assign a name to the container"),
             ("-p, --publish list", "Publish a container's port to the host  (HOST:CONTAINER)"),
             ("--network string", "Connect to a network — the one that resolves names"),
             ("-e, --env list", "Set environment variables inside the container"),
             ("-v, --volume list", "Mount a volume — storage that outlives the container"),
             ("--rm", "Automatically remove the container when it exits")],
            "`run` always makes a NEW container. To get back into one you already have, use `exec`."),
    "exec": ("docker exec [OPTIONS] CONTAINER COMMAND [ARG...]",
             "Execute a command in a RUNNING container",
             [("-i, --interactive", "Keep STDIN open"),
              ("-t, --tty", "Allocate a pseudo-TTY   (-it together = an interactive shell)")],
             "The container must already be running — `exec` never starts one."),
    "ps": ("docker ps [OPTIONS]",
           "List containers",
           [("-a, --all", "Show all containers (default shows just the running ones)")],
           "A crashed container is not gone, it is just not running — `-a` is where it went."),
    "build": ("docker build [OPTIONS] PATH | URL | -",
              "Build an image from a Dockerfile",
              [("-t, --tag name:tag", "Name and optionally tag the image you are building"),
               ("-f, --file string", "Name of the Dockerfile (default: PATH/Dockerfile)"),
               ("--no-cache", "Do not use cache when building the image")],
              "PATH is the build CONTEXT — the folder Docker may COPY from. That trailing `.` "
              "is not decoration; forgetting it is the #1 first-build error."),
    "images": ("docker images [OPTIONS] [REPOSITORY[:TAG]]",
               "List images",
               [("-q, --quiet", "Only show image IDs")],
               "The SIZE column is the slim-vs-full argument in one glance: python:3 ~1GB, "
               "python:3-slim ~150MB, alpine variants smaller still."),
    "pull": ("docker pull [OPTIONS] NAME[:TAG]",
             "Download an image from a registry",
             [], "No tag means `:latest` — a name, not a promise of freshness."),
    "push": ("docker push [OPTIONS] NAME[:TAG]",
             "Upload an image to a registry",
             [], "Push needs a namespaced name (<user>/<repo>) and a login — even for a public repo."),
    "logs": ("docker logs [OPTIONS] CONTAINER",
             "Fetch the logs of a container",
             [], "The output a container produced is kept after it dies. Read it before guessing."),
    "stop": ("docker stop [OPTIONS] CONTAINER [CONTAINER...]",
             "Stop one or more running containers",
             [], "`stop` keeps the writable layer (start resumes it); `rm` destroys it."),
    "start": ("docker start [OPTIONS] CONTAINER [CONTAINER...]",
              "Start one or more stopped containers", [], ""),
    "rm": ("docker rm [OPTIONS] CONTAINER [CONTAINER...]",
           "Remove one or more containers",
           [("-f, --force", "Force the removal of a running container")],
           "Removing a container destroys its writable layer — that is where your files were."),
    "rmi": ("docker rmi [OPTIONS] IMAGE [IMAGE...]",
            "Remove one or more images",
            [("-f, --force", "Force removal of the image")],
            "An image with containers made from it — even stopped ones — refuses to go."),
    "tag": ("docker tag SOURCE_IMAGE[:TAG] TARGET_IMAGE[:TAG]",
            "Create a tag TARGET_IMAGE that refers to SOURCE_IMAGE",
            [], "A tag is a second name for the SAME image — same ID, same size, no copy."),
    "login": ("docker login [OPTIONS] [SERVER]",
              "Log in to a registry",
              [], "Paste an access token, never your account password."),
    "version": ("docker version [OPTIONS]", "Show the Docker version information",
                [], "Client and Server are two different programs — that split is the whole story "
                    "on a Mac or Windows box, where the engine lives in a small Linux VM."),
    "network": ("docker network COMMAND",
                "Manage networks",
                [("create", "Create a network — the ONE that gives you name resolution"),
                 ("ls", "List networks"),
                 ("inspect", "Display detailed information: subnet, gateway, who is attached"),
                 ("connect / disconnect", "Attach or detach a running container"),
                 ("rm / prune", "Remove networks")],
                "Containers on a user-defined network find each other BY NAME. The default "
                "bridge does not do that — it is the whole reason you create your own."),
    "image": ("docker image COMMAND",
              "Manage images",
              [("ls", "List images  (same as `docker images`)"),
               ("rm", "Remove images  (same as `docker rmi`)"),
               ("prune", "Remove unused (dangling) images")],
              "`docker image ls` and `docker images` are the same command in two spellings."),
    "container": ("docker container COMMAND",
                  "Manage containers",
                  [("ls", "List containers  (same as `docker ps`)"),
                   ("rm", "Remove containers"), ("prune", "Remove all stopped containers")],
                  ""),
    "system": ("docker system COMMAND",
               "Manage Docker",
               [("prune", "Remove unused data: stopped containers, unused networks, dangling images"),
                ("prune -a", "…and every image no container is using")],
               "`prune` is the cleanup command; `-a` is the one that deletes the image you were "
               "about to run. Read the WARNING before you type y."),
    "compose": ("docker compose [OPTIONS] COMMAND",
                "Define and run multi-container applications",
                [("up -d", "Create and start everything in the compose file, detached"),
                 ("ps", "List the services"), ("logs <svc>", "Show one service's output"),
                 ("down", "Stop and remove everything it created")],
                "One file describes the whole app — the containers, their network and their order."),
}


def _docker_help(world, topic, io):
    """Asking the tool is free, so it never counts as a move."""
    world.flags["_noop"] = True
    if topic not in DOCKER_HELP:
        io.print("Usage:  docker [OPTIONS] COMMAND")
        io.print("")
        io.print("A self-sufficient runtime for containers")
        io.print("")
        io.print("Management Commands:")
        for k, v in DOCKER_MGMT.items():
            io.print(f"  {k:<12}{v}")
        io.print("")
        io.print("Commands:")
        for k, v in DOCKER_SUBS.items():
            io.print(f"  {k:<12}{v}")
        io.print("")
        io.print("Run 'docker COMMAND --help' for more information on a command.")
        io.print(c("(this is the 'ask the tool first' reflex both classes open with — every "
                   "subcommand takes --help, and it beats a search engine every time)", "dim"))
        return
    usage, summary, opts, note = DOCKER_HELP[topic]
    mgmt = topic in DOCKER_MGMT           # a management command lists commands, not flags
    io.print(f"Usage:  {usage}")
    io.print("")
    io.print(summary)
    if opts:
        io.print("")
        io.print("Commands:" if mgmt else "Options:")
        for flag, text in opts:
            io.print(f"  {flag:<22}{text}")
    if note:
        io.print("")
        io.print(c(note, "dim"))
    io.print(c(f"(these are the {'commands' if mgmt else 'flags'} this world simulates — real "
               f"docker has more: `docker {topic} --help` on your own machine prints the lot)",
               "dim"))


# `docker image ls` is `docker images` in docker's newer management-command
# spelling. Normalise once, so every branch below only ever sees the short form.
MGMT_ALIAS = {
    ("image", "ls"): "images", ("image", "list"): "images", ("image", "rm"): "rmi",
    ("image", "remove"): "rmi", ("image", "pull"): "pull", ("image", "push"): "push",
    ("image", "build"): "build", ("image", "tag"): "tag",
    ("container", "ls"): "ps", ("container", "list"): "ps", ("container", "rm"): "rm",
    ("container", "stop"): "stop", ("container", "start"): "start",
    ("container", "exec"): "exec", ("container", "logs"): "logs", ("container", "run"): "run",
}


def do_docker(world, args, io):
    if not args:
        _docker_help(world, "", io)
        return
    sub, rest = args[0], args[1:]

    if sub in ("--version", "-v") and not rest:
        world.flags["_noop"] = True
        io.print("Docker version 26.1.4, build 5650f9b")
        io.print(c("(the check-first habit — if this answers, Docker is installed; no reinstall needed)", "dim"))
        return
    if sub == "version" and not rest:
        world.flags["_noop"] = True
        server = "Docker Engine - Community" if PLAYER_OS == "linux" else "Docker Desktop"
        # The CLIENT runs on the player's machine; the ENGINE is always Linux.
        # That split is the whole lesson of `docker version` on a Mac/Windows box.
        client_arch = pick({"mac": "darwin/arm64", "windows": "windows/amd64",
                            "*": "linux/amd64"})
        io.print("Client:\n Version:      26.1.4\n API version:  1.45\n"
                 f" OS/Arch:      {client_arch}\n\n"
                 f"Server: {server}\n Engine:\n  Version:     26.1.4\n  OS/Arch:     linux/amd64")
        if client_arch != "linux/amd64":
            io.print(c("(client and engine differ — on your box Docker Desktop runs the engine "
                       "inside a small Linux VM. Containers are ALWAYS Linux)", "dim"))
        return

    # `docker image ls` → `docker images`, before anything else reads `sub`.
    if sub in DOCKER_MGMT and rest and (sub, rest[0]) in MGMT_ALIAS:
        sub, rest = MGMT_ALIAS[(sub, rest[0])], rest[1:]

    # --help is docker's, only up to the container/image name: after that the
    # flags belong to the command being run (`docker exec c1 wget -h`).
    scan = [sub]
    for a in rest:
        scan.append(a)
        if not a.startswith("-") and a not in DOCKER_HELP:
            break
    if sub == "help" or any(a in ("--help", "-h") for a in scan):
        _docker_help(world, next((a for a in scan if a in DOCKER_HELP), ""), io)
        return

    if sub == "compose":
        _do_compose(world, rest, io)
        return

    if sub == "pull":
        if not rest:
            io.print('"docker pull" requires exactly 1 argument.')
            io.print("See 'docker pull --help'.")
            world.flags["_noop"] = True
            return
        img = world.norm_image([a for a in rest if not a.startswith("-")][0])
        repo, tag = img.rsplit(":", 1)
        if _pull_denied(world, io, img):
            return
        if not image_exists(img):
            # Same verdict the kubelet reaches, one layer down: a tag that isn't
            # in the registry can't be pulled by anyone. In Kubernetes this exact
            # failure surfaces as ImagePullBackOff instead of an error you see.
            io.print(f"Error response from daemon: manifest for {img} not found: "
                     "manifest unknown: manifest unknown")
            io.print(c("(that tag doesn't exist. `docker search`/the registry's tag list is where "
                       "you check — and a Deployment asking for it parks its pods in "
                       "ImagePullBackOff rather than telling you this plainly.)", "dim"))
            world.flags["_noop"] = True
            return
        io.print(f"{tag}: Pulling from {repo if '/' in repo else 'library/' + repo}")
        if img in world.images:
            io.print(f"Digest: sha256:{_long_digest(img)}")
            io.print(f"Status: Image is up to date for {img}")
            io.print(c("(already cached — pull is idempotent: checking costs nothing, nothing re-downloads)", "dim"))
        else:
            io.print(f"{_rand_id()[:12]}: Pull complete")
            io.print(f"Digest: sha256:{_long_digest(img)}")
            io.print(f"Status: Downloaded newer image for {img}")
            world.images.add(img)
            world.flags.setdefault("_img_new", set()).add(img)
        io.print(f"docker.io/{repo if '/' in repo else 'library/' + repo}:{tag}")

    elif sub == "images":
        filt = next((a for a in rest if not a.startswith("-")), None)
        _images_table(world, io, filt=filt,
                      quiet=any(a in ("-q", "--quiet") for a in rest))

    elif sub == "run":
        _docker_run(world, rest, io)

    elif sub == "ps":
        show_all = "-a" in rest or "--all" in rest
        if show_all:
            world.flags["ps_a"] = True
            _ps_table(io, world.containers)
        else:
            running = world.running()
            # "I verified it's running" only means something when something IS
            # running — a bare header row is the definition of NOT verified.
            if running:
                world.flags["ps"] = True
            _ps_table(io, running)
            if not running:
                io.print(c("(nothing running — `docker ps -a` also shows stopped containers)", "dim"))

    elif sub == "logs":
        if not rest:
            io.print('"docker logs" requires exactly 1 argument.')
            io.print("See 'docker logs --help'.")
            world.flags["_noop"] = True
            return
        name = rest[-1]
        if name not in world.containers:
            io.print(f"Error response from daemon: No such container: {name}")
            world.flags["_noop"] = True
            return
        world.flags["logs_" + name] = True
        io.print(world.containers[name]["logs"] or "(no output)")

    elif sub == "exec":
        # Only docker's OWN flags come off the front — everything from the
        # container name on belongs to the command (`docker exec c ls -t`).
        while rest and rest[0] in ("-it", "-ti", "-i", "-t", "-d",
                                   "--interactive", "--tty", "--detach"):
            rest = rest[1:]
        if len(rest) < 2:
            io.print('"docker exec" requires at least 2 arguments.')
            io.print("See 'docker exec --help'.")
            world.flags["_noop"] = True
            return
        name, cmd = rest[0], rest[1:]
        if name not in world.containers:
            io.print(f"Error response from daemon: No such container: {name}")
            world.flags["_noop"] = True
            return
        if world.containers[name]["status"] != "running":
            io.print(f"Error response from daemon: container {name} is not running")
            io.print(c("(`exec` only reaches a RUNNING container — start it first: "
                       f"docker start {name})", "dim"))
            world.flags["_noop"] = True
            return
        if cmd[0].rsplit("/", 1)[-1] in ("bash", "sh", "ash", "zsh"):
            world.inside = name
            world.flags["exec_" + name] = True
            io.print(c(f"🐚 you are now INSIDE '{name}' — plain shell commands work here; type `exit` to leave", "dim"))
        else:
            run_inside(world, name, cmd, io)

    elif sub in ("stop", "start", "rm"):
        force = any(a in ("-f", "--force") for a in rest)
        names = [a for a in rest if not a.startswith("-")]
        if not names:
            io.print(f'"docker {sub}" requires at least 1 argument.')
            io.print(f"See 'docker {sub} --help'.")
            world.flags["_noop"] = True
            return
        for name in names:
            if name not in world.containers:
                io.print(f"Error response from daemon: No such container: {name}")
                continue
            ctr = world.containers[name]
            if sub == "stop":
                ctr["status"] = "exited"; ctr["exit_code"] = 0
                io.print(name)
                # --rm is not a runtime decoration: the container is gone the
                # moment it stops, which is why `docker ps -a` shows nothing.
                if ctr.get("rm"):
                    del world.containers[name]
                    io.print(c(f"   (--rm: '{name}' was removed the instant it stopped — its "
                               "writable layer went with it)", "dim"))
            elif sub == "start":
                ctr["status"] = "running"; io.print(name)
            else:  # rm
                if ctr["status"] == "running" and not force:
                    io.print(f"Error response from daemon: cannot remove container \"{name}\": "
                             "container is running: stop the container before removing or force remove")
                    continue
                del world.containers[name]
                io.print(name)

    elif sub == "rmi":
        _docker_rmi(world, rest, io)

    elif sub == "prune" or (sub in ("image", "container", "network", "system")
                            and rest[:1] == ["prune"]):
        _docker_prune(world, "system" if sub == "prune" else sub, rest[1:], io)

    elif sub == "network":
        _docker_network(world, rest, io)

    elif sub == "build":
        _docker_build(world, rest, io)

    elif sub == "tag":
        srcdst = [a for a in rest if not a.startswith("-")]
        if len(srcdst) != 2:
            io.print('"docker tag" requires exactly 2 arguments.')
            io.print("See 'docker tag --help'.")
            world.flags["_noop"] = True
            return
        src, dst = world.norm_image(srcdst[0]), world.norm_image(srcdst[1])
        if src not in world.images:
            io.print(f"Error response from daemon: No such image: {src}")
            world.flags["_noop"] = True
            return
        world.images.add(dst)
        # A tag is a second NAME for one image: same ID, same size, no copy made.
        world.flags.setdefault("_img_id", {})[dst] = image_id(world, src)
        world.flags.setdefault("_img_mb", {})[dst] = image_mb(world, src)
        if src in world.flags.get("_img_new", set()):
            world.flags.setdefault("_img_new", set()).add(dst)
        for store in ("_img_meta",):
            if src in world.flags.get(store, {}):
                world.flags[store][dst] = world.flags[store][src]
        world.flags["tagged"] = dst

    elif sub == "login":
        user = io.input("Username: ").strip()
        io.input("Password: ")
        io.print("")
        io.print("Login Succeeded")
        io.print(c("(in real life: paste an ACCESS TOKEN here, never your account password)", "dim"))
        world.flags["logged_in"] = user or True

    elif sub == "push":
        if not rest:
            io.print("docker push: needs an image"); return
        img = world.norm_image(rest[0])
        if img not in world.images:
            io.print(f"An image does not exist locally with the tag: {img.rsplit(':', 1)[0]}"); return
        if "/" not in img:
            io.print(f"denied: requested access to the resource is denied")
            io.print(c("(images must be namespaced <dockerhub-username>/<repo> — re-tag it with docker tag)", "dim"))
            return
        if not world.flags.get("logged_in"):
            io.print("denied: requested access to the resource is denied")
            io.print(c("(pushing always requires docker login — even to a public repo)", "dim"))
            return
        io.print(f"The push refers to repository [docker.io/{img.rsplit(':', 1)[0]}]")
        io.print(f"{_rand_id()}: Pushed")
        io.print(f"{img.rsplit(':', 1)[1]}: digest: sha256:{_rand_id()}{_rand_id()} size: 1234")
        world.flags["pushed_remote"] = img

    elif sub in ("ls", "list"):
        world.flags["_noop"] = True
        io.print("Almost! docker lists each kind of thing with its own command:")
        io.print(c("  docker images   → images you've downloaded", "dim"))
        io.print(c("  docker ps       → running containers  (add -a to include stopped ones)", "dim"))

    elif sub in DOCKER_MGMT:
        # A management command with a subcommand we don't have — show its page
        # rather than pretending the whole command is unknown.
        if rest:
            io.print(f"docker {sub}: '{rest[0]}' is not simulated in this world.")
        _docker_help(world, sub, io)

    elif sub in DOCKER_UNSIMULATED:
        world.flags["_noop"] = True
        io.print(f"docker: `{sub}` is a real docker command — this world just doesn't simulate it.")
        io.print(c(f"   {DOCKER_UNSIMULATED[sub]}", "dim"))
        io.print(c("   worth trying on your own machine · `docker --help` lists what works here",
                   "dim"))

    else:
        world.flags["_noop"] = True
        known = tuple(DOCKER_SUBS) + tuple(DOCKER_MGMT)
        io.print(f"docker: '{sub}' is not a docker command." + _suggest(sub, known))
        io.print(c("See 'docker --help' — it lists every command this world simulates.", "dim"))


# ------------------------------------------------------------- docker run --
HELLO_WORLD = """Hello from Docker!
This message shows that your installation appears to be working correctly.

To generate this message, Docker took the following steps:
 1. The Docker client contacted the Docker daemon.
 2. The Docker daemon pulled the "hello-world" image from the Docker Hub.
    (amd64)
 3. The Docker daemon created a new container from that image which runs the
    executable that produces the output you are currently reading.
 4. The Docker daemon streamed that output to the Docker client, which sent it
    to your terminal."""


class _Capture:
    """Collects what a command printed, so it can land in a container's LOGS
    instead of only on screen — `docker run -d alpine echo hi` really does put
    that line where `docker logs` will find it."""

    def __init__(self, io=None):
        self.io, self.lines = io, []

    def print(self, *args):
        self.lines.append(" ".join(str(a) for a in args))
        if self.io:
            self.io.print(*args)

    def write(self, text):
        if self.io:
            self.io.write(text)

    def input(self, prompt=""):
        return self.io.input(prompt) if self.io else ""


def _startup_logs(world, img):
    return STARTUP_LOGS.get(_img_parts(img)[0], "")


def _is_server(world, img, ports):
    """Does this image keep running on its own? An image built here answers
    honestly — we still have its Dockerfile. For the rest, a published port or
    an unfamiliar name means 'server'; the shell-only bases exit immediately."""
    if ports:
        return True
    meta = world.flags.get("_img_meta", {}).get(img)
    if meta is not None:
        return bool(meta.get("expose"))
    return _img_parts(img)[0] not in SHELL_IMAGES


def _docker_run(world, rest, io):
    """`docker run`, including what happens AFTER the container starts — which is
    where the class's flags earn their keep: -d, -it and --rm each change it."""
    parsed, bad, pos = _parse_run_flags(rest)
    if isinstance(bad, tuple) and bad[0] == "MISSINGVAL":
        world.flags["_noop"] = True
        io.print(f"docker: flag needs an argument: {bad[1]}.")
        io.print("See 'docker run --help'.")
        return
    if bad:
        world.flags["_noop"] = True
        io.print(f"unknown flag: {bad}")
        io.print(c("   (this world models the flags the course uses: -d -i -t --name "
                   "--network -p -e -v --rm — `docker run --help` lists them)", "dim"))
        return
    if not pos:
        world.flags["_noop"] = True
        io.print('"docker run" requires at least 1 argument.')
        io.print("See 'docker run --help'.")
        return
    img, cmd = world.norm_image(pos[0]), pos[1:]
    if img not in world.images:
        repo, tag = img.rsplit(":", 1)
        io.print(f"Unable to find image '{img}' locally")
        # `run` pulls, so `run` fails the way `pull` fails — a container that
        # starts from an image nobody published is the one thing this world must
        # not invent.
        if _pull_denied(world, io, img):
            return
        if not image_exists(img):
            io.print(f"docker: Error response from daemon: manifest for {img} not found: "
                     "manifest unknown: manifest unknown.")
            io.print(c("(the repository exists, that TAG does not. Same failure a Deployment hits "
                       "— except Kubernetes parks the pod in ImagePullBackOff instead of saying "
                       "so out loud.)", "dim"))
            world.flags["_noop"] = True
            return
        io.print(f"{tag}: Pulling from {repo if '/' in repo else 'library/' + repo}")
        io.print(f"Status: Downloaded newer image for {img}")
        world.images.add(img)
        world.flags.setdefault("_img_new", set()).add(img)
    name = parsed["name"] or _rand_name()
    if name in world.containers:
        io.print(f'docker: Error response from daemon: Conflict. The container name "/{name}" is '
                 f'already in use by container "{world.containers[name]["id"]}". You have to remove '
                 "(or rename) that container to be able to reuse that name.")
        io.print(c("(every `run` builds a NEW container — to get back into the one you have use "
                   f"`docker exec`, to replace it `docker rm {name}` first)", "dim"))
        world.flags["_noop"] = True
        return
    if parsed["network"] != "bridge" and parsed["network"] not in world.networks:
        io.print(f'docker: Error response from daemon: network {parsed["network"]} not found.')
        io.print(c("(`docker network ls` lists them — the network has to exist before a container "
                   "can join it: docker network create <name>)", "dim"))
        world.flags["_noop"] = True
        return
    if parsed["volumes"]:
        io.print(c(f"   (mounted {len(parsed['volumes'])} volume(s) — a volume outlives the "
                   "container, which is how data survives `docker rm`)", "dim"))
        if PLAYER_OS == "linux" and any(":" in v and not v.endswith((":z", ":Z"))
                                        for v in parsed["volumes"]):
            io.print(c("   (on Fedora/RHEL, SELinux blocks the mount unless you append :Z — "
                       "e.g. -v /data:/data:Z. The classic 'permission denied' on Fedora.)",
                       "dim"))
    if parsed["env"]:
        io.print(c(f"   (passed {len(parsed['env'])} env var(s) into the container — "
                   "config comes from the environment, not baked into the image; `env` inside "
                   "shows them)", "dim"))
    ctr = {
        "id": _rand_id(), "image": img, "status": "running", "exit_code": 0,
        "logs": "", "network": parsed["network"], "networks": [parsed["network"]],
        "ports": parsed["ports"], "files": {}, "env": list(parsed["env"]),
        "rm": parsed["rm"],
    }
    world.containers[name] = ctr
    world.flags["last_run"] = name
    detached = parsed["d"]
    if detached:
        io.print(ctr["id"] + _rand_id())        # the long id, like real docker

    def gone(code=0):
        """The container's process finished — and --rm means it doesn't linger."""
        ctr["status"], ctr["exit_code"] = "exited", code
        if parsed["rm"] and name in world.containers:
            del world.containers[name]
            io.print(c("   (--rm: removed the moment it exited — `docker ps -a` won't show it. "
                       "The class uses --rm everywhere for exactly this)", "dim"))

    shellish = bool(cmd) and cmd[0].rsplit("/", 1)[-1] in ("bash", "sh", "ash", "zsh")
    if cmd and (cmd[0] in ("sleep", "top", "watch") or (cmd[0] == "tail" and "-f" in cmd)):
        # A command that blocks keeps the container alive — which is exactly why
        # `docker run -d ubuntu sleep 3600` is the standard "give me a container
        # to poke at" trick.
        if not detached:
            # In the foreground you'd wait it out and get the prompt back with the
            # container already finished — so that is the state we land in.
            io.print(c(f"(`{' '.join(cmd)}` blocks — a real terminal would sit here until it "
                       "finished, then hand the prompt back with the container exited. Add -d "
                       "and the container stays up in the background instead.)", "dim"))
            gone(0)
        return
    if cmd and not shellish:
        # An explicit command IS the container's whole life: start, run, exit.
        cap = _Capture(None if detached else io)
        run_inside(world, name, cmd, cap)
        ctr["logs"] = "\n".join(cap.lines)
        world.flags.pop("_noop", None)          # it created a container: that's a move
        if detached:
            io.print(c(f"   (ran `{' '.join(cmd)}` and exited — a container lives exactly as long "
                       f"as its command. `docker logs {name}` still has the output)", "dim"))
        gone(0)
        return
    if shellish:
        if parsed["it"]:
            if detached:
                return                          # -dit: alive in the background, ready for exec
            world.inside = name
            ctr["main_shell"] = True
            io.print(c(f"🐚 you are now INSIDE '{name}' — and this shell IS the container's main "
                       "process (you ran it without -d), so `exit` stops the container", "dim"))
            return
        io.print(c(f"   (`{cmd[0]}` started, found no terminal attached, and exited immediately — "
                   f"that is what -i and -t are for. Try: docker run -dit --name {name} "
                   f"{pos[0]} {cmd[0]})", "dim"))
        gone(0)
        return
    if _img_parts(img)[0] == "hello-world":
        io.print(HELLO_WORLD)
        ctr["logs"] = HELLO_WORLD
        gone(0)
        return
    if _is_server(world, img, parsed["ports"]):
        ctr["logs"] = _startup_logs(world, img)
        if not detached:
            if ctr["logs"]:
                io.print(ctr["logs"])
            io.print(c("(no -d, so a REAL terminal would be stuck right here streaming this until "
                       "Ctrl-C — this world hands the prompt back so you can keep working. `-d` is "
                       f"what backgrounds it for real; `docker logs {name}` reads the same lines.)",
                       "dim"))
        return
    meta = world.flags.get("_img_meta", {}).get(img, {})
    io.print(c(f"   (the container ran its default command{' ' + meta['cmd'] if meta.get('cmd') else ''} "
               "and exited — this world models the container lifecycle, not your program's output. "
               "A container lives as long as that command does.)", "dim"))
    gone(0)


# ----------------------------------------------------------- docker build --
# BuildKit numbers only the instructions that produce a layer; EXPOSE, CMD and
# friends are metadata, folded into the export step. Modelling that split is
# what makes the step counter in the output ([2/5]) match a real build.
DF_META = {"EXPOSE", "CMD", "ENTRYPOINT", "LABEL", "ARG", "MAINTAINER", "VOLUME",
           "STOPSIGNAL", "HEALTHCHECK", "SHELL", "ONBUILD"}
DF_LAYER = {"FROM", "RUN", "COPY", "ADD", "WORKDIR", "ENV", "USER"}
INSTALLISH = ("pip install", "pip3 install", "apt-get install", "apt install",
              "apk add", "npm install", "npm ci", "yarn install", "dnf install",
              "yum install", "go mod download", "mvn ")


def _df_parse(text):
    """Dockerfile → [(INSTRUCTION, argument, line-number)], comments dropped and
    backslash continuations folded the way the builder folds them."""
    out, buf, start = [], "", 0
    for n, raw in enumerate(text.split("\n"), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if not buf:
            start = n
        if line.endswith("\\"):
            buf += line[:-1].strip() + " "
            continue
        head, _, arg = (buf + line).strip().partition(" ")
        out.append((head.upper(), arg.strip(), start))
        buf = ""
    if buf:
        head, _, arg = buf.strip().partition(" ")
        out.append((head.upper(), arg.strip(), start))
    return out


def _copy_sources(world, arg):
    """The build-context files a COPY/ADD reads. Their CONTENT is the cache key —
    which is why editing app.py busts that one step and nothing above it."""
    srcs = arg.split()[:-1] or ["."]
    names = []
    for s in srcs:
        s = s.strip('"').strip("'")
        if s in (".", "./", "*", "./*"):
            names += sorted(world.files)
        else:
            names.append(s.rstrip("/"))
    return names


def _copies_code(world, arg):
    """Does this COPY bring in application code rather than a dependency list?
    `COPY . .` always does — which is why it belongs BELOW the install step."""
    raw = [s.strip("\"'") for s in (arg.split()[:-1] or ["."])]
    if any(s in (".", "./", "*", "./*") for s in raw):
        return True
    return any(s.endswith((".py", ".js", ".ts", ".go", ".java", ".rb", ".php"))
               for s in _copy_sources(world, arg))


def _build_keys(world, steps):
    """One cache key per instruction. Docker's key is the instruction plus what
    it reads, so `COPY app.py .` changes when app.py changes even though the
    Dockerfile line is identical — the whole point of the caching drill."""
    keys = []
    for instr, arg, _ln in steps:
        if instr in ("COPY", "ADD"):
            body = "|".join(f"{n}={world.files.get(n, world.files.get(n + '/', '<missing>'))}"
                            for n in _copy_sources(world, arg))
            keys.append(f"{instr} {arg}\n{body}")
        else:
            keys.append(f"{instr} {arg}")
    return keys


def _common_prefix(a, b):
    n = 0
    while n < len(a) and n < len(b) and a[n] == b[n]:
        n += 1
    return n


def _docker_build(world, rest, io):
    """`docker build` with a real layer cache: rebuild an unchanged Dockerfile and
    every step says CACHED; change one and everything from there DOWN rebuilds."""
    name, dfile, ctx, no_cache = None, "Dockerfile", None, False
    i = 0
    while i < len(rest):
        a = rest[i]
        if a in ("-t", "--tag") and i + 1 < len(rest):
            i += 1; name = rest[i]
        elif a in ("-f", "--file") and i + 1 < len(rest):
            i += 1; dfile = rest[i]
        elif a == "--no-cache":
            no_cache = True
        elif a.startswith("-"):
            pass                       # --build-arg & friends: accepted, not modelled
        elif ctx is None:
            ctx = a
        i += 1
    if ctx is None:
        # The deck calls this the #1 first-build error, in docker's own words.
        world.flags["_noop"] = True
        io.print('ERROR: "docker buildx build" requires exactly 1 argument.')
        io.print("See 'docker build --help'.")
        io.print("")
        io.print("Usage:  docker build [OPTIONS] PATH | URL | -")
        io.print("")
        io.print("Build an image from a Dockerfile")
        io.print(c("(the missing piece is the trailing `.` — the BUILD CONTEXT, the folder Docker "
                   "is allowed to COPY from: docker build -t <name> .)", "dim"))
        return
    if dfile not in world.files:
        world.flags["_noop"] = True
        io.print(f"ERROR: failed to solve: failed to read dockerfile: open {dfile}: "
                 "no such file or directory")
        io.print(c(f"(create one first — try: edit {dfile})", "dim"))
        return
    steps = _df_parse(world.files[dfile])
    # A typo'd instruction is reported before the missing-FROM complaint, because
    # `FORM python:3` is a typo — not a Dockerfile that forgot its base image.
    bad = next(((ins, ln) for ins, _a, ln in steps if ins not in DF_LAYER | DF_META), None)
    if bad:
        world.flags["_noop"] = True
        io.print(f"ERROR: failed to solve: dockerfile parse error on line {bad[1]}: "
                 f"unknown instruction: {bad[0]}")
        io.print(c("(the instructions are FROM · WORKDIR · COPY · ADD · RUN · ENV · EXPOSE · CMD · "
                   "ENTRYPOINT — one typo stops the build before anything is built)", "dim"))
        return
    if not steps or steps[0][0] != "FROM":
        world.flags["_noop"] = True
        io.print("ERROR: failed to solve: dockerfile parse error: no build stage — a Dockerfile "
                 "must start with FROM <base-image>")
        return
    for instr, arg, _ln in steps:
        if instr in ("COPY", "ADD"):
            for s in _copy_sources(world, arg):
                if s not in world.files and s + "/" not in world.files:
                    world.flags["_noop"] = True
                    io.print("ERROR: failed to solve: failed to compute cache key: failed to "
                             f'calculate checksum of ref: "/{s}": not found')
                    io.print(c(f"(COPY reads from the build CONTEXT — `{s}` isn't in this folder. "
                               "`ls` shows what is.)", "dim"))
                    return
    base = world.norm_image(steps[0][1].split()[0])
    layers = [s for s in steps if s[0] not in DF_META]
    keys = _build_keys(world, steps)
    prev = world.flags.setdefault("_build_keys", [])
    cached = 0 if no_cache else max([_common_prefix(k, keys) for k in prev] or [0])

    # The clock is half the lesson: a cached rebuild finishes in a blink, and the
    # step that eats the seconds is always the install.
    secs = 0.2
    for pos, (instr, arg, _ln) in enumerate(steps):
        if instr in DF_META or pos < cached:
            continue
        secs += (12.4 if instr == "RUN" and any(k in arg for k in INSTALLISH)
                 else 1.6 if instr == "RUN" else 0.9 if instr == "FROM" else 0.2)
    io.print(f"[+] Building {secs:.1f}s ({len(layers) + 4}/{len(layers) + 4}) FINISHED")
    io.print(f" => [internal] load build definition from {dfile}")
    io.print(f" => [internal] load metadata for docker.io/library/{base}")
    if base not in world.images:
        world.images.add(base)
        world.flags.setdefault("_img_new", set()).add(base)
        io.print(c(f"   (the base image {base} wasn't local — the build pulled it. That download "
                   "is why a first build is slow and the next one isn't.)", "dim"))
    io.print(" => [internal] load .dockerignore")
    io.print(f" => [internal] load build context ({ctx})")
    n = 0
    for pos, (instr, arg, _ln) in enumerate(steps):
        if instr in DF_META:
            continue
        n += 1
        mark = "CACHED " if pos < cached else ""
        shown = f"{instr} {arg}" if instr != "FROM" else f"FROM docker.io/library/{base}"
        io.print(f" => {mark}[{n}/{len(layers)}] {shown[:72]}")
    io.print(" => exporting to image")

    mb = image_mb(world, base)
    for instr, arg, _ln in layers:
        if instr in ("COPY", "ADD"):
            mb += sum(len(world.files.get(s, "")) for s in _copy_sources(world, arg)) / 1e6
        elif instr == "RUN":
            mb += (5 + _stable_frac(arg) * 40) if any(k in arg for k in INSTALLISH) else 0.01
    mb = round(mb, 2)
    new_id = _stable_id("build:" + "".join(keys))[:12]
    prev.append(keys)                  # these layers are in the cache from now on
    if name is None:
        # Real docker builds it anyway — with no tag, which is exactly how a
        # dangling <none>:<none> image is born.
        world.flags.setdefault("_dangling", []).append({"id": new_id, "mb": mb})
        io.print(c("WARNING: this image has no name — `docker images` will show it as "
                   "<none>:<none> (a dangling image, and nothing you can run).", "yellow"))
        io.print(c("   rebuild it with a tag: docker build -t <name> .", "dim"))
        return
    img = world.norm_image(name)
    io.print(f" => => naming to docker.io/{img if '/' in img else 'library/' + img}")
    old_id = world.flags.get("_img_id", {}).get(img)
    if img in world.images and old_id and old_id != new_id:
        # The tag moved to the new image; the old one is now untagged = dangling.
        world.flags.setdefault("_dangling", []).append(
            {"id": old_id, "mb": world.flags.get("_img_mb", {}).get(img, mb)})
    world.images.add(img)
    world.flags.setdefault("_img_id", {})[img] = new_id
    world.flags.setdefault("_img_mb", {})[img] = mb
    world.flags.setdefault("_img_new", set()).add(img)
    world.flags.setdefault("_img_meta", {})[img] = {
        "base": base,
        "cmd": next((a for ins, a, _l in steps if ins == "CMD"), ""),
        "expose": [a for ins, a, _l in steps if ins == "EXPOSE"],
    }
    world.flags["built"] = img
    # How many layers the cache served, for missions that drill the caching
    # lesson ("rebuild it and watch the steps say CACHED").
    hits = sum(1 for pos, (ins, _a, _l) in enumerate(steps)
               if ins not in DF_META and pos < cached)
    world.flags["build_cached"] = hits

    if cached:
        io.print(c(f"({hits} of {len(layers)} steps CACHED — Docker reused every layer whose "
                   "inputs hadn't changed. The first step that DID change invalidates every "
                   "step below it.)", "dim"))
    elif len(prev) > 1:
        io.print(c("(nothing CACHED — the very first instruction's inputs changed, so every layer "
                   "below it rebuilt too. That cascade is why instruction ORDER matters.)", "dim"))
    code_at = next((i_ for i_, (ins, a, _l) in enumerate(steps)
                    if ins in ("COPY", "ADD") and _copies_code(world, a)), None)
    install_at = next((i_ for i_, (ins, a, _l) in enumerate(steps)
                       if ins == "RUN" and any(k in a for k in INSTALLISH)), None)
    if code_at is not None and install_at is not None and code_at < install_at:
        io.print(c("(cache tip: this Dockerfile COPYs code ABOVE the install step, so editing one "
                   "line of code re-runs the whole install next build. COPY requirements.txt "
                   "first, RUN the install, THEN copy the code.)", "dim"))


# ------------------------------------------------- docker rmi / prune / net --
def _docker_rmi(world, rest, io):
    """Removing an image is where beginners meet 'the image is still in use' —
    a stopped container pins its image just as hard as a running one does."""
    force = any(a in ("-f", "--force") for a in rest)
    targets = [a for a in rest if not a.startswith("-")]
    if not targets:
        world.flags["_noop"] = True
        io.print('"docker rmi" requires at least 1 argument.')
        io.print("See 'docker rmi --help'.")
        return
    for t in targets:
        img = world.norm_image(t)
        if img not in world.images:
            world.flags["_noop"] = True
            io.print(f"Error response from daemon: No such image: {img}")
            continue
        users = [n for n, d in world.containers.items()
                 if world.norm_image(d["image"]) == img]
        if users and not force:
            world.flags["_noop"] = True
            io.print(f'Error response from daemon: conflict: unable to remove repository reference '
                     f'"{t}" (must force) - container {world.containers[users[0]]["id"]} is using '
                     f'its referenced image {image_id(world, img)}')
            io.print(c("(the image is that container's recipe — even a STOPPED one pins it. Remove "
                       f"the container first: docker rm {users[0]}  … or force it with -f)", "dim"))
            continue
        world.images.discard(img)
        io.print(f"Untagged: {img}")
        # Another tag on the same image means nothing is deleted — the tag went,
        # the image stayed. Docker says so by printing Untagged and nothing else.
        twins = [o for o in world.images if image_id(world, o) == image_id(world, img)]
        if twins:
            io.print(c(f"   (the image itself is still here — {twins[0]} is another name for it. "
                       "Removing a tag is not removing an image.)", "dim"))
        elif users:
            # Forced: the tag is gone but the container still holds the image,
            # so it lives on with no name — the textbook dangling image.
            world.flags.setdefault("_dangling", []).append(
                {"id": image_id(world, img), "mb": image_mb(world, img)})
            io.print(c(f"   (forced — but {users[0]} still runs from it, so the image survives with "
                       "no name: that's a <none>:<none> dangling image)", "dim"))
        else:
            io.print(f"Deleted: sha256:{_long_digest(img)}")
        world.flags["rmi"] = img


def _docker_prune(world, what, rest, io):
    """`docker image prune` / `docker system prune` — with docker's own WARNING,
    because 'dangling' is the word students have to learn to recognise."""
    force = any(a in ("-f", "--force") for a in rest)
    every = any(a in ("-a", "--all") for a in rest)
    if not force:                     # -f skips the warning AND the question
        if what == "image":
            io.print("WARNING! This will remove all dangling images.")
        elif what == "container":
            io.print("WARNING! This will remove all stopped containers.")
        elif what == "network":
            io.print("WARNING! This will remove all custom networks not used by at least one container.")
        else:
            io.print("WARNING! This will remove:")
            io.print("  - all stopped containers")
            io.print("  - all networks not used by at least one container")
            io.print("  - all dangling images")
            io.print("  - all dangling build cache")
            if every:
                io.print("  - all images without at least one container associated to them")
        io.print("")
        try:
            answer = io.input("Are you sure you want to continue? [y/N] ")
        except EOFError:
            answer = ""
        if answer.strip().lower() not in ("y", "yes"):
            io.print(c("(nothing removed — prune always asks first. `-f` skips the question, which "
                       "is how you'd write it in a script)", "dim"))
            return
    reclaimed, dead, nets, imgs = 0.0, [], [], []
    if what in ("container", "system"):
        dead = [n for n, d in world.containers.items() if d["status"] != "running"]
        for n in dead:
            del world.containers[n]
    if what in ("network", "system"):
        busy = {net for d in world.containers.values() for net in _nets(d)}
        nets = sorted(n for n in world.networks if n != "bridge" and n not in busy)
        world.networks -= set(nets)
    if what in ("image", "system"):
        for d in world.flags.get("_dangling", []):
            imgs.append(d["id"]); reclaimed += d["mb"]
        world.flags["_dangling"] = []
        if every:
            used = {world.norm_image(d["image"]) for d in world.containers.values()}
            for img in sorted(world.images - used):
                imgs.append(image_id(world, img)); reclaimed += image_mb(world, img)
                world.images.discard(img)
    if dead:
        io.print("Deleted Containers:")
        for n in dead:
            io.print(n)
        io.print("")
    if nets:
        io.print("Deleted Networks:")
        for n in nets:
            io.print(n)
        io.print("")
    if imgs:
        io.print("Deleted Images:")
        for i in imgs:
            io.print(f"deleted: sha256:{_long_digest(i)}")
        io.print("")
    io.print(f"Total reclaimed space: {_fmt_mb(round(reclaimed, 2))}")
    world.flags["pruned"] = what
    if what in ("image", "system"):
        io.print(c("(dangling = an image no tag points at any more — every rebuild of the same tag "
                   "leaves one behind. `docker system prune -a` goes further and deletes images no "
                   "CONTAINER is using: that's the one that eats the image you were about to run.)",
                   "dim"))


def _docker_network(world, rest, io):
    sub = rest[0] if rest else ""
    args = [a for a in rest[1:] if not a.startswith("-")]
    if sub == "create" and args:
        net = args[-1]                       # `-d bridge` and friends come first
        if net in world.networks:
            world.flags["_noop"] = True
            io.print(f"Error response from daemon: network with name {net} already exists")
            io.print(c("(networks are created once and reused — `docker network ls` shows what "
                       "you already have)", "dim"))
            return
        world.networks.add(net)
        io.print(_long_digest("net:" + net))     # docker echoes the full network id
        io.print(c("(this one has Docker's embedded DNS: containers you attach to it reach each "
                   "other BY NAME — the default bridge never will)", "dim"))
    elif sub in ("ls", "list"):
        io.print(f"{'NETWORK ID':<15}{'NAME':<24}{'DRIVER':<10}SCOPE")
        for n in sorted(world.networks):
            io.print(f"{_net_id(n):<15}{n:<24}{'bridge':<10}local")
        if world.networks == {"bridge"}:
            io.print(c("(only the default bridge — and it has NO name resolution. "
                       "`docker network create <name>` makes one that does)", "dim"))
        world.flags["net_ls"] = True
    elif sub == "inspect" and args:
        net = args[0]
        if net not in world.networks:
            world.flags["_noop"] = True
            io.print(f"Error response from daemon: network {net} not found")
            io.print(c("(`docker network ls` lists what exists)", "dim"))
            return
        members = [(n, d) for n, d in world.containers.items() if net in _nets(d)]
        sn = _net_subnet(net)
        io.print("[\n    {")
        io.print(f'        "Name": "{net}",')
        io.print(f'        "Id": "{_long_digest("net:" + net)}",')
        io.print('        "Scope": "local",')
        io.print('        "Driver": "bridge",')
        io.print('        "IPAM": {')
        io.print('            "Config": [')
        io.print('                {')
        io.print(f'                    "Subnet": "{sn}.0.0/16",')
        io.print(f'                    "Gateway": "{sn}.0.1"')
        io.print('                }')
        io.print('            ]')
        io.print('        },')
        io.print('        "Containers": {')
        for i, (n, d) in enumerate(members):
            io.print(f'            "{d["id"]}": {{')
            io.print(f'                "Name": "{n}",')
            io.print(f'                "IPv4Address": "{_net_ip(world, net, n)}/16"')
            io.print("            }" + ("," if i < len(members) - 1 else ""))
        io.print("        }\n    }\n]")
        world.flags["net_inspect"] = net
        if net == "bridge":
            io.print(c("(the default bridge hands out IPs but no names — that Containers map is "
                       "not a DNS table, which is why `ping <name>` fails here)", "dim"))
        else:
            io.print(c("(this Containers map IS the phone directory — Docker's embedded DNS on a "
                       "user-defined network resolves each Name to its IPv4Address for you)", "dim"))
    elif sub in ("connect", "disconnect") and len(args) >= 2:
        net, cname = args[0], args[1]
        if net not in world.networks:
            world.flags["_noop"] = True
            io.print(f"Error response from daemon: network {net} not found"); return
        if cname not in world.containers:
            world.flags["_noop"] = True
            io.print(f"Error response from daemon: No such container: {cname}"); return
        ctr = world.containers[cname]
        nets = _nets(ctr)
        if sub == "connect":
            if net not in nets:
                nets.append(net)
            io.print(c(f"(connected — {cname} now answers on {net} too. A container can sit on "
                       "several networks at once; that's how a proxy reaches both sides)", "dim"))
        else:
            nets = [n for n in nets if n != net] or ["bridge"]
            io.print(c(f"(disconnected — {cname} can no longer be reached by name on {net})", "dim"))
        ctr["networks"] = nets
        ctr["network"] = nets[0]
    elif sub == "rm" and args:
        for net in args:
            if net == "bridge":
                io.print("Error response from daemon: bridge is a pre-defined network "
                         "and cannot be removed")
                continue
            if net not in world.networks:
                io.print(f"Error response from daemon: network {net} not found")
                continue
            busy = [n for n, d in world.containers.items() if net in _nets(d)]
            if busy:
                io.print(f"Error response from daemon: error while removing network: network {net} "
                         f"id {_net_id(net)} has active endpoints")
                io.print(c(f"(a network with containers on it can't go — remove or disconnect "
                           f"{busy[0]} first)", "dim"))
                continue
            world.networks.discard(net)
            io.print(net)
    else:
        world.flags["_noop"] = True
        _docker_help(world, "network", io)


# ---------------------------------------------------- inside-container shell --
# The "not found" message inside a container reads from this, so it has to name
# every branch run_inside actually has — a list that under-reports is the same
# lie as one that over-promises.
CONTAINER_CMDS = ("ls, cat, touch, mkdir, cp, mv, rm, echo, env, printenv, pwd, cd, "
                  "whoami, hostname, ping, curl, wget, sleep, clear")


def _cpath(p):
    """Container paths, normalised into this flat /root world: `/root/report.txt`,
    `~/report.txt`, `./report.txt` and `report.txt` are one file. The Class 01
    boss challenge depends on it — write it inside, read it back from the host
    with `docker exec boss cat /root/report.txt`."""
    p = p.strip().strip('"').strip("'")
    if p in ("/root", "~", ".", "./", "/root/"):
        return ""
    for prefix in ("/root/", "~/", "./"):
        if p.startswith(prefix):
            return p[len(prefix):]
    return p


def _resolve_peer(world, me_name, host):
    """Container name → (peer, ip). It only resolves when both containers share a
    USER-DEFINED network — that refusal on the default bridge is where Class 02's
    whole lesson lives, so it is modelled, not hand-waved."""
    me = world.containers[me_name]
    if host in ("localhost", "127.0.0.1", "0.0.0.0", me_name):
        return me_name, "127.0.0.1"
    for net in [n for n in _nets(me) if n != "bridge"]:
        peer = world.containers.get(host)
        if peer is not None and net in _nets(peer) and peer["status"] == "running":
            return host, _net_ip(world, net, host)
    # A raw IP still connects — the brittle fallback the class warns about,
    # because that number changes every time the container is recreated.
    for cname, d in world.containers.items():
        if any(_net_ip(world, net, cname) == host for net in _nets(d)):
            return cname, host
    return None, None


def _serves(world, ctr):
    """(port, body) — what a peer gets when it asks this container for a page."""
    repo, _v = _img_parts(ctr["image"])
    if repo in ("nginx", "httpd"):
        return 80, NGINX_HTML
    port = 8080
    expose = world.flags.get("_img_meta", {}).get(ctr["image"], {}).get("expose")
    if expose:
        port = int(re.sub(r"\D", "", expose[0].split("/")[0]) or 8080)
    elif ctr.get("ports"):
        port = int(re.sub(r"\D", "", ctr["ports"][0].rsplit(":", 1)[-1]) or 8080)
    if "flask" in ctr["image"]:
        return port, "Hello! I am a Flask application"
    return port, "Hello from Docker! Your app is running."


def _fetch(world, name, prog, args, io):
    """`wget` from inside a container — the Class 02 drill (`wget -qO- nginx2`)
    and the proof that a user-defined network resolves names."""
    ctr = world.containers[name]
    files = ctr["files"]
    if prog == "curl":
        world.flags["_noop"] = True
        io.print("sh: curl: not found" if _busybox(ctr["image"])
                 else "bash: curl: command not found")
        io.print(c("(base images ship almost nothing — nginx:alpine has busybox's `wget`, not "
                   "curl. That's why the class fetches with `wget -qO- <name>` from INSIDE a "
                   "container, and curls from the HOST, where curl actually lives.)", "dim"))
        return
    quiet, dest, target, i = False, "index.html", None, 0
    while i < len(args):
        a = args[i]
        if a in ("-q", "--quiet"):
            quiet = True
        elif a in ("-O", "--output-document"):
            i += 1
            dest = args[i] if i < len(args) else "-"
        elif a.startswith("-O"):
            dest = a[2:] or "-"
        elif a.startswith("-") and set(a[1:]) <= set("qO-") and a != "-":
            quiet = "q" in a                      # busybox's combined -qO- form
            if "O" in a:
                dest = a.split("O", 1)[1] or "-"
        elif not a.startswith("-"):
            target = target or a
        i += 1
    if not target:
        world.flags["_noop"] = True
        io.print("wget: missing URL")
        io.print(c("(Usage: wget [-q] [-O FILE] URL — `-O-` writes the page to your terminal "
                   "instead of a file, which is why the drill is `wget -qO- nginx2`)", "dim"))
        return
    url = re.sub(r"^https?://", "", target)
    hostport, _, _path = url.partition("/")
    host, _, port_s = hostport.partition(":")
    peer, ip = _resolve_peer(world, name, host)
    if not peer:
        world.flags["_noop"] = True
        io.print(f"wget: bad address '{host}'" if _busybox(ctr["image"])
                 else f"wget: unable to resolve host address '{host}'")
        io.print(c("(no name resolution here. Docker's embedded DNS only runs on a USER-DEFINED "
                   "network — on the default bridge you'd need the raw IP, which changes on every "
                   "restart. docker network create <net>, then --network <net> on both.)", "dim"))
        return
    svc_port, body = _serves(world, world.containers[peer])
    port = int(port_s) if port_s.isdigit() else svc_port
    if not quiet:
        io.print(f"Connecting to {hostport or host} ({ip}:{port})")
    if port != svc_port:
        io.print(f"wget: can't connect to remote host ({ip}): Connection refused")
        io.print(c(f"(inside the network you talk to the CONTAINER's own port — {peer} listens on "
                   f"{svc_port}. `-p` maps ports on the HOST side only; it changes nothing in here.)",
                   "dim"))
        world.flags["_noop"] = True
        return
    if dest == "-":
        io.print(body)
    else:
        files[_cpath(dest)] = body
        io.print(f"saving to '{dest}'")
        io.print(f"{dest:<20} 100% |{'*' * 32}| {len(body):>6}  0:00:00 ETA")
        io.print(f"'{dest}' saved")
    world.flags["fetch_ok"] = (name, peer)


def _host_curl(world, args, io):
    """`curl localhost:<port>` from the HOST — the other half of the -p lesson.
    It answers only if a container PUBLISHED that port, which is what makes
    `EXPOSE documents, -p publishes` something you can feel. Returns False when
    the target isn't a local port on a container world, so the 🌍 lesson still
    gets its turn for real URLs and for missions with no containers at all."""
    target = next((a for a in args if not a.startswith("-")), None)
    if not target or not world.containers:
        return False
    hostport = re.sub(r"^https?://", "", target).partition("/")[0]
    host, _, port_s = hostport.partition(":")
    if host not in ("localhost", "127.0.0.1", "0.0.0.0"):
        return False
    port = int(port_s) if port_s.isdigit() else 80
    for cname, ctr in world.running().items():
        for mapping in ctr.get("ports", []):
            # -p [ip:]HOST:CONTAINER — the last field is inside, the one before
            # it is the host port a curl on this machine can reach.
            fields = [f.split("/")[0] for f in mapping.split(":")]
            host_side = fields[-2] if len(fields) >= 2 else fields[-1]
            if host_side != str(port):
                continue
            svc_port, body = _serves(world, ctr)
            inner = int(re.sub(r"\D", "", fields[-1]) or svc_port)
            if inner != svc_port:
                # -p is HOST:CONTAINER. Flip them and the mapping lands on a port
                # nothing listens on — this is exactly what that looks like.
                io.print("curl: (52) Empty reply from server")
                io.print(c(f"(the mapping forwards to container port {inner}, but {cname} listens "
                           f"on {svc_port}. -p is HOST:CONTAINER — left is your machine, right is "
                           "inside the container.)", "dim"))
                world.flags["_noop"] = True
                return True
            io.print(body)
            world.flags["curl_ok"] = cname
            return True
    io.print(f"curl: (7) Failed to connect to localhost port {port}: Connection refused")
    io.print(c("(nothing is PUBLISHED on that host port. EXPOSE in a Dockerfile only documents "
               "the port — `docker run -p <host>:<container>` is what actually wires it up.)",
               "dim"))
    world.flags["_noop"] = True
    return True


def run_inside(world, name, cmd, io):
    ctr = world.containers[name]
    files = ctr["files"]
    if not cmd:
        return
    # Redirection is the shell's job, not each command's — so it happens here,
    # once, for all of them: `echo container wrangler > /root/report.txt`.
    op = next((x for x in cmd if x in (">", ">>")), None)
    if op:
        i = cmd.index(op)
        if len(cmd) <= i + 1:
            world.flags["_noop"] = True
            io.print("sh: syntax error: unexpected end of file")
            return
        cap = _Capture()
        run_inside(world, name, cmd[:i], cap)
        text = "\n".join(cap.lines)
        key = _cpath(cmd[i + 1])
        old = files.get(key, "")
        files[key] = (old.rstrip("\n") + "\n" + text) if op == ">>" and old else text
        world.flags.pop("_noop", None)            # writing a file IS a move
        return
    prog, args = cmd[0], cmd[1:]
    ops = [a for a in args if not a.startswith("-")]
    flags = "".join(a[1:] for a in args if a.startswith("-") and not a.startswith("--"))

    def entries(under):
        """Names directly inside `under` — a flat dict of paths, read as a tree."""
        found, prefix = set(), (under.rstrip("/") + "/") if under else ""
        for key in files:
            if prefix and not key.startswith(prefix):
                continue
            tail = key[len(prefix):]
            if tail:
                found.add(tail.split("/")[0])
        return sorted(found)

    if prog == "ls":
        target = _cpath(ops[0]).rstrip("/") if ops else ""
        if target and target not in files and target + "/" not in files:
            io.print(f"ls: {ops[0]}: No such file or directory")
            world.flags["_noop"] = True
            return
        if target and target + "/" not in files:      # a plain file lists itself
            io.print(ops[0]); return
        found = entries(target)
        if found:
            io.print("  ".join(found))
        elif not target:
            io.print(c("(nothing here — files live in this container's own writable layer, so a "
                       "fresh container from the same image always starts out this empty)", "dim"))
    elif prog == "touch" and ops:
        for p in map(_cpath, ops):
            files.setdefault(p, "")
    elif prog == "mkdir" and ops:
        for p in map(_cpath, ops):
            key = p.rstrip("/") + "/"
            if key in files and "p" not in flags:
                io.print(f"mkdir: cannot create directory '{p}': File exists")
            else:
                files[key] = ""
    elif prog in ("cp", "mv") and len(ops) == 2:
        src, dst = _cpath(ops[0]), _cpath(ops[1])
        if src not in files:
            io.print(f"{prog}: cannot stat '{ops[0]}': No such file or directory")
            world.flags["_noop"] = True
            return
        # `mv file1.txt temp/` keeps the name and lands it inside the directory;
        # `mv file1.txt file2.txt` renames it. Which one is decided by the target.
        into_dir = dst.rstrip("/") + "/" in files
        key = (dst.rstrip("/") + "/" + src.rsplit("/", 1)[-1]) if into_dir else dst
        files[key] = files[src] if prog == "cp" else files.pop(src)
    elif prog == "rm" and ops:
        for p in map(_cpath, ops):
            p = p.rstrip("/")
            victims = [k for k in files if k == p or k.startswith(p + "/")]
            if not victims:
                if "f" not in flags:
                    io.print(f"rm: cannot remove '{p}': No such file or directory")
                continue
            if p + "/" in files and not ("r" in flags or "R" in flags):
                io.print(f"rm: cannot remove '{p}': Is a directory")
                continue
            for k in victims:
                files.pop(k, None)
    elif prog == "cat" and ops:
        for p in ops:
            key = _cpath(p)
            io.print(files[key] if key in files else f"cat: {p}: No such file or directory")
    elif prog in ("env", "printenv"):
        io.print("PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
        io.print(f"HOSTNAME={ctr['id']}")
        for extra in IMAGE_ENV.get(_img_parts(ctr["image"])[0], []):
            io.print(extra)
        for e in ctr.get("env", []):
            io.print(e if "=" in e else f"{e}=")
        io.print("HOME=/root")
        io.print("TERM=xterm")
        io.print(c("(HOSTNAME is the container's own ID — and anything you passed with `-e` shows "
                   "up right here. Config reaches an app through the environment, so the same "
                   "image can run in dev and in prod.)", "dim"))
    elif prog == "pwd":
        io.print("/root")
    elif prog == "cd":
        world.flags["_noop"] = True           # nothing changes: this world is one folder
        target = _cpath(ops[0]) if ops else ""
        if not target:
            return                            # `cd` / `cd /root`: already home, silent
        if target.rstrip("/") + "/" in files:
            io.print(c(f"(this mini-shell has no working directory — you are always in /root. "
                       f"Reach into it with a path: ls {target} · cat {target}/<file>)", "dim"))
        else:
            io.print(f"sh: cd: can't cd to {ops[0]}: No such file or directory")
    elif prog == "whoami":
        io.print("root")
    elif prog == "hostname":
        io.print(ctr["id"])
    elif prog == "clear":
        # cursor home, clear screen, clear scrollback — what real `clear` sends.
        io.write("\033[H\033[2J\033[3J")
    elif prog == "echo":
        io.print(" ".join(args))
    elif prog == "sleep":
        # Faking a pause would teach nothing; saying what real sleep does teaches
        # why `docker run -d … sleep 3600` keeps a bare container alive.
        world.flags["_noop"] = True
        io.print(c(f"(real `sleep {' '.join(args)}` blocks this shell for that long — here it "
                   "returns at once. As a container's MAIN command it is the classic way to keep "
                   "a bare container up: docker run -d ubuntu sleep 3600)", "dim"))
    elif prog in ("wget", "curl"):
        _fetch(world, name, prog, args, io)
    elif prog == "ping" and ops:
        target = ops[-1]
        peer, ip = _resolve_peer(world, name, target)
        if peer:
            busy = _busybox(ctr["image"])
            io.print(f"PING {target} ({ip}): 56 data bytes" if busy
                     else f"PING {target} ({ip}) 56(84) bytes of data.")
            for i in range(3):
                seq = f"seq={i}" if busy else f"icmp_seq={i + 1}"
                io.print(f"64 bytes from {ip}: {seq} ttl=64 time=0.0{random.randint(4, 9)} ms")
            io.print(f"--- {target} ping statistics ---")
            io.print("3 packets transmitted, 3 packets received, 0% packet loss")
            world.flags["ping_ok"] = (name, peer)
        else:
            io.print(f"ping: bad address '{target}'" if _busybox(ctr["image"])
                     else f"ping: {target}: Name or service not known")
            io.print(c("(name resolution only exists on a USER-DEFINED network — and the target "
                       "has to be running. On the default bridge there is no DNS at all, which is "
                       "why the class always does `docker network create` first.)", "dim"))
            world.flags["_noop"] = True
    else:
        world.flags["_noop"] = True
        io.print(f"{prog}: not found — this tiny container shell knows: {CONTAINER_CMDS}")
        io.print(c("(you're INSIDE a container right now — `exit` brings you back to the host. "
                   "Real base images are nearly this bare: no editor, often no curl.)", "dim"))


# -------------------------------------------------------------- kubernetes --
K8S_KINDMAP = {
    "Namespace": "namespace", "Pod": "pod", "Deployment": "deployment.apps",
    "Service": "service", "ConfigMap": "configmap", "Secret": "secret",
    "ServiceAccount": "serviceaccount", "Role": "role.rbac.authorization.k8s.io",
    "RoleBinding": "rolebinding.rbac.authorization.k8s.io",
    "Ingress": "ingress.networking.k8s.io", "PersistentVolumeClaim": "persistentvolumeclaim",
    "HorizontalPodAutoscaler": "horizontalpodautoscaler.autoscaling",
    "NetworkPolicy": "networkpolicy.networking.k8s.io",
    "StatefulSet": "statefulset.apps", "DaemonSet": "daemonset.apps",
    "Job": "job.batch", "CronJob": "cronjob.batch",
    "CustomResourceDefinition": "customresourcedefinition.apiextensions.k8s.io",
    "ServiceMonitor": "servicemonitor.monitoring.coreos.com",
    "PodMonitor": "podmonitor.monitoring.coreos.com",
    "Prometheus": "prometheus.monitoring.coreos.com",
    "PrometheusRule": "prometheusrule.monitoring.coreos.com",
    "Alertmanager": "alertmanager.monitoring.coreos.com",
    "Probe": "probe.monitoring.coreos.com",
}


def _api_plural(kind):
    """`Deployment` → `deployments.apps`: how the API server names a kind in an
    error message — plural resource first, API group after."""
    name = K8S_KINDMAP.get(kind, kind.lower())
    head, _, group = name.partition(".")
    if head.endswith("y"):
        head = head[:-1] + "ies"
    elif head.endswith(("s", "x", "ch", "sh")):
        head += "es"
    else:
        head += "s"
    return head + ("." + group if group else "")


K8S_ALIASES = {
    "po": "pods", "pod": "pods", "pods": "pods",
    "deploy": "deployments", "deployment": "deployments", "deployments": "deployments",
    "svc": "services", "service": "services", "services": "services",
    "ns": "namespaces", "namespace": "namespaces", "namespaces": "namespaces",
    "no": "nodes", "node": "nodes", "nodes": "nodes",
    "rs": "rs", "replicaset": "rs", "replicasets": "rs",
    "cm": "configmap", "configmap": "configmap", "configmaps": "configmap",
    "secret": "secret", "secrets": "secret",
    "sa": "serviceaccount", "serviceaccount": "serviceaccount", "serviceaccounts": "serviceaccount",
    "role": "role", "roles": "role",
    "rolebinding": "rolebinding", "rolebindings": "rolebinding",
    "ingress": "ingress", "ingresses": "ingress", "ing": "ingress",
    "pvc": "pvc", "persistentvolumeclaim": "pvc", "persistentvolumeclaims": "pvc",
    "ep": "endpoints", "endpoint": "endpoints", "endpoints": "endpoints",
    "ev": "events", "event": "events", "events": "events",
    # CRDs themselves are built in (apiextensions.k8s.io) — it is the kinds they
    # DEFINE that only exist after they are applied. See K8S_CRD_KINDS.
    "crd": "crd", "crds": "crd", "customresourcedefinition": "crd",
    "customresourcedefinitions": "crd",
    "hpa": "hpa", "horizontalpodautoscaler": "hpa", "horizontalpodautoscalers": "hpa",
    "netpol": "networkpolicy", "networkpolicy": "networkpolicy",
    "networkpolicies": "networkpolicy",
    "sts": "statefulset", "statefulset": "statefulset", "statefulsets": "statefulset",
    "ds": "daemonset", "daemonset": "daemonset", "daemonsets": "daemonset",
    "job": "job", "jobs": "job", "cj": "cronjob", "cronjob": "cronjob", "cronjobs": "cronjob",
    "all": "all",
}

# The kinds `kubectl get <alias>` can list, mapped to the manifest kind they
# store under `k8s["objects"]`. Anything applied from a manifest lands there, so
# a multi-doc file's ConfigMap/Secret/PVC/Ingress all come back out of `get`.
K8S_OBJECT_KINDS = {
    "configmap": "ConfigMap", "secret": "Secret", "ingress": "Ingress",
    "pvc": "PersistentVolumeClaim", "hpa": "HorizontalPodAutoscaler",
    "networkpolicy": "NetworkPolicy", "statefulset": "StatefulSet",
    "daemonset": "DaemonSet", "job": "Job", "cronjob": "CronJob",
    "crd": "CustomResourceDefinition",
    "servicemonitor": "ServiceMonitor", "podmonitor": "PodMonitor",
    "prometheus": "Prometheus", "prometheusrule": "PrometheusRule",
    "alertmanager": "Alertmanager", "probe": "Probe",
}

# Kinds that live outside namespaces: `-n` never narrows them.
K8S_CLUSTER_SCOPED = {"crd"}

# CRD-backed kinds — real Kubernetes words that DO NOT EXIST until a
# CustomResourceDefinition has taught the API server about them. The capstone's
# monitoring mission applies ten CRDs and then creates a ServiceMonitor, so both
# halves of that round trip have to be true: the refusal before, the listing
# after. Word → (the K8S_OBJECT_KINDS key, the CRD plural that defines it).
K8S_CRD_KINDS = {
    "servicemonitor": ("servicemonitor", "servicemonitors"),
    "servicemonitors": ("servicemonitor", "servicemonitors"),
    "smon": ("servicemonitor", "servicemonitors"),
    "podmonitor": ("podmonitor", "podmonitors"),
    "podmonitors": ("podmonitor", "podmonitors"),
    "prometheus": ("prometheus", "prometheuses"),
    "prometheuses": ("prometheus", "prometheuses"),
    "prometheusrule": ("prometheusrule", "prometheusrules"),
    "prometheusrules": ("prometheusrule", "prometheusrules"),
    "alertmanager": ("alertmanager", "alertmanagers"),
    "alertmanagers": ("alertmanager", "alertmanagers"),
    "probe": ("probe", "probes"),
    "probes": ("probe", "probes"),
}


def _kind_of(k, word):
    """`kubectl get <word>` → the kind this world lists, or the word unchanged.

    Built-in aliases resolve from the static table. A CRD-backed word resolves
    only once its CustomResourceDefinition is in the cluster — before that the
    API server really does not know the word, and answering `no resource type`
    is not a gap in the simulation, it is the lesson."""
    if word in K8S_ALIASES:
        return K8S_ALIASES[word]
    entry = K8S_CRD_KINDS.get(word)
    if entry:
        defined = {n.split(".")[0] for n, _ in k["objects"].get("CustomResourceDefinition", set())}
        if entry[1] in defined:
            return entry[0]
    return word

# What `kubectl delete <kind> <name>` can address by name, alias → manifest
# kind. Pods are handled separately (a deployment's pod has a generated name a
# player only ever half-types). Services are here because k8s-03 creates three
# of them and k8s-04's finale is a Service bug — delete-and-reapply is a real
# route out of it.
_DELETABLE = {"namespaces": "Namespace", "deployments": "Deployment",
              "services": "Service", "serviceaccount": "ServiceAccount",
              "role": "Role", "rolebinding": "RoleBinding"}
_DELETABLE.update(K8S_OBJECT_KINDS)


def _k8s_exists(k, kind, name, ns):
    """Is this object in the cluster? `ns=None` asks "in ANY namespace", which is
    how delete can tell "no such thing" from "not in the namespace you named"."""
    def here(o_ns):
        return ns is None or o_ns == ns
    if kind == "Namespace":
        return name in k["namespaces"]
    if kind == "Deployment":
        return name in k["deployments"] and here(k["deployments"][name].get("ns", "default"))
    if kind == "Service":
        return name in k["services"] and here(k["services"][name].get("ns", "default"))
    if kind == "ServiceAccount":
        return any(n == name and here(o_ns) for n, o_ns in k["rbac"]["sa"])
    if kind == "Role":
        return name in k["rbac"]["roles"] and here(k["rbac"]["roles"][name])
    if kind == "RoleBinding":
        return name in k["rbac"]["bindings"] and here(k["rbac"]["bindings"][name][2])
    return any(n == name and here(o_ns) for n, o_ns in k["objects"].get(kind, set()))


# Workload kinds this world stores but does not RUN: applying one really does
# create the object, and `get` really does list it, but no pods appear. Saying
# that out loud beats either hiding the object or faking its pods.
K8S_UNSIMULATED = {"StatefulSet", "DaemonSet", "Job", "CronJob"}

# `kubectl <verb>` this world doesn't simulate — same contract as
# DOCKER_UNSIMULATED: an unknown command still teaches. These are not obscure:
# `run` is the throwaway debug shell, `top` is how you check the limits you just
# wrote, `port-forward` is how you skip the routing layer, and the Day-2 mission
# recommends the first one in its own dim notes. Answering all of them with
# "not simulated (yet)" was a dead end in exactly the missions they belong to.
KUBECTL_UNSIMULATED = {
    "run": "starts ONE bare pod — `kubectl run tmp --rm -it --image=busybox -- sh` is the "
           "throwaway shell people debug DNS and Services from. Nothing owns it, so deleting it "
           "keeps it deleted. Here, exec into a pod you already have: kubectl exec -it <pod> -- sh",
    "top": "live CPU and memory per pod or node, read from metrics-server (on minikube: "
           "`minikube addons enable metrics-server` first, or every row says <unknown>). It is "
           "how you find out whether the requests and limits you wrote are the right numbers.",
    "port-forward": "tunnels a local port straight into a pod or Service — "
                    "`kubectl port-forward svc/demo-svc 8080:80 -n dev`, then curl "
                    "localhost:8080. It skips NodePort and Ingress entirely, which makes it the "
                    "fastest way to ask 'is the APP broken, or the routing?'",
    "label": "adds or changes labels on a live object: `kubectl label pod web-1 app=demo`. "
             "Labels are what Services select on, so relabelling a pod moves it in or out of a "
             "Service's endpoints instantly — powerful, and the reason a stray label is a "
             "production incident.",
    "annotate": "like label, but for metadata nothing selects on — ingress classes, "
                "kubectl.kubernetes.io/last-applied-configuration, tool hints.",
    "config": "reads and edits your KUBECONFIG, not the cluster: `kubectl config get-contexts` "
              "lists every cluster you can talk to and stars the current one, "
              "`kubectl config use-context minikube` switches. Running the right command "
              "against the wrong context is the classic outage.",
    "api-resources": "lists every kind this API server knows, with its short name, apiVersion "
                     "and whether it is namespaced. After installing a CRD it is how you learn "
                     "the new words — and `kubectl api-versions` prints the groups.",
    "api-versions": "the API groups this server serves, one per line — apps/v1, batch/v1, and "
                    "anything a CRD added.",
    "patch": "changes ONE field on a live object without an editor: "
             "`kubectl patch deploy web -p '{\"spec\":{\"replicas\":5}}'`. Handy in scripts, and "
             "invisible to Git — which is why GitOps people dislike it.",
    "edit": "opens the live object in $EDITOR and applies what you save. It is the fastest way "
            "to fix something and the fastest way to lose the fix: the next apply overwrites it.",
    "cp": "copies files in or out of a running container: kubectl cp <ns>/<pod>:/path ./here "
          "(needs tar inside the image).",
    "wait": "blocks until a condition is true — `kubectl wait --for=condition=Ready pod -l "
            "app=web --timeout=60s`. The line that makes a CI script honest instead of a sleep.",
    "attach": "reattaches your terminal to a container's MAIN process (exec starts a new one).",
    "proxy": "runs a local authenticated proxy to the API server on 127.0.0.1:8001 — curl the "
             "raw API without minting a token.",
    "debug": "attaches an ephemeral container to a running pod so you can poke at a distroless "
             "image that has no shell of its own: kubectl debug -it <pod> --image=busybox",
    "drain": "evicts every pod off a node before you take it down for maintenance "
             "(`cordon` only stops NEW pods from landing there).",
    "cordon": "marks a node unschedulable — no new pods, existing ones stay.",
    "taint": "the node's side of the scheduling contract: only pods with a matching toleration "
             "may land here.",
    "diff": "shows what `apply -f` WOULD change against the live cluster. The review step people "
            "skip, then regret.",
    "kustomize": "renders a kustomization.yaml overlay to stdout — `kubectl apply -k` applies it.",
    "autoscale": "creates an HPA imperatively: kubectl autoscale deploy web --min=2 --max=10 "
                 "--cpu-percent=80 (needs metrics-server, same as top).",
    "replace": "the destructive sibling of apply: it deletes and recreates the object from your "
               "file instead of merging into it.",
    "completion": "prints the shell completion script — `source <(kubectl completion bash)`, and "
                  "`alias k=kubectl` with `complete -o default -F __start_kubectl k`.",
}

# Docker Hub's newest tag per repository, as of this world's "today". A numeric
# tag ABOVE the ceiling does not exist — which is the entire point of the Day-2
# assignment's `nginx:1.9999`: the pull fails, the new pods never start, and the
# old ones keep serving. Repositories we don't know (private registries, ghcr.io)
# are assumed pullable: guessing "no" there would lie more than it teaches.
IMAGE_MAX_TAG = {
    "nginx": (1, 29), "httpd": (2, 4), "redis": (7, 4), "postgres": (17, 4),
    "mysql": (9, 1), "mongo": (8, 0), "python": (3, 13), "node": (22, 12),
    "golang": (1, 24), "openjdk": (25, 0), "alpine": (3, 21), "busybox": (1, 37),
    "ubuntu": (26, 4), "debian": (13, 0), "rabbitmq": (4, 1), "traefik": (3, 3),
    "haproxy": (3, 1), "memcached": (1, 6),
}


def _split_tag(image):
    """(repository, tag) — the colon in `registry:5000/app` is a port, not a tag,
    so only the last path segment gets to own one."""
    head, sep, last = image.rpartition("/")
    if ":" in last:
        name, tag = last.rsplit(":", 1)
        return head + sep + name, tag
    return image, "latest"


def image_exists(image):
    """Can this image:tag actually be pulled? Non-numeric tags (alpine, latest,
    bookworm) are names we can't second-guess; a numeric one is checked against
    the repository's real newest release."""
    repo, tag = _split_tag(image)
    ceiling = IMAGE_MAX_TAG.get(repo.rsplit("/", 1)[-1])
    if ceiling is None:
        return True
    m = re.match(r"^v?(\d+)(?:\.(\d+))?", tag)
    if not m:
        return True
    return (int(m.group(1)), int(m.group(2) or 0)) <= ceiling


# Docker Hub's OFFICIAL repositories — the ones whose name is a single word,
# because `nginx` really means `library/nginx`. A name with a slash is a user or
# a private registry and is unknowable from in here, so it is always accepted;
# a one-word name that is close to one of these but not one of them is a typo,
# and inventing a 382 MB image for a typo is how `docker run -d --name typo
# ngnix` used to succeed while k8s-04 was busy teaching that an unpublished
# reference fails late.
OFFICIAL_IMAGES = {
    "alpine", "busybox", "ubuntu", "debian", "fedora", "centos", "rockylinux", "almalinux",
    "nginx", "httpd", "caddy", "traefik", "haproxy", "varnish", "registry",
    "python", "node", "golang", "openjdk", "eclipse-temurin", "amazoncorretto", "ruby", "php",
    "rust", "perl", "gcc", "maven", "gradle", "composer", "dart", "elixir", "erlang", "haskell",
    "redis", "memcached", "postgres", "mysql", "mariadb", "mongo", "couchdb", "influxdb",
    "cassandra", "neo4j", "elasticsearch", "kibana", "logstash",
    "rabbitmq", "nats", "kafka", "zookeeper", "consul", "vault", "telegraf",
    "wordpress", "drupal", "joomla", "ghost", "nextcloud", "adminer", "phpmyadmin",
    "jenkins", "sonarqube", "gitea", "tomcat", "jetty", "wildfly",
    "hello-world", "docker", "buildpack-deps", "swarm", "solr", "kong",
}


def _repo_guess(world, image):
    """Is this repository real? Returns the official image it is probably a typo
    OF, `""` when the name is simply unknown, or None when it is fine.

    The distinction matters: refusing every unfamiliar name would block a
    student's own `myapp`, while accepting every name taught that Docker Hub
    contains whatever you type."""
    repo = _split_tag(image)[0]
    if "/" in repo or repo in OFFICIAL_IMAGES:
        return None
    if any(_split_tag(i)[0] == repo for i in world.images):
        return None                          # built or tagged right here
    close = difflib.get_close_matches(repo, sorted(OFFICIAL_IMAGES), n=1, cutoff=0.75)
    return close[0] if close else ""


def _pull_denied(world, io, image):
    """docker's own refusal for a repository that isn't there. True when it fired;
    prints the one-time caveat and returns False when the name is merely
    unfamiliar, because this world genuinely cannot check that one."""
    guess = _repo_guess(world, image)
    if guess is None:
        return False
    repo = _split_tag(image)[0]
    if guess:
        io.print(f"docker: Error response from daemon: pull access denied for {repo}, repository "
                 "does not exist or may require 'docker login': denied: requested access to the "
                 "resource is denied.")
        io.print(c(f"(there is no library/{repo} on Docker Hub — did you mean {guess}? A one-word "
                   "name is an OFFICIAL image; anything else has to be <user>/<repo>.)", "dim"))
        world.flags["_noop"] = True
        return True
    if not world.flags.get("_unverified_repo"):
        world.flags["_unverified_repo"] = True
        io.print(c(f"(heads up: this world accepted `{repo}` because it has never heard of it and "
                   "cannot check. A real daemon can, and answers 'pull access denied' — "
                   f"`docker search {repo}` is that check.)", "dim"))
    return False


def _norm_deploy(dname, d):
    """Fill a deployment's defaults. Missions hand-build these dicts (helm's
    release sync, argocd's sync, a mission's `world` spec), so normalising here
    beats a constructor only some callers go through."""
    d.setdefault("ns", "default")
    d.setdefault("replicas", 1)
    d.setdefault("image", "nginx")
    d.setdefault("revision", 1)
    d.setdefault("history", [d["image"]])
    d.setdefault("app", dname)          # the pod label a Service has to match
    d.setdefault("container", "app")
    d.setdefault("probes", {})
    d.setdefault("resources", {})
    d.setdefault("strategy", {})
    return d


def _pod_template_hash(dname, image):
    """The `pod-template-hash` a ReplicaSet is named after. It hashes the pod
    TEMPLATE, so changing the image really does produce a new ReplicaSet — which
    is why `kubectl get rs` grows a row after a rollout and why `rollout undo`
    has something to go back to."""
    return _stable_id(f"{dname}|{image}")[:9]


def _pod_ip(name):
    """Fallback address for a pod that predates IP allocation. Stable per name:
    an IP that changes every time you look at it teaches nothing, and
    `get endpoints` has to agree with `describe pod`."""
    return "10.244.0." + str(2 + int(_stable_id("ip:" + name)[:4], 16) % 250)


def _alloc_ip(world):
    """Hand out the next free address in the pod CIDR — the way a CNI does, and
    the reason two endpoints can never collide in the ENDPOINTS column."""
    used = {pd.get("ip") for pd in world.k8s["pods"].values()}
    return next((f"10.244.0.{n}" for n in range(2, 254) if f"10.244.0.{n}" not in used),
                "10.244.0.254")


def _max_surge(d):
    """How many EXTRA pods a RollingUpdate may add above `replicas`. The
    assignment pins maxSurge: 1 / maxUnavailable: 0; Kubernetes' own default
    (25%) rounds to the same 1 at three replicas — which is why a broken rollout
    parks exactly one new pod and never touches the ones serving traffic."""
    v = (d.get("strategy") or {}).get("maxSurge")
    if isinstance(v, str) and v.endswith("%"):
        v = -(-d["replicas"] * int(v[:-1]) // 100)
    if v is None:
        v = -(-d["replicas"] // 4)
    try:
        return max(1, int(v))
    except (TypeError, ValueError):
        return 1


def _readiness_ok(d):
    """A readiness probe aimed at a port nothing listens on never passes: the pod
    RUNS, stays 0/1, and is kept OUT of its Service's endpoints. That is the
    graded debugging challenge's third planted error, as an object instead of a
    paragraph."""
    probe = (d.get("probes") or {}).get("readiness")
    port = d.get("containerPort")
    if not probe or port is None:
        return True                     # nothing declared → nothing to fail
    pport = probe.get("port")
    if probe.get("kind") not in ("http", "tcp") or not isinstance(pport, int):
        return True                     # a NAMED port resolves via the container spec
    return pport == port


def _last_good_image(d):
    """The newest image in this deployment's history that can actually be pulled
    — i.e. what the pods still serving traffic are running."""
    for img in reversed(d.get("history", [])):
        if img != d["image"] and image_exists(img):
            return img
    return None


def _spawn_pod(world, dname, d, image, ready_ok):
    k = world.k8s
    ok = image_exists(image)
    pname = f"{dname}-{_pod_template_hash(dname, image)}-{_rand_id()[:5]}"
    k["pods"][pname] = {
        "ns": d["ns"], "deploy": dname, "image": image, "restarts": 0,
        "status": "Running" if ok else "ImagePullBackOff",
        "ready": bool(ok and ready_ok), "labels": {"app": d["app"]},
        "ip": _alloc_ip(world), "port": d.get("containerPort"),
    }
    return pname


def _fit(world, dname, d, image, want, ready_ok):
    """Make exactly `want` pods of this deployment run `image`."""
    k = world.k8s
    have = sorted(p for p, pd in k["pods"].items()
                  if pd.get("deploy") == dname and pd["image"] == image)
    while len(have) > want:
        del k["pods"][have.pop()]
    while len(have) < want:
        have.append(_spawn_pod(world, dname, d, image, ready_ok))


def _reconcile(world):
    """The control loop: make pod reality match each deployment's desired state.

    It is also where a broken release becomes visible. When the deployment's
    image cannot be pulled, the surge pod sticks in ImagePullBackOff and the pods
    on the last GOOD image are left completely alone — `maxUnavailable: 0`
    expressed as pods instead of prose. Nothing here is on a timer: the loop runs
    to completion on every command, which is the one liberty this sim takes.
    """
    k = world.k8s
    if not k:
        return
    for dname, d in k["deployments"].items():
        _norm_deploy(dname, d)
        ready_ok = _readiness_ok(d)
        target, want = d["image"], d["replicas"]
        good = target if image_exists(target) else _last_good_image(d)
        keep = {target, good} - {None}
        for p in [p for p, pd in k["pods"].items()
                  if pd.get("deploy") == dname and pd["image"] not in keep]:
            del k["pods"][p]
        if good == target:              # healthy: every pod is the new pod
            d["stuck"] = False
            _fit(world, dname, d, target, want, ready_ok)
        elif good is None:              # born broken — nothing was ever serving
            d["stuck"] = True
            _fit(world, dname, d, target, want, ready_ok)
        else:                           # stuck rollout: old serves, new sulks
            d["stuck"] = True
            _fit(world, dname, d, good, want, ready_ok)
            _fit(world, dname, d, target, _max_surge(d), ready_ok)
        if d["stuck"]:
            # d["stuck"] is the CURRENT state (undo clears it); the flags record
            # that it happened at all, which is what a mission wants to check.
            world.flags[f"imagepull_failed_{dname}"] = True
            world.flags["imagepullbackoff"] = True
    # garbage-collect pods whose deployment is gone
    for p in [p for p, pd in k["pods"].items()
              if pd.get("deploy") and pd["deploy"] not in k["deployments"]]:
        del k["pods"][p]


# ------------------------------------------------------------ manifest YAML --
def _yaml_block(text, key):
    """The lines nested under `key:` — indentation is the only structure a
    manifest has, so indentation is what we read. None when the key is absent."""
    if not text:
        return None
    m = re.search(r"(?m)^([ \t]*)" + re.escape(key) + r":[ \t]*(?:#.*)?$", text)
    if not m:
        return None
    indent = len(m.group(1))
    out = []
    for line in text[m.end():].splitlines():
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            break
        out.append(line)
    return "\n".join(out)


def _yaml_scalar(text, key, default=None):
    """A `key: value` one-liner with YAML's decorative quotes stripped."""
    if not text:
        return default
    m = re.search(r"(?m)^[ \t]*-?[ \t]*" + re.escape(key) + r":[ \t]*(\S.*?)[ \t]*$", text)
    return m.group(1).strip('"\'') if m else default


def _yaml_int(text, key, default):
    try:
        return int(_yaml_scalar(text, key, default))
    except (TypeError, ValueError):
        return default


def _dedent(text):
    """Drop blank edges and the common leading indent — a nested YAML block read
    back out of its parent has to print as a block, not as a ragged quote."""
    lines = [ln for ln in (text or "").splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        return ""
    pad = min(len(ln) - len(ln.lstrip()) for ln in lines if ln.strip())
    return "\n".join(ln[pad:] if ln.strip() else "" for ln in lines)


def _yaml_map(text):
    """A flat `key: value` block — which is exactly what a ConfigMap's or a
    Secret's `data:` is."""
    out = {}
    for line in (text or "").splitlines():
        m = re.match(r"^\s*([\w.\-/]+):\s*(.*?)\s*$", line)
        if m and m.group(2) and not m.group(2).startswith("#"):
            out[m.group(1)] = m.group(2).strip('"\'')
    return out


def _parse_probe(block):
    """A probe, in the shape `kubectl describe` prints it: handler then timings.
    The defaults are Kubernetes' own — the assignment's YAML writes none of them
    down, so a student who never sees `#failure=3` never learns it exists."""
    if block is None:
        return None
    p = {"delay": _yaml_int(block, "initialDelaySeconds", 0),
         "period": _yaml_int(block, "periodSeconds", 10),
         "timeout": _yaml_int(block, "timeoutSeconds", 1),
         "failure": _yaml_int(block, "failureThreshold", 3),
         "success": _yaml_int(block, "successThreshold", 1)}
    http, tcp = _yaml_block(block, "httpGet"), _yaml_block(block, "tcpSocket")
    if http is not None:
        p.update(kind="http", path=_yaml_scalar(http, "path", "/"),
                 port=_yaml_scalar(http, "port"))
    elif tcp is not None:
        p.update(kind="tcp", port=_yaml_scalar(tcp, "port"))
    else:
        p.update(kind="exec", cmd=_yaml_scalar(_yaml_block(block, "exec"), "command", "?"))
    if str(p.get("port", "")).isdigit():
        p["port"] = int(p["port"])
    return p


def _probe_line(p):
    """kubectl's own one-line probe rendering, verbatim in shape."""
    if p["kind"] == "http":
        head = f"http-get http://:{p.get('port', 80)}{p.get('path', '/')}"
    elif p["kind"] == "tcp":
        head = f"tcp-socket :{p.get('port', 80)}"
    else:
        head = f"exec {p.get('cmd', '?')}"
    return (f"{head} delay={p['delay']}s timeout={p['timeout']}s period={p['period']}s "
            f"#success={p['success']} #failure={p['failure']}")


def _parse_manifests(text):
    """Tiny YAML-ish reader — just enough for the course's manifests.

    It deliberately reads more than the tables need (probes, resources, strategy,
    data, HPA bounds): a field this parser drops is a field the game silently
    pretends the student never wrote, and `describe` has to hand back what was
    actually declared or it teaches nothing.
    """
    docs = []
    for chunk in re.split(r"(?m)^---\s*$", text):
        if not chunk.strip():
            continue
        kind = re.search(r"(?m)^kind:\s*([\w-]+)", chunk)
        if not kind:
            continue
        names = re.findall(r"(?m)^\s*name:\s*([\w.-]+)", chunk)
        ns = re.search(r"(?m)^\s*namespace:\s*([\w.-]+)", chunk)
        doc = {
            "kind": kind.group(1), "name": names[0] if names else "unnamed",
            "names": names, "ns": ns.group(1) if ns else "default",
        }
        api = re.search(r"(?m)^apiVersion:\s*(\S+)", chunk)
        if api:
            doc["apiVersion"] = api.group(1)
        meta = _yaml_block(chunk, "metadata")
        labels = _yaml_map(_yaml_block(meta, "labels")) if meta else {}
        if labels:
            # A trailing `# comment` is a comment, not part of the value — and
            # the course manifests annotate their labels heavily.
            clean = {key: re.sub(r"\s+#.*$", "", val) for key, val in labels.items()}
            doc["labels"] = ",".join(f"{key}={val}" for key, val in sorted(clean.items()))
        spec = _yaml_block(chunk, "spec")
        if spec is not None:
            # `describe` on a kind this world only STORES echoes its spec back;
            # keeping the source text is the honest way to do that, because
            # anything else would be this parser's opinion of the manifest.
            doc["raw"] = _dedent(spec)
        m = re.search(r"replicas:\s*(\d+)", chunk)
        if m:
            doc["replicas"] = int(m.group(1))
        m = re.search(r"(?m)^\s*image:\s*(\S+)", chunk)
        if m:
            doc["image"] = m.group(1)
        m = re.search(r"(?m)^\s*type:\s*([\w-]+)", chunk)
        if m:
            doc["type"] = m.group(1)
        m = re.search(r"nodePort:\s*(\d+)", chunk)
        if m:
            doc["nodePort"] = int(m.group(1))
        m = re.search(r"(?m)^\s*-?\s*port:\s*(\d+)", chunk)
        if m:
            doc["port"] = int(m.group(1))
        m = re.search(r"containerPort:\s*(\d+)", chunk)
        if m:
            doc["containerPort"] = int(m.group(1))
        # The pod template's own label is what a Service selector must match, so
        # prefer it over the first `app:` in the file (which is the selector).
        tmpl = _yaml_block(chunk, "template")
        label = _yaml_scalar(_yaml_block(tmpl, "labels"), "app") if tmpl else None
        m = re.search(r"(?m)^\s*app:\s*([\w.-]+)", chunk)
        if label or m:
            doc["app"] = label or m.group(1)
        cname = _yaml_scalar(_yaml_block(chunk, "containers"), "name")
        if cname:
            doc["container"] = cname
        res = _yaml_block(chunk, "resources")
        if res is not None:
            req, lim = _yaml_block(res, "requests"), _yaml_block(res, "limits")
            doc["resources"] = {side: vals for side, vals in
                                (("requests", _yaml_map(req)), ("limits", _yaml_map(lim))) if vals}
            # A PVC asks for storage through the very same block.
            if _yaml_scalar(req, "storage"):
                doc["storage"] = _yaml_scalar(req, "storage")
        probes = {name: _parse_probe(_yaml_block(chunk, name + "Probe"))
                  for name in ("liveness", "readiness", "startup")}
        probes = {n: p for n, p in probes.items() if p}
        if probes:
            doc["probes"] = probes
        strat = _yaml_block(chunk, "strategy")
        if strat is not None:
            ru = _yaml_block(strat, "rollingUpdate")
            doc["strategy"] = {"type": _yaml_scalar(strat, "type", "RollingUpdate"),
                               "maxSurge": _yaml_scalar(ru, "maxSurge"),
                               "maxUnavailable": _yaml_scalar(ru, "maxUnavailable")}
        data = _yaml_block(chunk, "data") or _yaml_block(chunk, "stringData")
        if data is not None:
            doc["data"] = _yaml_map(data)
        if doc["kind"] == "HorizontalPodAutoscaler":
            doc["minReplicas"] = _yaml_int(chunk, "minReplicas", 1)
            doc["maxReplicas"] = _yaml_int(chunk, "maxReplicas", 1)
            doc["target"] = _yaml_int(chunk, "averageUtilization", 80)
            doc["scaleTarget"] = _yaml_scalar(_yaml_block(chunk, "scaleTargetRef"), "name", "?")
        if doc["kind"] == "Ingress":
            doc["host"] = _yaml_scalar(chunk, "host", "*")
        modes = re.findall(r"(?m)^\s*-\s*(ReadWrite\w+|ReadOnlyMany)\s*$", chunk)
        if modes:
            doc["accessModes"] = modes
        docs.append(doc)
    # namespaces first so `apply -f .` doesn't trip over ordering
    docs.sort(key=lambda d: 0 if d["kind"] == "Namespace" else 1)
    return docs


def _apply_verb(k, kind, name, ns, doc, existed):
    """created / configured / unchanged — kubectl's own three answers. Re-applying
    an untouched file says `unchanged`, which is the proof that apply converges
    on a state instead of stacking up changes."""
    if not existed:
        return "created"
    return "unchanged" if k["spec"].get((kind, name, ns)) == doc else "configured"


def _k8s_apply_doc(world, doc, io, deleting=False):
    k = world.k8s
    kind, name, ns = doc["kind"], doc["name"], doc["ns"]
    label = f'{K8S_KINDMAP.get(kind, kind.lower())}/{name}'
    if deleting:
        label = f'{K8S_KINDMAP.get(kind, kind.lower())} "{name}"'
    spec = k.setdefault("spec", {})
    verb = None
    if kind == "Namespace":
        if deleting:
            # "Deleting a namespace nukes everything scoped inside it" is one of
            # the note's own gotchas, so it has to be true here as well — every
            # collection, not just the ones with an obvious table.
            k["namespaces"].discard(name)
            for coll in ("deployments", "services"):
                for n in [n for n, o in k[coll].items() if o.get("ns") == name]:
                    del k[coll][n]
            for n in [n for n, o in k["pods"].items() if o.get("ns") == name]:
                del k["pods"][n]
            for objs in k["objects"].values():
                objs -= {(n, o_ns) for (n, o_ns) in objs if o_ns == name}
            k["rbac"]["sa"] -= {(n, o_ns) for (n, o_ns) in k["rbac"]["sa"] if o_ns == name}
            for n in [n for n, o_ns in k["rbac"]["roles"].items() if o_ns == name]:
                del k["rbac"]["roles"][n]
            for n in [n for n, (_, _, o_ns) in k["rbac"]["bindings"].items() if o_ns == name]:
                del k["rbac"]["bindings"][n]
            for key in [key for key in spec if key[2] == name]:
                del spec[key]
        else:
            verb = _apply_verb(k, kind, name, ns, doc, name in k["namespaces"])
            k["namespaces"].add(name)
            spec[(kind, name, ns)] = doc
            io.print(f"{label} {'unchanged' if verb != 'created' else 'created'}")
            return
    elif kind == "Pod":
        if deleting:
            k["pods"].pop(name, None)
        else:
            if ns not in k["namespaces"]:
                io.print(f'Error from server (NotFound): namespaces "{ns}" not found')
                io.print(c("(create the namespace first — it has its own YAML)", "dim"))
                return
            verb = _apply_verb(k, kind, name, ns, doc, name in k["pods"])
            img = doc.get("image", "nginx")
            ok = image_exists(img)
            k["pods"][name] = {"ns": ns, "deploy": None, "image": img, "restarts": 0,
                               "status": "Running" if ok else "ImagePullBackOff",
                               "ready": ok, "labels": {"app": doc.get("app", name)},
                               "ip": _alloc_ip(world), "port": doc.get("containerPort")}
            spec[(kind, name, ns)] = doc
            io.print(f"{label} {verb}")
            return
    elif kind == "Deployment":
        if deleting:
            k["deployments"].pop(name, None)
            spec.pop((kind, name, ns), None)
            _reconcile(world)
        else:
            if ns not in k["namespaces"]:
                io.print(f'Error from server (NotFound): namespaces "{ns}" not found')
                io.print(c("(create the namespace first — it has its own YAML)", "dim"))
                return
            prev = k["deployments"].get(name, {})
            verb = _apply_verb(k, kind, name, ns, doc, bool(prev))
            img = doc.get("image", prev.get("image", "nginx"))
            hist = list(prev.get("history", [])) or [img]
            rev = prev.get("revision", 1)
            if img != hist[-1]:
                # apply IS a rollout when the template changed — the same code
                # path as `set image`, which is why re-applying an edited file
                # can break a live app just as thoroughly.
                hist.append(img)
                rev += 1
            d = dict(prev)
            d.update({"ns": ns, "image": img, "revision": rev, "history": hist,
                      "replicas": doc.get("replicas", prev.get("replicas", 1)),
                      "app": doc.get("app", prev.get("app", name)),
                      "container": doc.get("container", prev.get("container", "app")),
                      "containerPort": doc.get("containerPort", prev.get("containerPort")),
                      "probes": doc.get("probes", {}), "resources": doc.get("resources", {}),
                      "strategy": doc.get("strategy", {})})
            k["deployments"][name] = _norm_deploy(name, d)
            spec[(kind, name, ns)] = doc
            _reconcile(world)
            io.print(f"{label} {verb}")
            return
    elif kind == "Service":
        if deleting:
            k["services"].pop(name, None)
            spec.pop((kind, name, ns), None)
        else:
            verb = _apply_verb(k, kind, name, ns, doc, name in k["services"])
            # `app` is the SELECTOR, not a name: a Service finds pods by label and
            # nothing else. Point it at a label no pod carries and you get a
            # Service with zero endpoints — the note's own #1 gotcha.
            svc = {"ns": ns, "type": doc.get("type", "ClusterIP"),
                   "port": doc.get("port", 80), "app": doc.get("app", name),
                   "clusterIP": _svc_ip(name + "/" + ns)}
            if svc["type"] in ("NodePort", "LoadBalancer"):
                svc["nodePort"] = doc.get("nodePort") or random.randint(30000, 32767)
            k["services"][name] = svc
            spec[(kind, name, ns)] = doc
            io.print(f"{label} {verb}")
            return
    elif kind == "ServiceAccount":
        verb = _apply_verb(k, kind, name, ns, doc, (name, ns) in k["rbac"]["sa"])
        (k["rbac"]["sa"].discard if deleting else k["rbac"]["sa"].add)((name, ns))
    elif kind == "Role":
        verb = _apply_verb(k, kind, name, ns, doc, name in k["rbac"]["roles"])
        if deleting:
            k["rbac"]["roles"].pop(name, None)
        else:
            k["rbac"]["roles"][name] = ns
    elif kind == "RoleBinding":
        verb = _apply_verb(k, kind, name, ns, doc, name in k["rbac"]["bindings"])
        if deleting:
            k["rbac"]["bindings"].pop(name, None)
        else:
            names = doc.get("names", [])
            sa = names[1] if len(names) > 1 else "?"
            role = names[2] if len(names) > 2 else "?"
            k["rbac"]["bindings"][name] = (role, sa, ns)
    else:  # ConfigMap, Secret, Ingress, PVC, HPA, NetworkPolicy, …
        coll = k["objects"].setdefault(kind, set())
        verb = _apply_verb(k, kind, name, ns, doc, (name, ns) in coll)
        (coll.discard if deleting else coll.add)((name, ns))
    if deleting:
        spec.pop((kind, name, ns), None)
    else:
        spec[(kind, name, ns)] = doc
    io.print(f"{label} {'deleted' if deleting else (verb or 'created')}")


def _pods_in(world, ns, all_ns=False):
    return {p: d for p, d in world.k8s["pods"].items() if all_ns or d["ns"] == ns}


def _svc_ip(seed):
    """A ClusterIP that stays put. It used to be re-rolled on every `get svc`,
    which made the one column students are told to compare against `get ep`
    unreadable."""
    n = int(_stable_id("svc:" + seed), 16)
    return f"10.{96 + n % 16}.{n // 16 % 256}.{2 + n // 4096 % 253}"


def _pod_ready(pd):
    return pd.get("status") == "Running" and pd.get("ready", True)


def _endpoints_for(world, svc):
    """The pods a Service actually reaches: same namespace, matching label, READY.
    Everything the note warns about — a selector typo, a pod stuck pulling, a
    readiness probe on the wrong port — shows up here as a missing row."""
    return sorted(
        f"{pd.get('ip') or _pod_ip(p)}:{svc.get('port', 80)}"
        for p, pd in world.k8s["pods"].items()
        if pd["ns"] == svc.get("ns", "default")
        and (pd.get("labels") or {}).get("app") == svc.get("app")
        and _pod_ready(pd))


def _find_pod(world, name, ns):
    """Exact pod name, or unique prefix (so scripts can say `logs frontend`)."""
    pods = world.k8s["pods"]
    if name in pods:
        return name
    matches = sorted(p for p, d in pods.items() if p.startswith(name) and d["ns"] == ns)
    return matches[0] if matches else None


def _extract_ns(args):
    ns, all_ns, rest = "default", False, []
    i = 0
    while i < len(args):
        if args[i] in ("-n", "--namespace") and i + 1 < len(args):
            ns = args[i + 1]; i += 2; continue
        if args[i] in ("-A", "--all-namespaces"):
            all_ns = True; i += 1; continue
        rest.append(args[i]); i += 1
    return ns, all_ns, rest


def _extract_out(args):
    """Pull `-o wide` / `-o jsonpath={…}` / `--output=yaml` out of the argument
    list so what's left is resource types and names."""
    out, rest = None, []
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-o", "--output") and i + 1 < len(args):
            out = args[i + 1]; i += 2; continue
        if a.startswith("-o=") or a.startswith("--output="):
            out = a.split("=", 1)[1]; i += 1; continue
        if a.startswith("-o") and len(a) > 2 and not a.startswith("-o-"):
            out = a[2:]; i += 1; continue
        rest.append(a); i += 1
    return out, rest


# ------------------------------------------------------- kubectl get tables --
class _Buffer:
    """A stand-in for `io` that collects lines instead of printing them, so
    `get all` can skip the resource groups that turn out to be empty (and the
    blank line that would otherwise separate nothing from nothing)."""

    def __init__(self):
        self.lines = []

    def print(self, *args):
        self.lines.append(" ".join(str(a) for a in args))


def _table(io, headers, rows):
    """Print the way kubectl does: every column as wide as its widest cell, three
    spaces between. Fixed widths used to smear the moment a pod went
    ImagePullBackOff (16 characters into a 10-wide STATUS column)."""
    widths = [max([len(h)] + [len(str(r[i])) for r in rows]) for i, h in enumerate(headers)]
    io.print("   ".join(h.ljust(w) for h, w in zip(headers, widths)).rstrip())
    for r in rows:
        io.print("   ".join(str(v).ljust(w) for v, w in zip(r, widths)).rstrip())


def _pick(mapping, ns, all_ns, only, ns_of=lambda v: v.get("ns", "default")):
    """The rows of one resource table: namespace-scoped, optionally one name."""
    return {n: v for n, v in sorted(mapping.items())
            if (all_ns or ns_of(v) == ns) and (not only or n == only or n.startswith(only))}


def _empty(io, resource, ns, only):
    """kubectl has two ways of saying nothing: an empty namespace is a note, a
    name that doesn't exist is an error. The difference matters when you're
    debugging a typo. `ns=None` is a cluster-scoped kind, which has no
    namespace to name."""
    if only:
        io.print(f'Error from server (NotFound): {resource} "{only}" not found')
    elif ns is None:
        io.print("No resources found")
    else:
        io.print(f"No resources found in {ns} namespace.")


def _t_pods(world, io, ns, all_ns, only=None, prefix="", wide=False):
    pods = _pick(_pods_in(world, ns, all_ns), ns, True, only)
    if not pods:
        return False
    head = (["NAMESPACE"] if all_ns else []) + ["NAME", "READY", "STATUS", "RESTARTS", "AGE"]
    head += ["IP", "NODE"] if wide else []
    rows = []
    for p, d in pods.items():
        row = ([d["ns"]] if all_ns else []) + [
            prefix + p, "1/1" if _pod_ready(d) else "0/1", d["status"],
            str(d.get("restarts", 0)), "42s"]
        rows.append(row + ([d.get("ip") or _pod_ip(p), "minikube"] if wide else []))
    _table(io, head, rows)
    world.flags["get_pods"] = True
    return True


def _ns_col(all_ns, head, rows, ns_of):
    """`-A` adds a NAMESPACE column — a list of names with no namespace beside
    them is a list you cannot act on. `ns_of(i)` gives row i's namespace."""
    if not all_ns:
        return head, rows
    return ["NAMESPACE"] + head, [[ns_of(i)] + r for i, r in enumerate(rows)]


def _t_deployments(world, io, ns, all_ns, only=None, prefix=""):
    deps = _pick(world.k8s["deployments"], ns, all_ns, only)
    if not deps:
        return False
    rows = []
    for n, d in deps.items():
        pods = [pd for pd in world.k8s["pods"].values() if pd.get("deploy") == n]
        ready = sum(1 for pd in pods if _pod_ready(pd))
        # UP-TO-DATE counts pods already on the NEW template — during a broken
        # rollout it sits at 1 while READY stays 3/3. That gap is the lesson.
        updated = sum(1 for pd in pods if pd["image"] == d["image"])
        rows.append([prefix + n, f"{ready}/{d['replicas']}", updated, ready, "42s"])
    order = list(deps.values())
    _table(io, *_ns_col(all_ns, ["NAME", "READY", "UP-TO-DATE", "AVAILABLE", "AGE"], rows,
                        lambda i: order[i].get("ns", "default")))
    world.flags["get_deployments"] = True
    return True


def _t_services(world, io, ns, all_ns, only=None, prefix=""):
    rows = []
    if (ns == "default" or all_ns) and not only:
        rows.append([prefix + "kubernetes", "ClusterIP", "10.96.0.1", "<none>", "443/TCP", "5m"])
    where = {"kubernetes": "default"}
    for n, s in _pick(world.k8s["services"], ns, all_ns, only).items():
        where[n] = s.get("ns", "default")
        ports = f"{s['port']}:{s['nodePort']}/TCP" if s.get("nodePort") else f"{s['port']}/TCP"
        ext = "<pending>" if s["type"] == "LoadBalancer" else "<none>"
        rows.append([prefix + n, s["type"], s.get("clusterIP") or _svc_ip(n), ext, ports, "42s"])
    if not rows:
        return False
    rows.sort(key=lambda r: r[0])       # kubectl sorts by name, built-ins included
    _table(io, *_ns_col(all_ns, ["NAME", "TYPE", "CLUSTER-IP", "EXTERNAL-IP", "PORT(S)", "AGE"],
                        rows, lambda i: where[rows[i][0].split("/")[-1]]))
    world.flags["get_services"] = True
    return True


def _t_rs(world, io, ns, all_ns, only=None, prefix=""):
    """ReplicaSets, derived rather than stored: one per image a deployment has
    worn. The old one lingering at DESIRED 0 is exactly what `rollout undo`
    scales back up."""
    rows = []
    where = []
    for n, d in _pick(world.k8s["deployments"], ns, all_ns, only).items():
        for img in dict.fromkeys(d.get("history", [d["image"]])):
            pods = [pd for pd in world.k8s["pods"].values()
                    if pd.get("deploy") == n and pd["image"] == img]
            where.append(d.get("ns", "default"))
            rows.append([f"{prefix}{n}-{_pod_template_hash(n, img)}", len(pods), len(pods),
                         sum(1 for pd in pods if _pod_ready(pd)), "42s"])
    if not rows:
        return False
    _table(io, *_ns_col(all_ns, ["NAME", "DESIRED", "CURRENT", "READY", "AGE"], rows,
                        lambda i: where[i]))
    world.flags["get_rs"] = True
    return True


def _t_endpoints(world, io, ns, all_ns, only=None):
    """`kubectl get endpoints` — the answer to "the Service exists, so why 503?"."""
    rows = []
    where = {"kubernetes": "default"}
    if (ns == "default" or all_ns) and not only:
        rows.append(["kubernetes", "192.168.49.2:8443", "5m"])
    for n, s in _pick(world.k8s["services"], ns, all_ns, only).items():
        where[n] = s.get("ns", "default")
        eps = _endpoints_for(world, s)
        shown = ",".join(eps[:3]) + (f" + {len(eps) - 3} more..." if len(eps) > 3 else "")
        rows.append([n, shown or "<none>", "42s"])
    if not rows:
        return False
    rows.sort(key=lambda r: r[0])
    _table(io, *_ns_col(all_ns, ["NAME", "ENDPOINTS", "AGE"], rows,
                        lambda i: where[rows[i][0]]))
    world.flags["get_endpoints"] = True
    return True


def _t_hpa(world, io, ns, all_ns, only=None, prefix=""):
    rows = []
    for (name, o_ns) in sorted(world.k8s["objects"].get("HorizontalPodAutoscaler", set())):
        if not (all_ns or o_ns == ns) or (only and not name.startswith(only)):
            continue
        spec = world.k8s.get("spec", {}).get(("HorizontalPodAutoscaler", name, o_ns), {})
        ref = spec.get("scaleTarget", "?")
        reps = world.k8s["deployments"].get(ref, {}).get("replicas", 0)
        # No metrics-server in this cluster, so utilisation reads <unknown> — the
        # exact symptom of the assignment's "did you set resources.requests?".
        cur = "<unknown>" if not world.k8s["deployments"].get(ref, {}).get("resources", {}).get("requests") else "12%"
        rows.append([prefix + name, f"Deployment/{ref}",
                     f"cpu: {cur}/{spec.get('target', 80)}%",
                     spec.get("minReplicas", 1), spec.get("maxReplicas", 1), reps, "42s"])
    if not rows:
        return False
    _table(io, ["NAME", "REFERENCE", "TARGETS", "MINPODS", "MAXPODS", "REPLICAS", "AGE"], rows)
    if any("<unknown>" in str(r[2]) for r in rows):
        io.print(c("(<unknown> means the HPA cannot compute utilisation: a percentage of WHAT? "
                   "Without resources.requests.cpu on the containers there is no denominator, so "
                   "it never scales. metrics-server has to be running too.)", "dim"))
    world.flags["get_hpa"] = True
    return True


def _iso_stamp():
    """The RFC-3339 UTC timestamp kubectl prints in a CREATED AT column."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _t_kind(kind):
    """Bind one object kind to the group-renderer signature `get all` expects."""
    def render(world, io, ns, all_ns, only=None, prefix=""):
        return _t_objects(world, io, kind, ns, all_ns, only, prefix)
    return render


def _t_objects(world, io, kind, ns, all_ns, only=None, prefix=""):
    """ConfigMap / Secret / Ingress / PVC / NetworkPolicy and the workload kinds
    this world stores but doesn't run — each with the columns real kubectl gives
    it, because the DATA count is the only proof that a multi-document apply
    actually read the keys inside."""
    pretty = K8S_OBJECT_KINDS[kind]
    spec = world.k8s.get("spec", {})
    found = sorted((n, o_ns) for (n, o_ns) in world.k8s["objects"].get(pretty, set())
                   if (all_ns or o_ns == ns) and (not only or n.startswith(only)))
    if not found:
        return False
    rows = []
    for n, o_ns in found:
        s = spec.get((pretty, n, o_ns), {})
        if kind == "configmap":
            rows.append([prefix + n, len(s.get("data", {})), "42s"])
        elif kind == "secret":
            rows.append([prefix + n, s.get("type", "Opaque"), len(s.get("data", {})), "42s"])
        elif kind == "pvc":
            rows.append([prefix + n, "Bound", "pvc-" + _stable_id(n)[:12], s.get("storage", "1Gi"),
                         "RWO" if "ReadWriteOnce" in s.get("accessModes", ["ReadWriteOnce"]) else "RWX",
                         "standard", "42s"])
        elif kind == "ingress":
            rows.append([prefix + n, "<none>", s.get("host", "*"), "", "80", "42s"])
        elif kind == "statefulset":
            reps = s.get("replicas", 1)
            rows.append([prefix + n, f"0/{reps}", "42s"])
        elif kind == "daemonset":
            rows.append([prefix + n, 1, 0, 0, 0, 0, "<none>", "42s"])
        elif kind == "crd":
            rows.append([prefix + n, _iso_stamp()])
        else:
            rows.append([prefix + n, "42s"])
    if all_ns and kind not in K8S_CLUSTER_SCOPED:
        # `-A` without a NAMESPACE column is a list of names you can't act on.
        rows = [[o_ns] + row for (_, o_ns), row in zip(found, rows)]
    heads = {"configmap": ["NAME", "DATA", "AGE"],
             "crd": ["NAME", "CREATED AT"],
             "secret": ["NAME", "TYPE", "DATA", "AGE"],
             "pvc": ["NAME", "STATUS", "VOLUME", "CAPACITY", "ACCESS MODES", "STORAGECLASS", "AGE"],
             "ingress": ["NAME", "CLASS", "HOSTS", "ADDRESS", "PORTS", "AGE"],
             "statefulset": ["NAME", "READY", "AGE"],
             "daemonset": ["NAME", "DESIRED", "CURRENT", "READY", "UP-TO-DATE", "AVAILABLE",
                           "NODE SELECTOR", "AGE"]}
    head = heads.get(kind, ["NAME", "AGE"])
    if all_ns and kind not in K8S_CLUSTER_SCOPED:
        head = ["NAMESPACE"] + head
    _table(io, head, rows)
    world.flags[f"get_{kind}"] = True
    if kind == "ingress":
        io.print(c("(ADDRESS is empty because no Ingress CONTROLLER is installed — the object "
                   "exists, nothing routes. `minikube addons enable ingress` is the real fix.)", "dim"))
    if pretty in K8S_UNSIMULATED:
        io.print(c(f"(the {pretty} object is real here, its pods are not — this world only runs "
                   "Deployment pods. On a cluster those READY columns would fill in.)", "dim"))
    return True


def _find_object(world, pretty, name, ns, all_ns=False):
    """(name, namespace) of a plain object, or None."""
    return next(((n, o) for (n, o) in sorted(world.k8s["objects"].get(pretty, set()))
                 if n == name and (all_ns or o == ns)), None)


def _k8s_jsonpath(world, io, kind, name, path, ns, all_ns=False):
    """`-o jsonpath` over the fields the course actually asks for.

    Real kubectl prints the value with no trailing newline and exits 0 even when
    the path matches nothing — which is precisely how the assignment's
    `secretKeyRef`-to-a-missing-key bug manages to hide.
    """
    k = world.k8s
    expr = path.strip().strip("'\"")
    if expr.startswith("{") and expr.endswith("}"):
        expr = expr[1:-1]
    expr = expr.lstrip(".")
    world.flags["jsonpath"] = True
    if kind in ("secret", "configmap"):
        pretty = K8S_OBJECT_KINDS[kind]
        hit = name and _find_object(world, pretty, name, ns, all_ns)
        if not hit:
            io.print(f'Error from server (NotFound): {kind}s "{name}" not found')
            return
        data = k.get("spec", {}).get((pretty, hit[0], hit[1]), {}).get("data", {})
        if expr in ("data", ""):
            io.print(json.dumps(data))
            return
        if expr.startswith("data."):
            key = expr.split(".", 1)[1]
            if key not in data:
                io.print(c(f"(empty — this {pretty} has no key '{key}'. kubectl prints nothing and "
                           f"exits 0, so a typo'd key looks like a working command. Keys that DO "
                           f"exist: {', '.join(sorted(data)) or '<none>'})", "dim"))
                return
            io.print(data[key])
            if pretty == "Secret":
                world.flags["jsonpath_secret"] = True
                io.print(c("(base64, not ciphertext — `| base64 -d` turns it back into the password "
                           "in one step. Encoding is not encryption; RBAC is what guards a Secret.)", "dim"))
            return
    if expr.startswith("items[*]") or expr.startswith("items[?"):
        rows = sorted(_pods_in(world, ns, all_ns)) if kind == "pods" else \
            sorted(n for n, v in world.k8s["deployments"].items()
                   if all_ns or v["ns"] == ns) if kind == "deployments" else \
            sorted(n for n, v in world.k8s["services"].items() if all_ns or v["ns"] == ns)
        io.print(" ".join(rows))
        return
    if kind == "deployments" and name in k["deployments"]:
        d = k["deployments"][name]
        value = {"spec.replicas": d["replicas"],
                 "spec.template.spec.containers[0].image": d["image"],
                 "status.replicas": d["replicas"]}.get(expr)
        if value is not None:
            io.print(value)
            return
    if kind == "pods":
        real = name and _find_pod(world, name, ns)
        pod = k["pods"].get(real, {})
        value = {"status.podIP": pod.get("ip"), "status.phase": pod.get("status"),
                 "spec.containers[0].image": pod.get("image")}.get(expr)
        if value is not None:
            io.print(value)
            return
    io.print(c(f"(jsonpath '{expr}' isn't simulated. What is: {{.data.<key>}} on a ConfigMap or "
               "Secret, {.items[*].metadata.name}, {.spec.replicas}, {.status.podIP}. "
               "On a real cluster `kubectl get <res> -o json` shows every path there is.)", "dim"))
    world.flags["_noop"] = True


def _k8s_yaml(world, io, kind, name, ns):
    """`-o yaml` for the two objects whose *contents* are the lesson."""
    if kind not in ("configmap", "secret") or not name:
        io.print(c("(-o yaml is only simulated for ConfigMaps and Secrets, where the stored value "
                   "IS the point. Everything else: use describe, or -o json on a real cluster.)", "dim"))
        world.flags["_noop"] = True
        return
    pretty = K8S_OBJECT_KINDS[kind]
    hit = _find_object(world, pretty, name, ns)
    if not hit:
        io.print(f'Error from server (NotFound): {kind}s "{name}" not found')
        return
    doc = world.k8s.get("spec", {}).get((pretty, hit[0], hit[1]), {})
    io.print("apiVersion: v1")          # kubectl sorts the top-level keys
    io.print("data:")
    for key, val in sorted(doc.get("data", {}).items()):
        io.print(f"  {key}: {val}")
    io.print(f"kind: {pretty}\nmetadata:\n  name: {hit[0]}\n  namespace: {hit[1]}")
    if pretty == "Secret":
        io.print(f"type: {doc.get('type', 'Opaque')}")
        io.print(c("(every value there is base64. `-o yaml` on a Secret hands the whole thing to "
                   "anyone who can read it — that is why Secrets need RBAC, not just a name.)", "dim"))
    world.flags[f"yaml_{kind}"] = True


def _pipe_through(io, lines, filt):
    """The one pipeline the class actually types: `… -o jsonpath=… | base64 -d`.
    Without it the Secret drill dead-ends at the encoded string, which is the
    half of the lesson everybody already believes."""
    if filt[:1] == ["base64"] and any(f in ("-d", "-D", "--decode") for f in filt[1:]):
        # Colour-coded lines are the sim's own commentary, not kubectl's stdout.
        payload = next((ln for ln in lines if ln and not ln.startswith("\033")), "")
        try:
            io.print(base64.b64decode(payload + "=" * (-len(payload) % 4)).decode("utf-8", "replace"))
        except Exception:
            io.print("base64: invalid input")
            io.print(c("(that wasn't base64 — check the jsonpath actually returned a value)", "dim"))
            return
        for ln in lines:
            if ln.startswith("\033"):
                io.print(ln)
        return
    io.print("\n".join(lines))
    io.print(c(f"(kubectl's output isn't piped here — `| {' '.join(filt)}` was ignored. The one "
               "pipeline this world runs is `| base64 -d`; on a real shell every pipe works.)", "dim"))


# -------------------------------------------------------------- kubectl exec --
def _resolve_in_cluster(world, host, ns):
    """Cluster DNS the way the deck describes it: pods resolve Services BY NAME —
    `<svc>`, `<svc>.<ns>`, `<svc>.<ns>.svc.cluster.local` — plus raw pod IPs."""
    k = world.k8s
    for pname, pd in k["pods"].items():
        if host in (pname, pd.get("ip") or _pod_ip(pname)):
            return ("pod", pname)
    parts = host.split(".")
    svc = k["services"].get(parts[0])
    if svc and svc["ns"] == (parts[1] if len(parts) > 1 else ns):
        return ("service", parts[0])
    return None


def _serve(pod, pd):
    """What an HTTP GET / gets back. nginxdemos/hello is the image the graded CLI
    assignment picks precisely because it names the pod that answered — which is
    how you SEE a Service load-balancing instead of being told it does."""
    image = pd.get("image", "")
    if "nginxdemos" in image or "hello" in image:
        return (f"<h1>Server address: {pd.get('ip')}:80</h1>\n"
                f"<h1>Server name: {pod}</h1>\n"
                "<p>Date: 17/Aug/2026:09:12:44 +0000 · URI: /</p>")
    if "nginx" in image or "httpd" in image:
        return NGINX_HTML
    return None


def _cluster_http(world, io, url, ns, tool="curl"):
    """A request from inside the cluster. Every failure mode here is a lesson the
    note names: no endpoints, wrong port, a name DNS never heard of."""
    k = world.k8s
    body = url.split("://", 1)[-1].split("/", 1)[0]
    host, _, port = body.partition(":")
    port = int(port) if port.isdigit() else 80
    hit = _resolve_in_cluster(world, host, ns)
    if hit is None:
        io.print(f"{tool}: (6) Could not resolve host: {host}" if tool == "curl"
                 else f"wget: bad address '{host}'")
        io.print(c("(cluster DNS only knows Services and pods in this cluster. Check the name and "
                   "the namespace: a Service in another namespace needs <svc>.<namespace>.)", "dim"))
        return
    what, name = hit
    if what == "service":
        svc = k["services"][name]
        eps = _endpoints_for(world, svc)
        if not eps or port != svc.get("port", 80):
            io.print(f"{tool}: (7) Failed to connect to {host} port {port} after 1 ms: "
                     "Connection refused")
            io.print(c(f"(the Service object exists — connecting is a different question. "
                       f"`kubectl get ep {name}` and `kubectl describe svc {name}` say whether it "
                       "points at any Ready pods, and on which port.)", "dim"))
            world.flags["curl_refused"] = True
            return
        # A Service picks a different backend per connection — so does this.
        backing = [(p, pd) for p, pd in sorted(k["pods"].items())
                   if f"{pd.get('ip') or _pod_ip(p)}:{svc['port']}" in eps]
        page = _serve(*random.choice(backing)) if backing else None
    else:
        pd = k["pods"][name]
        if not _pod_ready(pd):
            io.print(f"{tool}: (7) Failed to connect to {host} port {port} after 1 ms: "
                     "Connection refused")
            io.print(c("(that pod isn't Ready — nothing is listening in it yet)", "dim"))
            return
        page = _serve(name, pd)
    if page is None:
        io.print(f"{tool}: (52) Empty reply from server")
        io.print(c("(the port answered but that image serves no HTTP — it isn't a web server)", "dim"))
        return
    io.print(page)
    world.flags["curl_in_cluster"] = True


def _exec_in_pod(world, io, pod, cmd, ns):
    """The handful of commands the class runs inside a pod to prove reachability.

    A real `nginx:alpine` has no curl and a real `nginx` has neither curl nor
    wget — the authentic answer to most of these is "executable file not found".
    Both work here, with the caveat said out loud once, because the lesson being
    taught is cluster networking, not which binaries Debian ships.
    """
    k = world.k8s
    pd = k["pods"][pod]
    prog, args = cmd[0], cmd[1:]
    if pd["status"] != "Running":
        io.print(f'error: unable to upgrade connection: container not found ("{prog}")')
        io.print(c(f"(the pod is {pd['status']} — there is no running container to exec into. "
                   "`kubectl describe pod` explains why before `exec` can work at all.)", "dim"))
        world.flags["_noop"] = True
        return
    if prog in ("curl", "wget"):
        url = next((a for a in args if not a.startswith("-")), None)
        if not url:
            io.print(f"{prog}: try {prog} http://<service-name>")
            world.flags["_noop"] = True
            return
        if not world.flags.get("_exec_tool_note"):
            world.flags["_exec_tool_note"] = True
            io.print(c(f"(heads-up: the real {pd['image']} image ships no {prog}. On a live cluster "
                       "the reflex is a throwaway debug pod: "
                       "kubectl run tmp --rm -it --image=busybox -- sh)", "dim"))
        _cluster_http(world, io, url, pd["ns"], tool=prog)
    elif prog == "ping":
        host = next((a for a in args if not a.startswith("-")), "")
        hit = _resolve_in_cluster(world, host, pd["ns"])
        if hit is None:
            io.print(f"ping: bad address '{host}'")
        else:
            ip = (k["services"][hit[1]].get("clusterIP") or _svc_ip(hit[1]) if hit[0] == "service"
                  else k["pods"][hit[1]].get("ip") or _pod_ip(hit[1]))
            io.print(f"PING {host} ({ip}): 56 data bytes")
            for seq in range(3):
                io.print(f"64 bytes from {ip}: seq={seq} ttl=64 time=0.0{seq + 4}5 ms")
            io.print(c("(ping proves the pod NETWORK reaches it. A ClusterIP has no ICMP responder "
                       "on a real cluster — curl the port instead when you need proof of service.)", "dim"))
    elif prog == "nslookup":
        host = next((a for a in args if not a.startswith("-")), "")
        hit = _resolve_in_cluster(world, host, pd["ns"])
        io.print("Server:    10.96.0.10\nAddress:   10.96.0.10:53\n")
        if hit is None:
            io.print(f"** server can't find {host}: NXDOMAIN")
        elif hit[0] == "service":
            svc = k["services"][hit[1]]
            io.print(f"Name:      {hit[1]}.{svc['ns']}.svc.cluster.local")
            io.print(f"Address:   {svc.get('clusterIP') or _svc_ip(hit[1])}")
    elif prog == "hostname":
        io.print(pod)
    elif prog == "env":
        io.print(f"HOSTNAME={pod}\nKUBERNETES_SERVICE_HOST=10.96.0.1\nKUBERNETES_SERVICE_PORT=443\n"
                 "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
        io.print(c("(a ConfigMap or Secret wired in with envFrom/valueFrom shows up right here — "
                   "which is how you prove the wiring worked without reading the YAML again)", "dim"))
    elif prog == "cat" and args[:1] == ["/etc/resolv.conf"]:
        io.print(f"search {pd['ns']}.svc.cluster.local svc.cluster.local cluster.local\n"
                 "nameserver 10.96.0.10\noptions ndots:5")
        io.print(c(f"(that search line is why `curl backend` works from a pod in {pd['ns']} — the "
                   "short name gets the namespace appended for you)", "dim"))
    elif prog in ("sh", "bash") and not args:
        io.print("error: unable to use a TTY - this simulation has no interactive pod shell")
        io.print(c("(on a real cluster: kubectl exec -it <pod> -- sh. Here, pass the command you "
                   "wanted to run instead: kubectl exec <pod> -- curl http://backend)", "dim"))
        world.flags["_noop"] = True
    else:
        io.print(f'OCI runtime exec failed: exec failed: unable to start container process: '
                 f'exec: "{prog}": executable file not found in $PATH: unknown')
        io.print(c("(that binary isn't in this image — container images carry only what they need. "
                   f"This world runs curl · wget · ping · nslookup · env · hostname inside a pod.)", "dim"))
        world.flags["_noop"] = True


# ------------------------------------------------------------ cluster events --
# One source of truth for "what happened". `describe pod` prints these with its
# own column widths and `kubectl get events` prints the same records for the
# whole namespace — a student who reads one and then the other is checking
# whether the sim is telling the same story twice, and it has to be.
def _ev(kind, reason, age, src, msg, obj):
    return {"type": kind, "reason": reason, "age": age, "src": src, "msg": msg, "obj": obj}


def _pod_events(world, real):
    """The Events the kubelet would have recorded for one pod."""
    k = world.k8s
    p = k["pods"][real]
    d = k["deployments"].get(p.get("deploy")) or {}
    cont = d.get("container", "app")
    obj = f"pod/{real}"
    out = [_ev("Normal", "Scheduled", "42s", "default-scheduler",
               f"Successfully assigned {p['ns']}/{real} to minikube", obj)]
    if p["status"] == "ImagePullBackOff":
        ref = f"docker.io/library/{p['image']}" if "/" not in p["image"] else p["image"]
        out += [
            _ev("Normal", "Pulling", "41s (x4 over 90s)", "kubelet",
                f'Pulling image "{p["image"]}"', obj),
            _ev("Warning", "Failed", "40s (x4 over 89s)", "kubelet",
                f'Failed to pull image "{p["image"]}": rpc error: code = NotFound desc = failed '
                f'to pull and unpack image "{ref}": failed to resolve reference "{ref}": '
                f"{ref}: not found", obj),
            _ev("Warning", "Failed", "40s (x4 over 89s)", "kubelet", "Error: ErrImagePull", obj),
            _ev("Normal", "BackOff", "12s (x7 over 88s)", "kubelet",
                f'Back-off pulling image "{p["image"]}"', obj),
            _ev("Warning", "Failed", "12s (x7 over 88s)", "kubelet",
                "Error: ImagePullBackOff", obj),
        ]
    else:
        out += [
            _ev("Normal", "Pulled", "41s", "kubelet",
                f'Container image "{p["image"]}" already present on machine', obj),
            _ev("Normal", "Created", "41s", "kubelet", f"Created container {cont}", obj),
            _ev("Normal", "Started", "40s", "kubelet", f"Started container {cont}", obj),
        ]
    probes = d.get("probes") or {}
    if probes.get("readiness") and not _readiness_ok(d):
        port = probes["readiness"].get("port")
        out.append(_ev("Warning", "Unhealthy", "8s (x9 over 80s)", "kubelet",
                       f"Readiness probe failed: dial tcp {p.get('ip') or _pod_ip(real)}:{port}: "
                       "connect: connection refused", obj))
    return out


def _deploy_events(world, name):
    """The Events the deployment controller would have recorded."""
    # Missions hand-build deployment dicts, and `get events` can be the very
    # first command of a session — so read defensively rather than assuming a
    # reconcile has normalised this one yet.
    d = _norm_deploy(name, world.k8s["deployments"][name])
    hist = d.get("history") or [d["image"]]
    obj = f"deployment/{name}"
    out = [_ev("Normal", "ScalingReplicaSet", "42s", "deployment-controller",
               f"Scaled up replica set {name}-{_pod_template_hash(name, hist[0])} "
               f"to {d['replicas']}", obj)]
    if d.get("stuck"):
        out.append(_ev("Normal", "ScalingReplicaSet", "10s", "deployment-controller",
                       f"Scaled up replica set {name}-{_pod_template_hash(name, d['image'])} "
                       f"to {_max_surge(d)}", obj))
    return out


def _t_events(world, io, ns, all_ns, only=None):
    """`kubectl get events` — the single most-used debugging command there is.
    Same facts as `describe`, one table, whole namespace: which is exactly why
    people reach for it when they don't yet know WHICH object is unhappy."""
    k = world.k8s
    rows, warned = [], False
    for name in sorted(k["deployments"]):
        if all_ns or k["deployments"][name].get("ns") == ns:
            rows += [(k["deployments"][name].get("ns"), e) for e in _deploy_events(world, name)]
    for real in sorted(k["pods"]):
        if all_ns or k["pods"][real]["ns"] == ns:
            rows += [(k["pods"][real]["ns"], e) for e in _pod_events(world, real)]
    if only:
        rows = [(o_ns, e) for o_ns, e in rows if only in e["obj"]]
    if not rows:
        return False
    head = (["NAMESPACE"] if all_ns else []) + ["LAST SEEN", "TYPE", "REASON", "OBJECT", "MESSAGE"]
    body = []
    for o_ns, e in rows:
        warned = warned or e["type"] == "Warning"
        # LAST SEEN is the age alone; the "(x4 over 90s)" repeat count that
        # `describe` shows has no column of its own in this table.
        body.append(([o_ns] if all_ns else [])
                    + [e["age"].split(" ")[0], e["type"], e["reason"], e["obj"], e["msg"]])
    _table(io, head, body)
    if warned:
        io.print(c("(the Warning rows are the answer to 'why is it not working'. Real clusters are "
                   "noisy — `kubectl get events --sort-by=.lastTimestamp` and "
                   "`--field-selector type=Warning` are how you cut it down.)", "dim"))
    return True


# ---------------------------------------------------------- kubectl describe --
def _describe_deployment(world, io, name):
    """`describe deployment` — where probes, requests and limits become visible.
    A field the student wrote and can never see again might as well not exist."""
    k = world.k8s
    d = k["deployments"][name]
    pods = [pd for pd in k["pods"].values() if pd.get("deploy") == name]
    ready = sum(1 for pd in pods if _pod_ready(pd))
    updated = sum(1 for pd in pods if pd["image"] == d["image"])
    strat = d.get("strategy") or {}
    io.print(f"Name:                   {name}")
    io.print(f"Namespace:              {d['ns']}")
    io.print(f"Selector:               app={d['app']}")
    io.print(f"Replicas:               {d['replicas']} desired | {updated} updated | "
             f"{len(pods)} total | {ready} available | {max(0, d['replicas'] - ready)} unavailable")
    io.print(f"StrategyType:           {strat.get('type') or 'RollingUpdate'}")
    io.print(f"RollingUpdateStrategy:  {strat.get('maxUnavailable') or '25%'} max unavailable, "
             f"{strat.get('maxSurge') or '25%'} max surge")
    io.print("Pod Template:")
    io.print(f"  Labels:  app={d['app']}")
    io.print("  Containers:")
    io.print(f"   {d.get('container', 'app')}:")
    io.print(f"    Image:      {d['image']}")
    if d.get("containerPort"):
        io.print(f"    Port:       {d['containerPort']}/TCP")
    res = d.get("resources") or {}
    for side in ("limits", "requests"):
        if res.get(side):
            io.print(f"    {side.capitalize()}:")
            width = max(len(key) for key in res[side]) + 3
            for key in sorted(res[side]):
                io.print(f"      {(key + ':').ljust(width)}{res[side][key]}")
    probes = d.get("probes") or {}
    for kindname in ("liveness", "readiness", "startup"):
        if probes.get(kindname):
            io.print(f"    {(kindname.capitalize() + ':').ljust(12)}{_probe_line(probes[kindname])}")
    if not res and not probes and k.get("spec", {}).get(("Deployment", name, d["ns"])):
        # Only nag when the player actually applied a manifest — deployments that
        # helm or argocd conjured are not theirs to harden.
        io.print(c("    (no resources and no probes declared — the scheduler is guessing at this "
                   "pod's size and nothing is checking whether it's healthy)", "dim"))
    io.print("Conditions:")
    io.print("  Type           Status  Reason")
    io.print("  ----           ------  ------")
    io.print(f"  Available      {'True ' if ready else 'False'}   "
             f"{'MinimumReplicasAvailable' if ready else 'MinimumReplicasUnavailable'}")
    io.print(f"  Progressing    {'False' if d.get('stuck') else 'True '}   "
             f"{'ProgressDeadlineExceeded' if d.get('stuck') else 'NewReplicaSetAvailable'}")
    hist = d.get("history", [d["image"]])
    old = [i for i in dict.fromkeys(hist) if i != d["image"]]
    if old:
        io.print("OldReplicaSets:  " + ", ".join(
            f"{name}-{_pod_template_hash(name, i)} "
            f"({sum(1 for pd in pods if pd['image'] == i)}/"
            f"{sum(1 for pd in pods if pd['image'] == i)} replicas created)" for i in old))
    io.print(f"NewReplicaSet:   {name}-{_pod_template_hash(name, d['image'])} "
             f"({updated}/{updated} replicas created)")
    io.print("Events:")
    io.print("  Type    Reason             Age   From                   Message")
    io.print("  ----    ------             ----  ----                   -------")
    for e in _deploy_events(world, name):
        io.print(f"  {e['type']:<8}{e['reason']:<19}{e['age']:<6}{e['src']:<23}{e['msg']}")
    if d.get("stuck"):
        io.print(c("(Progressing=False + ProgressDeadlineExceeded is the deployment telling you the "
                   "rollout gave up. Available is still True — maxUnavailable kept the old pods "
                   "serving. `kubectl rollout undo` puts it back.)", "dim"))
    world.flags[f"describe_deploy_{name}"] = True
    world.flags["describe_deployment"] = True


def _describe_pod(world, io, real):
    """`describe pod` — the Events block at the bottom, which is where Kubernetes
    tells you WHY. Reading it is the skill; the note calls it debug gold."""
    k = world.k8s
    p = k["pods"][real]
    d = k["deployments"].get(p.get("deploy")) or {}
    probes = d.get("probes") or {}
    broken_probe = probes.get("readiness") and not _readiness_ok(d)
    io.print(f"Name:             {real}")
    io.print(f"Namespace:        {p['ns']}")
    io.print("Node:             minikube/192.168.49.2")
    io.print(f"Status:           {'Running' if p['status'] == 'Running' else 'Pending'}")
    io.print(f"IP:               {p.get('ip') or _pod_ip(real)}")
    io.print(f"Labels:           app={(p.get('labels') or {}).get('app', '<none>')}")
    io.print(f"Controlled By:    ReplicaSet/"
             f"{p['deploy'] + '-' + _pod_template_hash(p['deploy'], p['image']) if p.get('deploy') else '<none — bare pod>'}")
    io.print("Containers:")
    io.print(f"  {d.get('container', 'app')}:")
    io.print(f"    Image:          {p['image']}")
    if p["status"] == "Running":
        io.print("    State:          Running")
    else:
        io.print("    State:          Waiting")
        io.print(f"      Reason:       {p['status']}")
    io.print(f"    Ready:          {_pod_ready(p)}")
    for kindname in ("liveness", "readiness"):
        if probes.get(kindname):
            io.print(f"    {(kindname.capitalize() + ':').ljust(12)}{_probe_line(probes[kindname])}")
    io.print("Events:")
    io.print("  Type     Reason     Age                From               Message")
    io.print("  ----     ------     ----               ----               -------")
    for e in _pod_events(world, real):
        io.print(f"  {e['type']:<9}{e['reason']:<11}{e['age']:<19}{e['src']:<19}{e['msg']}")
    if p["status"] == "ImagePullBackOff":
        io.print(c("(read the last Message: that tag does not exist in the registry. The pod is not "
                   "crashing — it never got an image to run. Fix the tag, or roll the release back.)", "dim"))
    if broken_probe:
        probe = probes["readiness"]
        io.print(c(f"(the container listens on {d.get('containerPort')}, the readiness probe knocks on "
                   f"{probe.get('port')}. The pod RUNS and stays 0/1 forever — and a pod that is not "
                   "Ready is silently removed from its Service's endpoints.)", "dim"))
    world.flags["describe_pod"] = True


_NODE_CPU_M = 8000                                 # what `minikube start` gives you
_NODE_MEM_MI = 15823


def _cpu_m(text):
    """CPU quantity → millicores. `500m` is half a core; a bare `2` is two."""
    text = str(text or "").strip()
    if not text:
        return 0
    return int(float(text[:-1])) if text.endswith("m") else int(float(text) * 1000)


def _mem_mi(text):
    """Memory quantity → MiB. Ki/Mi/Gi are the units people actually write."""
    text = str(text or "").strip()
    for suffix, mult in (("Gi", 1024), ("Mi", 1), ("Ki", 1 / 1024), ("G", 954), ("M", 0.954)):
        if text.endswith(suffix):
            return int(float(text[:-len(suffix)]) * mult)
    return int(float(text or 0) / 1048576)


def _describe_node(world, io, name):
    """`describe node` — where the requests a student wrote stop being a YAML
    field and become a number the scheduler subtracts from a finite machine.
    Allocated resources is the whole argument for writing requests at all."""
    k = world.k8s
    pods = sorted((p, pd) for p, pd in k["pods"].items())
    io.print(f"Name:               {name}")
    io.print(f"Roles:              {'control-plane' if name == k['nodes'][0] else '<none>'}")
    io.print(f"Labels:             kubernetes.io/hostname={name}\n"
             "                    kubernetes.io/os=linux")
    io.print("Taints:             <none>")
    io.print("Conditions:")
    io.print("  Type             Status  Reason")
    io.print("  ----             ------  ------")
    for cond, status, reason in (("MemoryPressure", "False", "KubeletHasSufficientMemory"),
                                 ("DiskPressure", "False", "KubeletHasNoDiskPressure"),
                                 ("PIDPressure", "False", "KubeletHasSufficientPID"),
                                 ("Ready", "True ", "KubeletReady")):
        io.print(f"  {cond:<17}{status:<8}{reason}")
    io.print("Addresses:\n  InternalIP:  192.168.49.2\n  Hostname:    " + name)
    for title, cpu, mem in (("Capacity", _NODE_CPU_M, 16305480),
                            ("Allocatable", _NODE_CPU_M, 16203080)):
        io.print(f"{title}:")
        io.print(f"  cpu:                {cpu // 1000}")
        io.print(f"  memory:             {mem}Ki")
        io.print("  pods:               110")
    io.print(f"Non-terminated Pods:          ({len(pods)} in total)")
    io.print("  Namespace   Name                                 CPU Requests  CPU Limits  "
             "Memory Requests  Memory Limits")
    io.print("  ---------   ----                                 ------------  ----------  "
             "---------------  -------------")
    totals = {"requests": [0, 0], "limits": [0, 0]}
    for p, pd in pods:
        res = (k["deployments"].get(pd.get("deploy")) or {}).get("resources") or {}
        cells = []
        for side in ("requests", "limits"):
            cpu, mem = _cpu_m(res.get(side, {}).get("cpu")), _mem_mi(res.get(side, {}).get("memory"))
            totals[side][0] += cpu
            totals[side][1] += mem
            cells += [f"{cpu}m ({cpu * 100 // _NODE_CPU_M}%)" if cpu else "0 (0%)",
                      f"{mem}Mi ({mem * 100 // _NODE_MEM_MI}%)" if mem else "0 (0%)"]
        io.print(f"  {pd['ns']:<12}{p:<37}{cells[0]:<14}{cells[2]:<12}{cells[1]:<17}{cells[3]}")
    io.print("Allocated resources:")
    io.print("  (Total limits may be over 100 percent, i.e., overcommitted.)")
    io.print("  Resource           Requests       Limits")
    io.print("  --------           --------       ------")
    for i, (unit, span) in enumerate((("m", _NODE_CPU_M), ("Mi", _NODE_MEM_MI))):
        req, lim = totals["requests"][i], totals["limits"][i]
        io.print(f"  {['cpu', 'memory'][i]:<19}"
                 f"{f'{req}{unit} ({req * 100 // span}%)':<15}{lim}{unit} ({lim * 100 // span}%)")
    if not any(totals["requests"]):
        io.print(c("(every pod on this node requests ZERO cpu and ZERO memory, so the scheduler "
                   "believes the node is empty and will keep packing it until something is OOM-"
                   "killed. Requests are not a wish — they are the number the scheduler subtracts.)",
                   "dim"))
    world.flags["describe_node"] = True


def _describe_object(world, io, kind, name, ns):
    """`describe` for the kinds this world stores as objects rather than runs —
    ConfigMaps, CRDs, ServiceMonitors and friends. It prints the header real
    kubectl prints plus the Spec block the manifest carried, which is enough to
    answer the question people actually ask it: *did the thing I applied land
    with the fields I wrote?*"""
    pretty = K8S_OBJECT_KINDS[kind]
    cluster = kind in K8S_CLUSTER_SCOPED
    hit = _find_object(world, pretty, name, ns, all_ns=cluster)
    if not hit:
        io.print(f'Error from server (NotFound): {_api_plural(pretty)} "{name}" not found')
        world.flags["_noop"] = True
        return
    doc = world.k8s.get("spec", {}).get((pretty, hit[0], hit[1]), {})
    io.print(f"Name:         {hit[0]}")
    if not cluster:
        io.print(f"Namespace:    {hit[1]}")
    io.print(f"Labels:       {doc.get('labels') or '<none>'}")
    io.print(f"Annotations:  {doc.get('annotations') or '<none>'}")
    data, body = doc.get("data"), doc.get("raw")
    if data is not None:
        io.print("\nData\n====")
        for key in sorted(data):
            # describe never prints a Secret's VALUES, only their sizes. That
            # asymmetry is the point: `get secret -o yaml` is what leaks, and
            # base64 is not a lock.
            if pretty == "Secret":
                io.print(f"{key}:  {len(data[key])} bytes")
            else:
                io.print(f"{key}:\n----\n{data[key]}\n")
        if pretty == "Secret":
            io.print(c("\n(sizes, not values — describe deliberately withholds them. "
                       "`kubectl get secret -o yaml` hands them over base64-encoded, which is "
                       "encoding, not encryption.)", "dim"))
    elif body:
        io.print(f"API Version:  {doc.get('apiVersion', 'v1')}")
        io.print(f"Kind:         {pretty}")
        io.print("Spec:")
        for line in body.splitlines():
            io.print("  " + line)
    else:
        io.print(f"API Version:  {doc.get('apiVersion', 'v1')}")
        io.print(f"Kind:         {pretty}")
        io.print(c("(no stored spec: this object was created by a tool rather than parsed from a "
                   f"manifest — `kubectl get {kind}` confirms it exists)", "dim"))
    io.print("\nEvents:  <none>")
    world.flags[f"describe_{kind}"] = True


def _describe_service(world, io, name):
    """`describe service` — Endpoints on one line, which is the fastest answer to
    "the Service exists, the pods are Running, so why does curl hang?"."""
    s = world.k8s["services"][name]
    eps = _endpoints_for(world, s)
    io.print(f"Name:              {name}")
    io.print(f"Namespace:         {s['ns']}")
    io.print(f"Selector:          app={s.get('app')}")
    io.print(f"Type:              {s['type']}")
    io.print(f"IP:                {s.get('clusterIP') or _svc_ip(name)}")
    io.print(f"Port:              <unset>  {s.get('port', 80)}/TCP")
    if s.get("nodePort"):
        io.print(f"NodePort:          <unset>  {s['nodePort']}/TCP")
    io.print(f"Endpoints:         {','.join(eps) if eps else '<none>'}")
    if not eps:
        labels = sorted({(pd.get("labels") or {}).get("app") for pd in world.k8s["pods"].values()
                         if pd["ns"] == s["ns"]} - {None})
        present = ", ".join(labels) if labels else "none — there are no pods in this namespace"
        io.print(c(f"(zero endpoints. This Service hunts for pods labelled app={s.get('app')}; the "
                   f"labels actually present in {s['ns']} are: {present}. A selector typo is a 503 "
                   "with no error message anywhere.)", "dim"))
    world.flags[f"describe_svc_{name}"] = True
    world.flags["describe_service"] = True


def do_kubectl(world, args, io):
    k = world.k8s
    if "|" in args:
        # shlex hands us the pipe as a plain token; run the left side into a
        # buffer so the right side has something to eat.
        cut = args.index("|")
        args, filt = args[:cut], args[cut + 1:]
        buf = _Buffer()
        do_kubectl(world, args, buf)
        _pipe_through(io, buf.lines, filt)
        return
    if args and args[0] == "version" and any(
            a in ("--client", "--client=true", "-c") for a in args[1:]):
        world.flags["_noop"] = True
        io.print("Client Version: v1.30.2")
        io.print("Kustomize Version: v5.0.4")
        io.print(c("(it answered → kubectl is installed. --client asks the BINARY, so it works "
                   "with no cluster running at all)", "dim"))
        return
    if k is None:
        io.print("This mission has no Kubernetes world — try `task`.")
        return
    ns, all_ns, args = _extract_ns(args)
    if not args:
        io.print("kubectl controls the Kubernetes cluster manager.\n"
                 " Basic: get, apply, delete, describe, logs, scale, cluster-info")
        return
    sub, rest = args[0], args[1:]

    if sub == "version":
        if any(a in ("--client", "--client=true", "-c") for a in rest):
            io.print("Client Version: v1.30.2")
            io.print("Kustomize Version: v5.0.4")
            world.flags["_noop"] = True
            return
        io.print("Client Version: v1.30.0")
        if k["started"]:
            io.print("Server Version: v1.30.0")
        world.flags["kubectl_version"] = True
        return
    if not k["started"]:
        io.print("The connection to the server localhost:8080 was refused - did you specify the right host or port?")
        io.print(c("(no cluster is running — start one: minikube start)", "dim"))
        return

    if sub == "cluster-info":
        io.print(c("Kubernetes control plane", "green") + " is running at " + c("https://127.0.0.1:32771", "yellow"))
        io.print(c("CoreDNS", "green") + " is running at " + c("https://127.0.0.1:32771/api/v1/namespaces/kube-system/services/kube-dns:dns/proxy", "yellow"))
        world.flags["cluster_info"] = True

    elif sub == "get":
        out, rest = _extract_out(rest)
        if not rest:
            io.print("error: you must specify the type of resource to get"); return
        kinds = [_kind_of(k, x.strip()) for x in rest[0].split(",")]
        only = next((a for a in rest[1:] if not a.startswith("-")), None)
        if "/" in rest[0]:                       # `get pod/app-xyz` — one object
            kind_part, only = rest[0].split("/", 1)
            kinds = [_kind_of(k, kind_part)]
        world.flags["get_" + "_".join(kinds) + ("_A" if all_ns else "")] = True
        if out and out.startswith("jsonpath"):
            _k8s_jsonpath(world, io, kinds[0], only, out.split("=", 1)[1] if "=" in out else "", ns, all_ns)
            return
        if out == "yaml":
            _k8s_yaml(world, io, kinds[0], only, ns)
            return
        if out == "json":
            io.print(c("(-o json isn't simulated — the object graph behind it is deeper than this "
                       "world models. `-o yaml` works on ConfigMaps and Secrets, `-o jsonpath` "
                       "pulls single fields, and `describe` covers the rest.)", "dim"))
            world.flags["_noop"] = True
            return
        if out == "name":
            # `-o name` is what you pipe into xargs; it prints type/name and
            # nothing else. Worth having honestly rather than ignoring the flag.
            names = {"pods": sorted(f"pod/{p}" for p in _pods_in(world, ns, all_ns)),
                     "deployments": sorted(f"deployment.apps/{n}" for n in
                                           _pick(k["deployments"], ns, all_ns, only)),
                     "services": sorted(f"service/{n}" for n in
                                        _pick(k["services"], ns, all_ns, only))}.get(kinds[0])
            if names is None:
                io.print(c("(-o name is simulated for pods, deployments and services)", "dim"))
                world.flags["_noop"] = True
                return
            for n in names:
                io.print(n)
            world.flags["get_" + kinds[0]] = True
            return
        # `-A` has no namespace to name in "No resources found in X namespace."
        where = None if all_ns else ns
        for kind in kinds:
            if kind == "nodes":
                _table(io, ["NAME", "STATUS", "ROLES", "AGE", "VERSION"],
                       [[n, "Ready", "control-plane" if i == 0 else "<none>", "5m", "v1.30.0"]
                        for i, n in enumerate(k["nodes"])])
                world.flags["get_nodes"] = True
            elif kind == "namespaces":
                _table(io, ["NAME", "STATUS", "AGE"],
                       [[n, "Active", "5m"] for n in sorted(k["namespaces"])])
                world.flags["get_namespaces"] = True
            elif kind == "pods":
                if ns == "kube-system" and not all_ns:
                    _table(io, ["NAME", "READY", "STATUS", "RESTARTS", "AGE"],
                           [[p, "1/1", "Running", "0", "5m"] for p in
                            ("coredns-7db6d8ff4d-x2m9k", "etcd-minikube", "kube-apiserver-minikube",
                             "kube-controller-manager-minikube", "kube-proxy-bqpfz",
                             "kube-scheduler-minikube", "storage-provisioner")])
                    world.flags["get_pods_system"] = True
                    continue
                if not _t_pods(world, io, ns, all_ns, only, wide=(out == "wide")):
                    _empty(io, "pods", where, only)
                    others = {d["ns"] for d in k["pods"].values()}
                    if others and not only:
                        io.print(c(f"(pods DO exist — in namespace{'s' if len(others) > 1 else ''} "
                                   f"{', '.join(sorted(others))}. Add -n <namespace>)", "dim"))
            elif kind == "deployments":
                if not _t_deployments(world, io, ns, all_ns, only):
                    _empty(io, "deployments.apps", where, only)
            elif kind == "rs":
                if not _t_rs(world, io, ns, all_ns, only):
                    _empty(io, "replicasets.apps", where, only)
            elif kind == "services":
                if not _t_services(world, io, ns, all_ns, only):
                    _empty(io, "services", where, only)
            elif kind == "endpoints":
                if not _t_endpoints(world, io, ns, all_ns, only):
                    _empty(io, "endpoints", where, only)
            elif kind == "events":
                if not _t_events(world, io, ns, all_ns, only):
                    _empty(io, "events", where, only)
            elif kind == "hpa":
                if not _t_hpa(world, io, ns, all_ns, only):
                    _empty(io, "horizontalpodautoscalers.autoscaling", where, only)
            elif kind == "all":
                # Real `get all` is a CATEGORY, not "everything": workloads and
                # services, with type-qualified names. ConfigMaps, Secrets, PVCs
                # and Ingresses are deliberately NOT in it — which is why the
                # class note lists them on separate lines.
                shown = False
                for fn, prefix in ((_t_pods, "pod/"), (_t_services, "service/"),
                                   (_t_kind("daemonset"), "daemonset.apps/"),
                                   (_t_deployments, "deployment.apps/"), (_t_rs, "replicaset.apps/"),
                                   (_t_kind("statefulset"), "statefulset.apps/"),
                                   (_t_hpa, "horizontalpodautoscaler.autoscaling/"),
                                   (_t_kind("job"), "job.batch/"), (_t_kind("cronjob"), "cronjob.batch/")):
                    buf = _Buffer()
                    if not fn(world, buf, ns, all_ns, None, prefix):
                        continue
                    if shown:
                        io.print("")            # kubectl blank-lines between groups
                    for line in buf.lines:
                        io.print(line)
                    shown = True
                if not shown:
                    io.print(f"No resources found in {ns} namespace.")
                else:
                    io.print(c("(`all` is a category, not everything: no ConfigMaps, Secrets, PVCs "
                               "or Ingresses here. Ask for those by name — kubectl get configmap,secret,pvc)", "dim"))
                world.flags["get_all"] = True
            elif kind in K8S_OBJECT_KINDS:
                cluster = all_ns or kind in K8S_CLUSTER_SCOPED
                if not _t_objects(world, io, kind, ns, cluster, only):
                    _empty(io, _api_plural(K8S_OBJECT_KINDS[kind]),
                           None if kind in K8S_CLUSTER_SCOPED else where, only)
            elif kind in ("serviceaccount", "role", "rolebinding"):
                if kind == "serviceaccount":
                    rows = [n for (n, o_ns) in k["rbac"]["sa"] if all_ns or o_ns == ns]
                elif kind == "role":
                    rows = [n for n, o_ns in k["rbac"]["roles"].items() if all_ns or o_ns == ns]
                else:
                    rows = [n for n, (_, _, o_ns) in k["rbac"]["bindings"].items() if all_ns or o_ns == ns]
                if only:
                    rows = [n for n in rows if n.startswith(only)]
                if not rows:
                    _empty(io, kind + "s", where, only)
                    continue
                _table(io, ["NAME", "AGE"], [[n, "42s"] for n in sorted(rows)])
                world.flags[f"get_{kind}"] = True
            else:
                world.flags["_noop"] = True     # a refusal is not a move
                io.print(f'error: the server doesn\'t have a resource type "{kind}"'
                         + _suggest(kind, K8S_ALIASES))

    elif sub == "apply":
        if "-f" not in rest:
            io.print("error: must specify one of -f or -k"); return
        target = rest[rest.index("-f") + 1] if rest.index("-f") + 1 < len(rest) else "."
        files = sorted(f for f in world.files if f.endswith((".yaml", ".yml"))) if target == "." else [target]
        if target != "." and target not in world.files:
            io.print(f'error: the path "{target}" does not exist'); return
        if not files:
            io.print("error: no YAML files found in the current directory"); return
        docs = []
        for f in files:
            docs.extend(_parse_manifests(world.files[f]))
        docs.sort(key=lambda d: 0 if d["kind"] == "Namespace" else 1)
        for doc in docs:
            _k8s_apply_doc(world, doc, io)
        world.flags["applied"] = world.flags.get("applied", set()) | set(files)

    elif sub == "delete":
        if rest[:1] == ["-f"] or ("-f" in rest):
            target = rest[rest.index("-f") + 1] if rest.index("-f") + 1 < len(rest) else "."
            files = sorted(f for f in world.files if f.endswith((".yaml", ".yml"))) if target == "." else [target]
            docs = []
            for f in files:
                if f in world.files:
                    docs.extend(_parse_manifests(world.files[f]))
            for doc in reversed(docs):
                _k8s_apply_doc(world, doc, io, deleting=True)
            world.flags["deleted_f"] = True
            return
        if not rest:
            io.print("error: resource(s) were provided, but no name was specified"); return
        kind = _kind_of(k, rest[0])
        if "/" in rest[0]:                       # `delete svc/demo-svc`
            kind_part, one = rest[0].split("/", 1)
            kind, rest = _kind_of(k, kind_part), [kind_part, one] + rest[1:]
        name = rest[1] if len(rest) > 1 else None
        if kind == "pods" and name:
            real = _find_pod(world, name, ns)
            if not real:
                io.print(f'Error from server (NotFound): pods "{name}" not found'); return
            owned = k["pods"][real].get("deploy")
            del k["pods"][real]
            io.print(f'pod "{real}" deleted')
            _reconcile(world)
            if owned:
                world.flags["pod_deleted_owned"] = True
        elif kind in _DELETABLE and name:
            # Deleting something that isn't there is an ERROR, not a shrug.
            # Confirming a delete that did nothing sends a student hunting for
            # the wrong bug — and it is the same already-exists realism the
            # `create` path already has, pointed the other way.
            doc_kind = _DELETABLE[kind]
            # A namespace IS its own scope; a cluster-scoped kind has none.
            scope = (name if kind == "namespaces" else
                     None if kind in K8S_CLUSTER_SCOPED else ns)
            if not _k8s_exists(k, doc_kind, name, scope):
                io.print(f'Error from server (NotFound): {_api_plural(doc_kind)} "{name}" not found')
                if kind != "namespaces" and _k8s_exists(k, doc_kind, name, None):
                    io.print(c("(it exists — in another namespace. delete is namespaced too, so "
                               "-n has to match the one it lives in)", "dim"))
                world.flags["_noop"] = True
                return
            found = _find_object(world, doc_kind, name, ns, all_ns=True)
            _k8s_apply_doc(world, {"kind": doc_kind, "name": name,
                                   "ns": (name if kind == "namespaces" else
                                          found[1] if found else ns),
                                   "names": [name]}, io, deleting=True)
            if kind == "rolebinding":
                world.flags["binding_deleted"] = True
        else:
            world.flags["_noop"] = True
            io.print(f'error: unable to delete "{rest[0]}" — this world deletes pod / namespace / '
                     "deployment / service / configmap / secret / the RBAC trio and the rest of "
                     "`kubectl api-resources`, by name or with -f <file>")

    elif sub == "describe":
        if not rest:
            io.print("error: you must specify a resource and a name"); return
        kind = _kind_of(k, rest[0].split("/")[0])
        name = rest[1] if len(rest) > 1 else (rest[0].split("/", 1)[1] if "/" in rest[0] else None)
        if not name:
            io.print("error: you must specify a resource and a name"); return
        if kind == "deployments":
            if name not in k["deployments"]:
                io.print(f'Error from server (NotFound): deployments.apps "{name}" not found'); return
            _describe_deployment(world, io, name)
        elif kind == "pods":
            real = _find_pod(world, name, ns)
            if not real:
                io.print(f'Error from server (NotFound): pods "{name}" not found'); return
            _describe_pod(world, io, real)
        elif kind == "services":
            if name not in k["services"]:
                io.print(f'Error from server (NotFound): services "{name}" not found'); return
            _describe_service(world, io, name)
        elif kind == "nodes":
            if name not in k["nodes"]:
                io.print(f'Error from server (NotFound): nodes "{name}" not found'); return
            _describe_node(world, io, name)
        elif kind in K8S_OBJECT_KINDS:
            _describe_object(world, io, kind, name, ns)
        else:
            world.flags["_noop"] = True
            io.print(f"describe for '{rest[0]}' isn't simulated — try deployment / pod / service / node")

    elif sub == "logs":
        if not rest:
            io.print("error: expected 'logs POD'"); return
        real = _find_pod(world, rest[0], ns)
        if not real:
            io.print(f'Error from server (NotFound): pods "{rest[0]}" not found'); return
        pod = k["pods"][real]
        img = pod["image"]
        if pod["status"] != "Running":
            # There are no logs before there is a container. Students reach for
            # `logs` first on any broken pod; this is where they learn that
            # `describe` is the tool for a pod that never started.
            io.print(f'Error from server (BadRequest): container "app" in pod "{real}" is waiting '
                     f'to start: trying and failing to pull image')
            io.print(c("(no container ever ran, so there is nothing to log. For a pod that failed "
                       "BEFORE start-up, `kubectl describe pod` is the tool — its Events say why.)", "dim"))
            world.flags["_noop"] = True
            return
        if "nginx" in img or "hello" in img:
            io.print(f'/docker-entrypoint.sh: Configuration complete; ready for start up\n'
                     f'10.244.0.1 - - [{random.randint(10, 28)}/Jul/2026:10:0{random.randint(0, 9)}:12 +0000] "GET / HTTP/1.1" 200 615 "-" "kube-probe/1.30"')
        else:
            io.print("(container started; no recent output)")
        world.flags["logs_pod"] = True

    elif sub == "exec":
        if "--" in rest:
            head, cmd = rest[:rest.index("--")], rest[rest.index("--") + 1:]
        else:
            # `kubectl exec POD COMMAND` still runs, with the real deprecation
            # warning — that warning is how everyone learns the `--` exists.
            head = rest[:1]
            cmd = rest[1:]
            if cmd:
                io.print("kubectl exec [POD] [COMMAND] is DEPRECATED and will be removed in a "
                         "future version. Use kubectl exec [POD] -- [COMMAND] instead.")
        target, skip = None, False
        for a in head:
            if skip:
                skip = False; continue
            if a in ("-c", "--container"):
                skip = True; continue
            if not a.startswith("-"):
                target = a; break
        if not target:
            io.print("error: expected 'exec POD_NAME -- COMMAND'"); return
        if not cmd:
            io.print("error: you must specify at least one command for the container")
            io.print(c("(everything after `--` is run INSIDE the pod: kubectl exec "
                       f"{target} -- curl http://backend)", "dim"))
            world.flags["_noop"] = True
            return
        if target.split("/")[0] in ("deploy", "deployment"):
            dep = target.split("/", 1)[1]
            real = next((p for p, pd in sorted(k["pods"].items())
                         if pd.get("deploy") == dep and _pod_ready(pd)), None)
        else:
            real = _find_pod(world, target, ns)
        if not real:
            io.print(f'Error from server (NotFound): pods "{target}" not found'); return
        world.flags["exec_pod"] = True
        _exec_in_pod(world, io, real, cmd, ns)

    elif sub == "expose":
        # The imperative half of the Services class. It matters pedagogically for
        # exactly one reason: it copies the SELECTOR off the workload instead of
        # asking you to retype it, and a mistyped selector is the #1 way to build
        # a Service that reaches nothing.
        pos = [a for a in rest if not a.startswith("-")]
        target = (rest[0].split("/", 1)[1] if rest and "/" in rest[0]
                  else pos[1] if len(pos) > 1 else None)
        kind = _kind_of(k, rest[0].split("/")[0]) if rest else ""
        flags = {a.split("=", 1)[0]: a.split("=", 1)[1] for a in rest if a.startswith("--") and "=" in a}
        if kind != "deployments" or not target:
            io.print("error: expected `kubectl expose deployment <name> --port=<n> "
                     "[--type=NodePort] [--name=<svc>]`")
            world.flags["_noop"] = True
            return
        if target not in k["deployments"]:
            io.print(f'Error from server (NotFound): deployments.apps "{target}" not found'); return
        d = _norm_deploy(target, k["deployments"][target])
        if "--port" not in flags and not d.get("containerPort"):
            io.print("error: couldn't find port via --port flag or introspection")
            io.print(c("(the Deployment declares no containerPort, so there is nothing to copy — "
                       "say --port=<n> yourself)", "dim"))
            world.flags["_noop"] = True
            return
        svc = flags.get("--name", target)
        if svc in k["services"]:
            io.print(f'Error from server (AlreadyExists): services "{svc}" already exists')
            io.print(c("(imperative commands refuse to run over existing things — `--name=` gives "
                       "the second Service its own name, or delete the first one)", "dim"))
            world.flags["_noop"] = True
            return
        # The "service/... exposed" line replaces apply's "created" — same object,
        # kubectl's own wording for the verb the player used.
        _k8s_apply_doc(world, {"kind": "Service", "name": svc, "ns": d["ns"], "names": [svc],
                               "type": flags.get("--type", "ClusterIP"),
                               "port": int(flags.get("--port", d.get("containerPort") or 80)),
                               "app": d["app"]}, _Buffer())
        io.print(f"service/{svc} exposed")
        io.print(c(f"(it read the selector off the Deployment: app={d['app']}. That is the one "
                   "field `expose` saves you from typo-ing — and the one field a Service cannot "
                   "work without.)", "dim"))

    elif sub == "scale":
        m = re.search(r"--replicas[= ](\d+)", " ".join(rest))
        target = next((a for a in rest if not a.startswith("--") and a not in ("deployment", "deploy")), None)
        if target and "/" in target:
            target = target.split("/", 1)[1]
        if not m or not target:
            io.print("Usage: kubectl scale deployment <name> --replicas=<N>"); return
        if target not in k["deployments"]:
            io.print(f'Error from server (NotFound): deployments.apps "{target}" not found'); return
        k["deployments"][target]["replicas"] = int(m.group(1))
        _reconcile(world)
        io.print(f"deployment.apps/{target} scaled")
        world.flags[f"scaled_{target}"] = int(m.group(1))

    elif sub == "set" and rest[:1] == ["image"]:
        target = rest[1] if len(rest) > 1 else ""
        target = target.split("/", 1)[1] if "/" in target else target
        pair = rest[2] if len(rest) > 2 else ""
        if "=" not in pair or target not in k["deployments"]:
            io.print("Usage: kubectl set image deployment/<name> <container>=<image>"); return
        d = _norm_deploy(target, k["deployments"][target])
        new = pair.split("=", 1)[1]
        if new == d["image"]:
            io.print(f"deployment.apps/{target} image updated (no change)")
            world.flags["_noop"] = True
            return
        d["history"].append(new)
        d["image"] = new
        d["revision"] += 1
        # No pod deletion here: the reconcile loop decides who lives. If the new
        # tag can't be pulled it keeps the old pods serving and parks the surge
        # pod in ImagePullBackOff — which is the whole point of the exercise.
        _reconcile(world)
        io.print(f"deployment.apps/{target} image updated")
        world.flags[f"set_image_{target}"] = d["image"]
        if d.get("stuck"):
            io.print(c("(no error here — kubectl only recorded the desired state. Whether it WORKS "
                       "is a separate question: `kubectl rollout status` and `kubectl get pods`.)", "dim"))

    elif sub == "rollout":
        action = rest[0] if rest else ""
        target = rest[1].split("/")[-1] if len(rest) > 1 else ""
        if target not in k["deployments"]:
            io.print(f'Error from server (NotFound): deployments.apps "{target}" not found'); return
        d = _norm_deploy(target, k["deployments"][target])
        if action == "status":
            if not d.get("stuck"):
                io.print(f'deployment "{target}" successfully rolled out')
            else:
                updated = sum(1 for pd in k["pods"].values()
                              if pd.get("deploy") == target and pd["image"] == d["image"])
                io.print(f'Waiting for deployment "{target}" rollout to finish: {updated} out of '
                         f'{d["replicas"]} new replicas have been updated...')
                io.print(f'error: deployment "{target}" exceeded its progress deadline')
                io.print(c("(real kubectl BLOCKS on that Waiting line — we skip the ten-minute wait, "
                           "the verdict is identical. progressDeadlineSeconds is what eventually turns "
                           "a hung rollout into an error you can see in CI.)", "dim"))
            world.flags[f"rollout_status_{target}"] = "stuck" if d.get("stuck") else "ok"
        elif action == "undo":
            if len(d.get("history", [])) < 2:
                io.print(f'error: no rollout history found for deployment "{target}"')
                world.flags["_noop"] = True
                return
            d["history"].pop()
            d["image"] = d["history"][-1]
            d["revision"] += 1
            _reconcile(world)
            io.print(f"deployment.apps/{target} rolled back")
            world.flags[f"rolled_back_{target}"] = d["image"]
        elif action == "history":
            io.print(f"deployment.apps/{target}")
            _table(io, ["REVISION", "CHANGE-CAUSE"],
                   [[i, "<none>"] for i in range(1, d.get("revision", 1) + 1)])
            world.flags[f"rollout_history_{target}"] = True
        elif action == "restart":
            for p in [p for p, pd in k["pods"].items() if pd.get("deploy") == target]:
                del k["pods"][p]
            _reconcile(world)
            io.print(f"deployment.apps/{target} restarted")
        else:
            world.flags["_noop"] = True
            io.print("rollout: try status / undo / history / restart deployment/<name>")

    elif sub == "auth" and rest[:1] == ["can-i"]:
        as_sa = next((a.split(":")[-1] for a in rest if a.startswith("--as=system:serviceaccount:")), None)
        if as_sa is None:
            io.print("yes" + c("  (you're cluster-admin here)", "dim"))
            return
        ok = any(sa == as_sa and b_ns == ns and role in k["rbac"]["roles"]
                 for role, sa, b_ns in k["rbac"]["bindings"].values())
        io.print("yes" if ok else "no")
        world.flags["can_i"] = "yes" if ok else "no"

    elif sub == "create":
        if rest[:1] == ["namespace"] and len(rest) > 1:
            if rest[1] in k["namespaces"]:
                io.print(f'Error from server (AlreadyExists): namespaces "{rest[1]}" already exists')
                io.print(c("(imperative `create` refuses to run over existing things — declarative `apply` converges instead)", "dim"))
                return
            k["namespaces"].add(rest[1])
            io.print(f"namespace/{rest[1]} created")
        elif rest[:1] == ["deployment"] and len(rest) > 1:
            if rest[1] in k["deployments"]:
                io.print(f'Error from server (AlreadyExists): deployments.apps "{rest[1]}" already exists')
                io.print(c("(imperative `create` refuses to run over existing things — declarative `apply` converges instead)", "dim"))
                return
            img = next((a.split("=", 1)[1] for a in rest if a.startswith("--image=")), "nginx")
            k["deployments"][rest[1]] = {"ns": ns, "replicas": 1, "image": img,
                                         "revision": 1, "history": [img]}
            _reconcile(world)
            io.print(f"deployment.apps/{rest[1]} created")
        else:
            io.print("kubectl create: try `create namespace <n>` or `create deployment <n> --image=<img>` — or use apply -f")

    elif sub == "explain":
        io.print(f"KIND:       {rest[0].split('.')[0].capitalize() if rest else '?'}\nVERSION:    v1\n\n"
                 "DESCRIPTION:\n     (offline field documentation — the real command documents EVERY field\n"
                 "     of every resource. Try it on a real cluster: kubectl explain pod.spec)")
        world.flags["explain"] = True

    elif sub in KUBECTL_UNSIMULATED:
        world.flags["_noop"] = True
        io.print(f"kubectl: `{sub}` is a real kubectl command — this world just doesn't simulate it.")
        io.print(c(f"   {KUBECTL_UNSIMULATED[sub]}", "dim"))
        io.print(c("   worth trying on your own cluster · `task` shows what this mission needs",
                   "dim"))

    else:
        world.flags["_noop"] = True
        known = tuple(KUBECTL_UNSIMULATED) + (
            "get", "apply", "delete", "describe", "logs", "exec", "scale", "set",
            "rollout", "auth", "create", "expose", "explain", "cluster-info", "version")
        io.print(f"kubectl: '{sub}' is not simulated (yet)." + _suggest(sub, known))
        io.print(c("Try `task` to see what the mission needs.", "dim"))


def do_minikube(world, args, io):
    k = world.k8s
    if k is None:
        io.print("This mission has no Kubernetes world — try `task`.")
        return
    sub = args[0] if args else ""
    if sub == "version":
        io.print("minikube version: v1.33.1")
        world.flags["minikube_version"] = True
    elif sub == "start":
        if k["started"]:
            io.print("🏄  minikube is already running — kubectl is ready to go!")
            return
        io.print("😄  minikube v1.33.1 on your machine")
        io.print("✨  Automatically selected the docker driver")
        io.print("🐳  Preparing Kubernetes v1.30.0 on Docker 26.1 ...")
        io.print("🔎  Verifying Kubernetes components...")
        io.print("🏄  Done! kubectl is now configured to use \"minikube\" cluster and \"default\" namespace")
        k["started"] = True
        world.flags["minikube_started"] = True
    elif sub == "stop":
        k["started"] = False
        io.print("✋  Stopping node \"minikube\" ...\n🛑  1 node stopped.")
    elif sub == "dashboard":
        if not k["started"]:
            io.print("❌  Exiting due to GUEST_STATUS: state: unknown state \"minikube\": docker container inspect minikube")
            io.print(c("(the cluster isn't running — minikube start first)", "dim"))
            return
        io.print("🤔  Verifying dashboard health ...\n🚀  Launching proxy ...")
        io.print("🎉  Opening http://127.0.0.1:43211/api/v1/namespaces/kubernetes-dashboard/services/http:kubernetes-dashboard:/proxy/ in your default browser...")
        world.flags["dashboard"] = True
    elif sub == "service":
        if not k["started"]:
            io.print("❌  Exiting due to MK_NOT_RUNNING: minikube is not running"); return
        name = next((a for a in args[1:] if not a.startswith("-")), None)
        ns = "default"
        if "-n" in args:
            ns = args[args.index("-n") + 1]
        if not name or name not in k["services"]:
            io.print(f"❌  Exiting due to SVC_NOT_FOUND: Service '{name}' was not found in '{ns}' namespace.")
            return
        svc = k["services"][name]
        if svc["type"] != "NodePort":
            io.print(f"❌  Exiting due to SVC_UNREACHABLE: service '{name}' has no node port")
            io.print(c("(only NodePort/LoadBalancer services are reachable from outside the cluster)", "dim"))
            return
        node_port = svc.get("nodePort", 30080)
        io.print("|-----------|" + "-" * 12 + "|" + "-" * 13 + "|" + "-" * 27 + "|")
        io.print(f"| NAMESPACE |    NAME    | TARGET PORT |            URL            |")
        io.print("|-----------|" + "-" * 12 + "|" + "-" * 13 + "|" + "-" * 27 + "|")
        io.print(f"| {svc['ns']:<9} | {name:<10} | {svc['port']:<11} | http://192.168.49.2:{node_port} |")
        io.print("|-----------|" + "-" * 12 + "|" + "-" * 13 + "|" + "-" * 27 + "|")
        io.print(f"🎉  Opening service {svc['ns']}/{name} in default browser...")
        io.print(c("    (a page with the running app opens — screenshot-worthy!)", "dim"))
        world.flags[f"minikube_service_{name}"] = True
    else:
        io.print("minikube: try start / stop / version / dashboard / service <name>")


# ----------------------------------------------------------- docker compose --
def _do_compose(world, rest, io):
    cfile = next((f for f in ("docker-compose.yaml", "docker-compose.yml", "compose.yaml") if f in world.files), None)
    if cfile is None:
        io.print("no configuration file provided: not found")
        return
    body = world.files[cfile]
    services = re.findall(r"(?m)^  ([\w-]+):\s*$", body.split("services:", 1)[-1])
    images = dict(re.findall(r"(?m)^  ([\w-]+):\s*\n(?:.*\n)*?\s+image:\s*(\S+)", body))
    sub = rest[0] if rest else ""
    if sub == "up":
        detached = "-d" in rest
        # `up` creates a network for the project before anything else, and it
        # counts toward the total — so the header and the body have to agree,
        # and `docker network ls` has to show it afterwards.
        fresh_net = "compose_default" not in world.networks
        total = len(services) + (1 if fresh_net else 0)
        io.print(f"[+] Running {total}/{total}")
        if fresh_net:
            world.networks.add("compose_default")
            io.print(" ✔ Network compose_default  Created")
        for s in services:
            img = images.get(s, s)
            world.containers[s] = {"id": _rand_id(), "image": img, "status": "running",
                                   "exit_code": 0, "network": "compose_default", "ports": [],
                                   "files": {}, "logs": ""}
            if "rabbitmq" in img:
                world.containers[s]["logs"] = ("Starting RabbitMQ 3.13 on Erlang 26\n"
                                               "started TCP listener on [::]:5672\n"
                                               "Management plugin: HTTP listener started on port 15672\n"
                                               "Server startup complete; 4 plugins started.")
                world.containers[s]["ports"] = ["5672:5672", "15672:15672"]
            io.print(f" ✔ Container {s}  Started")
        world.flags["compose_up"] = True
        if not detached:
            io.print(c("(running attached — in class we always use -d for detached)", "dim"))
    elif sub == "ps":
        _ps_table(io, {n: d for n, d in world.containers.items() if d["network"] == "compose_default"})
        world.flags["compose_ps"] = True
    elif sub == "logs":
        name = rest[1] if len(rest) > 1 else (services[0] if services else None)
        if name and name in world.containers:
            io.print(world.containers[name]["logs"] or "(no output)")
            world.flags["compose_logs"] = True
        else:
            io.print(f"no such service: {name}")
    elif sub == "down":
        for s in services:
            if s in world.containers:
                del world.containers[s]
                io.print(f" ✔ Container {s}  Removed")
        if "compose_default" in world.networks:
            world.networks.discard("compose_default")
            io.print(" ✔ Network compose_default  Removed")
        world.flags["compose_down"] = True
    else:
        io.print("docker compose: try up -d / ps / logs <svc> / down")


# --------------------------------------------------------------------- git --
MARKERS = ("<<<<<<<", "=======", ">>>>>>>")


def _has_markers(text):
    return any(m in text for m in MARKERS)


# Everything this sandbox simulates, with the line `git help <cmd>` answers with.
# The usage block, the did-you-mean suggestions and the "not simulated" fallback
# all read from here, so there is exactly ONE list that can go stale.
GIT_SUBS = {
    "clone": "copy a remote repository to your machine (and wire up 'origin')",
    "config": "read/write git settings — user.name and user.email come first",
    "status": "what is untracked / modified / staged right now",
    "add": "stage a change for the next commit",
    "restore": "discard a working-tree change (--staged un-stages instead)",
    "reset": "move HEAD (--hard throws the work away too) or un-stage a file",
    "commit": "snapshot the staged changes (--amend rewrites the last one)",
    "revert": "add a NEW commit that undoes an old one",
    "rm": "--cached: stop tracking a file without deleting it",
    "stash": "park uncommitted work, clean the tree, pop it back later",
    "log": "the commit history (--oneline · --graph · --all)",
    "show": "one commit: its message and its diff",
    "diff": "line-by-line changes (--staged compares the index with HEAD)",
    "branch": "list / create / delete branches",
    "checkout": "switch branches (-b creates) — the classic spelling",
    "switch": "switch branches (-c creates) — the modern spelling",
    "merge": "join another branch in (--abort backs out of a conflict)",
    "tag": "name a commit for good — v1.0.0",
    "fetch": "download what origin has, WITHOUT touching your files",
    "pull": "fetch + merge: bring the team's commits into your branch",
    "push": "upload your commits (and, asked nicely, your tags) to origin",
    "remote": "the named URLs this repo talks to",
}

# Real git refuses the very first commit until user.name and user.email are set.
# Enforcing that would wall every mission at objective one, so the sandbox ships
# with an identity and `git config` teaches the real rule on top of it.
GIT_ID = ("you", "you@example.com")
GIT_SYSTEM_CONFIG = {"init.defaultbranch": "main", "core.editor": "vi", "color.ui": "auto"}
# Commits a mission started with have no timestamp of their own to show.
GIT_EPOCH = "Mon Jul 14 09:12:44 2025 +0300"


def _gitconfig(world):
    """`git config --global` writes to ~/.gitconfig — it belongs to the MACHINE,
    not to a repo, so it lives in flags and works in a mission with no git world."""
    return world.flags.setdefault("gitconfig", {})


def _author(world):
    cfg = world.flags.get("gitconfig", {})
    return cfg.get("user.name", GIT_ID[0]), cfg.get("user.email", GIT_ID[1])


def _now_stamp():
    from datetime import datetime
    return datetime.now().astimezone().strftime("%a %b %d %H:%M:%S %Y %z")


def _ignored(world, name):
    """Does .gitignore cover this path? fnmatch over the patterns students
    actually write (.env, *.log, __pycache__/, secrets/), last match wins —
    which is how git resolves a `!negation` line."""
    base = name.rstrip("/")
    hit = False
    for raw in world.files.get(".gitignore", "").split("\n"):
        rule = raw.strip()
        if not rule or rule.startswith("#"):
            continue
        neg = rule.startswith("!")
        pat = rule[1:].strip().rstrip("/") if neg else rule.rstrip("/")
        if "/" in pat:
            match = fnmatch.fnmatch(base, pat) or base.startswith(pat.lstrip("/") + "/")
        else:
            match = any(fnmatch.fnmatch(part, pat) for part in base.split("/"))
        if match:
            hit = not neg
    return hit


def _untracked(world, g):
    """The untracked files git would actually SHOW you — .gitignore hides the rest."""
    return {f for f in g["untracked"] if not _ignored(world, f)}


def _sha(cm):
    return cm.get("sha") or _stable_id(cm["msg"])


def _long_sha(cm):
    """The full 40-character object name `git log` prints, built out of the short
    one so it stays stable — and reseeded so it doesn't visibly repeat itself."""
    return (_sha(cm) + _stable_id(_sha(cm)))[:40]


def _base_branch(g):
    """The trunk every other branch in these repos was cut from."""
    for name in ("main", "master"):
        if name in g["branches"]:
            return name
    return g["branch"]


def _reachable(g, branch=None):
    """The commits `git log` may show from a branch — its own, the trunk's up to
    the point it forked, and anything it has merged in.

    This world stores history as one ordered list with a `branch` tag per
    commit, not a real DAG, so the fork point is approximated as the first
    commit carrying the branch's name. That approximation is exact for every
    repo the missions ship, and the alternative was far worse: `git log` on main
    listing a feature branch's work that had never been merged, which real git
    never does — and which made "merge it, then graph it" nonsense."""
    branch = branch or g["branch"]
    base = _base_branch(g)
    commits = g["commits"]
    if branch == base:
        keep = {base} | set(g["merged"])
        fork = len(commits)
    else:
        keep = {branch}
        fork = next((i for i, cm in enumerate(commits)
                     if cm.get("branch") == branch), len(commits))
    return [cm for i, cm in enumerate(commits)
            if cm.get("branch", base) in keep or (cm.get("branch") == base and i < fork)]


def _branch_tip(g, branch):
    """The sha at a branch's tip — what git echoes when it deletes the branch, so
    that `git checkout <sha>` and the reflog can still find the work."""
    tips = [cm for cm in _reachable(g, branch) if cm.get("branch") == branch]
    return _sha(tips[-1])[:7] if tips else _stable_id(branch)[:7]


def _decorate(g, cm, sha):
    """`(HEAD -> main, tag: v1.0.0)` — the labels git hangs off a commit line.
    Seeing HEAD move is how the pointer stops being an abstraction."""
    labels = []
    if g["commits"] and _sha(g["commits"][-1]) == sha:
        labels.append(f"HEAD -> {g['branch']}")
    labels += [f"tag: {t}" for t in sorted(t for t, s in g["tags"].items() if s == sha)]
    return c(f" ({', '.join(labels)})", "yellow") if labels else ""


def _is_rev(g, ref):
    return bool(re.fullmatch(r"HEAD(?:~\d+|\^+)?", ref)) or _resolve(g, ref) is not None


def _resolve(g, ref):
    """A revision -> (index, commit). Understands HEAD, HEAD~n, HEAD^, a tag and
    any sha prefix — the ways a student actually names a commit."""
    commits = g["commits"]
    if not commits:
        return None
    ref = ref.strip()
    m = re.fullmatch(r"HEAD(?:~(\d+)|(\^+))?", ref)
    if m:
        back = int(m.group(1)) if m.group(1) else len(m.group(2) or "")
        i = len(commits) - 1 - back
        return (i, commits[i]) if i >= 0 else None
    ref = g["tags"].get(ref, ref).lower()
    if len(ref) < 4:
        return None
    for i in range(len(commits) - 1, -1, -1):
        if _sha(commits[i]).startswith(ref):
            return i, commits[i]
    return None


def _diffstat(old, new):
    """(insertions, deletions) — the numbers git prints under a commit."""
    a = [] if old is None else old.split("\n")
    b = [] if new is None else new.split("\n")
    ins = dele = 0
    for line in difflib.unified_diff(a, b, lineterm="", n=0):
        if line.startswith("+") and not line.startswith("+++"):
            ins += 1
        elif line.startswith("-") and not line.startswith("---"):
            dele += 1
    return ins, dele


def _plural(ins, dele):
    """git's own ' 1 file changed, 2 insertions(+), 1 deletion(-)' tail."""
    bits = []
    if ins:
        bits.append(f"{ins} insertion{'s' if ins != 1 else ''}(+)")
    if dele:
        bits.append(f"{dele} deletion{'s' if dele != 1 else ''}(-)")
    return (", " + ", ".join(bits)) if bits else ""


def _print_patch(io, fname, old, new):
    """A real unified diff. difflib gives honest @@ hunk headers — learning to
    read those is half of learning `git diff`."""
    a = [] if old is None else old.split("\n")
    b = [] if new is None else new.split("\n")
    body = [ln for ln in difflib.unified_diff(a, b, lineterm="", n=3)
            if not ln.startswith("--- ") and not ln.startswith("+++ ")]
    if not body:
        return False
    io.print(f"diff --git a/{fname} b/{fname}")
    if old is None:
        io.print("new file mode 100644")
    elif new is None:
        io.print("deleted file mode 100644")
    # The `index <before>..<after>` line names the two blob objects the patch sits
    # between — the thing students always ask about when they first read a diff.
    # The trailing mode only appears when the mode did NOT change: a new or
    # deleted file already said its mode on the line above, and real git does
    # not repeat it.
    io.print(f"index {'0000000' if old is None else _stable_id(old)[:7]}.."
             f"{'0000000' if new is None else _stable_id(new)[:7]}"
             + (" 100644" if old is not None and new is not None else ""))
    io.print(c("--- " + ("/dev/null" if old is None else "a/" + fname), "red"))
    io.print(c("+++ " + ("/dev/null" if new is None else "b/" + fname), "green"))
    for line in body:
        if line.startswith("@@"):
            io.print(c(line, "cyan"))
        elif line.startswith("+"):
            io.print(c(line, "green"))
        elif line.startswith("-"):
            io.print(c(line, "red"))
        else:
            io.print(line)
    return True


def _tracking_line(g, io):
    """`git status` says how far your branch has drifted from origin's copy —
    the sentence that makes fetch-vs-pull visible instead of theoretical."""
    b = g["branch"]
    if b not in g["pushed"]:
        return
    # A negative "ahead" means history moved backwards under origin — a reset or
    # an amend. Git calls that behind, and it is why the next push is rejected.
    drift = len(g["commits"]) - g["pushed_at"].get(b, len(g["commits"]))
    # Status compares against your LOCAL copy of origin/main, so it cannot know
    # about commits you have never fetched — the reason `git fetch` exists.
    behind = (len(g["remote_new"]) if g["fetched"] else 0) + max(0, -drift)
    ahead = max(0, drift)
    if not ahead and not behind:
        io.print(f"Your branch is up to date with 'origin/{b}'.")
    elif ahead and not behind:
        io.print(f"Your branch is ahead of 'origin/{b}' by {ahead} commit{'s' if ahead != 1 else ''}.")
        io.print('  (use "git push" to publish your local commits)')
    elif behind and not ahead:
        io.print(f"Your branch is behind 'origin/{b}' by {behind} commit{'s' if behind != 1 else ''}, "
                 "and can be fast-forwarded.")
        io.print('  (use "git pull" to update your local branch)')
    else:
        io.print(f"Your branch and 'origin/{b}' have diverged,")
        io.print(f"and have {ahead} and {behind} different commits each, respectively.")
    io.print("")


def _status_body(world, g, io):
    """The body of `git status` — also what `git stash pop` prints when it hands
    your changes back, which is why it lives on its own."""
    io.print(f"On branch {g['branch']}")
    if not g["commits"]:
        io.print("\nNo commits yet\n")
    _tracking_line(g, io)
    if g["conflict"]:
        io.print("You have unmerged paths.")
        io.print('  (fix conflicts and run "git commit")')
        io.print('  (use "git merge --abort" to abort the merge)')
        io.print("")
        io.print("Unmerged paths:")
        io.print('  (use "git add <file>..." to mark resolution)')
        io.print(c(f"\tboth modified:   {g['conflict']}", "red"))
        return
    untracked = _untracked(world, g)
    if g["staged"]:
        io.print("Changes to be committed:")
        # Before the first commit there is no HEAD to restore from, so git offers
        # a different escape hatch — and says so.
        io.print('  (use "git restore --staged <file>..." to unstage)' if g["commits"]
                 else '  (use "git rm --cached <file>..." to unstage)')
        for f in sorted(g["staged"]):
            kind = "modified:   " if f in g["head_files"] else "new file:   "
            io.print(c(f"\t{kind}{f}", "green"))
        io.print("")
    if g["modified"]:
        io.print("Changes not staged for commit:")
        io.print('  (use "git add <file>..." to update what will be committed)')
        io.print('  (use "git restore <file>..." to discard changes in working directory)')
        for f in sorted(g["modified"]):
            io.print(c(f"\tmodified:   {f}", "red"))
        io.print("")
    if untracked:
        io.print("Untracked files:")
        io.print('  (use "git add <file>..." to include in what will be committed)')
        for f in sorted(untracked):
            io.print(c(f"\t{f}", "red"))
        io.print("")
    if not (g["staged"] or g["modified"] or untracked):
        io.print("nothing to commit, working tree clean" if g["commits"]
                 else 'nothing to commit (create/copy files and use "git add" to track)')
    elif g["staged"]:
        pass                       # something is staged: git adds no summary line
    elif g["modified"]:
        io.print('no changes added to commit (use "git add" and/or "git commit -a")')
    else:
        io.print('nothing added to commit but untracked files present (use "git add" to track)')


def _git_config(world, rest, io):
    cfg = _gitconfig(world)
    args = [a for a in rest if a not in ("--global", "--local", "--system", "--get")]
    if any(a in ("-l", "--list") for a in rest):
        world.flags["_noop"] = True
        for k, v in list(GIT_SYSTEM_CONFIG.items()) + sorted(cfg.items()):
            io.print(f"{k}={v}")
        if not (cfg.get("user.name") and cfg.get("user.email")):
            io.print(c('(no user.name/user.email here — on a real box the FIRST commit refuses '
                       'until you set them:  git config --global user.name "Your Name")', "dim"))
        return
    args = [a for a in args if not a.startswith("-")]
    if not args:
        world.flags["_noop"] = True
        io.print('usage: git config [--global] <key> <value>   ·   git config --list')
        io.print(c('(the two every machine needs once:  git config --global user.name "Your Name"  '
                   'and  git config --global user.email "you@example.com")', "dim"))
        return
    key = args[0]
    if len(args) == 1:                    # a read — real git prints the value, or nothing
        world.flags["_noop"] = True
        if key in cfg or key in GIT_SYSTEM_CONFIG:
            io.print(cfg.get(key, GIT_SYSTEM_CONFIG.get(key)))
        else:
            io.print(c(f"(nothing set for {key} — real git prints nothing and exits 1)", "dim"))
        return
    cfg[key] = " ".join(args[1:])
    # Real `git config` is silent on success. Silence in a game reads as "nothing
    # happened", so the sandbox echoes the write back once.
    io.print(c(f"({key} = {cfg[key]}  → saved to ~/.gitconfig · `git config --list` shows it)", "dim"))
    if key == "user.email" and "@" not in cfg[key]:
        io.print(c("(that isn't an email address — GitHub links commits to you by this "
                   "address, so use the one on your account)", "yellow"))
    elif key == "user.name":
        io.print(c("(--global = every repo on this machine. Per-repo: same command without it)", "dim"))


def _git_clone(world, rest, io):
    urls = [a for a in rest if not a.startswith("-")]
    if not urls:
        world.flags["_noop"] = True
        io.print("fatal: You must specify a repository to clone.")
        io.print(c("(usage: git clone <url> [dir] — the URL is behind the green Code button "
                   "on the repo page, HTTPS tab)", "dim"))
        return
    url = urls[0]
    if "://" not in url and not url.startswith("git@"):
        world.flags["_noop"] = True
        io.print(f"fatal: repository '{url}' does not exist")
        io.print(c("(clone takes a URL, not a repo name: "
                   "git clone https://github.com/<user>/<repo>.git)", "dim"))
        return
    name = urls[1] if len(urls) > 1 else url.rstrip("/").rsplit("/", 1)[-1]
    name = name[:-4] if name.endswith(".git") else name
    here = world.flags.get("repo_name")
    if name + "/" in world.files or name in world.files or (here and name == here):
        world.flags["_noop"] = True
        io.print(f"fatal: destination path '{name}' already exists and is not an empty directory.")
        if here and name == here:
            # Honest beats convenient: the mission already dropped you INSIDE this
            # clone, so re-cloning it here isn't the story the world is telling.
            io.print(c(f"(you are already standing inside {name} — the mission starts one "
                       "step after the clone. `git status` is the 'where am I' command.)", "dim"))
        return
    io.print(f"Cloning into '{name}'...")
    io.print("remote: Enumerating objects: 12, done.")
    io.print("remote: Counting objects: 100% (12/12), done.")
    io.print("remote: Compressing objects: 100% (9/9), done.")
    io.print("remote: Total 12 (delta 2), reused 12 (delta 2), pack-reused 0")
    io.print("Receiving objects: 100% (12/12), 3.94 KiB | 3.94 MiB/s, done.")
    io.print("Resolving deltas: 100% (2/2), done.")
    world.files[name + "/"] = ""
    world.flags["cloned"] = name
    world.flags["clone_url"] = url
    world.flags.setdefault("origin_url", url)
    io.print(c(f"(clone copied the FULL history into ./{name}/ and named that URL 'origin' "
               f"for you. On a real box the next command is: cd {name})", "dim"))
    if url.startswith("https://"):
        io.print(c("(over HTTPS git asks for your username and a PAT — never your account "
                   "password. Nothing echoes while you paste it; that is normal.)", "dim"))


def _git_show(world, g, rest, io):
    refs = [a for a in rest if not a.startswith("-")]
    ref = refs[0] if refs else "HEAD"
    if not g["commits"]:
        io.print("fatal: your current branch does not have any commits yet")
        world.flags["_noop"] = True
        return
    found = _resolve(g, ref)
    if not found:
        world.flags["_noop"] = True
        io.print(f"fatal: ambiguous argument '{ref}': unknown revision or path not in the working tree.")
        io.print(c("(copy a sha from `git log --oneline` — the 7 characters that start the line. "
                   "HEAD, HEAD~1 and a tag name work too.)", "dim"))
        return
    _i, cm = found
    world.flags["git_show"] = True
    # WHICH commit was opened, not just that one was — an objective that names a
    # specific commit cannot be satisfied by a bare `git show` of the tip.
    world.flags.setdefault("git_shown", set()).add(_sha(cm))
    name, email = _author(world)
    io.print(c(f"commit {_long_sha(cm)}", "yellow"))
    io.print(f"Author: {name} <{email}>")
    io.print(f"Date:   {cm.get('date', GIT_EPOCH)}")
    io.print(f"\n    {cm['msg']}\n")
    if "files" not in cm:
        io.print(c("(this commit was already in the repo when the mission opened — the sandbox "
                   "kept its message, not its patch. Commits you make here show a real diff.)", "dim"))
        return
    shown = False
    for f, new in sorted(cm["files"].items()):
        shown = _print_patch(io, f, cm.get("prev", {}).get(f), new) or shown
    if not shown:
        io.print(c("(no file changes in this commit — a merge commit only records the join)", "dim"))


def _git_restore(world, g, rest, io):
    staged_view = any(a in ("--staged", "--cached") for a in rest)
    names = [a for a in rest if not a.startswith("-")]
    if not names:
        world.flags["_noop"] = True
        io.print("fatal: you must specify path(s) to restore")
        io.print(c("(git restore --staged <file> un-stages · git restore <file> throws the "
                   "edit away — one arrow back up the pipeline, one out of it)", "dim"))
        return
    for f in names:
        if staged_view:
            if f not in g["staged"]:
                # git only complains when it has never heard of the path at all;
                # un-staging something that isn't staged is a silent no-op.
                if f in g["tracked"] or f in world.files:
                    io.print(c(f"(nothing staged for {f} — it is already out of the next "
                               "snapshot)", "dim"))
                else:
                    io.print(f"error: pathspec '{f}' did not match any file(s) known to git")
                continue
            g["staged"].discard(f)
            (g["modified"] if f in g["tracked"] else g["untracked"]).add(f)
            io.print(c(f"(un-staged {f} — your edit is untouched, it just isn't in the next "
                       "snapshot any more. `git status` spells this command out for you.)", "dim"))
        else:
            committed = g["head_files"].get(f)
            if committed is None:
                io.print(f"error: pathspec '{f}' did not match any file(s) known to git")
                io.print(c(f"(git can only restore a copy it already has — {f} was never committed)", "dim"))
                continue
            dirty = world.files.get(f) != committed
            world.files[f] = committed
            g["modified"].discard(f)
            if dirty:
                io.print(c(f"(restored {f} from the last commit — the uncommitted edit is GONE, "
                           "and restore has no undo. `git stash` is the reversible version.)", "dim"))
            else:
                io.print(c(f"({f} already matches the last commit — nothing to discard)", "dim"))


def _git_reset(world, g, rest, io):
    mode = next((a for a in rest if a in ("--hard", "--soft", "--mixed")), "--mixed")
    names = [a for a in rest if not a.startswith("-")]
    paths = [a for a in names if not _is_rev(g, a)]
    refs = [a for a in names if _is_rev(g, a)]

    if paths:                              # `git reset <file>` / `git reset HEAD <file>`
        for f in paths:
            if f in g["staged"]:
                g["staged"].discard(f)
                (g["modified"] if f in g["tracked"] else g["untracked"]).add(f)
        # git lists only the paths that actually left the index dirty — a file that
        # was already clean produces no line at all.
        moved = [f for f in sorted(paths) if f in g["modified"]]
        if moved:
            io.print("Unstaged changes after reset:")
            for f in moved:
                io.print(f"M\t{f}")
        io.print(c("(`git reset <file>` is the OLD spelling — modern git says "
                   "`git restore --staged <file>`, and `git status` suggests exactly that)", "dim"))
        return

    ref = refs[0] if refs else "HEAD"
    found = _resolve(g, ref)
    if not found:
        world.flags["_noop"] = True
        io.print(f"fatal: ambiguous argument '{ref}': unknown revision or path not in the working tree.")
        io.print(c("(HEAD~1 needs a commit behind you — `git log --oneline` shows how many there are)", "dim"))
        return
    i, _cm = found
    dropped = g["commits"][i + 1:]
    g["commits"] = g["commits"][:i + 1]
    touched = set()
    for d in reversed(dropped):            # walk the files those commits introduced back
        for f in d.get("files", {}):
            touched.add(f)
            prev = d.get("prev", {}).get(f)
            if prev is None:
                g["head_files"].pop(f, None)
                g["tracked"].discard(f)
            else:
                g["head_files"][f] = prev
    if mode == "--hard":
        for f, content in g["head_files"].items():
            world.files[f] = content
        for f in touched - set(g["head_files"]):
            world.files.pop(f, None)
        g["staged"], g["modified"] = set(), set()
    else:
        if mode == "--mixed":
            g["staged"] = set()
        for f in touched:
            if mode == "--soft":
                g["staged"].add(f)
            elif f in g["tracked"]:
                g["modified"].add(f)
            else:
                g["untracked"].add(f)
    head = g["commits"][-1]
    if mode == "--hard":
        io.print(f"HEAD is now at {_sha(head)[:7]} {head['msg']}")
    elif dropped and mode == "--mixed":
        io.print("Unstaged changes after reset:")
        for f in sorted(touched):
            io.print(f"M\t{f}")
    if not dropped:
        io.print(c("(nothing to drop — HEAD was already where you pointed it"
                   + (", and --hard threw every uncommitted edit away)" if mode == "--hard"
                      else ", so only the index moved)"), "dim"))
        return
    io.print(c(f"({len(dropped)} commit(s) erased from this branch — history REWRITTEN, not "
               "undone. Perfectly safe while it lives only on your machine.)", "dim"))
    if mode == "--hard":
        io.print(c("(--hard also deleted the work those commits held. There is no undo "
                   "prompt; that is the whole reputation of this flag.)", "yellow"))
    if g["branch"] in g["pushed"]:
        g["rewritten"] = True
        io.print(c("(⚠ origin already has those commits. Your next push will be REJECTED — "
                   "this is precisely the case `git revert` exists for.)", "yellow"))


def _git_revert(world, g, rest, io):
    refs = [a for a in rest if not a.startswith("-")]
    if not refs:
        world.flags["_noop"] = True
        io.print("fatal: empty commit set passed")
        io.print(c("(revert names the commit to undo: git revert <sha> — `git log --oneline` "
                   "has the shas)", "dim"))
        return
    found = _resolve(g, refs[0])
    if not found:
        world.flags["_noop"] = True
        io.print(f"fatal: bad revision '{refs[0]}'")
        io.print(c("(copy the 7-character sha from `git log --oneline`)", "dim"))
        return
    i, cm = found
    if g["staged"] or g["modified"]:
        io.print("error: your local changes would be overwritten by revert.")
        io.print("fatal: revert failed")
        io.print(c("(commit or stash what you're holding first — revert wants a clean tree)", "dim"))
        return
    if "files" not in cm:
        world.flags["_noop"] = True
        io.print(f"error: could not revert {_sha(cm)[:7]}... {cm['msg']}")
        io.print(c("(this commit came with the mission, so the sandbox has its message but not "
                   "its patch — there is nothing here to subtract. Revert one you made yourself.)", "dim"))
        return
    later = {f for d in g["commits"][i + 1:] for f in d.get("files", {})}
    if later & set(cm["files"]):
        # Undoing an old commit is a three-way merge, not a subtraction: when a
        # later commit touched the same file, real git stops and asks a human.
        world.flags["_noop"] = True
        io.print(f"error: could not revert {_sha(cm)[:7]}... {cm['msg']}")
        io.print('hint: after resolving the conflicts, mark the corrected paths')
        io.print("hint: with 'git add <paths>' and commit the result with 'git commit'")
        io.print(c("(a later commit changed the same file, so this undo isn't clean. Real git "
                   "hands you conflict markers here; this sandbox doesn't simulate a revert "
                   "conflict, so nothing was changed.)", "dim"))
        return
    msg = f'Revert "{cm["msg"]}"'
    files, prev = {}, {}
    for f in cm.get("files", {}):
        back = cm.get("prev", {}).get(f)     # the content the commit replaced
        prev[f] = g["head_files"].get(f)
        files[f] = back
        if back is None:                     # the commit CREATED the file — undo = delete it
            world.files.pop(f, None)
            g["head_files"].pop(f, None)
            g["tracked"].discard(f)
        else:
            world.files[f] = back
            g["head_files"][f] = back
    sha = _stable_id(f"revert:{_sha(cm)}:{len(g['commits'])}")
    g["commits"].append({"branch": g["branch"], "msg": msg, "sha": sha, "date": _now_stamp(),
                         "files": files, "prev": prev})
    world.flags["git_revert"] = True
    ins = sum(_diffstat(prev[f], files[f])[0] for f in files)
    dele = sum(_diffstat(prev[f], files[f])[1] for f in files)
    io.print(f"[{g['branch']} {sha[:7]}] {msg}")
    io.print(f" {len(files)} file{'s' if len(files) != 1 else ''} changed" + _plural(ins, dele))
    io.print(c("(real git opens an editor with that message pre-filled — `--no-edit` accepts "
               "it as-is, which is what happened here)", "dim"))
    io.print(c("(revert ADDS a commit that undoes an old one: history is preserved, so it is "
               "the safe choice on a branch other people already pulled. reset erases; revert "
               "answers.)", "dim"))


def _git_rm(world, g, rest, io):
    """`git rm --cached` — the second half of the .gitignore story: the rule
    stops NEW files, this is how you evict one you already committed."""
    names = [a for a in rest if not a.startswith("-")]
    cached = any(a in ("--cached",) for a in rest)
    if not names:
        world.flags["_noop"] = True
        io.print("fatal: No pathspec was given. Which files should I remove?")
        return
    if not cached:
        world.flags["_noop"] = True
        io.print("(plain `git rm <file>` deletes the file AND stages the deletion. This world "
                 "doesn't model a staged deletion, so it won't pretend to.)")
        io.print(c("   what you want here: `rm <file>` for the working tree, or "
                   "`git rm --cached <file>` to untrack it and KEEP it on disk", "dim"))
        return
    for f in names:
        if f not in g["tracked"]:
            io.print(f"fatal: pathspec '{f}' did not match any files")
            continue
        g["tracked"].discard(f)
        g["head_files"].pop(f, None)
        g["staged"].discard(f)
        g["modified"].discard(f)
        g["untracked"].add(f)
        io.print(f"rm '{f}'")
    io.print(c("(removed from the INDEX, kept on disk — git stops tracking it, and .gitignore "
               "keeps it out from here on. It is still in the OLD commits though: a leaked "
               "secret has to be rotated, not just ignored.)", "dim"))


def _git_stash(world, g, rest, io):
    action = next((a for a in rest if not a.startswith("-")), "push")
    include_untracked = any(a in ("-u", "--include-untracked") for a in rest)
    if action == "list":
        world.flags["_noop"] = True
        if not g["stash"]:
            io.print(c("(the stash is empty — `git stash` parks work here, `git stash pop` "
                       "takes it back)", "dim"))
        for n, e in enumerate(g["stash"]):
            io.print(f"stash@{{{n}}}: WIP on {e['branch']}: {e['at']}")
        return
    if action == "drop":
        if not g["stash"]:
            io.print("No stash entries found.")
            return
        e = g["stash"].pop(0)
        io.print(f"Dropped refs/stash@{{0}} ({_stable_id(e['at'])[:20]})")
        return
    if action in ("pop", "apply"):
        if not g["stash"]:
            io.print("No stash entries found.")
            io.print(c("(the stash is empty — `git stash list` is the pile, pop takes the top "
                       "entry off it)", "dim"))
            return
        e = g["stash"][0] if action == "apply" else g["stash"].pop(0)
        for f, content in e["files"].items():
            world.files[f] = content
        # A plain pop hands everything back as unstaged work (only `--index`
        # rebuilds the staged/unstaged split) — so the sets go back that way too.
        g["modified"] |= {f for f in e["modified"] + e["staged"] if f in g["tracked"]}
        g["untracked"] |= set(e["untracked"]) | {f for f in e["modified"] + e["staged"]
                                                 if f not in g["tracked"]}
        _status_body(world, g, io)
        if action == "pop":
            io.print(f"\nDropped refs/stash@{{0}} ({_stable_id(e['at'])[:20]})")
        else:
            io.print(c("\n(apply keeps the entry in the stash — pop takes it out. `git stash "
                       "list` to check.)", "dim"))
        return
    if action not in ("push", "save"):
        world.flags["_noop"] = True
        io.print(f"error: unknown subcommand: `{action}`")
        io.print(c("(this sandbox does: git stash · stash list · stash pop · stash apply · stash drop)", "dim"))
        return

    save = set(g["modified"]) | set(g["staged"])
    extra = _untracked(world, g) if include_untracked else set()
    if not save and not extra:
        io.print("No local changes to save")
        if _untracked(world, g):
            io.print(c("(the only changes here are UNTRACKED files, and stash ignores those by "
                       "default — `git stash -u` includes them)", "dim"))
        return
    head = g["commits"][-1] if g["commits"] else None
    at = f"{_sha(head)[:7]} {head['msg']}" if head else "0000000 (no commits yet)"
    g["stash"].insert(0, {"branch": g["branch"], "at": at,
                          "files": {f: world.files.get(f, "") for f in save | extra},
                          "modified": sorted(g["modified"]), "staged": sorted(g["staged"]),
                          "untracked": sorted(extra)})
    for f in save:                       # the working tree goes back to HEAD
        if f in g["head_files"]:
            world.files[f] = g["head_files"][f]
        else:
            world.files.pop(f, None)
    for f in extra:
        world.files.pop(f, None)
    g["modified"], g["staged"] = set(), set()
    g["untracked"] -= extra
    world.flags["git_stash"] = True
    io.print(f"Saved working directory and index state WIP on {g['branch']}: {at}")
    io.print(c("(your edits are parked and the tree is clean — switch branch, fix the urgent "
               "thing, then `git stash pop` to get them back. Not a commit: nothing is in history.)", "dim"))


def _git_tag(world, g, rest, io):
    names, flags, msg = [], [], None
    skip = False
    for i, a in enumerate(rest):
        if skip:
            skip = False
            continue
        if a == "-m":
            msg, skip = (rest[i + 1] if i + 1 < len(rest) else None), True
        elif a.startswith("-"):
            flags.append(a)
        else:
            names.append(a)
    if any(f in ("-d", "--delete") for f in flags):
        if not names:
            world.flags["_noop"] = True
            io.print("fatal: too few parameters")
            io.print(c("(-d needs the tag to delete: git tag -d v1.0.0)", "dim"))
            return
        for t in names:
            if t in g["tags"]:
                io.print(f"Deleted tag '{t}' (was {g['tags'][t][:7]})")
                del g["tags"][t]
                g["pushed_tags"].discard(t)
            else:
                io.print(f"error: tag '{t}' not found.")
        return
    if not names and (msg or flags):
        world.flags["_noop"] = True
        io.print("fatal: too few parameters")
        io.print(c('(a tag needs a name: git tag -a v1.0.0 -m "first release")', "dim"))
        return
    if not names:
        world.flags["_noop"] = True
        for t in sorted(g["tags"]):
            io.print(t)
        if not g["tags"]:
            io.print(c("(no tags yet — `git tag v1.0.0` pins a name to the commit you're on)", "dim"))
        elif any(t not in g["pushed_tags"] for t in g["tags"]):
            io.print(c("(tags stay LOCAL until you send them: git push origin <tag>. A plain "
                       "`git push` never carries tags.)", "dim"))
        return
    if not g["commits"]:
        io.print("fatal: Failed to resolve 'HEAD' as a valid ref.")
        io.print(c("(a tag labels a commit — make one first)", "dim"))
        return
    name = names[0]
    if name in g["tags"]:
        io.print(f"fatal: tag '{name}' already exists")
        io.print(c("(a tag is a promise: v1.0.0 means the same commit forever. Moving one "
                   "takes -f, and breaks everyone who already fetched it.)", "dim"))
        return
    g["tags"][name] = _sha(g["commits"][-1])
    world.flags["git_tag"] = True
    kind = "annotated" if msg or any(f in ("-a", "--annotate") for f in flags) else "lightweight"
    io.print(c(f"(tagged {name} → {g['tags'][name][:7]} ({kind}). Branches move as you commit; "
               f"tags never do. Publish it with: git push origin {name})", "dim"))


def _git_sync(world, g, sub, rest, io):
    """fetch and pull — same download, and one of them touches your files."""
    branch = g["branch"]
    repo = world.flags.get("repo_name", "repo")
    incoming = g["remote_new"]
    if sub == "pull" and branch not in g["pushed"]:
        io.print("There is no tracking information for the current branch.")
        io.print("Please specify which branch you want to merge with.")
        io.print(c(f"(the same missing link as the first push — wire them together once with: "
                   f"git push -u origin {branch})", "dim"))
        world.flags["_noop"] = True
        return
    if not incoming:
        world.flags["git_fetch" if sub == "fetch" else "git_pull"] = True
        if sub == "fetch":
            io.print(c("(nothing new on origin. Real `git fetch` prints NOTHING when it is up "
                       "to date — and it never touches your files, which is what makes it the "
                       "safe half of pull.)", "dim"))
        else:
            io.print("Already up to date.")
        return
    old = _sha(g["commits"][-1])[:7] if g["commits"] else "0000000"
    new = _stable_id(incoming[-1]["msg"])[:7]
    io.print(f"remote: Enumerating objects: {len(incoming) * 3}, done.")
    io.print(f"From github.com:you/{repo}")
    io.print(f"   {old}..{new}  {branch}     -> origin/{branch}")
    g["fetched"] = True
    if sub == "fetch":
        world.flags["git_fetch"] = True
        io.print(c(f"({len(incoming)} commit(s) downloaded and origin/{branch} moved. YOUR branch "
                   f"did not — nothing in your working tree changed. Look before you leap: "
                   f"git diff {branch} origin/{branch})", "dim"))
        return
    ahead = max(0, len(g["commits"]) - g["pushed_at"].get(branch, len(g["commits"])))
    how = next((a for a in rest if a in ("--rebase", "--no-rebase", "--ff-only", "--merge")), None)
    if ahead and not how:
        # Both sides moved. Since 2.34 git refuses to pick merge-or-rebase for you,
        # and being asked this question IS the lesson about the two shapes.
        io.print("hint: You have divergent branches and need to specify how to reconcile them.")
        io.print("hint:   git config pull.rebase false  # merge")
        io.print("hint:   git config pull.rebase true   # rebase")
        io.print("hint:   git config pull.ff only       # fast-forward only")
        io.print("fatal: Need to specify how to reconcile divergent branches.")
        io.print(c(f"(your {ahead} commit(s) and their {len(incoming)} both moved on from the same "
                   "point. `git pull --rebase` replays yours on top — a straight line, no merge "
                   "commit. `--no-rebase` merges instead.)", "dim"))
        return
    if ahead and how == "--ff-only":
        io.print("fatal: Not possible to fast-forward, aborting.")
        io.print(c("(--ff-only means 'only if it's a clean catch-up' — it isn't, so nothing "
                   "happened. That refusal is the flag doing its job.)", "dim"))
        return
    stat, plus, minus = [], 0, 0
    for cm in incoming:
        cm.setdefault("branch", branch)
        for f, content in cm.get("files", {}).items():
            ins, dele = _diffstat(g["head_files"].get(f), content)
            plus, minus = plus + ins, minus + dele
            stat.append(f" {f} | {ins + dele} " + "+" * ins + "-" * dele)
            world.files[f] = content
            g["head_files"][f] = content
            g["tracked"].add(f)
    # A rebase slots their commits UNDER yours and replays yours on top — new
    # shas and all. A merge just appends. That reordering is the whole difference.
    base = len(g["commits"]) - ahead if how == "--rebase" else len(g["commits"])
    g["commits"][base:base] = incoming
    for cm in g["commits"][base + len(incoming):]:
        cm["sha"] = _stable_id("rebase:" + _sha(cm))
    g["remote_new"] = []
    g["pushed_at"][branch] = g["pushed_at"].get(branch, len(g["commits"]) - len(incoming)) + len(incoming)
    world.flags["git_pull"] = True
    if how == "--rebase":
        io.print(f"Successfully rebased and updated refs/heads/{branch}.")
    else:
        io.print(f"Updating {old}..{new}")
        io.print("Fast-forward" if not ahead else "Merge made by the 'ort' strategy.")
    for line in stat:
        io.print(line)
    io.print(f" {len(stat)} file{'s' if len(stat) != 1 else ''} changed" + _plural(plus, minus))
    if how == "--rebase":
        io.print(c("(rebase replayed your commits on top of theirs — a straight line, and new "
                   "shas for yours: they are rewritten copies. Never do this to commits others "
                   "already pulled.)", "dim"))
    else:
        io.print(c("(pull = fetch + merge in one move — that second half is what edited your "
                   "files. `git pull --rebase` replays YOUR commits on top instead of adding a "
                   "merge commit.)", "dim"))


def _git_remote(world, g, rest, io):
    url = world.flags.get("origin_url") or f"https://github.com/you/{world.flags.get('repo_name', 'repo')}.git"
    if rest and rest[0] in ("add", "set-url"):
        if len(rest) < 3:
            io.print(f"usage: git remote {rest[0]} <name> <url>")
            world.flags["_noop"] = True
            return
        # Every repo in this world arrived by clone, so it already HAS an origin —
        # which is exactly the already-exists error a real second `add` gives you.
        if rest[0] == "add" and rest[1] == "origin":
            io.print("error: remote origin already exists.")
            io.print(c("(check first: `git remote -v`. Repointing an existing one is "
                       "`git remote set-url origin <url>`. `add` is for a repo made with "
                       "git init, which has no remote at all.)", "dim"))
            return
        if rest[1] != "origin":
            world.flags["_noop"] = True
            io.print(f"(this sandbox models one remote — origin. A second remote named "
                     f"'{rest[1]}' is real git, and real work: it is how you track the repo "
                     "you forked FROM.)")
            return
        world.flags["origin_url"] = rest[2]
        io.print(c(f"(origin now points at {rest[2]} — the name stayed, the URL moved. "
                   "That is all a remote is.)", "dim"))
        return
    world.flags["_noop"] = True
    if any(a in ("-v", "--verbose") for a in rest):
        io.print(f"origin\t{url} (fetch)")
        io.print(f"origin\t{url} (push)")
    else:
        io.print("origin")
    io.print(c("(origin = the conventional name for the remote you cloned from. Nothing magic "
               "about the word — you can have several remotes.)", "dim"))


def do_git(world, args, io):
    if args and args[0] in ("--version", "version") and len(args) == 1:
        world.flags["_noop"] = True
        io.print("git version 2.45.1")
        io.print(c("(check-first: a version answer = the tool is installed and on PATH)", "dim"))
        return
    if not args or (len(args) == 1 and args[0] in ("help", "--help")):
        world.flags["_noop"] = True
        io.print("usage: git <command> [<args>]\n")
        io.print("These are the commands this world simulates:")
        for name, summary in GIT_SUBS.items():
            io.print(f"   {name:<10} {summary}")
        io.print(c("\n(`git help <command>` explains one · `git <command> --help` is the same page)", "dim"))
        return
    sub, rest = args[0], args[1:]

    if sub in ("help", "--help") and rest:
        sub = rest[0]
        rest = ["--help"]
    if any(a in ("-h", "--help") for a in rest) and sub in GIT_SUBS:
        world.flags["_noop"] = True
        io.print(f"git {sub} — {GIT_SUBS[sub]}")
        io.print(c(f"(on your own machine the full manual is: git help {sub} — and `git {sub} -h` "
                   "is the one-screen flag summary. Ask the tool before you ask a search engine.)", "dim"))
        return

    # `git config --global` and `git clone` are the two commands you run when you
    # do NOT have a repo yet — so they answer before the no-git-world gate.
    if sub == "config":
        _git_config(world, rest, io)
        return
    if sub == "clone":
        _git_clone(world, rest, io)
        return

    g = world.git
    if g is None:
        world.flags["_noop"] = True
        io.print("This mission has no git world — try `task`.")
        return

    if sub == "status":
        world.flags["git_status"] = True
        if any(a in ("-s", "--short", "--porcelain") for a in rest):
            for f in sorted(g["staged"]):
                io.print(c(("M  " if f in g["head_files"] else "A  ") + f, "green"))
            for f in sorted(g["modified"]):
                io.print(c(" M " + f, "red"))
            for f in sorted(_untracked(world, g)):
                io.print(c("?? " + f, "red"))
            io.print(c("(short form: LEFT column = the index/staging area, RIGHT column = the "
                       "working tree. ?? = untracked.)", "dim"))
            return
        _status_body(world, g, io)
        hidden = sorted(f for f in g["untracked"] if _ignored(world, f))
        if hidden and any(a == "--ignored" for a in rest):
            io.print("Ignored files:")
            for f in hidden:
                io.print(c(f"\t{f}", "dim"))
        elif hidden and not world.flags.get("_ignore_taught"):
            world.flags["_ignore_taught"] = True
            io.print(c(f"({len(hidden)} file(s) hidden by .gitignore — that is how a .env stays "
                       "off GitHub. `git status --ignored` lists them.)", "dim"))

    elif sub == "add":
        force = any(a in ("-f", "--force") for a in rest)
        sweep = any(a in ("-A", "--all", "-u", "--update") for a in rest)
        names = [a for a in rest if not a.startswith("-")]
        if not names and not sweep:
            io.print("Nothing specified, nothing added.")
            io.print(c("(hint: maybe you wanted `git add .`?)", "dim"))
            return
        if sweep or names[0] in (".", "*"):
            targets = _untracked(world, g) | g["modified"]
        else:
            targets = set(names)
        blocked = [f for f in sorted(targets) if _ignored(world, f) and not force]
        if blocked:
            io.print("The following paths are ignored by one of your .gitignore files:")
            for f in blocked:
                io.print(f)
            io.print("hint: Use -f if you really want to add them.")
            io.print(c("(that is .gitignore doing its job — the .env-shaped disaster it exists "
                       "to prevent. If it SHOULD be tracked, the ignore rule is the bug.)", "dim"))
            return
        for f in sorted(targets):
            if f not in world.files:
                io.print(f"fatal: pathspec '{f}' did not match any files")
                return
            if _has_markers(world.files.get(f, "")):
                io.print(c(f"⚠️  '{f}' still contains conflict markers (<<<<<<< / ======= / >>>>>>>).", "yellow"))
                io.print(c("   Edit the file to the final content first (try: edit " + f + ")", "yellow"))
                return
            g["staged"].add(f)
            g["untracked"].discard(f)
            g["modified"].discard(f)

    elif sub == "commit":
        amend, stage_all, msg, paths = False, False, None, []
        empty_m = False
        i = 0
        while i < len(rest):
            a = rest[i]
            if a in ("-m", "--message", "-am", "-ma"):
                stage_all = stage_all or a in ("-am", "-ma")
                if i + 1 < len(rest):
                    msg = rest[i + 1]
                    i += 1
                else:
                    empty_m = True
            elif a.startswith("--message="):
                msg = a.split("=", 1)[1]
            elif a in ("-a", "--all"):
                stage_all = True
            elif a == "--amend":
                amend = True
            elif not a.startswith("-"):
                paths.append(a)
            i += 1
        if stage_all:
            for f in list(g["modified"]):
                g["staged"].add(f)
                g["modified"].discard(f)
        for f in paths:                    # `git commit <file> -m …` — the class cheat-sheet form
            if f not in world.files:
                io.print(f"fatal: pathspec '{f}' did not match any files")
                return
            if _has_markers(world.files.get(f, "")):
                io.print(c(f"⚠️  '{f}' still contains conflict markers — resolve it first "
                           "(edit " + f + "), then add and commit.", "yellow"))
                return
            if f not in g["staged"]:
                io.print(c(f"(pathspec form: `git commit {f} …` commits {f} even though you never "
                           "added it. Handy — but add-then-commit is the habit worth building.)", "dim"))
            g["staged"].add(f)
            g["modified"].discard(f)
            g["untracked"].discard(f)
        if empty_m:
            world.flags["_noop"] = True
            io.print("error: switch `m' requires a value")
            io.print(c('(the message is an ARGUMENT to -m, and it needs quotes when it has '
                       'spaces: git commit -m "add greet app")', "dim"))
            return
        if msg is None:
            # Real git opens an editor here. This world has no editor to hand you,
            # so it says what would have happened instead of quietly accepting.
            world.flags["_noop"] = True
            io.print("hint: Waiting for your editor to close the file...")
            io.print(c("(real git just opened vi with a comment block asking for the message" +
                       (" — pre-filled with the old one, for --amend" if amend else "") +
                       ". This sandbox has no editor to give you.)", "dim"))
            io.print("Aborting commit due to empty commit message.")
            io.print(c("(git's own wording when you save an empty one. Escape vi with Esc then "
                       ":wq — or skip the editor for good: git commit -m \"what changed and why\")", "dim"))
            return
        if amend:
            if not g["commits"]:
                io.print("fatal: You have nothing to amend.")
                world.flags["_noop"] = True
                return
            last = g["commits"][-1]
            was = _sha(last)[:7]
            for f in g["staged"]:
                last.setdefault("files", {})[f] = world.files.get(f, "")
                last.setdefault("prev", {}).setdefault(f, g["head_files"].get(f))
                g["head_files"][f] = world.files.get(f, "")
            g["tracked"] |= g["staged"]
            n = len(g["staged"])
            g["staged"] = set()
            last["msg"] = msg
            last["sha"] = _stable_id(f"amend:{was}:{msg}")
            io.print(f"[{g['branch']} {_sha(last)[:7]}] {msg}")
            if not n:
                # Amending only the message keeps the original author date, and git
                # prints it to say so — the commit is new, the authorship is not.
                io.print(" Date: " + last.get("date", GIT_EPOCH))
            ins = dele = 0
            for f, content in last.get("files", {}).items():
                plus, minus = _diffstat(last.get("prev", {}).get(f), content)
                ins, dele = ins + plus, dele + minus
            touched = len(last.get("files", {}))
            io.print(f" {touched} file{'s' if touched != 1 else ''} changed" + _plural(ins, dele))
            io.print(c(f"(amend REPLACED the last commit: {was} no longer exists, {_sha(last)[:7]} "
                       "took its place. Same work, new identity — that is a rewrite.)", "dim"))
            if g["branch"] in g["pushed"]:
                g["rewritten"] = True
                io.print(c("(⚠ that commit was already on origin. Rewriting published history means "
                           "the next push is rejected — amend BEFORE you push, never after.)", "yellow"))
            return
        if not g["staged"] and not g["conflict"]:
            io.print("nothing to commit, working tree clean")
            io.print(c("(nothing is STAGED — a commit only records what you added)", "dim"))
            return
        if g["conflict"] and g["conflict"] not in g["staged"]:
            io.print("fatal: cannot commit — resolve the conflict and `git add` the file first")
            return
        sha = _stable_id(f"{g['branch']}:{len(g['commits'])}:{msg}")
        ins = dele = 0
        for f in g["staged"]:
            plus, minus = _diffstat(g["head_files"].get(f), world.files.get(f, ""))
            ins, dele = ins + plus, dele + minus
        g["commits"].append({"branch": g["branch"], "msg": msg, "sha": sha, "date": _now_stamp(),
                             "files": {f: world.files.get(f, "") for f in g["staged"]},
                             "prev": {f: g["head_files"].get(f) for f in g["staged"]},
                             # Sealing a conflicted merge makes THIS the merge
                             # commit, whatever the player called it — the graph
                             # has to draw its second lane all the same.
                             **({"merged_from": world.flags.get("merging")} if g["conflict"] else {})})
        for f in g["staged"]:
            g["head_files"][f] = world.files.get(f, "")
        g["tracked"] |= g["staged"]
        n = len(g["staged"])
        g["staged"] = set()
        if g["conflict"]:
            g["merged"].add(world.flags.get("merging", "?"))
            g["conflict"], g["merge_backup"] = None, {}
        root = " (root-commit)" if len(g["commits"]) == 1 else ""
        io.print(f"[{g['branch']}{root} {sha[:7]}] {msg}")
        io.print(f" {n} file{'s' if n != 1 else ''} changed" + _plural(ins, dele))
        for f in sorted(g["commits"][-1]["files"]):
            if g["commits"][-1]["prev"].get(f) is None:
                io.print(f" create mode 100644 {f}")
        if not world.flags.get("gitconfig", {}).get("user.name") and not world.flags.get("_ident_taught"):
            world.flags["_ident_taught"] = True
            io.print(c(f"(committed as {GIT_ID[0]} <{GIT_ID[1]}> — this box came with an identity. "
                       "A fresh machine does not: git refuses the first commit until you run "
                       'git config --global user.name "…" and user.email "…")', "dim"))

    elif sub == "log":
        if not g["commits"]:
            io.print(f"fatal: your current branch '{g['branch']}' does not have any commits yet")
            return
        commits = _reachable(g)
        world.flags["git_log"] = True
        oneline = "--oneline" in rest
        # `--graph` is the flag the assignment asks you to submit a screenshot of,
        # so it has to draw something real: `*` per commit, and a merge that draws
        # the branch's OWN commits in the second lane. Prose in the graph column
        # cannot come from git, and this output is the artifact people hand in.
        graph = "--graph" in rest
        if graph:
            world.flags["git_graph"] = True
        name, email = _author(world)

        def short(cm):
            sha = _sha(cm)
            return f"{sha[:7]}{_decorate(g, cm, sha)} {cm['msg']}"

        if graph:
            drawn = set()
            for cm in reversed(commits):
                if id(cm) in drawn:
                    continue
                src = cm.get("merged_from")
                incoming = [x for x in reversed(commits)
                            if src and x is not cm and x.get("branch") == src]
                io.print(("*   " if incoming else "* ") + short(cm))
                if incoming:
                    io.print("|\\")
                    for x in incoming:
                        io.print("| * " + short(x))
                        drawn.add(id(x))
                    io.print("|/")
            return
        for cm in reversed(commits):
            if oneline:
                io.print(short(cm))
            else:
                io.print(c(f"commit {_long_sha(cm)}", "yellow") + _decorate(g, cm, _sha(cm)))
                io.print(f"Author: {name} <{email}>")
                io.print(f"Date:   {cm.get('date', GIT_EPOCH)}")
                io.print(f"\n    {cm['msg']}\n")

    elif sub == "show":
        _git_show(world, g, rest, io)

    elif sub == "restore":
        _git_restore(world, g, rest, io)

    elif sub == "reset":
        _git_reset(world, g, rest, io)

    elif sub == "revert":
        _git_revert(world, g, rest, io)

    elif sub == "rm":
        _git_rm(world, g, rest, io)

    elif sub == "stash":
        _git_stash(world, g, rest, io)

    elif sub == "tag":
        _git_tag(world, g, rest, io)

    elif sub in ("fetch", "pull"):
        _git_sync(world, g, sub, rest, io)

    elif sub == "remote":
        _git_remote(world, g, rest, io)

    elif sub == "branch":
        # The flags come first: `git branch -a` LISTS branches, and taking `-a`
        # as a name created a branch literally called "-a" — a silent lie the
        # player only discovers later, in the listing.
        flags = [a for a in rest if a.startswith("-")]
        names = [a for a in rest if not a.startswith("-")]
        delete = next((f for f in flags if f in ("-d", "-D", "--delete")), None)
        if delete and names:
            for target in names:
                if target == g["branch"]:
                    io.print(f"error: Cannot delete branch '{target}' checked out at "
                             f"'/root/{world.flags.get('repo_name', 'repo')}'")
                    # Naming the branch you are standing on as the one to switch
                    # to is advice that cannot work. Offer a real other branch.
                    away = next((b for b in sorted(g["branches"]) if b != target), None)
                    io.print(c(f"(switch away first: git checkout {away})" if away else
                               "(it is the only branch there is — there is nowhere to switch to, "
                               "and git will not leave you branchless)", "dim"))
                elif target not in g["branches"]:
                    io.print(f"error: branch '{target}' not found.")
                elif delete == "-D" or target in g["merged"]:
                    was = _branch_tip(g, target)
                    g["branches"].discard(target)
                    # The sha git echoes is the TIP COMMIT — that is the whole
                    # point of it echoing anything: `git checkout <sha>` and the
                    # reflog can still find the work you just unnamed.
                    io.print(f"Deleted branch {target} (was {was}).")
                else:
                    io.print(f"error: The branch '{target}' is not fully merged.")
                    io.print(c(f"(git refuses to drop work nobody kept. Merge it, or "
                               f"insist with: git branch -D {target})", "dim"))
            return
        if not names:
            for b in sorted(g["branches"]):
                io.print(("* " if b == g["branch"] else "  ") + b)
            if any(f in ("-a", "--all", "-r", "--remotes") for f in flags):
                for b in sorted(g["pushed"]):
                    io.print(c(f"  remotes/origin/{b}", "red"))
                io.print(c("(-a adds the remote-tracking branches — the ones you have "
                           "pushed. -r shows only those)", "dim"))
            return
        if names[0] in g["branches"]:
            io.print(f"fatal: a branch named '{names[0]}' already exists")
            io.print(c("(check-first: plain `git branch` lists what exists before you create)", "dim"))
            return
        g["branches"].add(names[0])
        io.print(c(f"(created branch '{names[0]}' — switch to it with checkout/switch)", "dim"))

    elif sub in ("checkout", "switch"):
        create = False
        if rest and rest[0] in ("-b", "-c"):
            create = True
            rest = rest[1:]
        if rest and rest[0] == "--":        # `git checkout -- <file>` = the old `git restore`
            _git_restore(world, g, rest[1:], io)
            io.print(c("(`git checkout -- <file>` is what people typed before `git restore "
                       "<file>` existed — same discard, older spelling)", "dim"))
            return
        if not rest:
            io.print(f"usage: git {sub} <branch>")
            return
        target = rest[0]
        if create:
            if target in g["branches"]:
                io.print(f"fatal: a branch named '{target}' already exists")
                io.print(c(f"(it exists — just switch to it: git {sub} {target})", "dim"))
                return
            g["branches"].add(target)
        if target not in g["branches"]:
            if _is_rev(g, target):
                # Checking out a raw commit is real, and detaching HEAD is a state
                # this world doesn't model — so say that instead of pretending.
                world.flags["_noop"] = True
                world.flags["detached_seen"] = True
                io.print(f"Note: switching to '{target}'.\n")
                io.print("You are in 'detached HEAD' state. You can look around, make experimental")
                io.print("changes and commit them, and you can discard any commits you make in this")
                io.print("state without impacting any branches by switching back to a branch.")
                io.print(c(f"(this sandbox keeps you on {g['branch']} — detached HEAD is a real "
                           "state it does not model. On a real box: `git switch -` goes back, and "
                           "`git switch -c rescue` keeps whatever you committed there.)", "dim"))
                return
            if target in world.files:
                # `git checkout <file>` with no `--` is a path restore, not a
                # branch switch — the overload that made `git restore` exist.
                _git_restore(world, g, [target], io)
                io.print(c("(that is a FILE, so checkout discarded its changes instead of "
                           "switching branch. One command, two jobs — which is exactly why "
                           "`git restore` and `git switch` were split out of it.)", "dim"))
                return
            io.print(f"error: pathspec '{target}' did not match any file(s) known to git")
            return
        g["branch"] = target
        for fname, content in g["branch_files"].get(target, {}).items():
            world.files[fname] = content
            g["head_files"][fname] = content
        io.print(f"Switched to branch '{target}'")

    elif sub == "merge":
        if any(a in ("--abort", "--quit") for a in rest):
            if not g["conflict"]:
                world.flags["_noop"] = True
                io.print("fatal: There is no merge to abort (MERGE_HEAD missing).")
                io.print(c("(--abort only has something to do while a merge is stuck — "
                           "`git status` says when that is)", "dim"))
                return
            fname = g["conflict"]
            for f, content in g["merge_backup"].items():
                world.files[f] = content
            g["merge_backup"], g["conflict"] = {}, None
            world.flags.pop("merging", None)
            io.print(c(f"(merge aborted — {fname} is back exactly as it was, markers and all "
                       "gone, and so is the merge. Nothing was lost: the branch is still there "
                       "to merge when you're ready.)", "dim"))
            return
        if not rest:
            io.print("usage: git merge <branch>")
            return
        other = rest[0]
        if other not in g["branches"]:
            io.print(f"merge: {other} - not something we can merge")
            return
        mine = g["branch_files"].get(g["branch"], {})
        theirs = g["branch_files"].get(other, {})
        clash = [f for f in mine if f in theirs and mine[f] != theirs[f]]
        world.flags["merging"] = other
        if clash:
            f = clash[0]
            g["merge_backup"] = {f: world.files.get(f, mine[f])}
            world.files[f] = (f"<<<<<<< HEAD\n{mine[f]}\n=======\n{theirs[f]}\n>>>>>>> {other}")
            g["conflict"] = f
            world.flags["conflict_seen"] = True
            io.print(f"Auto-merging {f}")
            io.print(c(f"CONFLICT (content): Merge conflict in {f}", "red"))
            io.print("Automatic merge failed; fix conflicts and then commit the result.")
        else:
            g["merged"].add(other)
            brought = {}
            for f, content in theirs.items():   # a clean merge really does bring their files over
                if f not in mine:
                    world.files[f] = content
                    g["head_files"][f] = content
                    g["tracked"].add(f)
                    brought[f] = content
            # `merged_from` is what lets `git log --graph` draw the incoming
            # commits in the second lane instead of narrating them.
            g["commits"].append({"branch": g["branch"], "msg": f"Merge branch '{other}'",
                                 "date": _now_stamp(), "files": {}, "prev": {},
                                 "merged_from": other})
            io.print("Merge made by the 'ort' strategy.")
            # Real git prints the diffstat of what the merge brought over — and
            # prints nothing when it brought nothing, which is the only case
            # this model can produce a no-op merge in.
            if brought:
                ins = sum(len(t.split("\n")) for t in brought.values())
                for f in sorted(brought):
                    lines = len(brought[f].split("\n"))
                    io.print(f" {f} | {lines} " + "+" * min(lines, 60))
                io.print(f" {len(brought)} file{'s' if len(brought) != 1 else ''} changed"
                         + _plural(ins, 0))
                for f in sorted(brought):
                    io.print(f" create mode 100644 {f}")

    elif sub == "push":
        repo = world.flags.get("repo_name", "repo")
        force = any(a in ("-f", "--force", "--force-with-lease") for a in rest)
        setup = "-u" in rest or "--set-upstream" in rest
        named = [a for a in rest if not a.startswith("-") and a != "origin"]
        tags = [a for a in named if a in g["tags"]]
        if tags or any(a in ("--tags", "--follow-tags") for a in rest):
            tags = tags or sorted(g["tags"])
            fresh = [t for t in tags if t not in g["pushed_tags"]]
            if not fresh:
                io.print("Everything up-to-date")
                io.print(c("(no tags exist yet — `git tag v1.0.0` makes one first)" if not tags
                           else "(those tags are already on origin — tags never move, so there "
                                "is nothing to re-send)", "dim"))
                return
            io.print(f"To github.com:you/{repo}.git")
            for t in fresh:
                io.print(f" * [new tag]         {t} -> {t}")
                g["pushed_tags"].add(t)
            io.print(c("(tags travel one at a time — a plain `git push` never carries them. "
                       "`--tags` sends the lot.)", "dim"))
            return
        branch = named[0] if named else g["branch"]
        if branch not in g["branches"]:
            io.print(f"error: src refspec {branch} does not match any")
            io.print(f"error: failed to push some refs to 'github.com:you/{repo}.git'")
            io.print(c("(you can only push something that exists — `git branch` and `git tag` "
                       "list what you have)", "dim"))
            return
        first_time = branch not in g["pushed"]
        if first_time and not setup:
            io.print("fatal: The current branch has no upstream branch.")
            io.print(f"    (use: git push -u origin {branch})")
            return
        if g["remote_new"] and not first_time and not force:
            # Someone else pushed while you were working. This is THE everyday
            # rejection, and its fix is the whole reason `git pull` exists.
            io.print(f"To github.com:you/{repo}.git")
            io.print(c(f" ! [rejected]        {branch} -> {branch} (fetch first)", "red"))
            io.print(f"error: failed to push some refs to 'github.com:you/{repo}.git'")
            io.print("hint: Updates were rejected because the remote contains work that you do")
            io.print("hint: not have locally. This is usually caused by another repository pushing")
            io.print("hint: to the same ref. If you want to integrate the remote changes, use")
            io.print("hint: 'git pull' before pushing again.")
            io.print(c("(nothing is broken — a teammate simply got there first. `git pull`, then "
                       "push. Never `--force` your way past this one.)", "dim"))
            return
        if g.get("rewritten") and not first_time and not force:
            io.print(f"To github.com:you/{repo}.git")
            io.print(c(f" ! [rejected]        {branch} -> {branch} (non-fast-forward)", "red"))
            io.print(f"error: failed to push some refs to 'github.com:you/{repo}.git'")
            io.print("hint: Updates were rejected because the tip of your current branch is behind")
            io.print("hint: its remote counterpart.")
            io.print(c("(you rewrote history origin already had. `git push --force-with-lease` is "
                       "the loaded gun; on a branch anyone else uses the answer is `git revert`.)", "dim"))
            return
        g["pushed"].add(branch)
        g["pushed_at"][branch] = len(g["commits"])
        g["rewritten"] = False
        io.print(f"To github.com:you/{repo}.git")
        io.print(f" * [new branch]      {branch} -> {branch}" if first_time else f"   {branch} -> {branch}")
        if first_time and setup:
            io.print(f"branch '{branch}' set up to track 'origin/{branch}'.")
        if force:
            io.print(c("(forced. Everyone else now has commits that no longer exist upstream — "
                       "only ever do this on a branch that is yours alone.)", "yellow"))

    elif sub == "diff":
        # `git diff` compares the WORKING TREE with the index; `--staged` compares
        # the index with HEAD. Showing both in one lump made staging invisible —
        # and "why is my diff empty after add?" is the lesson the drill is for.
        staged_view = any(a in ("--staged", "--cached") for a in rest)
        refs = [a for a in rest if not a.startswith("-")]
        if any("origin/" in r for r in refs):
            if not g["remote_new"]:
                io.print(c("(no difference — your branch and origin agree. `git fetch` first if "
                           "you want to be sure you're comparing against today's origin.)", "dim"))
                return
            for cm in g["remote_new"]:
                for f, content in cm.get("files", {}).items():
                    _print_patch(io, f, g["head_files"].get(f), content)
            io.print(c(f"({len(g['remote_new'])} commit(s) waiting on origin — this is what "
                       "`git pull` would bring in)", "dim"))
            return
        files = sorted(g["staged"] if staged_view else g["modified"])
        shown = False
        for f in files:
            old = g["head_files"].get(f)
            shown = _print_patch(io, f, old, world.files.get(f, "")) or shown
        if not shown:
            if not staged_view and g["staged"]:
                io.print(c("(nothing: `git diff` compares the working tree with what you "
                           "STAGED, and you already staged those changes — "
                           "`git diff --staged` shows them)", "dim"))
            else:
                io.print(c("(no changes)", "dim"))

    elif sub == "rebase":
        # Rebase rewrites commits one by one onto a new base. Faking that here
        # would teach a shape that isn't true — better to name it and move on.
        world.flags["_noop"] = True
        io.print("git: `rebase` is real, and this sandbox does not simulate it.")
        io.print(c("   What it does: replays YOUR commits on top of another branch, giving a "
                   "LINEAR history with no merge commit. `git pull --rebase` is the everyday "
                   "form. Same golden rule as amend and reset: never rebase commits you have "
                   "already pushed to a shared branch. Try it for real in the bonus assignment.", "dim"))

    else:
        world.flags["_noop"] = True
        io.print(f"git: '{sub}' is not a git command. See 'git --help'." + _suggest(sub, GIT_SUBS))
        io.print(c("(this world simulates: " + " · ".join(GIT_SUBS) + ")", "dim"))

# -------------------------------------------------------------- host shell --
def _mark_edited(world, fname):
    g = world.git
    if g is None:
        return
    if g["conflict"] == fname:
        return  # conflict resolution handled at add-time
    if fname in g["tracked"]:
        g["modified"].add(fname)
    else:
        g["untracked"].add(fname)


COMMON_IMAGES = {"ubuntu", "nginx", "alpine", "redis", "python", "busybox", "hello-world"}

# Every host-shell command do_host answers. Dispatch routes from it, `help` prints
# it and the did-you-mean suggester ranks against it — one list, so a command added
# to do_host can't quietly go missing from the manual (the 🐧 Linux missions run
# their own, much larger shell; this is the flat host world the other topics use).
HOST_CMDS = ("ls", "cat", "touch", "mkdir", "rm", "echo", "edit", "pwd", "whoami",
             "hostname", "clear", "date", "uname", "history", "which", "where",
             "command", "type")


def _host_pipe(world, args, io, produce):
    """`… | grep X`, `… | wc -l`, `… | head -n N` for the host shell.

    Without this a pipe was silently dropped and the unfiltered output printed —
    which reads as "grep matched everything" and teaches the opposite of the truth.
    """
    if "|" not in args:
        return None
    i = args.index("|")
    rest = args[i + 1:]
    if not rest:
        io.print("bash: syntax error near unexpected token `|'")
        world.flags["_noop"] = True
        return True
    text = produce(args[:i])
    if text is None:
        return True
    tool, targs = rest[0], rest[1:]
    if tool == "grep":
        pats = [a for a in targs if not a.startswith("-")]
        insensitive = any(a.startswith("-") and "i" in a for a in targs)
        invert = any(a.startswith("-") and "v" in a for a in targs)
        if not pats:
            io.print("usage: grep [OPTION]... PATTERN [FILE]...")
            return True
        pat = pats[0]
        keep = [ln for ln in text.split("\n")
                if ((pat.lower() in ln.lower()) if insensitive else (pat in ln)) != invert]
        if keep:
            io.print("\n".join(keep))
    elif tool == "wc":
        lines = [x for x in text.split("\n")]
        if lines and lines[-1] == "":
            lines.pop()
        io.print(str(len(lines)) if any("l" in a for a in targs if a.startswith("-"))
                 else f"{len(lines):>7} {len(text.split()):>7} {len(text):>7}")
    elif tool in ("head", "tail"):
        n = 10
        for j, a in enumerate(targs):
            if a == "-n" and j + 1 < len(targs) and targs[j + 1].isdigit():
                n = int(targs[j + 1])
            elif re.fullmatch(r"-\d+", a):
                n = int(a[1:])
        lines = [x for x in text.split("\n") if x != ""]
        io.print("\n".join(lines[:n] if tool == "head" else lines[-n:]))
    else:
        world.flags["_noop"] = True
        io.print(f"`{tool}` isn't wired into this world's pipes yet — "
                 "grep, wc, head and tail are.")
    return True


def do_host(world, prog, args, io):
    files = world.files

    def _produce(left):
        """Text that the left-hand side of a pipe would have printed."""
        if prog == "ls":
            return "\n".join(sorted(files))
        if prog == "cat":
            names = [a for a in left if not a.startswith("-")]
            out = []
            for n in names:
                if n in files:
                    out.append(files[n])
                else:
                    io.print(f"cat: {n}: No such file or directory")
                    return None
            return "\n".join(out)
        if prog == "echo":
            return " ".join(left)
        if prog == "history":
            return "\n".join(f"  {i}  {c}" for i, c in enumerate(world.history, 1))
        return None

    if _host_pipe(world, args, io, _produce):
        return
    if prog == "ls":
        target = next((a for a in args if not a.startswith("-")), None)
        if target:
            if target in files:
                io.print(target)
            else:
                io.print(f"ls: cannot access '{target}': No such file or directory")
                base = target.split(":")[0]
                if base in COMMON_IMAGES or any(i.startswith(base) for i in world.images):
                    io.print(c(f"({target} is a docker IMAGE, not a file — images are listed with: docker images)", "dim"))
        elif files:
            io.print("  ".join(sorted(files)))
        else:
            io.print(c("(the host folder is empty — this mission's action happens elsewhere; `task` shows where)", "dim"))
    elif prog == "pwd":
        io.print("/root/quest")
    elif prog == "whoami":
        io.print("root")
    elif prog == "hostname":
        io.print("quest-host")
    elif prog == "clear":
        # cursor home, clear screen, clear scrollback — what real `clear` sends.
        io.write("\033[H\033[2J\033[3J")
    elif prog == "date":
        from datetime import datetime
        io.print(datetime.now().strftime("%a %b %d %H:%M:%S %Y"))
    elif prog == "uname":
        flags = "".join(a[1:] for a in args if a.startswith("-"))
        if not flags:
            io.print("Linux")
        elif "a" in flags:
            io.print("Linux quest-host 6.8.0-quest #1 SMP x86_64 GNU/Linux")
        else:
            parts = []
            if "s" in flags: parts.append("Linux")
            if "n" in flags: parts.append("quest-host")
            if "r" in flags: parts.append("6.8.0-quest")
            if "m" in flags: parts.append("x86_64")
            io.print(" ".join(parts) or "Linux")
    elif prog == "history":
        for i, cmd in enumerate(world.history, 1):
            io.print(f"  {i}  {cmd}")
    elif prog in ("which", "where", "command", "type"):
        world.flags["_noop"] = True
        target = next((a for a in args if not a.startswith("-")), None)
        if not target:
            io.print(f"usage: {prog} <command>")
            return
        on_path = {"docker", "git", "kubectl", "minikube", "docker-compose", "helm", "terraform",
                   "ansible", "ansible-playbook", "ansible-doc", "argocd", "python", "python3",
                   "pip", "curl", "bash", "sh", "ls", "cat", "touch", "mkdir", "rm", "echo", "edit"}
        if target in on_path:
            io.print(f"{target} is /usr/bin/{target}" if prog == "type" else f"/usr/bin/{target}")
            io.print(c("(on PATH = installed — the check to run BEFORE any install step)", "dim"))
            if prog == "which":
                io.print(c("(`command -v` is the portable form — the one to use inside scripts)", "dim"))
        else:
            if prog == "which":
                io.print(f"which: no {target} in (/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin)")
            elif prog == "type":
                io.print(f"bash: type: {target}: not found")
            # `command -v` says nothing on failure — that silence is the point.
            if in_real_world(target):
                io.print(c(f"(not on this Linux-ish host — type `{target}` by itself to see how it maps here)", "dim"))
        if prog == "where":
            if PLAYER_OS == "windows":
                io.print(c("(`where` is right at home on your Windows box — but every server you'll "
                           "ever ssh into speaks `which`, so build that habit here)", "dim"))
            else:
                io.print(c(f"(`where` is the Windows spelling — you're on {os_label()}, "
                           "where the habit is `which`)", "dim"))
    elif prog == "mkdir" and args:
        parents = any(a.startswith("-") and "p" in a for a in args)
        for target in [a for a in args if not a.startswith("-")]:
            key = target.rstrip("/") + "/"
            if key in files and not parents:
                io.print(f"mkdir: cannot create directory '{target}': File exists")
                io.print(c("(-p makes mkdir idempotent: create if missing, quiet if it "
                           "already exists)", "dim"))
            else:
                files[key] = ""
    elif prog == "cat":
        if not args:
            io.print("cat: needs a file"); return
        io.print(files.get(args[0], f"cat: {args[0]}: No such file or directory"))
    elif prog == "touch" and args:
        for n in [a for a in args if not a.startswith("-")]:
            if n not in files:
                files[n] = ""
            _mark_edited(world, n)
    elif prog == "rm" and args:
        names = [a for a in args if not a.startswith("-")]
        force = any(a.startswith("-") and "f" in a for a in args)
        for n in names:
            if n in files:
                files.pop(n, None)
            elif not force:
                io.print(f"rm: cannot remove '{n}': No such file or directory")
    elif prog == "echo":
        # support: echo "text" > file
        op = next((x for x in (">>", ">") if x in args), None)
        if op:
            i = args.index(op)
            if i + 1 >= len(args):
                world.flags["_noop"] = True
                io.print("bash: syntax error near unexpected token `newline'")
                return
            text, fname = " ".join(args[:i]), args[i + 1]
            if op == ">>" and fname in files and files[fname]:
                files[fname] = files[fname].rstrip("\n") + "\n" + text
            else:
                files[fname] = text
            _mark_edited(world, fname)
        else:
            io.print(" ".join(args))
    elif prog == "edit":
        if not args:
            io.print("edit: needs a file (a tiny editor: type lines, end with a single '.')"); return
        fname = args[0]
        io.print(c(f"--- editing {fname} (current content below; type NEW content, end with a single '.') ---", "dim"))
        io.print(files.get(fname, "(new file)"))
        io.print(c("--- type new content now ---", "dim"))
        lines, aborted = [], False
        while True:
            try:
                line = io.input("… ")
            except KeyboardInterrupt:
                aborted = True
                io.print(c("^C  (edit cancelled — nothing written)", "yellow"))
                break
            except EOFError:
                io.print(c("(end of input — saving what you typed)", "dim"))
                break
            if line.strip() == ".":
                break
            lines.append(line)
        if aborted:
            world.flags["_noop"] = True
            return
        files[fname] = "\n".join(lines)
        _mark_edited(world, fname)
        io.print(c(f"saved {fname}", "dim"))
    else:
        world.flags["_noop"] = True
        io.print(f"{prog}: command not found (this simulated shell knows: ls, cat, touch, mkdir, rm, "
                 "echo, edit, pwd, whoami, clear, history)")


# ------------------------------------------------- real-world command atlas --
# Commands players type because they're REAL — recognized and redirected with a
# micro-lesson instead of a cold "command not found". (headline, dim follow-up)
# Keyed by DISTRO FAMILY where it matters: telling a Fedora student that `apt` is
# their package manager is exactly the kind of wrong the OS layer exists to prevent.
_PKG_HOME = {"winget": "windows", "choco": "windows", "scoop": "windows", "brew": "mac",
             "apt": "debian", "apt-get": "debian", "yum": "fedora", "dnf": "fedora",
             "flatpak": "linux", "snap": "debian"}


def _pkg_matches_player(prog):
    home = _PKG_HOME.get(prog)
    if home is None:
        return False
    if home in ("windows", "mac"):
        return PLAYER_OS == home
    if home == "linux":
        return PLAYER_OS == "linux"
    return PLAYER_OS == "linux" and PLAYER_DISTRO == home


def _PKG_MGR(prog="apt"):
    mgr, _install = pkg_mgr()
    yours = {"windows": "winget (or choco/scoop)", "mac": "brew"}.get(PLAYER_OS, mgr)
    head = ("🌍 `{cmd}` is real — a package manager. It installs APPS on your machine "
            "(e.g. it could install Docker itself).")
    tail = ("An IMAGE isn't an app though: Docker fetches those itself → "
            "docker pull <image>   (`setup` = the real install steps)")
    if _pkg_matches_player(prog):
        return head, f"`{prog}` is the right one for your {os_label()} box. " + tail
    return head, (f"`{prog}` belongs to another OS — on your {os_label()} box it's "
                  f"`{yours}`. " + tail)


_EDITOR = ("🌍 `{cmd}` is a real editor — this world ships a tiny one instead: edit <file>",
           "type the new content, finish with a single `.` on its own line")
REAL_WORLD = {
    "winget": _PKG_MGR, "choco": _PKG_MGR, "scoop": _PKG_MGR,
    "apt": _PKG_MGR, "apt-get": _PKG_MGR, "yum": _PKG_MGR, "dnf": _PKG_MGR, "brew": _PKG_MGR,
    "wsl": lambda p: pick({
        "windows": ("🌍 `wsl` opens a Linux shell on a real Windows box — good instinct!",
                    "it's how Docker Desktop runs containers on Windows at all. Here you're already "
                    "on a Linux-ish host, so docker & friends work right where you are"),
        # macOS is Unix but NOT Linux — it has no Linux kernel either, which is why
        # Docker Desktop runs a hidden Linux VM there too. Don't claim it has one.
        "mac": ("🌍 `wsl` is a WINDOWS thing — on macOS there's nothing to open.",
                "WSL gives Windows a Linux kernel; macOS is Unix but not Linux, so Docker "
                "Desktop runs its own small Linux VM for you — same trick, no command to type"),
        "*": ("🌍 `wsl` is a WINDOWS thing — you're on Linux, so there's nothing to bridge.",
              "WSL exists to give Windows a Linux kernel; your machine already has one"),
    }),
    "sudo": lambda p: pick({
        "windows": ("🌍 no `sudo` needed — you're already root in this world.",
                    "Windows has no sudo: the equivalent is an Administrator PowerShell, and "
                    "Docker Desktop doesn't need it once installed"),
        "mac": ("🌍 no `sudo` needed — you're already root in this world.",
                "on macOS Docker Desktop runs as your user — if you're typing `sudo docker`, "
                "you probably don't need to"),
        "*": ("🌍 no `sudo` needed — you're already root in this world.",
              "on your real Linux box docker DOES need root — the fix is one-time: "
              "`sudo usermod -aG docker $USER`, then log out and back in (`setup` explains)"),
    }),
    "ssh": ("🌍 `ssh` connects to a REMOTE machine — this world is a single host.",
            "the Ansible missions are where 'many machines' happens (agentless, over ssh — simulated)"),
    "ps": ("🌍 plain `ps` lists Linux processes — in docker-land the 'processes' are containers:",
           "docker ps (running) · docker ps -a (including stopped)"),
    "top": ("🌍 `top` watches Linux processes — here the things that run are containers:",
            "docker ps shows them · docker logs <name> shows what one is saying"),
    "htop": ("🌍 `htop` watches Linux processes — here the things that run are containers:",
             "docker ps shows them · docker logs <name> shows what one is saying"),
    "ifconfig": ("🌍 `ifconfig` shows real network interfaces — this world's networking is docker's:",
                 "docker network ls · docker network create <name>"),
    "ipconfig": lambda p: pick({
        "windows": ("🌍 `ipconfig` is the Windows one — this world's networking is docker's:",
                    "docker network ls · docker network create <name>"),
        # macOS DOES have an `ipconfig`, but it's a different program and there is
        # no `ip` on a Mac at all — don't send people to a command they don't have.
        "mac": ("🌍 macOS has an `ipconfig`, but it's not the Windows one — for 'show me my "
                "addresses' the Mac spelling is `ifconfig`.",
                "and in here, networking means docker's: docker network ls · docker network create <name>"),
        "*": ("🌍 `ipconfig` is the WINDOWS spelling — on Linux it's `ip a` (`ifconfig` is the "
              "deprecated older one).",
              "and in here, networking means docker's: docker network ls · docker network create <name>"),
    }),
    "ip": ("🌍 `ip` manages real Linux networking — this world's networking is docker's:",
           "docker network ls · docker network create <name>"),
    "cd": ("🌍 this world is one folder — no `cd` needed.",
           "`ls` lists the host's files; a container's files are separate (docker exec … to go inside)"),
    "vi": _EDITOR, "vim": _EDITOR, "nano": _EDITOR, "code": _EDITOR, "notepad": _EDITOR,
    "gedit": _EDITOR, "kate": _EDITOR, "emacs": _EDITOR,
    "podman": lambda p: pick({
        "linux": ("🌍 `podman` is Fedora's daemonless, rootless container engine — and it speaks docker.",
                  "same subcommands (`podman run`, `podman ps`); the `podman-docker` package even installs a "
                  "`docker` shim. This course grades `docker`, so type docker here — but knowing they're "
                  "interchangeable is a real Fedora superpower"),
        "*": ("🌍 `podman` is docker without a daemon — common on Fedora/RHEL boxes.",
              "same subcommands as docker; in this world, type docker"),
    }),
    "systemctl": lambda p: pick({
        "linux": ("🌍 `systemctl` controls real Linux services — the sim's daemon is always up.",
                  "on your box the docker daemon is a service: sudo systemctl enable --now docker "
                  "(podman needs no daemon at all).  `setup` has the full install"),
        "*": ("🌍 `systemctl` is Linux's service manager — this world's daemon is always running.",
              "on a real Linux host: sudo systemctl enable --now docker"),
    }),
    "flatpak": _PKG_MGR,
    "get-command": ("🌍 `Get-Command` is PowerShell's 'where does this live?' — the Linux spelling is `which`.",
                    "portable everywhere (scripts included): command -v <tool>"),
    "snap": _PKG_MGR,
    "man": ("🌍 `man` — reading the manual — is exactly the right instinct.",
            "here: `help` lists what works · `learn` opens the vault note · `hint` nudges the next step"),
    "curl": ("🌍 `curl` talks HTTP to a URL — it works the moment a mission serves something.",
             "publish a port first, then: curl localhost:<port>  (`task` shows what to build)"),
    "wget": ("🌍 `wget` downloads from a URL — but docker images don't come from URLs.",
             "they come from a REGISTRY (Docker Hub): docker pull <image>. INSIDE a container "
             "wget is the tool for reaching a peer by name: wget -qO- <other-container>"),
    "python": ("🌍 `python` runs here only where a mission provides a script (like the RabbitMQ producer).",
               "`task` shows what this mission actually needs"),
    "python3": ("🌍 `python3` runs here only where a mission provides a script (like the RabbitMQ producer).",
                "`task` shows what this mission actually needs"),
    "pip": ("🌍 `pip` installs Python packages — in docker-land dependencies bake into IMAGES instead.",
            "that's what a Dockerfile's `RUN pip install …` line is for (see the build missions)"),
}
for _img in ("ubuntu", "nginx", "alpine", "redis", "busybox"):
    REAL_WORLD[_img] = (f"🌍 `{_img}` is an IMAGE — a packaged filesystem, not a command.",
                        f"images are used THROUGH docker: docker pull {_img} → docker run {_img}")


def _fmt(text, prog):
    """{cmd} substitution that survives entries containing literal braces."""
    try:
        return text.format(cmd=prog)
    except (KeyError, IndexError, ValueError):
        return text


def in_real_world(prog):
    """Case-insensitive so PowerShell spellings (Get-Command) land too."""
    return prog in REAL_WORLD or prog.lower() in REAL_WORLD


def real_world_entry(prog):
    """REAL_WORLD values may be a (head, follow) tuple or a callable returning one
    (callables let an entry read PLAYER_OS at the moment it's printed)."""
    v = REAL_WORLD.get(prog) or REAL_WORLD[prog.lower()]
    return v(prog.lower()) if callable(v) else v


# ------------------------------------------------------ real-machine setup --
# What `setup` prints: how to install the REAL tools on the player's own box.
# The sim can't teach this — it's the one thing that genuinely differs per OS,
# and it's the wall most students hit before they ever reach a mission.
SETUP_STEPS = {
    "fedora": [
        ("Docker Engine", [
            "sudo dnf -y install dnf-plugins-core",
            "sudo dnf config-manager --add-repo https://download.docker.com/linux/fedora/docker-ce.repo",
            "sudo dnf -y install docker-ce docker-ce-cli containerd.io docker-compose-plugin",
            "sudo systemctl enable --now docker",
            "sudo usermod -aG docker $USER      # then LOG OUT and back in",
        ], "Fedora ships podman, not docker. `podman` is drop-in for most commands, but this "
           "course grades `docker` — install the real thing. The usermod line is what stops "
           "you needing sudo on every command."),
        ("kubectl", ["sudo dnf -y install kubernetes-client"], "or grab the upstream binary if you need a specific version"),
        ("minikube", [
            "curl -LO https://storage.googleapis.com/minikube/releases/latest/minikube-latest.x86_64.rpm",
            "sudo rpm -Uvh minikube-latest.x86_64.rpm",
        ], "minikube runs a one-node cluster on your laptop; it needs docker (or podman) working first"),
        ("Helm", ["sudo dnf -y install helm"], "if your Fedora is older than the helm package, use the official install script"),
    ],
    "debian": [
        ("Docker Engine", [
            "sudo apt-get update && sudo apt-get -y install ca-certificates curl",
            "sudo install -m 0755 -d /etc/apt/keyrings",
            "sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc",
            "sudo apt-get update && sudo apt-get -y install docker-ce docker-ce-cli containerd.io docker-compose-plugin",
            "sudo usermod -aG docker $USER      # then LOG OUT and back in",
        ], "the distro's own docker.io package lags badly — use Docker's repo"),
        ("kubectl", ["sudo apt-get -y install kubectl"], "needs the Kubernetes apt repo added first"),
        ("minikube", [
            "curl -LO https://storage.googleapis.com/minikube/releases/latest/minikube_latest_amd64.deb",
            "sudo dpkg -i minikube_latest_amd64.deb",
        ], ""),
        ("Helm", ["sudo apt-get -y install helm"], "or the official get-helm-3 script"),
    ],
    "mac": [
        ("Docker Desktop", ["brew install --cask docker"], "then LAUNCH Docker.app once — the CLI only works while the daemon runs"),
        ("kubectl", ["brew install kubectl"], "Docker Desktop bundles one too; `which kubectl` tells you which wins"),
        ("minikube", ["brew install minikube"], ""),
        ("Helm", ["brew install helm"], ""),
    ],
    "windows": [
        ("WSL2 (do this first)", ["wsl --install"], "Docker Desktop runs its Linux containers inside WSL2 — without it nothing works"),
        ("Docker Desktop", ["winget install Docker.DockerDesktop"], "launch it once and let it finish setting up before you open a terminal"),
        ("kubectl", ["winget install Kubernetes.kubectl"], ""),
        ("minikube", ["winget install Kubernetes.minikube"], ""),
        ("Helm", ["winget install Helm.Helm"], ""),
    ],
}


def setup_key():
    if PLAYER_OS == "linux":
        return PLAYER_DISTRO if PLAYER_DISTRO in ("fedora", "debian") else "debian"
    return PLAYER_OS


def print_setup(io):
    """The `setup` meta-command: install the real tools on the player's machine."""
    key = setup_key()
    io.print("")
    io.print(c(f"🧰 REAL-MACHINE SETUP — {os_label()}", "bold"))
    io.print(c("   This world is simulated; these are the commands for YOUR box.", "dim"))
    if PLAYER_OS == "linux" and PLAYER_DISTRO not in ("fedora", "debian"):
        io.print(c(f"   (no exact recipe for {PLAYER_DISTRO or 'this distro'} — showing the "
                   "Debian-family shape; swap in your package manager)", "yellow"))
    io.print(c("\n   ALWAYS check before installing — a reinstall can trample a working setup:", "cyan"))
    io.print(c("     docker --version · kubectl version --client · helm version · git --version", "dim"))
    for tool, cmds, note in SETUP_STEPS[key]:
        io.print(c(f"\n   {tool}", "bold"))
        for cmd in cmds:
            io.print(c(f"     $ {cmd}", "green"))
        if note:
            io.print(c(f"     {note}", "dim"))
    io.print(c("\n   Change OS:  `os linux` · `os mac` · `os windows`   (or: python quest.py --os <name>)\n", "dim"))

# What a version check prints for the tools that live in mission handlers. `setup`
# points players at these, so they must answer from any mission — and a tool that
# does NOT answer belongs here only by its absence. `rabbitmqctl` is the case in
# point: it ships inside the rabbitmq image, not on the host, and the RabbitMQ
# mission teaches exactly that. Claiming a version for it here would have the
# game contradict its own lesson one mission later.
TOOL_VERSION_LINES = {
    "helm": 'version.BuildInfo{Version:"v3.15.2", GitCommit:"1a500d5", '
            'GitTreeState:"clean", GoVersion:"go1.22.4"}',
    "terraform": "Terraform v1.9.0\non linux_amd64",
    "ansible": "ansible [core 2.17.1]\n  python version = 3.12.4",
    "ansible-playbook": "ansible-playbook [core 2.17.1]",
    "argocd": "argocd: v2.11.3+3f344d5",
    "ansible-doc": "ansible-doc [core 2.17.1]",
    "aws": "aws-cli/2.17.9 Python/3.11.9 Linux/6.8.0 exe/x86_64.fedora.40",
}

# Tools that ARE in the game, but live in other missions' handlers. `aws` earns
# its place here rather than in REAL_WORLD's "not simulated" atlas: Terraform's
# day-two mission answers `sts get-caller-identity`, `s3api create-bucket` and
# `s3 ls`, so calling it unsimulated stopped being true.
MISSION_TOOLS = {
    "helm": "the ⎈ Helm missions", "terraform": "the 🏗️ Terraform missions",
    "ansible": "the 📜 Ansible missions", "ansible-playbook": "the 📜 Ansible missions",
    "ansible-doc": "the 📜 Ansible missions", "argocd": "the 🔁 GitOps missions",
    "rabbitmqctl": "the 📨 RabbitMQ mission",
    "aws": "the 🏗️ Terraform missions (the S3 remote-state lab)",
}


# ----------------------------------------------------------------- mission --
def dispatch(world, line, io, mission):
    """Route one command line. Returns False if the player wants to leave."""
    try:
        args = shlex.split(line)
    except ValueError:
        # Python's own wording ("No closing quotation") would break the illusion —
        # and the fix the player needs is bash's, not Python's.
        quote = '"' if line.count('"') % 2 else "'"
        io.print(f"bash: unexpected EOF while looking for matching `{quote}'")
        io.print(c(f"   you opened a {quote} and never closed it — add the closing "
                   f"{quote} and run it again", "dim"))
        world.flags["_noop"] = True
        return True
    if not args:
        return True
    prog, rest = args[0], args[1:]

    # mission-scripted handlers first (they can override anything)
    for pattern, fn in mission.get("handlers", []):
        m = re.fullmatch(pattern, line.strip())
        if m:
            fn(world, m, io)
            return True

    if world.inside:
        if prog == "exit":
            name, world.inside = world.inside, None
            ctr = world.containers.get(name)
            if ctr and ctr.get("main_shell"):
                # That shell WAS the container's main process (started without -d),
                # so leaving it stops the container — Class 01's fourth gotcha.
                ctr["status"], ctr["main_shell"] = "exited", False
                io.print(c(f"container '{name}' stopped — that shell WAS its main process, and "
                           "the process exiting is the container exiting", "yellow"))
                if ctr.get("rm"):
                    del world.containers[name]
                    io.print(c("   --rm removed it as well: the container, its writable layer and "
                               "every file you made in it are gone. `docker run -dit` + "
                               "`docker exec` is the pattern that survives an exit.", "dim"))
            else:
                io.print(c(f"left container '{name}' — you're back on the host", "dim"))
        else:
            run_inside(world, world.inside, args, io)
        return True

    if prog == "sudo" and rest:
        io.print(c("(no sudo needed here — you're already root; running it anyway)", "dim"))
        if PLAYER_OS == "linux":
            io.print(c("   on your real box: sudo usermod -aG docker $USER, re-login, and you can "
                       "drop the sudo for good", "dim"))
        else:
            io.print(c("   on your real box Docker Desktop runs under your own user — there's no "
                       "sudo in that story at all", "dim"))
        return dispatch(world, shlex.join(rest), io, mission)

    if prog == "docker":
        do_docker(world, rest, io)
    elif prog == "git":
        do_git(world, rest, io)
    elif prog == "kubectl":
        do_kubectl(world, rest, io)
    elif prog == "minikube":
        do_minikube(world, rest, io)
    elif prog == "docker-compose":
        _do_compose(world, rest, io)
    elif prog in HOST_CMDS:
        do_host(world, prog, rest, io)
    elif prog == "ping":
        io.print("ping: works from INSIDE a container here — docker exec -it <name> bash, then ping <other>")
    # A published port answers for real. Anything else — a real URL, or a mission
    # with no containers — falls through to the 🌍 lesson further down.
    elif prog == "curl" and _host_curl(world, rest, io):
        pass
    elif prog in ("quit", "exit"):
        return False
    elif any(re.search(rf"\b{re.escape(prog)}\b", pat) for pat, _fn in mission.get("handlers", [])):
        # right tool for THIS mission, wrong form — encourage, don't wall
        world.flags["_noop"] = True
        io.print(f"`{prog}` is the right tool for this mission — that exact form just isn't wired up.")
        io.print(c("   `hint` points at the next step · `task` re-shows the goal", "dim"))
    elif in_real_world(prog):
        world.flags["_noop"] = True
        head, follow = real_world_entry(prog)
        io.print(_fmt(head, prog))
        io.print(c("   " + _fmt(follow, prog), "dim"))
    elif prog in MISSION_TOOLS:
        world.flags["_noop"] = True
        if (prog in TOOL_VERSION_LINES
                and rest and rest[0] in ("version", "--version", "-v", "--short")):
            io.print(TOOL_VERSION_LINES[prog])
            io.print(c("(it answered → it's installed. `setup` shows how it gets there)", "dim"))
            return True
        io.print(f"🌍 `{prog}` IS in the game — it lives in {MISSION_TOOLS[prog]}. This mission doesn't use it.")
        io.print(c("   `task` shows what THIS mission needs · `quit` returns to the map", "dim"))
    else:
        world.flags["_noop"] = True
        known = ["docker", "docker-compose", "git", "kubectl", "minikube",
                 *MISSION_TOOLS, *HOST_CMDS]
        io.print(f"`{prog}` isn't part of this simulated world (yet!)" + _suggest(prog, known))
        io.print(c("   `help` lists everything that works here · `hint` nudges the next objective", "dim"))
    return True


class _DemoFeed:
    """During demo playback, dispatch-time inputs (edit lines, login prompts)
    are fed from the solution script instead of the player."""

    def __init__(self, io, sol):
        self.io, self.sol = io, sol

    def input(self, prompt=""):
        if not self.sol:
            raise EOFError("demo script exhausted")
        line = self.sol.pop(0)
        self.io.print(prompt + c(line, "dim"))
        return line

    def print(self, *args):
        self.io.print(*args)

    def write(self, text):
        self.io.write(text)


def bind_completion(mission, world):
    """Wire the mission's own tab-completion into readline for its duration.

    Returns a restore callable — a mission's completer must not leak into the
    map screen, where filenames mean nothing.
    """
    fn = mission.get("complete")
    if not (_readline and fn):
        return lambda: None
    try:
        previous = _readline.get_completer()
        delims = _readline.get_completer_delims()

        def completer(text, state):
            try:
                matches = fn(world, text)
            except Exception:             # noqa: BLE001 — completion must never crash a mission
                matches = []
            return matches[state] if state < len(matches) else None

        _readline.set_completer(completer)
        # bash splits completion words on whitespace and operators, NOT on `/`
        # or `-`: `cd linux_course/we<TAB>` has to see the whole path.
        _readline.set_completer_delims(" \t\n;|&<>")
        _readline.parse_and_bind("bind ^I rl_complete"
                                 if "libedit" in (getattr(_readline, "__doc__", "") or "")
                                 else "tab: complete")

        def restore():
            _readline.set_completer(previous)
            _readline.set_completer_delims(delims)
        return restore
    except Exception:                     # noqa: BLE001 — a readline-less build must still play
        return lambda: None


def run_mission(mission, profile, io=None):
    """Run one mission. Returns (completed: bool, xp_earned: int, hints: int)."""
    io = io or IO()
    world = World(mission.get("world"))
    world.flags["repo_name"] = mission.get("repo_name", "repo")
    objectives = [dict(o, done=False) for o in mission["objectives"]]
    xp_earned, hints_used = 0, 0
    demo_used, user_cmds, studied = False, 0, False
    demo_sol = list(mission.get("solution", []))

    io.print("")
    io.print(c("═" * 62, "blue"))
    io.print(c(f"  🗡️  MISSION: {mission['title']}", "bold"))
    io.print(c("═" * 62, "blue"))
    io.print(mission["brief"])
    io.print(c(f"\n📖 pairs with the note: {mission.get('vault_note', '—')}"
               "   (`learn` reads it here · `learn cards` drills it)", "dim"))
    io.print(c("meta-commands: task · hint · demo (watch it solved!) · learn · help · quit\n", "dim"))

    def show_task():
        io.print(c("\n🎯 Objectives:", "bold"))
        for o in objectives:
            mark = c("✔", "green") if o["done"] else c("·", "dim")
            io.print(f"  {mark} {o['desc']}  {c('(+' + str(o['xp']) + ' XP)', 'dim')}")
        io.print("")

    show_task()

    teach = mission.get("teach", [])

    def check_objs(demo=False):
        nonlocal xp_earned
        for i, o in enumerate(objectives):
            if o["done"]:
                continue
            try:
                hit = o["check"](world)
            except Exception as exc:              # noqa: BLE001
                # A check runs after EVERY command, so a check that raises turns
                # one legal move into a crash that eats the rest of the session
                # (`kubectl delete deployment skywatch` used to do exactly that).
                # Report it loudly enough to be fixed, quietly enough to play on.
                # A scripted run — the selftest — still raises, so CI cannot miss it.
                if getattr(io, "script", None) is not None:
                    raise
                io.print(c(f"   ⚠ internal: the check for “{o['desc']}” raised "
                           f"{type(exc).__name__}: {exc} — please report this. The mission "
                           "continues; that objective just won't tick.", "yellow"))
                continue
            if hit:
                o["done"] = True
                if demo:
                    io.print(c(f"  ✔ (demo) objective complete: {o['desc']}  — no XP for watching 😉", "green"))
                else:
                    xp_earned += o["xp"]
                    io.print(c(f"  ✔ OBJECTIVE COMPLETE: {o['desc']}  (+{o['xp']} XP)", "green"))
                if i < len(teach):
                    io.print(c(f"     📚 {teach[i]}", "cyan"))

    restore_completion = bind_completion(mission, world)
    try:
        while True:
            # A mission with its own shell draws its own prompt (the Linux ones put
            # the working directory in it, because every relative path depends on it).
            prompt = (mission["prompt"](world) if mission.get("prompt")
                      else c(f"({world.inside}) $ " if world.inside else "$ ", "cyan"))
            try:
                line = io.input(prompt)
            except (EOFError, KeyboardInterrupt):
                io.print(c("\nleaving mission — progress in this mission isn't saved mid-way", "yellow"))
                return False, xp_earned, hints_used

            line, raw_keys = edit_keys(line)
            if raw_keys:
                io.print(c(f"   (your {' '.join(dict.fromkeys(raw_keys))} keypress"
                           f"{'es' if len(raw_keys) > 1 else ''} arrived as raw characters and "
                           "were dropped — this terminal gave Python no line editor", "yellow"))
                io.print(c("    on Windows: `pip install pyreadline3`, or use Windows Terminal / "
                           "WSL. Retype the line and it will run.", "dim"))
                continue

            stripped = line.strip().lower()
            if stripped == "task":
                show_task(); continue
            if stripped == "learn" or stripped.startswith("learn "):
                # The study half of the game. It reads the player's own vault, so
                # every failure mode is somebody else's filesystem — it must never
                # take the mission down with it.
                try:
                    import study
                    if study.learn(io, profile, mission.get("vault_note", ""),
                                   line.strip()[5:]):
                        studied = True
                    # Mastery is earned the moment it happens, not when the
                    # mission is won — quitting must not cost you a drilled deck.
                    if "xp" in profile:               # a real profile, not a test stub
                        save_profile(profile)
                except Exception as exc:              # noqa: BLE001
                    io.print(c(f"📖 the vault couldn't be read ({exc}) — the note for this "
                               f"mission is: {mission.get('vault_note', '—')}", "yellow"))
                continue
            if stripped == "setup":
                print_setup(io)
                continue
            if stripped == "os" or stripped.startswith("os "):
                arg = stripped[3:].strip()
                if not arg:
                    io.print(c(f"🖥️  teaching for: {os_label()}   (change: os linux · os mac · os windows)", "cyan"))
                elif arg in OS_NAMES:
                    set_player_os(arg)
                    profile["os"] = arg
                    io.print(c(f"🖥️  real-machine tips now target {os_label()}.", "green"))
                    io.print(c("   `setup` shows how to install the real tools there.", "dim"))
                else:
                    io.print(c(f"unknown OS '{arg}' — pick one of: linux · mac · windows", "yellow"))
                continue
            if stripped == "help":
                io.print(c("🧭 meta:  task (objectives) · hint (nudge, -5 XP) · demo (watch it solved) · "
                           "learn (📖 the note: cards · quiz · drills · find) · "
                           "setup (install the real tools) · os (your OS) · "
                           "quit (back to map)", "cyan"))
                # A mission with a real shell behind it prints its own manual, with a
                # page per command — a flag cheat-sheet is a dead end the moment you
                # need the flag it left out.
                if mission.get("help_fn"):
                    mission["help_fn"](io)
                    continue
                # A mission whose world isn't the default one describes its own toolbox.
                for line in mission.get("help_lines") or [
                    "   tools: docker · git · kubectl · minikube — plus whatever the mission brings "
                    "(helm/terraform/ansible/…)",
                    "   shell: " + " · ".join(HOST_CMDS) + "    (edit <file> = the tiny editor)",
                ]:
                    io.print(c(line, "dim"))
                io.print(c("   type real commands — the world reacts like the real tools would", "dim"))
                continue
            if stripped == "hint":
                pending = next((o for o in objectives if not o["done"]), None)
                if pending:
                    hints_used += 1
                    xp_earned = max(0, xp_earned - 5)
                    io.print(c(f"💡 {pending['hint']}  (–5 XP)", "yellow"))
                continue
            if stripped in ("demo", "demo!"):
                if user_cmds and stripped == "demo":
                    io.print(c("🎬 demo replays the solution from a FRESH world — but you've already made moves.", "yellow"))
                    io.print(c("   `demo!` resets the world and plays from the top (XP earned this run is cleared)", "dim"))
                    continue
                if stripped == "demo!" and user_cmds:
                    world = World(mission.get("world"))
                    world.flags["repo_name"] = mission.get("repo_name", "repo")
                    restore_completion()          # completion follows the new world
                    restore_completion = bind_completion(mission, world)
                    for o in objectives:
                        o["done"] = False
                    xp_earned, user_cmds = 0, 0
                    demo_sol = list(mission.get("solution", []))
                    io.print(c("🔄 fresh world — watching from the top", "magenta"))
                if not demo_sol:
                    io.print(c("this mission has no demo script", "yellow")); continue
                demo_used = True
                io.print(c("\n🎬 DEMO MODE — watch a correct solution play out, step by step.", "magenta"))
                io.print(c("   ⏎ Enter = next step · `takeover` = grab the keyboard · `stop` = leave demo\n", "dim"))
                while demo_sol:
                    cmd = demo_sol.pop(0)
                    shown = (mission["prompt"](world) if mission.get("prompt") else c("$ ", "cyan"))
                    io.print(shown + c(cmd, "bold") + c("   ⟵ demo", "dim"))
                    try:
                        dispatch(world, cmd, _DemoFeed(io, demo_sol), mission)
                    except EOFError:
                        break
                    world.flags.pop("_noop", None)
                    check_objs(demo=True)
                    if all(o["done"] for o in objectives) or not demo_sol:
                        break
                    try:
                        nxt = io.input(c("   ⏎ next · takeover · stop > ", "dim")).strip().lower()
                    except (EOFError, KeyboardInterrupt):
                        nxt = "stop"
                    if nxt == "takeover":
                        io.print(c("\n🎮 the keyboard is YOURS — finish the remaining objectives for real XP\n", "magenta"))
                        show_task()
                        break
                    if nxt in ("stop", "quit", "exit"):
                        io.print(c("left demo — the world stays as the demo left it; play on or `quit`", "yellow"))
                        break
                if all(o["done"] for o in objectives):
                    io.print("")
                    io.print(c("🎬 DEMO COMPLETE — you watched the whole solution.", "magenta"))
                    io.print(c("   Nothing was recorded: replay the mission and type it YOURSELF to earn the XP. 💪", "bold"))
                    return False, 0, hints_used
                continue

            world.history.append(line.strip())
            try:
                keep_playing = dispatch(world, line, io, mission)
            except (EOFError, KeyboardInterrupt):
                # Some commands ask a second question — `docker login`'s Username,
                # `system prune`'s [y/N], terraform's "Enter a value:". Ctrl-C /
                # Ctrl-D there aborts THAT command in a real terminal; it must not
                # take the whole game down with it. Scripted runs keep raising, so
                # the selftest still reports a solution that ran out of answers.
                if getattr(io, "script", None) is not None:
                    raise
                io.print("")
                io.print(c(f"^C  `{line.strip().split()[0] if line.strip() else 'command'}` "
                           "cancelled — nothing was changed", "yellow"))
                continue
            if world.flags.pop("_noop", None) is None:
                user_cmds += 1          # unrecognized/teach-only lines don't count as "moves"
            if not keep_playing:
                io.print(c("left the mission — run it again anytime", "yellow"))
                return False, xp_earned, hints_used

            check_objs()

            if all(o["done"] for o in objectives):
                bonus = 10 if hints_used == 0 and not demo_used else 0
                # Looking it up in the notes is the professional reflex; asking
                # the game for the answer isn't. The economy should say so — the
                # scholar who never typed `hint` ends up ahead.
                scholar = 5 if studied and hints_used == 0 else 0
                xp_earned += bonus + scholar
                io.print("")
                io.print(c("🏆 MISSION COMPLETE!", "green") + (c(f"  +{bonus} XP no-hint bonus!", "magenta") if bonus else ""))
                if scholar:
                    io.print(c(f"  📖 +{scholar} XP SCHOLAR BONUS — you read the note instead "
                               "of asking for a hint.", "magenta"))
                if demo_used:
                    io.print(c("   (finished after a demo assist — demoed objectives paid no XP)", "dim"))
                io.print(c(f"   earned {xp_earned} XP · hints used: {hints_used}", "bold"))
                if teach:
                    io.print(c("\n📚 What you just practiced:", "cyan"))
                    for line in teach[:len(objectives)]:
                        io.print(c(f"   • {line}", "dim"))
                return True, xp_earned, hints_used
    finally:
        restore_completion()


# ----------------------------------------------------------------- profile --
PROFILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "progress.json")
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "quest.config.json")


def load_profile():
    if os.path.exists(PROFILE_PATH):
        with open(PROFILE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"name": None, "xp": 0, "completed": {}}


def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_config(config):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


def sync_vault_note(profile):
    """If the player linked an Obsidian vault (quest.config.json — gitignored),
    render progress as a markdown note there. Never breaks the game on failure."""
    target = load_config().get("vault_progress_file")
    if not target:
        return None
    try:
        from datetime import datetime
        from missions import ALL_MISSIONS, TOPICS
        lvl, lvl_name = level(profile["xp"])
        done = profile["completed"]
        # +10 no-hint, +5 scholar: the ceiling a player who studies can reach.
        total_xp_possible = sum(sum(o["xp"] for o in m["objectives"]) + 15 for m in ALL_MISSIONS)
        lines = [
            "---",
            "tags: [devops, shell-quest, progress, auto-generated]",
            "---",
            "",
            "# 🗡️ Shell Quest Progress",
            "",
            f"> [!success] {profile.get('name') or 'player'} — Level {lvl} **{lvl_name}** · "
            f"{profile['xp']} XP · {len(done)}/{len(ALL_MISSIONS)} missions",
            f"> Auto-written by the game on every save (last: {datetime.now():%Y-%m-%d %H:%M}). "
            f"Don't edit — play instead: `python quest.py`. Ladder home: [[🎓 Mastery Path]].",
            "",
            "| # | Mission | Topic | Status | Best XP |",
            "|---|---|---|---|---|",
        ]
        for n, m in enumerate(ALL_MISSIONS, 1):
            rec = done.get(m["id"])
            status = "✅" if rec else "🔓"
            best = str(rec["xp"]) if rec else "—"
            lines.append(f"| {n} | {m['title']} | {TOPICS[m['topic']]} | {status} | {best} |")
        lines += [
            "",
            f"Earned {profile['xp']} of ~{total_xp_possible} XP available across all missions.",
            "",
            "**Per-topic:** " + " · ".join(
                f"{TOPICS[t]} {sum(1 for m in ALL_MISSIONS if m['topic'] == t and m['id'] in done)}"
                f"/{sum(1 for m in ALL_MISSIONS if m['topic'] == t)}"
                for t in TOPICS),
            "",
        ]
        # What `learn` did inside the game, written back into the vault it read —
        # the study half of the loop deserves to show up next to the missions.
        study = {k: v for k, v in (profile.get("study") or {}).items()
                 if v.get("cards_known") or v.get("quiz_best") or v.get("reads")}
        if study:
            lines += ["## 📖 Study (from `learn` in the game)", "",
                      "| Note | Cards mastered | Best self-check | Opened |",
                      "|---|---|---|---|"]
            for note, rec in sorted(study.items()):
                cards = len(rec.get("cards_known", []))
                quiz = (f"{rec['quiz_best']}/{rec.get('quiz_total', '?')}"
                        if rec.get("quiz_best") else "—")
                star = " 🌟" if rec.get("perfect") else ""
                lines.append(f"| [[{note}]] | {cards}{star} | {quiz} | {rec.get('reads', 0)}× |")
            lines.append("")
        if profile.get("badges"):
            lines += ["**Badges:** " + " · ".join(profile["badges"]), ""]
        with open(target, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return target
    except Exception:
        return None  # a broken vault path must never kill the game


def save_profile(profile):
    with open(PROFILE_PATH, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2)
    sync_vault_note(profile)


def level(xp):
    names = ["Rookie", "Tinkerer", "Operator", "Engineer", "Senior", "DevOps Legend"]
    lvl = min(xp // 100, len(names) - 1)
    return lvl + 1, names[lvl]
