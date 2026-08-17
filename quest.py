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

The menus paint across the terminal you're in, however wide that is.
QUEST_WIDTH=<columns> caps them · NO_COLOR=1 drops the colour · QUEST_EMOJI=wide
if your terminal draws ☸️-style emoji two columns wide (`theme emoji` in the
game shows you which it is).
"""
import os
import random
import sys

from engine import (ASSIST_CAP, ASSIST_COST, IO, OS_NAMES, QUOTES, assists_on, c,
                    clear_screen, detect_os, disp_width, fit_columns, grid, heading,
                    leader, level, load_config, load_profile, menu_width, meter,
                    os_label, pad, print_setup, prompt_line, rule, run_mission,
                    save_config, save_profile, screen_too_small, set_emoji_width,
                    set_player_os, set_theme, signature, size_alert, spread,
                    sync_vault_note, term_lines, theme_cmd, title_card, wrap_text)
from missions import ALL_MISSIONS, TOPICS


def stat_lines(profile, w):
    """Who you are and how far you've got, with the bar filling the leftover."""
    lvl, name = level(profile["xp"])
    done, total = len(profile["completed"]), len(ALL_MISSIONS)
    stat = (f"{profile['name']} · Level {lvl} {name} · {profile['xp']} XP "
            f"· {done}/{total} missions")
    # The bar gets whatever the stat line leaves over. A window too narrow to
    # hold both loses the bar, never the numbers.
    room = w - disp_width(stat) - 10
    if room >= 14:
        pct = c(f" {round(100 * done / max(1, total)):>3}%", "dim")
        return [spread(c("  " + stat, "bold"), meter(done, total, min(30, room)) + pct, w)]
    return [c(line, "bold") for line in wrap_text(stat, w)]


def banner_lines(profile, w, budget=99, quote=None):
    """The title screen as LINES, inside `budget` rows.

    The letters come first and the drip last, so a smaller budget buys itself
    back from decoration before it ever touches the art. Returned rather than
    printed because the caller has to know how tall it came out before it
    commits to a layout.
    """
    quote = quote or random.choice(QUOTES)
    named = bool(profile.get("name"))
    stats = stat_lines(profile, w) if named else []
    tagline = ["", c("  🗡️  learn DevOps by typing the real commands", "cyan")]
    quoted = [c(line, "dim") for line in wrap_text(f"💭 {quote}", w)]
    tips = ([c(line, "dim") for line in
             wrap_text(f"🖥️  real-machine tips: {os_label()}   "
                       "(`os <linux|mac|windows>` in a mission · "
                       "`setup` = install the real tools)", w)] if named else [])

    def compose(card, extras):
        out = [""] + card + [signature(w)]
        out += tagline if "tag" in extras else []
        out += quoted if "quote" in extras else []
        out += [rule(w)] + stats
        out += tips if "tips" in extras else []
        return out

    # Spend the budget in the order things matter: the letters, then the words,
    # and only then the drip. The drip is the one part of this screen that is
    # purely decorative, so it is the part that pays for everything else.
    fixed = 3 + len(stats)                       # blank + signature + rule + stats
    for art in (7, 6, 3, 1):                     # big letters · small · framed · one line
        if fixed + len(title_card(w, art)) <= budget:
            break
    extras = []
    for name, block in (("tag", tagline), ("quote", quoted), ("tips", tips)):
        if block and len(compose(title_card(w, art), extras + [name])) <= budget:
            extras.append(name)
    card = title_card(w, art)
    if art >= 6:                                 # only the block fonts can drip
        for spare in range(5, 0, -1):
            taller = title_card(w, art + spare)
            if len(compose(taller, extras)) <= budget:
                card = taller
                break
    return compose(card, extras)


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


