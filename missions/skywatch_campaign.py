"""THE CAMPAIGN — the SkyWatch capstone, in two runs.

Mission 1 is the build: Terraform provisions, Ansible installs K3s, Helm ships
the app, ArgoCD guards it, RabbitMQ carries the weather, and `terraform destroy`
pays the bill. Mission 2 is Part 4 of the capstone, observability — the part
that gets skipped because the app already "works".

The note is blunt about which mistakes eat a whole session, and narrating them
as flavour teaches nobody. So the playbook that ships here is *wrong in the way
everyone's is wrong* — it joins the workers on the master's PUBLIC ip — and the
join fails the way AWS really fails it. Fixing it with a careless `sed` breaks
`--tls-san` instead, which is the second gotcha wearing the first one's coat.
`--limit worker`, an unpinned K3s version and `--token` instead of the env var
are all detected too: each one is a flashcard in the note, and each one answers
in the tool's own voice.

Reuses the terraform handler and the gitops module's `cat`/`grep`/`sed`;
everything else is campaign-local.
"""
import re
import shlex

from engine import (TOOL_VERSION_LINES, _k8s_apply_doc, _parse_manifests, _reconcile,
                    _table, c, do_kubectl)
from missions.gitops_ci import _cat, _grep, _sed, _yq
from missions.terraform_infra import _tf

# The whole private-vs-public lesson, as two strings. The master's PUBLIC ip is
# the right answer for --tls-san and the wrong answer for K3S_URL — same host,
# two different network paths, which is exactly why the trap works.
MASTER_PUB, MASTER_PRIV = "54.72.18.3", "10.0.1.10"

MAIN_TF = '''provider "aws" {
  region = "eu-west-1"
}

resource "aws_instance" "master" {
  ami           = "ami-0abcdef1234567890"
  instance_type = "t3.micro"
  tags = { Name = "skywatch-master" }
}

resource "aws_instance" "worker" {
  ami           = "ami-0abcdef1234567890"
  instance_type = "t3.micro"
  tags = { Name = "skywatch-worker" }
}

resource "aws_instance" "worker2" {
  ami           = "ami-0abcdef1234567890"
  instance_type = "t3.micro"
  tags = { Name = "skywatch-worker2" }
}
'''

INVENTORY = f'''# rendered by terraform from inventory.tmpl — BOTH addresses, on purpose
[master]
skywatch-master  ansible_host={MASTER_PUB}  private_ip={MASTER_PRIV}

[workers]
skywatch-worker   ansible_host=54.72.19.7  private_ip=10.0.1.11
skywatch-worker2  ansible_host=54.72.21.4  private_ip=10.0.1.12
'''

K3S_PLAYBOOK = f'''---
- name: install the K3s control plane
  hosts: master
  become: true
  tasks:
    - name: is k3s already running?
      command: systemctl is-active k3s
      register: k3s_active
      failed_when: false
      changed_when: false

    - name: install the k3s server
      shell: >
        curl -sfL https://get.k3s.io |
        INSTALL_K3S_VERSION=v1.29.5+k3s1
        sh -s - server --tls-san {MASTER_PUB}
      when: k3s_active.rc != 0

    - name: read the join token
      slurp:
        src: /var/lib/rancher/k3s/server/node-token
      register: k3s_token

- name: join the workers
  hosts: workers
  become: true
  tasks:
    - name: join the cluster
      shell: >
        curl -sfL https://get.k3s.io |
        K3S_URL=https://{MASTER_PUB}:6443
        K3S_TOKEN={{{{ hostvars['skywatch-master'].k3s_token }}}}
        sh -s - agent
'''

VALUES_YAML = '''frontend:
  image: ghcr.io/you/skywatch-frontend
  tag: v1
worker:
  replicas: 2
  image: ghcr.io/you/skywatch-worker
rabbitmq:
  image: rabbitmq:3.13-management
  # 5672 amqp · 15672 management UI · 15692 prometheus (plugin is on by default)
'''

ARGO_APP = '''apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: skywatch
  namespace: argocd
spec:
  project: default
  source:
    repoURL: https://github.com/you/skywatch
    path: helm/skywatch
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
'''

# ---------------------------------------------------- mission 2: monitoring --
PROM_VALUES = '''# helm/monitoring/values.yaml — kube-prometheus-stack, slimmed for a 1 GB node
alertmanager:
  enabled: false          # ~100 MB we do not have
prometheus:
  prometheusSpec:
    retention: 24h
    resources:
      limits: { cpu: 500m, memory: 512Mi }
    nodeSelector: { kubernetes.io/hostname: skywatch-worker2 }
grafana:
  adminPassword: skywatch-grafana
  service:
    type: NodePort
    nodePort: 30030
  resources:
    limits: { cpu: 200m, memory: 128Mi }
  nodeSelector: { kubernetes.io/hostname: skywatch-worker2 }
'''

CRD_LIST = ["alertmanagerconfigs", "alertmanagers", "podmonitors", "probes",
            "prometheusagents", "prometheuses", "prometheusrules",
            "scrapeconfigs", "servicemonitors", "thanosrulers"]

SERVICEMONITOR = '''apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: skywatch-rabbitmq
  namespace: skywatch
  labels:
    release: kube-prometheus-stack   # how the Prometheus CR selects it
spec:
  selector:
    matchLabels:
      app.kubernetes.io/name: skywatch-rabbitmq
  endpoints:
    - port: prometheus               # the SERVICE port NAME, not a number
      interval: 30s
'''


def _cluster_up(world):
    return world.k8s and world.k8s["started"]


# ------------------------------------------------------------------ ansible --
def _play_setting(world, key, pattern):
    """Pull one value out of the playbook. The player edits that file with `sed`,
    so the playbook text — not a flag — has to be what the run reads."""
    m = re.search(pattern, world.files.get("k3s.yml", ""))
    return m.group(1) if m else None


