#!/usr/bin/env python3
"""Shell Quest — learn DevOps by typing the real commands.

    python quest.py                      play (mission map)
    python quest.py --catchup            missed classes? the ordered route back to current
    python quest.py --os <name>          aim real-world tips at linux / mac / windows
    python quest.py --setup              how to install the real tools on your machine
    python quest.py --selftest           lint + prove every mission is completable (CI)
    python quest.py --link-vault <file>  write live progress into an Obsidian note
    python quest.py --vault <folder>     point `learn` at your study vault (📖 the Codex)
    python quest.py --sync-vault         re-render the vault progress note now
"""
import os
import sys

from engine import (IO, OS_NAMES, c, detect_os, level, load_config, load_profile,
                    os_label, print_setup, run_mission, save_config, save_profile,
                    set_player_os, sync_vault_note)
from missions import ALL_MISSIONS, TOPICS


def banner(profile):
    lvl, name = level(profile["xp"])
    print(c("\n╔══════════════════════════════════════════════════╗", "blue"))
    print(c("║        🗡️  S H E L L   Q U E S T  🗡️              ║", "blue"))
    print(c("║   learn DevOps by typing the real commands        ║", "blue"))
    print(c("╚══════════════════════════════════════════════════╝", "blue"))
    if profile["name"]:
        print(c(f"  {profile['name']} · Level {lvl} {name} · {profile['xp']} XP "
                f"· {len(profile['completed'])}/{len(ALL_MISSIONS)} missions", "bold"))
        print(c(f"  🖥️  real-machine tips: {os_label()}   ({'os <linux|mac|windows>' } in a mission "
                f"· `setup` = install the real tools)", "dim"))


def ask_os(profile):
    """Ask once which machine the player is actually on. The simulated host is
    always Linux; this only aims the 🌍 real-world advice at the right OS."""
    guess = detect_os()
    print(c("\n🖥️  Which machine are you on for real?", "cyan"))
    print(c("   The world in here is always Linux — this only decides the install/sudo/"
            "which-vs-where advice you get.", "dim"))
    for i, (key, label) in enumerate(OS_NAMES.items(), 1):
        star = c("  ← detected", "dim") if key == guess else ""
        print(f"   {i}. {label}{star}")
    try:
        raw = input(c(f"> [{guess}] ", "cyan")).strip().lower()
    except (EOFError, KeyboardInterrupt):
        raw = ""
    by_num = dict(enumerate(OS_NAMES, 1))
    choice = by_num.get(int(raw)) if raw.isdigit() else (raw if raw in OS_NAMES else None)
    profile["os"] = choice or guess
    set_player_os(profile["os"])
    print(c(f"   → tips tuned for {os_label()}. Change any time with `os <name>`.", "green"))


def mission_map(profile):
    print(c("\n🗺️  MISSION MAP", "bold"))
    n = 0
    index = {}
    for topic, label in TOPICS.items():
        print(f"\n  {label}")
        for m in [m for m in ALL_MISSIONS if m["topic"] == topic]:
            n += 1
            index[str(n)] = m
            done = m["id"] in profile["completed"]
            mark = c("✅", "green") if done else c("🔓", "yellow")
            best = f" · best {profile['completed'][m['id']]['xp']} XP" if done else ""
            print(f"   {mark} {n}. {m['title']}{c(best, 'dim')}")
    print(c("\n  pick a mission number · 'catchup' for a route · 'learn' for the 📖 Codex "
            "· 'q' to quit", "dim"))
    return index


# Missed a few classes? This is the order that actually builds on itself, and
# which of the course's REAL graded assignments each stretch prepares you for.
CATCHUP_ROUTE = [
    ("linux", "Linux Fundamentals",
     "the 10-part Linux assignment (+ the 3 extra exercises)"),
    ("docker", "Class 01/02 - Docker",
     "Docker Basics – Assignment 1 (Dockerfile → build → tag → push)"),
    ("git", "Class 03 - Git",
     "Git Fundamentals: branching, merging & conflicts (+ the bonus undo section)"),
    ("k8s", "Class 05 - Kubernetes",
     "K8s CLI assignment · Core Resources & RBAC · Day-2 Ops & Resilience"),
    ("helm", "Class 06 - Helm",
     "Helm From Scratch (Assignment A) · the advanced 'orbit' chart"),
    ("gitops", "Class 08 - GitOps and CI-CD", None),
    # Two notes, one topic: class 11 is the theory, class 14 is the dockerized
    # lab that makes it hands-on — read them in that order, play them in that order.
    ("ansible", "Class 11 - Ansible → then Class 14 - Ansible Lab", None),
    ("terraform", "Class 12 - Terraform", None),
    ("rabbitmq", "Class 13 - RabbitMQ Messaging", None),
    ("capstone", "SkyWatch Capstone",
     "the graduation project — build SkyWatch for real, then terraform destroy it"),
]


