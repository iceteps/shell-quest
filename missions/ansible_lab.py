"""Class 14 — the dockerized Ansible lab: a mini-datacenter made of containers.

Class 11's mission taught Ansible against an abstract fleet. This one teaches
the LAB the course actually ships: the control node is a container, the
"servers" are containers, and the reason `ansible all -m ping` works with no
password is a shell script — `entrypoint.sh` — that copied a public key into
each node before the player ever typed anything.

Every trap in the class notes is a real trap here, because every one of them is
a consequence of world state rather than a printed warning:

  * the HOST has no ansible — Ansible, the inventory and the private key live
    inside `ansible-control`, so everything runs after `docker compose exec`;
  * `docker compose up -d` returns when the containers START, not when the keys
    have landed, and the only place "ready" is written is the control node's log;
  * a node lives in THREE files — the compose file, `inventory.ini` and the
    entrypoint's `for host in …` loop — and skipping any one of them fails in its
    own distinct way: no DNS, no host matched, or Permission denied (publickey).

What it borrows and what it owns: the YAML reader, the Jinja renderer and the
play/task printing come from `ansible_ops` — same topic, same parser, no reason
for two. The FLEET is this module's own, because in this lab the fleet is
defined by files the player edits: add node3 to the compose file and there IS a
node3, with its own published port.

Deliberate limits, said out loud instead of faked:
  * modules simulated: apt · copy · template · service (systemd is an alias) ·
    file · lineinfile · user · shell · command · debug · ping. Anything else
    says so instead of pretending to have worked.
  * a node's "shell" answers hostname · whoami · id · nginx -v · systemctl
    status · cat · ls · curl · echo, chained with `&&` — everything else gets a
    real `not found` and rc=127.
  * Semaphore is a WEB UI. `curl` shows its login page; this terminal will not
    pretend to click its buttons.
"""
import copy
import re

from engine import c, _rand_id
# The other Ansible mission already parses YAML, renders Jinja and prints plays.
# Importing its guts is what keeps the two missions behaving identically for a
# student who plays them back to back.
from missions.ansible_ops import (MODULES, _ansible_doc, _bar, _json, _lookup,
                                  _MISSING, _mod_args, _module_of, _notify_list,
                                  _render, _render_deep, _resolve, _scalar,
                                  _status_line, _strip_comment, _Undefined,
                                  _when, _yaml, _YamlError)

COMPOSE = "docker-compose.yml"
INVENTORY = "inventory.ini"
ANSIBLE_CFG = "ansible.cfg"
ENTRYPOINT = "entrypoint.sh"
CONTROL = "ansible-control"
# The two services in this stack that are not managed nodes: the desk, and the
# web UI bolted onto it.
NOT_A_NODE = (CONTROL, "semaphore")

# How many commands pass before the entrypoint's key loop finishes. It is not a
# stopwatch — it is "long enough that the player who exec's in and pings
# immediately meets the failure the class notes warn about", and short enough
# that reading the log twice fixes it.
READY_TICKS = 3


# ------------------------------------------------------------ the lab files --
COMPOSE_YML = '''name: lab

services:
  node1:
    build: ./node
    container_name: node1
    hostname: node1
    ports:
      - "8081:80"
    networks:
      - ansible-net

  node2:
    build: ./node
    container_name: node2
    hostname: node2
    ports:
      - "8082:80"
    networks:
      - ansible-net

  ansible-control:
    build: ./control
    container_name: ansible-control
    depends_on:
      - node1
      - node2
    volumes:
      - .:/ansible
      - ssh_keys:/root/.ssh
    networks:
      - ansible-net

  semaphore:
    image: semaphoreui/semaphore:latest
    container_name: semaphore
    ports:
      - "3000:3000"
    volumes:
      - ssh_keys:/home/semaphore/.ssh:ro
    networks:
      - ansible-net

volumes:
  ssh_keys:

networks:
  ansible-net:
    driver: bridge
'''

ENTRYPOINT_SH = '''#!/bin/bash
set -e

# Generate the keypair ONCE - if the volume already has one, keep it.
if [ ! -f /root/.ssh/id_rsa ]; then
  ssh-keygen -t rsa -N "" -f /root/.ssh/id_rsa
fi

echo "Waiting for nodes to be ready and distributing SSH keys..."
for host in node1 node2; do
  until sshpass -p ubuntu ssh-copy-id -o StrictHostKeyChecking=no ubuntu@$host; do
    echo "  $host not ready yet - retrying in 2s"
    sleep 2
  done
  echo "SSH key distributed to $host"
done

echo "Ansible control node is ready."
tail -f /dev/null
'''

INVENTORY_INI = '''[web]
node1
node2

[web:vars]
ansible_user=ubuntu
ansible_ssh_private_key_file=/root/.ssh/id_rsa
'''

ANSIBLE_CFG_INI = '''[defaults]
inventory = inventory.ini
host_key_checking = False
retry_files_enabled = False
'''

INSTALL_NGINX = '''---
- name: install nginx
  hosts: web
  become: true
  tasks:
    - name: nginx is present
      apt:
        name: nginx
        state: present
        update_cache: true
'''

