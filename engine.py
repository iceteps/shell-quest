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
        k = spec.get("k8s")
        self.k8s = None
        if k is not None:
            self.k8s = {
                "started": k.get("started", False),
                "namespaces": set(k.get("namespaces", [])) | {"default", "kube-system"},
                # deployments: name -> {ns, replicas, image, revision}
                "deployments": {n: dict(d) for n, d in k.get("deployments", {}).items()},
                "pods": {},          # name -> {ns, deploy, status, image, restarts}
                "services": {n: dict(s) for n, s in k.get("services", {}).items()},
                # objects: plain kinds -> {(name, ns), ...}
                "objects": {kind: set(map(tuple, v)) for kind, v in k.get("objects", {}).items()},
                "rbac": {"sa": set(), "roles": {}, "bindings": {}},  # bindings: name -> (role, sa, ns)
            }
            for d in self.k8s["deployments"].values():
                d.setdefault("revision", 1)
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

    if sub == "compose":
        _do_compose(world, rest, io)
        return

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


# -------------------------------------------------------------- kubernetes --
K8S_KINDMAP = {
    "Namespace": "namespace", "Pod": "pod", "Deployment": "deployment.apps",
    "Service": "service", "ConfigMap": "configmap", "Secret": "secret",
    "ServiceAccount": "serviceaccount", "Role": "role.rbac.authorization.k8s.io",
    "RoleBinding": "rolebinding.rbac.authorization.k8s.io",
    "Ingress": "ingress.networking.k8s.io", "PersistentVolumeClaim": "persistentvolumeclaim",
}
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
    "all": "all",
}


def _reconcile(world):
    """The control loop: make pod reality match each deployment's desired state."""
    k = world.k8s
    if not k:
        return
    for dname, d in k["deployments"].items():
        owned = [p for p, pd in k["pods"].items() if pd.get("deploy") == dname]
        while len(owned) < d["replicas"]:
            pname = f"{dname}-{_rand_id()[:9]}-{_rand_id()[:5]}"
            k["pods"][pname] = {"ns": d.get("ns", "default"), "deploy": dname,
                                "status": "Running", "image": d["image"], "restarts": 0}
            owned.append(pname)
        while len(owned) > d["replicas"]:
            k["pods"].pop(owned.pop(), None)
    # garbage-collect pods whose deployment is gone
    for p in [p for p, pd in k["pods"].items()
              if pd.get("deploy") and pd["deploy"] not in k["deployments"]]:
        del k["pods"][p]


def _parse_manifests(text):
    """Tiny YAML-ish reader — just enough for the course's manifests."""
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
        m = re.search(r"(?m)^\s*app:\s*([\w-]+)", chunk)
        if m:
            doc["app"] = m.group(1)
        docs.append(doc)
    # namespaces first so `apply -f .` doesn't trip over ordering
    docs.sort(key=lambda d: 0 if d["kind"] == "Namespace" else 1)
    return docs


