"""THE CAMPAIGN — the SkyWatch capstone as one continuous mission.
Terraform provisions, Ansible installs K3s, Helm ships the app, ArgoCD guards
it, RabbitMQ carries the weather, and terraform destroy pays the bill.
Reuses the terraform handler; everything else is campaign-local."""
from engine import _reconcile, c
from missions.terraform_infra import _tf

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

INVENTORY = '''[master]
skywatch-master

[workers]
skywatch-worker
skywatch-worker2
'''

K3S_PLAYBOOK = '''---
- name: install K3s control plane
  hosts: master
  become: true
  tasks:
    - name: install k3s server (pinned version)
      shell: curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION=v1.29.5+k3s1 sh -

- name: join the workers
  hosts: workers
  become: true
  tasks:
    - name: join with K3S_URL + K3S_TOKEN (env vars, PRIVATE ip!)
      shell: curl -sfL https://get.k3s.io | K3S_URL=https://master:6443 K3S_TOKEN=xxx sh -s - agent
'''

VALUES_YAML = '''frontend:
  image: ghcr.io/you/skywatch-frontend
  tag: v1
worker:
  replicas: 2
  image: ghcr.io/you/skywatch-worker
rabbitmq:
  image: rabbitmq:3.13-management
'''

ARGO_APP = '''apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: skywatch
  namespace: argocd
spec:
  source:
    repoURL: https://github.com/you/skywatch
    path: helm/skywatch
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
'''


def _cluster_up(world):
    return world.k8s and world.k8s["started"]


def _ansible_k3s(world, m, io):
    line = m.group(0)
    if "k3s.yml" not in line:
        io.print("ERROR! the playbook: could not be found (try: ansible-playbook -i inventory k3s.yml)")
        return
    if not world.flags.get("tf_state"):
        io.print(c("fatal: [skywatch-master]: UNREACHABLE! => ssh: connect to host: No route to host", "red"))
        io.print(c("(there are no machines yet — terraform hasn't applied. Provision first.)", "dim"))
        return
    io.print("\nPLAY [install K3s control plane] " + "*" * 26)
    io.print(c("changed: [skywatch-master]", "yellow") + "  (k3s v1.29.5+k3s1 installed, tls-san set)")
    io.print("\nPLAY [join the workers] " + "*" * 35)
    for h in ("skywatch-worker", "skywatch-worker2"):
        io.print(c(f"changed: [{h}]", "yellow") + "  (agent joined via PRIVATE ip)")
    io.print("\nPLAY RECAP " + "*" * 48)
    for h in ("skywatch-master", "skywatch-worker", "skywatch-worker2"):
        io.print(f"{h:<18}: " + c("ok=2", "green") + "  " + c("changed=1", "yellow") + "  failed=0")
    world.k8s["started"] = True
    world.k8s["nodes"] = ["skywatch-master", "skywatch-worker", "skywatch-worker2"]
    world.flags["k3s_installed"] = True


def _helm_sky(world, m, io):
    line = m.group(0)
    args = line.split()[1:]
    if args[:1] != ["install"]:
        io.print("helm: in this campaign, the move is: helm install skywatch ./helm/skywatch -n skywatch --create-namespace")
        return
    if not _cluster_up(world):
        io.print('Error: INSTALLATION FAILED: Kubernetes cluster unreachable')
        io.print(c("(no cluster yet — terraform, then ansible, THEN helm. Order matters.)", "dim"))
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
    _reconcile(world)
    io.print("NAME: skywatch\nNAMESPACE: skywatch\nSTATUS: deployed\nREVISION: 1\n"
             "NOTES:\nSkyWatch is rolling out — frontend on NodePort 30080.")
    world.flags["helm_sky"] = True


def _argocd_sky(world, m, io):
    args = m.group(0).split()[1:]
    if args[:2] == ["app", "get"]:
        if not world.flags.get("argo_app"):
            io.print('rpc error: code = NotFound desc = applications.argoproj.io "skywatch" not found')
            io.print(c("(register it first: kubectl apply -f argocd-app.yaml)", "dim"))
            return
        io.print("Name:               skywatch\nSync Policy:        Automated (prune, selfHeal)\n"
                 "Sync Status:        Synced to HEAD\nHealth Status:      Healthy")
        world.flags["argo_guarding"] = True
    else:
        io.print("argocd: try `argocd app get skywatch`")


def _apply_argo(world, m, io):
    if not _cluster_up(world):
        io.print("The connection to the server localhost:8080 was refused - did you specify the right host or port?")
        return
    io.print("application.argoproj.io/skywatch created")
    io.print(c("(auto-sync is ON: from now on, Git changes deploy themselves — and drift heals)", "dim"))
    world.flags["argo_app"] = True


