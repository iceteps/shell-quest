"""Ansible mission — ping, playbook, IDEMPOTENCY, handlers. All simulated
with mission-local handlers (the engine knows nothing about ansible)."""
from engine import c

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


def _hosts(world):
    return world.flags.setdefault(
        "ansible_state",
        {h: {"nginx": False, "html": None, "running": False} for h in ("node1", "node2")})


def _ansible(world, m, io):
    line = m.group(0)
    if "-m ping" in line:
        if "hosts" not in world.files:
            io.print("[WARNING]: Unable to parse inventory — no hosts file here")
            return
        for h in ("node1", "node2"):
            io.print(c(f"{h} | SUCCESS => ", "green") + '{\n    "changed": false,\n    "ping": "pong"\n}')
        world.flags["ansible_pinged"] = True
    elif "-m " in line:
        io.print("(only the ping module is simulated here — the playbook is where the action is)")
    else:
        io.print("ansible: try  ansible all -i hosts -m ping")


def _playbook(world, m, io):
    line = m.group(0)
    if "playbook.yml" not in line:
        io.print("ERROR! the playbook: could not be found")
        return
    check = "--check" in line
    state = _hosts(world)
    desired_html = world.files.get("index.html", INDEX_HTML)
    io.print(f"\nPLAY [configure web servers] {'*' * 30}\n")
    io.print(f"TASK [Gathering Facts] {'*' * 36}")
    for h in state:
        io.print(c(f"ok: [{h}]", "green"))
    changed_total = {h: 0 for h in state}

    def task(name, will_change):
        io.print(f"\nTASK [{name}] {'*' * (58 - len(name))}")
        for h in state:
            if will_change(state[h]):
                io.print(c(f"changed: [{h}]", "yellow"))
                changed_total[h] += 1
            else:
                io.print(c(f"ok: [{h}]", "green"))

    task("install nginx", lambda s: not s["nginx"])
    task("copy website", lambda s: s["html"] != desired_html)
    task("nginx is running", lambda s: not s["running"])

    handler_fired = any(s["html"] != desired_html for s in state.values())
    if handler_fired and not check:
        io.print(f"\nRUNNING HANDLER [restart nginx] {'*' * 27}")
        for h in state:
            if state[h]["html"] != desired_html:
                io.print(c(f"changed: [{h}]", "yellow"))
        if any(s["html"] is not None for s in state.values()):
            world.flags["handler_refired"] = True

    if not check:
        for h, s in state.items():
            s["nginx"], s["running"] = True, True
            s["html"] = desired_html

    io.print(f"\nPLAY RECAP {'*' * 48}")
    for h in state:
        ch = changed_total[h] + (1 if handler_fired and not check else 0)
        io.print(f"{h:<9}: " + c(f"ok={4 - changed_total[h]}", "green") + "  "
                 + (c(f"changed={ch}", "yellow") if ch else "changed=0") + "  unreachable=0  failed=0")
    if check:
        io.print(c("\n(--check = dry run: it REPORTED what would change but touched nothing)", "dim"))
        world.flags["check_ran"] = True
        return
    world.flags["play_runs"] = world.flags.get("play_runs", 0) + 1
    if all(changed_total[h] == 0 for h in state) and not handler_fired:
        io.print(c("\nchanged=0 everywhere — THAT is idempotency: re-running is safe, only drift gets fixed", "dim"))
        world.flags["idempotent_proven"] = True


def _ansible_doc(world, m, io):
    io.print("> APT    (/usr/lib/python3/ansible/modules/apt.py)\n\n"
             "        Manages apt packages (such as for Debian/Ubuntu).\n\n"
             "EXAMPLES:\n- name: Install nginx\n  apt:\n    name: nginx\n    state: present")
    world.flags["ansible_doc"] = True


MISSIONS = [
    {
        "id": "ansible-01",
        "topic": "ansible",
        "title": "Agentless Army 📜 — one playbook, N servers",
        "vault_note": "Class 11 - Ansible",
        "brief": ("Two fresh Ubuntu nodes (node1, node2) and zero agents installed —\n"
                  "Ansible works over plain SSH. The inventory (hosts) and playbook.yml\n"
                  "are here (cat them!). Reach the nodes, configure them, then prove the\n"
                  "two ideas everyone gets asked about: IDEMPOTENCY and HANDLERS."),
        "world": {
            "files": {"hosts": HOSTS_INI, "playbook.yml": PLAYBOOK, "index.html": INDEX_HTML},
        },
        "handlers": [
            (r"ansible-playbook\s+.*", _playbook),
            (r"ansible-doc\s+.*", _ansible_doc),
            (r"ansible\s+.*", _ansible),
        ],
        "objectives": [
            {"desc": "Prove you can reach every host (agentless!)", "xp": 10,
             "hint": "ansible all -i hosts -m ping — 'pong' means SSH + Python are good to go.",
             "check": lambda w: w.flags.get("ansible_pinged")},
            {"desc": "Run the playbook — configure BOTH nodes in one shot", "xp": 20,
             "hint": "ansible-playbook -i hosts playbook.yml",
             "check": lambda w: w.flags.get("play_runs", 0) >= 1},
            {"desc": "Run it AGAIN — prove idempotency (changed=0)", "xp": 20,
             "hint": "Same command, second run. Watch every task report ok, not changed.",
             "check": lambda w: w.flags.get("idempotent_proven")},
            {"desc": "Change index.html, re-run — ONLY the copy task changes + handler fires", "xp": 25,
             "hint": "edit index.html (new text), then run the playbook again. The 'notify' on the copy "
                     "task wakes the restart-nginx handler.",
             "check": lambda w: w.flags.get("handler_refired")},
            {"desc": "Look up a module's docs WITHOUT leaving the terminal", "xp": 10,
             "hint": "ansible-doc apt — docs + copy-paste examples, offline.",
             "check": lambda w: w.flags.get("ansible_doc")},
        ],
        "solution": [
            "ansible all -i hosts -m ping",
            "ansible-playbook -i hosts playbook.yml",
            "ansible-playbook -i hosts playbook.yml",
            "edit index.html",
            "<h1>Hello from Ansible v2!</h1>", ".",
            "ansible-playbook -i hosts playbook.yml",
            "ansible-doc apt",
        ],
    },
]
