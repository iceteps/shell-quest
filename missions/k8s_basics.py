"""Kubernetes missions — mirror the course's K8s classes AND the two REAL
graded assignments: the CLI assignment (YAML provided) and the Core
Resources & RBAC homework."""

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


MISSIONS = [
    {
        "id": "k8s-01",
        "topic": "k8s",
        "title": "First Contact ☸️ — the REAL CLI assignment",
        "vault_note": "Class 05 - Kubernetes",
        "brief": ("This mission mirrors the graded 'Kubernetes Basics – CLI Assignment':\n"
                  "you got a k8s/ folder with four YAML files (ls to see them — read them,\n"
                  "don't change them). Start a local cluster, inspect it, deploy everything\n"
                  "with ONE command, verify, and open the frontend in a browser.\n"
                  "Rules from the real assignment: CLI only, no Helm."),
        "world": {
            "k8s": {},
            "files": {
                "backend-deployment.yaml": BACKEND_DEPLOY,
                "backend-service.yaml": BACKEND_SVC,
                "frontend-deployment.yaml": FRONTEND_DEPLOY,
                "frontend-service.yaml": FRONTEND_SVC,
            },
        },
        "objectives": [
            {"desc": "Start a local Kubernetes cluster", "xp": 10,
             "hint": "The course uses minikube. One word after it.",
             "check": lambda w: w.k8s and w.k8s["started"]},
            {"desc": "Inspect the cluster: control plane + nodes", "xp": 10,
             "hint": "kubectl cluster-info shows the control plane; kubectl get nodes shows the machines.",
             "check": lambda w: w.flags.get("cluster_info") and w.flags.get("get_nodes")},
            {"desc": "Deploy ALL four YAML files with one command", "xp": 25,
             "hint": "From inside the folder: kubectl apply -f .  (the dot = every manifest here)",
             "check": lambda w: w.k8s and "backend" in w.k8s["deployments"] and "frontend" in w.k8s["deployments"]},
            {"desc": "Verify the pods are Running", "xp": 10,
             "hint": "kubectl get pods",
             "check": lambda w: w.flags.get("get_pods") and w.k8s and len(w.k8s["pods"]) >= 2},
            {"desc": "Check the services — which one is reachable from outside?", "xp": 15,
             "hint": "kubectl get services — compare the TYPE column: ClusterIP vs NodePort.",
             "check": lambda w: w.flags.get("get_services")},
            {"desc": "Open the frontend in your browser", "xp": 20,
             "hint": "minikube has a one-word subcommand that opens a Service for you: minikube service <name>.",
             "check": lambda w: w.flags.get("minikube_service_frontend")},
        ],
        "solution": [
            "minikube start",
            "kubectl cluster-info",
            "kubectl get nodes",
            "kubectl apply -f .",
            "kubectl get pods",
            "kubectl get services",
            "minikube service frontend",
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
                  "it back. Then scale it and roll out a new image like the real homework."),
        "world": {
            "k8s": {
                "started": True,
                "namespaces": ["dev"],
                "deployments": {"app-deployment": {"ns": "dev", "replicas": 3, "image": "nginx"}},
            },
        },
        "objectives": [
            {"desc": "Find the pods (they're hiding in plain sight)", "xp": 15,
             "hint": "Everything in K8s is namespace-scoped. If a list is empty, ask: which NAMESPACE? (-n)",
             "check": lambda w: w.flags.get("get_pods")},
            {"desc": "Delete ONE pod — then prove the count healed back to 3", "xp": 25,
             "hint": "kubectl delete pod <name> -n dev, then list again. The ReplicaSet notices count < desired…",
             "check": lambda w: w.flags.get("pod_deleted_owned")
                                and w.k8s and sum(1 for p in w.k8s["pods"].values()
                                                  if p.get("deploy") == "app-deployment") == 3},
            {"desc": "Scale the deployment to 5 replicas — without editing YAML", "xp": 15,
             "hint": "kubectl scale deployment app-deployment --replicas=5 -n dev",
             "check": lambda w: w.flags.get("scaled_app-deployment") == 5},
            {"desc": "Roll out a new image version", "xp": 20,
             "hint": "kubectl set image deployment/app-deployment app=nginx:1.27 -n dev "
                     "(this is why K8s creates a NEW ReplicaSet)",
             "check": lambda w: w.flags.get("set_image_app-deployment")},
            {"desc": "Describe a pod and read its Events (debug gold)", "xp": 10,
             "hint": "kubectl describe pod <name-or-prefix> -n dev — the Events at the bottom tell the story.",
             "check": lambda w: w.flags.get("describe_pod")},
        ],
        "solution": [
            "kubectl get pods",
            "kubectl get pods -n dev",
            "kubectl delete pod app-deployment -n dev",
            "kubectl get pods -n dev",
            "kubectl scale deployment app-deployment --replicas=5 -n dev",
            "kubectl set image deployment/app-deployment app=nginx:1.27 -n dev",
            "kubectl describe pod app-deployment -n dev",
        ],
    },
    {
        "id": "k8s-03",
        "topic": "k8s",
        "title": "Locked Down 🛡️ — the REAL RBAC homework",
        "vault_note": "Class 05 - Kubernetes",
        "brief": ("The 'Core Resources & RBAC' homework, part 8, as a mission. The YAML\n"
                  "files are all here (ls). Build the dev namespace, feed it config +\n"
                  "secrets, then wire the RBAC trio — ServiceAccount, Role, RoleBinding —\n"
                  "and PROVE the permission exists… then break it and prove it's gone.\n"
                  "That yes→no flip is what RBAC understanding feels like."),
        "world": {
            "k8s": {"started": True},
            "files": {
                "namespace.yaml": NAMESPACE_YAML,
                "configmap.yaml": CONFIGMAP_YAML,
                "secret.yaml": SECRET_YAML,
                "serviceaccount.yaml": SA_YAML,
                "role.yaml": ROLE_YAML,
                "rolebinding.yaml": BINDING_YAML,
            },
        },
        "objectives": [
            {"desc": "Create the dev namespace", "xp": 10,
             "hint": "kubectl apply -f namespace.yaml (or the imperative shortcut: kubectl create namespace dev)",
             "check": lambda w: w.k8s and "dev" in w.k8s["namespaces"]},
            {"desc": "Apply the ConfigMap AND the Secret into dev", "xp": 15,
             "hint": "kubectl apply -f configmap.yaml, then the same for secret.yaml. "
                     "(Remember: that Secret is base64, NOT encrypted.)",
             "check": lambda w: w.k8s and ("app-config", "dev") in w.k8s["objects"].get("ConfigMap", set())
                                and ("app-secret", "dev") in w.k8s["objects"].get("Secret", set())},
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
        "solution": [
            "kubectl apply -f namespace.yaml",
            "kubectl apply -f configmap.yaml",
            "kubectl apply -f secret.yaml",
            "kubectl apply -f serviceaccount.yaml",
            "kubectl apply -f role.yaml",
            "kubectl apply -f rolebinding.yaml",
            "kubectl auth can-i get pods --as=system:serviceaccount:dev:app-sa -n dev",
            "kubectl delete rolebinding pod-reader-binding -n dev",
            "kubectl auth can-i get pods --as=system:serviceaccount:dev:app-sa -n dev",
        ],
    },
]
