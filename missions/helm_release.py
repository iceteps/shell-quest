"""Helm mission — install / upgrade / rollback the class-6 style chart.

Helm isn't simulated by the engine; this module ships a mission-local
handler (the house rule: promote to engine only when 2+ missions need it).
"""
import re

from engine import _reconcile, c

VALUES_YAML = '''replicaCount: 2
image:
  repository: nginx
  tag: "1.25"
service:
  type: ClusterIP
  port: 80
'''

CHART_YAML = '''apiVersion: v2
name: my-service
description: A Helm chart for Kubernetes
version: 0.1.0
'''

DEPLOY_TPL = '''apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ .Release.Name }}-my-service
spec:
  replicas: {{ .Values.replicaCount }}
  template:
    spec:
      containers:
        - name: my-service
          image: "{{ .Values.image.repository }}:{{ .Values.image.tag }}"
'''


def _releases(world):
    return world.flags.setdefault("helm_releases", {})


def _sync_deployment(world, release, values):
    """A helm release materializes as a k8s Deployment — wire the two worlds."""
    name = f"{release}-my-service"
    world.k8s["deployments"][name] = {
        "ns": "default", "replicas": values["replicaCount"],
        "image": f"nginx:{values['tag']}", "revision": len(_releases(world)[release]["history"]),
    }
    _reconcile(world)


def _helm(world, m, io):
    line = m.group(0)
    args = line.split()[1:]
    rel_db = _releases(world)
    sub = args[0] if args else ""

    if sub == "install" and len(args) >= 3:
        release, chart = args[1], args[2]
        if not chart.rstrip("/").endswith("my-service"):
            io.print(f"Error: INSTALLATION FAILED: path \"{chart}\" not found")
            io.print(c("(the chart folder here is ./my-service)", "dim"))
            return
        if release in rel_db:
            io.print(f"Error: INSTALLATION FAILED: cannot re-use a name that is still in use")
            return
        values = {"replicaCount": 2, "tag": "1.25"}
        m_set = re.search(r"--set\s+replicaCount=(\d+)", line)
        if m_set:
            values["replicaCount"] = int(m_set.group(1))
        rel_db[release] = {"history": [dict(values)]}
        _sync_deployment(world, release, values)
        io.print(f"NAME: {release}\nLAST DEPLOYED: right now\nNAMESPACE: default\n"
                 f"STATUS: deployed\nREVISION: 1\nNOTES:\nThank you for installing my-service.")
        world.flags["helm_installed"] = release

    elif sub == "list" or sub == "ls":
        io.print(f"{'NAME':<10}{'NAMESPACE':<11}{'REVISION':<10}{'STATUS':<10}CHART")
        for r, d in rel_db.items():
            io.print(f"{r:<10}{'default':<11}{len(d['history']):<10}{'deployed':<10}my-service-0.1.0")
        world.flags["helm_list"] = True

    elif sub == "upgrade" and len(args) >= 3:
        release = args[1]
        if release not in rel_db:
            io.print(f'Error: UPGRADE FAILED: "{release}" has no deployed releases'); return
        values = dict(rel_db[release]["history"][-1])
        m_set = re.search(r"--set\s+replicaCount=(\d+)", line)
        if m_set:
            values["replicaCount"] = int(m_set.group(1))
        m_tag = re.search(r"--set\s+image\.tag=([\w.\"-]+)", line)
        if m_tag:
            values["tag"] = m_tag.group(1).strip('"')
        rel_db[release]["history"].append(values)
        _sync_deployment(world, release, values)
        io.print(f"Release \"{release}\" has been upgraded. Happy Helming!\n"
                 f"NAME: {release}\nSTATUS: deployed\nREVISION: {len(rel_db[release]['history'])}")
        world.flags["helm_upgraded"] = len(rel_db[release]["history"])

    elif sub == "rollback" and len(args) >= 2:
        release = args[1]
        if release not in rel_db:
            io.print(f"Error: release: not found"); return
        target = int(args[2]) if len(args) > 2 and args[2].isdigit() else max(1, len(rel_db[release]["history"]) - 1)
        if target > len(rel_db[release]["history"]):
            io.print(f"Error: revision {target} does not exist"); return
        values = dict(rel_db[release]["history"][target - 1])
        rel_db[release]["history"].append(values)
        _sync_deployment(world, release, values)
        io.print("Rollback was a success! Happy Helming!")
        world.flags["helm_rolled_back"] = target

    elif sub == "history" and len(args) >= 2:
        release = args[1]
        if release not in rel_db:
            io.print("Error: release: not found"); return
        io.print(f"{'REVISION':<10}{'STATUS':<13}{'CHART':<19}DESCRIPTION")
        hist = rel_db[release]["history"]
        for i, v in enumerate(hist, 1):
            status = "deployed" if i == len(hist) else "superseded"
            io.print(f"{i:<10}{status:<13}{'my-service-0.1.0':<19}replicaCount={v['replicaCount']} tag={v['tag']}")
        world.flags["helm_history"] = True

    elif sub == "template":
        io.print("---\n# Source: my-service/templates/deployment.yaml")
        io.print(DEPLOY_TPL.replace("{{ .Release.Name }}", "demo")
                 .replace("{{ .Values.replicaCount }}", "2")
                 .replace("{{ .Values.image.repository }}", "nginx")
                 .replace("{{ .Values.image.tag }}", "1.25"))
        io.print(c("(this is the RENDERED yaml — templates + values, no cluster touched)", "dim"))
        world.flags["helm_template"] = True

    elif sub == "uninstall" and len(args) >= 2:
        release = args[1]
        if rel_db.pop(release, None):
            world.k8s["deployments"].pop(f"{release}-my-service", None)
            _reconcile(world)
            io.print(f'release "{release}" uninstalled')
        else:
            io.print("Error: release: not found")

    else:
        io.print("helm: try install <release> ./my-service · upgrade · rollback · history · list · template")


