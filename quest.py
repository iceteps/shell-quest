#!/usr/bin/env python3
"""Shell Quest — learn DevOps by typing the real commands.

    python quest.py             play (mission map)
    python quest.py --selftest  prove every mission is completable (for forks/CI)
"""
import sys

from engine import IO, c, level, load_profile, run_mission, save_profile
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


def selftest():
    print(c("Shell Quest selftest — running every mission's solution script…\n", "bold"))
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


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        play()
