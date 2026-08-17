"""powerlevel10k's look, rebuilt in the standard library.

p10k is a zsh theme, and this game is a Python program that must run after a
bare `git clone` — so none of it can be borrowed, only rebuilt. What IS
borrowed is the design, because the design is the teaching:

  * a **two-line** prompt — decoration on top, a bare `❯` where you type. That
    is p10k's default for a reason, and here it is also the only shape that
    works: `engine.rl_prompt` has to strip colour from a prompt carrying
    non-ASCII glyphs (readline miscounts it and redraws the line twice), so the
    fancy half lives on a line we PRINT and the readline prompt stays short.
  * segments that appear only when they have something to say — p10k shows the
    exit code when a command failed, jobs when jobs exist, and nothing when
    there is nothing. A prompt that always shows everything teaches nothing.
  * the same information a real p10k user reads: where you are, who you are,
    what the last command returned, what's running in the background.

Three glyph tiers, because we cannot see the player's font: `nerd` uses the
Nerd Font code points (the powerline arrows and  ), `unicode` uses emoji and
box characters any modern terminal has, `ascii` uses none. `setup` prints how
to install a Nerd Font on the player's real machine.

Three styles: `kali` (the distro's own ┌──(root㉿host)-[~] / └─$, where this
whole look comes from), `lean` (coloured text, thin separators — p10k's
most-used style) and `rainbow` (256-colour background blocks with powerline
arrows, the look people picture when they say powerlevel10k).
"""
from engine import NO_COLOR, c, color_depth, menu_width, prompt_theme, spread

# Glyphs per tier. Keys are the same in all three so a segment names what it
# means ("folder") and never which character to print.
TIERS = {
    "nerd": {
        "top": "╭─", "bot": "╰─", "arrow": "❯", "sep": "", "rsep": "",
        "psep": "", "rpsep": "", "host": "", "folder": "",
        "err": "", "jobs": "", "clock": "", "kbd": "",
        "git": "", "k8s": "⎈",
    },
    "unicode": {
        "top": "╭─", "bot": "╰─", "arrow": "❯", "sep": "│", "rsep": "│",
        "psep": "▶", "rpsep": "◀", "host": "🐧", "folder": "📁", "err": "✘",
        "jobs": "⚙", "clock": "🕐", "kbd": "⌨", "git": "⎇", "k8s": "☸",
    },
    "ascii": {
        "top": "+-", "bot": "\\-", "arrow": ">", "sep": "|", "rsep": "|",
        "psep": ">", "rpsep": "<", "host": "", "folder": "", "err": "x",
        "jobs": "&", "clock": "", "kbd": "tab", "git": "git", "k8s": "k8s",
    },
}

# (background, foreground) as xterm-256 indices, for the rainbow style.
BLOCKS = {
    "cyan": (31, 231), "blue": (24, 231), "green": (28, 231),
    "yellow": (136, 231), "red": (160, 231), "magenta": (90, 231),
    "dim": (238, 252), "bold": (240, 231),
}


def tier():
    return TIERS.get(prompt_theme()[1], TIERS["unicode"])


def _glyph(key):
    return tier().get(key, "")


def _plain(segments, joiner):
    out = []
    for icon, text, _colour in segments:
        mark = _glyph(icon)
        out.append(f"{mark} {text}".strip())
    return joiner.join(out)


def _lean(segments, sep):
    """Coloured text, thin separators — no background, so it survives any
    terminal that can do the eight colours at all."""
    parts = []
    for icon, text, colour in segments:
        mark = _glyph(icon)
        parts.append(c(f"{mark} {text}".strip(), colour))
    return c(f" {sep} ", "dim").join(parts)