def _k8s_apply_doc(world, doc, io, deleting=False):
    k = world.k8s
    kind, name, ns = doc["kind"], doc["name"], doc["ns"]
    label = f'{K8S_KINDMAP.get(kind, kind.lower())}/{name}'
    if deleting:
        label = f'{K8S_KINDMAP.get(kind, kind.lower())} "{name}"'
    if kind == "Namespace":
        if deleting:
            k["namespaces"].discard(name)
            for coll in ("deployments", "services"):
                for n in [n for n, o in k[coll].items() if o.get("ns") == name]:
                    del k[coll][n]
            for n in [n for n, o in k["pods"].items() if o.get("ns") == name]:
                del k["pods"][n]
        else:
            existed = name in k["namespaces"]
            k["namespaces"].add(name)
            io.print(f"{label} {'unchanged' if existed else 'created'}")
            return
    elif kind == "Pod":
        if deleting:
            k["pods"].pop(name, None)
        else:
            if ns not in k["namespaces"]:
                io.print(f'Error from server (NotFound): namespaces "{ns}" not found')
                io.print(c("(create the namespace first — it has its own YAML)", "dim"))
                return
            existed = name in k["pods"]
            k["pods"][name] = {"ns": ns, "deploy": None, "status": "Running",
                               "image": doc.get("image", "nginx"), "restarts": 0}
            io.print(f"{label} {'configured' if existed else 'created'}")
            return
    elif kind == "Deployment":
        if deleting:
            k["deployments"].pop(name, None)
            _reconcile(world)
        else:
            if ns not in k["namespaces"]:
                io.print(f'Error from server (NotFound): namespaces "{ns}" not found')
                io.print(c("(create the namespace first — it has its own YAML)", "dim"))
                return
            existed = name in k["deployments"]
            prev = k["deployments"].get(name, {})
            k["deployments"][name] = {
                "ns": ns, "replicas": doc.get("replicas", 1),
                "image": doc.get("image", "nginx"),
                "revision": prev.get("revision", 1),
                "history": prev.get("history", [doc.get("image", "nginx")]),
            }
            _reconcile(world)
            io.print(f"{label} {'configured' if existed else 'created'}")
            return
    elif kind == "Service":
        if deleting:
            k["services"].pop(name, None)
        else:
            existed = name in k["services"]
            svc = {"ns": ns, "type": doc.get("type", "ClusterIP"),
                   "port": doc.get("port", 80), "app": doc.get("app", name)}
            if svc["type"] == "NodePort":
                svc["nodePort"] = doc.get("nodePort") or random.randint(30000, 32767)
            k["services"][name] = svc
            io.print(f"{label} {'configured' if existed else 'created'}")
            return
    elif kind == "ServiceAccount":
        (k["rbac"]["sa"].discard if deleting else k["rbac"]["sa"].add)((name, ns))
    elif kind == "Role":
        if deleting:
            k["rbac"]["roles"].pop(name, None)
        else:
            k["rbac"]["roles"][name] = ns
    elif kind == "RoleBinding":
        if deleting:
            k["rbac"]["bindings"].pop(name, None)
        else:
            names = doc.get("names", [])
            sa = names[1] if len(names) > 1 else "?"
            role = names[2] if len(names) > 2 else "?"
            k["rbac"]["bindings"][name] = (role, sa, ns)
    else:  # ConfigMap, Secret, Ingress, PVC, …
        coll = k["objects"].setdefault(kind, set())
        (coll.discard if deleting else coll.add)((name, ns))
    io.print(f"{label} {'deleted' if deleting else 'created'}")


def _pods_in(world, ns, all_ns=False):
    return {p: d for p, d in world.k8s["pods"].items() if all_ns or d["ns"] == ns}


def _find_pod(world, name, ns):
    """Exact pod name, or unique prefix (so scripts can say `logs frontend`)."""
    pods = world.k8s["pods"]
    if name in pods:
        return name
    matches = [p for p, d in pods.items() if p.startswith(name) and d["ns"] == ns]
    return matches[0] if len(matches) == 1 else (matches[0] if matches else None)


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


