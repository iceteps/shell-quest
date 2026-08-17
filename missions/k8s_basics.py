"""Kubernetes missions — mirror the course's K8s classes AND the three REAL
graded assignments: the CLI assignment (YAML provided), the Core Resources &
RBAC homework, and the Day-2 Ops & Resilience extension.

kubectl itself is engine-native; the handlers here only wrap it. Two of them
exist because the engine's flags answer "was this listed?" and the assignments
ask "was this listed AFTER you changed something?" — see _forget_listings."""
import json
import shlex

from engine import c, do_kubectl


def _kargs(m):
    """The words the engine would have received. dispatch() has already proved
    the line is shlex-safe, so quotes can be stripped the same way it does —
    `kubectl apply -f "my file.yaml"` must not break just because a handler
    stands in the middle."""
    return shlex.split(m.group(1))


def _forget_listings(world):
    """Clear every `get_*` flag after a command that CHANGED the cluster.

    The graded flow is survey → deploy → verify → tear down → verify again, and
    the same engine flag answers all four times: `kubectl get pods -n kube-system`
    in step 2 sets `get_pods` long before the app exists. Without this, the
    verification objectives would fire on the `apply` itself and the teardown
    proof would be free. A listing from before the change proves nothing about
    the change — so the player has to look again."""
    for key in [k for k in world.flags if k.startswith("get_")]:
        del world.flags[key]


def _listed(world, kind, all_ns=False):
    """Did the player list `kind` since the last change? `kubectl get pods` and
    `kubectl get deploy,pods,svc` are the same proof, but the engine keys its
    flag on the whole comma-joined list (`get_deployments_pods_services`) — so
    ask by word rather than by exact key, or a legitimate route loses."""
    for key in world.flags:
        if not key.startswith("get_"):
            continue
        parts = key.split("_")[1:]
        if kind in parts and (not all_ns or parts[-1] == "A"):
            return True
    return False


def _change(world, m, io):
    """apply/delete: run the real thing, then make the player re-verify."""
    do_kubectl(world, _kargs(m), io)
    _forget_listings(world)


# Shared by all three missions: the rule is the same everywhere — you verify
# AFTER you change, never before.
VERIFY_AFTER_CHANGE = [
    (r"kubectl\s+(apply\s+.*)", _change),
    (r"kubectl\s+(delete\s+.*)", _change),
]


def _reread(world):
    """Forget every LOOK the player has taken — listings and `describe` alike.

    Day-2 ops is one loop: change something, then look again. `_forget_listings`
    only clears `get_*`, and half of this mission's looking is `describe` — a
    player who described the deployment BEFORE hardening it would otherwise
    satisfy "read the probes back" without ever reading anything back."""
    _forget_listings(world)
    for key in [k for k in world.flags if k.startswith("describe_")]:
        del world.flags[key]


def _day2(world, m, io):
    """Anything that CHANGES the deployment: run it, then make every proof stale."""
    do_kubectl(world, _kargs(m), io)
    _reread(world)


_SVC_ALIASES = ("service", "services", "svc")


def _patch(world, m, io):
    """`kubectl patch` — change ONE field of a LIVE object, which is what the
    debugging challenge's "fix it without deleting the namespace" means in
    practice.

    Only a Service's selector is simulated: it is the field the planted bug hides
    in, and a general patch engine would promise far more than this world can
    keep. Every other target says so plainly instead of printing `patched` over
    a change that never happened."""
    args = _kargs(m)
    kind = name = body = None
    ns = "default"
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-n", "--namespace") and i + 1 < len(args):
            ns = args[i + 1]; i += 2; continue
        if a in ("-p", "--patch") and i + 1 < len(args):
            body = args[i + 1]; i += 2; continue
        if a.startswith(("-p=", "--patch=")):
            body = a.split("=", 1)[1]; i += 1; continue
        if a.startswith("-"):                    # --type=merge and friends
            i += 1; continue
        if kind is None:
            kind, _, name = a.partition("/")     # `patch svc/demo-svc` is one word
            name = name or None
        elif name is None:
            name = a
        i += 1
    if kind and kind not in _SVC_ALIASES:
        io.print(f"kubectl patch: patching {kind} isn't simulated here")
        io.print(c("(this world patches a Service's selector — the field this bug hides in. For "
                   "every other field the route that always works is the one your teammates can "
                   "review anyway: fix the manifest, then kubectl apply -f it.)", "dim"))
        world.flags["_noop"] = True
        return
    if not name:
        io.print("error: exactly one NAME is required, got 0")
        io.print(c("(kubectl patch service <name> -n <ns> -p '<json>')", "dim"))
        world.flags["_noop"] = True
        return
    svc = world.k8s["services"].get(name)
    if not svc or svc.get("ns") != ns:
        io.print(f'Error from server (NotFound): services "{name}" not found')
        io.print(c("(-n picks the namespace; without it kubectl patches in `default`)", "dim"))
        world.flags["_noop"] = True
        return
    if body is None:
        io.print("error: at least one patch must be specified")
        io.print(c("(the patch is a fragment of the object itself: "
                   "-p '{\"spec\":{\"selector\":{\"app\":\"demo\"}}}')", "dim"))
        world.flags["_noop"] = True
        return
    try:
        doc = json.loads(body)
    except ValueError:
        io.print(f'error: unable to parse "{body}": invalid JSON')
        io.print(c("(wrap the whole patch in SINGLE quotes and use double quotes inside it — JSON "
                   "has no single-quoted strings, and the shell would eat the braces bare)", "dim"))
        world.flags["_noop"] = True
        return
    app = ((doc.get("spec") or {}).get("selector") or {}).get("app") if isinstance(doc, dict) else None
    if not app:
        io.print(c("(no spec.selector.app in that patch — that is the one field this world patches. "
                   "The shape kubectl expects is a slice of the real object: "
                   "'{\"spec\":{\"selector\":{\"app\":\"demo\"}}}')", "dim"))
        world.flags["_noop"] = True
        return
    if svc.get("app") == app:
        # kubectl's own wording when a patch asks for what is already true — the
        # same converging behaviour `apply` has, on a single field.
        io.print(f"service/{name} patched (no change)")
        world.flags["_noop"] = True
        return
    svc["app"] = app
    io.print(f"service/{name} patched")
    _reread(world)