def _ansible_k3s(world, m, io):
    line = m.group(0)
    if "k3s.yml" not in line:
        io.print("ERROR! the playbook: could not be found (try: ansible-playbook -i inventory k3s.yml)")
        world.flags["_noop"] = True
        return
    if not world.flags.get("tf_state"):
        io.print(c("fatal: [skywatch-master]: UNREACHABLE! => ssh: connect to host: No route to host", "red"))
        io.print(c("(there are no machines yet — terraform hasn't applied. Provision first.)", "dim"))
        world.flags["_noop"] = True
        return

    limit = re.search(r"(?:--limit|-l)[= ]([\w,:!*]+)", line)
    only = limit.group(1) if limit else ""
    skip_master = bool(only) and not re.search(r"master|all|\*", only)

    version = _play_setting(world, "version", r"INSTALL_K3S_VERSION=(\S+)")
    tls_san = _play_setting(world, "tls-san", r"--tls-san\s+([\d.]+)")
    join_ip = _play_setting(world, "join", r"K3S_URL=https://([\d.]+):6443")
    token_flag = "--token" in world.files.get("k3s.yml", "")

    io.print("")
    master_changed = False
    if skip_master:
        io.print(c(f"(--limit {only} — the master play is not in the pattern, so it is skipped "
                   "entirely)", "dim"))
    else:
        io.print("PLAY [install the K3s control plane] " + "*" * 22)
        if not version or not re.match(r"v1\.(?:2\d|30)\b", version):
            # The note's first lesson: v1.35's api-server + etcd footprint does
            # not fit in 1 GB. Unpinning it is a slow, confusing death, so it
            # gets a fast, loud one.
            io.print(c("changed: [skywatch-master]", "yellow") + "  (k3s server installing…)")
            io.print(c("fatal: [skywatch-master]: FAILED! => k3s.service: "
                       "Main process exited, status=137/OOM", "red"))
            io.print(c(f"(status 137 is the kernel's OOM killer. INSTALL_K3S_VERSION="
                       f"{version or '<unset>'} — the api-server + etcd footprint of a modern "
                       "K3s does not fit in a t3.micro's 1 GB. Pin v1.29.5+k3s1; on constrained "
                       "nodes pinning is not optional.)", "dim"))
            world.flags["k3s_oom"] = True
            return
        again = world.flags.get("k3s_master_ok") and tls_san == world.flags.get("k3s_tls_san")
        if again:
            io.print(c("ok: [skywatch-master]", "green")
                     + f"  (systemctl is-active k3s → active, so the install task is skipped)")
        else:
            master_changed = True
            io.print(c("changed: [skywatch-master]", "yellow")
                     + f"  (k3s {version} installed, --tls-san {tls_san or '<none>'})")
            if world.flags.get("k3s_master_ok"):
                io.print(c("(the server args changed, so the role reinstalled the control plane "
                           "and regenerated its certificate. On a real cluster that is a "
                           "maintenance window — the cert is minted at INSTALL time, which is "
                           "why --tls-san has to be right the first time.)", "dim"))
        io.print(c("ok: [skywatch-master]", "green") + "  (join token read into the k3s_token fact)")
        world.k8s["started"] = True
        world.k8s["nodes"] = ["skywatch-master"]
        world.flags["k3s_master_ok"] = True
        world.flags["k3s_tls_san"] = tls_san
        world.flags["k3s_version"] = version

    io.print("\nPLAY [join the workers] " + "*" * 35)
    hosts = ("skywatch-worker", "skywatch-worker2")
    if not world.flags.get("k3s_master_ok"):
        for h in hosts:
            io.print(c(f"fatal: [{h}]: FAILED! => \"The task includes an option with an undefined "
                       "variable. The error was: 'k3s_token' is undefined\"", "red"))
        io.print(c("(--limit skipped the master play, so the token fact was never gathered. Facts "
                   "live for ONE run — they are not saved between playbook invocations. Run the "
                   "FULL playbook; the master's own tasks are idempotent anyway.)", "dim"))
        world.flags["k3s_limit_trap"] = True
        return
    if token_flag:
        for h in hosts:
            io.print(c(f"fatal: [{h}]: FAILED! => k3s agent: token is required", "red"))
        io.print(c("(the install pipe reads K3S_TOKEN from the ENVIRONMENT. Passed as a --token "
                   "flag it is dropped on the floor and never written to the agent's env file.)", "dim"))
        return
    if join_ip != MASTER_PRIV:
        for h in hosts:
            io.print(c(f"fatal: [{h}]: FAILED! => curl: (28) Failed to connect to {join_ip} "
                       "port 6443 after 130002 ms: Connection timed out", "red"))
        io.print(c(f"(the security group allows 6443 from ITSELF — and it is doing exactly that. "
                   f"A packet aimed at the master's PUBLIC ip {MASTER_PUB} leaves the VPC through "
                   "the Internet Gateway and comes back as outside traffic, which the self-rule "
                   f"does not match. Same port, wrong path: join on the PRIVATE ip {MASTER_PRIV}. "
                   "Careful with the fix — --tls-san wants the public one.)", "dim"))
        world.flags["k3s_public_ip_trap"] = True
        return
    again = world.flags.get("k3s_joined")
    for h in hosts:
        io.print(c(f"ok: [{h}]", "green") + "  (agent already joined)" if again else
                 c(f"changed: [{h}]", "yellow") + f"  (joined via K3S_URL=https://{join_ip}:6443)")
    io.print("\nPLAY RECAP " + "*" * 48)
    for h, unchanged in ((("skywatch-master", not master_changed),)
                         + tuple((w, again) for w in hosts)):
        io.print(f"{h:<18}: " + c("ok=3" if h == "skywatch-master" else "ok=1", "green") + "  "
                 + (c("changed=0", "green") if unchanged else c("changed=1", "yellow"))
                 + "  failed=0")
    if again:
        io.print(c("\n(changed=0 on a re-run is the whole point of Ansible: it describes the "
                   "DESIRED state, checks what is already true, and only acts on the difference. "
                   "Note the uninstall guard — `systemctl is-active k3s` FIRST. Without it the "
                   "uninstall script, which exists on a healthy node, fires and wipes a live "
                   "cluster on the second run.)", "dim"))
    world.k8s["nodes"] = ["skywatch-master", "skywatch-worker", "skywatch-worker2"]
    world.flags["k3s_joined"] = True
    world.flags["k3s_installed"] = True


def _get_nodes(world, m, io):
    """`kubectl get nodes` runs from the PLAYER's laptop, over the public ip —
    which is the only place a missing `--tls-san` ever shows up."""
    if _cluster_up(world) and world.flags.get("k3s_tls_san") != MASTER_PUB:
        san = world.flags.get("k3s_tls_san") or "none"
        io.print(c(f'E0817 couldn\'t get current server API group list: Get '
                   f'"https://{MASTER_PUB}:6443/api?timeout=32s": tls: failed to verify '
                   f'certificate: x509: certificate is valid for {san}, 127.0.0.1, '
                   f'not {MASTER_PUB}', "red"))
        io.print("Unable to connect to the server: TLS handshake error")
        io.print(c("(your kubeconfig points at the master's PUBLIC ip, and the K3s cert only "
                   "lists what --tls-san named at install time. Put the public ip in --tls-san "
                   "and re-run — the role will reinstall the server to regenerate the cert.)", "dim"))
        world.flags["_noop"] = True
        return
    args = shlex.split(m.group(0))[1:]
    # The engine prints a generic v1.30.0; this cluster is pinned, and a mission
    # that spends a paragraph on the pin has to show the pinned version back.
    if world.k8s and world.k8s["nodes"] and not any(a.startswith("-o") for a in args):
        _table(io, ["NAME", "STATUS", "ROLES", "AGE", "VERSION"],
               [[n, "Ready", "control-plane,master" if i == 0 else "<none>", "6m",
                 world.flags.get("k3s_version", "v1.29.5+k3s1")]
                for i, n in enumerate(world.k8s["nodes"])])
        world.flags["get_nodes"] = True
        world.flags["_noop"] = True
        return
    do_kubectl(world, args, io)


