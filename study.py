"""📖 The Codex — the study half of the game, read straight from your vault.

`learn` used to print the name of a note and leave you to go find it. That is a
footnote, not a feature: the study material and the practice lived in different
windows, and only one of them was a game.

This module makes the notes playable. Point the game at the Obsidian vault
(`python quest.py --vault <folder>`, or it infers the folder from the progress
note you already linked) and `learn` becomes a terminal reader for the mission's
own note, with three modes built from structure the notes already have:

  * `> [!question]- …` under **Self-check quiz** → a quiz you answer and grade
  * `> [!question]- …` under **Flashcards**      → a drill deck that repeats what you miss
  * `- [ ] **(10 XP) …**` under **Drills**        → side quests, listed as challenges

Nothing here pays XP for reading — the game's rule is that doing scores. What it
pays is the **Scholar bonus**: finish a mission having consulted the note and
used zero hints, and you are +5 XP better off than the player who asked for a
hint. Looking it up IS the professional reflex; the economy should say so.

Mastery (cards known, best quiz score, perfect decks) lives in the profile and is
written back into the vault's progress note, so the loop closes: play, study,
and the vault shows both.

Pure standard library, and every vault read is guarded — a vault on a removable
drive that isn't mounted must never take the game down with it.
"""
import os
import re
import shutil

from engine import (COLORS, c, disp_width, heading, leader, load_config, menu_width,
                    pad, save_config, term_cols)

CLASS_NUM = re.compile(r"^(?:class|c)\s*0*(\d+)\s*$")
CALLOUT = re.compile(r"^>\s*\[!(\w+)\]([-+]?)\s*(.*)$")
QUOTE = re.compile(r"^>\s?(.*)$")
DRILL = re.compile(r"^- \[[ xX]\]\s*(.*)$")
FENCE = re.compile(r"^\s*```(\w*)\s*$")
HEADING = re.compile(r"^(#{1,6})\s+(.*)$")

CALLOUT_LOOK = {
    "question": ("❓", "cyan"), "tip": ("💡", "green"), "warning": ("⚠️", "yellow"),
    "danger": ("🚨", "red"), "important": ("❗", "magenta"), "info": ("ℹ️", "blue"),
    "abstract": ("📄", "blue"), "example": ("🧪", "cyan"), "note": ("📝", "dim"),
    "success": ("✅", "green"), "srdeck": ("🔁", "dim"), "quote": ("❝", "dim"),
}


# ------------------------------------------------------------------ vault --
def vault_dir():
    """The vault folder, explicit or inferred from the linked progress note."""
    config = load_config()
    explicit = config.get("vault_dir")
    if explicit and os.path.isdir(explicit):
        return explicit
    progress = config.get("vault_progress_file")
    if progress:
        folder = os.path.dirname(progress)
        if os.path.isdir(folder):
            return folder
    return None


def set_vault_dir(path):
    config = load_config()
    config["vault_dir"] = os.path.abspath(os.path.expanduser(path))
    save_config(config)
    return config["vault_dir"]


def _slug(name):
    """Compare note names the way a human would: ignore case, emoji and spacing."""
    return re.sub(r"[^a-z0-9]+", "", name.lower())


# A vault is somebody's repo as well as their study material. These are its
# plumbing, not notes — listing them as things to study is noise.
NOT_NOTES = {"readme", "claude", "license", "contributing", "teacherprompt",
             "changelog", "shellquestprogress"}


def notes_index():
    """Every note in the vault: [(title, path)], shallowest first."""
    root = vault_dir()
    if not root:
        return []
    found = []
    try:
        for base, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs
                       if not d.startswith(".") and d not in ("plans", "uploads", "setup")]
            for f in files:
                if f.endswith(".md") and _slug(f[:-3]) not in NOT_NOTES:
                    found.append((f[:-3], os.path.join(base, f)))
    except OSError:
        return []
    return sorted(found, key=lambda t: (t[1].count(os.sep), t[0].lower()))


