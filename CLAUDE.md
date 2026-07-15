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
- **Demo mode** (`demo` in a fresh mission) replays the `solution` step-by-step: Enter advances, `takeover` hands control back mid-run. Objectives completed during demo pay 0 XP and an all-demo run is never recorded — watching teaches, doing scores. Because `solution` doubles as the demo script, keep solutions clean and pedagogically ordered (inspect → act → verify), not just minimal.
- Error messages should mimic the real tools' output (e.g. `denied: requested access to the resource is denied`) — the authenticity is the pedagogy. A dim parenthetical teaching hint after a realistic error is the house style.

## Adding a mission

1. Add the dict to the right topic module in `missions/` (or a new module — register it in `missions/__init__.py`: extend `ALL_MISSIONS` and `TOPICS`).
2. If the topic needs commands the engine lacks, prefer a mission-local `handler` first; promote to a generic `engine.py` command only when 2+ missions need it.
3. Mirror the course's REAL graded assignments when one exists (missions 4 and 5–7 do this) — the game is a risk-free rehearsal for them.
4. Every objective needs a hint; every mission needs a `solution`; run `--selftest`.

## Adding quiz questions

`quiz/quiz.py` → `QUESTIONS` list at the top. Two formats: multiple-choice (`options` + `answer` index) or type-the-command (`accept` = list of lenient lowercase substrings). Options are shuffled at runtime — never encode "the answer is always b". Add the topic to `TOPIC_NAMES` if it's new.

## When the course advances

When a new class/topic appears in the course (upstream: `yfreifeld/devops-course`), extend BOTH games in one commit: new mission(s) if the topic is hands-on simulatable, plus quiz questions for the topic. Companion study notes live at https://github.com/iceteps/devops-study-vault — each mission's `vault_note` field names its note; keep them consistent.

## Repo hygiene

Owner: `iceteps`. The teacher (`yfreifeld`) may become a collaborator — keep history clean and messages descriptive. This repo is public and meant to be forked by students; never commit anything personal (progress, tokens, names beyond what's already public).
