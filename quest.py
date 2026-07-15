#!/usr/bin/env python3
"""Shell Quest — learn DevOps by typing the real commands.

    python quest.py                      play (mission map)
    python quest.py --selftest           lint + prove every mission is completable (CI)
    python quest.py --link-vault <file>  write live progress into an Obsidian note
    python quest.py --sync-vault         re-render the vault progress note now
"""
import sys

from engine import (IO, c, level, load_config, load_profile, run_mission,
                    save_config, save_profile, sync_vault_note)
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
    print(c("\n  pick a mission number · 'q' to quit", "dim"))
    return index


def play():
    profile = load_profile()
    banner(profile)
    if not profile["name"]:
        try:
            profile["name"] = input(c("\nWhat's your handle, engineer? ", "cyan")).strip() or "anonymous"
        except (EOFError, KeyboardInterrupt):
            return
        save_profile(profile)
        print(c(f"Welcome, {profile['name']}. Your progress saves to progress.json (gitignored — it's yours).", "dim"))

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
        m = index.get(choice)
        if not m:
            print(c("pick a number from the map (or q)", "yellow"))
            continue
        completed, xp, hints = run_mission(m, profile)
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


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    elif "--link-vault" in sys.argv:
        i = sys.argv.index("--link-vault")
        if i + 1 >= len(sys.argv):
            print(c('usage: python quest.py --link-vault "<path>\\Shell Quest Progress.md"', "yellow"))
            sys.exit(1)
        link_vault(sys.argv[i + 1])
    elif "--sync-vault" in sys.argv:
        written = sync_vault_note(load_profile())
        print(c(f"Progress note refreshed: {written}", "green") if written
              else c("No vault linked yet — run: python quest.py --link-vault <file>", "yellow"))
    else:
        play()
