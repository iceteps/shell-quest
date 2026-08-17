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

Runs every mission's embedded `solution` script and fails if any mission can't be completed. **Run it after ANY change to engine or missions — a PR/commit with a failing selftest is broken.**

The second one, for anything touching the 🐧 Linux shell:

```bash
python tests/test_shell_vs_bash.py
```

Runs ~300 command cases through both the simulated shell and the machine's **real bash**, and fails on any divergence, and lints that every command on the shell's `$PATH` has a help page. Both run in CI. Note what the selftest does NOT prove: it only replays winning solutions, so it says a mission is *completable*, never that it is *pleasant*. Wrong commands, meta-commands and quitting are a separate pass — play the mission by hand before shipping it.

## How the game works (design rules)

- `engine.py` holds a `World` (containers, images, networks, host files, git state, and an optional `k8s` cluster with a real reconcile loop — deleting an owned pod respawns it) and simulates commands against it. Missions win by **checking world state, never by matching keystrokes** — any correct command route must win.
- Engine-native commands: `docker` (incl. `docker compose`), `git`, `kubectl`, `minikube`. Helm/ansible/terraform/argocd/pika live as **mission-local handlers** in their topic modules (`helm_release.py`, `ansible_ops.py`, `ansible_lab.py`, `terraform_infra.py`, `gitops_ci.py`, `rabbitmq_queue.py`) per the promote-only-when-2+-missions-need-it rule.
- Missions are dicts: `world` (starting state — `files`, and `flags` for state a previous mission left behind), `objectives` (each: `desc`, `xp`, `hint`, `check(world)` lambda), `solution` (proves completability — mandatory; it also powers `demo`), optional `handlers` (regex → function, for behavior the engine doesn't simulate natively; they run BEFORE generic dispatch and can override anything), and optional UI hooks `prompt(world)`, `help_fn(io)` and `complete(world, text)` — a mission with its own shell draws its own prompt, prints its own manual and offers its own Tab-completion.
- Hints cost 5 XP; finishing hint-free earns +10. Keep XP values in the ranges the existing missions use.
- Every mission has a **`teach` list** — one micro-lesson string per objective (same order). It prints the moment that objective completes and again in the end-of-mission recap. The selftest's lint **fails** if lengths don't match. Write the *transferable concept*, not a restatement of the desc.
- The selftest runs a **lint pass first** (required fields, unique ids, topic registered, XP 5–40, teach parity, handler regexes compile) — contributor mistakes fail fast in CI (`.github/workflows/selftest.yml`).
- **Vault sync**: `save_profile` renders a markdown progress note into the player's Obsidian vault if `quest.config.json` (gitignored) points at one — see `--link-vault`. `sync_vault_note` must never raise into the game.
- **`study.py` is the study half — `learn` reads the player's own vault** (`--vault <folder>`, or inferred from the linked progress note's folder). It parses structure the notes already have rather than asking authors for new markup: `## Self-check quiz` + `> [!question]-` → the quiz, `## 🃏 Flashcards` + `> [!question]-` → the deck, `## Drills` + `- [ ] **(10 XP) Name.**` → side quests. Rules: it renders markdown to the terminal itself (no dependencies), pages long output, and **every** vault read is guarded — an unmounted drive or a note with no headings must degrade to a message, never an exception (the engine wraps the whole call too). Reading pays **no** XP; the **Scholar bonus** (+5) is paid at mission end for consulting the note with zero hints, which is the only way study touches the economy. Mastery (cards known, best quiz score, 🌟 perfect decks, badges) lives in `profile["study"]`/`profile["badges"]`, is saved the moment it is earned, and is written back into the vault progress note.
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
- **`missions/linux_shell.py` is a real shell, and it is held to bash's behaviour.** The
  engine's host world is one flat folder with no cwd, permissions or processes — right for
  docker/k8s, wrong for Linux. Rather than reshape a world 16 missions depend on, the Linux
  missions register a catch-all `(r"(?!(?:quit|exit)\s*$).+", shell)` handler (handlers
  dispatch first; `quit`/`exit` must fall through or the player is trapped) and run that
  module: tokenizer with bash's quoting/escaping rules, a `check_syntax()` parser pass that
  REJECTS `echo hi |` / `;;` / a leading `|` the way bash does, brace expansion, globbing
  (dotfiles hidden unless the pattern leads with `.`; a trailing `/` means directories only),
  pipes (`|` and `|&`), `> >> < 2> 2>&1 1>&2`, `; && ||`, exit codes, cwd, mode bits that
  actually deny (a file's read bit AND a directory's write/search bits — `chmod 500 d` then
  `touch d/f` is Permission denied), processes, cron and tar over `world.files` +
  `world.flags`. `missions/linux_basics.py` holds only mission data — including the
  `WORKSPACE` files missions 2 and 3 start with, because they continue mission 1's story and
  a directory the student built an hour ago must still be there.
  - Every builtin returns `Res(out, err, code, nonl)`. `out` is the stream minus at most one
    trailing newline and `nonl` says whether that newline was there — that is what makes
    `wc -c` honest about `echo a > f` (2) vs `printf a > f` (1). Use `stream(text)` to build
    a Res from raw bytes.
  - `run_line(world, io, line)` is the single entry point for one command line, and
    `run_script()` feeds a script's lines through it — so `exit 3`, `$1`, pipes and
    redirection inside a script behave exactly as at the prompt. `shell()` is a thin wrapper.
  - `sort`/`ls`/globs order with `collate()` (glibc dictionary collation), not codepoints.
    `Gamma` after `alpha` is what the student's terminal shows.
  - **It has to FEEL like a terminal too**, or the simulation is beside the point. The prompt
    carries the cwd (`prompt(world)`), arrow keys and Tab work (engine imports `readline` and
    binds `mission["complete"]`; colour codes in a prompt are fenced with `\001…\002` or
    readline miscounts the column), and `clear` goes out through `io.write()` raw — printed as
    a line, its escape leaves the cursor one row down. Without readline the arrows arrive as
    raw bytes; `edit_keys()` strips them and says so rather than running a line the player
    never typed.
  - **Every command answers `--help`, and it is the same page as `help <cmd>` and `man <cmd>`.**
    Pages live in `missions/linux_help.py` as structured records (usage · summary · the flags
    THIS shell implements · notes · examples · the real tool's flags we don't simulate), so
    they stay honest in both directions, render as plain text (help must pipe), and end by
    pointing at the real binary on the player's own machine. Add a command → add its page;
    the shell test fails on any `$PATH` command without one. `help` with no argument prints
    the grouped index, topics (`redirection`, `pipes`, `globs`, `variables`, `scripts`,
    `permissions`) included.
  **The bar is behavioural honesty, not feature count.** `tests/test_shell_vs_bash.py` runs
  ~300 cases through BOTH this shell and the machine's real bash and fails on any divergence
  — it runs in CI next to the selftest. Add a case there for anything you touch. Two waiver
  sets exist and both are linted against `CASES` (a stale name fails the run): `ENV_SPECIFIC`
  for machine facts, `ORDER_FREE` for output whose order the filesystem doesn't define
  (`find`, `tar -t`). Where a faithful simulation isn't possible (a command that would block,
  a tool that isn't installed), say so in the shell's voice rather than faking success — e.g.
  foreground `sleep` explains that real bash blocks instead of silently backgrounding, which
  is the whole point of teaching `&`. Deliberate omissions (documented in the module
  docstring): `$(…)` only wraps side-effect-free commands, no awk/sed, and `&` backgrounds
  only at the end of a line.
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

Adding a topic also touches three things that silently go stale: `TOPICS`/`ALL_MISSIONS` order in `missions/__init__.py` (it drives both the map's numbering and "next up"), the `CATCHUP_ROUTE` table in `quest.py` (the ordered path for a student returning after missed classes — topic, note, and which REAL graded assignment it prepares for), and the README's mission table, which is numbered. Current state: **30 missions, 120 quiz questions**.

## Repo hygiene

Owner: `iceteps`. The teacher (`yfreifeld`) may become a collaborator — keep history clean and messages descriptive. This repo is public and meant to be forked by students; never commit anything personal (progress, tokens, names beyond what's already public).
