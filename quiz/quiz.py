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