def do_kubectl(world, args, io):
    k = world.k8s
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
        if not rest:
            io.print("error: you must specify the type of resource to get"); return
        kinds = [K8S_ALIASES.get(x.strip(), x.strip()) for x in rest[0].split(",")]
        world.flags["get_" + "_".join(kinds) + ("_A" if all_ns else "")] = True
        for kind in kinds:
            if kind == "nodes":
                io.print(f"{'NAME':<12}{'STATUS':<9}{'ROLES':<16}{'AGE':<6}VERSION")
                io.print(f"{'minikube':<12}{'Ready':<9}{'control-plane':<16}{'5m':<6}v1.30.0")
                world.flags["get_nodes"] = True
            elif kind == "namespaces":
                io.print(f"{'NAME':<22}{'STATUS':<9}AGE")
                for n in sorted(k["namespaces"]):
                    io.print(f"{n:<22}{'Active':<9}5m")
                world.flags["get_namespaces"] = True
            elif kind == "pods":
                if rest[0] == "pods" and rest[1:2] and not rest[1].startswith("-"):
                    pass  # `get pods <name>` — fall through to table of all (keep simple)
                pods = _pods_in(world, "kube-system" if ns == "kube-system" else ns, all_ns)
                if ns == "kube-system" and not all_ns:
                    io.print(f"{'NAME':<38}{'READY':<8}{'STATUS':<10}{'RESTARTS':<10}AGE")
                    for p in ("coredns-7db6d8ff4d-x2m9k", "etcd-minikube", "kube-apiserver-minikube",
                              "kube-controller-manager-minikube", "kube-proxy-bqpfz",
                              "kube-scheduler-minikube", "storage-provisioner"):
                        io.print(f"{p:<38}{'1/1':<8}{'Running':<10}{'0':<10}5m")
                    world.flags["get_pods_system"] = True
                    continue
                if not pods:
                    io.print(f"No resources found in {ns} namespace.")
                    others = {d["ns"] for d in k["pods"].values()}
                    if others:
                        io.print(c(f"(pods DO exist — in namespace{'s' if len(others) > 1 else ''} "
                                   f"{', '.join(sorted(others))}. Add -n <namespace>)", "dim"))
                    continue
                head = f"{'NAMESPACE':<14}" if all_ns else ""
                io.print(head + f"{'NAME':<34}{'READY':<8}{'STATUS':<10}{'RESTARTS':<10}AGE")
                for p, d in sorted(pods.items()):
                    lead = f"{d['ns']:<14}" if all_ns else ""
                    io.print(lead + f"{p:<34}{'1/1':<8}{d['status']:<10}{str(d['restarts']):<10}42s")
                world.flags["get_pods"] = True
            elif kind == "deployments":
                deps = {n: d for n, d in k["deployments"].items() if all_ns or d["ns"] == ns}
                if not deps:
                    io.print(f"No resources found in {ns} namespace.")
                    continue
                io.print(f"{'NAME':<22}{'READY':<8}{'UP-TO-DATE':<12}{'AVAILABLE':<11}AGE")
                for n, d in sorted(deps.items()):
                    r = d["replicas"]
                    io.print(f"{n:<22}{f'{r}/{r}':<8}{r:<12}{r:<11}42s")
                world.flags["get_deployments"] = True
            elif kind == "rs":
                deps = {n: d for n, d in k["deployments"].items() if all_ns or d["ns"] == ns}
                io.print(f"{'NAME':<32}{'DESIRED':<9}{'CURRENT':<9}{'READY':<7}AGE")
                for n, d in sorted(deps.items()):
                    io.print(f"{n + '-' + _rand_id()[:9]:<32}{d['replicas']:<9}{d['replicas']:<9}{d['replicas']:<7}42s")
                world.flags["get_rs"] = True
            elif kind == "services":
                io.print(f"{'NAME':<18}{'TYPE':<14}{'CLUSTER-IP':<16}{'EXTERNAL-IP':<13}{'PORT(S)':<16}AGE")
                if ns == "default" or all_ns:
                    io.print(f"{'kubernetes':<18}{'ClusterIP':<14}{'10.96.0.1':<16}{'<none>':<13}{'443/TCP':<16}5m")
                for n, s in sorted(k["services"].items()):
                    if not (all_ns or s["ns"] == ns):
                        continue
                    ports = f"{s['port']}:{s['nodePort']}/TCP" if s.get("nodePort") else f"{s['port']}/TCP"
                    ext = "<pending>" if s["type"] == "LoadBalancer" else "<none>"
                    io.print(f"{n:<18}{s['type']:<14}{'10.' + str(random.randint(96, 111)) + '.' + str(random.randint(0, 255)) + '.' + str(random.randint(2, 254)):<16}{ext:<13}{ports:<16}42s")
                world.flags["get_services"] = True
            elif kind == "all":
                do_kubectl(world, ["get", "pods"] + (["-n", ns] if ns != "default" else []), io)
                do_kubectl(world, ["get", "deployments"] + (["-n", ns] if ns != "default" else []), io)
                do_kubectl(world, ["get", "services"] + (["-n", ns] if ns != "default" else []), io)
                world.flags["get_all"] = True
            elif kind in ("configmap", "secret", "serviceaccount", "ingress", "pvc", "role", "rolebinding"):
                pretty = {"configmap": "ConfigMap", "secret": "Secret", "serviceaccount": "ServiceAccount",
                          "ingress": "Ingress", "pvc": "PersistentVolumeClaim"}.get(kind)
                if kind == "serviceaccount":
                    rows = [n for (n, o_ns) in k["rbac"]["sa"] if all_ns or o_ns == ns]
                elif kind == "role":
                    rows = [n for n, o_ns in k["rbac"]["roles"].items() if all_ns or o_ns == ns]
                elif kind == "rolebinding":
                    rows = [n for n, (_, _, o_ns) in k["rbac"]["bindings"].items() if all_ns or o_ns == ns]
                else:
                    rows = [n for (n, o_ns) in k["objects"].get(pretty, set()) if all_ns or o_ns == ns]
                if not rows:
                    io.print(f"No resources found in {ns} namespace.")
                    continue
                io.print(f"{'NAME':<26}AGE")
                for n in sorted(rows):
                    io.print(f"{n:<26}42s")
                world.flags[f"get_{kind}"] = True
            else:
                io.print(f'error: the server doesn\'t have a resource type "{kind}"')

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
        kind = K8S_ALIASES.get(rest[0], rest[0])
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
        elif kind == "namespaces" and name:
            _k8s_apply_doc(world, {"kind": "Namespace", "name": name, "ns": name, "names": [name]}, io, deleting=True)
        elif kind == "deployments" and name:
            _k8s_apply_doc(world, {"kind": "Deployment", "name": name, "ns": ns, "names": [name]}, io, deleting=True)
        elif kind == "rolebinding" and name:
            _k8s_apply_doc(world, {"kind": "RoleBinding", "name": name, "ns": ns, "names": [name]}, io, deleting=True)
            world.flags["binding_deleted"] = True
        else:
            io.print(f'error: unable to delete "{rest[0]}" — try: pod/namespace/deployment/rolebinding <name> or -f <file>')

    elif sub == "describe":
        if len(rest) < 2:
            io.print("error: you must specify a resource and a name"); return
        kind = K8S_ALIASES.get(rest[0].split("/")[0], rest[0])
        name = rest[1] if len(rest) > 1 else rest[0].split("/")[-1]
        if kind == "deployments":
            if name not in k["deployments"]:
                io.print(f'Error from server (NotFound): deployments.apps "{name}" not found'); return
            d = k["deployments"][name]
            io.print(f"Name:                   {name}\nNamespace:              {d['ns']}\n"
                     f"Selector:               app={name}\nReplicas:               {d['replicas']} desired | "
                     f"{d['replicas']} updated | {d['replicas']} total | {d['replicas']} available | 0 unavailable\n"
                     f"StrategyType:           RollingUpdate\nPod Template:\n  Containers:\n   app:\n"
                     f"    Image:        {d['image']}\nEvents:\n"
                     f"  Type    Reason             Age   From                   Message\n"
                     f"  ----    ------             ----  ----                   -------\n"
                     f"  Normal  ScalingReplicaSet  42s   deployment-controller  Scaled up replica set {name}-{_rand_id()[:9]} to {d['replicas']}")
            world.flags[f"describe_deploy_{name}"] = True
        elif kind == "pods":
            real = _find_pod(world, name, ns)
            if not real:
                io.print(f'Error from server (NotFound): pods "{name}" not found'); return
            p = k["pods"][real]
            io.print(f"Name:             {real}\nNamespace:        {p['ns']}\nNode:             minikube/192.168.49.2\n"
                     f"Status:           {p['status']}\nIP:               10.244.0.{random.randint(2, 254)}\n"
                     f"Controlled By:    ReplicaSet/{p['deploy'] or '<none — bare pod>'}\nContainers:\n  app:\n"
                     f"    Image:          {p['image']}\n    State:          Running\nEvents:\n"
                     f"  Type    Reason     Age   From               Message\n"
                     f"  ----    ------     ----  ----               -------\n"
                     f"  Normal  Scheduled  42s   default-scheduler  Successfully assigned {p['ns']}/{real} to minikube\n"
                     f"  Normal  Pulled     41s   kubelet            Container image \"{p['image']}\" already present on machine\n"
                     f"  Normal  Created    41s   kubelet            Created container app\n"
                     f"  Normal  Started    40s   kubelet            Started container app")
            world.flags["describe_pod"] = True
        else:
            io.print(f"describe for '{rest[0]}' isn't simulated — try deployment/pod")

    elif sub == "logs":
        if not rest:
            io.print("error: expected 'logs POD'"); return
        real = _find_pod(world, rest[0], ns)
        if not real:
            io.print(f'Error from server (NotFound): pods "{rest[0]}" not found'); return
        img = k["pods"][real]["image"]
        if "nginx" in img or "hello" in img:
            io.print(f'/docker-entrypoint.sh: Configuration complete; ready for start up\n'
                     f'10.244.0.1 - - [{random.randint(10, 28)}/Jul/2026:10:0{random.randint(0, 9)}:12 +0000] "GET / HTTP/1.1" 200 615 "-" "kube-probe/1.30"')
        else:
            io.print("(container started; no recent output)")
        world.flags["logs_pod"] = True

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
        d = k["deployments"][target]
        d.setdefault("history", [d["image"]])
        d["history"].append(pair.split("=", 1)[1])
        d["image"] = pair.split("=", 1)[1]
        d["revision"] = d.get("revision", 1) + 1
        for p in [p for p, pd in k["pods"].items() if pd.get("deploy") == target]:
            del k["pods"][p]
        _reconcile(world)
        io.print(f"deployment.apps/{target} image updated")
        world.flags[f"set_image_{target}"] = d["image"]

    elif sub == "rollout":
        action = rest[0] if rest else ""
        target = rest[1].split("/")[-1] if len(rest) > 1 else ""
        if target not in k["deployments"]:
            io.print(f'Error from server (NotFound): deployments.apps "{target}" not found'); return
        d = k["deployments"][target]
        if action == "status":
            io.print(f'deployment "{target}" successfully rolled out')
        elif action == "undo":
            if len(d.get("history", [])) > 1:
                d["history"].pop()
                d["image"] = d["history"][-1]
                d["revision"] += 1
                for p in [p for p, pd in k["pods"].items() if pd.get("deploy") == target]:
                    del k["pods"][p]
                _reconcile(world)
            io.print(f"deployment.apps/{target} rolled back")
            world.flags[f"rolled_back_{target}"] = d["image"]
        elif action == "history":
            io.print(f"deployment.apps/{target}\nREVISION  CHANGE-CAUSE")
            for i in range(1, d.get("revision", 1) + 1):
                io.print(f"{i:<10}<none>")
        else:
            io.print("rollout: try status/undo/history deployment/<name>")

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
            k["namespaces"].add(rest[1])
            io.print(f"namespace/{rest[1]} created")
        elif rest[:1] == ["deployment"] and len(rest) > 1:
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

    else:
        io.print(f"kubectl: '{sub}' is not simulated (yet). Try `task` to see what the mission needs.")


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
        io.print(f"[+] Running {len(services)}/{len(services)}")
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
        world.flags["compose_down"] = True
    else:
        io.print("docker compose: try up -d / ps / logs <svc> / down")


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
    elif prog == "kubectl":
        do_kubectl(world, rest, io)
    elif prog == "minikube":
        do_minikube(world, rest, io)
    elif prog == "docker-compose":
        _do_compose(world, rest, io)
    elif prog in ("ls", "cat", "touch", "rm", "echo", "edit"):
        do_host(world, prog, rest, io)
    elif prog == "ping":
        io.print("ping: works from INSIDE a container here — docker exec -it <name> bash, then ping <other>")
    elif prog in ("quit", "exit"):
        return False
    else:
        io.print(f"{prog}: command not found — `task` shows the goal, `hint` costs a little XP, `quit` leaves")
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


