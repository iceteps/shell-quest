"""Shell Quest engine — a tiny simulated DevOps world.

Simulates just enough docker / git / shell for the missions to feel real:
state lives in a World object, commands are parsed and mutate it, and
missions win by checking that state (never by matching your exact keystrokes —
any correct route works).
"""
import json
import os
import random
import re
import shlex
import sys

# ---------------------------------------------------------------- terminal --
os.system("")  # enable ANSI on Windows
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdin.reconfigure(encoding="utf-8")
except Exception:
    pass

COLORS = {
    "reset": "\033[0m", "bold": "\033[1m", "dim": "\033[2m",
    "green": "\033[92m", "red": "\033[91m", "yellow": "\033[93m",
    "cyan": "\033[96m", "magenta": "\033[95m", "blue": "\033[94m",
}


def c(text, color):
    return f"{COLORS[color]}{text}{COLORS['reset']}"


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
        return input(prompt)

    def print(self, *args):
        print(*args)


# ------------------------------------------------------------------- world --
ADJ = ["brave", "sleepy", "witty", "cosmic", "mellow", "rusty"]
NOUN = ["panda", "otter", "falcon", "moose", "cactus", "walrus"]


def _rand_name():
    return f"{random.choice(ADJ)}_{random.choice(NOUN)}"


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
        self.flags = {}                                 # mission scratch space
        g = spec.get("git")
        self.git = None
        if g is not None:
            self.git = {
                "branch": g.get("branch", "main"),
                "branches": set(g.get("branches", ["main"])),
                "commits": list(g.get("commits", [])),   # {branch, msg}
                "tracked": set(g.get("tracked", [])),
                "staged": set(), "modified": set(),
                "untracked": set(g.get("untracked", [])),
                "branch_files": {k: dict(v) for k, v in g.get("branch_files", {}).items()},
                "conflict": None, "merged": set(), "pushed": set(),
            }

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
def _parse_run_flags(args):
    f = {"d": False, "it": False, "rm": False, "name": None, "network": "bridge", "ports": []}
    pos, i = [], 0
    while i < len(args):
        a = args[i]
        if a == "--rm":
            f["rm"] = True
        elif a == "--name":
            i += 1; f["name"] = args[i]
        elif a == "--network":
            i += 1; f["network"] = args[i]
        elif a in ("-p", "--publish"):
            i += 1; f["ports"].append(args[i])
        elif a in ("--detach",):
            f["d"] = True
        elif a.startswith("-") and not a.startswith("--") and set(a[1:]) <= set("dit") and a[1:]:
            if "d" in a: f["d"] = True
            if "i" in a or "t" in a: f["it"] = True
        elif a.startswith("-"):
            return None, a, None
        else:
            pos.append(a)
        i += 1
    return f, None, pos