def _edit(world, m, io):
    """`kubectl edit` is everyone's reflex for "fix it in place" — and it opens
    $EDITOR on the live object, which a scripted world has nowhere to put. Say
    so, then point at the two routes that do work here."""
    io.print('error: unable to launch the editor "vi": this simulation has no interactive editor')
    io.print(c("(same fix, non-interactively: kubectl patch service <name> -n <ns> "
               "-p '{\"spec\":{\"selector\":{\"app\":\"demo\"}}}'. Or correct the YAML file and "
               "kubectl apply -f it — `edit` changes a live object and leaves no trace in git.)", "dim"))
    world.flags["_noop"] = True


# Day-2 is change → look again, so every mutating verb has to invalidate the
# looking. `set image` and `rollout undo` are here for the same reason apply and
# delete are in VERIFY_AFTER_CHANGE.
DAY2_OPS = [
    (r"kubectl\s+(apply\s+.*)", _day2),
    (r"kubectl\s+(delete\s+.*)", _day2),
    (r"kubectl\s+(set\s+image\s+.*)", _day2),
    (r"kubectl\s+(scale\s+.*)", _day2),
    (r"kubectl\s+(rollout\s+undo\s+.*)", _day2),
    (r"kubectl\s+patch\s*(.*)", _patch),
    (r"kubectl\s+edit(\s+.*)?", _edit),
]


def _version(world, m, io):
    """`kubectl version --client` is step 1 of the graded assignment, but the
    engine answers it as pure inspection and records nothing — right for the
    engine, invisible to an objective. Delegate for the real output, then note
    that it happened."""
    do_kubectl(world, _kargs(m), io)
    world.flags["kubectl_version_checked"] = True


_STRATEGY_ALIASES = (["deploy"], ["deployment"], ["deployments"])

# kubectl v1.30's real output for the two fields this class hinges on, verbatim
# in shape and wording (the descriptions are the API's own, trimmed at sentence
# boundaries — never reworded, so a student can match it against a real cluster).
_EXPLAIN_STRATEGY = {
    (): ("FIELD: strategy <DeploymentStrategy>\n\n"
         "DESCRIPTION:\n"
         "    The deployment strategy to use to replace existing pods with new ones.\n"
         "    DeploymentStrategy describes how to replace existing pods with new ones.\n\n"
         "FIELDS:\n"
         "  rollingUpdate\t<RollingUpdateDeployment>\n"
         "    Rolling update config params. Present only if DeploymentStrategyType =\n"
         "    RollingUpdate.\n\n"
         "  type\t<string>\n"
         "    enum: Recreate, RollingUpdate\n"
         '    Type of deployment. Can be "Recreate" or "RollingUpdate". Default is\n'
         "    RollingUpdate.",
         "(no docs site, no browser: explain reads the API server's own schema. Go one level "
         "deeper — kubectl explain deployment.spec.strategy.rollingUpdate — and "
         "maxSurge/maxUnavailable are waiting there.)"),
    ("rollingupdate",): ("FIELD: rollingUpdate <RollingUpdateDeployment>\n\n"
                         "DESCRIPTION:\n"
                         "    Rolling update config params. Present only if DeploymentStrategyType =\n"
                         "    RollingUpdate.\n"
                         "    Spec to control the desired behavior of rolling update.\n\n"
                         "FIELDS:\n"
                         "  maxSurge\t<IntOrString>\n"
                         "    The maximum number of pods that can be scheduled above the desired number\n"
                         "    of pods. Value can be an absolute number (ex: 5) or a percentage of\n"
                         "    desired pods (ex: 10%). This can not be 0 if MaxUnavailable is 0.\n"
                         "    Defaults to 25%.\n\n"
                         "  maxUnavailable\t<IntOrString>\n"
                         "    The maximum number of pods that can be unavailable during the update.\n"
                         "    Value can be an absolute number (ex: 5) or a percentage of desired pods\n"
                         "    (ex: 10%). This can not be 0 if MaxSurge is 0. Defaults to 25%.",
                         "(maxSurge: 1 + maxUnavailable: 0 is the zero-downtime pair the Day-2 "
                         "assignment asks for — one extra pod may start, but not one serving pod "
                         "may go away.)"),
}