def shown_name(path):
    """Notes in a subfolder show it — two files called SOLUTION are otherwise
    the same row twice."""
    root = vault_dir()
    try:
        rel = os.path.relpath(path, root)[:-3]
    except (ValueError, TypeError):
        return os.path.basename(path)[:-3]
    return rel.replace(os.sep, "/")


def find_note(name):
    """The file for a mission's `vault_note`, matched forgivingly."""
    if not name:
        return None
    want = _slug(name)
    index = notes_index()
    for title, path in index:
        if _slug(title) == want:
            return path
    for title, path in index:                    # 'Class 03 - Git' ⊃ 'Git'
        if want and want in _slug(title):
            return path
    # `class 3` — the way anyone actually refers to a class, and not a substring
    # of any title (it's written `Class 03`).
    m = CLASS_NUM.match(name.strip().lower())
    if m:
        for title, path in index:
            got = re.search(r"class\s*0*(\d+)", title.lower())
            if got and int(got.group(1)) == int(m.group(1)):
                return path
    return None


def read(path):
    """A note's text, or None if the file cannot be read at all.

    `errors="replace"` is doing real work: the vault belongs to the player, and
    one note saved by some other editor in latin-1 used to raise
    UnicodeDecodeError out of `read` — which took down not just that note but
    `learn`, `learn find` and the whole note index with it. A stray byte should
    cost one character, not the Codex.
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


# ----------------------------------------------------------------- parsing --
def strip_frontmatter(text):
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[text.find("\n", end + 1) + 1:]
    return text


def sections(text):
    """[(level, title, body_lines)] — the note's `##` structure, in order."""
    out, level, title, body, fence = [], 1, "(top)", [], False
    for line in strip_frontmatter(text).split("\n"):
        if FENCE.match(line):
            fence = not fence
        m = None if fence else HEADING.match(line)
        if m and len(m.group(1)) <= 3:
            out.append((level, title, body))
            level, title, body = len(m.group(1)), m.group(2).strip(), []
        else:
            body.append(line)
    out.append((level, title, body))
    return [s for s in out if s[2] or s[1] != "(top)"]


def callouts(lines, kind=None):
    """[(type, title, [body])] for the `> [!type]` blocks in these lines."""
    out, cur = [], None
    for line in lines:
        m = CALLOUT.match(line)
        if m:
            if cur:
                out.append(cur)
            cur = (m.group(1).lower(), m.group(3).strip(), [])
            continue
        q = QUOTE.match(line)
        if cur and q:
            cur[2].append(q.group(1))
            continue
        if cur:
            out.append(cur)
            cur = None
    if cur:
        out.append(cur)
    return [x for x in out if kind is None or x[0] == kind]


def _section_named(text, *words):
    """The body of the first `##` section whose title mentions one of `words`."""
    for _level, title, body in sections(text):
        low = title.lower()
        if any(w in low for w in words):
            return body
    return []


def quiz_items(text):
    """The note's Self-check quiz: [(question, answer)]."""
    body = _section_named(text, "self-check", "quiz")
    return [(t, "\n".join(b).strip()) for _k, t, b in callouts(body, "question") if t]


def card_items(text):
    """The note's flashcards: [(front, back)]."""
    body = _section_named(text, "flashcard", "🃏")
    return [(t, "\n".join(b).strip()) for _k, t, b in callouts(body, "question") if t]


def drill_items(text):
    """The note's drills: [(title, rest, xp)] from `- [ ] **(10 XP) Name.** …`."""
    out = []
    for line in _section_named(text, "drill"):
        m = DRILL.match(line.strip())
        if not m:
            continue
        item = m.group(1)
        xp = re.search(r"\((\d+)\s*XP\)", item)
        name = re.search(r"\*\*\(\d+\s*XP\)\s*([^*]+)\*\*", item)
        out.append((name.group(1).strip() if name else item[:60],
                    re.sub(r"\*\*\(\d+\s*XP\)[^*]*\*\*", "", item).strip(),
                    int(xp.group(1)) if xp else 0))
    return out