def catchup(profile):
    """Show the shortest honest path back to current, from wherever you are."""
    done = profile.get("completed", {})
    print(c("\n🔁 CATCH-UP ROUTE", "bold"))
    print(c("   Missed some classes? Take these in order — each one assumes the last.", "dim"))
    print(c("   New topic? Run the mission once with `demo` to watch it, then beat it yourself.\n", "dim"))
    numbers, n = {}, 0
    seq = []
    for topic, _note, _assignment in CATCHUP_ROUTE:
        for m in ALL_MISSIONS:
            if m["topic"] == topic:
                n += 1
                numbers[m["id"]] = n
                seq.append(m)
    next_up = None
    for topic, note, assignment in CATCHUP_ROUTE:
        ms = [m for m in ALL_MISSIONS if m["topic"] == topic]
        if not ms:
            continue
        got = sum(1 for m in ms if m["id"] in done)
        if got == len(ms):
            state = c("✅ done", "green")
        elif got:
            state = c(f"◐ {got}/{len(ms)}", "yellow")
        else:
            state = c("○ not started", "dim")
        print(f"  {TOPICS[topic]}  {state}")
        print(c(f"      read: {note}", "dim"))
        nums = ", ".join(str(numbers[m["id"]]) for m in ms)
        print(c(f"      play: mission {nums}", "dim"))
        if assignment:
            print(c(f"      prove: {assignment}", "dim"))
        if next_up is None and got < len(ms):
            next_up = next(m for m in ms if m["id"] not in done)
    if next_up:
        print(c(f"\n  ⏭️  start here: {numbers[next_up['id']]}. {next_up['title']}", "magenta"))
        print(c(f"      pairs with the note: {next_up.get('vault_note', '—')}", "dim"))
    else:
        print(c("\n  🌟 the whole route is clear — you're current.", "magenta"))
    print("")


def play():
    profile = load_profile()
    set_player_os(profile.get("os") or detect_os())
    banner(profile)
    if not profile["name"]:
        try:
            profile["name"] = input(c("\nWhat's your handle, engineer? ", "cyan")).strip() or "anonymous"
        except (EOFError, KeyboardInterrupt):
            return
        ask_os(profile)
        save_profile(profile)
        print(c(f"Welcome, {profile['name']}. Your progress saves to progress.json (gitignored — it's yours).", "dim"))
    elif not profile.get("os"):
        ask_os(profile)          # existing player from before the OS picker existed
        save_profile(profile)

    while True:
        index = mission_map(profile)
        try:
            choice = input(c("\n> ", "cyan")).strip().lower()
        except (EOFError, KeyboardInterrupt):
            choice = "q"
        if choice in ("q", "quit", "exit"):
            lvl, name = level(profile["xp"])
            print(c(f"\nSee you, {profile['name']} — Level {lvl} {name}, {profile['xp']} XP. 👋\n", "bold"))
            return
        if choice in ("catchup", "catch-up", "route", "c"):
            catchup(profile)
            continue
        if choice == "setup":
            print_setup(IO())
            continue
        if choice == "learn" or choice.startswith("learn ") or choice in ("codex", "study"):
            import study
            arg = choice[6:] if choice.startswith("learn ") else ""
            io = IO()
            try:
                # No mission in hand on the map screen, so `learn` means the
                # library: browse it, search it, or open any note by name.
                verb, _, rest = arg.partition(" ")
                if verb in ("find", "search", "grep") and rest:
                    study.search(io, rest)
                elif arg:
                    study.learn(io, profile, arg, "")
                else:
                    study.list_notes(io, profile)
                save_profile(profile)
            except Exception as exc:  # noqa: BLE001 — the vault is not ours to trust
                print(c(f"the vault couldn't be read: {exc}", "yellow"))
            continue
        m = index.get(choice)
        if not m:
            print(c("pick a number from the map (or q)", "yellow"))
            continue
        os_before = profile.get("os")
        completed, xp, hints = run_mission(m, profile)
        if profile.get("os") != os_before:
            save_profile(profile)          # `os <name>` typed mid-mission — keep it
        if completed:
            prev = profile["completed"].get(m["id"], {}).get("xp", 0)
            if xp > prev:
                profile["xp"] += xp - prev
                profile["completed"][m["id"]] = {"xp": xp, "hints": hints}
                save_profile(profile)
            lvl, name = level(profile["xp"])
            print(c(f"  total: {profile['xp']} XP · Level {lvl} {name}", "magenta"))
            nxt = next((x for x in ALL_MISSIONS if x["id"] not in profile["completed"]), None)
            if nxt:
                print(c(f"  ⏭️  next up: {nxt['title']}  ({TOPICS[nxt['topic']]})", "dim"))
            else:
                print(c("  🌟 every mission complete — you've cleared the course map!", "magenta"))


