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

Runs ~300 command cases through both the simulated shell and the machine's **real bash**, and fails on any divergence, and lints that every command on the shell's `$PATH` has a help page.

The third, for anything touching the map screen, the prompt or the menus:

```bash
python tests/test_keys_and_commands.py
```

**All three run in CI.** Note what the selftest does NOT prove: it only replays winning solutions, so it says a mission is *completable*, never that it is *pleasant*. Wrong commands, meta-commands and quitting are a separate pass — play the mission by hand before shipping it.

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
- **The chrome fills the terminal; simulated output never reflows.** `engine.py`'s layout block (`menu_width`, `disp_width`, `pad`/`fit`, `spread`/`leader`/`heading`/`rule`, `fit_columns`+`grid`, `box`, `wrap_text`, `meter`) is what the map, banner, catch-up route, objective lists, `setup`, the shell's `help` index and the Codex list are drawn with: width is measured at DRAW time (so a resized window is simply right on the next screen), entries stretch to the edge with a dotted leader and tile into 2–3 columns when there's room, and everything degrades to the plain list on a narrow one. Squeezed, a row drops the dots, then the right-hand value — never the title. `QUEST_WIDTH=<cols>` caps it. **Never** run command output through these: `ls`, `kubectl get`, `terraform plan` print what the real tool prints, and `tests/test_shell_vs_bash.py` fails anything else. `disp_width` is the only honest measure of a line (colour codes are free, an East-Asian-wide emoji is two columns, and `☸`+VS16 is whatever the next bullet says it is) — `len()` will drift every table that carries an emoji.
- **The prompt is a theme, and assists cost XP.** `missions/prompt_theme.py` rebuilds powerlevel10k's look (two-line prompt, segments that appear only when they have something to say, `lean` text style and `rainbow` 256-colour blocks) over three glyph tiers — `nerd` / `unicode` / `ascii` — because we cannot see the player's font. **`classic` stays the default**: the plain bash prompt is the one waiting on their first real server. A themed prompt returns ONE string containing a newline; `run_mission` prints everything above the last line and hands only the last to readline, which is what keeps non-ASCII glyphs off readline's ruler (see `rl_prompt`). Tab-completion is an assist: `profile["assists"]["complete"]`, **off by default**, `complete on|off`, and `bind_completion`'s `on_use` charges `ASSIST_COST` (1 XP) per Tab that actually inserts text, capped at `ASSIST_CAP` (10) a mission — listing candidates is free, and the running tab shows in the prompt's right segment. New assists follow that shape: opt-in, priced per use, visible while it costs you.
- **The map screen is one scrollable page, composed before it is printed.** `quest.draw_screen` builds banner + map as a single document and shows a window onto it: at offset 0 the window is over the banner (so every launch opens on it) and scrolling moves past it — no pages, nothing pinned, nothing chopped. The footer and prompt stay at the bottom; a btop-style `scrollbar()` runs down the right, and the whole layout is rebuilt two columns narrower when the bar shows so the bar has a column of its own. The map still flexes before it scrolls (spaced → `tight` → `squeeze`); the banner never does. Because the screen clears on redraw, anything drawn under it is followed by `pause()` or a `chooser()` — nothing is erased before somebody has said they read it. Under `MIN_COLS`×`MIN_LINES` (52×16) it draws btop's panel instead (`size_alert()`), then Enter retries.
  - **Input**: `engine.prompt_line()` — cbreak via termios/tty (msvcrt on Windows; a plain line read when stdin is a pipe, which is how the other suites still work). It echoes what you type, and keys in its keymap fire the INSTANT they are pressed: ↑ ↓ PgUp PgDn Home End scroll, Esc clears the line, and half-typed text is handed back so scrolling never costs you a number. Only keys that can never be part of an answer are bound — Space was bound once and ate the gap in `/complete on`. `read_key()` uses `os.read`, NOT `sys.stdin.read`: the text layer buffers the rest of an escape sequence where `select()` cannot see it, and PgUp arrives as the literal text `[5~`.
  - **Commands** take an optional leading `/` (`/catchup`, `/task`, `/quit`); `META_COMMANDS` lists the words that count, so `/tmp/build.sh` is still a path and runs in the simulated world.
  - **Size** comes from `_tty_size()` — the ioctl, never `$COLUMNS`: importing `readline` sets COLUMNS/LINES **once**, so after a resize `shutil.get_terminal_size()` reports the window you used to have and every redraw lays out for the wrong width (which is what "the text overlaps" looks like). The env vars remain the fallback with no tty, which is how the piped tests pin a width. `watch_resize()` repaints on SIGWINCH — POSIX only, never fatal, and only while the map is the live screen, since a resize mid-mission must not clear the mission.