# --------------------------------------------------------------------- helm --
# The catch-all used to answer EVERY helm subcommand with the mission's own
# answer command — wrong (real helm has repositories, and `install` from a
# repo requires one) and a spoiler besides. These are the subcommands that
# belong around an install: the check before it, the prerequisite, and the two
# ways of verifying it afterwards.
CHART_INDEX = {
    "prometheus-community": [
        ("kube-prometheus-stack", "61.3.1", "v0.75.1",
         "kube-prometheus-stack collects Kubernetes manifests, Gr..."),
        ("prometheus", "25.24.1", "v2.53.0",
         "Prometheus is a monitoring system and time series data..."),
    ],
    "grafana": [("grafana", "8.3.2", "11.1.0", "The leading tool for querying and visualizing t...")],
    "bitnami": [("bitnami/rabbitmq", "14.6.6", "3.13.6", "RabbitMQ is an open source general-purpose mess...")],
}
# The canonical URL per repo — a wrong one is not refused (there is no network
# to refuse it), it is named, which is the more useful correction.
REPO_URLS = {
    "prometheus-community": "https://prometheus-community.github.io/helm-charts",
    "grafana": "https://grafana.github.io/helm-charts",
    "bitnami": "https://charts.bitnami.com/bitnami",
    "argo": "https://argoproj.github.io/argo-helm",
    "ingress-nginx": "https://kubernetes.github.io/ingress-nginx",
}


def _repos(world):
    return world.flags.setdefault("helm_repos", {})


def _ns_of(args, default="default"):
    for i, a in enumerate(args):
        if a in ("-n", "--namespace") and i + 1 < len(args):
            return args[i + 1]
        if a.startswith("--namespace="):
            return a.split("=", 1)[1]
    return default


def _helm_repo(world, io, args):
    repos = _repos(world)
    verb = args[0] if args else ""
    if verb == "add" and len(args) >= 3:
        name, url = args[1], args[2]
        if name in repos:
            if repos[name] == url:
                io.print(f'"{name}" already exists with the same configuration, skipping')
            else:
                io.print(c(f"Error: repository name ({name}) already exists, please specify a "
                           "different name", "red"))
                io.print(c("(`helm repo remove` first, or pick another local name — the name is "
                           "just your alias for that URL)", "dim"))
            world.flags["_noop"] = True
            return
        repos[name] = url
        io.print(f'"{name}" has been added to your repositories')
        if name in REPO_URLS and REPO_URLS[name] != url:
            io.print(c(f"(heads up: the published URL for {name} is {REPO_URLS[name]} — the name "
                       "is only a local alias, but the URL has to be the real one)", "dim"))
        else:
            io.print(c("(that only wrote ~/.config/helm/repositories.yaml. Nothing is downloaded "
                       "until `helm repo update` builds the local index.)", "dim"))
        return
    if verb == "remove" and len(args) >= 2:
        if args[1] not in repos:
            io.print(c(f'Error: no repo named "{args[1]}" found', "red"))
            world.flags["_noop"] = True
            return
        del repos[args[1]]
        io.print(f'"{args[1]}" has been removed from your repositories')
        return
    if verb == "update":
        if not repos:
            io.print(c("Error: no repositories found. You must add one before updating", "red"))
            io.print(c("(helm repo add <name> <url> first — update refreshes an index that does "
                       "not exist yet)", "dim"))
            world.flags["_noop"] = True
            return
        io.print("Hang tight while we grab the latest from your chart repositories...")
        for name in repos:
            io.print(f'...Successfully got an update from the "{name}" chart repository')
        io.print("Update Complete. ⎈Happy Helming!⎈")
        world.flags["helm_repo_updated"] = True
        return
    if verb == "list":
        world.flags["_noop"] = True
        if not repos:
            io.print(c("Error: no repositories to show", "red"))
            return
        _table(io, ["NAME", "URL"], [[n, u] for n, u in sorted(repos.items())])
        return
    world.flags["_noop"] = True
    io.print("Usage:\n  helm repo add NAME URL\n  helm repo update [REPO...]\n"
             "  helm repo list\n  helm repo remove NAME")


def _helm_search(world, io, args):
    world.flags["_noop"] = True
    # `search repo <term>` / `search hub <term>` — the first word is the source,
    # not the search term.
    args = args[1:] if args[:1] and args[0] in ("repo", "hub") else args
    term = next((a for a in args if not a.startswith("-")), "")
    rows = [[f"{repo}/{name}".replace(f"{repo}/{repo}/", f"{repo}/"), ver, app, desc]
            for repo in _repos(world) for name, ver, app, desc in CHART_INDEX.get(repo, [])
            if not term or term in name]
    if not rows:
        io.print("No results found")
        io.print(c("(`search repo` reads the LOCAL index built by `helm repo update` — it never "
                   "goes to the network. No repo added, no results.)", "dim"))
        return
    _table(io, ["NAME", "CHART VERSION", "APP VERSION", "DESCRIPTION"], rows)


def _helm_releases(world):
    return world.flags.setdefault("sky_releases", {})


def _helm_list(world, io, args):
    world.flags["_noop"] = True
    ns, all_ns = _ns_of(args), any(a in ("-A", "--all-namespaces") for a in args)
    rows = [[name, r_ns, "1", "deployed", r["chart"], r["app"]]
            for (r_ns, name), r in sorted(_helm_releases(world).items())
            if all_ns or r_ns == ns]
    if not rows:
        io.print(c(f"(no releases in namespace {ns} — releases are namespace-scoped: "
                   "-n <ns> looks elsewhere, -A looks everywhere)", "dim"))
        return
    _table(io, ["NAME", "NAMESPACE", "REVISION", "STATUS", "CHART", "APP VERSION"], rows)


def _helm_status(world, io, args):
    world.flags["_noop"] = True
    ns = _ns_of(args)
    name = next((a for a in args if not a.startswith("-")), None)
    rel = _helm_releases(world).get((ns, name))
    if not rel:
        io.print(c("Error: release: not found", "red"))
        other = [r_ns for (r_ns, n) in _helm_releases(world) if n == name]
        io.print(c(f"(it is in namespace {other[0]} — `helm status {name} -n {other[0]}`)" if other
                   else "(`helm list -A` shows every release this cluster has)", "dim"))
        return
    io.print(f"NAME: {name}\nNAMESPACE: {ns}\nSTATUS: deployed\nREVISION: 1\n"
             f"CHART: {rel['chart']}\nNOTES:\n{rel['notes']}")