def do_docker(world, args, io):
    if not args:
        io.print("Usage: docker COMMAND  (try: pull, run, ps, exec, logs, stop, rm, images, network)")
        return
    sub, rest = args[0], args[1:]

    if sub == "pull":
        if not rest:
            io.print("docker pull: needs an image"); return
        img = world.norm_image(rest[0])
        io.print(f"{img.split(':')[1]}: Pulling from library/{img.split(':')[0]}")
        io.print(f"Status: Downloaded newer image for {img}")
        world.images.add(img)

    elif sub == "images":
        io.print(f"{'REPOSITORY':<28}{'TAG':<12}SIZE")
        for img in sorted(world.images):
            repo, tag = img.rsplit(":", 1)
            io.print(f"{repo:<28}{tag:<12}{random.randint(60, 400)}MB")

    elif sub == "run":
        parsed, bad, pos = _parse_run_flags(rest)
        if bad:
            io.print(f"unknown flag: {bad}"); return
        if not pos:
            io.print("docker run: needs an image"); return
        img = world.norm_image(pos[0])
        if img not in world.images:
            io.print(f"Unable to find image '{img}' locally")
            io.print(f"Status: Downloaded newer image for {img}")
            world.images.add(img)
        name = parsed["name"] or _rand_name()
        if name in world.containers:
            io.print(f'docker: Error response from daemon: Conflict. The container name "/{name}" is already in use.')
            return
        if parsed["network"] != "bridge" and parsed["network"] not in world.networks:
            io.print(f'docker: Error response from daemon: network {parsed["network"]} not found.')
            return
        world.containers[name] = {
            "id": _rand_id(), "image": img, "status": "running", "exit_code": 0,
            "logs": "", "network": parsed["network"], "ports": parsed["ports"], "files": {},
        }
        io.print(world.containers[name]["id"] + _rand_id())  # long id like real docker
        world.flags["last_run"] = name

    elif sub == "ps":
        show_all = "-a" in rest or "--all" in rest
        if show_all:
            world.flags["ps_a"] = True
            _ps_table(io, world.containers)
        else:
            world.flags["ps"] = True
            _ps_table(io, world.running())

    elif sub == "logs":
        if not rest:
            io.print("docker logs: needs a container"); return
        name = rest[-1]
        if name not in world.containers:
            io.print(f"Error: No such container: {name}"); return
        world.flags["logs_" + name] = True
        io.print(world.containers[name]["logs"] or "(no output)")

    elif sub == "exec":
        rest = [a for a in rest if a not in ("-it", "-ti", "-i", "-t")]
        if len(rest) < 2:
            io.print('Usage: docker exec -it <container> <command>'); return
        name, cmd = rest[0], rest[1:]
        if name not in world.containers:
            io.print(f"Error: No such container: {name}"); return
        if world.containers[name]["status"] != "running":
            io.print(f"Error: Container {name} is not running"); return
        if cmd[0] in ("bash", "sh"):
            world.inside = name
            world.flags["exec_" + name] = True
            io.print(c(f"🐚 you are now INSIDE '{name}' — plain shell commands work here; type `exit` to leave", "dim"))
        else:
            run_inside(world, name, cmd, io)

    elif sub in ("stop", "start", "rm"):
        force = "-f" in rest
        rest = [a for a in rest if a != "-f"]
        if not rest:
            io.print(f"docker {sub}: needs a container"); return
        name = rest[0]
        if name not in world.containers:
            io.print(f"Error: No such container: {name}"); return
        ctr = world.containers[name]
        if sub == "stop":
            ctr["status"] = "exited"; ctr["exit_code"] = 0; io.print(name)
        elif sub == "start":
            ctr["status"] = "running"; io.print(name)
        else:  # rm
            if ctr["status"] == "running" and not force:
                io.print(f"Error: cannot remove a running container — stop it first (or use -f)")
                return
            del world.containers[name]; io.print(name)

    elif sub == "network":
        if rest[:1] == ["create"] and len(rest) > 1:
            world.networks.add(rest[1]); io.print(_rand_id())
        elif rest[:1] == ["ls"]:
            io.print("NETWORK NAME")
            for n in sorted(world.networks):
                io.print(n)
        else:
            io.print("Usage: docker network create <name> | docker network ls")

    elif sub == "build":
        name = None
        if "-t" in rest:
            i = rest.index("-t")
            if i + 1 < len(rest):
                name = rest[i + 1]
        if "Dockerfile" not in world.files:
            io.print("ERROR: failed to solve: failed to read dockerfile: open Dockerfile: no such file or directory")
            io.print(c("(create one first — try: edit Dockerfile)", "dim"))
            return
        df = world.files["Dockerfile"]
        if "FROM" not in df.upper():
            io.print("ERROR: Dockerfile parse error: no build stage — a Dockerfile must start with FROM <base-image>")
            return
        if name is None:
            io.print(c("built an UNNAMED image — rebuild with -t <name> so you can refer to it", "yellow"))
            return
        img = world.norm_image(name)
        for i, line in enumerate([l for l in df.split("\n") if l.strip() and not l.strip().startswith("#")], 1):
            io.print(f" => [stage-0 {i}/?] {line.strip()[:60]}")
        io.print(f" => naming to docker.io/{img if '/' in img else 'library/' + img}")
        world.images.add(img)
        world.flags["built"] = img

    elif sub == "tag":
        if len(rest) != 2:
            io.print("Usage: docker tag SOURCE_IMAGE TARGET_IMAGE"); return
        src, dst = world.norm_image(rest[0]), world.norm_image(rest[1])
        if src not in world.images:
            io.print(f"Error response from daemon: No such image: {src}"); return
        world.images.add(dst)
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

    else:
        io.print(f"docker: '{sub}' is not simulated (yet). Try `task` to see what the mission needs.")


