#!/usr/bin/env python3
"""
DevOps Experts — terminal quiz game.

A fast, fun self-test across the whole course (Docker, Git, K8s, Helm,
Ansible, Terraform, RabbitMQ, GitOps, foundations). Pure standard library.

Run:
    python quiz.py                # 12 random questions from all topics
    python quiz.py --all          # every question
    python quiz.py --topic git    # only one topic (docker/git/k8s/helm/
                                  # ansible/terraform/rabbitmq/gitops/foundations)
    python quiz.py -n 20          # choose how many questions
"""
import argparse
import os
import random
import sys

# --- make ANSI colours work on Windows 10+ terminals ---
os.system("")
# --- force UTF-8 so emojis/box-drawing don't crash on Windows (cp1252) ---
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdin.reconfigure(encoding="utf-8")
except Exception:
    pass
C = {
    "reset": "\033[0m", "bold": "\033[1m", "dim": "\033[2m",
    "green": "\033[92m", "red": "\033[91m", "yellow": "\033[93m",
    "cyan": "\033[96m", "magenta": "\033[95m", "blue": "\033[94m",
}


def c(text, color):
    return f"{C[color]}{text}{C['reset']}"


# Each question: topic, q, either "options"+"answer"(index) for multiple choice,
# or "accept" (list of accepted substrings, lowercased) for free-text.
QUESTIONS = [
    # ---------------- Docker ----------------
    {"topic": "docker", "q": "What's the difference between an image and a container?",
     "options": ["An image runs; a container is stored", "An image is a template; a container is a running instance of it",
                 "They're the same thing", "A container builds an image"], "answer": 1},
    {"topic": "docker", "q": "Which command opens a shell inside an ALREADY-running container?",
     "options": ["docker run -it", "docker exec -it <name> bash", "docker start", "docker attach --new"], "answer": 1},
    {"topic": "docker", "q": "Type the command to download the ubuntu:latest image:",
     "accept": ["docker pull ubuntu"]},
    {"topic": "docker", "q": "In `-p 8080:5000`, which port is the CONTAINER's?",
     "options": ["8080", "5000", "both", "neither"], "answer": 1},
    {"topic": "docker", "q": "Why put `COPY requirements.txt` BEFORE `COPY . .` in a Dockerfile?",
     "options": ["Alphabetical order", "So the dependency-install layer stays cached when only code changes",
                 "It's required syntax", "To make the image bigger"], "answer": 1},
    {"topic": "docker", "q": "On which network can containers reach each other by NAME?",
     "options": ["The default bridge", "A user-defined network", "Only host network", "None"], "answer": 1},

    # ---------------- Git ----------------
    {"topic": "git", "q": "Which command STAGES a file for the next commit?",
     "options": ["git commit <file>", "git add <file>", "git push <file>", "git stage <file>"], "answer": 1},
    {"topic": "git", "q": "Type the command that shows which files are changed/staged/untracked:",
     "accept": ["git status"]},
    {"topic": "git", "q": "What does `git diff` show?",
     "options": ["Commit history", "The exact line-level changes you haven't committed", "Remote URL", "Branch list"], "answer": 1},
    {"topic": "git", "q": "First push of a new branch needs which flag to set upstream?",
     "options": ["-f", "-u", "--new", "-b"], "answer": 1},
    {"topic": "git", "q": "A monorepo is...",
     "options": ["Always a monolith", "One repo holding many projects/services", "A single-file repo", "A backup"], "answer": 1},

    # ---------------- Kubernetes ----------------
    {"topic": "k8s", "q": "What is the smallest deployable unit in Kubernetes?",
     "options": ["Container", "Pod", "Deployment", "Node"], "answer": 1},
    {"topic": "k8s", "q": "Which object keeps N replicas running and self-heals them?",
     "options": ["Service", "Deployment", "ConfigMap", "Ingress"], "answer": 1},
    {"topic": "k8s", "q": "Kubernetes Secrets are stored base64-encoded. Are they encrypted by default?",
     "options": ["Yes, fully encrypted", "No — base64 is encoding, not encryption", "Only on cloud", "Yes with a password"], "answer": 1},
    {"topic": "k8s", "q": "Type the kubectl command to apply a manifest file called app.yaml:",
     "accept": ["kubectl apply -f app.yaml", "kubectl apply -f app"]},
    {"topic": "k8s", "q": "Which Service type opens a port (30000-32767) on every node?",
     "options": ["ClusterIP", "NodePort", "LoadBalancer", "Ingress"], "answer": 1},
    {"topic": "k8s", "q": "Which command documents any field of any resource offline?",
     "options": ["kubectl describe", "kubectl explain", "kubectl get -o yaml", "kubectl docs"], "answer": 1},

    # ---------------- Helm ----------------
    {"topic": "helm", "q": "Helm is best described as...",
     "options": ["A container runtime", "The package manager for Kubernetes", "A CI server", "A cloud provider"], "answer": 1},
    {"topic": "helm", "q": "Which file holds the default, overridable settings of a chart?",
     "options": ["Chart.yaml", "values.yaml", "templates/", "helmfile"], "answer": 1},
    {"topic": "helm", "q": "Which command RENDERS templates locally without installing?",
     "options": ["helm install", "helm template", "helm apply", "helm render"], "answer": 1},

    # ---------------- Ansible ----------------
    {"topic": "ansible", "q": "Ansible connects to managed hosts using...",
     "options": ["An installed agent", "SSH (agentless)", "A kernel module", "HTTP only"], "answer": 1},
    {"topic": "ansible", "q": "What does 'idempotent' mean for a playbook?",
     "options": ["It runs once only", "Re-running changes only what isn't already correct", "It needs root", "It's encrypted"], "answer": 1},
    {"topic": "ansible", "q": "A task that runs ONLY when notified by a change is a...",
     "options": ["role", "handler", "module", "fact"], "answer": 1},
    {"topic": "ansible", "q": "Which command shows a module's docs + examples in your terminal?",
     "options": ["ansible --docs", "ansible-doc <module>", "ansible help", "man ansible"], "answer": 1},

    # ---------------- Terraform ----------------
    {"topic": "terraform", "q": "Terraform is an example of...",
     "options": ["Configuration management", "Infrastructure as Code (provisioning)", "A container registry", "A message broker"], "answer": 1},
    {"topic": "terraform", "q": "Which command shows what WILL change before applying?",
     "options": ["terraform apply", "terraform plan", "terraform show", "terraform diff"], "answer": 1},
    {"topic": "terraform", "q": "What does the Terraform state file track?",
     "options": ["Your SSH keys", "What resources Terraform has created (code ↔ real infra)", "Logs", "Nothing"], "answer": 1},
    {"topic": "terraform", "q": "Which files must NEVER be committed to git?",
     "options": ["*.tf", "terraform.tfstate and *.tfvars and *.pem", "provider.tf", "README"], "answer": 1},

    # ---------------- RabbitMQ ----------------
    {"topic": "rabbitmq", "q": "The main point of a message queue is to...",
     "options": ["Encrypt data", "Decouple producers from consumers (buffer work)", "Store files", "Replace a database"], "answer": 1},
    {"topic": "rabbitmq", "q": "In RabbitMQ, the app that SENDS messages is the...",
     "options": ["consumer", "producer", "broker", "exchange"], "answer": 1},
    {"topic": "rabbitmq", "q": "Two consumers on one queue will...",
     "options": ["Each get every message", "Split the messages between them (load-balance)", "Crash", "Merge into one"], "answer": 1},

    # ---------------- GitOps / CI-CD ----------------
    {"topic": "gitops", "q": "In GitOps, the single source of truth is...",
     "options": ["The cluster", "Git", "The Docker registry", "The engineer's laptop"], "answer": 1},
    {"topic": "gitops", "q": "What does ArgoCD's 'self-heal' do?",
     "options": ["Restarts nodes", "Reverts manual drift back to what Git declares", "Patches CVEs", "Scales pods"], "answer": 1},
    {"topic": "gitops", "q": "Why add `[skip ci]` to a CI-made commit that bumps an image tag?",
     "options": ["To sign it", "To stop the pipeline re-triggering itself in a loop", "To skip tests forever", "Required by git"], "answer": 1},

    # ---------------- Foundations ----------------
    {"topic": "foundations", "q": "Which deployment strategy releases to a small % of users first?",
     "options": ["Blue-green", "Canary", "Rolling", "Big-bang"], "answer": 1},
    {"topic": "foundations", "q": "Blue-green deployment means...",
     "options": ["Two envs; switch all traffic at once, instant rollback", "Deploy on Tuesdays", "Only for databases", "A testing framework"], "answer": 0},
    {"topic": "foundations", "q": "A fixed-length iteration in Scrum is called a...",
     "options": ["standup", "sprint", "backlog", "epic"], "answer": 1},

    # ---------------- Docker (round 2) ----------------
    {"topic": "docker", "q": "You push <repo>:1.0 to Docker Hub and get 'denied: requested access…' even though you're logged in. Most likely cause?",
     "options": ["Docker Hub is down", "The image isn't namespaced <your-username>/<repo>", "The tag must be 'latest'", "You need sudo"], "answer": 1},
    {"topic": "docker", "q": "Why does `docker login` want an ACCESS TOKEN instead of your account password?",
     "options": ["Tokens are shorter", "Tokens are revocable + scoped — leak one, kill one, account survives", "Passwords don't work over HTTP", "It doesn't matter"], "answer": 1},
    {"topic": "docker", "q": "Type the command that removes stopped containers + dangling images in one go:",
     "accept": ["docker system prune"]},

    # ---------------- Git (round 2 — the bonus assignment) ----------------
    {"topic": "git", "q": "You need to switch branches NOW but have messy uncommitted changes. The course-approved move?",
     "options": ["git commit -m 'wip'", "git stash (then git stash pop later)", "Delete the changes", "git push -f"], "answer": 1},
    {"topic": "git", "q": "git revert vs git reset --hard: which one is SAFE on pushed history, and why?",
     "options": ["reset — it's stronger", "revert — it ADDS an undo commit instead of rewriting history", "Both equal", "Neither works on pushed commits"], "answer": 1},
    {"topic": "git", "q": "After `git checkout <commit-hash>` git warns you are in ... state?",
     "accept": ["detached head", "detached"]},
    {"topic": "git", "q": "Type the command that marks the current commit as version v1.0.0:",
     "accept": ["git tag v1.0.0"]},
    {"topic": "git", "q": "Rebase vs merge: what does rebase promise that merge doesn't?",
     "options": ["It's faster", "A LINEAR history (no merge commits)", "It can't conflict", "It auto-pushes"], "answer": 1},

    # ---------------- Kubernetes (round 2 — the real assignments) ----------------
    {"topic": "k8s", "q": "`kubectl get pods` prints 'No resources found' but you KNOW the app is deployed. First thing to check?",
     "options": ["Reinstall kubectl", "The namespace — add -n <namespace> (or -A)", "Restart minikube", "The YAML is corrupt"], "answer": 1},
    {"topic": "k8s", "q": "You delete a pod owned by a Deployment. What do you see in `kubectl get pods` a moment later?",
     "options": ["One pod fewer", "Same count — a NEW pod with a new name replaced it", "All pods restarted", "An error"], "answer": 1},
    {"topic": "k8s", "q": "Type the command to scale deployment `backend` to 3 replicas (default namespace):",
     "accept": ["kubectl scale deployment backend --replicas=3", "kubectl scale deploy backend --replicas=3"]},
    {"topic": "k8s", "q": "In the CLI assignment, why is the frontend browser-reachable but the backend is not?",
     "options": ["The backend crashed", "frontend Service is NodePort; backend is ClusterIP (internal-only)", "Firewall rules", "The backend has no pods"], "answer": 1},
    {"topic": "k8s", "q": "The RBAC trio that grants an app permission is ServiceAccount + Role + ...?",
     "accept": ["rolebinding", "role binding"]},
    {"topic": "k8s", "q": "Which minikube command opens a NodePort service in your browser?",
     "options": ["minikube open <svc>", "minikube service <svc>", "minikube expose <svc>", "minikube browse"], "answer": 1},
    {"topic": "k8s", "q": "Deployment → ReplicaSet → Pod: why does `kubectl set image` create a NEW ReplicaSet?",
     "options": ["A bug", "Each RS pins one pod-template version — that's how rollbacks are possible", "RS expire daily", "To use more RAM"], "answer": 1},

    # ---------------- Helm (round 2) ----------------
    {"topic": "helm", "q": "Type the command that undoes release `demo` back to revision 1:",
     "accept": ["helm rollback demo 1", "helm rollback demo"]},
    {"topic": "helm", "q": "`helm upgrade demo ./chart --set replicaCount=4` — what wins when values.yaml says 2?",
     "options": ["values.yaml (files beat flags)", "--set (CLI overrides file defaults)", "Neither — error", "Random"], "answer": 1},
    {"topic": "helm", "q": "One release, several upgrades later — which command lists every revision?",
     "options": ["helm list", "helm history <release>", "helm log", "helm get all"], "answer": 1},

    # ---------------- Ansible (round 2) ----------------
    {"topic": "ansible", "q": "The file listing which hosts Ansible manages (the class used INI format) is the...",
     "accept": ["inventory", "hosts"]},
    {"topic": "ansible", "q": "`ansible-playbook play.yml --check` does what?",
     "options": ["Syntax check only", "DRY RUN — reports would-be changes, touches nothing", "Runs twice", "Checks SSH keys"], "answer": 1},
    {"topic": "ansible", "q": "Second run of a correct playbook shows changed=0. That property is called...",
     "accept": ["idempotency", "idempotent", "idempotence"]},

    # ---------------- Terraform (round 2) ----------------
    {"topic": "terraform", "q": "Fresh clone of a terraform repo. `terraform plan` errors about plugins/providers. The fix?",
     "accept": ["terraform init"]},
    {"topic": "terraform", "q": "In CI there's no human to type 'yes'. How do pipelines apply?",
     "options": ["echo yes | terraform apply", "terraform apply -auto-approve", "terraform apply --force", "They can't"], "answer": 1},
    {"topic": "terraform", "q": "Lab's over. Which command deletes every resource Terraform created (and why run it)?",
     "options": ["terraform rm -all", "terraform destroy — so the cloud stops billing you", "terraform reset", "Delete main.tf"], "answer": 1},

    # ---------------- RabbitMQ (round 2) ----------------
    {"topic": "rabbitmq", "q": "Producer sends 5 messages while NO consumer is running. What happens to them?",
     "options": ["Lost", "The queue holds them until a consumer connects — that's the decoupling", "Error thrown", "Sent back"], "answer": 1},
    {"topic": "rabbitmq", "q": "The RabbitMQ management web UI (class compose file) listens on port...",
     "accept": ["15672"]},
    {"topic": "rabbitmq", "q": "Type the in-container command that lists queues and their depth:",
     "accept": ["rabbitmqctl list_queues"]},

    # ---------------- GitOps (round 2) ----------------
    {"topic": "gitops", "q": "In the class-8 flow, who is allowed to change the image tag in values.yaml?",
     "options": ["Any engineer, by hand", "The CI pipeline (a bot commit) — humans only push code", "The cluster", "ArgoCD support"], "answer": 1},
    {"topic": "gitops", "q": "Someone kubectl-edits prod directly. ArgoCD (with self-heal) will...",
     "options": ["Keep the manual change", "Revert it to match Git — Git is the only truth", "Crash", "Email the CEO"], "answer": 1},
    {"topic": "gitops", "q": "ArgoCD reports OutOfSync. What does that literally mean?",
     "options": ["The cluster is down", "Git's desired state ≠ the cluster's live state", "ArgoCD needs an update", "The repo was deleted"], "answer": 1},
]

