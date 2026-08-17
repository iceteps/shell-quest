#!/usr/bin/env python3
"""
DevOps Experts — terminal quiz game.

A fast, fun self-test across the whole course (Linux, Docker, Git, K8s, Helm,
Ansible, Terraform, RabbitMQ, GitOps, foundations). Pure standard library.

Run:
    python quiz.py                # 12 random questions from all topics
    python quiz.py --all          # every question
    python quiz.py --topic linux  # only one topic (linux/docker/git/k8s/helm/
                                  # ansible/terraform/rabbitmq/gitops/capstone/
                                  # foundations)
    python quiz.py -n 20          # choose how many questions
"""
import argparse
import os
import random
import re
import sys

# --- make ANSI colours work on Windows 10+ terminals ---
if os.name == "nt":
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
    # ---------------- Linux (class 1 — mirrors the real graded assignment) ----------------
    {"topic": "linux", "q": "chmod 600 on a file means:",
     "options": ["Everyone can read and write it", "Owner can read and write; group and others get nothing",
                 "Owner can execute it", "It becomes read-only for everyone"], "answer": 1},
    {"topic": "linux", "q": "In a permission triad, which numbers add up to rwx?",
     "options": ["1+2+3", "4+2+1", "7+7+7", "3+3+1"], "answer": 1},
    {"topic": "linux", "q": "What is the difference between > and >> ?",
     "options": ["No difference", "> overwrites the file; >> appends to it",
                 "> appends; >> overwrites", "> is for text, >> is for binary"], "answer": 1},
    {"topic": "linux", "q": "Type the command that finds every .txt file under ~/linux_course:",
     "accept": ["find ~/linux_course -name", "find /root/linux_course -name"]},
    {"topic": "linux", "q": "Type the command that sends exactly 4 ping packets to google.com:",
     "accept": ["ping -c 4 google.com", "ping -c4 google.com"]},
    {"topic": "linux", "q": "You wrote hello.sh; running ./hello.sh says 'Permission denied'. Why?",
     "options": ["The file is empty", "The execute bit isn't set — chmod +x hello.sh",
                 "You need sudo", "Bash isn't installed"], "answer": 1},
    {"topic": "linux", "q": "What does the #!/bin/bash line at the top of a script do?",
     "options": ["Nothing — it's a comment", "Tells the kernel which interpreter to run the file with",
                 "Imports bash functions", "Makes the file executable"], "answer": 1},
    {"topic": "linux", "q": "Cron's five fields, in order, are:",
     "options": ["hour minute day month weekday", "minute hour day-of-month month day-of-week",
                 "second minute hour day month", "day month year hour minute"], "answer": 1},
    {"topic": "linux", "q": 'You run: echo "* * * * * echo $(date) >> log" | crontab -   What breaks?',
     "options": ["Nothing, it's correct",
                 "$(date) expands ONCE as you write the crontab — the job logs one frozen timestamp forever",
                 "cron cannot append to a file", "echo isn't allowed in cron"], "answer": 1},
    {"topic": "linux", "q": "What is the difference between tar and gzip?",
     "options": ["They're the same thing", "tar bundles many files into one; gzip compresses a single file",
                 "tar compresses; gzip bundles", "gzip only works on directories"], "answer": 1},
    {"topic": "linux", "q": "Type the command that lists a tar archive's contents WITHOUT extracting it:",
     "accept": ["tar -tvf", "tar -tf", "tar tvf", "tar -ztvf"]},
    {"topic": "linux", "q": "On a modern Fedora box `ifconfig` says command not found. What replaced it?",
     "options": ["netstat", "ip a", "ipconfig", "ss -l"], "answer": 1},
    {"topic": "linux", "q": "`ps aux | grep sleep` — what is the | actually doing?",
     "options": ["Running both commands at once", "Feeding ps's output into grep as grep's input",
                 "Comparing the two outputs", "Sending output to a file"], "answer": 1},
    {"topic": "linux", "q": "kill <PID> vs kill -9 <PID>:",
     "options": ["Identical", "kill asks politely (SIGTERM); -9 forces it (SIGKILL) with no cleanup",
                 "-9 is the gentler one", "kill -9 only works as root"], "answer": 1},

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

    # ---------------- Linux (round 3 — the 3 extra exercises) ----------------
    {"topic": "linux", "q": "You run `cp report.txt backup.txt`, then `mv backup.txt archive.txt`. Which files exist at the end?",
     "options": ["report.txt and archive.txt", "archive.txt only", "report.txt only", "all three"], "answer": 0},
    {"topic": "linux", "q": "Why doesn't a plain `ls` show `.bashrc`?",
     "options": ["It belongs to root", "A leading dot is a naming CONVENTION that ls hides unless you pass -a — it is not a permission",
                 "It lives in a hidden folder", "You'd need sudo"], "answer": 1},
    {"topic": "linux", "q": "`echo one > log.txt` then `echo two > log.txt`. What is in log.txt?",
     "options": ["one then two, on two lines", "one", "two — `>` truncates the file to zero bytes before every single write", "an error: file exists"], "answer": 2},
    {"topic": "linux", "q": "Type the command that deletes the directory `ex1`, which still has files in it:",
     "accept": ["rm -r ex1", "rm -rf ex1", "rm -r ex1/", "rm -rf ex1/"]},
    {"topic": "linux", "q": "You run `tar -xf site.tar`. Where do the extracted files land?",
     "options": ["In /tmp", "Back in the directory they were archived from", "Nowhere — extraction needs -C",
                 "In your CURRENT working directory — tar unpacks relative to where you stand"], "answer": 3},
    {"topic": "linux", "q": "You are somewhere deep in /tmp. `cd` with NO arguments takes you...",
     "options": ["Nowhere — it errors", "To your home directory", "Up one level", "To /"], "answer": 1},

    # ---------------- Docker (round 3 — the Lab B image lab) ----------------
    {"topic": "docker", "q": "You rebuild with nothing changed: every step prints CACHED and it takes 0.2s instead of 13.9s. Why?",
     "options": ["Docker skipped the build entirely", "Each instruction is a LAYER — unchanged inputs mean Docker reuses the stored layer instead of re-running it",
                 "The image came from Docker Hub", "BuildKit compressed it"], "answer": 1},
    {"topic": "docker", "q": "`docker images` shows python:3 at 1.02GB and python:3-slim at ~150MB. Beyond disk, what does the small base buy you?",
     "options": ["A faster CPU", "Automatic security patches", "Faster pulls and pushes, plus a much smaller attack surface", "More layers to cache"], "answer": 2},
    {"topic": "docker", "q": "You created files inside a container, then `docker rm -f` it and ran a fresh one from the SAME image. Your files are...",
     "options": ["Gone — they lived in that container's writable layer, which died with it (this is what volumes are for)",
                 "Still there — images keep writes", "Recoverable from /var/lib/docker", "Restored on the next start"], "answer": 0},

    # ---------------- Git (round 3 — the graded bonus section) ----------------
    {"topic": "git", "q": "`git add .` swept up debug.log. Which command unstages it WITHOUT touching the file on disk?",
     "options": ["git rm debug.log", "git restore --staged debug.log", "git checkout debug.log", "git clean -f"], "answer": 1},
    {"topic": "git", "q": "`.env` is already committed, so adding it to .gitignore alone changes nothing. What does `git rm --cached .env` do?",
     "options": ["Deletes it from disk too", "Rewrites every commit that touched it",
                 "Stops git TRACKING it while leaving the file on disk — .gitignore then keeps it out", "Nothing without -f"], "answer": 2},
    {"topic": "git", "q": "When is `git commit --amend` genuinely free of consequences?",
     "options": ["Always", "While the commit is still LOCAL — amending replaces it, which only hurts once someone else has pulled it",
                 "Only on main", "Only with --force"], "answer": 1},

    # ---------------- Kubernetes (round 3 — Day-2 Ops & Resilience) ----------------
    {"topic": "k8s", "q": "Liveness vs readiness probe — one line each:",
     "options": ["Both restart the container", "Readiness GATES TRAFFIC (fail → pulled out of the Service); liveness RESTARTS the container (fail → killed and restarted)",
                 "Liveness gates traffic; readiness restarts", "Readiness only runs once at startup"], "answer": 1},
    {"topic": "k8s", "q": "You ship nginx:1.9999 to a Deployment with `maxSurge: 1, maxUnavailable: 0`. Did users see downtime?",
     "options": ["Yes — every pod was replaced at once", "Yes, for a few seconds",
                 "No — with 0 unavailable, K8s never removes an old pod until a new one is Ready, and the new ones never got Ready (ImagePullBackOff)",
                 "Only if replicas were fewer than 3"], "answer": 2},
    {"topic": "k8s", "q": "Type the command that instantly reverts deployment `app-deployment` (namespace dev) to its previous revision:",
     "accept": ["kubectl rollout undo deployment/app-deployment",
                "kubectl rollout undo deploy/app-deployment",
                "kubectl rollout undo deployment app-deployment"]},
    {"topic": "k8s", "q": "requests vs limits — which one does the SCHEDULER use to decide which node a pod fits on?",
     "options": ["limits", "requests", "both, averaged", "neither — it's random"], "answer": 1},
    {"topic": "k8s", "q": "A Service returns 503 and `kubectl get endpoints` shows none. First suspect?",
     "options": ["The nodes are down", "kube-proxy crashed",
                 "The Service SELECTOR doesn't match the pods' labels — no matching pods, no endpoints", "The image is wrong"], "answer": 2},

    # ---------------- Helm (round 3 — Assignment A) ----------------
    {"topic": "helm", "q": "values.yaml says replicaCount: 4, `-f values-dev.yaml` says 2, and you add `--set replicaCount=5`. What ships?",
     "options": ["4", "2", "It errors on the conflict", "5 — precedence is chart values.yaml < each -f file (in order) < --set"], "answer": 3},
    {"topic": "helm", "q": 'Install into namespace dev fails: `create: failed to create: namespaces "dev" not found`. Type the flag that fixes it:',
     "accept": ["--create-namespace"]},
    {"topic": "helm", "q": "Why do pipelines always use `helm upgrade --install` instead of plain `helm install`?",
     "options": ["It's faster", "It installs when the release is missing and upgrades when it exists — the SAME command works on run 1 and run 50",
                 "It skips hooks", "install is deprecated"], "answer": 1},

    # ---------------- Ansible (round 3 — playbooks + the class-14 lab) ----------------
    {"topic": "ansible", "q": "`ansible servers -m ping` prints `[WARNING]: Could not match supplied host pattern`. Most likely cause?",
     "options": ["SSH is down on the nodes", "The inventory's group isn't called `servers` — compare the [group] header letter by letter",
                 "Ansible isn't installed", "The nodes need rebooting"], "answer": 1},
    {"topic": "ansible", "q": "In the dockerized lab, where do you actually run the playbooks?",
     "options": ["On your laptop, from ansible_lab_files/", "Inside the ansible-control container — the host has no Ansible, no inventory and no SSH keys",
                 "On node1", "Only through the Semaphore UI"], "answer": 1},
    {"topic": "ansible", "q": "Adding a node3 to the dockerized lab means editing how many places — and which?",
     "options": ["One — inventory.ini", "Two — docker-compose.yml and ansible.cfg",
                 "Three — docker-compose.yml (service + port), inventory.ini, and entrypoint.sh's key-distribution loop",
                 "None — Ansible discovers new hosts"], "answer": 2},
    {"topic": "ansible", "q": "Type the flag that runs ONLY the tasks tagged `deploy`:",
     "accept": ["--tags deploy", "--tags=deploy", "-t deploy"]},
    {"topic": "ansible", "q": "You `register: r` a `command:` task and branch on `when: r.rc != 0`, but the run dies at the register task. Why, and the fix?",
     "options": ["Ansible can't register command output — use shell",
                 "A non-zero rc IS a task failure — add `ignore_errors: true` (or `failed_when: false`) so it can report rc and let `when` decide",
                 "You forgot become: true", "register only works inside a loop"], "answer": 1},

    # ---------------- Terraform (round 3 — variables, outputs, remote state) ----------------
    {"topic": "terraform", "q": "In a subnet resource, `vpc_id = aws_vpc.main.id` does what beyond passing an ID?",
     "options": ["Nothing else", "Creates an IMPLICIT DEPENDENCY — Terraform builds the VPC first, the subnet second, and destroys them in reverse",
                 "Locks the state file", "Validates the ID format"], "answer": 1},
    {"topic": "terraform", "q": "You declare a new `output \"vpc_id\"` and plan. Terraform offers to apply it 'without changing any real infrastructure'. Why?",
     "options": ["A quirk of the CLI", "Outputs live in STATE, not in the cloud — applying only writes the value into the ledger",
                 "The output is invalid", "It would recreate the VPC"], "answer": 1},
    {"topic": "terraform", "q": "`terraform init` after uncommenting an S3 backend asks whether to copy existing state. You answer `no`. Then what?",
     "options": ["Nothing changes", "It destroys the resources",
                 "Terraform starts with an EMPTY remote state — the resources still exist in AWS but Terraform no longer knows about them (import is the way back)",
                 "It refuses and exits"], "answer": 2},
    {"topic": "terraform", "q": "A variable with no `default`, nothing in terraform.tfvars, and no `-var`. What does Terraform do?",
     "options": ["Uses an empty string", "PROMPTS you for a value — on every single run, which is exactly why TF_VAR_* and -var-file exist",
                 "Fails immediately", "Skips the resource that uses it"], "answer": 1},

    # ---------------- RabbitMQ (round 3 — durability, acks, fair dispatch) ----------------
    {"topic": "rabbitmq", "q": "Which combination survives `docker compose restart rabbitmq` with the messages intact?",
     "options": ["queue durable=True only", "delivery_mode=2 only",
                 "Both halves: queue declared durable=True AND messages published with delivery_mode=2", "auto_ack=True"], "answer": 2},
    {"topic": "rabbitmq", "q": "A worker running with `auto_ack=True` is killed mid-message. Those messages are...",
     "options": ["Requeued to the other worker", "Gone — the broker acked them the moment it delivered them and already considers them done",
                 "Sent to a dead-letter queue", "Retried after 30 seconds"], "answer": 1},
    {"topic": "rabbitmq", "q": "A fast worker (2s/job) and a slow one (6s/job), no QoS: round-robin hands each 10 of 20 messages. What does `basic_qos(prefetch_count=1)` change?",
     "options": ["Nothing measurable", "It doubles throughput",
                 "The broker only offers the next message after an ACK — so the fast worker takes more (15/5) and the whole batch finishes sooner",
                 "Both workers receive every message"], "answer": 2},
    {"topic": "rabbitmq", "q": "`basic_publish(exchange='', routing_key='orders')` — where does the message actually go?",
     "options": ["Straight into the queue; no exchange involved", "To the DEFAULT exchange, which routes it to the queue whose NAME equals the routing key",
                 "To every queue on the vhost", "To a topic exchange"], "answer": 1},

    # ---------------- GitOps (round 3) ----------------
    {"topic": "gitops", "q": "You `git revert` the bad tag-bump commit locally and the cluster doesn't budge. Why?",
     "options": ["ArgoCD is broken", "ArgoCD watches the REMOTE repo — until you push, your revert doesn't exist as far as it's concerned",
                 "revert can't undo a bot commit", "You still need kubectl apply"], "answer": 1},
    {"topic": "gitops", "q": "CI pushes `myapp:latest` and ArgoCD happily reports Synced. What is still broken?",
     "options": ["Nothing — Synced is Synced", "`:latest` is not a commit — the manifest never changes, so ArgoCD can't tell releases apart or roll one back",
                 "Synced actually means failed", "latest is fine in production"], "answer": 1},

    # ---------------- SkyWatch capstone (the graduation project) ----------------
    {"topic": "capstone", "q": "kube-prometheus-stack times out installing on the t3.micro API server. The two-step fix is...",
     "options": ["A bigger node", "helm install --wait --timeout 10m",
                 "Apply the CRDs with `kubectl apply --server-side` FIRST, then `helm install --skip-crds` — you need both halves", "Install Grafana alone"], "answer": 2},
    {"topic": "capstone", "q": "The rabbitmq:3.13-management image auto-starts a Prometheus metrics endpoint on which port?",
     "accept": ["15692"]},
    {"topic": "capstone", "q": "What does a ServiceMonitor actually do?",
     "options": ["Restarts unhealthy Services", "Tells Prometheus which Service and port to scrape — without it the RabbitMQ panel just says 'No data'",
                 "Monitors systemd units", "Generates a Grafana dashboard"], "answer": 1},
    {"topic": "capstone", "q": "Both k3s workers fail to join with `curl: (28) ... port 6443 ... Connection timed out`. The playbook joins on the master's PUBLIC IP. Why does that break?",
     "options": ["Port 6443 isn't open on the master at all", "k3s refuses public addresses",
                 "The token is wrong",
                 "Traffic to the public IP leaves via the internet gateway, so the security group's self-referencing rule never matches — join on the PRIVATE IP"], "answer": 3},
]

