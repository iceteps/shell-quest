# 🗡️ Shell Quest

![selftest](https://github.com/iceteps/shell-quest/actions/workflows/selftest.yml/badge.svg)

**Learn DevOps by typing the real commands.** A terminal game: you get missions
("the app is down — fix it"), a simulated world (containers, images, networks,
git branches, a Kubernetes cluster, cloud resources), and you solve them with
the actual `ls`/`chmod`/`grep` and `docker` / `git` / `kubectl` / `helm` / `ansible` / `terraform`
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
python3 quest.py
```

> On many Linux boxes (Debian/Ubuntu, a minimal Fedora) there is no bare `python`
> — only `python3`. Every `python` below works the same way; use `python3` if
> your shell says *command not found*.

Pick a mission from the map. In-mission meta-commands:

| command | what it does |
|---|---|
| `task` | re-show the objectives and your progress |
| `hint` | a nudge for the next objective (costs 5 XP) |
| `demo` | 🎬 **watch the mission solved step-by-step** — Enter advances, `takeover` hands you the keyboard mid-run. Watching pays no XP; doing does. Already made moves? `demo!` resets the world and plays from the top. |
| `learn` | which study note this mission pairs with |
| `setup` | 🧰 how to install the real tools **on your own machine** |
| `os` | show or change which OS the real-world tips target (`os linux`) |
| `help` | everything the simulated world understands (tools + shell basics) |
| `quit` | leave the mission (no partial save) |

Objectives check **state, not keystrokes** — any correct route wins. Finish a
mission with **zero hints and no demo** for a +10 XP bonus. XP levels you up:
Rookie → Tinkerer → Operator → Engineer → Senior → DevOps Legend.

**Every objective teaches.** The moment you complete one, a 📚 one-liner tells
you the *transferable concept* you just used — and finishing a mission prints a
"what you just practiced" recap. Typos get a gentle *did-you-mean* nudge.

**The game knows which machine you're on.** First run asks: Linux, macOS or
Windows (it guesses from your platform). That one choice retunes every
real-world tip — `dnf` vs `brew` vs `winget`, whether `sudo` is even a thing,
`which` vs `where`, WSL, `podman` on a Fedora box. The simulated world is always
Linux; only the advice about *your* machine changes.

```bash
python quest.py --os linux      # or mac / windows
python quest.py --setup         # the real install steps for that OS
```

`--setup` prints the actual commands — Docker Engine, kubectl, minikube, Helm —
for your distro, including the two lines everyone forgets on Linux:
`systemctl enable --now docker` and `usermod -aG docker $USER`.

**Wrong commands teach too.** Type `winget`, `apt`, `dnf`, `podman`, `systemctl`,
`wsl`, `sudo`, `vim`, or an image name as if it were a command, and the game
recognizes the real-world tool and explains 🌍 how it maps to this world — in the
dialect of the OS you picked. No cold "command not found" walls.
The host shell also does the basics for real: `ls`, `cat`, `pwd`, `whoami`,
`mkdir`, `clear`, `history`, and a tiny `edit <file>` editor.

**The 🐧 Linux missions run a real shell.** Not a lookup table of blessed commands —
a tokenizer with bash's quoting and escaping rules, a parser that *rejects* `echo hi |`
and `;;` the way bash does, brace expansion (`file{1,2,3}.txt`), globs that skip
dotfiles and honour a trailing `/`, pipes (`|` and `|&`), `>` `>>` `<` `2>` `2>&1`,
`;` `&&` `||`, exit codes and `$?`, a working directory, permission bits that
actually *stop* things (`chmod 000 f` then `cat f` → Permission denied), background
jobs, `$(date)` substitution, and shell scripts that really run — `exit 3` sets
`$?`, `$1`/`$#` work, and a script's output pipes like any other command's.
`sort` collates like a dictionary (not ASCII) because that's what your terminal
does, and `/etc/passwd` is a real file so `cut -d: -f1 /etc/passwd` works.

It is held to that standard mechanically: `tests/test_shell_vs_bash.py` runs ~285
cases through **both** the simulated shell and your machine's real bash and fails on
any divergence — and it runs in CI.

Where a faithful simulation isn't possible, it says so instead of faking it. Type
`sleep 300` without `&` and it tells you real bash would have blocked your terminal
for five minutes — which is the entire reason `&` exists.

**Check before you install — the game rewards the habit.** `docker --version`,
`git --version`, and `which <tool>` answer like the real thing, so "is it
already installed?" is always one command away. And nothing silently runs over
existing state: re-pulling an image says *up to date*, `docker network create`
on a taken name refuses, `kubectl create` on an existing object throws
*AlreadyExists* (and explains why `apply` wouldn't), `git branch`/`checkout -b`
on an existing branch fails exactly like real git.

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
| 🐧 1 | First Contact 🐧 | **the real Linux assignment 1–3**: the filesystem tree, `chmod`, `ls -l`, `find` |
| 🐧 2 | Read the Logs 🔎 | **assignments 4–6**: grep + redirection, background jobs, `ps`/`kill`, `df`/`du` |
| 🐧 3 | Ship the Script 📜 | **assignments 7–10**: `ip a`, `ping -c`, a shebang script, cron, tar/gzip |
| 🐳 4 | Hello, Container | pull, run -dit, exec, container lifecycle |
| 🐳 5 | The Vanishing Container | debugging: ps -a, logs, root-cause, rebuild |
| 🐳 6 | Talk to Each Other | user-defined networks, name resolution |
| 🐳 7 | Ship It ⚓ | **the real Assignment 1**: Dockerfile → build → tag → login → push |
| 🌿 8 | The First Commit | status, add, commit, push -u |
| 🌿 9 | Branch Out | branches, switching, pushing a feature branch |
| 🌿 10 | The Conflict 💥 | **the real Git assignment finale**: merge conflict + resolution |
| ☸️ 11 | First Contact | **the real K8s CLI assignment**: minikube, apply -f ., services, browser |
| ☸️ 12 | Break It, Watch It Heal 🩹 | namespaces (-n!), self-healing, scale, set image |
| ☸️ 13 | Locked Down 🛡️ | **the real RBAC homework**: SA + Role + RoleBinding, auth can-i yes→no |
| ⎈ 14 | Package It | helm template/install/upgrade --set/rollback/history |
| 🔁 15 | The Robot Deploys 🤖 | GitOps loop: push → CI bumps tag → ArgoCD syncs |
| 📜 16 | Agentless Army | inventory, playbook, **idempotency**, handlers |
| 🏗️ 17 | Declare the Cloud | terraform init → plan → apply → grow → destroy |
| 📨 18 | Post Office | compose, producer/queue/consumer, decoupling |
| 🛰️ 19 | **THE CAMPAIGN** | the whole course in one run: terraform → ansible → kubectl → helm → argocd → weather through the queue → destroy |

Missions 1–3, 7, 10, 11 and 13 mirror the course's actual graded assignments —
beat them here first, then do the real thing with confidence. Mission 19 is the
capstone dress rehearsal: don't touch it until the others fall.

### 🔁 Behind on classes?

```bash
python quest.py --catchup
```

Prints the ordered route back to current — for each topic, the study note to
read, the missions to play, and which REAL graded assignment that stretch
prepares you for, with your progress marked against it. Inside the game, type
`catchup` at the mission map for the same thing.

## Your progress

Saved to `progress.json` — **gitignored**, so your XP is yours alone and never
lands in a commit. Delete the file to start fresh.

## Verify the game works (for forks / CI)

Every mission ships with a solution script. This proves all of them are completable:

```bash
python3 quest.py --selftest              # every mission is completable
python3 tests/test_shell_vs_bash.py      # the Linux shell agrees with real bash
```

## Also in this repo: ⚡ the quick quiz

`quiz/quiz.py` — a zero-setup rapid-fire quiz across **all** course topics (Linux, K8s, Helm,
Ansible, Terraform, RabbitMQ, GitOps too — broader than the missions). Perfect for a
5-minute warm-up when a full mission is too much:

```bash
python quiz/quiz.py                 # 12 random questions
python quiz/quiz.py --topic linux   # drill one topic
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
