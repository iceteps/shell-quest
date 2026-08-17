"""The map screen, driven through a real terminal.

Everything this file tests is invisible to the other two suites, because it only
exists when a tty does: a keypress that acts without Enter, an escape sequence
that must NOT arrive as the text "[6~", a screen that reflows on SIGWINCH, a
scrollbar that has to stay inside the window. So it makes a terminal — pty.fork,
which gives the child the pty as its CONTROLLING terminal (without that the
kernel has no foreground process group to send SIGWINCH to and every resize is
silently dropped) — drives the game through it, and reads the screen back.

    python tests/test_keys_and_commands.py

POSIX only; on Windows it skips itself. The player's own progress.json is moved
aside for the duration and put back afterwards — a test must never cost somebody
their save file.
"""
import atexit
import codecs
import fcntl
import json
import os
import pty
import re
import select
import shutil
import struct
import sys
import tempfile
import termios
import time

ANSI = re.compile(r"\033\[[0-9;?]*[A-Za-z]")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GAME = os.path.join(HERE, "quest.py")
sys.path.insert(0, HERE)
from engine import disp_width            # noqa: E402 — measure like the game does

if not hasattr(os, "fork"):               # pragma: no cover — Windows
    print("no pty here (Windows) — skipping the terminal tests")
    sys.exit(0)

# The game saves into progress.json next to engine.py. Park the player's real
# one, play with a throwaway, and put theirs back whatever happens.
PROFILE = os.path.join(HERE, "progress.json")
PARKED = PROFILE + ".test-backup"
if os.path.exists(PROFILE):
    os.replace(PROFILE, PARKED)
with open(PROFILE, "w", encoding="utf-8") as f:
    json.dump({"name": "pytest", "xp": 0, "completed": {}, "os": "linux"}, f)


def restore_profile():
    try:
        os.remove(PROFILE)
    except OSError:
        pass
    if os.path.exists(PARKED):
        os.replace(PARKED, PROFILE)


atexit.register(restore_profile)

# The Codex reads the player's OWN vault (quest.config.json → vault_dir), so on
# a fresh runner `/learn` correctly says "no vault linked" and every Codex check
# fails for the wrong reason. Bring one: three tiny notes in a throwaway folder,
# with the config parked exactly the way the profile is. Now the Codex is tested
# the same here and in CI, and against notes whose content the test knows.
CONFIG = os.path.join(HERE, "quest.config.json")
PARKED_CONFIG = CONFIG + ".test-backup"
VAULT = tempfile.mkdtemp(prefix="quest-test-vault-")

# Deliberately NOT the missions' real `vault_note` names: unmatched notes keep
# the index's alphabetical order, so `2` is always Class 02 whatever the course
# spine grows into.
NOTES = {
    "Class 01 - Fixture Basics": """---
tags: [fixture]
---

## Overview
The shell is a program that reads a line and runs it.

> [!abstract] TL;DR
> One note, three sections — enough for the Codex to page through.

## Commands
`ls` lists, `cd` moves, `pwd` says where you are.

## 🃏 Flashcards
> [!question]- What does `pwd` print?
> The absolute path of the working directory.

> [!question]- Which command lists a folder?
> `ls`

## Self-check quiz
> [!question]- Which flag makes `ls` show hidden files?
> `-a`

## Drills
- [ ] **(10 XP) Print the working directory.** Run it, read it, move one folder up.
""",
    "Class 02 - Fixture Containers": """
## Images and containers
An image is the recipe; a container is the meal.

## Ports
`-p 8080:80` maps a host port onto a container port.

## 🃏 Flashcards
> [!question]- What does `docker ps` show?
> The containers that are running right now.
""",
    "Class 03 - Fixture Git": """
## Commits
A commit is a snapshot plus a message saying why.

## Branches
A branch is a movable name pointing at one commit.
""",
}

# No `# H1` — an Obsidian note is titled by its filename, and an H1 would shift
# every section number by one.
for title, body in NOTES.items():
    with open(os.path.join(VAULT, title + ".md"), "w", encoding="utf-8") as f:
        f.write(body.lstrip("\n"))

if os.path.exists(CONFIG):
    os.replace(CONFIG, PARKED_CONFIG)
with open(CONFIG, "w", encoding="utf-8") as f:
    json.dump({"vault_dir": VAULT}, f)


def restore_config():
    try:
        os.remove(CONFIG)
    except OSError:
        pass
    if os.path.exists(PARKED_CONFIG):
        os.replace(PARKED_CONFIG, CONFIG)
    shutil.rmtree(VAULT, ignore_errors=True)


