# 🗡️ Shell Quest

![selftest](https://github.com/iceteps/shell-quest/actions/workflows/selftest.yml/badge.svg)

**Learn DevOps by typing the real commands.** A terminal game: you get missions
("the app is down — fix it"), a simulated world (containers, images, networks,
git branches, a Kubernetes cluster, cloud resources), and you solve them with
the actual `docker` / `git` / `kubectl` / `helm` / `ansible` / `terraform`
commands — against an engine that responds like the real tools do.

No dependencies. Pure Python 3.8+ standard library. Windows/macOS/Linux.

```
═══════════════════════════════════════════════════
  🗡️  MISSION: The Vanishing Container 🕵️
═══════════════════════════════════════════════════
The demo is in 5 minutes and the app is DOWN...

$ docker ps
CONTAINER ID  IMAGE  STATUS  ...
(nothing)

$ docker ps -a
a1b2c3ef      my-flask-app   Exited (1) 2 minutes ago   webapp
  ✔ OBJECTIVE COMPLETE: Find the dead container  (+10 XP)

$ docker logs webapp
ModuleNotFoundError: No module named 'flask'
  ✔ OBJECTIVE COMPLETE: Read the crash logs  (+15 XP)
```

## Play

```bash
python quest.py
```

Pick a mission from the map. In-mission meta-commands:

| command | what it does |
|---|---|
| `task` | re-show the objectives and your progress |
| `hint` | a nudge for the next objective (costs 5 XP) |
| `demo` | 🎬 **watch the mission solved step-by-step** — Enter advances, `takeover` hands you the keyboard mid-run. Watching pays no XP; doing does. |
| `learn` | which study note this mission pairs with |
| `quit` | leave the mission (no partial save) |

Objectives check **state, not keystrokes** — any correct route wins. Finish a
mission with **zero hints and no demo** for a +10 XP bonus. XP levels you up:
Rookie → Tinkerer → Operator → Engineer → Senior → DevOps Legend.

**Every objective teaches.** The moment you complete one, a 📚 one-liner tells
you the *transferable concept* you just used — and finishing a mission prints a
"what you just practiced" recap. Typos get a gentle *did-you-mean* nudge.

**New to a topic? The demo loop:** run the mission once with `demo` and just
watch the commands and their real outputs; then replay it yourself for the XP.

**📓 Obsidian player? Link your vault** and the game keeps a live progress note
(mission checklist, XP, per-topic completion) inside it — updated on every save:

```bash
python quest.py --link-vault "<your-vault>/Shell Quest Progress.md"
```

## Missions

| # | mission | trains |
|---|---|---|
| 🐳 1 | Hello, Container | pull, run -dit, exec, container lifecycle |
| 🐳 2 | The Vanishing Container | debugging: ps -a, logs, root-cause, rebuild |
| 🐳 3 | Talk to Each Other | user-defined networks, name resolution |
| 🐳 4 | Ship It ⚓ | **the real Assignment 1**: Dockerfile → build → tag → login → push |
| 🌿 5 | The First Commit | status, add, commit, push -u |
| 🌿 6 | Branch Out | branches, switching, pushing a feature branch |
| 🌿 7 | The Conflict 💥 | **the real Git assignment finale**: merge conflict + resolution |
| ☸️ 8 | First Contact | **the real K8s CLI assignment**: minikube, apply -f ., services, browser |
| ☸️ 9 | Break It, Watch It Heal 🩹 | namespaces (-n!), self-healing, scale, set image |
| ☸️ 10 | Locked Down 🛡️ | **the real RBAC homework**: SA + Role + RoleBinding, auth can-i yes→no |
| ⎈ 11 | Package It | helm template/install/upgrade --set/rollback/history |
| 🔁 12 | The Robot Deploys 🤖 | GitOps loop: push → CI bumps tag → ArgoCD syncs |
| 📜 13 | Agentless Army | inventory, playbook, **idempotency**, handlers |
| 🏗️ 14 | Declare the Cloud | terraform init → plan → apply → grow → destroy |
| 📨 15 | Post Office | compose, producer/queue/consumer, decoupling |
| 🛰️ 16 | **THE CAMPAIGN** | the whole course in one run: terraform → ansible → kubectl → helm → argocd → weather through the queue → destroy |

Missions 4, 7, 8, and 10 mirror the course's actual graded assignments — beat
them here first, then do the real thing with confidence. Mission 16 is the
capstone dress rehearsal: don't touch it until the others fall.

## Your progress

Saved to `progress.json` — **gitignored**, so your XP is yours alone and never
lands in a commit. Delete the file to start fresh.

## Verify the game works (for forks / CI)

Every mission ships with a solution script. This proves all of them are completable:

```bash
python quest.py --selftest
```

## Also in this repo: ⚡ the quick quiz

`quiz/quiz.py` — a zero-setup rapid-fire quiz across **all** course topics (K8s, Helm,
Ansible, Terraform, RabbitMQ, GitOps too — broader than the missions). Perfect for a
5-minute warm-up when a full mission is too much:

```bash
python quiz/quiz.py                 # 12 random questions
python quiz/quiz.py --topic git     # drill one topic
```

Same repo on purpose (monorepo!): one clone gets you both games, and adding a course
topic updates missions and quiz questions in a single commit.

## Fork it, make it yours 🍴

This repo is built to be forked by classmates:

1. Fork / clone it.
2. `python quest.py` — your own `progress.json` is created locally (never committed).
3. Add missions! Drop a dict into any `missions/*.py` topic module
   (or a new one — register it in `missions/__init__.py`). A mission is:
   - `world` — starting state (images, containers, files, git branches)
   - `objectives` — each with a `check(world)` lambda, XP, and a hint
   - `solution` — the command list that proves it's beatable (`--selftest` runs it)
   - optional `handlers` — regex-triggered custom responses for anything the engine
     doesn't simulate natively
4. Run `python quest.py --selftest` before you PR. That's the whole CI.

**Roadmap ideas (PRs welcome):** a timed "incident mode", multi-mission
campaigns (SkyWatch end-to-end), a shared hall-of-fame file.

---

*Built as a study companion for a DevOps course — missions pair with the course's
class topics (each mission's `learn` command names its matching study note).*