MISSIONS = [
    {
        "id": "helm-01",
        "topic": "helm",
        "title": "Package It ⎈ — install, upgrade, roll back",
        "vault_note": "Class 06 - Helm",
        "brief": ("Raw YAML doesn't scale — Helm charts do. The class-6 chart ./my-service\n"
                  "is here (ls, cat my-service/values.yaml). Install it as a release named\n"
                  "'demo', watch it become real pods, upgrade it with --set, then use\n"
                  "Helm's killer feature: roll back a bad release in one command."),
        "world": {
            "k8s": {"started": True},
            "files": {
                "my-service/Chart.yaml": CHART_YAML,
                "my-service/values.yaml": VALUES_YAML,
                "my-service/templates/deployment.yaml": DEPLOY_TPL,
            },
        },
        "handlers": [
            (r"helm\s+.*", _helm),
        ],
        "objectives": [
            {"desc": "Render the chart locally FIRST — see what it would create", "xp": 10,
             "hint": "helm template ./my-service — renders templates + values without touching the cluster.",
             "check": lambda w: w.flags.get("helm_template")},
            {"desc": "Install the chart as a release named 'demo'", "xp": 20,
             "hint": "helm install demo ./my-service",
             "check": lambda w: w.flags.get("helm_installed") == "demo"},
            {"desc": "Verify the release became real pods (2 of them)", "xp": 10,
             "hint": "kubectl get pods — helm is just a factory for k8s objects.",
             "check": lambda w: w.flags.get("get_pods")
                                and w.k8s and sum(1 for p in w.k8s["pods"].values()
                                                  if p.get("deploy") == "demo-my-service") >= 2},
            {"desc": "Upgrade the release to 4 replicas using --set", "xp": 20,
             "hint": "helm upgrade demo ./my-service --set replicaCount=4  (values.yaml is the default; --set overrides)",
             "check": lambda w: w.flags.get("helm_upgraded") == 2
                                and w.k8s["deployments"].get("demo-my-service", {}).get("replicas") == 4},
            {"desc": "Something's wrong with rev 2 — ROLL BACK to revision 1", "xp": 20,
             "hint": "helm rollback demo 1 — then kubectl get pods to see the replica count snap back.",
             "check": lambda w: w.flags.get("helm_rolled_back") == 1
                                and w.k8s["deployments"].get("demo-my-service", {}).get("replicas") == 2},
            {"desc": "Read the release history — every revision is recorded", "xp": 10,
             "hint": "helm history demo",
             "check": lambda w: w.flags.get("helm_history")},
        ],
        "solution": [
            "helm template ./my-service",
            "helm install demo ./my-service",
            "kubectl get pods",
            "helm upgrade demo ./my-service --set replicaCount=4",
            "helm rollback demo 1",
            "helm history demo",
        ],
    },
]