def _rainbow(segments, right=False):
    """256-colour blocks with powerline arrows: bg of one segment becomes the
    fg of the arrow into the next, which is what makes them look welded."""
    arrow = _glyph("rpsep" if right else "psep")
    out, prev = [], None
    for i, (icon, text, colour) in enumerate(segments):
        bg, fg = BLOCKS.get(colour, BLOCKS["dim"])
        mark = _glyph(icon)
        body = f" {mark} {text} ".replace("  ", " ") if mark else f" {text} "
        if right:
            out.append(f"\033[49m\033[38;5;{bg}m{arrow}" if i == 0
                       else f"\033[48;5;{bg}m\033[38;5;{prev}m{arrow}")
            out.append(f"\033[48;5;{bg}m\033[38;5;{fg}m{body}")
        else:
            if i:
                out.append(f"\033[48;5;{bg}m\033[38;5;{prev}m{arrow}")
            out.append(f"\033[48;5;{bg}m\033[38;5;{fg}m{body}")
        prev = bg
    if not out:
        return ""
    if right:
        return "".join(out) + "\033[0m"
    return "".join(out) + f"\033[49m\033[38;5;{prev}m{arrow}\033[0m"


def _kali(left, code):
    """Kali's own two-line prompt, which is where this whole look comes from:

        ┌──(root㉿quest-host)-[~/linux_course]
        └─$

    On Kali the `$` turns red when the last command failed; that one red
    character is the entire status display, and it is a good lesson in how
    little a prompt needs to say.
    """
    who = next((t for i, t, _c in left if i == "host"), "root@quest-host")
    where = next((t for i, t, _c in left if i == "folder"), "~")
    user, _at, host = who.partition("@")
    join = "㉿" if prompt_theme()[1] != "ascii" else "@"
    box_l, box_r = ("┌──", "└─") if prompt_theme()[1] != "ascii" else ("+--", "\\-")
    head = (c(box_l, "blue") + c("(", "blue") + c(user, "red") + c(join, "dim")
            + c(host, "red") + c(")-[", "blue") + where + c("]", "blue"))
    return head + "\n" + c(box_r, "blue") + c("$", "red" if code else "blue") + " "


def render(left, right=(), width=None):
    """The whole prompt as ONE string with a newline in it.

    `engine.run_mission` prints everything before the last newline and hands
    only the last line to readline — that split is what keeps the glyphs off
    readline's ruler.
    """
    style, _tiername = prompt_theme()
    width = width or menu_width()
    code = next((t for i, t, _c in left if i == "err"), "")
    left = [s for s in left if s[1] not in ("", None)]
    right = [s for s in right if s[1] not in ("", None)]
    top, bot, arrow = _glyph("top"), _glyph("bot"), _glyph("arrow")
    if style == "kali":
        return _kali(left, code)
    if style == "rainbow" and color_depth() in ("truecolor", "256"):
        head, tail = c(top, "dim") + _rainbow(left), _rainbow(right, right=True)
    elif NO_COLOR or color_depth() == "none":
        head = f"{top} " + _plain(left, f" {_glyph('sep')} ")
        tail = _plain(right, f" {_glyph('rsep')} ")
        return spread(head, tail, width) + f"\n{bot}{arrow} "
    else:
        head = f"{c(top, 'dim')} " + _lean(left, _glyph("sep"))
        tail = _lean(right, _glyph("rsep"))
    line = spread(head, tail, width) if tail else head
    # The second line is what readline measures, so it stays short and quiet.
    return line + "\n" + c(bot, "dim") + c(arrow, "green") + " "


def sample(style, tiername, width=None):
    """One rendered example line — what `theme` shows before you commit to it."""
    import engine
    was = (engine.PROMPT_THEME, engine.GLYPH_TIER)
    engine.set_theme(style, tiername)
    try:
        text = render([("host", "root@quest-host", "yellow"),
                       ("folder", "~/linux_course/week1", "cyan"),
                       ("err", "1", "red")],
                      [("kbd", "−2 XP", "magenta"), ("clock", "14:22", "dim")],
                      width=width)
    finally:
        engine.set_theme(*was)
    return text.split("\n")[0]