def lint():
    """Structural checks so contributor mistakes fail fast (runs inside selftest)."""
    import re as _re
    problems, ids = [], set()
    for m in ALL_MISSIONS:
        mid = m.get("id", "<missing id>")
        if mid in ids:
            problems.append(f"{mid}: duplicate id")
        ids.add(mid)
        if m.get("topic") not in TOPICS:
            problems.append(f"{mid}: topic '{m.get('topic')}' not in TOPICS")
        for key in ("title", "brief", "vault_note", "objectives", "solution"):
            if not m.get(key):
                problems.append(f"{mid}: missing/empty '{key}'")
        for i, o in enumerate(m.get("objectives", [])):
            for key in ("desc", "xp", "hint", "check"):
                if key not in o:
                    problems.append(f"{mid}: objective {i + 1} missing '{key}'")
            if not (5 <= o.get("xp", 0) <= 40):
                problems.append(f"{mid}: objective {i + 1} xp {o.get('xp')} outside 5–40")
        teach = m.get("teach", [])
        if len(teach) != len(m.get("objectives", [])):
            problems.append(f"{mid}: teach lines ({len(teach)}) != objectives ({len(m.get('objectives', []))})")
        for pattern, _fn in m.get("handlers", []):
            try:
                _re.compile(pattern)
            except _re.error as e:
                problems.append(f"{mid}: bad handler regex {pattern!r}: {e}")
    problems += lint_quiz()
    if problems:
        print(c(f"LINT: {len(problems)} problem(s)", "red"))
        for p in problems:
            print(c(f"  ✗ {p}", "red"))
        sys.exit(1)
    print(c(f"LINT: {len(ALL_MISSIONS)} missions structurally OK ✔\n", "green"))


def lint_quiz():
    """The quiz lives in the same repo and ships in the same commits. The one
    thing that can silently rot is grading: every answer the quiz PRINTS as
    accepted must actually be accepted by the grader."""
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "quiz", "quiz.py")
    if not os.path.exists(path):
        return []
    spec = importlib.util.spec_from_file_location("_quiz_lint", path)
    quiz = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(quiz)
    problems, topics = [], set()
    for q in quiz.QUESTIONS:
        topics.add(q.get("topic"))
        if q.get("topic") not in quiz.TOPIC_NAMES:
            problems.append(f"quiz: topic '{q.get('topic')}' missing from TOPIC_NAMES")
        if "options" in q:
            if not isinstance(q.get("answer"), int) or not 0 <= q["answer"] < len(q["options"]):
                problems.append(f"quiz: answer index out of range — {q['q'][:48]}")
            continue
        if not q.get("accept"):
            problems.append(f"quiz: no options and no accept — {q['q'][:48]}")
            continue
        cmd = quiz.is_command_answer(q)
        for a in q["accept"]:
            if not quiz.grade_text(a, q["accept"], cmd):
                problems.append(f"quiz: grader rejects its own answer {a!r} — {q['q'][:48]}")
    return problems


def selftest():
    print(c("Shell Quest selftest — lint, then run every mission's solution script…\n", "bold"))
    lint()
    failures = 0
    for m in ALL_MISSIONS:
        io = IO(script=list(m["solution"]) + ["quit"], echo_script=False)
        # silence mission output: swallow prints
        class Quiet(IO):
            def __init__(self, script):
                super().__init__(script=script)
            def print(self, *args):
                pass
        io = Quiet(list(m["solution"]) + ["quit"])
        try:
            completed, xp, hints = run_mission(m, {"completed": {}}, io=io)
        except Exception as e:  # noqa: BLE001 — report, don't crash the suite
            completed, xp, e_msg = False, 0, str(e)
            print(f"  {c('ERROR', 'red')}  {m['id']}: {e_msg}")
            failures += 1
            continue
        if completed:
            print(f"  {c('PASS', 'green')}   {m['id']:<10} {m['title']}  ({xp} XP)")
        else:
            print(f"  {c('FAIL', 'red')}   {m['id']:<10} {m['title']} — solution script did not complete it")
            failures += 1
    print()
    if failures:
        print(c(f"{failures} mission(s) broken.", "red"))
        sys.exit(1)
    print(c(f"All {len(ALL_MISSIONS)} missions completable. ✔", "green"))