# --------------------------------------------------------------- rendering --
def width():
    """Prose width — deliberately NOT the whole window.

    The menus in the game stretch to the terminal (see engine.menu_width); a
    note is a page of text, and a 200-column line of prose is unreadable no
    matter how much room the window offers. 96 is about the widest a paragraph
    should ever get.
    """
    return max(48, min(term_cols(), 96)) - 2


def tint(text, colour):
    """Colour a run that already contains colour. Every inner `c()` ends with a
    RESET, which would drop the outer colour for the rest of the line — so each
    reset re-opens it."""
    return c(text.replace(COLORS["reset"], COLORS["reset"] + COLORS[colour]), colour)


def inline(text):
    """Markdown emphasis → terminal colour. Order matters: code before bold."""
    text = re.sub(r"`([^`]+)`", lambda m: c(m.group(1), "cyan"), text)
    text = re.sub(r"\*\*([^*]+)\*\*", lambda m: c(m.group(1), "bold"), text)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", lambda m: c(m.group(1), "bold"), text)
    text = re.sub(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]",
                  lambda m: c(f"[[{m.group(1)}]]", "magenta"), text)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)",
                  lambda m: m.group(1) + c(f" ⟨{m.group(2)}⟩", "dim"), text)
    return text


def wrap(text, indent="", first=None):
    import textwrap
    return textwrap.wrap(text, width=width(), initial_indent=first if first is not None
                         else indent, subsequent_indent=indent) or [first or indent]


TABLE_ROW = re.compile(r"^\s*\|(.+)\|\s*$")
TABLE_RULE = re.compile(r"^[\s|:-]+$")