# ---------------------------------------------------- inside-container shell --
def run_inside(world, name, cmd, io):
    files = world.containers[name]["files"]
    prog, args = cmd[0], cmd[1:]
    if prog == "ls":
        io.print("  ".join(sorted(files)) if files else "")
    elif prog == "touch" and args:
        files[args[0]] = files.get(args[0], "")
    elif prog == "mkdir" and args:
        files[args[0].rstrip("/") + "/"] = ""
    elif prog == "cp" and len(args) == 2:
        src, dst = args
        if src not in files:
            io.print(f"cp: cannot stat '{src}': No such file"); return
        files[dst.rstrip("/") + "/" + src if dst.rstrip("/") + "/" in files else dst] = files[src]
    elif prog == "mv" and len(args) == 2:
        src, dst = args
        if src not in files:
            io.print(f"mv: cannot stat '{src}': No such file"); return
        key = dst.rstrip("/") + "/" + src if dst.rstrip("/") + "/" in files else dst
        files[key] = files.pop(src)
    elif prog == "cat" and args:
        io.print(files.get(args[0], f"cat: {args[0]}: No such file"))
    elif prog == "pwd":
        io.print("/root")
    elif prog == "echo":
        io.print(" ".join(args))
    elif prog == "ping" and args:
        target = args[0]
        me = world.containers[name]
        if target in world.containers and me["network"] != "bridge" \
                and world.containers[target]["network"] == me["network"] \
                and world.containers[target]["status"] == "running":
            for i in range(3):
                io.print(f"64 bytes from {target}: icmp_seq={i + 1} ttl=64 time=0.0{random.randint(4, 9)} ms")
            io.print(f"--- {target} ping statistics ---\n3 packets transmitted, 3 received, 0% packet loss")
            world.flags["ping_ok"] = (name, target)
        else:
            io.print(f"ping: {target}: Name or service not known")
            io.print(c("(hint: name-resolution only works on a USER-DEFINED network, and the target must be running)", "dim"))
    else:
        io.print(f"{prog}: not available in this tiny container shell")


# --------------------------------------------------------------------- git --
MARKERS = ("<<<<<<<", "=======", ">>>>>>>")


def _has_markers(text):
    return any(m in text for m in MARKERS)