def map_parts(profile, w, tight=False, squeeze=False):
    """The map as (title lines, one block per topic, footer lines, number index).

    Thirty missions in one narrow column wastes most of a modern window and
    still doesn't fit on a screen. So: each mission is a row that stretches to
    the edge (title left, XP right, dots between — a table of contents), and
    when the window is wide enough for two or three of those side by side, they
    tile instead of stacking. Narrow it back down and it degrades to the plain
    one-per-line list it always was. `tight` drops the blank line between
    topics; `squeeze` lets a cell be narrower than the longest title so a second
    column fits, and the handful of longest titles end in `…`.

    Blocks come back separately so the caller can decide how they stack.
    """
    done, total = len(profile["completed"]), len(ALL_MISSIONS)
    title = ["", heading(c("🗺️  MISSION MAP", "bold"),
                         c(f"{done}/{total} complete", "dim"), w)]
    n, index, topics = 0, {}, []
    for topic, label in TOPICS.items():
        rows, got = [], 0
        for m in [m for m in ALL_MISSIONS if m["topic"] == topic]:
            n += 1
            index[str(n)] = m
            rec = profile["completed"].get(m["id"])
            got += 1 if rec else 0
            mark = c("✅", "green") if rec else c("🔓", "yellow")
            rows.append((f"{mark} {n}. {m['title']}",
                         c(f"best {rec['xp']} XP", "green") if rec
                         else c(f"+{sum(o['xp'] for o in m['objectives'])} XP", "dim")))
        topics.append((label, got, rows))
    # One cell width for the whole map, so the columns line up across topics.
    natural = max(disp_width(left) + disp_width(right) + 4
                  for _label, _got, rows in topics for left, right in rows)
    if squeeze:
        natural = min(natural, max(46, (w - 3 - 3) // 2))
    ncols, widths = fit_columns(natural, w, gutter=3, indent=3, max_cols=3)
    blocks = []
    for label, got, rows in topics:
        block = [] if tight else [""]
        block.append(heading(c(label, "bold"), c(f"{got}/{len(rows)}", "dim"), w))
        # i % ncols is the column this row lands in — the columns are not all
        # the same width when the leftover space had to be shared out.
        cells = [pad(leader(left, right, widths[i % ncols]), widths[i % ncols])
                 for i, (left, right) in enumerate(rows)]
        blocks.append(block + grid(cells, ncols, gutter=3, indent=3))
    foot = ["", rule(w)]
    keys = "  number = play it · /catchup = the route back · /learn = 📖 the Codex"
    more = ("  /theme = the prompt's look · /complete = Tab help (costs XP) · "
            "/setup = install the tools")
    if disp_width(keys) + 12 <= w:
        foot.append(spread(c(keys, "dim"), c("/quit", "dim"), w))
        foot += [c(line, "dim") for line in wrap_text(more.strip(), w)]
    else:
        foot += [c(line, "dim") for line in
                 wrap_text(keys.strip() + " · " + more.strip() + " · /quit", w)]
    return title, blocks, foot, index


def scrollbar(offset, shown, total, rows):
    """A one-column track down the right of the list, the way btop draws one."""
    if total <= shown or rows < 3:
        return []
    span = max(1, round(rows * shown / total))
    at = round((rows - span) * offset / max(1, total - shown))
    return ["█" if at <= i < at + span else "░" for i in range(rows)]


# The banner is the game's face — it does not get sacrificed to fit a window.
# What gives instead is the MAP, which scrolls under it. This is the floor of
# rows the map is guaranteed even so; below it the banner finally starts to
# shrink, because a screen showing nothing but a logo is not a menu.
MIN_MAP_ROWS = 14
# Big letters + byline + signature + rule + stat line: the banner never goes
# under this on a window that could possibly hold it.
BANNER_MIN = 11


def draw_screen(profile, quote=None, offset=0):
    """One scrollable document — banner on top of it, every mission under it.

    Not pages, and nothing pinned. Banner and map are a single virtual page and
    the terminal shows a window onto it: at offset 0 the window is over the
    banner, which is what you get every time the game starts. Scroll and the
    banner slides away like anything else on a page, uncovering the rest of the
    map. The command footer and the prompt stay put at the bottom, because
    that's where you're typing.

    Returns (index of playable numbers, the largest legal offset).
    """
    if screen_too_small():
        size_alert()
        return None, 0
    w, rows = menu_width(), term_lines()

    def build(width):
        """The whole page at `width`, in the roomiest layout that fits.

        Vertical space stopped being scarce the moment the page could scroll, so
        the tighter layouts are only worth reaching for when they remove the
        scrolling altogether. Otherwise: the roomy one.
        """
        head = banner_lines(profile, width, 99, quote)   # always its full self
        best = None
        for tight, squeeze in ((False, False), (True, False), (True, True)):
            title, blocks, foot, index = map_parts(profile, width, tight, squeeze)
            doc = head + title + [line for block in blocks for line in block]
            room = max(3, rows - len(foot) - 3)          # footer + prompt + slack
            if best is None or len(doc) <= room:
                best = (doc, foot, index, room)
            if len(doc) <= room:
                break
        return best

    doc, foot, index, room = build(w)
    if len(doc) > room:
        # It will scroll, so the scrollbar needs a column of its own — and a
        # column it takes has to come out of the layout, not off the end of the
        # line. Rebuild two narrower; nothing here has been printed yet.
        doc, foot, index, room = build(w - 2)
    last_offset = max(0, len(doc) - room)
    top = max(0, min(offset, last_offset))
    shown = doc[top:top + room]
    more = len(doc) > room
    track = scrollbar(top, room, len(doc), len(shown)) if more else []
    if track:                               # a bar down the edge, btop-style
        shown = [pad(line, w - 1) + c(bar, "cyan" if bar == "█" else "dim")
                 for line, bar in zip(shown, track)]
    clear_screen()
    for line in shown + foot:
        print(line)
    if more:
        pct = round(100 * top / last_offset) if last_offset else 100
        print(spread(c(f"  ▾ {pct:>3}%  ", "cyan")
                     + c("(Home = back to the top)" if top else "(scroll for the rest)", "dim"),
                     c("↑ ↓ PgUp PgDn scroll · Home · End — no Enter needed", "dim"), w))
    return index, last_offset


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
    """Show the shortest honest path back to current, from wherever you are.

    Returns {"<number>": mission} for the numbers it printed, so the screen
    around it can let a player start one without going back to the map first.
    """
    done = profile.get("completed", {})
    w = menu_width()
    print("")
    print(heading(c("🔁 CATCH-UP ROUTE", "bold"),
                  c(f"{len(done)}/{len(ALL_MISSIONS)} missions done", "dim"), w))
    for line in wrap_text("Missed some classes? Take these in order — each one assumes the "
                          "last. New topic? Run the mission once with `demo` to watch it, "
                          "then beat it yourself.", w, indent="   "):
        print(c(line, "dim"))
    print("")
    numbers, n, by_number = {}, 0, {}
    for topic, _note, _assignment in CATCHUP_ROUTE:
        for m in ALL_MISSIONS:
            if m["topic"] == topic:
                n += 1
                numbers[m["id"]] = n
                by_number[str(n)] = m
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
        print(heading(c(f"  {TOPICS[topic]}", "bold"), state, w))
        nums = ", ".join(str(numbers[m["id"]]) for m in ms)
        for kind, text in (("read", note), ("play", f"mission {nums}"),
                           ("prove", assignment)):
            if not text:
                continue
            for i, line in enumerate(wrap_text(text, w - 13, indent="")):
                print(c(f"      {kind + ':' if not i else '':<7}{line}", "dim"))
        if next_up is None and got < len(ms):
            next_up = next(m for m in ms if m["id"] not in done)
    print("")
    if next_up:
        print(leader(c(f"  ⏭️  start here: {numbers[next_up['id']]}. {next_up['title']}", "magenta"),
                     c(f"note: {next_up.get('vault_note', '—')}", "dim"), w))
    else:
        print(c("  🌟 the whole route is clear — you're current.", "magenta"))
    print("")
    return by_number


def pause(note="⏎ back to the map"):
    """Hold a sub-screen until the player is done with it.

    The map screen clears before it draws, so anything printed under it —
    a catch-up route, the Codex, a mission's end-of-run recap — would be wiped
    the instant the loop came round again. Nothing gets erased before somebody
    has said they've read it.
    """
    if not sys.stdout.isatty():
        return
    try:
        input(c(f"\n{note} ", "dim"))
    except (EOFError, KeyboardInterrupt):
        pass


BACK_WORDS = ("quit", "q", "exit", "back", "done")


def is_back(text):
    """`quit` — with or without a slash — and the words people mean by it."""
    return text.strip().lstrip("/").strip().lower() in BACK_WORDS


def ask(label, hint=""):
    """A prompt that looks like one, on a screen that expects an answer.

    `pause()` is for a screen you only read; this is for one you answer. And a
    bare Enter here does what a bare Enter does in any shell — hands you a fresh
    prompt. Leaving takes the word `quit`, because a stray Enter should never
    cost you the screen you were reading.
    """
    print(c(f"\n  {label}", "cyan") + (c(f"   {hint}", "dim") if hint else ""))
    while True:
        try:
            answer, _action = prompt_line(c("  › ", "cyan"))
        except (EOFError, KeyboardInterrupt):
            return "quit"
        if answer.strip():
            return answer.strip()


def play_mission(profile, m):
    """Run one mission and bank whatever it earned. Shared by the map and the
    catch-up route, because "start here: 3" should be startable from there."""
    os_before = profile.get("os")
    completed, xp, hints = run_mission(m, profile)
    if profile.get("os") != os_before:
        save_profile(profile)              # `os <name>` typed mid-mission — keep it
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
    pause()              # the recap is the point of finishing — read it first


def chooser(label, hint, render, apply):
    """A screen that OFFERS things: show it, take a pick, act, show it again.

    Every list of options in this game used to end on `pause()` — the screen
    told you what you could choose and then had no way to hear it. One loop, so
    every such screen behaves the same: the options are live until you `quit`,
    a bare Enter re-prompts, and what you pick takes effect in front of you.
    """
    while True:
        render()
        answer = ask(label, hint)
        if is_back(answer):
            return
        apply(answer)


def theme_screen(profile):
    """🎨 The look, picked from the menu that shows it."""
    io = IO()
    chooser("🎨 pick a look",
            "classic · kali · lean · rainbow · nerd/unicode/ascii · "
            "emoji wide|narrow · `quit` = the map",
            lambda: theme_cmd(io, profile, ""),
            lambda answer: theme_cmd(io, profile, answer))


def complete_screen(profile):
    """⌨ The Tab-completion toggle, with its price in plain sight."""
    def render():
        state = c("ON", "green") if assists_on(profile) else c("OFF", "yellow")
        print("")
        print(c("  ⌨  Tab-completion is ", "cyan") + state)
        for line in wrap_text(f"It costs {ASSIST_COST} XP each time it finishes a word for you, "
                              f"never more than {ASSIST_CAP} in one mission, and listing the "
                              "candidates with a double-Tab is always free.", indent="     "):
            print(c(line, "dim"))

    def apply(answer):
        want = answer.strip().lower()
        if want in ("on", "off"):
            profile.setdefault("assists", {})["complete"] = (want == "on")
            save_profile(profile)
        else:
            print(c("     type `on` or `off`", "yellow"))

    chooser("⌨ Tab-completion", "on · off · `quit` = the map", render, apply)


def setup_screen(profile):
    """🧰 The real-machine install steps, for whichever OS you're actually on."""
    io = IO()

    def apply(answer):
        want = answer.strip().lower().split()[-1]
        if want in OS_NAMES:
            set_player_os(want)
            profile["os"] = want
            save_profile(profile)
        else:
            print(c(f"     which machine? {' · '.join(OS_NAMES)}", "yellow"))

    chooser("🧰 whose machine are these for?",
            "linux · mac · windows · `quit` = the map",
            lambda: print_setup(io), apply)


def catchup_screen(profile):
    """🔁 The route back — and you can start a mission straight off it."""
    while True:
        numbers = catchup(profile)
        answer = ask("🔁 the route back",
                     "a number on the route plays it · `quit` = the map")
        if is_back(answer):
            return None
        mission = numbers.get(answer.strip())
        if mission:
            return mission
        print(c("     that number isn't on the route — they're the ones in `play:`", "yellow"))


def codex(profile, arg=""):
    """📖 Browse the vault: the list → a note → one of its sections.

    Every level answers a prompt rather than dead-ending on "press Enter", and
    a note is named the way a person would name it — `3`, `class 3`, `docker` —
    never by typing "Class 02 - Docker Networking and Images" in full.
    """
    import study
    io = IO()
    while True:
        try:
            verb, _, rest = arg.partition(" ")
            if verb in ("find", "search", "grep") and rest.strip():
                study.search(io, rest.strip())
                arg = ""
                pause("⏎ back to the Codex")
                continue
            ranked = study.list_notes(io, profile)
            if not ranked:
                pause()
                return
            pick = arg or ask("📖 open a note",
                              "number · `class 3` · a word · `find <word>` · `quit` = the map")
            arg = ""
            pick = pick[6:].strip() if pick.lower().startswith("learn ") else pick
            if is_back(pick):
                return
            verb, _, rest = pick.partition(" ")
            if verb.lower() in ("find", "search", "grep") and rest.strip():
                arg = pick
                continue
            title = study.pick_note(pick, ranked)
            if not title:
                print(c(f"  no note matches “{pick}” — try its number, or `class 3`", "yellow"))
                pause("⏎ back to the Codex")
                continue
            read_note(profile, io, study, title)
            save_profile(profile)
        except Exception as exc:            # noqa: BLE001 — the vault is not ours to trust
            print(c(f"the vault couldn't be read: {exc}", "yellow"))
            pause()
            return


def read_note(profile, io, study, title):
    """One note: its TL;DR and sections, then whatever you want out of it."""
    while True:
        study.learn(io, profile, title, "")
        want = ask(f"📖 {title}",
                   "section number · cards · quiz · drills · all · `quit` = the Codex")
        want = want[6:].strip() if want.lower().startswith("learn ") else want
        if is_back(want):
            return
        study.learn(io, profile, title, want)
        save_profile(profile)
        pause("⏎ back to the note")


def watch_resize(redraw):
    """Reflow on window resize, the way a dashboard does.

    SIGWINCH arrives while we're blocked reading the prompt. Redraw, then put
    the prompt back under the cursor — the alternative is a screen laid out for
    a window that no longer exists, with old long lines re-wrapped by the
    terminal into what looks like overlapping text.
    """
    import signal
    if not hasattr(signal, "SIGWINCH") or not sys.stdout.isatty():
        return                              # Windows has no such signal
    try:
        signal.signal(signal.SIGWINCH, lambda *_a: redraw())
    except (ValueError, OSError):           # not the main thread — never fatal
        pass


# Keys that scroll the map the instant they are pressed. ONLY keys that can
# never be part of what you'd type here: no letter, no digit, and not space
# either — space is what separates `/complete` from `on`, and binding it ate
# exactly that. (Reading these at all is why this prompt does not use `input()`:
# readline would swallow the arrows AND take our SIGWINCH handler with it.)
SCROLL_KEYS = {"down": "down", "up": "up", "right": "pgdn", "left": "pgup",
               "pgdn": "pgdn", "pgup": "pgup", "home": "top", "end": "bottom"}


def play():
    profile = load_profile()
    set_player_os(profile.get("os") or detect_os())
    set_theme(profile.get("theme"), profile.get("glyphs"))
    set_emoji_width(profile.get("emoji"))
    if not profile["name"]:
        clear_screen()
        for line in banner_lines(profile, menu_width(), max(6, term_lines() - 8)):
            print(line)
        try:
            profile["name"] = input(c("\nWhat's your handle, engineer? ", "cyan")).strip() or "anonymous"
        except (EOFError, KeyboardInterrupt):
            return
        ask_os(profile)
        save_profile(profile)
        print(c(f"Welcome, {profile['name']}. Your progress saves to progress.json (gitignored — it's yours).", "dim"))
        pause("⏎ to the map")
    elif not profile.get("os"):
        ask_os(profile)          # existing player from before the OS picker existed
        save_profile(profile)

    screen = {"quote": random.choice(QUOTES), "index": {}, "offset": 0}
    PROMPT = c("\n> ", "cyan")

    def redraw():
        """Repaint the map screen and put the prompt back under the cursor.

        Only while the map is the thing on screen: a resize in the middle of a
        mission must not clear the mission away to draw a menu.
        """
        if not screen.get("active"):
            return
        try:
            index, _last = draw_screen(profile, screen["quote"], screen["offset"])
            if index:
                screen["index"] = index
            sys.stdout.write(PROMPT)
            sys.stdout.flush()
        except Exception:                   # noqa: BLE001 — a resize never crashes a game
            pass

    watch_resize(redraw)
    typed = ""
    while True:
        screen["active"] = True
        index, last_offset = draw_screen(profile, screen["quote"], screen["offset"])
        screen["index"] = index or screen["index"]
        index = screen["index"]
        try:
            text, action = prompt_line(PROMPT, SCROLL_KEYS, typed)
        except (EOFError, KeyboardInterrupt):
            text, action = "q", None
        # A paging key fires on the keypress itself; whatever was half-typed
        # comes back to the next prompt, so navigating never costs you a number.
        if action:
            here, jump = screen["offset"], max(1, term_lines() // 2)
            screen["offset"] = {"down": here + 3, "up": here - 3,
                                "pgdn": here + jump, "pgup": here - jump,
                                "top": 0, "bottom": last_offset}[action]
            screen["offset"] = max(0, min(screen["offset"], last_offset))
            typed = text                    # paging never costs you a number
            continue
        typed = ""
        # Commands take a leading slash, the way every other tool with a prompt
        # does it now: it keeps them apart from the numbers, which are the one
        # thing you type here constantly. Bare words still work — a student who
        # types `catchup` is right, and being corrected for a missing slash
        # teaches nothing.
        choice = text.strip().lower()
        choice = choice[1:].strip() if choice.startswith("/") else choice
        screen["active"] = False            # off the map now — no auto-repaint
        if choice in ("q", "quit", "exit"):
            lvl, name = level(profile["xp"])
            print(c(f"\nSee you, {profile['name']} — Level {lvl} {name}, {profile['xp']} XP. 👋\n", "bold"))
            return
        if choice in ("n", "next", "more", ">"):        # typed, for a dumb terminal
            screen["offset"] = min(last_offset, screen["offset"] + term_lines() // 2)
            continue
        if choice in ("p", "prev", "back", "<", "top"):
            screen["offset"] = 0 if choice == "top" else max(
                0, screen["offset"] - term_lines() // 2)
            continue
        if choice == "":
            screen["quote"] = random.choice(QUOTES)
            continue                        # Enter on the map = repaint it
        if choice in ("help", "?", "h"):
            w = menu_width()
            print("")
            print(heading(c("  🧭 MAP COMMANDS", "bold"), c("the slash is optional", "dim"), w))
            for cmd, what in (("<number>", "play that mission (any page — the numbers never move)"),
                              ("/catchup", "the ordered route back to the class you're on"),
                              ("/learn", "📖 the Codex: your vault, browsable · /learn find <word>"),
                              ("/theme", "the prompt's look · /theme emoji if columns look ragged"),
                              ("/complete", f"Tab-completion on/off ({ASSIST_COST} XP a word, "
                                             f"cap {ASSIST_CAP})"),
                              ("/setup", "install the real tools on your own machine"),
                              ("/quit", "leave the game")):
                print(leader(f"   {c(cmd, 'cyan')}", c(what, "dim"), w))
            print(c("\n   keys: ↑ ↓ scroll · PgUp PgDn by half a screen · Home back to the "
                    "banner · End the bottom · Esc clears the line — none need Enter", "dim"))
            pause()
            continue
        if choice in ("catchup", "catch-up", "route", "c"):
            picked = catchup_screen(profile)
            if picked:
                play_mission(profile, picked)
            continue
        if choice == "setup":
            setup_screen(profile)
            continue
        if choice == "theme" or choice.startswith("theme "):
            if choice[5:].strip():          # `/theme kali` — do it and come back
                theme_cmd(IO(), profile, choice[5:].strip())
                pause()
            else:
                theme_screen(profile)
            continue
        if choice == "complete" or choice.startswith("complete "):
            arg = choice[8:].strip()
            if arg in ("on", "off"):        # `/complete on` — do it and come back
                profile.setdefault("assists", {})["complete"] = (arg == "on")
                save_profile(profile)
                print(c(f"⌨  Tab-completion is {'ON' if arg == 'on' else 'OFF'}.", "cyan"))
                pause()
            else:
                complete_screen(profile)
            continue
        if choice == "learn" or choice.startswith("learn ") or choice in ("codex", "study"):
            codex(profile, choice[6:].strip() if choice.startswith("learn ") else "")
            continue
        m = index.get(choice)
        if not m:
            print(c("pick a mission number from the map — or /catchup · /learn · "
                    "/theme · /complete · /setup · /quit", "yellow"))
            pause()
            continue
        play_mission(profile, m)


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