def render_table(rows):
    """A markdown table as aligned columns.

    The notes carry a lot of their reference material in tables — signal
    numbers, kubectl verbs, git commands. Printed as raw `| a | b |` they wrap
    into pipe soup, which is the fastest way to make a reader skip the densest
    part of the page.
    """
    import textwrap
    grid = [[cell.strip() for cell in TABLE_ROW.match(r).group(1).split("|")]
            for r in rows if not TABLE_RULE.match(r)]
    if not grid:
        return []
    cols = max(len(r) for r in grid)
    grid = [r + [""] * (cols - len(r)) for r in grid]
    want = [max(disp_width(r[i]) for r in grid) for i in range(cols)]
    room = width() - 4 - 3 * (cols - 1)
    if sum(want) > room:                      # squeeze the widest columns first
        share = max(8, room // cols)
        spare = sum(min(w, share) for w in want)
        want = [min(w, share + (room - spare) // max(1, sum(x > share for x in want)))
                if w > share else w for w in want]
    out = []
    for n, row in enumerate(grid):
        wrapped = [textwrap.wrap(cell, width=max(4, want[i])) or [""]
                   for i, cell in enumerate(row)]
        for line_no in range(max(len(w) for w in wrapped)):
            parts = []
            for i in range(cols):
                cell = wrapped[i][line_no] if line_no < len(wrapped[i]) else ""
                shown = inline(cell)
                parts.append(pad(shown, want[i]) if i < cols - 1 else shown)
            line = "   " + "   ".join(parts).rstrip()
            out.append(c(line, "bold") if n == 0 else line)
        if n == 0:
            rule = min(max(disp_width(x) for x in out) - 3, width() - 4)
            out.append(c("   " + "─" * max(rule, 8), "dim"))
    return out


def render(lines, hide_answers=False):
    """Markdown lines → the coloured, wrapped lines to print."""
    out, fence, callout = [], False, None
    table, in_quoted_fence = [], False
    for raw in list(lines) + [""]:            # the sentinel flushes a trailing table
        line = raw.rstrip()
        if TABLE_ROW.match(line) and not fence:
            table.append(line)
            continue
        if table:
            out += render_table(table)
            table = []
        fm = FENCE.match(line)
        if fm:
            fence = not fence
            if fence and fm.group(1):
                out.append(c(f"    ┌─ {fm.group(1)}", "dim"))
            continue
        if fence:
            out.append(c("    │ ", "dim") + c(line, "cyan"))
            continue
        m = CALLOUT.match(line)
        if m:
            kind = m.group(1).lower()
            icon, colour = CALLOUT_LOOK.get(kind, ("▸", "blue"))
            callout = kind
            head = m.group(3).strip() or kind.upper()
            if kind == "srdeck":
                # The spaced-repetition plugin's raw deck: a hundred lines of
                # Q::A that exist for a machine. `learn cards` is the human way in.
                out.append(c(f"  🔁 {head}", "dim"))
                out.append(c("     (raw review deck, hidden here — `learn cards` drills "
                             "the same questions)", "dim"))
                continue
            out.append(c(f"  {icon} ", colour) + c(inline(head), "bold"))
            continue
        q = QUOTE.match(line)
        if q is not None and callout:
            body = q.group(1).rstrip()
            if in_quoted_fence:
                # A command must never be word-wrapped: a student copies these.
                if body.strip().startswith("```"):
                    in_quoted_fence = False
                else:
                    out.append(c("     │ ", "dim") + c(body.strip(), "cyan"))
                continue
            if body.strip().startswith("```"):
                in_quoted_fence = True
                lang = body.strip()[3:].strip()
                out.append(c(f"     ┌─ {lang}" if lang else "     ┌─", "dim"))
                continue
            body = body.strip()
            if not body or callout == "srdeck":
                continue
            nested = CALLOUT.match(body)      # `> > [!tip] …` — a callout inside one
            if nested:
                icon, colour = CALLOUT_LOOK.get(nested.group(1).lower(), ("▸", "blue"))
                out.append(c(f"     {icon} ", colour)
                           + c(inline(nested.group(3).strip() or nested.group(1)), "bold"))
                continue
            body = re.sub(r"^>\s?", "", body)
            out += [tint(x, "dim") for x in wrap(inline(body), indent="     ")]
            continue
        callout = None
        if not line.strip():
            out.append("")
            continue
        h = HEADING.match(line)
        if h:
            level, title = len(h.group(1)), inline(h.group(2))
            if level == 1:
                out += ["", c("━" * min(width(), 60), "blue"), c(f"  {title}", "bold"),
                        c("━" * min(width(), 60), "blue")]
            elif level == 2:
                out += ["", c(f"  {title}", "bold"), c("  " + "─" * min(width() - 2, 46), "dim")]
            else:
                out += ["", c(f"   {title}", "cyan")]
            continue
        d = DRILL.match(line.strip())
        if d:
            out += wrap(inline(d.group(1)), indent="      ", first="   ☐  ")
            continue
        li = re.match(r"^(\s*)([-*+]|\d+\.)\s+(.*)$", line)
        if li:
            pad = "  " + " " * len(li.group(1))
            out += wrap(inline(li.group(3)), indent=pad + "   ", first=pad + " • ")
            continue
        if set(line.strip()) <= {"-", "*", "_"} and len(line.strip()) >= 3:
            out.append(c("  " + "·" * min(width() - 2, 46), "dim"))
            continue
        out += wrap(inline(line.strip()), indent="  ")
    return out


def page(io, lines):
    """Print with a pager. Returns False if the reader asked to stop."""
    try:
        rows = max(10, shutil.get_terminal_size().lines - 4)
    except Exception:                                  # noqa: BLE001
        rows = 20
    shown = 0
    for line in lines:
        io.print(line)
        shown += 1
        if shown % rows == 0:
            try:
                nxt = io.input(c("   —— more —— ⏎ next · q back", "dim")).strip().lower()
            except (EOFError, KeyboardInterrupt):
                io.print("")
                return False
            if nxt in ("q", "quit", "stop", "exit"):
                return False
    return True


# ------------------------------------------------------------ study record --
def record(profile, note):
    """This note's study record inside the profile (created on demand)."""
    study = profile.setdefault("study", {})
    return study.setdefault(note, {"cards_known": [], "quiz_best": 0, "quiz_total": 0,
                                   "perfect": False, "reads": 0})


def mastery(profile, note, total_cards):
    rec = (profile.get("study") or {}).get(note)
    if not rec or not total_cards:
        return ""
    known = len(rec.get("cards_known", []))
    bar = "█" * round(8 * known / total_cards) + "░" * (8 - round(8 * known / total_cards))
    star = " 🌟" if rec.get("perfect") else ""
    return c(f"{bar} {known}/{total_cards} cards{star}", "green" if known else "dim")


# ------------------------------------------------------------------ modes --
def show_note(io, path, title, arg=""):
    """Read the note: a table of contents, then any section by number or name."""
    text = read(path)
    if text is None:
        io.print(c(f"📖 could not read {path} — is the vault mounted?", "yellow"))
        return
    secs = [s for s in sections(text) if s[1] != "(top)"]
    if arg:
        pick = None
        if arg.isdigit() and 1 <= int(arg) <= len(secs):
            pick = secs[int(arg) - 1]
        else:
            want = _slug(arg)
            pick = next((s for s in secs if want in _slug(s[1])), None)
        if not pick:
            io.print(c(f"   no section matching '{arg}' — `learn toc` lists them", "yellow"))
            return
        io.print("")
        page(io, render([f"{'#' * pick[0]} {pick[1]}"] + pick[2]))
        return
    io.print("")
    io.print(c(f"  📖 {title}", "bold"))
    io.print(c(f"     {path}", "dim"))
    # The note's own TL;DR is the best blurb it could have — the author already
    # wrote the summary, so don't invent a worse one from the first paragraph.
    blurb = next((cl for cl in callouts(text.split("\n"))
                  if cl[0] in ("abstract", "tldr", "summary")), None)
    if blurb:
        io.print("")
        for line in render(["> [!abstract] " + (blurb[1] or "TL;DR")]
                           + ["> " + b for b in blurb[2]]):
            io.print(line)
    io.print(c("\n  Sections — `learn <number>` or `learn <word>` opens one:", "cyan"))
    for i, (level, stitle, body) in enumerate(secs, 1):
        pad = "   " if level == 2 else "      "
        io.print(f"{pad}{c(str(i).rjust(2), 'bold')}. {inline(stitle)}"
                 + c(f"  ({len(body)} lines)", "dim"))
    io.print(c("\n  study modes:  learn cards · learn quiz · learn drills · "
               "learn find <word>  ·  learn all reads the whole note", "cyan"))


def flashcards(io, profile, path, note):
    """Drill the note's flashcards. Missed cards come back until they stick."""
    text = read(path) or ""
    cards = card_items(text)
    if not cards:
        io.print(c("   this note has no 🃏 flashcards section yet", "yellow"))
        return
    rec = record(profile, note)
    known = set(rec.get("cards_known", []))
    io.print("")
    io.print(c(f"  🃏 FLASHCARDS — {note}   {len(cards)} cards · {len(known & {f for f, _ in cards})} already mastered", "bold"))
    io.print(c("     ⏎ flips the card · y = knew it · n = missed it · q = stop", "dim"))
    queue = list(cards)
    first_try, missed, seen = 0, set(), 0
    while queue:
        front, back = queue.pop(0)
        seen += 1
        io.print("")
        # The denominator is the DECK, not the queue: cards you miss come back,
        # and a total that grows as you struggle is a demoralising way to say so.
        io.print(c(f"  card {min(seen, len(cards))}/{len(cards)}"
                   + (f" · ↻{len(missed)} to revisit" if missed else "") + "  ", "dim")
                 + c(inline(front), "bold"))
        try:
            io.input(c("     ⏎ flip ", "dim"))
        except (EOFError, KeyboardInterrupt):
            io.print("")
            return
        for line in render(back.split("\n")):
            io.print(line)
        try:
            got = io.input(c("     knew it? (y/n/q) ", "cyan")).strip().lower()
        except (EOFError, KeyboardInterrupt):
            io.print("")
            return
        if got.startswith("q"):
            break
        if got.startswith("y"):
            known.add(front)
            if front not in missed:
                first_try += 1
        else:
            missed.add(front)
            known.discard(front)
            queue.append((front, back))              # it comes back around
    rec["cards_known"] = sorted(known)
    perfect = first_try == len(cards) and not missed
    io.print("")
    if perfect:
        rec["perfect"] = True
        io.print(c(f"  🌟 PERFECT DECK — {len(cards)}/{len(cards)} on the first try.", "magenta"))
        badge = f"🃏 {note}"
        if badge not in profile.setdefault("badges", []):
            profile["badges"].append(badge)
            io.print(c(f"  🏅 badge earned: {badge}", "green"))
    else:
        io.print(c(f"  first-try {first_try}/{len(cards)} · mastered {len(known)}/{len(cards)}"
                   + (f" · {len(missed)} to revisit" if missed else ""), "bold"))
        if missed:
            io.print(c("     came back around: " + ", ".join(sorted(missed)[:6]), "dim"))
    io.print(c("     (mastery is saved — `learn cards` again to push it to 🌟)", "dim"))


def selfquiz(io, profile, path, note):
    """The note's self-check questions: answer, reveal, grade yourself."""
    text = read(path) or ""
    items = quiz_items(text)
    if not items:
        io.print(c("   this note has no 🧪 self-check quiz yet", "yellow"))
        return
    rec = record(profile, note)
    io.print("")
    io.print(c(f"  🧪 SELF-CHECK — {note}   {len(items)} questions", "bold"))
    io.print(c("     answer in your own words, then ⏎ to compare with the note", "dim"))
    score = 0
    for i, (q, a) in enumerate(items, 1):
        io.print("")
        for line in wrap(inline(re.sub(r"^\d+\.\s*", "", q)), indent="     ",
                         first=c(f"  {i}. ", "bold")):
            io.print(line)
        try:
            io.input(c("     your answer: ", "cyan"))
            io.input(c("     ⏎ reveal ", "dim"))
        except (EOFError, KeyboardInterrupt):
            io.print("")
            return
        for line in render(a.split("\n")):
            io.print(line)
        try:
            got = io.input(c("     did you have it? (y/n/q) ", "cyan")).strip().lower()
        except (EOFError, KeyboardInterrupt):
            io.print("")
            return
        if got.startswith("q"):
            break
        score += got.startswith("y")
    io.print("")
    best = rec.get("quiz_best", 0)
    io.print(c(f"  score {score}/{len(items)}"
               + (f" · your best is {best}/{rec.get('quiz_total', len(items))}"
                  if best else ""), "bold"))
    rec["quiz_total"] = len(items)
    if score > best:
        rec["quiz_best"] = score
        if score == len(items):
            io.print(c("  🌟 clean sweep — you can explain every one of them.", "magenta"))
        else:
            io.print(c("  new personal best. 📈", "green"))
    elif score < len(items):
        # No congratulations for a score you didn't beat — say what to do instead.
        io.print(c("  the note has the answers to the ones you missed: `learn quiz` again, "
                   "or `learn find <word>` for the section behind one.", "dim"))


def show_drills(io, path, note):
    """The note's drills, as side quests. They're graded by you, in a real shell."""
    text = read(path) or ""
    items = drill_items(text)
    if not items:
        io.print(c("   this note has no 🔬 drills section yet", "yellow"))
        return
    io.print("")
    io.print(c(f"  🔬 DRILLS — {note}   {len(items)} side quests · "
               f"{sum(x[2] for x in items)} XP on the note's own scale", "bold"))
    io.print(c("     these are yours to run — in a 🐧 Linux mission here, or on your own box",
               "dim"))
    for i, (name, rest, xp) in enumerate(items, 1):
        io.print("")
        io.print(c(f"   {i}. {name}", "cyan") + c(f"  ({xp} XP)" if xp else "", "dim"))
        for line in wrap(inline(rest), indent="      "):
            io.print(line)


def snippet(line):
    """One matched line, stripped of the markdown that carried it — a search hit
    should read like a sentence, not like the file it came out of."""
    line = re.sub(r"^\s*>\s?", "", line)
    line = re.sub(r"^\[!\w+\][-+]?\s*", "", line)
    line = re.sub(r"^\s*([-*+]|\d+\.)\s+", "", line)
    line = re.sub(r"^#+\s*", "", line).strip()
    if line.startswith("|") and line.endswith("|"):          # a table row
        line = " · ".join(x.strip() for x in line.strip("|").split("|") if x.strip())
    return line


def search(io, query):
    """Grep the whole vault, and show which note and section each hit is in."""
    hits, index = 0, notes_index()
    if not index:
        return no_vault(io)
    io.print("")
    io.print(c(f"  🔎 searching {len(index)} notes for '{query}'", "bold"))
    rx = re.compile(re.escape(query), re.I)
    for title, path in index:
        text = read(path)
        if not text or not rx.search(text):
            continue
        for _level, stitle, body in sections(text):
            for line in body:
                if rx.search(line) and line.strip() and not line.strip().startswith("```"):
                    if hits < 24:
                        io.print("")
                        io.print(c(f"   {title}", "cyan") + c(f"  › {stitle}", "dim"))
                        snip = rx.sub(lambda m: c(m.group(0), "bold"), snippet(line))
                        for out in wrap(inline(snip), indent="      "):
                            io.print(out)
                    hits += 1
                    break                              # one line per section is enough
    if not hits:
        io.print(c("   nothing matched — try a single word, or `learn notes` to browse",
                   "yellow"))
    else:
        io.print(c(f"\n   {hits} section(s) matched"
                   + ("  (showing the first 24)" if hits > 24 else "")
                   + " · `learn <note name>` opens one", "dim"))


def course_notes():
    """The notes the missions pair with, in course order — the study spine."""
    try:
        from missions import ALL_MISSIONS
    except Exception:                                  # noqa: BLE001
        return []
    out = []
    for m in ALL_MISSIONS:
        if m.get("vault_note") and m["vault_note"] not in out:
            out.append(m["vault_note"])
    return out


def list_notes(io, profile, here=()):
    """The Codex home: every note, with what you've mastered in it.

    Course notes lead, in the order the classes run — the vault holds plenty
    besides, and an alphabetical dump buries the one you're meant to read next.
    Numbered, and RETURNS the list in the order shown, so the caller can let a
    reader open one by typing `3` instead of `Class 02 - Docker Networking and
    Images`.
    """
    index = notes_index()
    if not index:
        no_vault(io)
        return []
    w = menu_width()
    io.print("")
    io.print(heading(c("  📚 THE CODEX", "bold"),
                     c(f"{len(index)} notes in {vault_dir()}", "dim"), w))
    spine = course_notes()
    ranked = sorted(index, key=lambda x: next(
        (i for i, s in enumerate(spine) if _slug(s) == _slug(x[0])), len(spine)))
    for n, (title, path) in enumerate(ranked, 1):
        text = read(path) or ""
        cards = card_items(text)
        rec = (profile.get("study") or {}).get(title, {})
        bits = []
        if cards:
            bits.append(mastery(profile, title, len(cards)) or c(f"0/{len(cards)} cards", "dim"))
        if rec.get("quiz_best"):
            bits.append(c(f"quiz {rec['quiz_best']}/{rec.get('quiz_total', '?')}", "green"))
        if any(_slug(title) == _slug(t) for t in here):
            mark = c("🗡️", "magenta")                  # the mission you're in
        elif any(_slug(title) == _slug(s) for s in spine):
            mark = c("📘", "dim")                      # pairs with some mission
        else:
            mark = " "
        io.print(leader(f"  {c(f'{n:>2}.', 'bold')} {mark} {c(shown_name(path), 'cyan')}",
                        "  ".join(bits), w))
    io.print(c("\n   open one by number, by class (`class 3`), or by any word in its name "
               "· `find <word>` searches them all", "dim"))
    return ranked


def pick_note(query, ranked):
    """Resolve what a human actually types into one of `ranked`'s titles.

    `3` · `class 3` · `class 03` · `docker` · the whole title. Nobody is going
    to type "Class 02 - Docker Networking and Images", and asking them to is how
    a library ends up unused.
    """
    q = (query or "").strip().lower()
    if not q or not ranked:
        return None
    if q.isdigit():
        i = int(q) - 1
        return ranked[i][0] if 0 <= i < len(ranked) else None
    m = CLASS_NUM.match(q)
    if m:
        want = int(m.group(1))
        for title, _path in ranked:
            got = re.search(r"class\s*0*(\d+)", title.lower())
            if got and int(got.group(1)) == want:
                return title
        return None
    slug = _slug(q)
    for title, _path in ranked:                  # exact, then contained
        if _slug(title) == slug:
            return title
    for title, _path in ranked:
        if slug and slug in _slug(title):
            return title
    return None


def no_vault(io):
    """No vault linked — say exactly how to get one, one command per line."""
    io.print("")
    io.print(c("  📖 No vault linked yet — that's what turns `learn` into a study game.", "yellow"))
    io.print(c("     The course notes are a public Obsidian vault: quizzes, flashcards and "
               "drills per class.", "dim"))
    io.print(c("\n     git clone https://github.com/iceteps/devops-study-vault", "cyan"))
    io.print(c("     python quest.py --vault devops-study-vault", "cyan"))
    io.print(c("\n     Already have it? Point at the folder that holds the .md notes.", "dim"))


# ------------------------------------------------------------- entry point --
def learn(io, profile, note_name, arg=""):
    """The `learn` meta-command. Returns True if a note was actually consulted."""
    arg = (arg or "").strip()
    verb, _, rest = arg.partition(" ")
    verb, rest = verb.lower(), rest.strip()
    if not vault_dir():
        if verb in ("link", "vault") and rest:
            io.print(c(f"  📖 vault linked: {set_vault_dir(rest)}", "green"))
            return False
        no_vault(io)
        io.print(c(f"\n     This mission pairs with the note: {note_name}", "cyan"))
        return False
    if verb in ("link", "vault") and rest:
        io.print(c(f"  📖 vault linked: {set_vault_dir(rest)}", "green"))
        return False
    if verb in ("find", "search", "grep") and rest:
        search(io, rest)
        return True
    if verb in ("notes", "codex", "list", "vault"):
        list_notes(io, profile, [note_name])
        return True

    path = find_note(note_name)
    if not path:
        io.print(c(f"  📖 no note named '{note_name}' in {vault_dir()}", "yellow"))
        list_notes(io, profile, [note_name])
        return False
    title = os.path.basename(path)[:-3]
    record(profile, title)["reads"] += 1

    if verb in ("cards", "flashcards", "deck"):
        flashcards(io, profile, path, title)
    elif verb in ("quiz", "test", "self-check", "selfcheck"):
        selfquiz(io, profile, path, title)
    elif verb in ("drills", "drill"):
        show_drills(io, path, title)
    elif verb in ("all", "full", "read"):
        page(io, render(strip_frontmatter(read(path) or "").split("\n")))
    elif verb in ("toc", "sections"):
        show_note(io, path, title)
    else:
        show_note(io, path, title, arg)
    return True