atexit.register(restore_config)


class Term:
    def __init__(self, cols=170, rows=45):
        # pty.fork(), not Popen: the child must be a session leader owning the
        # pty as its CONTROLLING terminal, or the kernel has no foreground
        # process group to send SIGWINCH to and a resize is silently dropped.
        self.pid, self.master = pty.fork()
        if self.pid == 0:                       # child
            os.environ.pop("COLUMNS", None)
            os.environ.pop("LINES", None)
            os.environ["TERM"] = "xterm-256color"
            os.chdir(os.path.dirname(GAME))
            os.execv(sys.executable, [sys.executable, GAME])
        self.size(cols, rows)
        self.buf = ""
        # One decoder for the whole stream: a read can land in the middle of a
        # multi-byte character, and decoding chunk-by-chunk turns the tail of
        # a box-drawing glyph into replacement characters that measure wrong.
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")

    def size(self, cols, rows):
        fcntl.ioctl(self.master, termios.TIOCSWINSZ,
                    struct.pack("HHHH", rows, cols, 0, 0))

    def read(self, timeout=0.6):
        end = time.time() + timeout
        out = ""
        while time.time() < end:
            if select.select([self.master], [], [], 0.05)[0]:
                try:
                    chunk = os.read(self.master, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                out += self.decoder.decode(chunk)
                end = time.time() + 0.25
        self.buf += out
        return ANSI.sub("", out)

    def send(self, data, wait=0.35):
        os.write(self.master, data.encode())
        time.sleep(wait)

    def close(self):
        try:
            os.kill(self.pid, 15)
            os.waitpid(self.pid, 0)
        except Exception:
            pass
        os.close(self.master)


FAIL = []


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else f"   {detail}"))
    if not cond:
        FAIL.append(name)


t = Term()
screen = t.read(1.5)
check("map drew with the banner", "DEVOPS EXPERTS QUEST" in screen or "█" in screen)
check("view starts at the banner",
      "DEVOPS EXPERTS QUEST  ✦" in screen and "▾   0%" in screen, screen[-300:])

# ---- paging keys, no Enter -------------------------------------------------
t.send("\x1b[6~")                                   # PgDn
screen = t.read()
check("PgDn scrolls down", "▾   0%" not in screen, screen[-200:])
check("PgDn leaves no literal [6~", "[6~" not in screen, screen[-200:])
check("banner scrolls away", "DEVOPS EXPERTS QUEST  ✦" not in screen, screen[:200])

t.send("\x1b[F")                                    # End
screen = t.read()
check("End jumps to the bottom", "100%" in screen, screen[-200:])
check("last mission is visible at the bottom", "30. Eyes on the Sky" in screen, screen[-400:])

t.send("\x1b[H")                                    # Home
screen = t.read()
check("Home comes back to the banner",
      "▾   0%" in screen and "DEVOPS EXPERTS QUEST  ✦" in screen, screen[-200:])

t.send("\x1b[B")                                    # down arrow
screen = t.read()
check("↓ scrolls a few lines", "▾   0%" not in screen, screen[-200:])
t.send("\x1b[A\x1b[A")                              # up arrow twice
screen = t.read()
check("↑ scrolls back up", "▾   0%" in screen, screen[-200:])
check("arrows leave no literal [A/[B", "[A" not in screen and "[B" not in screen, screen[-200:])

# ---- typing survives a page turn ------------------------------------------
t.send("2")
t.send("\x1b[6~")
screen = t.read()
check("half-typed text survives scrolling", screen.rstrip().endswith("2"), repr(screen[-80:]))
t.send("\x7f")                                      # backspace it away
t.read(0.2)

# ---- slash commands --------------------------------------------------------
t.send("/help\n")
screen = t.read()
check("/help shows the map commands", "MAP COMMANDS" in screen, screen[-200:])
t.send("\n")                                        # leave the pause
t.read(0.5)

t.send("/complete on\n")
screen = t.read()
check("/complete on turns Tab help on", "Tab-completion is ON" in screen, screen[-300:])
t.send("\n")
t.read(0.5)

t.send("/theme\n")
screen = t.read()
check("/theme opens the theme menu", "PROMPT THEMES" in screen, screen[-300:])
check("the theme menu asks for a pick", "pick a look" in screen and "›" in screen, screen[-200:])
t.send("kali\n")
screen = t.read()
check("a theme can be picked right there", "now: kali" in screen, screen[-300:])
t.send("classic\n")
t.read(0.8)
t.send("quit\n")
t.read(0.8)