- **Emoji width is a setting, not a fact.** `☸️` (a 1-column symbol + VS16) is drawn in ONE cell by VTE/GNOME Terminal and TWO by others, and the wrong guess puts every right-aligned value on that line a column off — a visibly ragged map. `EMOJI_WIDE` (default narrow, `profile["emoji"]`, `theme emoji wide|narrow`, `QUEST_EMOJI=wide`) decides, and `emoji_ruler()` prints the two lines that settle it by eye. Anything that must end flush at `menu_width()` gets no decorative trailing spaces, and `fit_columns` hands out the leftover columns one each rather than floor-dividing them away — otherwise every grid row stops short of the rules above it.
- **The title card is figlet+lolcat in stdlib** (`TITLE`, `BIG_FONT`/`BLOCK_FONT`, `ascii_art`, `drip`, `title_card`): no player is going to `apt install figlet` to see a banner, and neither exists on Windows. It picks big-font-one-line / big-font-two-lines / the small font / a plain box by width, paints the letters in one amber accent and melts them into `drip()` (per-column tails, dithered on a checkerboard — random per cell reads as static, random per column reads as melting), and signs off with a centred `━━━▪ ✦ TITLE ✦ ▪━━━` byline over `signature()` — the credit is a `chip()` (256-colour badge, reverse-video everywhere else), not a line of plain text, honours `NO_COLOR` and `TERM=dumb`, and falls back from truecolor to 256 to the 8 colours. `CREDIT`/`REPO`/`QUOTES` live next to it.
- **Two kinds of prompt, and never the wrong one.** `pause()` is for a screen you only READ ("⏎ back to the map"); `ask(label, hint)` is for a screen that expects an answer — it draws a real `›` prompt with the accepted answers beside it, and **a bare Enter just gives you a fresh prompt, the way it does in any shell**. Leaving a `ask()` level takes the word `quit` (`is_back()` — `quit`/`q`/`exit`/`back`/`done`, slash optional), because a stray Enter must never cost somebody the screen they were reading. Every screen that OFFERS things is built on `chooser(label, hint, render, apply)` — render it, take a pick, act, render it again — so `theme`, `complete`, `setup`, the catch-up route and the Codex (`codex()` → `read_note()`) all behave identically: options stay live until you `quit`, `quit` goes UP one level rather than out to the map, and what you pick takes effect in front of you (`theme kali` redraws the menu with "now: kali"). A route number on the catch-up screen starts that mission through `play_mission()`, shared with the map. `pause()` is left only where a screen has nothing to choose. Notes are named the way a person names them — `study.pick_note()` takes `3`, `class 3`, `class 03`, `docker` or the full title, and `find_note()` knows the `class N` rule too so `/learn class 3` works from anywhere.

## tests/test_keys_and_commands.py — the terminal itself

Everything about the map screen is invisible to the other two suites, because it only exists when a tty does. This one makes a pty (`pty.fork`, so the child owns it as its CONTROLLING terminal — without that the kernel has no foreground process group to send SIGWINCH to and every resize is silently dropped), drives the game through it and reads the screen back: arrows/PgUp/PgDn acting with no Enter, escape sequences NOT arriving as the text `[6~`, the banner scrolling away and Home bringing it back, half-typed input surviving a scroll, every `/command`, the Codex browser, `/tmp` still being a path, reflow on resize, and no line ever exceeding the window. It parks the player's `progress.json` and restores it on exit — a test must never cost somebody their save file. POSIX only; it skips itself on Windows. **Add a check here for anything you change about a menu, a key or a prompt.**

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