def run_mission(mission, profile, io=None):
    """Run one mission. Returns (completed: bool, xp_earned: int, hints: int)."""
    io = io or IO()
    world = World(mission.get("world"))
    world.flags["repo_name"] = mission.get("repo_name", "repo")
    objectives = [dict(o, done=False) for o in mission["objectives"]]
    xp_earned, hints_used = 0, 0
    demo_used, user_cmds = False, 0
    demo_sol = list(mission.get("solution", []))

    io.print("")
    io.print(c("═" * 62, "blue"))
    io.print(c(f"  🗡️  MISSION: {mission['title']}", "bold"))
    io.print(c("═" * 62, "blue"))
    io.print(mission["brief"])
    io.print(c(f"\n📖 pairs with vault note: {mission.get('vault_note', '—')}", "dim"))
    io.print(c("meta-commands: task · hint · demo (watch it solved!) · learn · quit\n", "dim"))

    def show_task():
        io.print(c("\n🎯 Objectives:", "bold"))
        for o in objectives:
            mark = c("✔", "green") if o["done"] else c("·", "dim")
            io.print(f"  {mark} {o['desc']}  {c('(+' + str(o['xp']) + ' XP)', 'dim')}")
        io.print("")

    show_task()

    def check_objs(demo=False):
        nonlocal xp_earned
        for o in objectives:
            if not o["done"] and o["check"](world):
                o["done"] = True
                if demo:
                    io.print(c(f"  ✔ (demo) objective complete: {o['desc']}  — no XP for watching 😉", "green"))
                else:
                    xp_earned += o["xp"]
                    io.print(c(f"  ✔ OBJECTIVE COMPLETE: {o['desc']}  (+{o['xp']} XP)", "green"))

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
        if stripped == "demo":
            if user_cmds:
                io.print(c("🎬 demo replays the solution from a FRESH world — but you've already made moves.", "yellow"))
                io.print(c("   quit and re-enter the mission to watch from the start (or keep going with `hint`)", "dim"))
                continue
            if not demo_sol:
                io.print(c("this mission has no demo script", "yellow")); continue
            demo_used = True
            io.print(c("\n🎬 DEMO MODE — watch a correct solution play out, step by step.", "magenta"))
            io.print(c("   ⏎ Enter = next step · `takeover` = grab the keyboard · `stop` = leave demo\n", "dim"))
            while demo_sol:
                cmd = demo_sol.pop(0)
                io.print(c("$ ", "cyan") + c(cmd, "bold") + c("   ⟵ demo", "dim"))
                try:
                    dispatch(world, cmd, _DemoFeed(io, demo_sol), mission)
                except EOFError:
                    break
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

        user_cmds += 1
        if not dispatch(world, line, io, mission):
            io.print(c("left the mission — run it again anytime", "yellow"))
            return False, xp_earned, hints_used

        check_objs()

        if all(o["done"] for o in objectives):
            bonus = 10 if hints_used == 0 and not demo_used else 0
            xp_earned += bonus
            io.print("")
            io.print(c("🏆 MISSION COMPLETE!", "green") + (c(f"  +{bonus} XP no-hint bonus!", "magenta") if bonus else ""))
            if demo_used:
                io.print(c("   (finished after a demo assist — demoed objectives paid no XP)", "dim"))
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