def _helm_common(world, io, args):
    """The helm subcommands that surround an install in every mission that has
    one. Returns True when it answered, so each mission's handler only has to
    own its own `install` narrative."""
    sub = args[0] if args else ""
    if not args or sub in ("help", "--help", "-h"):
        world.flags["_noop"] = True
        io.print("The Kubernetes package manager\n\nUsage:\n  helm [command]\n\n"
                 "Available Commands (in this mission):")
        for name, blurb in (("repo", "add / update / list chart repositories"),
                            ("search", "search the local index built by repo update"),
                            ("install", "install a chart into the cluster"),
                            ("list", "releases in a namespace (-A for all of them)"),
                            ("status", "one release's current state and NOTES"),
                            ("uninstall", "remove a release and everything it created"),
                            ("version", "the client version")):
            io.print(f"  {name:<11}{blurb}")
        io.print(c("\n(the full local-chart Helm — template, upgrade, rollback, history, get, "
                   "lint — lives in the ⎈ Helm missions, which ship a chart to point it at)", "dim"))
        return True
    if sub in ("version", "--version", "-v"):
        world.flags["_noop"] = True
        io.print(TOOL_VERSION_LINES["helm"] if "--short" not in args else "v3.15.2+g1a500d5")
        io.print(c("(it answered → helm is installed. That's the check that belongs before any "
                   "`helm install`)", "dim"))
        return True
    if sub == "repo":
        _helm_repo(world, io, args[1:])
        return True
    if sub == "search":
        _helm_search(world, io, args[1:])
        return True
    if sub in ("list", "ls"):
        _helm_list(world, io, args[1:])
        return True
    if sub == "status":
        _helm_status(world, io, args[1:])
        return True
    if sub in ("template", "upgrade", "rollback", "history", "get", "show", "lint", "create",
               "package", "dependency", "pull", "test"):
        world.flags["_noop"] = True
        io.print(f"🌍 `helm {sub}` isn't simulated in this mission.")
        io.print(c("   The full Helm — template, upgrade, rollback, history, get values, lint — "
                   "is in the ⎈ Helm missions, which ship a local chart to run it against. Here "
                   "the chart comes from a repository, and there is no network to fetch it.", "dim"))
        return True
    return False


def _helm_sky(world, m, io):
    line = m.group(0)
    args = line.split()[1:]
    if _helm_common(world, io, args):
        return
    if args[:1] != ["install"]:
        io.print(c(f'Error: unknown command "{args[0]}" for "helm"', "red"))
        io.print(c("(`helm help` lists what this mission answers)", "dim"))
        world.flags["_noop"] = True
        return
    if not _cluster_up(world):
        io.print('Error: INSTALLATION FAILED: Kubernetes cluster unreachable')
        io.print(c("(no cluster yet — terraform, then ansible, THEN helm. Order matters.)", "dim"))
        world.flags["_noop"] = True
        return
    world.k8s["namespaces"].add("skywatch")
    for name, replicas, image in (
            ("skywatch-frontend", 1, "ghcr.io/you/skywatch-frontend:v1"),
            ("skywatch-worker", 2, "ghcr.io/you/skywatch-worker:v1"),
            ("skywatch-rabbitmq", 1, "rabbitmq:3.13-management")):
        world.k8s["deployments"][name] = {"ns": "skywatch", "replicas": replicas,
                                          "image": image, "revision": 1}
    world.k8s["services"]["skywatch-frontend"] = {"ns": "skywatch", "type": "NodePort",
                                                  "port": 5000, "nodePort": 30080,
                                                  "app": "skywatch-frontend"}
    world.k8s["services"]["skywatch-rabbitmq"] = {"ns": "skywatch", "type": "ClusterIP",
                                                  "port": 5672, "app": "skywatch-rabbitmq"}
    _reconcile(world)
    notes = "SkyWatch is rolling out — frontend on NodePort 30080."
    io.print(f"NAME: skywatch\nNAMESPACE: skywatch\nSTATUS: deployed\nREVISION: 1\nNOTES:\n{notes}")
    _helm_releases(world)[("skywatch", "skywatch")] = {
        "chart": "skywatch-0.1.0", "app": "v1", "notes": notes}
    world.flags["helm_sky"] = True


# ------------------------------------------------------------------- argocd --
def _argocd_sky(world, m, io):
    args = m.group(0).split()[1:]
    if args[:2] == ["app", "get"]:
        if not world.flags.get("argo_app"):
            io.print('rpc error: code = NotFound desc = applications.argoproj.io "skywatch" not found')
            io.print(c("(register it first: kubectl apply -f argocd-app.yaml)", "dim"))
            world.flags["_noop"] = True
            return
        io.print("Name:               skywatch\nSync Policy:        Automated (prune, selfHeal)\n"
                 "Sync Status:        Synced to HEAD\nHealth Status:      Healthy")
        world.flags["argo_guarding"] = True
        world.flags["_noop"] = True
    elif args[:1] in (["version"], ["--version"]):
        io.print(TOOL_VERSION_LINES["argocd"])
        io.print(c("(it answered → the CLI is installed. Whether it can reach a server is the "
                   "next question: `argocd app get skywatch`)", "dim"))
        world.flags["_noop"] = True
    else:
        io.print("argocd: try `argocd app get skywatch`")
        world.flags["_noop"] = True


def _apply_argo(world, m, io):
    if not _cluster_up(world):
        io.print("The connection to the server localhost:8080 was refused - did you specify the right host or port?")
        world.flags["_noop"] = True
        return
    io.print("application.argoproj.io/skywatch created")
    io.print(c("(auto-sync is ON: from now on, Git changes deploy themselves — and drift heals)", "dim"))
    world.flags["argo_app"] = True


