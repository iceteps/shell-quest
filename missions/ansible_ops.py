"""Ansible missions — ad-hoc commands, playbooks, IDEMPOTENCY, handlers, tags,
register/when, per-host Jinja2 templates and roles.

The engine knows nothing about Ansible, so this module carries its own small
Ansible: a tiny YAML reader, a two-node fake fleet (node1/node2) and a module
runner. Every file the player writes is really parsed and really executed — a
role they invent, a `when:` they word differently, a playbook they restructure
all run the same way the shipped ones do. That is the only way "check world
state, never keystrokes" can be true for a topic whose whole subject is YAML.

Deliberate limits, said out loud instead of faked:
  * modules simulated: apt · copy · template · service (systemd is an alias) ·
    user · file · lineinfile · command · shell · debug · ping. Anything else
    says so instead of pretending to have worked.
  * YAML subset: block maps, block sequences, inline [a, b] lists, `|`/`>`
    blocks. No anchors, no flow maps, no multi-document files.
  * no network: ansible-galaxy / ansible-vault explain themselves rather than
    pretend to reach the internet.
"""
import copy
import re

from engine import c

NODES = ("node1", "node2")

# Facts Ansible would gather at play start. They exist so a template can render
# DIFFERENTLY per host — the single most convincing thing Ansible does.
FACTS = {
    "node1": {"ansible_hostname": "node1", "inventory_hostname": "node1",
              "ansible_default_ipv4": {"address": "172.20.0.11"},
              "ansible_distribution": "Ubuntu",
              "ansible_facts": {"os_family": "Debian", "distribution": "Ubuntu",
                                "hostname": "node1"}},
    "node2": {"ansible_hostname": "node2", "inventory_hostname": "node2",
              "ansible_default_ipv4": {"address": "172.20.0.12"},
              "ansible_distribution": "Ubuntu",
              "ansible_facts": {"os_family": "Debian", "distribution": "Ubuntu",
                                "hostname": "node2"}},
}

MODULES = ("apt", "copy", "template", "service", "systemd", "user", "file",
           "lineinfile", "command", "shell", "debug", "ping")

# ------------------------------------------------------------ mission files --

HOSTS_INI = '''[web]
node1
node2
'''

PLAYBOOK = '''---
- name: configure web servers
  hosts: web
  become: true
  tasks:
    - name: install nginx
      apt:
        name: nginx
        state: present

    - name: copy website
      copy:
        src: index.html
        dest: /var/www/html/index.html
      notify: restart nginx

    - name: nginx is running
      service:
        name: nginx
        state: started

  handlers:
    - name: restart nginx
      service:
        name: nginx
        state: restarted
'''

INDEX_HTML = "<h1>Hello from Ansible!</h1>"

# --- mission 2 -------------------------------------------------------------
# The inventory group is DELIBERATELY wrong: every shipped playbook says
# `hosts: servers`, the inventory says [managed_nodes]. That mismatch is the
# class gotcha, and it is the first thing the player has to read an error about.
INV_INI = '''[managed_nodes]
node1
node2

[managed_nodes:vars]
ansible_user=root
'''

VARS_YML = '''---
- name: vars, tags and conditionals
  hosts: servers
  become: true
  vars:
    package: vim
    listVar:
      - Item1
      - Item2
      - Item3
  tasks:
    - name: show the vars
      debug:
        msg: "installing {{ package }} on {{ ansible_hostname }}"
      tags: [show]

    - name: loop over listVar
      debug:
        msg: "{{ item }}"
      loop: "{{ listVar }}"
      tags: [loop]

    - name: install the package
      apt:
        name: "{{ package }}"
        state: present
      tags: [install]
'''

TEMPLATE_YML = '''---
- name: render a per-host config
  hosts: servers
  become: true
  tasks:
    - name: write hostname.conf from a template
      template:
        src: hostname.conf.j2
        dest: /root/hostname.conf
'''

# No {{ }} anywhere — so the first run copies the SAME bytes to both nodes and
# the player can see for themselves that a template without variables is a copy.
HOSTNAME_J2 = '''# managed by Ansible - do not edit by hand
server_name = REPLACE_ME
'''

SITE_YML = '''---
- name: the webserver role
  hosts: servers
  become: true
  roles:
    - webserver
'''


# ------------------------------------------------------------ tiny YAML --
class _YamlError(Exception):
    pass


def _strip_comment(line):
    """Drop a trailing ` # comment`, but not a '#' inside quotes."""
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i]
    return line


def _scalar(text):
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        return [_scalar(p) for p in inner.split(",")] if inner else []
    low = text.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    return text


def _yaml(text):
    """Parse the playbook subset described in the module docstring."""
    if "\t" in text:
        raise _YamlError("found character '\\t' that cannot start any token")
    lines = []
    for raw in text.splitlines():
        body = _strip_comment(raw).rstrip()
        if not body.strip() or body.strip() == "---":
            continue
        indent = len(body) - len(body.lstrip())
        body = body.strip()
        # `- key: value` is two logical lines: the sequence marker, then a map
        # entry two columns further in. Flattening it here keeps the recursive
        # reader below down to two cases.
        while body == "-" or body.startswith("- "):
            lines.append((indent, "-"))
            indent += 2
            body = body[2:].strip() if body.startswith("- ") else ""
            if not body:
                break
        if body:
            lines.append((indent, body))
    value, _ = _read(lines, 0, lines[0][0] if lines else 0)
    return value


def _read(lines, i, indent):
    if i >= len(lines):
        return None, i
    if lines[i][1] == "-":
        seq = []
        while i < len(lines) and lines[i][0] == indent and lines[i][1] == "-":
            i += 1
            if i < len(lines) and lines[i][0] > indent:
                item, i = _read(lines, i, lines[i][0])
            else:
                item = None
            seq.append(item)
        return seq, i
    if ":" not in lines[i][1]:
        # A bare scalar — a list item like `- Item1` or `- webserver`, which is
        # how role names and plain lists are written.
        return _scalar(lines[i][1]), i + 1
    mapping = {}
    while i < len(lines) and lines[i][0] == indent and lines[i][1] != "-":
        key, sep, val = lines[i][1].partition(":")
        if not sep:
            raise _YamlError(f"could not find expected ':' near {lines[i][1]!r}")
        key, val = key.strip(), val.strip()
        i += 1
        if val in ("|", ">"):
            # A literal/folded block: everything indented deeper, verbatim.
            chunk = []
            while i < len(lines) and lines[i][0] > indent:
                chunk.append(lines[i][1])
                i += 1
            mapping[key] = ("\n" if val == "|" else " ").join(chunk)
        elif val:
            mapping[key] = _scalar(val)
        elif i < len(lines) and lines[i][0] > indent:
            mapping[key], i = _read(lines, i, lines[i][0])
        elif i < len(lines) and lines[i][0] == indent and lines[i][1] == "-":
            # A sequence written at the same column as its key — legal YAML and
            # very common in hand-written playbooks.
            mapping[key], i = _read(lines, i, indent)
        else:
            mapping[key] = None
    return mapping, i


# --------------------------------------------------------- variables/Jinja --
_MISSING = object()


class _Undefined(Exception):
    pass


def _lookup(expr, vars_):
    expr = expr.strip()
    if len(expr) >= 2 and expr[0] == expr[-1] and expr[0] in "\"'":
        return expr[1:-1]
    if re.fullmatch(r"-?\d+", expr):
        return int(expr)
    if expr.lower() in ("true", "false"):
        return expr.lower() == "true"
    parts = re.findall(r"[\w.-]+", expr.replace("'", " ").replace('"', " "))
    parts = [p for chunk in parts for p in chunk.split(".") if p]
    cur = vars_
    for p in parts:
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            return _MISSING
    return cur