TOPIC_NAMES = {
    "docker": "🐳 Docker", "git": "🌿 Git", "k8s": "☸️ Kubernetes", "helm": "⎈ Helm",
    "ansible": "📜 Ansible", "terraform": "🏗️ Terraform", "rabbitmq": "📨 RabbitMQ",
    "gitops": "🔁 GitOps/CI-CD", "foundations": "🧭 Foundations",
}


def ask(q, idx, total):
    print(c(f"\n[{idx}/{total}] ", "dim") + c(TOPIC_NAMES.get(q["topic"], q["topic"]), "magenta"))
    print(c(q["q"], "bold"))
    if "options" in q:
        letters = "abcd"
        order = list(range(len(q["options"])))
        random.shuffle(order)
        for i, o in enumerate(order):
            print(f"  {c(letters[i], 'cyan')}) {q['options'][o]}")
        try:
            reply = input(c("\n> your answer (a/b/c/d): ", "yellow")).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return None
        if reply and reply[0] in letters[:len(order)]:
            return order[letters.index(reply[0])] == q["answer"]
        return False
    else:
        try:
            reply = input(c("\n> type the command: ", "yellow")).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return None
        return any(a in reply for a in q["accept"]) and len(reply) > 0


def rank(pct):
    if pct == 100:
        return c("🏆 DEVOPS LEGEND — flawless!", "green")
    if pct >= 80:
        return c("🥇 DevOps Engineer", "green")
    if pct >= 60:
        return c("🥈 Operator", "cyan")
    if pct >= 40:
        return c("🥉 Rookie — keep drilling", "yellow")
    return c("🐣 Beginner — revisit the notes, you've got this", "red")