def _kdelete(world, m, io):
    """Deleting things by hand, and finding out WHICH loop undoes you.

    The note says "delete a pod and self-heal recreates it", and the pod does
    come back — but not from ArgoCD. Two controllers with two jobs is worth ten
    minutes of a student's life, so the answer names them both.
    """
    args = shlex.split(m.group(0))[1:]
    before = {n: dict(d) for n, d in world.k8s["deployments"].items()}
    pods_before = set(world.k8s["pods"])
    do_kubectl(world, args, io)
    gone = [n for n in before if n not in world.k8s["deployments"]]
    if gone and world.flags.get("argo_app"):
        for n in gone:
            world.k8s["deployments"][n] = before[n]
        _reconcile(world)
        io.print("")
        io.print(c(f"ArgoCD  application skywatch  →  OutOfSync (deployment.apps/{gone[0]} missing)", "magenta"))
        io.print(c(f"ArgoCD  automated selfHeal    →  applied {len(gone)} resource(s) from Git → "
                   "Synced · Healthy", "magenta"))
        io.print(c("(THAT one was ArgoCD. selfHeal restores what the manifest in Git says should "
                   "exist — you deleted the object itself, so ArgoCD re-created it. To really "
                   "remove it, delete it from Git and let prune do the work.)", "dim"))
        world.flags["selfhealed"] = True
    elif world.flags.get("pod_deleted_owned") and set(world.k8s["pods"]) != pods_before:
        io.print(c("(the replacement is a NEW pod with a new name and a new ip — and ArgoCD had "
                   "nothing to do with it. The Deployment's ReplicaSet controller counted 3 where "
                   "it wanted 4 and fixed it inside the cluster, in milliseconds. ArgoCD's "
                   "selfHeal guards the MANIFEST: delete the Deployment and ArgoCD is what puts "
                   "it back. Two loops, two jobs — try it.)", "dim"))


def _curl_weather(world, m, io):
    if not world.flags.get("helm_sky") or not _cluster_up(world):
        io.print("curl: (7) Failed to connect to 54.72.21.4 port 30080: Connection refused")
        io.print(c("(is the app deployed? helm first — then curl)", "dim"))
        world.flags["_noop"] = True
        return
    io.print('> GET /?city=Tel+Aviv HTTP/1.1')
    io.print(c("  frontend → publishes to rabbitmq (reply_to + correlation_id)", "dim"))
    io.print(c("  worker   → consumes, calls Open-Meteo, publishes the reply", "dim"))
    io.print(c("  frontend → matches correlation_id, renders:", "dim"))
    io.print("")
    io.print("🌤️  Tel Aviv: 27.8°C, clear sky — SkyWatch is LIVE end to end.")
    world.flags["weather_served"] = True


# ------------------------------------------------- mission 2: observability --
def _prom_crds(world, m, io):
    """`kubectl apply --server-side` on the operator CRDs.

    Server-side apply pushes the merge work to the API server instead of sending
    a full three-way patch — which is what stops a 700 kB CRD from timing out a
    t3.micro. Without --server-side it fails the way it really fails.
    """
    line = m.group(0)
    if "--server-side" not in line:
        io.print(c("The CustomResourceDefinition \"prometheuses.monitoring.coreos.com\" is invalid: "
                   "metadata.annotations: Too long: must have at most 262144 bytes", "red"))
        io.print(c("(client-side apply stores the whole previous manifest in an annotation, and "
                   "these CRDs are far past the 256 kB limit. `--server-side` lets the API server "
                   "own the merge — it is the only way these particular CRDs go in.)", "dim"))
        world.flags["_noop"] = True
        return
    for crd in CRD_LIST:
        io.print(f"customresourcedefinition.apiextensions.k8s.io/{crd}.monitoring.coreos.com "
                 "serverside-applied")
    io.print(c("(one at a time, with a --request-timeout and a breath between them: the api-server "
               "on a 1 GB node needs the room. These ten CRDs are what teach Kubernetes the words "
               "Prometheus, ServiceMonitor and Alertmanager.)", "dim"))
    world.k8s["objects"].setdefault("CustomResourceDefinition", set()).update(
        (f"{crd}.monitoring.coreos.com", "default") for crd in CRD_LIST)
    world.flags["prom_crds"] = True


def _helm_prom(world, m, io):
    line = m.group(0)
    args = line.split()[1:]
    if _helm_common(world, io, args):
        return
    if args[:1] != ["install"]:
        io.print(c(f'Error: unknown command "{args[0]}" for "helm"', "red"))
        io.print(c("(`helm help` lists what this mission answers)", "dim"))
        world.flags["_noop"] = True
        return
    # A chart reference of the form <repo>/<chart> resolves through the LOCAL
    # repository index. No `helm repo add`, no chart — that is the prerequisite
    # the note's install command silently assumes, and skipping it here would
    # teach that charts fall out of the sky. `helm install <release> <chart>`,
    # so the chart is the SECOND positional — and a flag's value is not one.
    takes_value = {"-f", "--values", "-n", "--namespace", "--set", "--version",
                   "--timeout", "--kube-context", "--description"}
    pos, skip = [], False
    for a in args[1:]:
        if skip:
            skip = False
        elif a.startswith("-"):
            skip = a in takes_value
        else:
            pos.append(a)
    ref = pos[1] if len(pos) > 1 else ""
    repo = ref.split("/")[0] if "/" in ref else ""
    if repo and repo not in _repos(world):
        io.print(c(f"Error: repo {repo} not found", "red"))
        io.print(c(f"(a chart reference is <repo-alias>/<chart>, and the alias has to exist "
                   f"locally first: helm repo add {repo} "
                   f"{REPO_URLS.get(repo, 'https://…')} — then helm repo update)", "dim"))
        world.flags["_noop"] = True
        return
    if not world.flags.get("prom_crds"):
        io.print(c("Error: INSTALLATION FAILED: failed to install CRD crds/crd-prometheuses.yaml: "
                   "context deadline exceeded", "red"))
        io.print(c("(Helm bundles this chart's CRDs into the release and applies them client-side. "
                   "They are enormous, the t3.micro api-server takes too long, and the install "
                   "dies half-installed. Apply the CRDs yourself with `kubectl apply "
                   "--server-side` FIRST.)", "dim"))
        world.flags["_noop"] = True
        return
    if "--skip-crds" not in line:
        io.print(c("Error: INSTALLATION FAILED: rendered manifests contain a resource that already "
                   "exists. Unable to continue with install: CustomResourceDefinition "
                   "\"prometheuses.monitoring.coreos.com\" in namespace \"\" exists and cannot be "
                   "imported into the current release: invalid ownership metadata", "red"))
        io.print(c("(you applied those CRDs yourself, so Helm does not own them — and Helm refuses "
                   "to adopt someone else's objects. `--skip-crds` is the other half of the trick; "
                   "you need BOTH halves.)", "dim"))
        world.flags["_noop"] = True
        return
    world.k8s["namespaces"].add("monitoring")
    for name, image in (("prometheus", "quay.io/prometheus/prometheus:v2.52.0"),
                        ("grafana", "grafana/grafana:11.0.0"),
                        ("kube-state-metrics", "registry.k8s.io/kube-state-metrics:v2.12.0")):
        world.k8s["deployments"][name] = {"ns": "monitoring", "replicas": 1,
                                          "image": image, "revision": 1}
    world.k8s["services"]["grafana"] = {"ns": "monitoring", "type": "NodePort", "port": 3000,
                                        "nodePort": 30030, "app": "grafana"}
    world.k8s["services"]["prometheus"] = {"ns": "monitoring", "type": "ClusterIP",
                                           "port": 9090, "app": "prometheus"}
    # The operator's own custom resources. The chart creates them, and they are
    # the proof the CRDs applied a moment ago were not ceremony: `kubectl get
    # prometheus -n monitoring` is a word this cluster only learned today.
    world.k8s["objects"].setdefault("Prometheus", set()).add(
        ("kube-prometheus-stack-prometheus", "monitoring"))
    world.k8s["objects"].setdefault("PrometheusRule", set()).update(
        (n, "monitoring") for n in ("kube-prometheus-stack-kubernetes-apps",
                                    "kube-prometheus-stack-node-exporter"))
    _reconcile(world)
    notes = "Grafana on NodePort 30030 — user admin, password from values.yaml."
    io.print("NAME: kube-prometheus-stack\nNAMESPACE: monitoring\nSTATUS: deployed\nREVISION: 1\n"
             f"NOTES:\n{notes}")
    io.print(c("(alertmanager: enabled: false saved ~100 MB, and both pods are pinned to worker2. "
               "On a 1 GB node the values file is not decoration — it is the difference between "
               "Running and Evicted.)", "dim"))
    _helm_releases(world)[("monitoring", "kube-prometheus-stack")] = {
        "chart": "kube-prometheus-stack-61.3.1", "app": "v0.75.1", "notes": notes}
    world.flags["prom_installed"] = True