def do_git(world, args, io):
    g = world.git
    if g is None:
        io.print("This mission has no git world — try `task`.")
        return
    if not args:
        io.print("usage: git <command>  (status, add, commit, log, branch, checkout, switch, merge, push, diff)")
        return
    sub, rest = args[0], args[1:]

    if sub == "status":
        world.flags["git_status"] = True
        io.print(f"On branch {g['branch']}")
        if g["conflict"]:
            io.print("You have unmerged paths.\n  (fix conflicts and run \"git add <file>\", then \"git commit\")\n")
            io.print(c(f"\tboth modified:   {g['conflict']}", "red"))
            return
        if g["staged"]:
            io.print("Changes to be committed:")
            for f in sorted(g["staged"]):
                io.print(c(f"\tnew/modified:   {f}", "green"))
        if g["modified"]:
            io.print("Changes not staged for commit:")
            for f in sorted(g["modified"]):
                io.print(c(f"\tmodified:   {f}", "red"))
        if g["untracked"]:
            io.print("Untracked files:")
            for f in sorted(g["untracked"]):
                io.print(c(f"\t{f}", "red"))
        if not (g["staged"] or g["modified"] or g["untracked"]):
            io.print("nothing to commit, working tree clean")

    elif sub == "add":
        if not rest:
            io.print("Nothing specified, nothing added."); return
        targets = (g["untracked"] | g["modified"]) if rest[0] == "." else set(rest)
        for f in targets:
            if f not in world.files:
                io.print(f"fatal: pathspec '{f}' did not match any files"); return
            if _has_markers(world.files.get(f, "")):
                io.print(c(f"⚠️  '{f}' still contains conflict markers (<<<<<<< / ======= / >>>>>>>).", "yellow"))
                io.print(c("   Edit the file to the final content first (try: edit " + f + ")", "yellow"))
                return
            g["staged"].add(f)
            g["untracked"].discard(f)
            g["modified"].discard(f)

    elif sub == "commit":
        msg = None
        if "-m" in rest:
            i = rest.index("-m")
            if i + 1 < len(rest):
                msg = rest[i + 1]
        if msg is None:
            io.print("Aborting commit: please provide a message with -m \"msg\""); return
        if not g["staged"] and not g["conflict"]:
            io.print("nothing to commit, working tree clean"); return
        if g["conflict"] and g["conflict"] not in g["staged"]:
            io.print("fatal: cannot commit — resolve the conflict and `git add` the file first"); return
        g["commits"].append({"branch": g["branch"], "msg": msg})
        g["tracked"] |= g["staged"]
        g["staged"] = set()
        if g["conflict"]:
            g["merged"].add(world.flags.get("merging", "?"))
            g["conflict"] = None
        io.print(f"[{g['branch']} {_rand_id()[:7]}] {msg}")

    elif sub == "log":
        commits = [cm for cm in g["commits"]]
        if not commits:
            io.print("fatal: your current branch has no commits yet"); return
        world.flags["git_log"] = True
        oneline = "--oneline" in rest
        for cm in reversed(commits):
            if oneline:
                io.print(f"{_rand_id()[:7]} {cm['msg']}")
            else:
                io.print(c(f"commit {_rand_id()}{_rand_id()[:28]}", "yellow"))
                io.print(f"    {cm['msg']}\n")

    elif sub == "branch":
        if not rest:
            for b in sorted(g["branches"]):
                io.print(("* " if b == g["branch"] else "  ") + b)
        else:
            g["branches"].add(rest[0])
            io.print(c(f"(created branch '{rest[0]}' — switch to it with checkout/switch)", "dim"))

    elif sub in ("checkout", "switch"):
        create = False
        if rest and rest[0] in ("-b", "-c"):
            create = True; rest = rest[1:]
        if not rest:
            io.print(f"usage: git {sub} <branch>"); return
        target = rest[0]
        if create:
            g["branches"].add(target)
        if target not in g["branches"]:
            io.print(f"error: pathspec '{target}' did not match any branch"); return
        g["branch"] = target
        for fname, content in g["branch_files"].get(target, {}).items():
            world.files[fname] = content
        io.print(f"Switched to branch '{target}'")

    elif sub == "merge":
        if not rest:
            io.print("usage: git merge <branch>"); return
        other = rest[0]
        if other not in g["branches"]:
            io.print(f"merge: {other} - not something we can merge"); return
        mine = g["branch_files"].get(g["branch"], {})
        theirs = g["branch_files"].get(other, {})
        clash = [f for f in mine if f in theirs and mine[f] != theirs[f]]
        world.flags["merging"] = other
        if clash:
            f = clash[0]
            world.files[f] = (f"<<<<<<< HEAD\n{mine[f]}\n=======\n{theirs[f]}\n>>>>>>> {other}")
            g["conflict"] = f
            world.flags["conflict_seen"] = True
            io.print(f"Auto-merging {f}")
            io.print(c(f"CONFLICT (content): Merge conflict in {f}", "red"))
            io.print("Automatic merge failed; fix conflicts and then commit the result.")
        else:
            g["merged"].add(other)
            g["commits"].append({"branch": g["branch"], "msg": f"Merge branch '{other}'"})
            io.print(f"Merge made by the 'ort' strategy.")

    elif sub == "push":
        setup = "-u" in rest or "--set-upstream" in rest
        named = [a for a in rest if a not in ("-u", "--set-upstream", "origin")]
        branch = named[0] if named else g["branch"]
        first_time = branch not in g["pushed"]
        if first_time and not setup:
            io.print("fatal: The current branch has no upstream branch.")
            io.print(f"    (use: git push -u origin {branch})")
            return
        g["pushed"].add(branch)
        io.print(f"To github.com:you/{world.flags.get('repo_name', 'repo')}.git")
        io.print(f" * [new branch]      {branch} -> {branch}" if first_time else f"   {branch} -> {branch}")

    elif sub == "diff":
        if not (g["modified"] or g["staged"]):
            io.print("(no changes)"); return
        for f in sorted(g["modified"] | g["staged"]):
            io.print(c(f"--- a/{f}", "red")); io.print(c(f"+++ b/{f}", "green"))
            for line in world.files.get(f, "").split("\n"):
                io.print(c("+ " + line, "green"))

    else:
        io.print(f"git: '{sub}' is not simulated (yet). Try `task`.")


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


def do_host(world, prog, args, io):
    files = world.files
    if prog == "ls":
        io.print("  ".join(sorted(files)) if files else "")
    elif prog == "cat":
        if not args:
            io.print("cat: needs a file"); return
        io.print(files.get(args[0], f"cat: {args[0]}: No such file or directory"))
    elif prog == "touch" and args:
        if args[0] not in files:
            files[args[0]] = ""
            _mark_edited(world, args[0])
    elif prog == "rm" and args:
        files.pop(args[0], None)
    elif prog == "echo":
        # support: echo "text" > file
        if ">" in args:
            i = args.index(">")
            text, fname = " ".join(args[:i]), args[i + 1]
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
        lines = []
        while True:
            line = io.input("… ")
            if line.strip() == ".":
                break
            lines.append(line)
        files[fname] = "\n".join(lines)
        _mark_edited(world, fname)
        io.print(c(f"saved {fname}", "dim"))
    else:
        io.print(f"{prog}: command not found (this simulated shell knows: ls, cat, touch, rm, echo, edit)")