def main():
    ap = argparse.ArgumentParser(description="DevOps Experts quiz game")
    ap.add_argument("--all", action="store_true", help="use every question")
    ap.add_argument("-n", type=int, default=12, help="number of questions (default 12)")
    ap.add_argument("--topic", type=str, help="filter to one topic")
    args = ap.parse_args()

    pool = QUESTIONS
    if args.topic:
        t = args.topic.lower()
        pool = [q for q in QUESTIONS if q["topic"] == t]
        if not pool:
            print(c(f"No questions for topic '{args.topic}'. Topics: " + ", ".join(TOPIC_NAMES), "red"))
            sys.exit(1)

    pool = pool[:]
    random.shuffle(pool)
    if not args.all:
        pool = pool[:min(args.n, len(pool))]

    print(c("\n══════════════════════════════════════", "blue"))
    print(c("   ⚡ DEVOPS EXPERTS — QUIZ GAME ⚡", "bold"))
    print(c("══════════════════════════════════════", "blue"))
    print(c(f"{len(pool)} questions · answer to score · Ctrl+C to quit\n", "dim"))

    score = streak = best_streak = 0
    for i, q in enumerate(pool, 1):
        result = ask(q, i, len(pool))
        if result is None:
            print(c("\n\nBailed out early — no shame. Come back stronger. 👋", "yellow"))
            break
        if result:
            streak += 1
            best_streak = max(best_streak, streak)
            bonus = c(f"  🔥 x{streak} streak!", "magenta") if streak >= 3 else ""
            print(c("  ✅ Correct!", "green") + bonus)
            score += 1
        else:
            streak = 0
            if "options" in q:
                print(c("  ❌ Nope — correct: ", "red") + c(q["options"][q["answer"]], "bold"))
            else:
                print(c("  ❌ Nope — accepted: ", "red") + c(q["accept"][0], "bold"))

    total = len(pool)
    pct = round(100 * score / total) if total else 0
    print(c("\n──────────── RESULTS ────────────", "blue"))
    print(f"Score: {c(str(score), 'bold')}/{total}  ({pct}%)")
    print(f"Best streak: {c(str(best_streak), 'magenta')} 🔥")
    print("Rank:  " + rank(pct))
    print(c("\nStudy the notes (github.com/iceteps/devops-study-vault), then run me again. 📚", "dim"))


if __name__ == "__main__":
    main()