def _curl_rabbit(world, m, io):
    """15672 vs 15692 — one character apart, and the flashcard exists because
    everyone points the ServiceMonitor at the management UI first."""
    line = m.group(0)
    port = re.search(r":(\d+)", line)
    port = port.group(1) if port else ""
    if port == "15672":
        io.print("<!doctype html><html><head><title>RabbitMQ Management</title>…")
        io.print(c("(that is the management UI — HTML for humans. Prometheus wants a text exposition "
                   "format, and RabbitMQ serves it somewhere else.)", "dim"))
        world.flags["_noop"] = True
        return
    if port != "15692":
        io.print(f"curl: (7) Failed to connect to skywatch-rabbitmq port {port or '?'}: Connection refused")
        io.print(c("(RabbitMQ listens on 5672 for AMQP, 15672 for the management UI and one more "
                   "for metrics — `kubectl describe svc skywatch-rabbitmq` lists the ports, and "
                   "helm/skywatch/values.yaml has a comment about it.)", "dim"))
        world.flags["_noop"] = True
        return
    io.print("# HELP rabbitmq_queue_messages Sum of ready and unacknowledged messages")
    io.print("# TYPE rabbitmq_queue_messages gauge")
    io.print('rabbitmq_queue_messages{queue="skywatch_jobs"} 3')
    io.print('rabbitmq_queue_messages_ready{queue="skywatch_jobs"} 1')
    io.print("rabbitmq_connections 4")
    io.print(c("(15692 — the rabbitmq_prometheus plugin is enabled by default in the "
               "3.13-management image, so there is nothing to install. Now something has to be "
               "told to SCRAPE it.)", "dim"))
    world.flags["found_15692"] = True
    world.flags["_noop"] = True


def _apply_sm(world, m, io):
    if not world.flags.get("prom_crds"):
        io.print('error: resource mapping not found for name: "skywatch-rabbitmq" namespace: '
                 '"skywatch" from "monitoring/servicemonitor.yaml": no matches for kind '
                 '"ServiceMonitor" in version "monitoring.coreos.com/v1"')
        io.print(c("(ensure CRDs are installed first — a ServiceMonitor is not a built-in kind. "
                   "The CRD is what teaches the api-server the word.)", "dim"))
        world.flags["_noop"] = True
        return
    # Through the engine's own manifest path, so the object it creates carries
    # the spec the player can read back with `describe` — the CRDs taught the
    # api-server this word, and the round trip has to prove it.
    for doc in _parse_manifests(world.files.get("monitoring/servicemonitor.yaml", SERVICEMONITOR)):
        _k8s_apply_doc(world, doc, io)
    io.print(c("(a ServiceMonitor is a Prometheus target, expressed as a Kubernetes object: "
               "select a Service by label, name its port, set an interval. Nobody edits "
               "prometheus.yml any more.)", "dim"))
    world.flags["servicemonitor"] = True


def _grafana(world, m, io):
    if not world.flags.get("prom_installed"):
        io.print("curl: (7) Failed to connect to 54.72.21.4 port 30030: Connection refused")
        io.print(c("(nothing is listening on 30030 yet — install the stack first)", "dim"))
        world.flags["_noop"] = True
        return
    io.print("> GET / HTTP/1.1   (admin / skywatch-grafana)")
    io.print("")
    io.print(c("┌─ Grafana · Kubernetes / Compute Resources / Cluster ───┐", "cyan"))
    io.print(c("│", "cyan") + "  CPU  ▁▂▄▅▄▂▁▂▃  0.41 / 3.00 cores")
    io.print(c("│", "cyan") + "  MEM  ▃▃▄▄▅▅▅▆▆  1.9 GiB / 2.7 GiB")
    io.print(c("│", "cyan") + "  Pods 11 Running · 0 Pending · 0 Failed")
    io.print(c("├─ RabbitMQ Overview ───────────────────────────────────┤", "cyan"))
    if world.flags.get("servicemonitor"):
        io.print(c("│", "cyan") + "  queue depth   ▁▂▁▃▂▁  skywatch_jobs: 3 ready")
        io.print(c("│", "cyan") + "  publish rate  12/s   deliver rate 12/s")
        io.print(c("│", "cyan") + "  target        skywatch/skywatch-rabbitmq:prometheus  "
                 + c("UP", "green"))
        io.print(c("└────────────────────────────────────────────────────────┘", "cyan"))
        io.print(c("(that UP is the whole of Part 4: the broker exports metrics, a ServiceMonitor "
                   "tells Prometheus to scrape them, Grafana draws them. You can now see a queue "
                   "backing up BEFORE a user tells you.)", "dim"))
        world.flags["grafana_ok"] = True
    else:
        io.print(c("│", "cyan") + "  " + c("No data", "yellow")
                 + "   (0 targets matching job=\"skywatch-rabbitmq\")")
        io.print(c("└────────────────────────────────────────────────────────┘", "cyan"))
        io.print(c("(the cluster panels work — those come from kube-state-metrics, which the chart "
                   "installs. RabbitMQ is empty because nothing told Prometheus to scrape it: it "
                   "needs a ServiceMonitor pointing at the broker's metrics port.)", "dim"))
    world.flags["_noop"] = True