def link_vault(path):
    config = load_config()
    config["vault_progress_file"] = path
    save_config(config)
    written = sync_vault_note(load_profile())
    if written:
        print(c(f"Linked! Progress note written to:\n  {written}", "green"))
        print(c("It refreshes automatically every time you finish a mission.", "dim"))
    else:
        print(c(f"Config saved, but writing to {path} failed — check the folder exists.", "yellow"))


def link_study_vault(path):
    """Point `learn` at the folder holding the course notes."""
    import study
    if not os.path.isdir(os.path.expanduser(path)):
        print(c(f"no such folder: {path}", "yellow"))
        print(c("Don't have the notes yet? Clone them, then point at the folder:", "dim"))
        print(c("  git clone https://github.com/iceteps/devops-study-vault", "cyan"))
        sys.exit(1)
    folder = study.set_vault_dir(path)
    notes = study.notes_index()
    print(c(f"📖 Codex linked: {folder}  ({len(notes)} notes)", "green"))
    missing = [m["vault_note"] for m in ALL_MISSIONS if not study.find_note(m["vault_note"])]
    if missing:
        print(c(f"   {len(set(missing))} mission note(s) not found there: "
                + " · ".join(sorted(set(missing))), "yellow"))
    print(c("   In a mission: `learn` reads it · `learn cards` drills the flashcards · "
            "`learn quiz` tests you.", "dim"))


def set_os_cli(name):
    if name not in OS_NAMES:
        print(c(f"unknown OS '{name}' — pick one of: {' · '.join(OS_NAMES)}", "yellow"))
        sys.exit(1)
    profile = load_profile()
    profile["os"] = name
    set_player_os(name)
    save_profile(profile)
    print(c(f"Real-machine tips now target {os_label()}.", "green"))
    print(c("Run `python quest.py --setup` to see the install steps for it.", "dim"))


FLAGS = ("--selftest", "--os", "--setup", "--catchup", "--link-vault", "--vault",
         "--sync-vault", "--help", "-h")


def usage(stream_note=""):
    print(__doc__.rstrip())
    if stream_note:
        print(c("\n" + stream_note, "yellow"))


if __name__ == "__main__":
    # A mistyped flag must not silently drop you into the game — you'd never
    # notice that --setup didn't run. Anything unrecognised gets the usage text.
    unknown = [a for a in sys.argv[1:] if a.startswith("-") and a not in FLAGS]
    if "--help" in sys.argv or "-h" in sys.argv:
        usage()
    elif unknown:
        usage(f"unknown option: {unknown[0]}")
        sys.exit(2)
    elif "--selftest" in sys.argv:
        selftest()
    elif "--os" in sys.argv:
        i = sys.argv.index("--os")
        if i + 1 >= len(sys.argv):
            print(c(f"usage: python quest.py --os <{'|'.join(OS_NAMES)}>", "yellow"))
            sys.exit(1)
        set_os_cli(sys.argv[i + 1].strip().lower())
    elif "--setup" in sys.argv:
        set_player_os(load_profile().get("os") or detect_os())
        print_setup(IO())
    elif "--catchup" in sys.argv:
        catchup(load_profile())
    elif "--link-vault" in sys.argv:
        i = sys.argv.index("--link-vault")
        if i + 1 >= len(sys.argv):
            print(c('usage: python quest.py --link-vault "<path>/Shell Quest Progress.md"', "yellow"))
            sys.exit(1)
        link_vault(sys.argv[i + 1])
    elif "--vault" in sys.argv:
        i = sys.argv.index("--vault")
        if i + 1 >= len(sys.argv):
            print(c('usage: python quest.py --vault "<folder with your .md notes>"', "yellow"))
            sys.exit(1)
        link_study_vault(sys.argv[i + 1])
    elif "--sync-vault" in sys.argv:
        written = sync_vault_note(load_profile())
        print(c(f"Progress note refreshed: {written}", "green") if written
              else c("No vault linked yet — run: python quest.py --link-vault <file>", "yellow"))
    else:
        play()
