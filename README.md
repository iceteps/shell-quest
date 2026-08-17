# 🗡️ DevOps Experts Quest

![selftest](https://github.com/iceteps/shell-quest/actions/workflows/selftest.yml/badge.svg)

*(the repo is `shell-quest` — the game's title card calls it DevOps Experts Quest)*

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
| `/task` | re-show the objectives and your progress |
| `/hint` | a nudge for the next objective (costs 5 XP) |
| `/demo` | 🎬 **watch the mission solved step-by-step** — Enter advances, `takeover` hands you the keyboard mid-run. Watching pays no XP; doing does. Already made moves? `demo!` resets the world and plays from the top. |
| `/learn` | 📖 **the Codex** — opens this mission's note from your vault, right in the terminal. `learn cards` drills its flashcards, `learn quiz` runs its self-check, `learn drills` lists its side quests, `learn find <word>` searches every note |
| `/setup` | 🧰 how to install the real tools **on your own machine** |
| `/os` | show or change which OS the real-world tips target (`os linux`) |
| `/theme` | 🎨 the prompt's look: `classic` (real bash, the default) · `kali` (`┌──(root㉿quest-host)-[~]` / `└─$`) · `lean` · `rainbow`. Add a glyph set — `theme lean nerd` if you have a Nerd Font, `theme ascii` if your terminal has none. `theme emoji wide\|narrow` if right-hand columns look ragged; the menu prints a ruler that tells you which your terminal is |
| `/complete` | ⌨ Tab-completion, **off by default**. `complete on` turns it on; it costs **1 XP each time it finishes a word for you** (never more than 10 a mission), and listing the candidates with a double-Tab is always free |
| `/help` | the manual: every command this mission understands, grouped, with a real page behind each (`help ls`, `ls --help`, `man ls`) |
| `/quit` | leave the mission (no partial save) |

The leading slash is optional everywhere — `task` and `/task` are the same
command — but it is what keeps game commands and the simulated world apart:
`/tmp/deploy.sh` is still a path, and still runs.

### 🗺️ The mission map

One scrollable page: the banner at the top of it, every mission under it, and the
window opens on the banner every time you start. Scroll and the banner slides
away like anything else on a page — nothing is ever chopped into "page 2 of 3".
Resize the window and it reflows immediately, the way `btop` does; too small to
lay out at all (under 52×16) and it says so instead of drawing a broken screen.

**Keys act on the keypress — no Enter.** Only keys that could never be part of an
answer are bound, so `2` still means mission 2:

| key | does |
|---|---|
| `↑` `↓` | scroll a few lines |
| `PgUp` `PgDn` (or `←` `→`) | half a screen |
| `Home` | back to the banner · `End` the bottom |
| `Esc` | clear what you typed |

| on the map | what it does |
|---|---|
| `<number>` | play that mission — the numbers never move, wherever you've scrolled to |
| `/catchup` | the ordered route back to the class you're on; **type a number on the route to start that mission** |
| `/learn` | 📖 the Codex, browsable: pick a note by number, by `class 3`, or by any word in its name |
| `/theme` | a live menu — type `kali` and the sample prompt redraws under your pick |
| `/complete` | `on` / `off`, toggled in place, with the XP price restated |
| `/setup` | the real install steps; type `linux` / `mac` / `windows` to see another machine's |
| `/help` | this list, in the game · `/quit` leaves |

Every screen that lists options accepts one: `quit` steps back a level, and a
bare Enter just gives you a fresh prompt, so a stray keystroke can never drop you
out of what you were reading.

Objectives check **state, not keystrokes** — any correct route wins. Finish a
mission with **zero hints and no demo** for a +10 XP bonus, and **+5 more** if you
looked the answer up in the notes instead of asking for a hint (the Scholar
bonus — that's the professional reflex, so the economy pays for it). XP levels
you up: Rookie → Tinkerer → Operator → Engineer → Senior → DevOps Legend.

**Every objective teaches.** The moment you complete one, a 📚 one-liner tells
you the *transferable concept* you just used — and finishing a mission prints a
"what you just practiced" recap. Typos get a gentle *did-you-mean* nudge.

### 📖 The Codex — your study vault, playable

The course notes are a companion Obsidian vault
([devops-study-vault](https://github.com/iceteps/devops-study-vault)): one note per
class, each with a self-check quiz, a flashcard deck, and XP drills. Point the game
at it once and `learn` stops being a filename:

```bash
git clone https://github.com/iceteps/devops-study-vault
python quest.py --vault devops-study-vault
```

| in a mission | what you get |
|---|---|
| `learn` | the note's TL;DR and section list — `learn 7` or `learn cron` opens one |
| `learn cards` | 🃏 flashcard drill; cards you miss come back until they stick. Mastery is saved per note, and a clean first-try run earns a 🌟 badge |
| `learn quiz` | 🧪 the note's own self-check questions — answer, reveal, grade yourself; your best score is kept |
| `learn drills` | 🔬 the note's side quests, with the XP the note assigns them |
| `learn find <word>` | 🔎 searches every note in the vault and tells you which section to read |

Typing `learn` on the mission map opens the Codex itself: every note, in course
order, with how much of each deck you've mastered. None of it pays XP for reading —
**doing scores** — but studying instead of asking for hints is worth +5 XP a
mission, and what you master is written back into the vault's progress note, so
Obsidian shows both halves of the loop.

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

It is held to that standard mechanically: `tests/test_shell_vs_bash.py` runs ~300
cases through **both** the simulated shell and your machine's real bash and fails on
any divergence — and it runs in CI.

It also *feels* like a terminal: the prompt carries your working directory
(`[root@quest-host ~/linux_course/week1]#`), the arrow keys walk your history and
edit the line, Tab completes commands and paths, and `clear` really clears.

Every command has a manual behind it. `chmod --help`, `help chmod` and `man chmod`
all print the same page — usage line, the flags this shell honours, worked
examples, and an honest list of the flags the *real* tool has that this one
doesn't. `help` alone indexes them all, including pages for the ideas:
`help redirection`, `help pipes`, `help globs`, `help scripts`.

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

30 of them, in course order. The numbers match the mission map in the game.

| # | mission | trains |
|---|---|---|
| 🐧 1 | First Contact 🐧 | **the real Linux assignment 1–3**: relative vs absolute `cd`, the tree, `chmod`, `ls -l`/`ls -a`, `find`, `cp` |
| 🐧 2 | Read the Logs 🔎 | **assignments 4–6**: grep + redirection, `ps \| grep` into a file, background jobs, TERM before KILL, `df`/`du` |
| 🐧 3 | Ship the Script 📜 | **assignments 7–10**: `ip a`, `ping -c`, a shebang script, cron (**and the `$(date)` quoting trap**), tar/gzip |
| 🐧 4 | Dry Run 🧪 | **the 3 extra exercises**: cp vs mv, the `/tmp/ex1` drill, `>` truncating vs `>>`, reading octal off `ls -l`, a tar round trip, `rmdir` vs `rm -r` |
| 🐳 5 | Hello, Container | pull, run -dit, exec, the writable layer — stop keeps it, `rm` destroys it |
| 🐳 6 | The Vanishing Container | debugging: ps -a, logs, root-cause, rebuild |
| 🐳 7 | Talk to Each Other | default bridge vs user-defined network, name resolution |
| 🐳 8 | Ship It ⚓ | **the real Assignment 1**: Dockerfile → build → tag → login → push |
| 🐳 9 | Build It Twice 🧱 | **the Lab B image lab**: layer caching, `CACHED` vs a broken cache, slim vs full, COPY order, `-p HOST:CONTAINER` |
| 🌿 10 | The First Commit | `git config`, clone, status, add, commit, push -u |
| 🌿 11 | Branch Out | branches, surgical staging, `git diff` vs `--staged`, pushing a feature branch |
| 🌿 12 | The Conflict 💥 | **the real Git assignment finale**: a clean merge, a conflict + resolution, `log --oneline --graph` |
| 🌿 13 | Damage Control 🚑 | **the graded bonus section**: `restore --staged`, `--amend`, `.gitignore` + `rm --cached`, stash, revert vs `reset --hard`, tags |
| ☸️ 14 | First Contact | **the real K8s CLI assignment**: minikube, apply -f ., services, logs, browser, teardown |
| ☸️ 15 | Break It, Watch It Heal 🩹 | namespaces (-n!), self-healing (and the bare Pod that nobody heals), scale, set image, `explain` |
| ☸️ 16 | Locked Down 🛡️ | **the real RBAC homework**: SA + Role + RoleBinding, auth can-i yes→no, the three Service types |
| ☸️ 17 | Day-2 Ops 🚑 | **the real Day-2 assignment**: probes, requests/limits, `maxUnavailable: 0`, ImagePullBackOff → `rollout undo`, a zero-endpoints selector bug |
| ⎈ 18 | Package It | helm template/install/upgrade --set/rollback/history |
| ⎈ 19 | Ship It To Dev ⎈ | **Assignment A**: values files, `-n dev --create-namespace`, `upgrade --install`, `--set` beats `-f`, image tags, rollback |
| 🔁 20 | The Robot Deploys 🤖 | GitOps loop: push → CI bumps tag → ArgoCD syncs (and why `:latest` is not a deploy) |
| 🔁 21 | Drift and the Undo Button 🧲 | reading an Application manifest, `selfHeal` undoing `kubectl scale`, rollback by `git revert` + push |
| 📜 22 | Agentless Army | inventory, ad-hoc modules, `--check`, playbook, **idempotency**, handlers |
| 📜 23 | Playbook Pro 📜 | the group-name gotcha, `--tags`, `register` + `when`, per-host Jinja2, building a role |
| 📜 24 | Automation Alchemist 🧪 | **the class-14 dockerized lab**: compose up, SSH keys propagating, host vs control node, both playbooks, a 3rd node in three files |
| 🏗️ 25 | Declare the Cloud | terraform init → validate → plan → apply → the implicit dependency graph → destroy |
| 🏗️ 26 | Day Two 🔐 | variables, outputs living in state, a `~` diff, locking an open SSH rule, migrating state to an S3 backend |
| 📨 27 | Post Office | compose, producer/queue/consumer, durability, ack vs auto-ack, exchanges, the management API |
| 📨 28 | Ticket Rail 🍳 | competing consumers, blind round-robin vs `prefetch_count=1` fair dispatch, killing a worker mid-message |
| 🛰️ 29 | **THE CAMPAIGN** | the whole course in one run: terraform → ansible → kubectl → helm → argocd → weather through the queue → destroy |
| 🛰️ 30 | Eyes on the Sky 📊 | capstone part 4: CRDs `--server-side`, `helm install --skip-crds`, a RabbitMQ ServiceMonitor on 15692, Grafana on a NodePort |

Missions 1–4, 8, 12, 13, 14, 16, 17, 19 and 24 mirror the course's actual graded
assignments and labs — beat them here first, then do the real thing with
confidence. Missions 29–30 are the capstone dress rehearsal: don't touch them
until the others fall.

### 🔁 Behind on classes?

```bash
python quest.py --catchup
```

Prints the ordered route back to current — for each topic, the study note to
read, the missions to play, and which REAL graded assignment that stretch
prepares you for, with your progress marked against it. Inside the game,
`/catchup` shows the same route — and typing a number on it starts that mission.

## Your progress

Saved to `progress.json` — **gitignored**, so your XP is yours alone and never
lands in a commit. Delete the file to start fresh.

## Verify the game works (for forks / CI)

Every mission ships with a solution script. This proves all of them are completable:

```bash
python3 quest.py --selftest                  # every mission is completable
python3 tests/test_shell_vs_bash.py          # the Linux shell agrees with real bash
python3 tests/test_keys_and_commands.py      # the map screen, driven through a real pty
```

The third one makes a terminal (`pty.fork`) and plays the game through it: keys
that act without Enter, escape sequences that must not arrive as text, `/commands`,
the Codex browser, reflow on resize, and no line ever wider than the window. It
parks your `progress.json` and puts it back afterwards. All three run in CI.

## Also in this repo: ⚡ the quick quiz

`quiz/quiz.py` — a zero-setup rapid-fire quiz across **all** course topics: 120 questions
over Linux, Docker, Git, K8s, Helm, Ansible, Terraform, RabbitMQ, GitOps, the SkyWatch
capstone and the DevOps foundations — broader than the missions. Perfect for a
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