def _render(text, vars_):
    """Substitute {{ … }} — an undefined name fails the task, as it really does."""
    def one(m):
        val = _lookup(m.group(1), vars_)
        if val is _MISSING:
            raise _Undefined(m.group(1).strip().split(".")[0].split("[")[0])
        return str(val)
    return re.sub(r"\{\{(.*?)\}\}", one, str(text))


def _render_deep(value, vars_):
    if isinstance(value, dict):
        return {k: _render_deep(v, vars_) for k, v in value.items()}
    if isinstance(value, list):
        return [_render_deep(v, vars_) for v in value]
    if isinstance(value, str):
        return _render(value, vars_)
    return value


def _when(expr, vars_):
    """Evaluate a `when:` — the simple comparisons real playbooks are made of."""
    if isinstance(expr, list):
        return all(_when(e, vars_) for e in expr)
    expr = str(expr).strip()
    for joiner, combine in ((" and ", all), (" or ", any)):
        if joiner in expr:
            return combine(_when(p, vars_) for p in expr.split(joiner))
    if expr.startswith("not "):
        return not _when(expr[4:], vars_)
    m = re.match(r"^(.+?)\s*(==|!=|>=|<=|>|<)\s*(.+)$", expr)
    if m:
        left, op, right = _lookup(m.group(1), vars_), m.group(2), _lookup(m.group(3), vars_)
        if left is _MISSING:
            raise _Undefined(m.group(1).strip())
        if right is _MISSING:
            raise _Undefined(m.group(3).strip())
        try:
            return {"==": left == right, "!=": left != right, ">": left > right,
                    "<": left < right, ">=": left >= right, "<=": left <= right}[op]
        except TypeError:                       # int vs str — compare as text
            return {"==": str(left) == str(right), "!=": str(left) != str(right)}.get(op, False)
    val = _lookup(expr, vars_)
    if val is _MISSING:
        raise _Undefined(expr)
    return bool(val)


# --------------------------------------------------------------- the fleet --
def _fleet(world):
    """Per-node state: what Ansible would find already true on each box."""
    return world.flags.setdefault(
        "ansible_state",
        {h: {"pkgs": set(), "files": {}, "svcs": {}, "users": set()} for h in NODES})


def _parse_inventory(text):
    groups, cur = {}, None
    for raw in text.splitlines():
        line = _strip_comment(raw).strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            name = line[1:-1]
            # [group:vars] / [group:children] sections hold no hosts.
            cur = None if ":" in name else groups.setdefault(name, [])
            continue
        if cur is not None:
            host = line.split()[0]
            if host not in cur:
                cur.append(host)
    return groups


def _inventory(world, line):
    """(groups, filename). No -i? This lab falls back to ./hosts — real Ansible
    would read /etc/ansible/hosts, which is what the class lab copies it to."""
    toks = line.split()
    name = "hosts"
    for flag in ("-i", "--inventory", "--inventory-file"):
        if flag in toks and toks.index(flag) + 1 < len(toks):
            name = toks[toks.index(flag) + 1]
    if name not in world.files:
        return None, name
    return _parse_inventory(world.files[name]), name


