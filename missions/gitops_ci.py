"""GitOps / CI-CD mission — the class-8 loop: push code, CI builds + bumps
the tag, ArgoCD syncs the cluster to what Git says. Mission-local handlers
simulate the pipeline and argocd; git + kubectl come from the engine."""
from engine import c, do_git, _reconcile

APP_PY = '''from flask import Flask
app = Flask(__name__)

@app.route("/")
def home():
    return "SkyWatch v1"
'''

VALUES_YAML = '''# GitOps repo desired state — ArgoCD watches THIS file
image:
  repository: ghcr.io/you/skywatch
  tag: v1
replicas: 2
'''


def _git_push(world, m, io):
    """Real push side-effects + the CI robot: build → push image → bump tag."""
    line = m.group(0)
    do_git(world, line.split()[1:], io)
    g = world.git
    if not g or "main" not in g["pushed"]:
        return
    if len(g["commits"]) < 2:
        io.print(c("(push went up, but nothing new was committed — the pipeline only runs on new commits)", "dim"))
        return
    if world.flags.get("ci_ran"):
        return
    io.print("")
    io.print(c("┌─ GitHub Actions · ci.yaml ─────────────────────────────┐", "blue"))
    io.print(c("│", "blue") + " ✓ lint       flake8 app.py                    (4s)")
    io.print(c("│", "blue") + " ✓ build      docker build -t skywatch:v2      (41s)")
    io.print(c("│", "blue") + " ✓ push       ghcr.io/you/skywatch:v2          (12s)")
    io.print(c("│", "blue") + " ✓ bump-tag   yq -i '.image.tag=\"v2\"' values.yaml")
    io.print(c("│", "blue") + " ✓ commit     \"ci: bump image tag to v2 [skip ci]\"")
    io.print(c("└────────────────────────────────────────────────────────┘", "blue"))
    io.print(c("(the ROBOT just committed to the GitOps repo — you never touch the tag by hand)", "dim"))
    world.files["values.yaml"] = VALUES_YAML.replace("tag: v1", "tag: v2")
    g["commits"].append({"branch": "main", "msg": "ci: bump image tag to v2 [skip ci]"})
    world.flags["ci_ran"] = True


def _argocd(world, m, io):
    args = m.group(0).split()[1:]
    if args[:2] == ["app", "get"] or args[:2] == ["app", "sync"]:
        tag = "v2" if "tag: v2" in world.files.get("values.yaml", "") else "v1"
        deployed = world.k8s["deployments"].get("skywatch", {}).get("image", "?").split(":")[-1]
        if args[1] == "get":
            status = "Synced" if tag == deployed else "OutOfSync"
            health = "Healthy"
            io.print(f"Name:               skywatch\nProject:            default\n"
                     f"Server:             https://kubernetes.default.svc\n"
                     f"Repo:               github.com/you/skywatch (path: helm/skywatch)\n"
                     f"Sync Status:        {status}{' (values.yaml says ' + tag + ', cluster runs ' + deployed + ')' if status == 'OutOfSync' else ''}\n"
                     f"Health Status:      {health}")
            world.flags["argo_get"] = True
            if status == "OutOfSync":
                io.print(c("(Git changed but the cluster didn't yet — sync it: argocd app sync skywatch)", "dim"))
        else:  # sync
            if not world.flags.get("ci_ran"):
                io.print("Operation: Sync — already Synced (nothing new in Git)")
                return
            world.k8s["deployments"]["skywatch"]["image"] = f"ghcr.io/you/skywatch:{tag}"
            for p in [p for p, pd in world.k8s["pods"].items() if pd.get("deploy") == "skywatch"]:
                del world.k8s["pods"][p]
            _reconcile(world)
            io.print(f"TIMESTAMP     GROUP  KIND        NAME      STATUS   HEALTH\n"
                     f"just-now      apps   Deployment  skywatch  Synced   Healthy\n\n"
                     f"Operation:          Sync\nSync Status:        Synced to HEAD\nPhase:              Succeeded")
            world.flags["argo_synced"] = True
    else:
        io.print("argocd: try `argocd app get skywatch` or `argocd app sync skywatch`")


MISSIONS = [
    {
        "id": "gitops-01",
        "topic": "gitops",
        "title": "The Robot Deploys 🤖 — GitOps end to end",
        "vault_note": "Class 08 - GitOps and CI-CD",
        "brief": ("Nobody kubectl-applies to prod by hand. In GitOps, YOU only push code;\n"
                  "a CI robot builds the image and bumps the tag in Git, and ArgoCD makes\n"
                  "the cluster match Git. The skywatch app (app.py) runs as v1 right now.\n"
                  "Ship v2 without ever touching the cluster yourself."),
        "world": {
            "files": {"app.py": APP_PY, "values.yaml": VALUES_YAML},
            "git": {"branch": "main", "tracked": ["app.py", "values.yaml"],
                    "commits": [{"branch": "main", "msg": "initial skywatch"}],
                    "pushed": [],
                    "branch_files": {"main": {}}},
            "k8s": {"started": True,
                    "deployments": {"skywatch": {"ns": "default", "replicas": 2,
                                                 "image": "ghcr.io/you/skywatch:v1"}}},
        },
        "handlers": [
            (r"git\s+push.*", _git_push),
            (r"argocd\s+.*", _argocd),
        ],
        "objectives": [
            {"desc": "Change the app (make it say v2) and commit", "xp": 15,
             "hint": "edit app.py → change the returned string; then git add + git commit -m …",
             "check": lambda w: "v2" in w.files.get("app.py", "")
                                and w.git and len(w.git["commits"]) >= 2},
            {"desc": "Push — and watch the CI robot do its 5 jobs", "xp": 20,
             "hint": "git push (first push of main needs -u origin main). Then READ the pipeline output.",
             "check": lambda w: w.flags.get("ci_ran")},
            {"desc": "Prove Git and the cluster now DISAGREE (OutOfSync)", "xp": 15,
             "hint": "argocd app get skywatch — compare what values.yaml says vs what the cluster runs.",
             "check": lambda w: w.flags.get("argo_get")},
            {"desc": "Let ArgoCD reconcile the cluster to Git", "xp": 20,
             "hint": "argocd app sync skywatch",
             "check": lambda w: w.flags.get("argo_synced")},
            {"desc": "Verify the cluster now runs v2 — without you deploying anything", "xp": 15,
             "hint": "kubectl describe deployment skywatch — check the Image line.",
             "check": lambda w: w.flags.get("describe_deploy_skywatch")
                                and w.k8s["deployments"].get("skywatch", {}).get("image", "").endswith("v2")},
        ],
        "solution": [
            "edit app.py",
            "from flask import Flask", "app = Flask(__name__)", "",
            '@app.route("/")', "def home():", '    return "SkyWatch v2"', ".",
            "git add app.py",
            'git commit -m "bump greeting to v2"',
            "git push -u origin main",
            "argocd app get skywatch",
            "argocd app sync skywatch",
            "kubectl describe deployment skywatch",
        ],
    },
]