def _explain(world, m, io):
    """`kubectl explain` is the note's "power move for this class", and the
    engine's generic answer is a signpost rather than documentation. For the
    fields this mission is about, print what kubectl v1.30 really prints; hand
    every other path back to the engine rather than invent field docs."""
    path = m.group(1).lower().split(".")
    strategy = path[:1] in _STRATEGY_ALIASES and path[1:3] == ["spec", "strategy"]
    doc = _EXPLAIN_STRATEGY.get(tuple(path[3:])) if strategy else None
    if doc:
        body, note = doc
        io.print("GROUP:      apps\nKIND:       Deployment\nVERSION:    v1\n\n" + body)
        io.print(c(note, "dim"))
        world.flags["_noop"] = True          # reading documentation isn't a move
    else:
        do_kubectl(world, ["explain", m.group(1)], io)
    if strategy:
        world.flags["explain_strategy"] = True


BACKEND_DEPLOY = '''apiVersion: apps/v1
kind: Deployment
metadata:
  name: backend
spec:
  replicas: 1
  selector:
    matchLabels:
      app: backend
  template:
    metadata:
      labels:
        app: backend
    spec:
      containers:
        - name: backend
          image: nginxdemos/hello
          ports:
            - containerPort: 80
'''

BACKEND_SVC = '''apiVersion: v1
kind: Service
metadata:
  name: backend
spec:
  selector:
    app: backend
  ports:
    - port: 80
      targetPort: 80
  type: ClusterIP
'''

FRONTEND_DEPLOY = BACKEND_DEPLOY.replace("backend", "frontend").replace("nginxdemos/hello", "nginx:alpine")
FRONTEND_SVC = BACKEND_SVC.replace("backend", "frontend").replace("ClusterIP", "NodePort")

NAMESPACE_YAML = '''apiVersion: v1
kind: Namespace
metadata:
  name: dev
'''

CONFIGMAP_YAML = '''apiVersion: v1
kind: ConfigMap
metadata:
  name: app-config
  namespace: dev
data:
  ENV: dev
  LOG_LEVEL: debug
'''

SECRET_YAML = '''apiVersion: v1
kind: Secret
metadata:
  name: app-secret
  namespace: dev
type: Opaque
data:
  password: cGFzc3dvcmQ=
'''

# The homework's part 2: a Pod written by hand, with nothing above it. It exists
# to be deleted — the counter-example that makes self-healing mean something.
POD_YAML = '''apiVersion: v1
kind: Pod
metadata:
  name: demo-pod
  namespace: dev
  labels:
    app: demo
spec:
  containers:
    - name: demo
      image: nginx
      ports:
        - containerPort: 80
'''

# Part 5: the same selector three times, so the ONLY difference a student can
# see in `kubectl get services` is the TYPE column (and what it costs them).
SERVICE_TYPES_YAML = '''apiVersion: v1
kind: Service
metadata:
  name: app-clusterip
  namespace: dev
spec:
  type: ClusterIP
  selector:
    app: demo
  ports:
    - port: 80
      targetPort: 80
---
apiVersion: v1
kind: Service
metadata:
  name: app-nodeport
  namespace: dev
spec:
  type: NodePort
  selector:
    app: demo
  ports:
    - port: 80
      targetPort: 80
      nodePort: 30080
---
apiVersion: v1
kind: Service
metadata:
  name: app-loadbalancer
  namespace: dev
spec:
  type: LoadBalancer
  selector:
    app: demo
  ports:
    - port: 80
      targetPort: 80
'''

SA_YAML = '''apiVersion: v1
kind: ServiceAccount
metadata:
  name: app-sa
  namespace: dev
'''

ROLE_YAML = '''apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: pod-reader
  namespace: dev
rules:
  - apiGroups: [""]
    resources: ["pods"]
    verbs: ["get", "list"]
'''

BINDING_YAML = '''apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: pod-reader-binding
  namespace: dev
subjects:
  - kind: ServiceAccount
    name: app-sa
    namespace: dev
roleRef:
  kind: Role
  name: pod-reader
  apiGroup: rbac.authorization.k8s.io
'''


# The Day-2 assignment's own container spec, one file: probes, requests/limits
# and the zero-downtime strategy the same YAML pins (maxSurge 1, maxUnavailable
# 0 — the doc's prose says "1 unavailable", its YAML says 0; the YAML is what
# the graded question is about). failureThreshold is deliberately absent: it is
# Kubernetes' default 3, and `describe` is where a student should meet it.
HARDENED_DEPLOY = '''apiVersion: apps/v1
kind: Deployment
metadata:
  name: app-deployment
  namespace: dev
spec:
  replicas: 3
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 0
  selector:
    matchLabels:
      app: demo
  template:
    metadata:
      labels:
        app: demo
    spec:
      containers:
        - name: app
          image: nginx:alpine
          ports:
            - containerPort: 80
          resources:
            requests:
              memory: "64Mi"
              cpu: "100m"
            limits:
              memory: "128Mi"
              cpu: "200m"
          livenessProbe:
            httpGet:
              path: /
              port: 80
            initialDelaySeconds: 5
            periodSeconds: 5
          readinessProbe:
            httpGet:
              path: /
              port: 80
            initialDelaySeconds: 2
            periodSeconds: 5
'''