DEPLOY_WEBSITE = '''---
- name: deploy the website
  hosts: web
  become: true
  tasks:
    - name: nginx is present
      apt:
        name: nginx
        state: present

    - name: publish the homepage
      copy:
        content: "<h1>Hello from Ansible</h1>"
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

# Ships with no {{ }} in it on purpose: the boss objective is to notice that a
# template without variables is a copy with extra steps.
INDEX_J2 = '''<h1>Hello from Ansible</h1>
'''

# The class's second template: it takes a variable the playbook has to SUPPLY
# (`vars: { nginx_port: 80 }`), so rendering it without one fails the way an
# undefined Jinja variable really does.
NGINX_CONF_J2 = '''server {
    listen {{ nginx_port }} default_server;
    server_name _;

    location / {
        add_header Content-Type text/plain;
        return 200 'Hello from Ansible\\n';
    }
}
'''

NGINX_DEFAULT_PAGE = '''<!DOCTYPE html>
<html>
<head><title>Welcome to nginx!</title></head>
<body>
<h1>Welcome to nginx!</h1>
<p>If you see this page, the nginx web server is successfully installed and
working. Further configuration is required.</p>
</body>
</html>'''

SEMAPHORE_PAGE = ('<!DOCTYPE html><html><head><title>Semaphore</title></head>'
                  '<body><div id="app">Sign in — admin / admin123</div></body></html>')


# ------------------------------------------------- reading the player's files --
def _compose(world):
    """Parse the compose file into {service: {ports, image, depends_on}}.

    Hand-rolled and indentation-driven rather than fed through the YAML reader,
    because this file is edited BY THE PLAYER mid-mission: it has to survive a
    half-finished node3 block without taking the mission down with it.
    """
    body = world.files.get(COMPOSE) or world.files.get("docker-compose.yaml") or ""
    services, cur, section, in_services = {}, None, None, False
    for raw in body.splitlines():
        line = _strip_comment(raw).rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        text = line.strip()
        if indent == 0:
            in_services, cur, section = text.startswith("services:"), None, None
            continue
        if not in_services:
            continue
        if indent == 2 and text.endswith(":"):
            cur = text[:-1].strip()
            services[cur] = {"ports": [], "image": None, "depends_on": []}
            section = None
            continue
        if cur is None:
            continue
        if indent == 4:
            key, _sep, val = text.partition(":")
            key, val = key.strip(), val.strip()
            section = key if key in ("ports", "depends_on") and not val else None
            if key == "image" and val:
                services[cur]["image"] = val
            continue
        if section and text.startswith("- "):
            item = str(_scalar(text[2:].strip()))
            if section == "depends_on":
                services[cur]["depends_on"].append(item)
            else:
                hit = re.fullmatch(r"(?:[\d.]+:)?(\d+):(\d+)(?:/\w+)?", item)
                if hit:
                    services[cur]["ports"].append((int(hit.group(1)), int(hit.group(2))))
    return services


def _start_order(services):
    """depends_on, honoured — compose really does start node1/node2 first."""
    left, out = list(services), []
    while left:
        ready = [n for n in left if all(d in out or d not in services
                                        for d in services[n]["depends_on"])]
        ready = ready or left                   # a dependency cycle still starts
        out += ready
        left = [n for n in left if n not in ready]
    return out


def _entrypoint_nodes(world):
    """The hosts entrypoint.sh loops over — the third place a node has to exist."""
    hit = re.search(r"for\s+\w+\s+in\s+([^\n;]+?)\s*;?\s*do", world.files.get(ENTRYPOINT, ""))
    return hit.group(1).split() if hit else []


def _parse_inventory(text):
    """(groups, group_vars) from an ini inventory — [g] hosts and [g:vars]."""
    groups, gvars, hosts, vars_ = {}, {}, None, None
    for raw in (text or "").splitlines():
        line = _strip_comment(raw).strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            name = line[1:-1]
            if name.endswith(":vars"):
                hosts, vars_ = None, gvars.setdefault(name[:-5], {})
            else:
                hosts, vars_ = groups.setdefault(name.split(":")[0], []), None
            continue
        if vars_ is not None and "=" in line:
            key, _sep, val = line.partition("=")
            vars_[key.strip()] = val.strip()
        elif hosts is not None:
            host = line.split()[0]
            if host not in hosts:
                hosts.append(host)
    return groups, gvars


def _inventory_source(world, line):
    """(text, name) — an explicit -i wins, otherwise ansible.cfg's `inventory =`.

    Returning the NAME even when the file is missing is what lets the caller say
    'unable to parse inventory.ini' instead of the much less useful 'no hosts'.
    """
    toks = line.split()
    for flag in ("-i", "--inventory", "--inventory-file"):
        if flag in toks and toks.index(flag) + 1 < len(toks):
            name = toks[toks.index(flag) + 1]
            return world.files.get(name), name
    hit = re.search(r"(?m)^\s*inventory\s*=\s*(\S+)", world.files.get(ANSIBLE_CFG, ""))
    if hit:
        return world.files.get(hit.group(1)), hit.group(1)
    return None, None


def _inventory(world, io, line):
    """(groups, group_vars) or (None, None) after saying why, Ansible's way."""
    text, name = _inventory_source(world, line)
    if text is None:
        if name is None:
            io.print(c("[WARNING]: No inventory was parsed, only implicit localhost is "
                       "available", "yellow"))
            io.print(c("(nothing told Ansible where the inventory is — pass -i inventory.ini, or "
                       "put `inventory = inventory.ini` in ansible.cfg. That one line is why the "
                       "class's commands need no -i at all.)", "dim"))
        else:
            io.print(c(f"[WARNING]: Unable to parse {name} as an inventory source", "yellow"))
            io.print(c(f"(`ls` — there is no {name} in /ansible to read)", "dim"))
        return None, None
    return _parse_inventory(text)


def _managed(world):
    """Every host the CURRENT inventory points at — the fleet is whatever the
    player's own files say it is, which is the whole point of objective 8."""
    text, _name = _inventory_source(world, "")
    groups, _vars = _parse_inventory(text or "")
    return _resolve("all", groups)


# ------------------------------------------------------------- the lab world --
def _lab(world):
    """Per-node runtime state. Created by `docker compose up`, never by the
    mission spec: in this lab a node exists only because the compose file does."""
    return world.flags.setdefault("lab_nodes", {})


def _fresh_node():
    return {"pkgs": set(), "files": {}, "svcs": {}, "users": set(),
            "keyed": False, "running": True}


def _in_control(world):
    return bool(world.flags.get("in_control"))


def _tick(world):
    """One command's worth of time. The entrypoint is a background script — it
    makes progress while you type, not while you wait for it."""
    if not world.flags.get("lab_up"):
        return
    world.flags["lab_clock"] = world.flags.get("lab_clock", 0) + 1
    if world.flags["lab_clock"] >= READY_TICKS:
        _entrypoint_run(world)


def _log(world):
    return world.flags.setdefault("lab_log", [])


def _entrypoint_start(world):
    """The part of entrypoint.sh that is instant: keygen, then the wait message.

    Split from the loop below so the log reads in the order it really happens —
    the key exists long before any node has been reached.
    """
    world.flags["lab_entrypoint_done"] = False
    world.flags["lab_clock"] = 0
    log = _log(world)
    if not world.flags.get("lab_key"):
        world.flags["lab_key"] = True
        log.append("Generating public/private rsa key pair.")
        log.append("Your identification has been saved in /root/.ssh/id_rsa")
    else:
        # The keypair lives in the named ssh_keys volume, so a restart finds it
        # and the `if [ ! -f … ]` guard skips keygen — idempotence, in bash.
        log.append("/root/.ssh/id_rsa already exists - keeping it")
    log.append("Waiting for nodes to be ready and distributing SSH keys...")


def _entrypoint_run(world):
    """entrypoint.sh's key loop, run for real against the containers that exist.

    A host in the loop with no container is not an error here any more than it is
    there: `until ssh-copy-id …; do sleep 2; done` simply never returns, so the
    hosts after it never get a key and 'ready' is never printed. That is exactly
    the failure a student sees, so it is exactly what this prints.
    """
    if world.flags.get("lab_entrypoint_done"):
        return
    log, nodes = _log(world), _lab(world)
    for host in _entrypoint_nodes(world):
        if host in nodes and nodes[host]["keyed"]:
            continue
        if host not in nodes:
            stuck = f"  {host} not ready yet - retrying in 2s"
            if not log or log[-1] != stuck:
                log.append(stuck)
            world.flags["lab_stuck_on"] = host
            return
        nodes[host]["keyed"] = True
        log.append(f"SSH key distributed to {host}")
    world.flags.pop("lab_stuck_on", None)
    log.append("Ansible control node is ready.")
    world.flags["lab_entrypoint_done"] = True


def _reach(world, host):
    """(ok, why) — every way a host in the inventory can still be out of reach.

    The two failures read completely differently, and telling them apart IS the
    three-places lesson: no container means no DNS; a container with no key
    means the entrypoint's loop never named it.
    """
    nodes = _lab(world)
    if host not in nodes or not nodes[host]["running"]:
        return False, f"ssh: Could not resolve hostname {host}: Name or service not known"
    if not nodes[host]["keyed"]:
        return False, f"ubuntu@{host}: Permission denied (publickey,password)."
    return True, ""


def _unreachable_hint(world, io, host):
    live = _lab(world).get(host, {}).get("running")
    if not world.flags.get("lab_entrypoint_done") and live:
        io.print(c("(the key hasn't landed yet — `docker compose logs ansible-control` and wait "
                   "for 'Ansible control node is ready.' before you ping)", "dim"))
    elif not live:
        io.print(c(f"({host} has no running container on ansible-net, so Docker's DNS can't "
                   "resolve it. An inventory hostname has to match a compose service that is "
                   "actually up.)", "dim"))
    else:
        io.print(c(f"({host} is running and resolvable but has no key: entrypoint.sh's "
                   "`for host in …` loop is the THIRD place a node has to be listed.)", "dim"))


def _facts(world, host):
    """What `gather_facts` would find. inventory_hostname is the one that makes a
    template render differently per node, so it has to be honest."""
    order = sorted(_lab(world))
    idx = order.index(host) + 1 if host in order else 9
    return {"inventory_hostname": host, "ansible_hostname": host,
            "ansible_fqdn": host, "ansible_nodename": host,
            "ansible_default_ipv4": {"address": f"172.28.0.{10 + idx}"},
            "ansible_distribution": "Ubuntu", "ansible_distribution_version": "22.04",
            "ansible_facts": {"os_family": "Debian", "distribution": "Ubuntu",
                              "hostname": host}}


def _page(st):
    """What nginx on that node would serve right now, or None if nothing would."""
    if not st or not st["running"] or st["svcs"].get("nginx") != "started":
        return None
    conf = st["files"].get("/etc/nginx/sites-available/default", "")
    hit = re.search(r"return\s+\d+\s+'([^']*)'", conf)
    if hit:                                  # a `return 200 '…'` site short-circuits
        return hit.group(1).replace("\\n", "\n").rstrip("\n")
    return (st["files"].get("/var/www/html/index.html")
            or st["files"].get("/var/www/html/index.nginx-debian.html"))


# ------------------------------------------------------------ docker compose --
def _no_stack(world, io):
    world.flags["_noop"] = True
    io.print(c("(nothing is running — `docker compose up -d` in the lab folder builds and "
               "starts the whole datacenter)", "dim"))


def _make_container(world, name, spec):
    world.containers[name] = {
        "id": _rand_id(), "image": spec["image"] or f"lab-{name}",
        "status": "running", "exit_code": 0, "network": "ansible-net",
        "ports": [f"{h}:{cp}" for h, cp in spec["ports"]], "files": {}, "logs": ""}


def _compose_up(world, m, io):
    services = _compose(world)
    if not services:
        world.flags["_noop"] = True
        io.print("no configuration file provided: not found")
        io.print(c(f"(this mission's stack is described in {COMPOSE} — `ls` and `cat` it)", "dim"))
        return
    toks = m.group(0).split()
    first = not world.flags.get("lab_up")
    nodes = _lab(world)
    # Compose counts every RESOURCE it touches, not just the containers — the
    # network and the volume are two of them on the first run and already there
    # on every run after, which is exactly why the number changes.
    io.print(f"[+] Running {len(services) + (2 if first else 0)}/"
             f"{len(services) + (2 if first else 0)}")
    if first:
        io.print(" ✔ Network lab_ansible-net  Created")
        io.print(' ✔ Volume "lab_ssh_keys"    Created')
    new_nodes = []
    for name in _start_order(services):
        if name in world.containers:
            # A stopped container is not recreated, it is started again — the
            # difference between `up` and `up --force-recreate`.
            resumed = world.containers[name]["status"] != "running"
            world.containers[name]["status"] = "running"
            if name in nodes:
                nodes[name]["running"] = True
            io.print(f" ✔ Container {name}  " + ("Started" if resumed else "Running"))
            continue
        _make_container(world, name, services[name])
        io.print(f" ✔ Container {name}  Started")
        if name not in NOT_A_NODE:
            nodes[name] = _fresh_node()
            new_nodes.append(name)
    world.flags["lab_up"] = True
    _tick(world)
    if "-d" not in toks and "--detach" not in toks:
        io.print(c("(no -d: real compose would attach to every container's log and hold this "
                   "terminal until Ctrl-C. This lab detaches anyway and says so.)", "dim"))
    if first:
        _entrypoint_start(world)
        io.print(c("(compose returned the moment the containers STARTED. The control node's "
                   "entrypoint is still generating and copying keys — `docker compose logs "
                   f"{CONTROL}` is where you watch that finish.)", "dim"))
        return
    recreate = "--build" in toks or "--force-recreate" in toks
    if recreate:
        # Replacing the control container really does re-run its entrypoint —
        # this is the advertised fix, so it has to actually work.
        _entrypoint_start(world)
        io.print(c("(--build/--force-recreate replaced the containers, so the control node's "
                   "entrypoint runs again from the top. The named ssh_keys VOLUME outlives them, "
                   "which is how it keeps the same keypair.)", "dim"))
    elif new_nodes:
        # Honest about the one thing this lab smooths over: compose would leave
        # the unchanged control container alone, entrypoint and all.
        _entrypoint_start(world)
        io.print(c(f"(new: {', '.join(new_nodes)}. Real compose would NOT recreate the unchanged "
                   f"{CONTROL}, so its entrypoint would not re-run and the new node would never "
                   f"get a key — `docker compose up -d --build --force-recreate {CONTROL}` is the "
                   "real fix. This lab re-runs the key loop for you, and says so.)", "dim"))
    else:
        world.flags["_noop"] = True
        io.print(c("(nothing to do — compose only creates what the file describes and the daemon "
                   "doesn't already have. That is `up` being idempotent.)", "dim"))


def _compose_ps(world, m, io):
    _tick(world)
    world.flags["_noop"] = True
    if not world.flags.get("lab_up") or not world.containers:
        _no_stack(world, io)
        return
    io.print(f"{'NAME':<18}{'IMAGE':<34}{'STATUS':<14}PORTS")
    listed = _start_order(_compose(world))
    # A container the compose file no longer mentions is still running — hiding
    # it would be the one lie `ps` must never tell.
    listed += [n for n in world.containers if n not in listed]
    for name in listed:
        ctr = world.containers.get(name)
        if not ctr:
            continue
        ports = ", ".join(f"0.0.0.0:{p.split(':')[0]}->{p.split(':')[1]}/tcp"
                          for p in ctr["ports"])
        status = "Up 2 minutes" if ctr["status"] == "running" else "Exited (0) 1 minute ago"
        io.print(f"{name:<18}{ctr['image']:<34}{status:<14}{ports}".rstrip())
    io.print(c("(PORTS is the only column the HOST can use: a container port with no mapping "
               "here is unreachable from your laptop, however healthy it is.)", "dim"))


def _logs(world, m, io):
    _tick(world)
    world.flags["_noop"] = True
    args = m.group(0).split()
    rest = args[args.index("logs") + 1:]
    name = next((a for a in rest if not a.startswith("-")), None)
    compose = "compose" in args[:2] or args[0] == "docker-compose"
    if not world.flags.get("lab_up"):
        _no_stack(world, io)
        return
    if name and name not in world.containers:
        io.print(f'no such service: {name}' if compose else
                 f'Error response from daemon: No such container: {name}')
        io.print(c("(`docker compose ps` lists the names this stack really has)", "dim"))
        return
    if name and name != CONTROL:
        io.print("(no output)")
        io.print(c(f"({name} runs sshd and nothing else — the interesting log in this lab is "
                   f"{CONTROL}'s, because the entrypoint writes to it)", "dim"))
        return
    text = _log(world)
    prefix = f"{CONTROL}  | " if compose else ""
    for line in text:
        io.print(prefix + line)
    if "-f" in args or "--follow" in args:
        io.print(c("(real `-f` keeps the terminal attached until Ctrl-C — this lab prints what "
                   "is in the log and hands the prompt straight back)", "dim"))
    if world.flags.get("lab_entrypoint_done"):
        world.flags["lab_ready_seen"] = True
        return
    stuck = world.flags.get("lab_stuck_on")
    if stuck:
        io.print(c(f"(the loop is STUCK on {stuck}: `until ssh-copy-id …; do sleep 2; done` never "
                   f"returns while {stuck} has no container, so no host after it gets a key and "
                   "'ready' is never printed. Fix the compose file or the loop.)", "dim"))
    else:
        io.print(c("(not ready yet — ssh-copy-id retries every 2s until each node's sshd answers. "
                   "Run it again in a moment and wait for 'Ansible control node is ready.')", "dim"))


def _lifecycle(world, m, io):
    """down · stop · start · restart — the ones that decide what survives."""
    _tick(world)
    args = m.group(0).split()
    verb = args[args.index("compose") + 1] if "compose" in args[:2] else args[1]
    if args[0] == "docker-compose":
        verb = args[1]
    names = [a for a in args[args.index(verb) + 1:] if not a.startswith("-")]
    if not world.flags.get("lab_up"):
        _no_stack(world, io)
        return
    targets = names or list(_compose(world))
    if verb == "down":
        for name in list(world.containers):
            io.print(f" ✔ Container {name}  Removed")
            del world.containers[name]
        io.print(" ✔ Network lab_ansible-net  Removed")
        world.flags.update({"lab_up": False, "lab_nodes": {}, "in_control": False,
                            "lab_entrypoint_done": False, "lab_log": [], "lab_clock": 0})
        io.print(c("(the nodes are gone and so is everything the playbooks installed on them — a "
                   "container's writable layer dies with it. The named ssh_keys VOLUME is NOT "
                   "removed, so the control node's keypair survives; the nodes' authorized_keys "
                   "do not, which is why the entrypoint copies again on the next up.)", "dim"))
        return
    nodes = _lab(world)
    for name in targets:
        if name not in world.containers:
            continue
        io.print(f" ✔ Container {name}  " + {"stop": "Stopped", "start": "Started",
                                             "restart": "Started"}[verb])
        world.containers[name]["status"] = "exited" if verb == "stop" else "running"
        if name in nodes:
            # stop/start do NOT touch the writable layer: what a playbook
            # installed is still there when the container comes back.
            nodes[name]["running"] = verb != "stop"
    if verb == "stop" and CONTROL in targets:
        world.flags["in_control"] = False
    if verb in ("restart", "start") and CONTROL in targets:
        # Restarting the control node re-runs its entrypoint — the supported way
        # to hand a key to a node that appeared after the stack came up.
        _entrypoint_start(world)
        io.print(c("(a restart runs the entrypoint again from the top — that is how a node added "
                   "later gets its key without rebuilding anything)", "dim"))


def _exec(world, m, io):
    """`docker compose exec <svc> …` — the doorway the whole lab is played through."""
    _tick(world)
    args = m.group(0).split()
    i = args.index("exec") + 1
    while i < len(args) and args[i].startswith("-"):
        i += 1
    if i >= len(args):
        world.flags["_noop"] = True
        io.print("Usage:  docker compose exec [OPTIONS] SERVICE COMMAND [ARGS...]")
        return
    svc, cmd = args[i], args[i + 1:]
    if svc not in world.containers or world.containers[svc]["status"] != "running":
        world.flags["_noop"] = True
        io.print(f'service "{svc}" is not running')
        io.print(c("(`docker compose ps` first — and `docker compose up -d` if the stack "
                   "isn't up)", "dim"))
        return
    if svc != CONTROL:
        world.flags["_noop"] = True
        io.print(c(f"(you CAN shell into {svc} in real life — but then you are configuring a box "
                   "by hand, which is the habit Ansible exists to break. Reach it the way the "
                   f"control node does:  ansible {svc} -m shell -a \"hostname\" -b)", "dim"))
        return
    if not cmd or cmd[0] in ("bash", "sh", "/bin/bash", "/bin/sh"):
        world.flags["in_control"] = True
        world.flags["entered_control"] = True
        io.print(c("(you're inside the control node. WORKDIR is /ansible and the lab folder is "
                   "bind-mounted there, so ls/cat/edit see the very same files as the host. "
                   "`exit` goes back.)", "dim"))
        return
    for pattern, fn in ((r"ansible-playbook(?:\s.*)?", _playbook),
                        (r"ansible-doc(?:\s.*)?", _doc),
                        (r"ansible(?:\s.*)?", _ansible)):
        if re.fullmatch(pattern, " ".join(cmd)):
            # One-shot exec: run it as if the prompt were inside the container,
            # then put the player back where they were standing.
            was = world.flags.get("in_control")
            world.flags["in_control"] = True
            try:
                fn(world, re.fullmatch(r"(?s).+", " ".join(cmd)), io)
            finally:
                world.flags["in_control"] = was
            return
    world.flags["_noop"] = True
    io.print(c(f"(this lab runs one-shot `exec {CONTROL} ansible…` commands; for anything else "
               f"open a shell:  docker compose exec {CONTROL} bash)", "dim"))


def _exit(world, m, io):
    """`exit` leaves the CONTAINER, not the game.

    Registered as a handler because in this mission `exit` is a real command
    with a real effect — and the mission stays escapable because `quit` is never
    intercepted, which is what the host branch below says out loud.
    """
    if _in_control(world):
        world.flags["in_control"] = False
        io.print(c("(back on the host. The containers keep running — `docker compose ps` proves "
                   "it — and the lab folder is right here.)", "dim"))
        return
    world.flags["_noop"] = True
    io.print(c("(you're already on the host — `quit` is what leaves the mission and goes back "
               "to the map)", "dim"))


# ------------------------------------------------------- the nodes' own shell --
def _one_command(world, host, st, cmd, user):
    """(rc, stdout, stderr) for one command on one node."""
    toks = cmd.split()
    if not toks:
        return 0, "", ""
    prog, args = toks[0], toks[1:]
    if prog == "hostname":
        return 0, host, ""
    if prog == "whoami":
        return 0, user, ""
    if prog == "id":
        return (0, "uid=0(root) gid=0(root) groups=0(root)" if user == "root"
                else "uid=1000(ubuntu) gid=1000(ubuntu) groups=1000(ubuntu),27(sudo)", "")
    if prog == "nginx":
        if "nginx" not in st["pkgs"]:
            return 127, "", f"/bin/sh: 1: {prog}: not found"
        # `nginx -v` really does write its version to STDERR — worth meeting once.
        return 0, "", "nginx version: nginx/1.18.0 (Ubuntu)"
    if prog in ("systemctl", "service"):
        state = st["svcs"].get("nginx")
        if "nginx" not in st["pkgs"]:
            return 4, "", "Unit nginx.service could not be found."
        return (0, f"● nginx.service - A high performance web server\n"
                   f"     Active: {'active (running)' if state == 'started' else 'inactive (dead)'}", "")
    if prog == "cat" and args:
        path = args[-1]
        if path in st["files"]:
            return 0, st["files"][path], ""
        return 1, "", f"cat: {path}: No such file or directory"
    if prog == "ls":
        under = (args[-1] if args and not args[-1].startswith("-") else "/").rstrip("/") + "/"
        hits = sorted({f[len(under):].split("/")[0] for f in st["files"] if f.startswith(under)})
        return (0, "\n".join(hits), "") if hits else (
            2, "", f"ls: cannot access '{under}': No such file or directory")
    if prog == "curl":
        page = _page(st)
        if page is None:
            return 7, "", "curl: (7) Failed to connect to localhost port 80: Connection refused"
        return 0, page, ""
    if prog == "echo":
        return 0, cmd[5:].strip(), ""
    if prog in ("apt", "apt-get") and user != "root":
        return 100, "", "E: Could not open lock file /var/lib/dpkg/lock-frontend (13: Permission denied)"
    return 127, "", f"/bin/sh: 1: {prog}: not found"


def _node_shell(world, host, st, cmd, user):
    """`hostname && nginx -v` — && chaining, because that is what the drill types."""
    outs, errs, rc = [], [], 0
    for part in re.split(r"\s*&&\s*", str(cmd).strip()):
        rc, out, err = _one_command(world, host, st, part, user)
        outs += [out] if out else []
        errs += [err] if err else []
        if rc:
            break
    return rc, "\n".join(outs), "\n".join(errs)


# ---------------------------------------------------------------- the modules --
def _denied(mod, args, become):
    """What `become: true` is FOR. The inventory logs in as `ubuntu`, so every
    one of these is a genuine permission failure without it."""
    if become:
        return None
    if mod == "apt":
        return ("'/usr/bin/apt-get install nginx' failed: E: Could not open lock file "
                "/var/lib/dpkg/lock-frontend - open (13: Permission denied)")
    if mod in ("service", "systemd"):
        name = args.get("name", "the service")
        return f"Unable to start service {name}: Failed to start {name}.service: Access denied"
    if mod in ("copy", "template", "file", "lineinfile"):
        dest = str(args.get("dest") or args.get("path") or "")
        if dest and not dest.startswith(("/tmp", "/home/ubuntu", "/var/tmp")):
            folder = dest.rsplit("/", 1)[0] or "/"
            return f"Destination {folder} not writable"
    if mod == "user":
        return "useradd: Permission denied."
    return None


def _find_file(world, name):
    """src lookup: as typed, then templates/, then files/ — Ansible's own order,
    which is why a playbook says `src: index.html.j2` and not a path."""
    for key in (name, f"templates/{name}", f"files/{name}", str(name).split("/")[-1],
                f"templates/{str(name).split('/')[-1]}"):
        if key in world.files:
            return world.files[key]
    return None


def _run_module(world, host, st, mod, args, check, become):
    """One module on one node -> (status, result). status: ok | changed | failed."""
    def res(changed, **extra):
        out = {"changed": changed, "failed": False}
        out.update(extra)
        return ("changed" if changed else "ok"), out

    why = _denied(mod, args, become)
    if why:
        return "failed", {"failed": True, "changed": False, "msg": why}

    if mod == "ping":
        return res(False, ping="pong")

    if mod == "apt":
        names = args.get("name") or args.get("pkg") or args.get("package")
        if names is None:
            return "failed", {"failed": True, "changed": False,
                              "msg": "one of the following is required: name, deb, package, pkg"}
        names = names if isinstance(names, list) else [n.strip() for n in str(names).split(",")]
        state, changed = args.get("state", "present"), False
        for pkg in names:
            if state == "absent":
                if pkg in st["pkgs"]:
                    changed = True
                    if not check:
                        st["pkgs"].discard(pkg)
                        if pkg == "nginx":
                            st["svcs"].pop("nginx", None)
            elif pkg not in st["pkgs"]:
                changed = True
                if not check:
                    st["pkgs"].add(pkg)
                    if pkg == "nginx":
                        # Ubuntu's nginx package starts the service and drops its
                        # own placeholder page — which is why `curl` answers
                        # BEFORE any playbook of yours has deployed anything.
                        st["svcs"]["nginx"] = "started"
                        st["files"]["/var/www/html/index.nginx-debian.html"] = NGINX_DEFAULT_PAGE
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
            body = _find_file(world, src)
            if body is None:
                where = "Ansible Controller" if mod == "copy" else "lookup paths"
                return "failed", {"failed": True, "changed": False,
                                  "msg": f"Could not find or access '{src}' on the {where}"}
            if mod == "template":
                body = _render(body, args.get("_vars", {}))
                world.flags.setdefault("_tpl_dests", set()).add(dest)
        if st["files"].get(dest) == body:
            return res(False, dest=dest)
        if not check:
            st["files"][dest] = body
        return res(True, dest=dest)

    if mod in ("service", "systemd"):
        name = args.get("name")
        if not name:
            return "failed", {"failed": True, "changed": False,
                              "msg": "missing required arguments: name"}
        if name not in st["pkgs"]:
            return "failed", {"failed": True, "changed": False,
                              "msg": f"Could not find the requested service {name}: host"}
        want, now = args.get("state", "started"), st["svcs"].get(name)
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

    if mod == "file":
        path = args.get("path") or args.get("dest")
        if args.get("state") == "absent":
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

    if mod == "user":
        name = args.get("name")
        if args.get("state") == "absent":
            had = name in st["users"]
            if not check:
                st["users"].discard(name)
            return res(had, name=name)
        if name in st["users"]:
            return res(False, name=name)
        if not check:
            st["users"].add(name)
        return res(True, name=name)

    if mod in ("command", "shell"):
        cmd = args.get("_free_form") or args.get("cmd") or args.get("argv") or ""
        user = "root" if become else args.get("_user", "ubuntu")
        rc, out, err = _node_shell(world, host, st, cmd, user)
        return ("failed" if rc else "changed"), {
            "changed": rc == 0, "failed": rc != 0, "rc": rc, "cmd": str(cmd),
            "stdout": out, "stderr": err, "msg": "non-zero return code" if rc else ""}

    return "unsupported", {}


def _unsupported(io, mod):
    io.print(c(f"the '{mod}' module is real Ansible, but this lab doesn't simulate it.", "yellow"))
    io.print(c(f"(simulated here: {' · '.join(MODULES)} — on the control node `ansible-doc {mod}` "
               "documents the one you wanted)", "dim"))


# -------------------------------------------------------------- the ad-hoc CLI --
def _no_ansible_here(world, io, prog):
    """The class's number-one trap, in bash's own words."""
    world.flags["_noop"] = True
    world.flags["host_no_ansible"] = True
    _tick(world)
    io.print(f"bash: {prog}: command not found")
    if not world.flags.get("lab_up"):
        io.print(c("(and nothing is running yet — `docker compose up -d` builds the lab first)",
                   "dim"))
    io.print(c("(this is the HOST. Ansible, the inventory and the private SSH key all live inside "
               f"the CONTROL NODE — it is the only machine in this lab wired to reach the nodes. "
               f"Get in:  docker compose exec {CONTROL} bash)", "dim"))


def _adhoc_args(line, toks):
    """Parse an ad-hoc command line -> the handful of switches this lab uses."""
    opts = {"mod": None, "args": "", "pattern": None, "become": False,
            "oneline": False, "list_hosts": False, "user": None}
    i = 1
    while i < len(toks):
        tok = toks[i]
        if tok in ("-m", "--module-name") and i + 1 < len(toks):
            opts["mod"], i = toks[i + 1], i + 2
        elif tok in ("-a", "--args") and i + 1 < len(toks):
            opts["args"], i = toks[i + 1], i + 2
        elif tok in ("-u", "--user") and i + 1 < len(toks):
            opts["user"], i = toks[i + 1], i + 2
        elif tok in ("-i", "--inventory", "--inventory-file", "-l", "--limit",
                     "-e", "--extra-vars") and i + 1 < len(toks):
            i += 2
        elif tok in ("-b", "--become"):
            opts["become"], i = True, i + 1
        elif tok in ("-o", "--one-line"):
            opts["oneline"], i = True, i + 1
        elif tok == "--list-hosts":
            opts["list_hosts"], i = True, i + 1
        elif tok.startswith("-"):
            i += 1
        else:
            opts["pattern"], i = opts["pattern"] or tok, i + 1
    # shlex has already eaten the quotes by the time a handler sees the line, so
    # a multi-word -a is recovered from the raw text.
    quoted = re.search(r"(?:-a|--args)\s+(\"[^\"]*\"|'[^']*'|\S+)", line)
    if quoted:
        opts["args"] = _scalar(quoted.group(1))
    return opts


def _ansible(world, m, io):
    line = m.group(0).strip()
    if not _in_control(world):
        _no_ansible_here(world, io, "ansible")
        return
    _tick(world)
    toks = line.split()
    if "--version" in toks:
        from engine import TOOL_VERSION_LINES
        io.print(TOOL_VERSION_LINES["ansible"])
        world.flags["_noop"] = True
        return
    if len(toks) == 1 or toks[1] in ("-h", "--help"):
        io.print("Usage: ansible <host-pattern> [-m MODULE] [-a ARGS] [-b] [-o] [--list-hosts]")
        io.print(c("(start with:  ansible all -m ping)", "dim"))
        world.flags["_noop"] = True
        return

    opts = _adhoc_args(line, toks)
    groups, gvars = _inventory(world, io, line)
    if groups is None:
        world.flags["_noop"] = True
        return
    hosts = _resolve(opts["pattern"] or "all", groups)
    if not hosts:
        io.print(c(f"[WARNING]: Could not match supplied host pattern, ignoring: "
                   f"{opts['pattern']}", "yellow"))
        io.print(c("[WARNING]: No hosts matched, nothing to do", "yellow"))
        io.print(c("(the pattern has to name a group in [brackets] or a host the inventory "
                   f"really lists — `cat {INVENTORY}` and compare letter by letter)", "dim"))
        world.flags["_noop"] = True
        return

    if opts["list_hosts"]:
        io.print(f"  hosts ({len(hosts)}):")
        for host in hosts:
            io.print(f"    {host}")
        io.print(c("(--list-hosts resolves the pattern and contacts NOTHING — the fastest way to "
                   "find out whether you just aimed at `web`, at `all`, or at a typo)", "dim"))
        world.flags["listed_hosts"] = True
        world.flags["_noop"] = True
        return

    default_mod = opts["mod"] is None
    mod = opts["mod"] or "command"
    if mod not in MODULES:
        _unsupported(io, mod)
        world.flags["_noop"] = True
        return

    user = opts["user"] or _group_var(groups, gvars, hosts[0], "ansible_user", "ubuntu")
    ponged, changed_any, failed_any = [], False, False
    for host in hosts:
        ok, why = _reach(world, host)
        if not ok:
            io.print(c(f"{host} | UNREACHABLE! => {{\"changed\": false, \"msg\": \"Failed to "
                       f"connect to the host via ssh: {why}\", \"unreachable\": true}}", "red"))
            _unreachable_hint(world, io, host)
            failed_any = True
            continue
        st = _lab(world)[host]
        args = _mod_args({mod: opts["args"] or None}, mod)
        args["_vars"] = dict(_facts(world, host))
        args["_user"] = user
        status, result = _run_module(world, host, st, mod, args, False, opts["become"])
        if status == "unsupported":
            _unsupported(io, mod)
            world.flags["_noop"] = True
            return
        if mod in ("command", "shell"):
            rc = result.get("rc", 0)
            io.print(c(f"{host} | CHANGED | rc=0 >>", "yellow") if rc == 0
                     else c(f"{host} | FAILED | rc={rc} >>", "red"))
            body = "\n".join(p for p in (result.get("stdout"), result.get("stderr")) if p)
            io.print(body + ("" if rc == 0 else " non-zero return code"))
            changed_any = changed_any or rc == 0
            failed_any = failed_any or rc != 0
            continue
        if status == "failed":
            io.print(c(f"{host} | FAILED! => ", "red")
                     + '{\n    "changed": false,\n    "msg": "%s"\n}' % result.get("msg", ""))
            failed_any = True
            continue
        body = ", ".join(f'"{k}": {_json(v)}' for k, v in sorted(result.items())
                         if k != "failed")
        head = c(f"{host} | CHANGED => ", "yellow") if status == "changed" else \
            c(f"{host} | SUCCESS => ", "green")
        if opts["oneline"]:
            io.print(head + "{" + body + "}")
        else:
            io.print(head + "{\n    " + body.replace(", \"", ",\n    \"") + "\n}")
        changed_any = changed_any or status == "changed"
        if mod == "ping":
            ponged.append(host)

    if mod == "ping":
        world.flags["_noop"] = True
        if len(ponged) == len(hosts) and len(hosts) >= 2:
            world.flags["pinged_all"] = True
            if not opts["oneline"]:
                io.print(c("(no password anywhere: entrypoint.sh's ssh-copy-id put the control "
                           "node's PUBLIC key on each node, and the inventory points Ansible at "
                           "the matching private one. `pong` = SSH + Python both work.)", "dim"))
    if mod in ("command", "shell"):
        if opts["become"] and changed_any:
            world.flags["adhoc_root"] = True
        if not opts["become"] and changed_any:
            io.print(c(f"(that ran as `{user}` — the inventory's ansible_user. Add -b and the same "
                       "command runs through sudo as root.)", "dim"))
        if changed_any:
            io.print(c("(command/shell report CHANGED every single time — they cannot tell "
                       "whether the work was already done. That is why real modules exist.)",
                       "dim"))
    if default_mod:
        io.print(c("(no -m, so Ansible used its default module: command. -m picks the MODULE, "
                   "-a passes its ARGUMENTS.)", "dim"))
    if failed_any and not changed_any:
        world.flags["_noop"] = True


def _group_var(groups, gvars, host, key, default):
    for name, hosts in groups.items():
        if host in hosts and key in gvars.get(name, {}):
            return gvars[name][key]
    return gvars.get("all", {}).get(key, default)


def _doc(world, m, io):
    """ansible-doc lives on the control node too — and it is the same offline
    manual the other Ansible mission ships, so it is literally the same code."""
    if not _in_control(world):
        _no_ansible_here(world, io, "ansible-doc")
        return
    _tick(world)
    _ansible_doc(world, m, io)


# ------------------------------------------------------------- the playbooks --
def _recap(io, host, t):
    io.print(f"{host:<20}: " + c(f"ok={t['ok']}", "green") + "    "
             + (c(f"changed={t['changed']}", "yellow") if t["changed"] else "changed=0")
             + "    " + (c(f"unreachable={t['unreachable']}", "red")
                         if t["unreachable"] else "unreachable=0")
             + "    " + (c(f"failed={t['failed']}", "red") if t["failed"] else "failed=0")
             + f"    skipped={t['skipped']}    rescued=0    ignored=0")


def _play_vars(world, play, host, gvars, groups):
    vars_ = dict(_facts(world, host))
    for name, hosts in groups.items():
        if host in hosts:
            vars_.update(gvars.get(name, {}))
    vars_.update(play.get("vars") or {})
    return vars_


def _run_task(world, io, task, hosts, play, opts, tally, registered, notified, dead):
    """One task across the play's hosts. Returns False if the module is unknown."""
    mod = _module_of(task)
    name = task.get("name") or (mod or "task")
    if not opts.get("quiet_banner"):
        _bar(io, f"TASK [{name}]")
    if mod is None:
        io.print(c("ERROR! no module/action detected in task.", "red"))
        io.print(c("(every task calls exactly ONE module — `name:` alone is a label, not work)",
                   "dim"))
        return False
    if mod not in MODULES:
        _unsupported(io, mod)
        return False
    become = task.get("become", play.get("become")) is True
    hinted = False                       # say the diagnosis once, not once per host
    for host in hosts:
        if host in dead:
            continue
        st = _lab(world)[host]
        vars_ = _play_vars(world, play, host, opts["gvars"], opts["groups"])
        vars_.update(registered[host])
        try:
            if task.get("when") is not None:
                if not _when(_render_deep(task["when"], vars_) if "{{" in str(task["when"])
                             else task["when"], vars_):
                    _status_line(io, "skipping", host)
                    tally[host]["skipped"] += 1
                    continue
            items = task.get("loop", task.get("with_items"))
            if items is not None and "{{" in str(items):
                items = _lookup(str(items).strip("{} "), vars_)
                if items is _MISSING or not isinstance(items, list):
                    raise _Undefined(str(task.get("loop", task.get("with_items"))))
            statuses, result = [], {}
            for item in (items if isinstance(items, list) else [None]):
                if item is not None:
                    vars_["item"] = item
                args = _render_deep(_mod_args(task, mod), vars_)
                args["_vars"], args["_user"] = vars_, opts["user"]
                if mod == "debug":
                    msg = args.get("msg")
                    if "var" in args:
                        val = _lookup(str(args["var"]), vars_)
                        msg = "VARIABLE IS NOT DEFINED!" if val is _MISSING else val
                    _status_line(io, "ok", host, item,
                                 ' => {\n    "msg": "%s"\n}' % (msg if msg is not None
                                                                else "Hello world!"))
                    statuses, result = statuses + ["ok"], {"changed": False, "failed": False}
                    continue
                status, result = _run_module(world, host, st, mod, args, opts["check"], become)
                if status == "unsupported":
                    _unsupported(io, mod)
                    return False
                statuses.append(status)
                if status == "failed":
                    io.print(c(f"fatal: [{host}]: FAILED! => {{\"changed\": false, "
                               f"\"msg\": \"{result.get('msg', 'failed')}\"}}", "red"))
                    if not become and not hinted and _denied(mod, args, False):
                        hinted = True
                        io.print(c("(the inventory logs in as ubuntu — `become: true` on the play "
                                   "(or -b ad-hoc) is what runs the task through sudo)", "dim"))
                    break
                _status_line(io, status, host, item)
        except _Undefined as exc:
            io.print(c(f"fatal: [{host}]: FAILED! => {{\"msg\": \"The task includes an option "
                       f"with an undefined variable. The error was: '{exc}' is undefined\"}}",
                       "red"))
            tally[host]["failed"] += 1
            dead.add(host)
            continue
        if task.get("register"):
            registered[host][str(task["register"])] = result
        worst = ("failed" if "failed" in statuses
                 else "changed" if "changed" in statuses else "ok")
        if worst == "failed":
            if not task.get("ignore_errors"):
                tally[host]["failed"] += 1
                dead.add(host)
                continue
            io.print(c("...ignoring", "magenta"))
        tally[host]["ok"] += 1
        if worst == "changed":
            tally[host]["changed"] += 1
            for hname in _notify_list(task):
                if hname not in notified[host]:
                    notified[host].append(hname)
    return True


def _playbook(world, m, io):
    line = m.group(0).strip()
    if not _in_control(world):
        _no_ansible_here(world, io, "ansible-playbook")
        return
    _tick(world)
    toks = line.split()
    book = next((t for t in toks[1:] if t.endswith((".yml", ".yaml"))), None)
    if book is None:
        from engine import TOOL_VERSION_LINES
        if "--version" in toks:
            io.print(TOOL_VERSION_LINES["ansible-playbook"])
        else:
            io.print("Usage: ansible-playbook [--check] [--tags TAGS] playbook.yml")
            io.print(c("(the playbook file is the argument — `ls playbooks` shows which ones "
                       "this lab ships)", "dim"))
        world.flags["_noop"] = True
        return
    if book not in world.files:
        alt = next((f for f in sorted(world.files) if f.endswith("/" + book)), None)
        io.print(c(f"ERROR! the playbook: {book} could not be found", "red"))
        io.print(c(f"(it is one folder down — try: ansible-playbook {alt})" if alt else
                   "(`ls` shows what is really here — paths are relative to /ansible)", "dim"))
        world.flags["_noop"] = True
        return
    try:
        plays = _yaml(world.files[book])
    except _YamlError as exc:
        io.print(c(f"ERROR! Syntax Error while loading YAML.\n  {exc}", "red"))
        io.print(c("(YAML is spaces-only and indentation IS the structure — a tab is a syntax "
                   "error)", "dim"))
        world.flags["_noop"] = True
        return
    if isinstance(plays, dict):
        plays = [plays]
    if not isinstance(plays, list) or not all(isinstance(p, dict) for p in plays):
        io.print(c("ERROR! A playbook must be a list of plays (it starts with '- hosts: …')", "red"))
        world.flags["_noop"] = True
        return

    groups, gvars = _inventory(world, io, line)
    if groups is None:
        world.flags["_noop"] = True
        return
    check = "--check" in toks or "-C" in toks
    snapshot = copy.deepcopy(_lab(world)) if check else None
    world.flags["_tpl_dests"] = set()
    runs = world.flags.setdefault("lab_play_runs", {})
    before = runs.get(book, 0)
    total_changed, total_failed, handler_ran, ran_anything = 0, 0, False, False

    for play in plays:
        hosts = _resolve(play.get("hosts", "all"), groups)
        _bar(io, f"PLAY [{play.get('name', play.get('hosts', 'all'))}]")
        if not hosts:
            io.print(c(f"[WARNING]: Could not match supplied host pattern, ignoring: "
                       f"{play.get('hosts')}", "yellow"))
            io.print("skipping: no hosts matched")
            io.print(c(f"(`hosts:` has to name a group {INVENTORY} really has)", "dim"))
            continue
        tally = {h: {"ok": 0, "changed": 0, "skipped": 0, "failed": 0, "unreachable": 0}
                 for h in hosts}
        registered, notified, dead = {h: {} for h in hosts}, {h: [] for h in hosts}, set()
        opts = {"check": check, "groups": groups, "gvars": gvars,
                "user": _group_var(groups, gvars, hosts[0], "ansible_user", "ubuntu")}

        # Gathering facts is genuinely the moment Ansible first talks to the box,
        # so it is where an unreachable host announces itself.
        gather = play.get("gather_facts") is not False
        if gather:
            _bar(io, "TASK [Gathering Facts]")
        for host in hosts:
            ok, why = _reach(world, host)
            if ok:
                if gather:
                    _status_line(io, "ok", host)
                    tally[host]["ok"] += 1
                continue
            io.print(c(f"fatal: [{host}]: UNREACHABLE! => {{\"changed\": false, \"msg\": \"Failed "
                       f"to connect to the host via ssh: {why}\", \"unreachable\": true}}", "red"))
            _unreachable_hint(world, io, host)
            tally[host]["unreachable"] += 1
            dead.add(host)

        for task in (play.get("tasks") or []):
            ran_anything = True
            if not _run_task(world, io, task, hosts, play, opts, tally, registered,
                             notified, dead):
                return
            if len(dead) == len(hosts):
                _bar(io, "NO MORE HOSTS LEFT")
                break

        for handler in (play.get("handlers") or []):
            waiting = [h for h in hosts if handler.get("name") in notified[h] and h not in dead]
            if not waiting:
                continue
            handler_ran = True
            _bar(io, f"RUNNING HANDLER [{handler.get('name')}]")
            _run_task(world, io, handler, waiting, play, dict(opts, quiet_banner=True),
                      tally, registered, {h: [] for h in hosts}, dead)

        _bar(io, "PLAY RECAP")
        for host in hosts:
            _recap(io, host, tally[host])
            total_changed += tally[host]["changed"]
            total_failed += tally[host]["failed"] + tally[host]["unreachable"]

    if check:
        world.flags["lab_nodes"] = snapshot
        world.flags["check_ran"] = True
        world.flags["_noop"] = True
        io.print(c("\n(--check is a dry run: it reported what WOULD change and changed nothing. "
                   "Rehearse every change to a live fleet this way.)", "dim"))
        return
    if not ran_anything:
        return
    runs[book] = before + 1
    if total_changed == 0 and not handler_ran and not total_failed and before >= 1:
        world.flags["idempotent_proven"] = True
        io.print(c("\nchanged=0 everywhere — THAT is idempotency: the modules checked the state "
                   "they were asked for, found it, and did nothing. Re-running is always safe.",
                   "dim"))
    _template_note(world, io)


def _template_note(world, io):
    """Point at the tell-tale of a template that isn't templating anything."""
    nodes = _lab(world)
    for dest in sorted(world.flags.pop("_tpl_dests", ()) or ()):
        bodies = [st["files"].get(dest) for st in nodes.values() if dest in st["files"]]
        if len(bodies) > 1 and len(set(bodies)) == 1:
            io.print(c(f"\n(every node got byte-identical {dest} — whatever that template "
                       "interpolates is the same on every host, so it is a copy with extra "
                       "steps. Per-host FACTS like {{ inventory_hostname }} are what make one "
                       "template produce different files.)", "dim"))
            return


# ------------------------------------------------------------------- the host --
def _curl(world, m, io):
    """The browser, in one command — and the port-mapping lesson, both ways."""
    _tick(world)
    world.flags["_noop"] = True
    target = next((t for t in m.group(0).split()[1:] if not t.startswith("-")), "")
    if not target:
        io.print("curl: try 'curl --help' or 'curl --manual' for more information")
        io.print(c("(from the host:  curl localhost:8081 — from the control node:  curl node1)",
                   "dim"))
        return
    url = re.sub(r"^https?://", "", target).split("/")[0]
    host, _sep, port = url.partition(":")
    port = int(port) if port.isdigit() else 80
    nodes = _lab(world)
    services = _compose(world)

    if host in ("localhost", "127.0.0.1", "0.0.0.0"):
        if _in_control(world):
            io.print(f"curl: (7) Failed to connect to {host} port {port}: Connection refused")
            io.print(c("(published ports belong to the HOST, not to a container — from in here "
                       "nothing is listening on that port. On this network the nodes answer by "
                       "NAME on their own port 80:  curl node1)", "dim"))
            return
        for name, spec in services.items():
            for host_port, ctr_port in spec["ports"]:
                if host_port != port:
                    continue
                if name == "semaphore":
                    io.print(SEMAPHORE_PAGE)
                    io.print(c("(Semaphore is a WEB UI — a terminal can fetch its login page and "
                               "no more. Open http://localhost:3000 in a browser, admin/admin123, "
                               "and it runs these same playbooks from buttons.)", "dim"))
                    return
                page = _page(nodes.get(name))
                if page is None:
                    io.print(f"curl: (7) Failed to connect to {host} port {port}: "
                             "Connection refused")
                    if not nodes.get(name, {}).get("running"):
                        io.print(c(f"(the mapping {host_port}->{ctr_port} is in the file, but "
                                   f"{name} isn't running — a published port only forwards while "
                                   "the container behind it is up)", "dim"))
                    else:
                        io.print(c(f"(the mapping {host_port}->{ctr_port} exists, but nothing is "
                                   f"serving inside {name} yet — install nginx and start it)",
                                   "dim"))
                    return
                io.print(page)
                world.flags["curled_ok"] = True
                return
        io.print(f"curl: (7) Failed to connect to {host} port {port}: Connection refused")
        io.print(c("(nothing publishes that port — `docker compose ps` shows the PORTS column, "
                   "and a port only exists on the host if the compose file maps it)", "dim"))
        return

    if not _in_control(world):
        io.print(f"curl: (6) Could not resolve host: {host}")
        io.print(c("(container names are DNS only INSIDE the compose network. From your laptop "
                   "you reach them through published ports:  curl localhost:8081)", "dim"))
        return
    if host == "semaphore":
        io.print(SEMAPHORE_PAGE)
        return
    if host not in nodes:
        io.print(f"curl: (6) Could not resolve host: {host}")
        io.print(c(f"(no container called {host} on ansible-net — `docker compose ps`)", "dim"))
        return
    page = _page(nodes[host])
    if page is None:
        io.print(f"curl: (7) Failed to connect to {host} port {port}: Connection refused")
        io.print(c(f"(nothing is serving on {host} yet — that is what the playbooks are for)",
                   "dim"))
        return
    io.print(page)


def _ssh(world, m, io):
    """Doing by hand what Ansible does for you — including failing the same way."""
    _tick(world)
    world.flags["_noop"] = True
    toks = [t for t in m.group(0).split()[1:] if not t.startswith("-")]
    if not toks:
        io.print("usage: ssh [user@]host <command>")
        return
    target, cmd = toks[0], " ".join(toks[1:])
    user, _sep, host = target.rpartition("@")
    user = user or "root"
    if not _in_control(world):
        io.print(f"ssh: Could not resolve hostname {host}: Name or service not known")
        io.print(c("(the nodes live on the compose network — ssh to them from the CONTROL node)",
                   "dim"))
        return
    nodes = _lab(world)
    if not nodes.get(host, {}).get("running"):
        io.print(f"ssh: Could not resolve hostname {host}: Name or service not known")
        _unreachable_hint(world, io, host)
        return
    if not nodes[host]["keyed"] or user != "ubuntu":
        io.print(f"{user}@{host}: Permission denied (publickey,password).")
        if user != "ubuntu":
            io.print(c("(the key was copied to the `ubuntu` account — which is exactly what "
                       "ansible_user=ubuntu in [web:vars] tells Ansible. Try ubuntu@" + host
                       + ")", "dim"))
        else:
            _unreachable_hint(world, io, host)
        return
    if not cmd:
        io.print(c("(an interactive session would open a shell this simulator can't run — pass "
                   "the command:  ssh ubuntu@node1 hostname)", "dim"))
        return
    rc, out, err = _node_shell(world, host, nodes[host], cmd, user)
    for text in (out, err):
        if text:
            io.print(text)
    if rc == 0 and not world.flags.get("ssh_noted"):
        world.flags["ssh_noted"] = True          # worth saying once, not every hop
        io.print(c("(that worked with no password — the same trust Ansible rides on. Ansible is "
                   "this, times every host in the inventory, with modules instead of commands.)",
                   "dim"))


HANDLERS = [
    (r"(?:docker\s+compose|docker-compose)\s+up(?:\s+.*)?", _compose_up),
    (r"(?:docker\s+compose|docker-compose)\s+ps(?:\s+.*)?", _compose_ps),
    (r"(?:docker\s+compose|docker-compose)\s+logs(?:\s+.*)?", _logs),
    (r"docker\s+logs(?:\s+.*)?", _logs),
    (r"(?:docker\s+compose|docker-compose)\s+(?:down|stop|start|restart)(?:\s+.*)?", _lifecycle),
    (r"(?:docker\s+compose|docker-compose)\s+exec(?:\s+.*)?", _exec),
    (r"docker\s+exec(?:\s+.*)?", _exec),
    (r"ansible-playbook(?:\s+.*)?", _playbook),
    (r"ansible-doc(?:\s+.*)?", _doc),
    (r"ansible(?:\s+.*)?", _ansible),
    (r"curl(?:\s+.*)?", _curl),
    (r"ssh(?:\s+.*)?", _ssh),
    (r"exit", _exit),
]


def prompt(world):
    """Which machine you are standing on — the one fact this mission is about."""
    if _in_control(world):
        return c("root@ansible-control:/ansible# ", "cyan")
    return c("student@laptop:~/ansible_lab_files$ ", "cyan")


# -------------------------------------------------------------- check helpers --
def _serving(world, hosts):
    nodes = world.flags.get("lab_nodes") or {}
    return bool(hosts) and all(_page(nodes.get(h)) for h in hosts)


def _node3_wired(world):
    """A third node is only real when all THREE files know about it — and the
    proof is that it serves a page on a published port of its own."""
    services = _compose(world)
    spec = services.get("node3")
    if not spec or not any(ctr == 80 for _host_port, ctr in spec["ports"]):
        return False
    if "node3" not in _managed(world) or "node3" not in _entrypoint_nodes(world):
        return False
    st = (world.flags.get("lab_nodes") or {}).get("node3")
    return bool(st and st["keyed"] and _page(st))


def _per_host_pages(world):
    """One template, N different pages: every managed node serves its OWN name."""
    nodes = world.flags.get("lab_nodes") or {}
    hosts = _managed(world)
    pages = {}
    for host in hosts:
        page = _page(nodes.get(host))
        if page is None or host not in page:
            return False
        pages[host] = page
    return len(pages) >= 2 and len(set(pages.values())) == len(pages)


MISSIONS = [
    {
        "id": "ansible-03",
        "topic": "ansible",
        "title": "Automation Alchemist 🧪 — the dockerized Ansible lab",
        "vault_note": "Class 14 - Ansible Lab",
        "brief": ("A whole mini-datacenter on your laptop: one control node and two web\n"
                  "nodes, all containers on one compose network. `entrypoint.sh` generates\n"
                  "an SSH keypair and pushes it to the nodes, which is why `ansible all -m\n"
                  "ping` will just work — once it has finished.\n\n"
                  "You start on the HOST, in the lab folder. The host has no Ansible: the\n"
                  "control node has it, and the inventory, and the key. Bring the stack up,\n"
                  "get inside, run the two playbooks, and watch a real website appear on\n"
                  "http://localhost:8081 and :8082. Then grow the fleet.\n\n"
                  "(cat docker-compose.yml · entrypoint.sh · inventory.ini · ansible.cfg —\n"
                  " those four files ARE the lab.)"),
        "world": {
            "files": {
                COMPOSE: COMPOSE_YML,
                ENTRYPOINT: ENTRYPOINT_SH,
                INVENTORY: INVENTORY_INI,
                ANSIBLE_CFG: ANSIBLE_CFG_INI,
                "playbooks/install_nginx.yml": INSTALL_NGINX,
                "playbooks/deploy_website.yml": DEPLOY_WEBSITE,
                "templates/index.html.j2": INDEX_J2,
                "templates/nginx.conf.j2": NGINX_CONF_J2,
            },
        },
        "handlers": HANDLERS,
        "prompt": prompt,
        "help_lines": [
            "   host:    docker compose up -d · ps · logs ansible-control · restart <svc> · down",
            "            docker compose exec ansible-control bash   ← the doorway",
            "            curl localhost:8081 (the published port is a HOST thing)",
            "   control: ansible <pattern> [-m mod] [-a \"args\"] [-b] [-o] [--list-hosts]",
            "            ansible-playbook playbooks/<file>.yml [--check] · ansible-doc <module>",
            "            ssh ubuntu@node1 <cmd> · curl node1 · exit (back to the host)",
            "   shell:   ls · cat · edit <file> — the lab folder is bind-mounted into /ansible",
        ],
        "objectives": [
            {"desc": "Bring the datacenter up and wait for the control node to say it's ready",
             "xp": 15,
             "hint": "docker compose up -d, then docker compose logs ansible-control — the line "
                     "to wait for is 'Ansible control node is ready.' (keys are still being "
                     "copied before it appears; run the logs again).",
             "check": lambda w: w.flags.get("lab_ready_seen")},
            {"desc": "Try Ansible on the HOST (it fails — that IS the lesson), then step inside",
             "xp": 10,
             "hint": "Type `ansible all -m ping` right here on the host first and read the error. "
                     "Then: docker compose exec ansible-control bash — the prompt changes.",
             "check": lambda w: w.flags.get("host_no_ansible") and w.flags.get("entered_control")},
            {"desc": "Ping every node — no password, and no -i", "xp": 15,
             "hint": "ansible all -m ping. cat ansible.cfg and inventory.ini to see who told it "
                     "where to look and which key to use.",
             "check": lambda w: w.flags.get("pinged_all")},
            {"desc": "Make Ansible tell you exactly which hosts it manages", "xp": 10,
             "hint": "ansible web --list-hosts  (and `ansible all -m ping -o` for one compact "
                     "line per host).",
             "check": lambda w: w.flags.get("listed_hosts")},
            {"desc": "Run one command across the whole fleet at once — as root", "xp": 15,
             "hint": 'ansible web -m shell -a "hostname && whoami" -b   — run it without -b too '
                     "and watch the user change.",
             "check": lambda w: w.flags.get("adhoc_root")},
            {"desc": "Install nginx with the playbook, then run it AGAIN — changed=0", "xp": 20,
             "hint": "ansible-playbook playbooks/install_nginx.yml — twice. The second run is "
                     "the one that proves the point.",
             "check": lambda w: w.flags.get("idempotent_proven")},
            {"desc": "Dry-run the website deploy, deploy for real, and SEE it on the mapped port",
             "xp": 20,
             "hint": "ansible-playbook playbooks/deploy_website.yml --check first, then without "
                     "it. Then `exit` back to the host and curl localhost:8081.",
             "check": lambda w: (w.flags.get("check_ran") and w.flags.get("curled_ok")
                                 and _serving(w, ["node1", "node2"]))},
            {"desc": "Grow the fleet: add node3 in all THREE places and deploy to it", "xp": 20,
             "hint": "docker-compose.yml (a node3 service, ports \"8083:80\"), inventory.ini "
                     "(under [web]) and entrypoint.sh's `for host in …` loop — miss that one and "
                     "node3 never gets a key. Then docker compose up -d and re-run the deploy.",
             "check": _node3_wired},
            {"desc": "One template, a different page per node: each serves its OWN hostname",
             "xp": 20,
             "hint": "edit templates/index.html.j2 → <h1>Hello from {{ inventory_hostname }}</h1>, "
                     "then switch the copy task in playbooks/deploy_website.yml to the template "
                     "module (src: index.html.j2), re-run it, and curl 8081 / 8082 / 8083.",
             "check": _per_host_pages},
        ],
        "teach": [
            "`up -d` returns when the containers START, not when they're USABLE. Readiness is "
            "something the app writes to its log — which is why `logs` is step two of every stack.",
            "The control node is a MACHINE, not a command: Ansible, the inventory and the private "
            "key live inside that container, and everything in this lab is run from in there.",
            "'pong' proves SSH + Python on the target. Passwordless comes from entrypoint.sh's "
            "ssh-copy-id; no -i is needed because ansible.cfg sets `inventory = inventory.ini`.",
            "--list-hosts resolves the pattern and contacts nothing — the cheapest way to find "
            "out whether you just aimed at a group, a host, or a typo.",
            "-b (become) is sudo: without it you are the inventory's ansible_user. Ad-hoc is for "
            "looking around; anything you want repeatable belongs in a playbook.",
            "Idempotency: apt checks state before acting, so the second run reports ok, not "
            "changed. A playbook describes the destination, never the drive.",
            "--check is the rehearsal — it reports and changes nothing. And 8081/8082 are HOST "
            "ports: a container's port 80 reaches your laptop only through a published mapping.",
            "A node exists in three files: compose gives it a container, the inventory makes it a "
            "target, the entrypoint gives it a key. Each omission fails differently — no DNS, no "
            "host matched, Permission denied (publickey).",
            "copy ships bytes; template RENDERS them per host from that host's facts. One file, "
            "N results — the moment Ansible stops being a fancy ssh loop.",
        ],
        "solution": [
            "cat docker-compose.yml",
            "cat entrypoint.sh",
            "docker compose up -d",
            "docker compose ps",
            "docker compose logs ansible-control",
            "docker compose logs ansible-control",
            "ansible all -m ping",
            "docker compose exec ansible-control bash",
            "cat ansible.cfg",
            "cat inventory.ini",
            "ansible all -m ping",
            "ansible web --list-hosts",
            "ansible all -m ping -o",
            'ansible web -m shell -a "hostname && whoami" -b',
            "ansible-playbook playbooks/install_nginx.yml",
            "ansible-playbook playbooks/install_nginx.yml",
            "ansible-playbook playbooks/deploy_website.yml --check",
            "ansible-playbook playbooks/deploy_website.yml",
            "exit",
            "curl localhost:8081",
            "curl localhost:8082",
            "edit docker-compose.yml",
            "name: lab",
            "",
            "services:",
            "  node1:",
            "    build: ./node",
            "    container_name: node1",
            "    hostname: node1",
            "    ports:",
            '      - "8081:80"',
            "    networks:",
            "      - ansible-net",
            "",
            "  node2:",
            "    build: ./node",
            "    container_name: node2",
            "    hostname: node2",
            "    ports:",
            '      - "8082:80"',
            "    networks:",
            "      - ansible-net",
            "",
            "  node3:",
            "    build: ./node",
            "    container_name: node3",
            "    hostname: node3",
            "    ports:",
            '      - "8083:80"',
            "    networks:",
            "      - ansible-net",
            "",
            "  ansible-control:",
            "    build: ./control",
            "    container_name: ansible-control",
            "    depends_on:",
            "      - node1",
            "      - node2",
            "      - node3",
            "    volumes:",
            "      - .:/ansible",
            "      - ssh_keys:/root/.ssh",
            "    networks:",
            "      - ansible-net",
            "",
            "  semaphore:",
            "    image: semaphoreui/semaphore:latest",
            "    container_name: semaphore",
            "    ports:",
            '      - "3000:3000"',
            "    volumes:",
            "      - ssh_keys:/home/semaphore/.ssh:ro",
            "    networks:",
            "      - ansible-net",
            "",
            "volumes:",
            "  ssh_keys:",
            "",
            "networks:",
            "  ansible-net:",
            "    driver: bridge",
            ".",
            "edit inventory.ini",
            "[web]",
            "node1",
            "node2",
            "node3",
            "",
            "[web:vars]",
            "ansible_user=ubuntu",
            "ansible_ssh_private_key_file=/root/.ssh/id_rsa",
            ".",
            "edit entrypoint.sh",
            "#!/bin/bash",
            "set -e",
            "",
            "if [ ! -f /root/.ssh/id_rsa ]; then",
            '  ssh-keygen -t rsa -N "" -f /root/.ssh/id_rsa',
            "fi",
            "",
            'echo "Waiting for nodes to be ready and distributing SSH keys..."',
            "for host in node1 node2 node3; do",
            "  until sshpass -p ubuntu ssh-copy-id -o StrictHostKeyChecking=no ubuntu@$host; do",
            '    echo "  $host not ready yet - retrying in 2s"',
            "    sleep 2",
            "  done",
            '  echo "SSH key distributed to $host"',
            "done",
            "",
            'echo "Ansible control node is ready."',
            "tail -f /dev/null",
            ".",
            "docker compose up -d",
            "docker compose ps",
            "docker compose exec ansible-control bash",
            "ansible all -m ping",
            "ansible-playbook playbooks/deploy_website.yml",
            "edit templates/index.html.j2",
            "<h1>Hello from {{ inventory_hostname }}</h1>",
            ".",
            "edit playbooks/deploy_website.yml",
            "---",
            "- name: deploy the website",
            "  hosts: web",
            "  become: true",
            "  tasks:",
            "    - name: nginx is present",
            "      apt:",
            "        name: nginx",
            "        state: present",
            "",
            "    - name: publish the homepage",
            "      template:",
            "        src: index.html.j2",
            "        dest: /var/www/html/index.html",
            "      notify: restart nginx",
            "",
            "    - name: nginx is running",
            "      service:",
            "        name: nginx",
            "        state: started",
            "",
            "  handlers:",
            "    - name: restart nginx",
            "      service:",
            "        name: nginx",
            "        state: restarted",
            ".",
            "ansible-playbook playbooks/deploy_website.yml",
            "exit",
            "curl localhost:8081",
            "curl localhost:8082",
            "curl localhost:8083",
        ],
    },
]