def _resolve(pattern, groups):
    """Host pattern -> hosts, the way Ansible matches groups and names."""
    if not groups:
        return []
    out = []
    for part in str(pattern).replace(":", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if part == "all":
            for hosts in groups.values():
                out += [h for h in hosts if h not in out]
        elif part in groups:
            out += [h for h in groups[part] if h not in out]
        else:
            for hosts in groups.values():
                if part in hosts and part not in out:
                    out.append(part)
    return out


def _no_hosts(io, pattern, playbook=True):
    io.print(c(f"[WARNING]: Could not match supplied host pattern, ignoring: {pattern}", "yellow"))
    if playbook:
        io.print("skipping: no hosts matched")
    else:
        io.print(c("[WARNING]: No hosts matched, nothing to do", "yellow"))
    io.print(c("(the pattern has to match something the inventory really has — a group in "
               "[brackets] or a host name. `cat hosts` and compare it letter by letter.)", "dim"))


# -------------------------------------------------------------- the modules --
def _mod_args(task, mod):
    """Module args, whether written as a block or inline `apt: name=x state=y`."""
    raw = task.get(mod)
    if isinstance(raw, dict):
        return dict(raw)
    if raw is None:
        return {}
    if isinstance(raw, str) and "=" in raw:
        out = {}
        for tok in re.findall(r"(\w+)=(\"[^\"]*\"|'[^']*'|\S+)", raw):
            out[tok[0]] = _scalar(tok[1])
        return out
    return {"_free_form": raw}


def _find_file(world, name, role):
    """Files resolve inside the role first (roles/<r>/{templates,files}/) — that
    lookup order is exactly why a role can be dropped into any playbook."""
    if role:
        for sub in ("templates", "files"):
            key = f"roles/{role}/{sub}/{name}"
            if key in world.files:
                return world.files[key]
    return world.files.get(name)


def _fake_command(cmd, st):
    """Enough real commands to make `register:` + `when:` mean something."""
    parts = cmd.split()
    if not parts:
        return 0, "", ""
    if parts[0] in ("dpkg", "dpkg-query") and len(parts) > 2:
        pkg = parts[-1]
        if pkg in st["pkgs"]:
            return 0, f"Package: {pkg}\nStatus: install ok installed", ""
        return 1, "", f"dpkg-query: package '{pkg}' is not installed and no information is available"
    if parts[0] == "which" or parts[0] == "command":
        target = parts[-1]
        return (0, f"/usr/bin/{target}", "") if target in st["pkgs"] else (1, "", "")
    if parts[0] == "echo":
        return 0, cmd[5:].strip(), ""
    if parts[0] == "cat":
        path = parts[-1]
        if path in st["files"]:
            return 0, st["files"][path], ""
        return 1, "", f"cat: {path}: No such file or directory"
    if parts[0] == "ls":
        path = parts[-1] if len(parts) > 1 and not parts[-1].startswith("-") else "/"
        under = path.rstrip("/") + "/"
        hits = sorted({f[len(under):].split("/")[0] for f in st["files"] if f.startswith(under)})
        return (0, "\n".join(hits), "") if hits else (
            2, "", f"ls: cannot access '{path}': No such file or directory")
    return 0, "", ""


def _run_module(world, io, host, st, mod, args, role, check):
    """Run one module on one host. -> (status, result-dict-for-register).

    status is 'ok' | 'changed' | 'failed'; result is what `register:` keeps."""
    def res(changed, **extra):
        d = {"changed": changed, "failed": False}
        d.update(extra)
        return ("changed" if changed else "ok"), d

    if mod == "ping":
        return res(False, ping="pong")

    if mod == "apt":
        names = args.get("name") or args.get("pkg") or args.get("package")
        if names is None:
            return "failed", {"failed": True, "changed": False,
                              "msg": "one of the following is required: name, deb, package, pkg"}
        names = names if isinstance(names, list) else [n.strip() for n in str(names).split(",")]
        state = args.get("state", "present")
        changed = False
        for n in names:
            if state == "absent":
                if n in st["pkgs"]:
                    if not check:
                        st["pkgs"].discard(n)
                    changed = True
            elif n not in st["pkgs"]:
                if not check:
                    st["pkgs"].add(n)
                changed = True
        return res(changed)

    if mod in ("copy", "template"):
        dest = args.get("dest") or args.get("path")
        if not dest:
            return "failed", {"failed": True, "changed": False,
                              "msg": "missing required arguments: dest"}
        if "content" in args:
            body = str(args["content"])
        else:
            src = args.get("src")
            body = _find_file(world, src, role)
            if body is None:
                where = "Ansible Controller" if mod == "copy" else "lookup paths"
                return "failed", {"failed": True, "changed": False,
                                  "msg": f"Could not find or access '{src}' on the {where}"}
            if mod == "template":
                body = _render(body, dict(FACTS[host], **args.get("_vars", {})))
                # Remembered so the run can point out a template that rendered
                # the SAME bytes everywhere — the tell-tale of a missing {{ }}.
                world.flags.setdefault("_tpl_dests", set()).add(dest)
        old = st["files"].get(dest)
        if old == body:
            return res(False, dest=dest)
        if not check:
            st["files"][dest] = body
        return res(True, dest=dest)

    if mod in ("service", "systemd"):
        name = args.get("name")
        if not name:
            return "failed", {"failed": True, "changed": False,
                              "msg": "missing required arguments: name"}
        want = args.get("state", "started")
        now = st["svcs"].get(name)
        if want == "restarted":
            if not check:
                st["svcs"][name] = "started"
            return res(True, name=name, state="started")
        if want == "stopped":
            if not check:
                st["svcs"][name] = "stopped"
            return res(now == "started", name=name, state="stopped")
        if not check:
            st["svcs"][name] = "started"
        return res(now != "started", name=name, state="started")

    if mod == "user":
        name = args.get("name")
        if args.get("state") == "absent":
            gone = name in st["users"]
            if not check:
                st["users"].discard(name)
            return res(gone, name=name)
        if name in st["users"]:
            return res(False, name=name)
        if not check:
            st["users"].add(name)
        return res(True, name=name)

    if mod == "file":
        path = args.get("path") or args.get("dest")
        state = args.get("state", "file")
        if state == "absent":
            had = path in st["files"]
            if not check:
                st["files"].pop(path, None)
            return res(had, path=path)
        if path in st["files"]:
            return res(False, path=path)
        if not check:
            st["files"][path] = ""
        return res(True, path=path)

    if mod == "lineinfile":
        path = args.get("path") or args.get("dest")
        line = str(args.get("line", ""))
        body = st["files"].get(path, "")
        if line in body.splitlines():
            return res(False, path=path)
        if not check:
            st["files"][path] = (body + "\n" + line).strip("\n")
        return res(True, path=path)

    if mod in ("command", "shell"):
        cmd = args.get("_free_form") or args.get("cmd") or args.get("argv") or ""
        rc, out, err = _fake_command(str(cmd), st)
        # shell/command can't inspect anything, so they report changed EVERY
        # time — the reason they are the enemy of idempotency.
        status = "failed" if rc else "changed"
        return status, {"changed": rc == 0, "failed": rc != 0, "rc": rc,
                        "cmd": str(cmd), "stdout": out, "stderr": err,
                        "msg": "non-zero return code" if rc else ""}

    return "unsupported", {}


def _unsupported(io, mod):
    # Not dressed up as an Ansible error: `{mod}` really exists, this lab just
    # doesn't simulate it, and saying so beats faking a plausible success.
    io.print(c(f"the '{mod}' module is real Ansible, but this lab doesn't simulate it.", "yellow"))
    io.print(c(f"(simulated here: {' · '.join(MODULES)} — on a real control node "
               f"`ansible-doc {mod}` documents the one you wanted)", "dim"))


# ------------------------------------------------------------- the playbook --
def _bar(io, text):
    io.print(f"\n{text} " + "*" * max(4, 68 - len(text)))


def _status_line(io, status, host, item=None, extra=""):
    suffix = f" => (item={item})" if item is not None else ""
    colour = {"ok": "green", "changed": "yellow", "skipping": "cyan", "failed": "red"}[status]
    io.print(c(f"{status}: [{host}]{suffix}", colour) + extra)


def _play_hosts_vars(play, host, world):
    vars_ = dict(FACTS[host])
    for name in (play.get("vars_files") or []):
        text = world.files.get(name)
        if text:
            try:
                loaded = _yaml(text)
            except _YamlError:
                loaded = None
            if isinstance(loaded, dict):
                vars_.update(loaded)
    vars_.update(play.get("vars") or {})
    return vars_


def _tasks_of(world, io, play):
    """(tasks, handlers, role) — a role's files ARE the play, by convention."""
    roles = play.get("roles") or []
    if not roles:
        return list(play.get("tasks") or []), list(play.get("handlers") or []), None
    name = roles[0]
    if isinstance(name, dict):
        name = name.get("role") or name.get("name")
    if not any(k.startswith(f"roles/{name}/") for k in world.files):
        io.print(c(f"ERROR! the role '{name}' was not found in "
                   f"/root/quest/roles:/root/.ansible/roles:/etc/ansible/roles", "red"))
        io.print(c("(a role is a FOLDER LAYOUT, nothing more: "
                   f"roles/{name}/tasks/main.yml is the file Ansible loads first)", "dim"))
        return None, None, None
    tasks, handlers = [], []
    for path, into in ((f"roles/{name}/tasks/main.yml", "tasks"),
                       (f"roles/{name}/handlers/main.yml", "handlers")):
        text = world.files.get(path)
        if text is None:
            continue
        loaded = _yaml(text)
        if isinstance(loaded, list):
            (tasks if into == "tasks" else handlers).extend(loaded)
    if not tasks:
        io.print(c(f"[WARNING]: roles/{name}/tasks/main.yml is missing or empty — "
                   "that is the file a role runs", "yellow"))
    return tasks, handlers, name


# Keys a task can carry that are NOT the module it calls.
TASK_KEYWORDS = {"name", "when", "register", "loop", "with_items", "tags", "notify",
                 "ignore_errors", "failed_when", "changed_when", "become", "become_user",
                 "vars", "args", "delegate_to", "until", "retries", "delay", "no_log",
                 "check_mode", "environment", "run_once", "block"}


def _module_of(task):
    """The one module a task calls — or the name it TRIED to call, so an
    unsimulated module gets named instead of reported as 'no module at all'."""
    for key in task:
        if key in MODULES:
            return key
    for key in task:
        if key not in TASK_KEYWORDS:
            return key
    return None


def _run_task(world, io, task, hosts, play, role, opts, failed, registered, notified, tally):
    """One task, every host. Mutates the per-host tallies/registers in place."""
    check = opts["check"]
    mod = _module_of(task)
    name = task.get("name") or (mod or "task")
    # A handler already announced itself as RUNNING HANDLER [...] — real Ansible
    # prints that banner instead of a second TASK one, not as well as.
    banner = (lambda: None) if opts.get("quiet_banner") else (lambda: _bar(io, f"TASK [{name}]"))
    if mod is None:
        banner()
        io.print(c("ERROR! no module/action detected in task.", "red"))
        io.print(c("(every task calls exactly ONE module — `name:` alone is a label, not work)", "dim"))
        return False
    if mod not in MODULES:
        banner()
        _unsupported(io, mod)
        return False

    banner()
    if check and mod in ("command", "shell"):
        # Check mode refuses to run raw commands — it has no way to know what
        # they would do. THIS is the honest answer to "what can't --check predict".
        for host in hosts:
            if host in failed:
                continue
            _status_line(io, "skipping", host)
            tally[host]["skipped"] += 1
            if task.get("register"):
                registered[host][str(task["register"])] = {"changed": False, "skipped": True}
        io.print(c("(--check never runs a command/shell task — so anything downstream that "
                   "depends on its result is guesswork in a dry run)", "dim"))
        return True
    hinted = False
    for host in hosts:
        if host in failed:
            continue
        st = _fleet(world)[host]
        vars_ = dict(_play_hosts_vars(play, host, world))
        vars_.update(registered[host])
        try:
            if task.get("when") is not None:
                if _refs_register(task["when"], registered[host]):
                    world.flags["register_when"] = True
                try:
                    keep = _when(_render_deep(task["when"], vars_) if "{{" in str(task["when"])
                                 else task["when"], vars_)
                except _Undefined as exc:
                    # Ansible words a broken conditional differently from a broken
                    # template, and the difference is the whole diagnosis.
                    io.print(c(f"fatal: [{host}]: FAILED! => {{\"msg\": \"The conditional check "
                               f"'{task['when']}' failed. The error was: '{exc}' is undefined\"}}",
                               "red"))
                    if not failed:
                        io.print(c("(a when: can only branch on a variable that exists — register "
                                   "the task it comes from first, and remember --check never runs "
                                   "command/shell, so their results don't exist in a dry run)", "dim"))
                    tally[host]["failed"] += 1
                    failed.add(host)
                    continue
                if not keep:
                    _status_line(io, "skipping", host)
                    tally[host]["skipped"] += 1
                    continue
            items = task.get("loop", task.get("with_items"))
            if items is not None:
                items = _lookup(str(items).strip("{} "), vars_) if "{{" in str(items) else items
                if items is _MISSING or not isinstance(items, list):
                    raise _Undefined(str(task.get("loop", task.get("with_items"))))
            statuses, result = [], {}
            for item in (items if items is not None else [None]):
                if item is not None:
                    vars_["item"] = item
                args = _render_deep(_mod_args(task, mod), vars_)
                args["_vars"] = vars_
                if mod == "debug":
                    msg = args.get("msg")
                    if "var" in args:
                        val = _lookup(str(args["var"]), vars_)
                        msg = "VARIABLE IS NOT DEFINED!" if val is _MISSING else val
                    _status_line(io, "ok", host, item,
                                 ' => {\n    "msg": ' + f'"{msg if msg is not None else "Hello world!"}"' + "\n}")
                    statuses.append("ok")
                    result = {"changed": False, "failed": False, "msg": msg}
                    continue
                status, result = _run_module(world, io, host, st, mod, args, role, check)
                if status == "unsupported":     # a MODULES entry with no code behind it
                    _unsupported(io, mod)
                    return False
                if status == "failed" and task.get("failed_when") is False:
                    status, result["failed"] = "changed", False
                statuses.append(status)
                if status == "failed":
                    detail = result.get("stderr") or result.get("msg", "failed")
                    rc = result.get("rc")
                    io.print(c(f"fatal: [{host}]: FAILED! => {{\"changed\": false, "
                               + (f'"rc": {rc}, ' if rc is not None else "")
                               + f'"msg": "{detail}"}}', "red"))
                    if task.get("ignore_errors"):
                        io.print(c("...ignoring", "magenta"))
                    elif rc is not None and not hinted:
                        hinted = True
                        io.print(c("(a non-zero rc FAILS the task and stops the play for that "
                                   "host — `ignore_errors: true` or `failed_when:` is how a "
                                   "*check* stops killing the run)", "dim"))
                    break
                _status_line(io, status, host, item)
        except _Undefined as exc:
            io.print(c(f"fatal: [{host}]: FAILED! => "
                       f'{{"msg": "The task includes an option with an undefined variable. '
                       f"The error was: '{exc}' is undefined\"}}", "red"))
            if not failed:                       # say it once, not once per host
                io.print(c("(Ansible refuses to render half a file — define the variable in "
                           "`vars:`, a vars_file, or group_vars)", "dim"))
            tally[host]["failed"] += 1
            failed.add(host)
            continue

        if task.get("register"):
            registered[host][str(task["register"])] = result
        worst = "failed" if "failed" in statuses else ("changed" if "changed" in statuses else "ok")
        if worst == "failed":
            if not task.get("ignore_errors"):
                tally[host]["failed"] += 1
                failed.add(host)
                continue
            tally[host]["ignored"] += 1
        tally[host]["ok"] += 1
        if worst == "changed":
            tally[host]["changed"] += 1
            for hname in _notify_list(task):
                if hname not in notified[host]:
                    notified[host].append(hname)
    return True


def _notify_list(task):
    n = task.get("notify")
    if n is None:
        return []
    return [str(x) for x in (n if isinstance(n, list) else [n])]


def _refs_register(expr, registered):
    return any(re.search(rf"\b{re.escape(name)}\b", str(expr)) for name in registered)


def _task_tags(task):
    t = task.get("tags")
    if t is None:
        return []
    return [str(x) for x in (t if isinstance(t, list) else [t])]


def _wanted_tags(line):
    toks = re.split(r"[\s=]+", line)
    out = []
    for i, tok in enumerate(toks):
        if tok in ("--tags", "-t", "--skip-tags") and i + 1 < len(toks):
            out += [t for t in toks[i + 1].split(",") if t]
    return out


def _playbook(world, m, io):
    line = m.group(0)
    toks = line.split()
    book = next((t for t in toks[1:] if t.endswith((".yml", ".yaml"))), None)
    if book is None:
        from engine import TOOL_VERSION_LINES
        if "--version" in toks:
            io.print(TOOL_VERSION_LINES["ansible-playbook"])
        else:
            io.print("Usage: ansible-playbook [-i INVENTORY] [--check] [--tags TAGS] playbook.yml")
            io.print(c("(the playbook file is the argument — `ls` shows which ones are here)", "dim"))
        world.flags["_noop"] = True
        return
    if book not in world.files:
        io.print(c(f"ERROR! the playbook: {book} could not be found", "red"))
        io.print(c("(`ls` shows what's actually here — Ansible looks in the current directory)", "dim"))
        world.flags["_noop"] = True
        return
    try:
        plays = _yaml(world.files[book])
    except _YamlError as exc:
        io.print(c(f"ERROR! Syntax Error while loading YAML.\n  {exc}", "red"))
        io.print(c("(YAML is spaces-only and indentation IS the structure — tabs are a syntax error)", "dim"))
        world.flags["_noop"] = True
        return
    if isinstance(plays, dict):
        plays = [plays]
    if not isinstance(plays, list) or not all(isinstance(p, dict) for p in plays):
        io.print(c("ERROR! A playbook must be a list of plays (it starts with '- hosts: …')", "red"))
        world.flags["_noop"] = True
        return

    check = "--check" in toks or "-C" in toks
    tags = _wanted_tags(line)
    skip = "--skip-tags" in line
    groups, inv_name = _inventory(world, line)
    if groups is None:
        io.print(c(f"[WARNING]: Unable to parse {inv_name} as an inventory source", "yellow"))
        io.print(c("[WARNING]: No inventory was parsed, only implicit localhost is available", "yellow"))
        io.print(c(f"(no `{inv_name}` file here — `ls`, then point at it: -i <file>)", "dim"))
        world.flags["_noop"] = True
        return
    if not any(f in toks for f in ("-i", "--inventory", "--inventory-file")):
        io.print(c("(no -i given — real Ansible would read /etc/ansible/hosts, which is where the "
                   "class lab copies this file. This lab reads ./hosts instead.)", "dim"))

    if "--syntax-check" in toks:
        io.print(f"\nplaybook: {book}")
        io.print(c("(syntax-check parses the YAML and stops — nothing is contacted, nothing runs)", "dim"))
        world.flags["_noop"] = True
        return
    if "--list-tags" in toks or "--list-tasks" in toks:
        for n, play in enumerate(plays, 1):
            tasks = play.get("tasks") or []
            io.print(f"\n  play #{n} ({play.get('hosts')}): {play.get('name', '')}\tTAGS: []")
            if "--list-tasks" in toks:
                io.print("    tasks:")
                for task in tasks:
                    io.print(f"      {task.get('name', _module_of(task))}\t"
                             f"TAGS: [{', '.join(_task_tags(task))}]")
            else:
                found = sorted({t for task in tasks for t in _task_tags(task)})
                io.print(f"      TASK TAGS: [{', '.join(found)}]")
        io.print(c("(both of these read the file and stop — nothing is contacted, nothing runs)", "dim"))
        world.flags["_noop"] = True
        return

    prior_runs = world.flags.get("play_runs", 0)
    world.flags["_tpl_dests"] = set()
    snapshot = copy.deepcopy(_fleet(world)) if check else None
    ran_anything, matched, handler_ran, role_used = False, False, False, None
    total_changed, total_failed, filtered_out = 0, 0, False

    limit = None
    for flag in ("-l", "--limit"):
        if flag in toks and toks.index(flag) + 1 < len(toks):
            limit = toks[toks.index(flag) + 1]

    for play in plays:
        hosts = _resolve(play.get("hosts", "all"), groups)
        _bar(io, f"PLAY [{play.get('name', play.get('hosts', 'all'))}]")
        if hosts and limit:
            allowed = _resolve(limit, groups)
            hosts = [h for h in hosts if h in allowed]
            if not hosts:
                _no_hosts(io, limit, playbook=True)
                continue
        if not hosts:
            _no_hosts(io, play.get("hosts"), playbook=True)
            continue
        matched = True
        tasks, handlers, role = _tasks_of(world, io, play)
        if tasks is None:
            return
        role_used = role or role_used
        if play.get("gather_facts") is not False:
            _bar(io, "TASK [Gathering Facts]")
            for h in hosts:
                _status_line(io, "ok", h)
        tally = {h: {"ok": 0, "changed": 0, "skipped": 0, "failed": 0, "ignored": 0}
                 for h in hosts}
        registered = {h: {} for h in hosts}
        notified = {h: [] for h in hosts}
        failed = set()
        if play.get("gather_facts") is not False:
            for h in hosts:
                tally[h]["ok"] += 1

        selected = []
        for task in tasks:
            ttags = _task_tags(task) + ["all"]
            if tags:
                hit = any(t in ttags for t in tags)
                if hit == skip:
                    filtered_out = True
                    continue
            selected.append(task)
        if tags and not selected:
            io.print(c(f"ERROR! No matching task found. Tags: [{', '.join(tags)}]", "red"))
            io.print(c("(--tags only runs tasks that CARRY that tag — `--list-tags` shows which "
                       "tags exist)", "dim"))
            return
        for task in selected:
            ran_anything = True
            if not _run_task(world, io, task, hosts, play, role, {"check": check},
                             failed, registered, notified, tally):
                return
            if len(failed) == len(hosts):
                # Every host is out: Ansible stops the play right here rather
                # than printing task banners nobody will run.
                _bar(io, "NO MORE HOSTS LEFT")
                break

        for handler in handlers:
            hosts_notified = [h for h in hosts if handler.get("name") in notified[h] and h not in failed]
            if not hosts_notified:
                continue
            handler_ran = True
            _bar(io, f"RUNNING HANDLER [{handler.get('name')}]")
            _run_task(world, io, handler, hosts_notified, play, role,
                      {"check": check, "quiet_banner": True},
                      failed, registered, {h: [] for h in hosts}, tally)

        _bar(io, "PLAY RECAP")
        for h in hosts:
            t = tally[h]
            total_changed += t["changed"]
            total_failed += t["failed"]
            io.print(f"{h:<20}: " + c(f"ok={t['ok']}", "green") + "    "
                     + (c(f"changed={t['changed']}", "yellow") if t["changed"] else "changed=0")
                     + "    unreachable=0    "
                     + (c(f"failed={t['failed']}", "red") if t["failed"] else "failed=0")
                     + f"    skipped={t['skipped']}    ignored={t['ignored']}")

    if not matched:
        world.flags["_noop"] = True
        return
    world.flags["hosts_matched"] = True
    if check:
        world.flags["ansible_state"] = snapshot          # a dry run touches NOTHING
        io.print(c("\n(--check = dry run: it reported, and it changed nothing. What a dry run "
                   "cannot predict: command/shell tasks, which it refuses to run at all.)", "dim"))
        world.flags["check_ran"] = True
        world.flags["_noop"] = True
        return
    if not ran_anything:
        return
    world.flags["play_runs"] = prior_runs + 1
    if tags and filtered_out:
        world.flags["tags_used"] = True
    if handler_ran and prior_runs >= 1:
        world.flags["handler_refired"] = True
    # A tag-filtered run only executed part of the playbook, so "changed=0"
    # there proves nothing about the play as a whole — don't claim it does.
    if (total_changed == 0 and not handler_ran and not tags and not total_failed
            and prior_runs >= 1):
        io.print(c("\nchanged=0 everywhere — THAT is idempotency: re-running is safe, "
                   "only drift gets fixed", "dim"))
        world.flags["idempotent_proven"] = True
        if role_used:
            world.flags["role_idempotent"] = True
    if role_used:
        world.flags["role_ran"] = True
    _template_notes(world, io)
    # `become` never fails here (the inventory logs in as root), so the lesson
    # has to be told rather than felt — once, not on every run.
    if not world.flags.get("become_noted") and any(
            p.get("become") is not True for p in plays):
        world.flags["become_noted"] = True
        io.print(c("\n(this play worked without `become: true` only because the inventory logs "
                   "in as root — on a normal account apt/service/user fail with Permission "
                   "denied. Real playbooks set become.)", "dim"))


def _template_notes(world, io):
    """Nudge the player toward the point of a template: it renders PER HOST."""
    fleet = _fleet(world)
    for dest in sorted(world.flags.pop("_tpl_dests", ())):
        bodies = [fleet[h]["files"].get(dest) for h in NODES]
        if bodies[0] is not None and bodies[0] == bodies[1]:
            io.print(c(f"\n(both nodes got byte-identical {dest} — a template with no "
                       "{{ variables }} in it is just a copy with extra steps)", "dim"))
            return


# --------------------------------------------------------------- ad-hoc CLI --
def _ansible(world, m, io):
    line = m.group(0).strip()
    toks = line.split()
    if any(t in toks for t in ("--version", "-v", "--help", "-h")) and len(toks) == 2:
        from engine import TOOL_VERSION_LINES
        io.print(TOOL_VERSION_LINES["ansible"] if "--version" in toks else
                 "Usage: ansible <host-pattern> [-i INVENTORY] [-m MODULE] [-a ARGS]")
        world.flags["_noop"] = True
        return
    if len(toks) == 1:
        io.print("Usage: ansible <host-pattern> [-i INVENTORY] [-m MODULE] [-a ARGS]")
        io.print(c("(try:  ansible all -i hosts -m ping)", "dim"))
        world.flags["_noop"] = True
        return

    mod = None
    args_text = ""
    pattern = None
    i = 1
    while i < len(toks):
        tok = toks[i]
        if tok in ("-m", "--module-name") and i + 1 < len(toks):
            mod, i = toks[i + 1], i + 2
        elif tok in ("-a", "--args") and i + 1 < len(toks):
            args_text, i = toks[i + 1], i + 2
        elif tok in ("-i", "--inventory", "-u", "--user", "-l", "--limit") and i + 1 < len(toks):
            i += 2
        elif tok.startswith("-"):
            i += 1
        else:
            pattern = pattern or tok
            i += 1
    # `-a "echo hi"` survives shlex only if the whole line is re-read; recover
    # the quoted args from the raw line so multi-word arguments stay intact.
    quoted = re.search(r"(?:-a|--args)\s+(\"[^\"]*\"|'[^']*'|\S+)", line)
    if quoted:
        args_text = _scalar(quoted.group(1))

    groups, inv_name = _inventory(world, line)
    if groups is None:
        io.print(c(f"[WARNING]: Unable to parse {inv_name} as an inventory source", "yellow"))
        io.print(c("[WARNING]: No inventory was parsed, only implicit localhost is available", "yellow"))
        world.flags["_noop"] = True
        return
    hosts = _resolve(pattern or "all", groups)
    if not hosts:
        _no_hosts(io, pattern, playbook=False)
        world.flags["_noop"] = True
        return

    default_mod = mod is None
    if default_mod:
        mod = "command"
    if mod not in MODULES:
        _unsupported(io, mod)
        world.flags["_noop"] = True
        return

    task = {mod: args_text if args_text else None}
    changed_any, ok_any = False, False
    for host in hosts:
        st = _fleet(world)[host]
        args = _mod_args(task, mod)
        if mod == "debug":
            io.print(c(f"{host} | SUCCESS => ", "green")
                     + '{\n    "msg": "%s"\n}' % (args.get("msg", "Hello world!")))
            continue
        status, result = _run_module(world, io, host, st, mod, args, None, False)
        if mod in ("command", "shell"):
            rc = result.get("rc", 0)
            head = c(f"{host} | CHANGED | rc=0 >>", "yellow") if rc == 0 else \
                c(f"{host} | FAILED | rc={rc} >>", "red")
            io.print(head)
            io.print(result.get("stdout") or result.get("stderr") or "")
            changed_any = changed_any or rc == 0
            continue
        if status == "failed":
            io.print(c(f"{host} | FAILED! => ", "red")
                     + '{\n    "changed": false,\n    "msg": "%s"\n}' % result.get("msg", ""))
            continue
        body = ",\n    ".join(f'"{k}": {_json(v)}' for k, v in sorted(result.items())
                              if k not in ("failed",))
        if status == "changed":
            io.print(c(f"{host} | CHANGED => ", "yellow") + "{\n    " + body + "\n}")
            changed_any = True
        else:
            io.print(c(f"{host} | SUCCESS => ", "green") + "{\n    " + body + "\n}")
            ok_any = True

    if mod == "ping":
        world.flags["ansible_pinged"] = True
        world.flags["_noop"] = True
    if mod == "apt":
        if changed_any:
            world.flags["adhoc_apt_changed"] = True
        elif ok_any and world.flags.get("adhoc_apt_changed"):
            world.flags["adhoc_apt_ok"] = True
            io.print(c("(changed the first time, ok the second — the command didn't change, "
                       "the WORLD did. That's idempotency, no playbook required.)", "dim"))
    if default_mod:
        io.print(c("(no -m, so Ansible used its default module: command. -m picks the MODULE, "
                   "-a passes its ARGUMENTS as key=value pairs.)", "dim"))
    if mod in ("command", "shell"):
        io.print(c("(command/shell report CHANGED every single time — they can't tell whether "
                   "the work was already done. That's why real modules exist.)", "dim"))
    if not changed_any:
        world.flags["_noop"] = True


def _json(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return f'"{value}"'


# ------------------------------------------------------------- other tools --
DOCS = {
    "apt": ("APT", "Manages apt packages (such as for Debian/Ubuntu).",
            "- name: Install nginx\n  apt:\n    name: nginx\n    state: present"),
    "copy": ("COPY", "Copy files from the control node to the managed nodes.",
             "- name: Copy a file\n  copy:\n    src: index.html\n    dest: /var/www/html/index.html"),
    "template": ("TEMPLATE", "Template a file out to a target host (Jinja2, rendered PER HOST).",
                 "- name: Render a config\n  template:\n    src: hostname.conf.j2\n"
                 "    dest: /root/hostname.conf"),
    "service": ("SERVICE", "Controls services on remote hosts (started/stopped/restarted).",
                "- name: Start nginx\n  service:\n    name: nginx\n    state: started"),
    "user": ("USER", "Manage user accounts.",
             "- name: Add a user\n  user:\n    name: avielb\n    state: present"),
    "debug": ("DEBUG", "Print statements during execution.",
              '- name: Show a var\n  debug:\n    msg: "{{ package }}"'),
    "ping": ("PING", "Try to connect to host, verify a usable python and return 'pong'.",
             "- name: Are you there?\n  ping:"),
    "command": ("COMMAND", "Execute commands on targets. NOT idempotent — always reports changed.",
                '- name: Check a package\n  command: dpkg -s nginx\n  register: result\n'
                "  ignore_errors: true"),
}


def _ansible_doc(world, m, io):
    world.flags["_noop"] = True
    arg = m.group(0).split()[1:]
    arg = [a for a in arg if not a.startswith("-")]
    if "-l" in m.group(0).split() or not arg:
        io.print("\n".join(f"{k:<12} {DOCS[k][1]}" for k in sorted(DOCS)))
        io.print(c("(on a real control node `ansible-doc -l` lists thousands of modules, "
                   "offline; this lab documents the ones it simulates)", "dim"))
        world.flags["ansible_doc"] = True
        return
    name = arg[0]
    if name not in DOCS:
        io.print(f"[WARNING]: module {name} not found in: /usr/lib/python3/dist-packages/ansible/modules")
        io.print(c(f"(this lab ships docs for: {' · '.join(sorted(DOCS))})", "dim"))
        return
    title, summary, example = DOCS[name]
    io.print(f"> {title}    (/usr/lib/python3/ansible/modules/{name}.py)\n\n"
             f"        {summary}\n\nEXAMPLES:\n{example}")
    world.flags["ansible_doc"] = True


# The skeleton `ansible-galaxy init` writes. Each file is a header comment and
# nothing else — which is the point: the tool gives you the LAYOUT, and the
# layout is the only thing a role really is.
GALAXY_SKELETON = ("defaults/main.yml", "handlers/main.yml", "meta/main.yml",
                   "tasks/main.yml", "vars/main.yml", "tests/inventory", "tests/test.yml")


def _galaxy(world, m, io):
    """`ansible-galaxy` — init works offline, install does not.

    `init` scaffolds from a skeleton bundled with the ansible package; it never
    opens a socket. Faking a network error there taught a false fact about the
    tool in order to force hand-building the role — and the role still has to be
    hand-built, because a skeleton's tasks/main.yml is an empty list."""
    args = m.group(0).split()[1:]
    sub = args[0] if args else ""
    flags = [a for a in args[1:] if a.startswith("-")]
    names = [a for a in args[1:] if not a.startswith("-")]
    if sub in ("init", "role") and names and (sub == "init" or names[0] == "init"):
        path = (names[1] if sub == "role" else names[0]).rstrip("/")
        exists = any(k.startswith(path + "/") for k in world.files)
        if exists and "--force" not in flags and "-f" not in flags:
            io.print(c(f"ERROR! - the directory {path} already exists.", "red"))
            io.print("you can use --force to re-initialize this directory,")
            io.print("however it will reset any main.yml files that may have")
            io.print("been modified there already.")
            world.flags["_noop"] = True
            return
        role = path.split("/")[-1]
        world.files[f"{path}/README.md"] = f"Role Name\n=========\n\n{role}\n"
        for rel in GALAXY_SKELETON:
            world.files[f"{path}/{rel}"] = f"---\n# {rel.split('/')[0]} file for {role}\n"
        io.print(f"- Role {path} was created successfully")
        io.print(c("(that is the whole trick: folders. tasks/main.yml is an empty list right now — "
                   "the scaffold gives you the LAYOUT, you still write the play.)", "dim"))
        return
    world.flags["_noop"] = True
    if sub in ("install", "search", "info", "remove", "list"):
        io.print(c("ERROR! Unknown error when attempting to call Galaxy server "
                   "'https://galaxy.ansible.com': network is unreachable", "red"))
        io.print(c(f"(`ansible-galaxy {sub}` is the half that really does need the internet — "
                   "there is none in this lab. A Galaxy role is only ever a roles/<name>/ folder "
                   "someone else wrote; `ansible-galaxy init` scaffolds an empty one offline.)",
                   "dim"))
        return
    io.print("usage: ansible-galaxy [init|install|search|list] ...")
    io.print(c("(`ansible-galaxy init roles/<name>` is the one that works offline)", "dim"))


def _vault(world, m, io):
    world.flags["_noop"] = True
    io.print(c("(ansible-vault opens $EDITOR and asks for a password twice — neither works in a "
               "simulated terminal, so this lab won't pretend.)", "dim"))
    io.print(c("   On a real control node:  ansible-vault create secrets.yml   then run the play "
               "with  --ask-vault-pass. The file is AES-encrypted on disk, so it is safe in git.", "dim"))


def _ssh(world, m, io):
    """`ssh nodeN <cmd>` — the only honest way to check what actually landed."""
    world.flags["_noop"] = True
    toks = m.group(0).split()[1:]
    toks = [t for t in toks if not t.startswith("-")]
    if not toks:
        io.print("usage: ssh node1 <command>")
        return
    host = toks[0]
    if host not in NODES:
        io.print(f"ssh: Could not resolve hostname {host}: Name or service not known")
        io.print(c(f"(this lab's fleet is: {' · '.join(NODES)})", "dim"))
        return
    if len(toks) == 1:
        io.print(c("(an interactive ssh session would open a shell this simulator can't run — "
                   "pass the command instead:  ssh node1 cat /root/hostname.conf)", "dim"))
        return
    st = _fleet(world)[host]
    cmd = " ".join(toks[1:])
    prog = toks[1]
    if prog == "rm":
        target = toks[-1]
        if target in st["files"]:
            st["files"].pop(target)
            world.flags.pop("_noop", None)          # deleting a file IS a change
            io.print(c(f"(drift! {host} no longer has {target} — the next play run will "
                       "notice and fix it)", "dim"))
        else:
            io.print(f"rm: cannot remove '{target}': No such file or directory")
        return
    if prog == "userdel" and len(toks) > 2:
        if toks[-1] in st["users"]:
            st["users"].discard(toks[-1])
            world.flags.pop("_noop", None)
            io.print(c(f"(drift! the user is gone from {host} — re-run the play and watch it "
                       "come back as `changed`)", "dim"))
        else:
            io.print(f"userdel: user '{toks[-1]}' does not exist")
        return
    rc, out, err = _fake_command(cmd, st)
    if out:
        io.print(out)
    if err:
        io.print(err)


def _curl(world, m, io):
    world.flags["_noop"] = True
    host = re.search(r"node[12]", m.group(0)).group(0)
    st = _fleet(world)[host]
    page = st["files"].get("/var/www/html/index.html")
    if st["svcs"].get("nginx") != "started" or page is None:
        io.print(f"curl: (7) Failed to connect to {host} port 80: Connection refused")
        io.print(c("(nothing is serving there yet — install nginx, deploy the page and make "
                   "sure the service is started)", "dim"))
        return
    io.print(page)


HANDLERS = [
    (r"ansible-playbook\s+.*", _playbook),
    (r"ansible-doc(\s+.*)?", _ansible_doc),
    (r"ansible-galaxy(\s+.*)?", _galaxy),
    (r"ansible-vault(\s+.*)?", _vault),
    (r"ansible(\s+.*)?", _ansible),
    (r"ssh(\s+.*)?", _ssh),
    (r"curl\s+.*node[12].*", _curl),
]


# ------------------------------------------------------------- check helpers --
def _per_host_rendered(world, dest):
    """True when the SAME template produced DIFFERENT files on each node."""
    fleet = world.flags.get("ansible_state") or {}
    bodies = [fleet.get(h, {}).get("files", {}).get(dest) for h in NODES]
    if any(b is None for b in bodies) or bodies[0] == bodies[1]:
        return False
    return all(h in bodies[i] for i, h in enumerate(NODES))


def _all_nodes_have(world, pkg=None, svc=None):
    fleet = world.flags.get("ansible_state") or {}
    for h in NODES:
        st = fleet.get(h, {})
        if pkg and pkg not in st.get("pkgs", set()):
            return False
        if svc and st.get("svcs", {}).get(svc) != "started":
            return False
    return True


MISSIONS = [
    {
        "id": "ansible-01",
        "topic": "ansible",
        "title": "Agentless Army 📜 — one playbook, N servers",
        "vault_note": "Class 11 - Ansible",
        "brief": ("Two fresh Ubuntu nodes (node1, node2) and zero agents installed —\n"
                  "Ansible works over plain SSH. The inventory (hosts) and playbook.yml\n"
                  "are here (cat them!). Reach the nodes, fire an ad-hoc command, then\n"
                  "run the playbook and prove the two ideas everyone gets asked about:\n"
                  "IDEMPOTENCY and HANDLERS. `ssh node1 cat <file>` checks what landed."),
        "world": {
            "files": {"hosts": HOSTS_INI, "playbook.yml": PLAYBOOK, "index.html": INDEX_HTML},
        },
        "handlers": HANDLERS,
        "objectives": [
            {"desc": "Prove you can reach every host (agentless!)", "xp": 10,
             "hint": "ansible all -i hosts -m ping — 'pong' means SSH + Python are good to go.",
             "check": lambda w: w.flags.get("ansible_pinged")},
            {"desc": "Install a package ad-hoc, then run the SAME command again (changed → ok)", "xp": 15,
             "hint": 'ansible web -i hosts -m apt -a "name=vim state=present" — twice. '
                     "-m picks the module, -a passes its key=value arguments.",
             "check": lambda w: w.flags.get("adhoc_apt_ok")},
            {"desc": "Dry-run the playbook: report the changes without making any", "xp": 15,
             "hint": "ansible-playbook -i hosts playbook.yml --check — the rehearsal every "
                     "change to a live fleet deserves.",
             "check": lambda w: w.flags.get("check_ran")},
            {"desc": "Run the playbook for real — configure BOTH nodes in one shot", "xp": 20,
             "hint": "ansible-playbook -i hosts playbook.yml",
             "check": lambda w: (w.flags.get("play_runs", 0) >= 1
                                 and _all_nodes_have(w, "nginx", "nginx"))},
            {"desc": "Run it AGAIN — prove idempotency (changed=0)", "xp": 20,
             "hint": "Same command, second run. Watch every task report ok, not changed.",
             "check": lambda w: w.flags.get("idempotent_proven")},
            {"desc": "Cause drift, re-run — ONLY the copy task changes + the handler fires", "xp": 25,
             "hint": "edit index.html (new text) — or delete it off a node with "
                     "`ssh node1 rm /var/www/html/index.html` — then run the playbook again. "
                     "The 'notify' on the copy task wakes the restart-nginx handler.",
             "check": lambda w: w.flags.get("handler_refired")},
            {"desc": "Look up a module's docs WITHOUT leaving the terminal", "xp": 10,
             "hint": "ansible-doc apt — docs + copy-paste examples, offline.",
             "check": lambda w: w.flags.get("ansible_doc")},
        ],
        "teach": [
            "ping proves SSH + Python reachability — agentless means NOTHING to install on the nodes.",
            "-m module, -a arguments, no -m = the command module. Ad-hoc is for one-off work.",
            "--check is a dry run: it reports what WOULD change. It can't predict shell tasks it never ran.",
            "One playbook, N hosts, identical result — the inventory decides who gets configured.",
            "changed=0 on the re-run IS idempotency: safe to run anytime; only drift gets fixed.",
            "notify + handler: the restart fired ONLY because something really changed — once, at the end.",
            "ansible-doc is offline docs with copy-paste examples — faster than any browser tab.",
        ],
        "solution": [
            "cat hosts",
            "ansible all -i hosts -m ping",
            'ansible web -i hosts -m apt -a "name=vim state=present"',
            'ansible web -i hosts -m apt -a "name=vim state=present"',
            "ansible-playbook -i hosts playbook.yml --check",
            "ansible-playbook -i hosts playbook.yml",
            "ansible-playbook -i hosts playbook.yml",
            "edit index.html",
            "<h1>Hello from Ansible v2!</h1>", ".",
            "ansible-playbook -i hosts playbook.yml",
            "ssh node1 cat /var/www/html/index.html",
            "ansible-doc apt",
        ],
    },
    {
        "id": "ansible-02",
        "topic": "ansible",
        "title": "Playbook Pro 📜 — tags, when, Jinja2 and a role",
        "vault_note": "Class 11 - Ansible",
        "brief": ("Same two nodes, grown-up playbooks: vars.yml (tagged tasks), template.yml\n"
                  "(a Jinja2 render) and site.yml (a ROLE that doesn't exist yet). Nothing\n"
                  "runs at first — read the warning and fix the inventory before anything else.\n\n"
                  "The boss is the role. Build it by hand — Ansible loads it by CONVENTION:\n\n"
                  "  roles/webserver/tasks/main.yml       ← install nginx, deploy page, notify\n"
                  "  roles/webserver/handlers/main.yml    ← restart nginx\n"
                  "  roles/webserver/templates/index.html.j2\n\n"
                  "Verify like a human would: `ssh node1 cat <file>` and `curl node1`."),
        "world": {
            "files": {"hosts": INV_INI, "vars.yml": VARS_YML, "template.yml": TEMPLATE_YML,
                      "hostname.conf.j2": HOSTNAME_J2, "site.yml": SITE_YML},
        },
        "handlers": HANDLERS,
        "objectives": [
            {"desc": "Nothing runs: make the play's host group match the inventory", "xp": 10,
             "hint": "Every playbook says `hosts: servers`; `cat hosts` says [managed_nodes]. "
                     "Rename the group in the inventory (edit hosts) — or change every play.",
             "check": lambda w: w.flags.get("hosts_matched")},
            {"desc": "Run ONLY part of a playbook, by tag", "xp": 15,
             "hint": "ansible-playbook -i hosts vars.yml --tags=install  (--list-tags shows "
                     "which tags exist). Untagged tasks never even start.",
             "check": lambda w: w.flags.get("tags_used")},
            {"desc": "register a command's result, then branch on it with when:", "xp": 25,
             "hint": "Add to vars.yml: a `command: dpkg -s nginx` task with `register: nginx_check` "
                     "and `ignore_errors: true` (a non-zero rc FAILS the task otherwise), then a "
                     "task with `when: nginx_check.rc != 0`. Run it twice — second time it skips.",
             "check": lambda w: w.flags.get("register_when")},
            {"desc": "Render hostname.conf per host — each node gets its OWN name", "xp": 25,
             "hint": "edit hostname.conf.j2 and put {{ ansible_hostname }} in it, then "
                     "ansible-playbook -i hosts template.yml. Check with: ssh node1 cat "
                     "/root/hostname.conf",
             "check": lambda w: _per_host_rendered(w, "/root/hostname.conf")},
            {"desc": "Build the webserver ROLE and run site.yml — nginx + per-host page", "xp": 25,
             "hint": "edit roles/webserver/tasks/main.yml — a list of tasks: apt nginx, "
                     "template src=index.html.j2 dest=/var/www/html/index.html with "
                     "`notify: restart nginx`, service nginx started. handlers/main.yml holds the "
                     "restart task; templates/index.html.j2 holds {{ ansible_hostname }}.",
             "check": lambda w: (w.flags.get("role_ran") and _all_nodes_have(w, "nginx", "nginx")
                                 and _per_host_rendered(w, "/var/www/html/index.html"))},
            {"desc": "Run site.yml AGAIN — all ok, and the handler stays silent", "xp": 15,
             "hint": "Same command. A handler that fires on an unchanged run would restart nginx "
                     "for nothing — that's exactly what notify prevents.",
             "check": lambda w: w.flags.get("role_idempotent")},
        ],
        "teach": [
            "hosts: <group> must name a group the INVENTORY really has — a typo matches zero hosts.",
            "--tags runs a subset; untagged tasks aren't skipped, they're never considered.",
            "register captures a task's rc/stdout; when: turns it into a decision. Guard shell with it.",
            "The SAME template rendered differently per host — that's Jinja2 + facts, and it's the "
            "whole reason config management scales.",
            "A role is just a folder layout Ansible loads by convention — tasks/, handlers/, "
            "templates/ — which is what makes it shareable.",
            "Second run: ok everywhere and no handler. Nothing changed, so nothing restarted.",
        ],
        "solution": [
            "cat hosts",
            "ansible-playbook -i hosts vars.yml",
            "edit hosts",
            "[servers]", "node1", "node2", "",
            "[servers:vars]", "ansible_user=root", ".",
            "ansible-playbook -i hosts vars.yml",
            "ansible-playbook -i hosts vars.yml --tags=install",
            "edit vars.yml",
            "---",
            "- name: vars, tags and conditionals",
            "  hosts: servers",
            "  become: true",
            "  vars:",
            "    package: vim",
            "  tasks:",
            "    - name: is nginx installed?",
            "      command: dpkg -s nginx",
            "      register: nginx_check",
            "      ignore_errors: true",
            "",
            "    - name: install nginx only if it is missing",
            "      apt:",
            "        name: nginx",
            "        state: present",
            "      when: nginx_check.rc != 0",
            ".",
            "ansible-playbook -i hosts vars.yml",
            "cat hostname.conf.j2",
            "edit hostname.conf.j2",
            "# managed by Ansible - do not edit by hand",
            "server_name = {{ ansible_hostname }}", ".",
            "ansible-playbook -i hosts template.yml",
            "ssh node1 cat /root/hostname.conf",
            "ssh node2 cat /root/hostname.conf",
            "mkdir -p roles/webserver/tasks",
            "edit roles/webserver/tasks/main.yml",
            "---",
            "- name: install nginx",
            "  apt:",
            "    name: nginx",
            "    state: present",
            "",
            "- name: deploy the homepage from a template",
            "  template:",
            "    src: index.html.j2",
            "    dest: /var/www/html/index.html",
            "  notify: restart nginx",
            "",
            "- name: nginx is running",
            "  service:",
            "    name: nginx",
            "    state: started",
            ".",
            "edit roles/webserver/handlers/main.yml",
            "---",
            "- name: restart nginx",
            "  service:",
            "    name: nginx",
            "    state: restarted",
            ".",
            "edit roles/webserver/templates/index.html.j2",
            "<h1>Hello from {{ ansible_hostname }}</h1>", ".",
            "ansible-playbook -i hosts site.yml",
            "curl node1",
            "curl node2",
            "ansible-playbook -i hosts site.yml",
        ],
    },
]