t.send("/complete\n")
screen = t.read()
check("/complete asks on or off", "Tab-completion is" in screen and "on · off" in screen,
      screen[-200:])
t.send("on\n")
screen = t.read()
check("`on` toggles it in place", "Tab-completion is ON" in screen, screen[-300:])
t.send("off\n")
t.read(0.8)
t.send("quit\n")
t.read(0.8)

t.send("/catchup\n")
screen = t.read()
check("/catchup offers to start a mission", "CATCH-UP ROUTE" in screen
      and "plays it" in screen, screen[-300:])
t.send("1\n")
screen = t.read(1.2)
check("a route number starts that mission", "MISSION:" in screen, screen[-200:])
t.send("/quit\n")
t.read(1.0)
t.send("\n")
t.read(0.8)

# ---- the Codex browses, opens and comes back -------------------------------
t.send("/learn\n")
screen = t.read(1.2)
check("/learn lists the Codex", "THE CODEX" in screen, screen[-200:])
check("the Codex is numbered", "  1. " in screen or " 1." in screen, screen[-300:])
check("the Codex prompts for input", "open a note" in screen and "›" in screen, screen[-200:])
t.send("2\n")                                   # the fixture vault's second note
screen = t.read(1.5)
check("a number opens that note", "📖" in screen and "section number" in screen,
      screen[-200:])
check("...and it's the note the number pointed at", "Fixture Containers" in screen,
      screen[-300:])
t.send("\n")                                    # a stray Enter must NOT leave
screen = t.read(1.0)
check("Enter alone just gives a fresh prompt", "THE CODEX" not in screen, screen[-200:])
check("...and the prompt is still there", "›" in screen, repr(screen[-80:]))
t.send("1\n")                                   # a section by number
screen = t.read(1.2)
check("a section number opens that section", "recipe" in screen, screen[-300:])
t.send("\n")
t.read(0.8)
t.send("quit\n")
screen = t.read(1.2)
check("`quit` goes back to the Codex, not out of it", "THE CODEX" in screen, screen[-200:])
t.send("class 3\n")
screen = t.read(1.5)
check("`class 3` opens class 3", "Class 03" in screen, screen[-300:])
t.send("quit\n")
t.read(1.0)
t.send("quit\n")                                # `quit` = back to the map
t.read(1.0)

# ---- into a mission, with slash meta-commands ------------------------------
t.send("/1\n")
screen = t.read(1.0)
check("/1 starts mission 1", "MISSION: First Contact" in screen, screen[-200:])
t.send("/task\n")
screen = t.read()
check("/task inside a mission", "Objectives:" in screen, screen[-200:])
t.send("/hint\n")
screen = t.read()
check("/hint inside a mission", "–5 XP" in screen or "-5 XP" in screen, screen[-200:])
t.send("mkdir -p ~/linux_course/week1\n")
screen = t.read()
check("real shell commands still run", "OBJECTIVE" in screen or screen.strip() != "",
      screen[-200:])
t.send("/tmp\n")
screen = t.read()
check("/tmp is a path, not a command", "No such file" in screen or "Is a directory" in screen,
      screen[-200:])
t.send("/quit\n")
screen = t.read(1.0)
check("/quit leaves the mission", "left the mission" in screen, screen[-200:])
t.send("\n")                                        # past the pause
t.read(0.8)

# ---- resize reflows --------------------------------------------------------
t.send("\x1b[H")                                    # back to the top first
t.read(1.2)                                         # drain the old-width frame
t.size(110, 30)
screen = t.read(1.2)
check("resize repaints by itself", "MISSION MAP" in screen, repr(screen[-200:]))
lines = [line.rstrip("\r") for line in screen.split("\n") if line.strip()]
widest = max(lines, key=disp_width) if lines else ""
check("repaint fits the new width", disp_width(widest) <= 110,
      f"widest {disp_width(widest)}: " + repr(widest[:130]))

t.send("/quit\n")
screen = t.read(1.0)
check("/quit leaves the game", "See you" in screen, screen[-200:])
t.close()

print()
if FAIL:
    print(f"{len(FAIL)} terminal behaviour(s) broken:", ", ".join(FAIL))
    sys.exit(1)
print("PASS — keys, slash commands, scrolling and resize all behave. ✔")