def _curl_weather(world, m, io):
    if not world.flags.get("helm_sky") or not _cluster_up(world):
        io.print("curl: (7) Failed to connect to 192.168.49.2 port 30080: Connection refused")
        io.print(c("(is the app deployed? helm first — then curl)", "dim"))
        return
    io.print('> GET /?city=Tel+Aviv HTTP/1.1')
    io.print(c("  frontend → publishes to rabbitmq (reply_to + correlation_id)", "dim"))
    io.print(c("  worker   → consumes, calls Open-Meteo, publishes the reply", "dim"))
    io.print(c("  frontend → matches correlation_id, renders:", "dim"))
    io.print("")
    io.print("🌤️  Tel Aviv: 27.8°C, clear sky — SkyWatch is LIVE end to end.")
    world.flags["weather_served"] = True


MISSIONS = [
    {
        "id": "skywatch-01",
        "topic": "capstone",
        "title": "THE CAMPAIGN 🛰️ — SkyWatch, end to end",
        "vault_note": "SkyWatch Capstone",
        "brief": ("Everything you've learned, one run: DECLARE three machines, CONFIGURE\n"
                  "them into a K3s cluster, SHIP the app with Helm, put ArgoCD on guard,\n"
                  "prove the weather flows through the queue — then tear it all down like\n"
                  "a professional. The files are all here (ls). Order matters. Good luck. 🫡"),
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
            (r"terraform\s+.*", _tf),
            (r"ansible-playbook\s+.*", _ansible_k3s),
            (r"helm\s+.*", _helm_sky),
            (r"argocd\s+.*", _argocd_sky),
            (r"kubectl\s+apply\s+-f\s+argocd-app\.yaml", _apply_argo),
            (r"curl\s+.*30080.*", _curl_weather),
        ],
        "objectives": [
            {"desc": "Provision: 3 EC2 machines exist (init → apply)", "xp": 20,
             "hint": "terraform init, then terraform apply (the word is 'yes'). cat main.tf to see what you're declaring.",
             "check": lambda w: len(w.flags.get("tf_state", {})) == 3},
            {"desc": "Configure: K3s installed, workers joined (ansible)", "xp": 20,
             "hint": "ansible-playbook -i inventory k3s.yml — machines must exist first.",
             "check": lambda w: w.flags.get("k3s_installed")},
            {"desc": "Verify: 3 Ready nodes", "xp": 10,
             "hint": "kubectl get nodes",
             "check": lambda w: w.flags.get("get_nodes") and w.k8s and len(w.k8s["nodes"]) == 3},
            {"desc": "Ship: SkyWatch installed via Helm (frontend + 2 workers + rabbitmq)", "xp": 25,
             "hint": "helm install skywatch ./helm/skywatch -n skywatch --create-namespace",
             "check": lambda w: w.flags.get("helm_sky")},
            {"desc": "Prove the pods run — in THEIR namespace", "xp": 10,
             "hint": "kubectl get pods -n skywatch (4 pods: 1 frontend, 2 workers, 1 broker)",
             "check": lambda w: w.flags.get("get_pods")
                                and w.k8s and sum(1 for p in w.k8s["pods"].values()
                                                  if p["ns"] == "skywatch") >= 4},
            {"desc": "Guard: ArgoCD watches the repo (apply the Application, check it)", "xp": 20,
             "hint": "kubectl apply -f argocd-app.yaml, then argocd app get skywatch.",
             "check": lambda w: w.flags.get("argo_guarding")},
            {"desc": "USE IT: get a forecast through the whole pipeline", "xp": 25,
             "hint": "curl http://192.168.49.2:30080/?city=Tel+Aviv — watch the message take the full round trip.",
             "check": lambda w: w.flags.get("weather_served")},
            {"desc": "Tear down: leave AWS exactly as you found it", "xp": 20,
             "hint": "terraform destroy (yes) — the session is over, the billing must be too.",
             "check": lambda w: w.flags.get("tf_destroyed") and not w.flags.get("tf_state")},
        ],
        "teach": [
            "Infrastructure as Code: three machines exist because a FILE says so — reproducible on any AWS account.",
            "Provision (Terraform) then configure (Ansible) — two tools, two jobs, one pipeline. Workers join via PRIVATE ip.",
            "kubectl doesn't care it's K3s on EC2 — the API is the API. Three Ready nodes = a real cluster.",
            "One helm install shipped frontend + workers + broker as a unit — this is why charts exist.",
            "Four pods in their own namespace — apps get walls, and -n is still the reflex.",
            "ArgoCD with selfHeal means the cluster now defends its own desired state — Git rules from here.",
            "The forecast crossed: HTTP → queue → worker → API → reply queue → browser. Decoupled, scalable, yours.",
            "destroy is part of the job — pros leave no orphaned infra and no surprise bills.",
        ],
        "solution": [
            "terraform init",
            "terraform apply",
            "yes",
            "ansible-playbook -i inventory k3s.yml",
            "kubectl get nodes",
            "helm install skywatch ./helm/skywatch -n skywatch --create-namespace",
            "kubectl get pods -n skywatch",
            "kubectl apply -f argocd-app.yaml",
            "argocd app get skywatch",
            "curl http://192.168.49.2:30080/?city=Tel+Aviv",
            "terraform destroy",
            "yes",
        ],
    },
]
