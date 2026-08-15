# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository. (No Claude? It works as a CONTRIBUTING guide too.)

## What this is

A terminal learning game for a DevOps course: students type **real** `docker`/`git`/`kubectl`/`helm`/`ansible`/`terraform` commands against a simulated world. Two entry points:

- `quest.py` — mission-based game (the main event). Engine in `engine.py`, missions in `missions/*.py`.
- `quiz/quiz.py` — standalone rapid-fire quiz across all course topics.

**Hard constraints:** pure Python standard library (no pip installs, ever — students must be able to `git clone` + run), Windows/macOS/Linux compatible (UTF-8 is force-configured; keep it), and `progress.json` stays gitignored (players' personal state).

## The one command that matters

```bash
python quest.py --selftest
```

Runs every mission's embedded `solution` script and fails if any mission can't be completed. **Run it after ANY change to engine or missions — a PR/commit with a failing selftest is broken.** There is no other test suite; the selftest is the CI.

## How the game works (design rules)

- `engine.py` holds a `World` (containers, images, networks, host files, git state, and an optional `k8s` cluster with a real reconcile loop — deleting an owned pod respawns it) and simulates commands against it. Missions win by **checking world state, never by matching keystrokes** — any correct command route must win.
- Engine-native commands: `docker` (incl. `docker compose`), `git`, `kubectl`, `minikube`. Helm/ansible/terraform/argocd/pika live as **mission-local handlers** in their topic modules (`helm_release.py`, `ansible_ops.py`, `terraform_infra.py`, `gitops_ci.py`, `rabbitmq_queue.py`) per the promote-only-when-2+-missions-need-it rule.
- Missions are dicts: `world` (starting state), `objectives` (each: `desc`, `xp`, `hint`, `check(world)` lambda), `solution` (proves completability — mandatory; it also powers `demo`), optional `handlers` (regex → function, for behavior the engine doesn't simulate natively; they run BEFORE generic dispatch and can override anything).
- Hints cost 5 XP; finishing hint-free earns +10. Keep XP values in the ranges the existing missions use.
- Every mission has a **`teach` list** — one micro-lesson string per objective (same order). It prints the moment that objective completes and again in the end-of-mission recap. The selftest's lint **fails** if lengths don't match. Write the *transferable concept*, not a restatement of the desc.
- The selftest runs a **lint pass first** (required fields, unique ids, topic registered, XP 5–40, teach parity, handler regexes compile) — contributor mistakes fail fast in CI (`.github/workflows/selftest.yml`).
- **Vault sync**: `save_profile` renders a markdown progress note into the player's Obsidian vault if `quest.config.json` (gitignored) points at one — see `--link-vault`. `sync_vault_note` must never raise into the game.
- **Demo mode** (`demo` in a fresh mission) replays the `solution` step-by-step: Enter advances, `takeover` hands control back mid-run. Objectives completed during demo pay 0 XP and an all-demo run is never recorded — watching teaches, doing scores. Because `solution` doubles as the demo script, keep solutions clean and pedagogically ordered (inspect → act → verify), not just minimal.
- Error messages should mimic the real tools' output (e.g. `denied: requested access to the resource is denied`) — the authenticity is the pedagogy. A dim parenthetical teaching hint after a realistic error is the house style.
- **Unknown commands teach, never scold.** `REAL_WORLD` in `engine.py` maps common real-world commands (winget/apt/wsl/sudo/vim/image-names-as-commands/…) to a 🌍 micro-lesson explaining how the real tool maps to the sim; `MISSION_TOOLS` redirects tools that live in other missions. Teach-only/unknown responses set `world.flags["_noop"]` so they don't count as "moves" (and so `demo` stays available); a state-mutating command must never set it. `demo!` resets the world mid-mission to allow watching after moves. When adding a fallback error branch, set `_noop` there too.
- **The player's OS is a teaching layer, never a world change.** The simulated host is
  *always* Linux (`pwd` → `/root/quest`, `uname` → Linux) — that never varies. What varies is
  the 🌍 advice about the player's REAL machine: `PLAYER_OS` (+ `PLAYER_DISTRO`, sniffed from
  `/etc/os-release`) is set from `profile["os"]` in `progress.json`, asked once at first run,
  changeable with `os <name>` in a mission or `--os`. `REAL_WORLD` values may be a
  `(head, follow)` tuple **or a callable returning one** — use a callable + `pick({...})` when
  the lesson genuinely differs per OS (`wsl`, `sudo`, `ipconfig`, package managers, `podman`).
  Resolve entries through `real_world_entry()`/`in_real_world()`, never by indexing the dict.
  `print_setup()` / `SETUP_STEPS` hold the real install commands per OS — keep them accurate,
  they are commands people paste. When adding real-world guidance anywhere, ask "is this true
  on all three?" — if not, it belongs in a `pick({...})`.
- **`missions/linux_basics.py` carries its own shell**, deliberately. The engine's host world
  is one flat folder with no cwd, permissions or processes — right for docker/k8s, wrong for
  Linux. Rather than reshape a world 16 missions depend on, that module registers a catch-all
  `(r".+", _shell)` handler (handlers dispatch before generic dispatch) and implements cwd,
  a directory set, mode bits, processes, pipes and redirection over `world.files` +
  `world.flags`. Anything it doesn't know falls back to the shared `REAL_WORLD` atlas, so the
  teach-don't-scold rule still holds. Extend that module for Linux behaviour; don't push it
  into `engine.py` unless a non-Linux mission needs it too.
- **Prerequisite realism — check-first works, already-exists refuses.** Version checks (`docker --version`/`version`, `git --version`, `which`/`where`) answer like real tools and are `_noop` (pure inspection). Existing state is never silently run over: repeat `docker pull` → *Image is up to date*, duplicate `docker network create` → daemon error, `kubectl create` on existing → *AlreadyExists* + apply-vs-create hint, duplicate `git branch`/`checkout -b` → *fatal: already exists*, `mkdir` on existing → *File exists* (`-p` stays quiet). When simulating a new command, model the real tool's already-exists behavior, not just its happy path.

## Adding a mission

1. Add the dict to the right topic module in `missions/` (or a new module — register it in `missions/__init__.py`: extend `ALL_MISSIONS` and `TOPICS`).
2. If the topic needs commands the engine lacks, prefer a mission-local `handler` first; promote to a generic `engine.py` command only when 2+ missions need it.
3. Mirror the course's REAL graded assignments when one exists (missions 4 and 5–7 do this) — the game is a risk-free rehearsal for them.
4. Every objective needs a hint; every mission needs a `solution`; run `--selftest`.

## Adding quiz questions

`quiz/quiz.py` → `QUESTIONS` list at the top. Two formats: multiple-choice (`options` + `answer` index) or type-the-command (`accept` = list of lenient lowercase substrings). Options are shuffled at runtime — never encode "the answer is always b". Add the topic to `TOPIC_NAMES` if it's new.

## When the course advances

When a new class/topic appears in the course (upstream: `yfreifeld/devops-course`), extend BOTH games in one commit: new mission(s) if the topic is hands-on simulatable, plus quiz questions for the topic. Companion study notes live at https://github.com/iceteps/devops-study-vault — each mission's `vault_note` field names its note; keep them consistent.

Adding a topic also touches three things that silently go stale: `TOPICS`/`ALL_MISSIONS` order in `missions/__init__.py` (it drives both the map's numbering and "next up"), the `CATCHUP_ROUTE` table in `quest.py` (the ordered path for a student returning after missed classes — topic, note, and which REAL graded assignment it prepares for), and the README's mission table, which is numbered. Current state: **19 missions, 81 quiz questions**.

## Repo hygiene

Owner: `iceteps`. The teacher (`yfreifeld`) may become a collaborator — keep history clean and messages descriptive. This repo is public and meant to be forked by students; never commit anything personal (progress, tokens, names beyond what's already public).