# ----------------------------------------------------------------- mission --
def dispatch(world, line, io, mission):
    """Route one command line. Returns False if the player wants to leave."""
    try:
        args = shlex.split(line)
    except ValueError as e:
        io.print(f"parse error: {e}")
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
            io.print(c(f"left container '{world.inside}' — you're back on the host", "dim"))
            world.inside = None
        else:
            run_inside(world, world.inside, args, io)
        return True

    if prog == "docker":
        do_docker(world, rest, io)
    elif prog == "git":
        do_git(world, rest, io)
    elif prog in ("ls", "cat", "touch", "rm", "echo", "edit"):
        do_host(world, prog, rest, io)
    elif prog == "ping":
        io.print("ping: works from INSIDE a container here — docker exec -it <name> bash, then ping <other>")
    elif prog in ("quit", "exit"):
        return False
    else:
        io.print(f"{prog}: command not found — `task` shows the goal, `hint` costs a little XP, `quit` leaves")
    return True


def run_mission(mission, profile, io=None):
    """Run one mission. Returns (completed: bool, xp_earned: int, hints: int)."""
    io = io or IO()
    world = World(mission.get("world"))
    world.flags["repo_name"] = mission.get("repo_name", "repo")
    objectives = [dict(o, done=False) for o in mission["objectives"]]
    xp_earned, hints_used = 0, 0

    io.print("")
    io.print(c("═" * 62, "blue"))
    io.print(c(f"  🗡️  MISSION: {mission['title']}", "bold"))
    io.print(c("═" * 62, "blue"))
    io.print(mission["brief"])
    io.print(c(f"\n📖 pairs with vault note: {mission.get('vault_note', '—')}", "dim"))
    io.print(c("meta-commands: task · hint · learn · quit\n", "dim"))

    def show_task():
        io.print(c("\n🎯 Objectives:", "bold"))
        for o in objectives:
            mark = c("✔", "green") if o["done"] else c("·", "dim")
            io.print(f"  {mark} {o['desc']}  {c('(+' + str(o['xp']) + ' XP)', 'dim')}")
        io.print("")

    show_task()

    while True:
        prompt = c(f"({world.inside}) $ " if world.inside else "$ ", "cyan")
        try:
            line = io.input(prompt)
        except (EOFError, KeyboardInterrupt):
            io.print(c("\nleaving mission — progress in this mission isn't saved mid-way", "yellow"))
            return False, xp_earned, hints_used

        stripped = line.strip().lower()
        if stripped == "task":
            show_task(); continue
        if stripped == "learn":
            io.print(c(f"📖 Open your vault note: {mission.get('vault_note', '—')}", "cyan")); continue
        if stripped == "hint":
            pending = next((o for o in objectives if not o["done"]), None)
            if pending:
                hints_used += 1
                xp_earned = max(0, xp_earned - 5)
                io.print(c(f"💡 {pending['hint']}  (–5 XP)", "yellow"))
            continue

        if not dispatch(world, line, io, mission):
            io.print(c("left the mission — run it again anytime", "yellow"))
            return False, xp_earned, hints_used

        # objective checks after every command
        for o in objectives:
            if not o["done"] and o["check"](world):
                o["done"] = True
                xp_earned += o["xp"]
                io.print(c(f"  ✔ OBJECTIVE COMPLETE: {o['desc']}  (+{o['xp']} XP)", "green"))

        if all(o["done"] for o in objectives):
            bonus = 10 if hints_used == 0 else 0
            xp_earned += bonus
            io.print("")
            io.print(c("🏆 MISSION COMPLETE!", "green") + (c(f"  +{bonus} XP no-hint bonus!", "magenta") if bonus else ""))
            io.print(c(f"   earned {xp_earned} XP · hints used: {hints_used}", "bold"))
            return True, xp_earned, hints_used


# ----------------------------------------------------------------- profile --
PROFILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "progress.json")


def load_profile():
    if os.path.exists(PROFILE_PATH):
        with open(PROFILE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"name": None, "xp": 0, "completed": {}}


def save_profile(profile):
    with open(PROFILE_PATH, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2)


def level(xp):
    names = ["Rookie", "Tinkerer", "Operator", "Engineer", "Senior", "DevOps Legend"]
    lvl = min(xp // 100, len(names) - 1)
    return lvl + 1, names[lvl]
