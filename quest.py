#!/usr/bin/env python3
"""Shell Quest — learn DevOps by typing the real commands.

    python quest.py                      play (mission map)
    python quest.py --catchup            missed classes? the ordered route back to current
    python quest.py --os <name>          aim real-world tips at linux / mac / windows
    python quest.py --setup              how to install the real tools on your machine
    python quest.py --selftest           lint + prove every mission is completable (CI)
    python quest.py --link-vault <file>  write live progress into an Obsidian note
    python quest.py --sync-vault         re-render the vault progress note now
"""
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
    print(c("\n  pick a mission number · 'catchup' for a route · 'q' to quit", "dim"))
    return index


# Missed a few classes? This is the order that actually builds on itself, and
# which of the course's REAL graded assignments each stretch prepares you for.
CATCHUP_ROUTE = [
    ("linux", "Linux Fundamentals", "the 10-part Linux assignment (+3 extras)"),
    ("docker", "Class 01/02 - Docker", "Docker Basics – Assignment 1"),
    ("git", "Class 03 - Git", "Git Fundamentals: branching, merging & conflicts"),
    ("k8s", "Class 05 - Kubernetes", "K8s CLI assignment · Core Resources & RBAC · Day-2 Ops"),
    ("helm", "Class 06 - Helm", "Helm From Scratch · the advanced 'orbit' chart"),
    ("gitops", "Class 08 - GitOps and CI-CD", None),
    ("ansible", "Class 11 - Ansible", None),
    ("terraform", "Class 12 - Terraform", None),
    ("rabbitmq", "Class 13 - RabbitMQ Messaging", None),
    ("capstone", "SkyWatch Capstone", None),
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
    if problems:
        print(c(f"LINT: {len(problems)} problem(s)", "red"))
        for p in problems:
            print(c(f"  ✗ {p}", "red"))
        sys.exit(1)
    print(c(f"LINT: {len(ALL_MISSIONS)} missions structurally OK ✔\n", "green"))


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


if __name__ == "__main__":
    if "--selftest" in sys.argv:
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
    elif "--sync-vault" in sys.argv:
        written = sync_vault_note(load_profile())
        print(c(f"Progress note refreshed: {written}", "green") if written
              else c("No vault linked yet — run: python quest.py --link-vault <file>", "yellow"))
    else:
        play()