MISSIONS = [
    {
        "id": "skywatch-01",
        "topic": "capstone",
        "title": "THE CAMPAIGN 🛰️ — SkyWatch, end to end",
        "vault_note": "SkyWatch Capstone",
        "brief": ("Everything you've learned, one run: DECLARE three machines, CONFIGURE\n"
                  "them into a K3s cluster, SHIP the app with Helm, put ArgoCD on guard,\n"
                  "prove the weather flows through the queue — then tear it all down like\n"
                  "a professional.\n\n"
                  "Fair warning: k3s.yml is the playbook a real person wrote at 2am, and it\n"
                  "has the bug everyone writes. READ the failure when it comes — the answer\n"
                  "is in it. Files are all here (ls · cat · grep · sed -i 's/old/new/' file)."),
        "world": {
            "k8s": {"started": False},
            "files": {
                "main.tf": MAIN_TF,
                "inventory": INVENTORY,
                "k3s.yml": K3S_PLAYBOOK,
                "helm/skywatch/values.yaml": VALUES_YAML,
                "argocd-app.yaml": ARGO_APP,
            },
        },
        "handlers": [
            (r"cat\s+.*", _cat),
            (r"grep\s+.*", _grep),
            (r"sed\s+.*", _sed),
            (r"yq(\s+.*)?", _yq),
            (r"terraform\s+.*", _tf),
            (r"ansible-playbook\s+.*", _ansible_k3s),
            (r"helm\s+.*", _helm_sky),
            (r"argocd\s+.*", _argocd_sky),
            (r"kubectl\s+get\s+nodes.*", _get_nodes),
            (r"kubectl\s+apply\s+-f\s+argocd-app\.yaml", _apply_argo),
            (r"kubectl\s+delete\s+.*", _kdelete),
            (r"curl\s+.*30080.*", _curl_weather),
        ],
        "objectives": [
            {"desc": "Provision: 3 EC2 machines exist (init → apply)", "xp": 20,
             "hint": "terraform init, then terraform apply (the word is 'yes'). cat main.tf to see what you're declaring.",
             "check": lambda w: len(w.flags.get("tf_state", {})) == 3},
            {"desc": "Configure: K3s control plane up on the master", "xp": 15,
             "hint": "ansible-playbook -i inventory k3s.yml — machines must exist first.",
             "check": lambda w: w.flags.get("k3s_master_ok")},
            {"desc": "Join BOTH workers — read the failure before you fix it", "xp": 25,
             "hint": ("the join says 'Connection timed out' on port 6443. cat inventory: the "
                      "master has two addresses. Fix the one in K3S_URL only — sed -i "
                      "'s|K3S_URL=https://54.72.18.3|K3S_URL=https://10.0.1.10|' k3s.yml"),
             "check": lambda w: w.flags.get("k3s_joined")},
            {"desc": "Verify from YOUR laptop: 3 Ready nodes", "xp": 15,
             "hint": "kubectl get nodes. A TLS error here is about --tls-san, not about the join.",
             "check": lambda w: w.flags.get("get_nodes") and w.k8s and len(w.k8s["nodes"]) == 3},
            {"desc": "Ship: SkyWatch installed via Helm (frontend + 2 workers + rabbitmq)", "xp": 25,
             "hint": "helm install skywatch ./helm/skywatch -n skywatch --create-namespace",
             "check": lambda w: w.flags.get("helm_sky")},
            {"desc": "Guard: ArgoCD watches the repo (apply the Application, check it)", "xp": 20,
             "hint": "kubectl apply -f argocd-app.yaml, then argocd app get skywatch.",
             "check": lambda w: w.flags.get("argo_guarding")},
            {"desc": "Kill a pod in the skywatch namespace — and find out what resurrects it", "xp": 20,
             "hint": ("kubectl get pods -n skywatch, then kubectl delete pod <name> -n skywatch. "
                      "Then look again. (Braver: delete the whole deployment.)"),
             "check": lambda w: (w.flags.get("get_pods")
                                 and (w.flags.get("pod_deleted_owned") or w.flags.get("selfhealed")))},
            {"desc": "USE IT: get a forecast through the whole pipeline", "xp": 25,
             "hint": "curl http://54.72.21.4:30080/?city=Tel+Aviv — watch the message take the full round trip.",
             "check": lambda w: w.flags.get("weather_served")},
            {"desc": "Tear down: leave AWS exactly as you found it", "xp": 20,
             "hint": "terraform destroy (yes) — the session is over, the billing must be too.",
             "check": lambda w: w.flags.get("tf_destroyed") and not w.flags.get("tf_state")},
        ],
        "teach": [
            "Infrastructure as Code: three machines exist because a FILE says so — reproducible on any AWS account.",
            "Provision (Terraform) then configure (Ansible) — two tools, two jobs, one pipeline. The uninstall task "
            "is guarded by `systemctl is-active` FIRST, or a second run wipes a live cluster.",
            "Inside a VPC, use PRIVATE ips. A packet sent to a public ip leaves via the Internet Gateway and no "
            "longer matches the security group's self-rule — the port looks closed and nothing in the error says so.",
            "--tls-san decides whose name is on the certificate, and the certificate is minted at INSTALL time. "
            "Public ip for the cert, private ip for the join: the same host, two different paths.",
            "One helm install shipped frontend + workers + broker as a unit — this is why charts exist.",
            "ArgoCD with selfHeal means the cluster now defends its own desired state — Git rules from here.",
            "Two control loops, not one: the ReplicaSet controller replaces missing PODS, ArgoCD replaces missing "
            "MANIFESTS. Knowing which one answered you is how you debug 'it came back on its own'.",
            "The forecast crossed: HTTP → queue → worker → API → reply queue → browser. Decoupled, scalable, yours.",
            "destroy is part of the job — pros leave no orphaned infra and no surprise bills.",
        ],
        "solution": [
            "terraform init",
            "terraform apply",
            "yes",
            "ansible-playbook -i inventory k3s.yml",
            "cat inventory",
            "sed -i 's|K3S_URL=https://54.72.18.3|K3S_URL=https://10.0.1.10|' k3s.yml",
            "ansible-playbook -i inventory k3s.yml",
            "kubectl get nodes",
            "helm install skywatch ./helm/skywatch -n skywatch --create-namespace",
            "kubectl get pods -n skywatch",
            "kubectl apply -f argocd-app.yaml",
            "argocd app get skywatch",
            "kubectl delete pod skywatch-worker -n skywatch",
            "kubectl get pods -n skywatch",
            "curl http://54.72.21.4:30080/?city=Tel+Aviv",
            "terraform destroy",
            "yes",
        ],
    },
    {
        "id": "skywatch-02",
        "topic": "capstone",
        "title": "Eyes on the Sky 📊 — Prometheus, Grafana and one UP target",
        "vault_note": "SkyWatch Capstone",
        "brief": ("Part 4 of the capstone, the part everyone skips because the app already\n"
                  "'works'. The cluster is back up and SkyWatch is serving — you just cannot\n"
                  "SEE anything. Install kube-prometheus-stack on nodes with 1 GB of RAM,\n"
                  "then teach Prometheus to scrape RabbitMQ and prove it in Grafana.\n\n"
                  "Two traps live here, and the note says you need BOTH halves of the fix.\n"
                  "Files: helm/monitoring/values.yaml · monitoring/crds.yaml · "
                  "monitoring/servicemonitor.yaml"),
        "world": {
            "k8s": {"started": True,
                    "nodes": ["skywatch-master", "skywatch-worker", "skywatch-worker2"],
                    "namespaces": ["skywatch", "argocd"],
                    "deployments": {
                        "skywatch-frontend": {"ns": "skywatch", "replicas": 1,
                                              "image": "ghcr.io/you/skywatch-frontend:v1"},
                        "skywatch-worker": {"ns": "skywatch", "replicas": 2,
                                            "image": "ghcr.io/you/skywatch-worker:v1"},
                        "skywatch-rabbitmq": {"ns": "skywatch", "replicas": 1,
                                              "image": "rabbitmq:3.13-management"}},
                    "services": {"skywatch-rabbitmq": {"ns": "skywatch", "type": "ClusterIP",
                                                       "port": 5672, "app": "skywatch-rabbitmq"}}},
            "files": {
                "helm/monitoring/values.yaml": PROM_VALUES,
                "helm/skywatch/values.yaml": VALUES_YAML,
                "monitoring/crds.yaml": "# 10 CustomResourceDefinitions, 1.4 MB of them.\n"
                                        "# Read the sizes before you decide how to apply this:\n"
                                        + "".join(f"#   {crd}.monitoring.coreos.com\n"
                                                  for crd in CRD_LIST),
                "monitoring/servicemonitor.yaml": SERVICEMONITOR,
            },
            "flags": {"helm_sky": True, "argo_app": True},
        },
        "handlers": [
            (r"cat\s+.*", _cat),
            (r"grep\s+.*", _grep),
            (r"sed\s+.*", _sed),
            (r"yq(\s+.*)?", _yq),
            (r"kubectl\s+apply\s+.*crds?\.yaml.*", _prom_crds),
            (r"kubectl\s+apply\s+.*servicemonitor\.yaml.*", _apply_sm),
            (r"helm\s+.*", _helm_prom),
            (r"curl\s+.*30030.*", _grafana),
            (r"curl\s+.*", _curl_rabbit),
        ],
        "objectives": [
            {"desc": "Read the slim values — know what you turned off and why", "xp": 10,
             "hint": "cat helm/monitoring/values.yaml — alertmanager, retention, limits, nodeSelector.",
             "check": lambda w: "helm/monitoring/values.yaml" in w.flags.get("read", set())},
            {"desc": "Get the operator CRDs in — the way enormous CRDs have to go in", "xp": 20,
             "hint": "kubectl apply --server-side --request-timeout=3m -f monitoring/crds.yaml",
             "check": lambda w: w.flags.get("prom_crds")},
            {"desc": "Install kube-prometheus-stack WITHOUT re-installing those CRDs", "xp": 25,
             "hint": ("the chart lives in a repository, so add it first: helm repo add "
                      "prometheus-community https://prometheus-community.github.io/helm-charts "
                      "then helm repo update. Then: helm install kube-prometheus-stack "
                      "prometheus-community/kube-prometheus-stack -n monitoring "
                      "--create-namespace --skip-crds -f helm/monitoring/values.yaml"),
             "check": lambda w: w.flags.get("prom_installed")},
            {"desc": "Find RabbitMQ's metrics port yourself — it is not the one you think", "xp": 15,
             "hint": ("the 3.13-management image serves AMQP, a UI and metrics on three different "
                      "ports. Try them: curl http://skywatch-rabbitmq:15672/metrics — then the "
                      "other one. helm/skywatch/values.yaml has a comment."),
             "check": lambda w: w.flags.get("found_15692")},
            {"desc": "Tell Prometheus to scrape it: apply the ServiceMonitor", "xp": 20,
             "hint": "cat monitoring/servicemonitor.yaml, then kubectl apply -f monitoring/servicemonitor.yaml",
             "check": lambda w: w.flags.get("servicemonitor")},
            {"desc": "Prove it in Grafana: cluster panels AND an UP RabbitMQ target", "xp": 20,
             "hint": "curl http://54.72.21.4:30030 — worker2's NodePort, admin / skywatch-grafana.",
             "check": lambda w: w.flags.get("grafana_ok")},
        ],
        "teach": [
            "Observability values are capacity planning: alertmanager off saves ~100 MB, retention "
            "24h saves disk, and nodeSelector keeps the heavy pods off a tainted 1 GB master.",
            "Client-side apply stashes the previous manifest in an annotation capped at 256 kB — "
            "operator CRDs blow straight past it. --server-side moves the merge into the api-server.",
            "--skip-crds is the other half: you own those CRDs now, and Helm refuses to adopt "
            "objects it did not create. Both halves, or neither works.",
            "5672 is AMQP, 15672 is the management UI, 15692 is Prometheus — and the plugin is on "
            "by default in 3.13-management, so there is nothing to install, only to scrape.",
            "A ServiceMonitor is a scrape config expressed as a Kubernetes object: select a Service "
            "by label, name its port, set an interval. The operator rewrites prometheus.yml for you.",
            "A dashboard you have never looked at is not observability. UP on the target list is the "
            "checkable fact; the graph is what makes a queue backing up visible before a user calls.",
        ],
        "solution": [
            "ls",
            "cat helm/monitoring/values.yaml",
            "helm repo add prometheus-community https://prometheus-community.github.io/helm-charts",
            "helm repo update",
            "helm search repo kube-prometheus-stack",
            "helm install kube-prometheus-stack prometheus-community/kube-prometheus-stack -n monitoring --create-namespace --skip-crds -f helm/monitoring/values.yaml",
            "kubectl apply --server-side --request-timeout=3m -f monitoring/crds.yaml",
            "kubectl get crd",
            "helm install kube-prometheus-stack prometheus-community/kube-prometheus-stack -n monitoring --create-namespace --skip-crds -f helm/monitoring/values.yaml",
            "helm list -n monitoring",
            "kubectl get pods -n monitoring",
            "curl http://skywatch-rabbitmq:15672/metrics",
            "curl http://skywatch-rabbitmq:15692/metrics",
            "cat monitoring/servicemonitor.yaml",
            "kubectl apply -f monitoring/servicemonitor.yaml",
            "kubectl get servicemonitor -n skywatch",
            "curl http://54.72.21.4:30030",
        ],
    },
]
