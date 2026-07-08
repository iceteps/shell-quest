# 🗡️ Shell Quest

**Learn DevOps by typing the real commands.** A terminal game: you get missions
("the app is down — fix it"), a simulated world (containers, images, networks,
git branches), and you solve them with the actual `docker` and `git` commands —
against an engine that responds like the real tools do.

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
| `learn` | which study note this mission pairs with |
| `quit` | leave the mission (no partial save) |

Objectives check **state, not keystrokes** — any correct route wins. Finish a
mission with **zero hints** for a +10 XP bonus. XP levels you up:
Rookie → Tinkerer → Operator → Engineer → Senior → DevOps Legend.

## Missions (v1)

| # | mission | trains |
|---|---|---|
| 🐳 1 | Hello, Container | pull, run -dit, exec, container lifecycle |
| 🐳 2 | The Vanishing Container | debugging: ps -a, logs, root-cause, rebuild |
| 🐳 3 | Talk to Each Other | user-defined networks, name resolution |
| 🐳 4 | Ship It ⚓ | **the real Assignment 1**: Dockerfile → build → tag → login → push |
| 🌿 5 | The First Commit | status, add, commit, push -u |
| 🌿 6 | Branch Out | branches, switching, pushing a feature branch |
| 🌿 7 | The Conflict 💥 | **the real Git assignment finale**: merge conflict + resolution |

Missions 4 and 7 mirror the course's actual graded assignments — beat them here
first, then do the real thing with confidence.

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
3. Add missions! Drop a dict into `missions/docker_basics.py` / `missions/git_basics.py`
   (or a new topic module — register it in `missions/__init__.py`). A mission is:
   - `world` — starting state (images, containers, files, git branches)
   - `objectives` — each with a `check(world)` lambda, XP, and a hint
   - `solution` — the command list that proves it's beatable (`--selftest` runs it)
   - optional `handlers` — regex-triggered custom responses for anything the engine
     doesn't simulate natively
4. Run `python quest.py --selftest` before you PR. That's the whole CI.

**Roadmap ideas (PRs welcome):** kubectl missions, helm missions, an ansible
inventory mission, a timed "incident mode", a shared hall-of-fame file.

---

*Built as a study companion for a DevOps course — missions pair with the course's
class topics (each mission's `learn` command names its matching study note).*