# Planted error #2 of the graded debugging challenge, exactly as the assignment
# describes it: selector `app: demo-app`, pods labelled `app: demo`. Nothing in
# the file marks it — a comment saying "wrong" would hand over the answer the
# student is supposed to find in `kubectl get endpoints`.
BROKEN_SVC = '''apiVersion: v1
kind: Service
metadata:
  name: demo-svc
  namespace: dev
spec:
  type: ClusterIP
  selector:
    app: demo-app
  ports:
    - port: 80
      targetPort: 80
'''


def _deploy(world):
    """The deployment this whole mission hardens — {} before it exists, so the
    objective checks can ask about fields without guarding every access."""
    return (world.k8s or {}).get("deployments", {}).get("app-deployment", {})


MISSIONS = [
    {
        "id": "k8s-01",
        "topic": "k8s",
        "title": "First Contact ☸️ — the REAL CLI assignment",
        "vault_note": "Class 05 - Kubernetes",
        "brief": ("This mission mirrors the graded 'Kubernetes Basics – CLI Assignment':\n"
                  "you got a k8s/ folder with four YAML files (ls to see them — read them,\n"
                  "don't change them). Check your tools, start a local cluster, inspect it,\n"
                  "deploy everything with ONE command, verify, open the frontend in a\n"
                  "browser — and tear it all down again. CLI only, no Helm."),
        "world": {
            "k8s": {},
            "files": {
                "backend-deployment.yaml": BACKEND_DEPLOY,
                "backend-service.yaml": BACKEND_SVC,
                "frontend-deployment.yaml": FRONTEND_DEPLOY,
                "frontend-service.yaml": FRONTEND_SVC,
            },
        },
        "handlers": [(r"kubectl\s+(version.*)", _version)] + VERIFY_AFTER_CHANGE,
        "objectives": [
            {"desc": "Prove the tools are installed — before there's a cluster to talk to", "xp": 10,
             "hint": "kubectl version --client asks the BINARY (no cluster needed); "
                     "minikube version asks the other one.",
             "check": lambda w: w.flags.get("kubectl_version_checked") and w.flags.get("minikube_version")},
            {"desc": "Start a local Kubernetes cluster", "xp": 10,
             "hint": "The course uses minikube. One word after it.",
             "check": lambda w: w.k8s and w.k8s["started"]},
            {"desc": "Inspect the cluster: control plane + nodes", "xp": 10,
             "hint": "kubectl cluster-info shows the control plane; kubectl get nodes shows the machines.",
             "check": lambda w: w.flags.get("cluster_info") and w.flags.get("get_nodes")},
            {"desc": "Survey it: the namespaces, the pods it runs for ITSELF, every Service", "xp": 15,
             "hint": "kubectl get namespaces · kubectl get pods -n kube-system · "
                     "kubectl get services -A  (-A = every namespace at once)",
             "check": lambda w: (_listed(w, "namespaces") and w.flags.get("get_pods_system")
                                 and _listed(w, "services", all_ns=True))},
            {"desc": "Deploy ALL four YAML files with one command", "xp": 25,
             "hint": "From inside the folder: kubectl apply -f .  (the dot = every manifest here)",
             "check": lambda w: w.k8s and "backend" in w.k8s["deployments"] and "frontend" in w.k8s["deployments"]},
            {"desc": "Verify both apps landed: the deployments, then the pods they made", "xp": 10,
             "hint": "kubectl get deployments, then kubectl get pods — READY x/x is the column that matters.",
             "check": lambda w: (_listed(w, "deployments") and _listed(w, "pods")
                                 and w.k8s and len(w.k8s["pods"]) >= 2)},
            {"desc": "Check the services — which one is reachable from outside?", "xp": 15,
             "hint": "kubectl get services — compare the TYPE column: ClusterIP vs NodePort.",
             "check": lambda w: _listed(w, "services") and w.k8s and "frontend" in w.k8s["services"]},
            {"desc": "Open the frontend in your browser", "xp": 20,
             "hint": "minikube has a one-word subcommand that opens a Service for you: minikube service <name>.",
             "check": lambda w: w.flags.get("minikube_service_frontend")},
            {"desc": "Read a pod's own logs", "xp": 10,
             "hint": "kubectl logs <pod> — a name prefix is enough here: kubectl logs frontend. "
                     "(kubectl get pods prints the full names.)",
             "check": lambda w: w.flags.get("logs_pod")},
            {"desc": "Tear the whole stack down with one command — and prove the cluster is empty", "xp": 20,
             "hint": "kubectl delete -f . removes exactly what the folder created. Then list "
                     "deployments, pods and services again — 'No resources found' IS the deliverable.",
             "check": lambda w: (w.k8s and not w.k8s["deployments"] and not w.k8s["pods"]
                                 and not w.k8s["services"] and _listed(w, "deployments")
                                 and _listed(w, "pods") and _listed(w, "services"))},
        ],
        "teach": [
            "`--version`/`version --client` answers from the BINARY alone — it separates "
            "'not installed' from 'installed but not connected to anything'.",
            "minikube start boots a one-node cluster — kubectl talks to it from that second on.",
            "cluster-info + get nodes: the two-second health check before touching anything.",
            "kube-system is Kubernetes running ITSELF — CoreDNS, the API server, kube-proxy. And "
            "-n/-A is the difference between 'nothing is running' and 'nothing is running HERE'.",
            "`apply -f .` is declarative — k8s reads the desired state and makes it real.",
            "You applied Deployments, not Pods — the pods were created FOR you. That's the management chain.",
            "The TYPE column decides reachability: ClusterIP = internal-only, NodePort = a door to the outside.",
            "minikube service opens NodePort services in a browser — the screenshot the assignment wants.",
            "logs = what the container printed. describe = why it never printed anything. Reach for "
            "logs when it RAN, describe when it didn't.",
            "delete -f . is apply's mirror: same file, opposite verb. The pods went with their "
            "Deployments — nothing healed them, because the thing that heals them is what you deleted.",
        ],
        "solution": [
            "kubectl version --client",
            "minikube version",
            "minikube start",
            "kubectl cluster-info",
            "kubectl get nodes",
            "kubectl get namespaces",
            "kubectl get pods -n kube-system",
            "kubectl get services -A",
            "kubectl apply -f .",
            "kubectl get deployments",
            "kubectl get pods",
            "kubectl get services",
            "minikube service frontend",
            "kubectl logs frontend",
            "kubectl delete -f .",
            "kubectl get deployments",
            "kubectl get pods",
            "kubectl get services",
        ],
    },
    {
        "id": "k8s-02",
        "topic": "k8s",
        "title": "Break It, Watch It Heal 🩹",
        "vault_note": "Class 05 - Kubernetes",
        "brief": ("A deployment named app-deployment is running somewhere in this cluster —\n"
                  "but `kubectl get pods` says there's nothing. Find it, then do the thing\n"
                  "everyone remembers from class: DELETE a pod and watch Kubernetes bring\n"
                  "it back. Then do it to a BARE pod (pod.yaml) and watch nothing happen —\n"
                  "that contrast is the whole lesson. Then scale and roll out a new image."),
        "world": {
            "k8s": {
                "started": True,
                "namespaces": ["dev"],
                "deployments": {"app-deployment": {"ns": "dev", "replicas": 3, "image": "nginx"}},
            },
            "files": {"pod.yaml": POD_YAML},
        },
        "handlers": [(r"kubectl\s+explain\s+(\S+)", _explain)] + VERIFY_AFTER_CHANGE,
        "objectives": [
            {"desc": "Find the pods (they're hiding in plain sight)", "xp": 15,
             "hint": "Everything in K8s is namespace-scoped. If a list is empty, ask: which NAMESPACE? (-n)",
             "check": lambda w: _listed(w, "pods")},
            {"desc": "Delete ONE pod — then prove the count healed back to 3", "xp": 25,
             "hint": "kubectl delete pod <name> -n dev, then list again. The ReplicaSet notices count < desired…",
             "check": lambda w: w.flags.get("pod_deleted_owned")
                                and w.k8s and sum(1 for p in w.k8s["pods"].values()
                                                  if p.get("deploy") == "app-deployment") == 3},
            {"desc": "Apply pod.yaml — a BARE Pod, with no Deployment above it", "xp": 10,
             "hint": "cat pod.yaml first (no replicas, no template — it IS the pod), "
                     "then kubectl apply -f pod.yaml.",
             "check": lambda w: w.k8s and "demo-pod" in w.k8s["pods"]},
            {"desc": "Delete demo-pod — and prove nobody brings this one back", "xp": 20,
             "hint": "kubectl delete pod demo-pod -n dev, then list the pods again. "
                     "Compare with what happened a few commands ago.",
             "check": lambda w: (w.k8s and "demo-pod" not in w.k8s["pods"]
                                 and "pod.yaml" in w.flags.get("applied", set())
                                 and _listed(w, "pods"))},
            {"desc": "Scale the deployment to 5 replicas — without editing YAML", "xp": 15,
             "hint": "kubectl scale deployment app-deployment --replicas=5 -n dev",
             "check": lambda w: w.flags.get("scaled_app-deployment") == 5},
            {"desc": "Roll out a new image version", "xp": 20,
             "hint": "kubectl set image deployment/app-deployment app=nginx:1.27 -n dev "
                     "(this is why K8s creates a NEW ReplicaSet)",
             "check": lambda w: w.flags.get("set_image_app-deployment")},
            {"desc": "Ask the tool itself what governs that rollout", "xp": 10,
             "hint": "kubectl explain deployment.spec.strategy — offline documentation for ANY "
                     "field of ANY resource, straight from the API server's schema.",
             "check": lambda w: w.flags.get("explain_strategy")},
            {"desc": "Describe a pod and read its Events (debug gold)", "xp": 10,
             "hint": "kubectl describe pod <name-or-prefix> -n dev — the Events at the bottom tell the story.",
             "check": lambda w: w.flags.get("describe_pod")},
        ],
        "teach": [
            "An empty list ≠ nothing running — everything is namespace-scoped. Make -n (or -A) a reflex.",
            "You deleted a pod and the count healed: the ReplicaSet reconciles actual→desired. Self-healing is a LOOP, not magic.",
            "A Pod you write by hand has nothing watching it — no Deployment, no ReplicaSet, no "
            "declared count. It is the only object here with no controller above it.",
            "Same command, opposite outcome: the owned pod came back, the bare one stayed dead. "
            "Self-healing isn't a property of Pods — it belongs to the controller above them.",
            "scale edits desired state; the reconcile loop does the labor. No YAML file was harmed.",
            "set image rolls pods via a NEW ReplicaSet — the old one is kept, which is exactly how rollback works.",
            "`kubectl explain <resource>.<field>` beats any blog: it documents the exact API version "
            "your cluster runs, offline, and it is how you learn fields nobody told you about.",
            "describe's Events section is where Kubernetes tells you WHY — read it before googling.",
        ],
        "solution": [
            "kubectl get pods",
            "kubectl get pods -n dev",
            "kubectl delete pod app-deployment -n dev",
            "kubectl get pods -n dev",
            "cat pod.yaml",
            "kubectl apply -f pod.yaml",
            "kubectl get pods -n dev",
            "kubectl delete pod demo-pod -n dev",
            "kubectl get pods -n dev",
            "kubectl scale deployment app-deployment --replicas=5 -n dev",
            "kubectl set image deployment/app-deployment app=nginx:1.27 -n dev",
            "kubectl explain deployment.spec.strategy",
            "kubectl describe pod app-deployment -n dev",
        ],
    },
    {
        "id": "k8s-03",
        "topic": "k8s",
        "title": "Locked Down 🛡️ — the REAL RBAC homework",
        "vault_note": "Class 05 - Kubernetes",
        "brief": ("The 'Core Resources & RBAC' homework as a mission. The YAML files are\n"
                  "all here (ls). Build the dev namespace, feed it config + secrets, expose\n"
                  "an app three different ways, then wire the RBAC trio — ServiceAccount,\n"
                  "Role, RoleBinding — and PROVE the permission exists… then break it and\n"
                  "prove it's gone. That yes→no flip is what RBAC understanding feels like."),
        "world": {
            "k8s": {"started": True},
            "files": {
                "namespace.yaml": NAMESPACE_YAML,
                "configmap.yaml": CONFIGMAP_YAML,
                "secret.yaml": SECRET_YAML,
                "service-types.yaml": SERVICE_TYPES_YAML,
                "serviceaccount.yaml": SA_YAML,
                "role.yaml": ROLE_YAML,
                "rolebinding.yaml": BINDING_YAML,
            },
        },
        "handlers": VERIFY_AFTER_CHANGE,
        "objectives": [
            {"desc": "Create the dev namespace", "xp": 10,
             "hint": "kubectl apply -f namespace.yaml (or the imperative shortcut: kubectl create namespace dev)",
             "check": lambda w: w.k8s and "dev" in w.k8s["namespaces"]},
            {"desc": "Apply the ConfigMap AND the Secret into dev", "xp": 15,
             "hint": "kubectl apply -f configmap.yaml, then the same for secret.yaml. "
                     "(Remember: that Secret is base64, NOT encrypted.)",
             "check": lambda w: w.k8s and ("app-config", "dev") in w.k8s["objects"].get("ConfigMap", set())
                                and ("app-secret", "dev") in w.k8s["objects"].get("Secret", set())},
            {"desc": "Expose the app three ways — ClusterIP, NodePort, LoadBalancer", "xp": 15,
             "hint": "service-types.yaml holds all three (one file, three docs separated by ---). "
                     "Apply it, then kubectl get services -n dev and read TYPE + EXTERNAL-IP.",
             "check": lambda w: (w.k8s and _listed(w, "services")
                                 and {s["type"] for s in w.k8s["services"].values()}
                                 >= {"ClusterIP", "NodePort", "LoadBalancer"})},
            {"desc": "Wire the RBAC trio: ServiceAccount + Role + RoleBinding", "xp": 20,
             "hint": "Apply serviceaccount.yaml, role.yaml, rolebinding.yaml — identity, permissions, glue.",
             "check": lambda w: w.k8s and ("app-sa", "dev") in w.k8s["rbac"]["sa"]
                                and "pod-reader" in w.k8s["rbac"]["roles"]
                                and "pod-reader-binding" in w.k8s["rbac"]["bindings"]},
            {"desc": "PROVE the ServiceAccount can read pods (answer: yes)", "xp": 20,
             "hint": "kubectl auth can-i get pods --as=system:serviceaccount:dev:app-sa -n dev",
             "check": lambda w: w.flags.get("can_i") == "yes"},
            {"desc": "Delete the RoleBinding, prove the permission is GONE (answer: no)", "xp": 25,
             "hint": "kubectl delete rolebinding pod-reader-binding -n dev — then run the same can-i again.",
             "check": lambda w: w.flags.get("binding_deleted") and w.flags.get("can_i") == "no"},
        ],
        "teach": [
            "Namespaces are logical walls — cheap isolation, and the unit RBAC operates within.",
            "ConfigMap = plain config, Secret = base64 (encoding, NOT encryption) — both decouple config from images.",
            "One selector, three doors: ClusterIP is cluster-internal, NodePort opens a port in "
            "30000–32767 on every node, LoadBalancer asks the CLOUD for an IP — which minikube "
            "can't do, so its EXTERNAL-IP sits at <pending> forever. And all three select "
            "app: demo, a label no pod here carries: a Service is a selector, not a pod.",
            "ServiceAccount = identity, Role = permissions, RoleBinding = the glue. No binding, no access.",
            "auth can-i answers permission questions WITHOUT attempting the action — audit tool #1.",
            "Deleting only the BINDING revoked access — identity and permissions still exist; the grant was the glue.",
        ],
        "solution": [
            "kubectl apply -f namespace.yaml",
            "kubectl apply -f configmap.yaml",
            "kubectl apply -f secret.yaml",
            "kubectl apply -f service-types.yaml",
            "kubectl get services -n dev",
            "kubectl apply -f serviceaccount.yaml",
            "kubectl apply -f role.yaml",
            "kubectl apply -f rolebinding.yaml",
            "kubectl auth can-i get pods --as=system:serviceaccount:dev:app-sa -n dev",
            "kubectl delete rolebinding pod-reader-binding -n dev",
            "kubectl auth can-i get pods --as=system:serviceaccount:dev:app-sa -n dev",
        ],
    },
    {
        "id": "k8s-04",
        "topic": "k8s",
        "title": "Day-2 Ops 🚑 — probes, a bad tag, and a planted bug",
        "vault_note": "Class 05 - Kubernetes",
        "brief": ("The 'Day-2 Ops & Resilience' extension assignment. Your dev namespace\n"
                  "from last week is still running app-deployment — three pods, no probes,\n"
                  "no resource requests, nothing watching its health. Harden it, then\n"
                  "deliberately ship a broken image tag and watch whether your users\n"
                  "notice. Roll it back. Finally: someone left a Service in dev that\n"
                  "returns 503 to everything. Find the bug and fix it WITHOUT deleting\n"
                  "the namespace."),
        "world": {
            "k8s": {
                "started": True,
                "namespaces": ["dev"],
                "deployments": {"app-deployment": {
                    "ns": "dev", "replicas": 3, "image": "nginx:alpine",
                    "app": "demo", "container": "app", "containerPort": 80}},
                "services": {"demo-svc": {"ns": "dev", "type": "ClusterIP",
                                          "port": 80, "app": "demo-app"}},
            },
            "files": {
                "app-deployment.yaml": HARDENED_DEPLOY,
                "demo-service.yaml": BROKEN_SVC,
            },
        },
        "handlers": DAY2_OPS,
        "objectives": [
            {"desc": "Inspect what you inherited: the pods, and the deployment's pod template", "xp": 10,
             "hint": "kubectl get pods -n dev, then kubectl describe deployment app-deployment -n dev "
                     "— look for Limits, Requests, Liveness and Readiness lines. They aren't there.",
             "check": lambda w: _listed(w, "pods") and w.flags.get("describe_deploy_app-deployment")},
            {"desc": "Harden it: apply the manifest with probes AND resource requests/limits", "xp": 20,
             "hint": "cat app-deployment.yaml first — it's the assignment's own container spec — "
                     "then kubectl apply -f app-deployment.yaml.",
             "check": lambda w: (_deploy(w).get("probes", {}).get("readiness")
                                 and _deploy(w).get("probes", {}).get("liveness")
                                 and _deploy(w).get("resources", {}).get("requests")
                                 and _deploy(w).get("resources", {}).get("limits"))},
            {"desc": "Read the hardened template back — including the rollout policy it set", "xp": 15,
             "hint": "kubectl describe deployment app-deployment -n dev again. RollingUpdateStrategy, "
                     "Limits/Requests, and the two probe lines (with the #failure kubectl fills in for you).",
             "check": lambda w: (w.flags.get("describe_deploy_app-deployment")
                                 and str(_deploy(w).get("strategy", {}).get("maxUnavailable")) == "0")},
            {"desc": "Ship a deliberately broken release: an image tag that does not exist", "xp": 20,
             "hint": "kubectl set image deployment/app-deployment app=nginx:1.9999 -n dev "
                     "(the assignment's own tag — nothing has ever published it)",
             "check": lambda w: w.flags.get("imagepull_failed_app-deployment")},
            {"desc": "Prove the users never noticed: the rollout stalls, the old pods keep serving", "xp": 15,
             "hint": "kubectl rollout status deployment/app-deployment -n dev, then "
                     "kubectl get deployments -n dev (compare READY with UP-TO-DATE) and "
                     "kubectl get pods -n dev — count Running vs ImagePullBackOff.",
             "check": lambda w: (w.flags.get("rollout_status_app-deployment") == "stuck"
                                 and _listed(w, "pods"))},
            {"desc": "Read the failure from the cluster itself, not from a guess", "xp": 15,
             "hint": "kubectl describe pod app-deployment -n dev (a name prefix is enough) and read "
                     "the Events at the bottom — or kubectl get events -n dev for the whole "
                     "namespace at once. Try kubectl logs on it too; that answer teaches as much.",
             # `get events` reads the SAME Event objects describe prints. It is
             # the command people actually reach for when they don't yet know
             # which object is unhappy, so it has to win here too.
             "check": lambda w: ((w.flags.get("describe_pod") or _listed(w, "events"))
                                 and _deploy(w).get("stuck"))},
            {"desc": "Undo the bad rollout and prove the deployment is healthy again", "xp": 20,
             "hint": "kubectl rollout undo deployment/app-deployment -n dev, then "
                     "kubectl rollout history … and kubectl get pods -n dev.",
             "check": lambda w: (not _deploy(w).get("stuck") and _deploy(w).get("image") != "nginx:1.9999"
                                 and w.flags.get("imagepull_failed_app-deployment")
                                 and _listed(w, "pods"))},
            {"desc": "The planted bug: reproduce the 503, then find which pods demo-svc reaches", "xp": 15,
             "hint": "From inside a pod: kubectl exec deploy/app-deployment -n dev -- curl http://demo-svc. "
                     "Then kubectl get endpoints -n dev (and kubectl describe service demo-svc -n dev).",
             "check": lambda w: w.flags.get("curl_refused") and _listed(w, "endpoints")},
            {"desc": "Fix it in place — no namespace deleted — and prove the endpoints appear", "xp": 20,
             "hint": "kubectl describe service demo-svc -n dev prints both sides of the mismatch — "
                     "the selector it hunts for, and the labels that actually exist in dev "
                     "(cat demo-service.yaml shows where it came from). Then patch the live object: "
                     "kubectl patch service demo-svc -n dev -p '{\"spec\":{\"selector\":{\"app\":\"demo\"}}}' "
                     "and list the endpoints again.",
             "check": lambda w: (w.k8s["services"].get("demo-svc", {}).get("app") == "demo"
                                 and _listed(w, "endpoints"))},
        ],
        "teach": [
            "`describe deployment` prints the pod TEMPLATE. No Requests, no Limits and no probe "
            "lines is what 'not production-ready' looks like in output: the scheduler is guessing "
            "at this pod's size and nothing is checking whether it is alive.",
            "Readiness gates TRAFFIC — fail it and the pod is pulled out of its Service's endpoints, "
            "no restart. Liveness triggers RESTARTS — fail it failureThreshold times and the kubelet "
            "kills the container. Requests are what the scheduler packs nodes with, limits are the "
            "ceiling the kernel enforces: requests below limits is what lets a node be filled "
            "without lying about how much room is left.",
            "maxSurge: 1 + maxUnavailable: 0 is the zero-downtime contract in two numbers — one "
            "EXTRA pod may start, not one serving pod may go away. A rollout that never becomes "
            "Ready therefore cannot take the app down; it just stalls.",
            "kubectl accepted that instantly because it only recorded desired state. A tag nobody "
            "published fails much later, on the node, as ImagePullBackOff — the API accepting your "
            "YAML is never proof that the app works.",
            "UP-TO-DATE 1 while READY stays 3/3 is a rollout stalling safely: the new ReplicaSet "
            "can't produce a Ready pod, so maxUnavailable: 0 refuses to retire an old one. No "
            "downtime by policy, not by luck — and progressDeadlineSeconds is what finally turns "
            "the wait into an error CI can fail on.",
            "`logs` needs a container that ran; `describe` explains a container that never started. "
            "An ImagePullBackOff pod has no logs at all — its Events are the only witness, and the "
            "last Message names the exact tag the registry could not resolve.",
            "`rollout undo` re-points the Deployment at the previous ReplicaSet, which was never "
            "deleted — that is why rollback is instant and why `rollout history` has two revisions "
            "to show you. Keeping the old ReplicaSet costs nothing; it is the undo button.",
            "A Service is a label selector, not a link to a Deployment. `kubectl get endpoints` is "
            "the one command that answers 'does this Service reach ANY pod?' — and <none> has "
            "exactly three causes: the selector matches no labels, no pod is Ready, or a readiness "
            "probe is knocking on a port nothing listens on.",
            "You patched the live object, not the file that created it — the next `kubectl apply "
            "-f demo-service.yaml` puts the bug straight back. A surgical patch is the right "
            "emergency fix; correcting the manifest is what stops it happening twice.",
        ],
        "solution": [
            "kubectl get pods -n dev",
            "kubectl describe deployment app-deployment -n dev",
            "cat app-deployment.yaml",
            "kubectl apply -f app-deployment.yaml",
            "kubectl describe deployment app-deployment -n dev",
            "kubectl set image deployment/app-deployment app=nginx:1.9999 -n dev",
            "kubectl rollout status deployment/app-deployment -n dev",
            "kubectl get deployments -n dev",
            "kubectl get pods -n dev",
            # `describe pod` and `logs` take a NAME PREFIX and resolve ties by
            # sorted name — which here is the ImagePullBackOff pod, the one whose
            # Events (and missing logs) are the point. On a live cluster you copy
            # the full name out of the `get pods` above.
            "kubectl describe pod app-deployment -n dev",
            "kubectl logs app-deployment -n dev",
            "kubectl rollout undo deployment/app-deployment -n dev",
            "kubectl rollout history deployment/app-deployment -n dev",
            "kubectl get pods -n dev",
            "kubectl exec deploy/app-deployment -n dev -- curl http://demo-svc",
            "kubectl get endpoints -n dev",
            "kubectl describe service demo-svc -n dev",
            "cat demo-service.yaml",
            "kubectl patch service demo-svc -n dev -p '{\"spec\":{\"selector\":{\"app\":\"demo\"}}}'",
            # The curl comes BEFORE the endpoints listing on purpose: that listing
            # completes the last objective and ends the mission, and "it serves
            # now" is the proof worth watching in demo mode.
            "kubectl exec deploy/app-deployment -n dev -- curl http://demo-svc",
            "kubectl get endpoints -n dev",
        ],
    },
]