TOPIC_NAMES = {
    "linux": "🐧 Linux",
    "docker": "🐳 Docker", "git": "🌿 Git", "k8s": "☸️ Kubernetes", "helm": "⎈ Helm",
    "ansible": "📜 Ansible", "terraform": "🏗️ Terraform", "rabbitmq": "📨 RabbitMQ",
    "gitops": "🔁 GitOps/CI-CD", "foundations": "🧭 Foundations",
    "capstone": "🛰️ SkyWatch capstone",
}


# Free-text answers come in two shapes: a COMMAND to type, and a CONCEPT/value to
# name. They must be graded differently — a command has to actually start with the
# right command, while a concept may sit inside a sentence.
CMD_HEADS = {
    "docker", "git", "kubectl", "helm", "ansible", "ansible-playbook", "ansible-doc",
    "terraform", "argocd", "rabbitmqctl", "minikube", "find", "ping", "tar", "ls",
    "chmod", "chown", "grep", "ps", "kill", "df", "du", "ip", "crontab", "gzip",
    "gunzip", "cat", "mkdir", "touch", "cp", "mv", "rm", "which", "systemctl",
}


def _norm(s):
    """Whitespace and trailing punctuation are not answers."""
    return re.sub(r"\s+", " ", s.strip().lower()).strip(" .;,!?")


def is_command_answer(q):
    return _norm(q["accept"][0]).split()[0] in CMD_HEADS


def grade_text(reply, accept, command):
    """A substring test would pass "no idea, maybe git status?" — and "rm -rf /;
    git status". Commands must BE the answer (extra flags welcome); concepts may
    appear as a whole word in a sentence."""
    r = _norm(reply)
    if not r:
        return False
    for raw in accept:
        a = _norm(raw)
        if not a:
            continue
        if r == a:
            return True
        if command:
            if r.startswith(a + " "):      # `git status -s` still answers `git status`
                return True
        elif re.search(rf"(?<!\w){re.escape(a)}(?!\w)", r):
            return True
    return False


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
        command = is_command_answer(q)
        prompt = "\n> type the command: " if command else "\n> your answer: "
        try:
            reply = input(c(prompt, "yellow")).strip()
        except (EOFError, KeyboardInterrupt):
            return None
        return grade_text(reply, q["accept"], command)


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
