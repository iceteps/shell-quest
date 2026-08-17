"""Git missions — mirror Yariv's real 'Git Fundamentals Assignment:
Branching, Merging & Conflicts' step by step."""
import shlex

from engine import _has_markers, _ignored, _resolve, _sha, c, do_git

BASE_APP = '''def greet(name):
    return f"Hello, {name}!"

if __name__ == "__main__":
    user = "World"
    print(greet(user))'''

MAIN_GREET = '''def greet(name):
    return f"Hello there, {name}!!"

if __name__ == "__main__":
    user = "World"
    print(greet(user))'''

FEAT_GREET = '''import datetime

def greet(name):
    now = datetime.datetime.now().strftime("%H:%M")
    return f"Hello, {name}! The time is {now}"

if __name__ == "__main__":
    user = "World"
    print(greet(user))'''

README = '''# git-python-practice

Practice repo for the Git assignment.

Run it with: python app.py'''

# A teammate's branch that touches a file main has never seen — which is what
# makes its merge the boring kind. Most merges in real life look like this.
TEST_APP = '''from app import greet

def test_greet():
    assert "World" in greet("World")'''

# ---- git-04: the state a bad afternoon left behind -------------------------
# app.py as the last PUSHED commit left it: a debug line that prints the token
# it reads out of .env. Both halves of the leak are already in origin's history,
# which is what makes `revert` — not `reset` — the only honest way out.
APP_DEBUG = '''import os

def greet(name):
    print(f"[DEBUG] token={os.environ.get('API_TOKEN')}")
    return f"Hello, {name}!"

if __name__ == "__main__":
    user = "World"
    print(greet(user))'''

ENV_FILE = '''API_TOKEN=ghp_exampleNOTAREALTOKEN0000000000
DB_PASSWORD=hunter2'''

DEBUG_LOG = '''2026-07-14 18:02:11 greet("World") -> Hello, World!
2026-07-14 18:02:11 [DEBUG] token=ghp_exampleNOTAREALTOKEN0000000000
2026-07-14 18:44:02 [DEBUG] token=ghp_exampleNOTAREALTOKEN0000000000'''

# The half-finished fix: what the DEBUG line should have been all along.
SANITIZE = '''def mask(token):
    """Never print a secret in full — four characters is plenty for a log."""
    return (token[:4] + "…") if token else "(unset)"'''

# Seeded shas. `git log --oneline` prints their first 7 characters, so the
# solution — which doubles as the demo — can name a commit exactly the way a
# student does: by copying it off the log.
LEAK_SHA = "7e5b0c29da41f836b9a2"
DEBUG_SHA = "b2c93a17e04d5f68c1ab"


def _clone_step(world, m, io):
    """`git clone` for a mission that starts BEFORE the clone.

    The engine refuses to clone a repo whose name matches the mission's own
    `repo_name` — "you are already standing inside it" — because every other git
    mission drops you inside the working copy. git-01 does not: cloning IS
    step 1 of the assignment. So the guard stands down for exactly one command
    and the engine still does the cloning, the progress lines and the PAT
    warning; nothing about clone is re-implemented here.
    """
    repo = world.flags.pop("repo_name", None)
    try:
        do_git(world, m.group(0).split()[1:], io)
    finally:
        if repo is not None:
            world.flags["repo_name"] = repo


def _branch_listing(world, m, io):
    """A plain `git branch` — recorded, because the objective is the LOOKING.

    The engine flags `git status` and `git log` when the player inspects with
    them; branch listing has no such flag. Seeing the `*` jump to the new branch
    is the whole point of the drill, and it leaves no state behind to check.
    """
    do_git(world, m.group(0).split()[1:], io)
    # Which branch it was run FROM is the lesson: the drill is passed when the
    # player watches the * sit on the branch they just switched to.
    world.flags["saw_branch_list"] = world.git["branch"] if world.git else True


def _diff_view(world, m, io):
    """`git diff` vs `git diff --staged`, recorded only when it showed something.

    Typing both spellings at an empty repo proves nothing — the lesson lands
    only when the two views answer DIFFERENTLY about the same moment. So the
    flag goes down for a view that actually had changes to print.
    """
    rest = m.group(0).split()[1:]
    g = world.git
    staged_view = any(a in ("--staged", "--cached") for a in rest)
    had = bool(g and (g["staged"] if staged_view else g["modified"]))
    do_git(world, rest, io)
    if had:
        world.flags["saw_diff_staged" if staged_view else "saw_diff_work"] = True


def _merge_once(world, m, io):
    """Merging a branch that is already in makes real git say 'Already up to date.'

    The engine would stack a second merge commit for the same branch — a history
    that could not exist — and the graph the player submits would show it. So the
    repeat is refused here, in git's wording, before it reaches the engine.
    """
    rest = m.group(0).split()[1:]           # rest[0] is the subcommand, "merge"
    g = world.git
    other = next((a for a in rest[1:] if not a.startswith("-")), None)
    if g and g["conflict"] and other:
        # Real git will not start a second merge on top of an unfinished one —
        # and letting it through here would re-snapshot the marker-filled file as
        # the "pre-merge" backup, quietly poisoning `git merge --abort`.
        world.flags["_noop"] = True
        io.print("error: Merging is not possible because you have unmerged files.")
        io.print("hint: Fix them up in the work tree, and then use 'git add/rm <file>'")
        io.print("hint: as appropriate to mark resolution and make a commit.")
        io.print("fatal: Exiting because of an unfinished merge.")
        io.print(c("(one merge at a time. Finish this one — resolve, add, commit — or walk away "
                   "from it with git merge --abort.)", "dim"))
        return
    if g and other in g["merged"] and not g["conflict"]:
        world.flags["_noop"] = True
        io.print("Already up to date.")
        io.print(c("(that branch's commits are already sitting in this one — merging again has "
                   "nothing left to bring. `git branch -d` is what comes next.)", "dim"))
        return
    do_git(world, rest, io)


def _head(g):
    """The sha at the tip — the thing an `--amend` quietly replaces."""
    return _sha(g["commits"][-1]) if g["commits"] else None


def _revert_pushed(w):
    """Is a revert actually ON origin? `pushed_at` is a COUNT, so comparing it
    with len(commits) proves only that the numbers agree — and `git reset --hard
    HEAD~1` makes them agree again with the revert deleted and the commit it
    undid back at the tip. Look for the commit itself, inside the pushed range."""
    g = w.git
    return any(cm["msg"].startswith('Revert "')
               for cm in g["commits"][:g["pushed_at"].get("main", 0)])


def _undo_watch(world, m, io):
    """`git restore` / `commit --amend` / `reset` — run by the engine, watched here.

    Two things this mission needs that a plain dispatch can't give it:

    1. An undo that WORKED leaves a world that looks like the mistake never
       happened, so there is no state left for a `check()` to find. These flags
       remember it — and only when git actually moved something, because typing
       `--amend` at a commit git refused teaches nothing and must not score.
    2. The engine warns that amend/reset rewrite PUBLISHED history whenever the
       branch has ever been pushed. Every commit rewritten in this mission was
       made minutes ago and has never left the machine — real git takes those
       without a murmur, and the warning (plus the rejected push behind it)
       would be a lie. So the branch steps out of `pushed` for exactly one
       command, the way `_clone_step` lifts the clone guard, and only when
       everything being rewritten is still local.
    """
    rest = shlex.split(m.group(0))[1:]          # rest[0] is the subcommand
    sub, g = rest[0], world.git
    staged_before, depth, head = set(g["staged"]), len(g["commits"]), _head(g)

    first = None                                # oldest commit index this rewrites
    if sub == "commit" and "--amend" in rest:
        first = depth - 1
    elif sub == "reset":
        found = _resolve(g, next((a for a in rest[1:] if not a.startswith("-")), "HEAD"))
        first = found[0] + 1 if found else None
    local = first is not None and first >= g["pushed_at"].get(g["branch"], depth)
    lift = local and g["branch"] in g["pushed"]
    if lift:
        g["pushed"].discard(g["branch"])
    try:
        do_git(world, rest, io)
    finally:
        if lift:
            g["pushed"].add(g["branch"])

    # `git restore --staged <f>` and its older spelling `git reset <f>` both
    # count: the objective is the file leaving the index, not one route to it.
    if sub in ("restore", "reset") and "--hard" not in rest and staged_before - g["staged"]:
        world.flags["unstaged"] = True
    if sub == "commit" and "--amend" in rest and _head(g) != head:
        world.flags["amended"] = True
    # …and only a LOCAL one counts: erasing commits origin already has is the
    # mistake the objective teaches you to spot, not the drill it asks for.
    if sub == "reset" and "--hard" in rest and local and len(g["commits"]) < depth:
        world.flags["reset_hard"] = True


def _cfg(w):
    return w.flags.get("gitconfig", {})


MISSIONS = [
    {
        "id": "git-01",
        "topic": "git",
        "title": "The First Commit 🌱",
        "vault_note": "Class 03 - Git",
        "repo_name": "git-python-practice",
        "brief": ("Parts 1–3 of the real assignment. The repo 'git-python-practice'\n"
                  "exists on GitHub and is empty — nothing is on your disk yet.\n"
                  "Teach this machine who you are, clone the repo, then walk one file\n"
                  "the whole way down the pipeline: working dir → staging → local\n"
                  "repo → GitHub.\n"
                  "  url: https://github.com/you/git-python-practice.git"),
        "world": {"git": {"branch": "main"}, "files": {}},
        "handlers": [(r"git\s+clone(\s+.*)?", _clone_step)],
        "objectives": [
            {"desc": "Tell git who you are: user.name AND user.email", "xp": 10,
             "hint": 'git config --global user.name "Your Name"  — then the same for user.email.',
             "check": lambda w: bool(_cfg(w).get("user.name")) and "@" in _cfg(w).get("user.email", "")},
            {"desc": "Clone git-python-practice from GitHub", "xp": 10,
             "hint": "git clone https://github.com/you/git-python-practice.git  (the URL lives behind the green Code button).",
             "check": lambda w: w.flags.get("cloned") == "git-python-practice"},
            {"desc": "Create app.py containing a greet() function", "xp": 15,
             "hint": "edit app.py — write a tiny `def greet(name): return f\"Hello, {name}!\"` program.",
             "check": lambda w: "def greet" in w.files.get("app.py", "")},
            {"desc": "Check the repo status (make it a reflex!)", "xp": 5,
             "hint": "The command you should run before AND after everything.",
             "check": lambda w: w.flags.get("git_status")},
            {"desc": "Stage app.py", "xp": 10,
             "hint": "Staging = choosing what goes in the next snapshot: git add <file>.",
             "check": lambda w: w.git and ("app.py" in w.git["staged"] or "app.py" in w.git["tracked"])},
            {"desc": "Commit with a clear message", "xp": 15,
             "hint": "git commit -m \"a message a teammate would understand\"",
             "check": lambda w: w.git and len(w.git["commits"]) >= 1},
            {"desc": "Push main to GitHub (first push needs upstream!)", "xp": 15,
             "hint": "First-ever push of a branch: git push -u origin main.",
             "check": lambda w: w.git and "main" in w.git["pushed"]},
        ],
        "teach": [
            "Identity is a MACHINE setting, not a repo one — --global writes ~/.gitconfig once "
            "and every commit you ever make carries it.",
            "`clone` copies the FULL history and names that URL 'origin' for you — which is why "
            "a bare `git push` knows where to go later.",
            "New files start UNTRACKED — git ignores them until you say otherwise.",
            "`git status` before AND after everything — it's your instrument panel.",
            "`add` stages: you choose exactly what goes into the next snapshot.",
            "A commit is a permanent snapshot plus a message future-you will actually read.",
            "First push of a branch needs -u to wire local↔remote; afterwards plain `git push` works.",
        ],
        "solution": [
            'git config --global user.name "Ada Lovelace"',
            'git config --global user.email "ada@example.com"',
            "git clone https://github.com/you/git-python-practice.git",
            "edit app.py",
            'def greet(name):', '    return f"Hello, {name}!"', "",
            'if __name__ == "__main__":', '    print(greet("World"))', ".",
            "git status",
            "git add app.py",
            "git status",
            'git commit -m "add greet app"',
            "git push -u origin main",
        ],
    },
    {
        "id": "git-02",
        "topic": "git",
        "title": "Branch Out 🌿 — feature/add-time",
        "vault_note": "Class 03 - Git",
        "repo_name": "git-python-practice",
        "brief": ("Part 4 of the real assignment: main already has app.py and a README.\n"
                  "Create a branch named feature/add-time, switch to it, and make the\n"
                  "greeting include the current time (import datetime…).\n"
                  "On the way you'll run the class's staging drill: change BOTH files,\n"
                  "stage only one, and prove that `git diff` and `git diff --staged`\n"
                  "answer two different questions about the same moment."),
        "world": {
            "files": {"app.py": BASE_APP, "README.md": README},
            "git": {"branch": "main", "tracked": ["app.py", "README.md"],
                    "commits": [{"branch": "main", "msg": "add greet app"},
                                {"branch": "main", "msg": "add README"}],
                    "pushed": ["main"],
                    "branch_files": {"main": {"app.py": BASE_APP, "README.md": README}}},
        },
        "handlers": [(r"git\s+branch(\s+-[a-zA-Z-]+)*\s*", _branch_listing),
                     (r"git\s+diff(\s+.*)?", _diff_view)],
        "objectives": [
            {"desc": "Create the branch feature/add-time AND switch to it", "xp": 15,
             "hint": "git branch feature/add-time then git switch it — or one shot: git checkout -b feature/add-time.",
             "check": lambda w: w.git and w.git["branch"] == "feature/add-time"},
            {"desc": "Confirm the * has moved: list your branches", "xp": 5,
             "hint": "git branch — no arguments, run it while you're ON feature/add-time.",
             "check": lambda w: w.flags.get("saw_branch_list") == "feature/add-time"},
            {"desc": "Change app.py so the greeting includes the current time", "xp": 20,
             "hint": "edit app.py — import datetime and put the time into the returned string.",
             "check": lambda w: "datetime" in w.files.get("app.py", "")
                                and (w.git and w.git["branch"] == "feature/add-time")},
            {"desc": "Surgical staging: edit README.md too, then stage ONLY app.py", "xp": 15,
             "hint": "edit README.md, then git add app.py alone — git status should show one file green and one red.",
             "check": lambda w: w.git and "app.py" in w.git["staged"] and "README.md" in w.git["modified"]},
            {"desc": "See the difference: git diff, then git diff --staged", "xp": 10,
             "hint": "Plain diff shows what is NOT staged (README.md); --staged shows what IS (app.py).",
             "check": lambda w: w.flags.get("saw_diff_work") and w.flags.get("saw_diff_staged")},
            {"desc": "Commit the change ON the feature branch", "xp": 15,
             "hint": "You already staged app.py — commit -m now, and README.md stays behind on purpose.",
             "check": lambda w: w.git and any(cm["branch"] == "feature/add-time" for cm in w.git["commits"])},
            {"desc": "Push the branch to GitHub", "xp": 15,
             "hint": "A brand-new branch needs: git push -u origin feature/add-time.",
             "check": lambda w: w.git and "feature/add-time" in w.git["pushed"]},
        ],
        "teach": [
            "checkout -b creates AND switches in one move — branches are free, spend them on every feature.",
            "`git branch` with no arguments only LISTS — the * is git telling you which branch your "
            "next commit will land on.",
            "Edits ride on whichever branch is checked out — the branch you're ON owns the change.",
            "The index lets you commit HALF your work: stage what belongs in this snapshot and leave "
            "the rest sitting modified in the working tree.",
            "`git diff` = working tree vs index · `git diff --staged` = index vs last commit. Empty "
            "after an `add` doesn't mean 'no changes' — it means 'nothing left unstaged'.",
            "Commits on a feature branch leave main untouched — that isolation is the whole point.",
            "Pushing a branch shares work WITHOUT merging it — review happens before merge, not after.",
        ],
        "solution": [
            "git status",
            "git checkout -b feature/add-time",
            "git branch",
            "edit app.py",
            "import datetime", "",
            "def greet(name):",
            '    now = datetime.datetime.now().strftime("%H:%M")',
            '    return f"Hello, {name}! The time is {now}"', "",
            'if __name__ == "__main__":',
            '    user = "World"', "    print(greet(user))", ".",
            "edit README.md",
            "# git-python-practice", "",
            "Practice repo for the Git assignment.", "",
            "Run it with: python app.py",
            "The greeting now includes the current time.", ".",
            "git diff",
            "git add app.py",
            "git status",
            "git diff",
            "git diff --staged",
            'git commit -m "greeting includes current time"',
            "git push -u origin feature/add-time",
        ],
    },
    {
        "id": "git-03",
        "topic": "git",
        "title": "The Conflict 💥 — final boss of the Git assignment",
        "vault_note": "Class 03 - Git",
        "repo_name": "git-python-practice",
        "brief": ("Parts 5 and 7–9 of the real assignment, in that order.\n"
                  "First the merge that just works: a teammate's branch feature/tests\n"
                  "only adds a test file, so bringing it into main is uneventful —\n"
                  "merge it, push, and delete the branch you no longer need.\n"
                  "Then the one that doesn't: main reworded greet() while\n"
                  "feature/add-time added the time — the SAME lines, two ways.\n"
                  "Merge it, face the conflict, resolve it keeping BOTH ideas, and\n"
                  "submit the graph the assignment asks for."),
        "world": {
            "files": {"app.py": MAIN_GREET},
            "git": {"branch": "main",
                    "branches": ["main", "feature/add-time", "feature/tests"],
                    "tracked": ["app.py"],
                    "pushed": ["main", "feature/add-time"],
                    "commits": [{"branch": "main", "msg": "add greet app"},
                                {"branch": "feature/add-time", "msg": "greeting includes current time"},
                                {"branch": "feature/tests", "msg": "add a test for greet()"},
                                {"branch": "main", "msg": "reword greeting"}],
                    "branch_files": {"main": {"app.py": MAIN_GREET},
                                     "feature/add-time": {"app.py": FEAT_GREET},
                                     "feature/tests": {"test_app.py": TEST_APP}}},
        },
        "handlers": [(r"git\s+merge(\s+.*)?", _merge_once)],
        "objectives": [
            {"desc": "Merge feature/tests into main — the merge that just works", "xp": 10,
             "hint": "You're already on main: git merge feature/tests. It touches no file you touched.",
             "check": lambda w: w.git and "feature/tests" in w.git["merged"]},
            {"desc": "Publish the merged main", "xp": 10,
             "hint": "main already has an upstream, so a bare git push is enough.",
             "check": lambda w: w.git and "feature/tests" in w.git["merged"]
                                and w.git["pushed_at"].get("main") == len(w.git["commits"])},
            {"desc": "Clean up: delete the branch you just merged", "xp": 5,
             "hint": "git branch -d feature/tests — lowercase -d, the safe one.",
             "check": lambda w: w.git and "feature/tests" in w.git["merged"]
                                and "feature/tests" not in w.git["branches"]},
            {"desc": "Trigger the merge conflict (merge the feature branch into main)", "xp": 15,
             "hint": "git merge feature/add-time — and don't panic at the red text.",
             "check": lambda w: w.flags.get("conflict_seen")},
            {"desc": "Inspect the conflicted file and understand the markers", "xp": 10,
             "hint": "cat app.py — everything between <<<<<<< HEAD and ======= is YOUR side; below it is THEIRS.",
             "check": lambda w: w.flags.get("conflict_seen") and "app.py" in w.files},
            {"desc": "Resolve: rewrite app.py combining BOTH changes, no markers left", "xp": 30,
             "hint": "edit app.py — keep the new wording AND the datetime logic. Delete every <<<<<<< ======= >>>>>>> line.",
             "check": lambda w: w.flags.get("conflict_seen")
                                and not _has_markers(w.files.get("app.py", ""))
                                and "datetime" in w.files.get("app.py", "")},
            {"desc": "Stage the resolved file and commit the merge", "xp": 20,
             "hint": "git add app.py, then git commit -m \"resolve merge conflict\".",
             "check": lambda w: w.git and "feature/add-time" in w.git["merged"]},
            {"desc": "Draw your history: git log --oneline --graph (the submission artifact)", "xp": 10,
             "hint": "git log --oneline --graph — the exact command the assignment asks you to hand in.",
             "check": lambda w: w.flags.get("git_graph")},
        ],
        "teach": [
            "Most merges are boring: two branches that touched different files merge with no "
            "argument at all. The conflict is the exception, not the rule.",
            "A merge commit is still just a commit — main is merged for the TEAM only once you push it.",
            "`git branch -d` deletes only what is already merged; it refuses to drop work nobody kept. "
            "That refusal is a feature (`-D` is the override, and it's on you).",
            "A conflict isn't an error — it's git asking a HUMAN to choose between two truths.",
            "<<<<<<< HEAD is YOUR side, >>>>>>> is THEIRS, ======= divides them — read both before deciding.",
            "Resolving = writing the final truth and deleting every marker; keep BOTH ideas when both matter.",
            "`add` marks it resolved; the commit seals a merge with two parents.",
            "`git log --oneline --graph` is the proof you submit — the diamond IS the story of two "
            "branches meeting.",
        ],
        "solution": [
            "git status",
            "git branch",
            "git merge feature/tests",
            "cat test_app.py",
            "git push",
            "git branch -d feature/tests",
            "git merge feature/add-time",
            "cat app.py",
            "edit app.py",
            "import datetime", "",
            "def greet(name):",
            '    now = datetime.datetime.now().strftime("%H:%M")',
            '    return f"Hello there, {name}!! The time is {now}"', "",
            'if __name__ == "__main__":',
            '    user = "World"', "    print(greet(user))", ".",
            "git add app.py",
            'git commit -m "resolve merge conflict: keep new wording + time"',
            "git log --oneline --graph",
        ],
    },
    {
        "id": "git-04",
        "topic": "git",
        "title": "Damage Control 🚑 — the bonus section",
        "vault_note": "Class 03 - Git",
        "repo_name": "git-python-practice",
        "brief": ("The assignment's BONUS section — eleven tasks, and almost all of\n"
                  "them are undo. You come back to git-python-practice after a bad\n"
                  "afternoon: a DEBUG line that prints the API token is already on\n"
                  "origin, the .env it reads is committed right next to it, junk is\n"
                  "lying around the working tree, and the release notes were never\n"
                  "finished.\n"
                  "Six different undos fix that — restore · amend · rm --cached ·\n"
                  "stash · revert · reset --hard — and the only thing separating them\n"
                  "is what they COST. Published history gets reverted. Local history\n"
                  "gets erased. Learn which is which here, not on your team's main."),
        "world": {
            "files": {"app.py": APP_DEBUG, "README.md": README, ".env": ENV_FILE,
                      "sanitize.py": SANITIZE, "debug.log": DEBUG_LOG},
            "git": {"branch": "main", "branches": ["main"],
                    "tracked": ["app.py", "README.md", ".env"],
                    "untracked": ["sanitize.py", "debug.log"],
                    "pushed": ["main"],
                    # Seeded with `files`/`prev`, so `show` prints a real patch and
                    # `revert` has something to subtract — a commit without them is
                    # a message and nothing else.
                    "commits": [
                        {"branch": "main", "msg": "add greet app", "sha": "4c1a70bd93e2f5a8016d",
                         "files": {"app.py": BASE_APP}, "prev": {"app.py": None}},
                        {"branch": "main", "msg": "add README", "sha": "e0d4318fa27c6b95d3f1",
                         "files": {"README.md": README}, "prev": {"README.md": None}},
                        {"branch": "main", "msg": "load settings from .env", "sha": LEAK_SHA,
                         "files": {".env": ENV_FILE}, "prev": {".env": None}},
                        {"branch": "main", "msg": "TEMP: print the token while debugging",
                         "sha": DEBUG_SHA,
                         "files": {"app.py": APP_DEBUG}, "prev": {"app.py": BASE_APP}},
                    ]},
        },
        "handlers": [(r"git\s+(restore|reset|commit)(\s+.*)?", _undo_watch)],
        "objectives": [
            {"desc": "Read the crime scene: git log --oneline, then git show the .env commit", "xp": 10,
             "hint": f"git log --oneline lists the shas; git show {LEAK_SHA[:7]} opens that one "
                     "commit — message AND patch. `git show HEAD~1` names it too.",
             # The objective names ONE commit, so the check has to name it too:
             # `git show` with no argument opens HEAD, which is the debug commit
             # — a different crime scene entirely. HEAD~1 resolves to this sha.
             "check": lambda w: (w.flags.get("git_log")
                                 and LEAK_SHA in w.flags.get("git_shown", set()))},
            {"desc": "git add . sweeps up debug.log too — take it back out, keep sanitize.py in", "xp": 15,
             "hint": "git add . then git restore --staged debug.log. The file stays on disk; it "
                     "just leaves the index.",
             "check": lambda w: w.flags.get("unstaged") and "sanitize.py" in w.git["staged"]
                                and "debug.log" not in w.git["staged"]},
            {"desc": "Commit sanitize.py — then fix that message with --amend, before it's pushed", "xp": 15,
             "hint": 'git commit -m "…" first. Then git commit --amend -m "the message you '
                     'meant" — nothing is on origin yet, so the rewrite is free.',
             "check": lambda w: w.flags.get("amended")},
            {"desc": "Stop the leak: ignore .env and *.log, and untrack the .env git is carrying", "xp": 20,
             "hint": "edit .gitignore — one pattern per line: .env and *.log. Then git rm --cached "
                     ".env to drop the copy git already tracks (the file itself stays), and commit.",
             "check": lambda w: _ignored(w, ".env") and _ignored(w, "debug.log")
                                and ".env" not in w.git["tracked"] and ".env" in w.files},
            {"desc": "Urgent interrupt: park the half-written release notes, get a clean tree", "xp": 10,
             "hint": "edit README.md, then git stash. git status should say 'working tree clean' "
                     "and git stash list shows where the work went.",
             "check": lambda w: w.flags.get("git_stash") and not w.git["modified"]
                                and not w.git["staged"]},
            {"desc": "Bring the parked work back, and leave the stash empty", "xp": 10,
             "hint": "git stash pop — it replays the change AND drops the entry (git stash apply "
                     "would keep it). git stash list to prove the pile is empty.",
             "check": lambda w: w.flags.get("git_stash") and not w.git["stash"]
                                and "README.md" in w.git["modified"]},
            {"desc": "Undo the PUSHED debug commit the safe way: revert it, then push the undo", "xp": 20,
             "hint": f"git revert {DEBUG_SHA[:7]} makes a NEW commit that subtracts that one, then "
                     "git push. Revert wants a clean tree — commit what you're holding first.",
             # Not "the counts match" — a `reset --hard` restores that equality
             # with the revert gone and the debug commit back at the tip. The
             # only thing worth proving is that a Revert commit is ON origin.
             "check": _revert_pushed},
            {"desc": "Undo an UNPUSHED commit the cheap way: commit an experiment, then erase it", "xp": 15,
             "hint": "Make the experiment a real commit first, then git reset --hard HEAD~1. Don't "
                     "push it — never having pushed is exactly what makes this safe.",
             "check": lambda w: w.flags.get("reset_hard")},
            {"desc": "Name the recovered release: tag v1.0.0 and publish the tag", "xp": 10,
             "hint": 'git tag -a v1.0.0 -m "…" then git push origin v1.0.0 — a plain git push '
                     "never carries tags.",
             "check": lambda w: "v1.0.0" in w.git["pushed_tags"]},
        ],
        "teach": [
            "`git show <sha>` opens ONE commit: its message and its patch. Read before you undo — "
            "which commit did the damage is what decides which undo you need.",
            "`git add .` is a broom, not a scalpel: it sweeps up whatever is lying around. "
            "`git restore --staged <file>` takes one back out of the index and never touches the "
            "file on disk — un-staging is not undoing.",
            "`--amend` REPLACES the last commit instead of adding one: same work, new sha. Free "
            "while the commit is still local, a history rewrite the second it isn't.",
            ".gitignore only stops files git isn't tracking YET — one it already tracks needs "
            "`git rm --cached`. And neither erases the old commits: a pushed secret has to be "
            "ROTATED, not just ignored.",
            "stash parks uncommitted work and hands you a clean tree — the answer to 'I'm "
            "mid-change and something urgent just landed'. It is not history: nothing is "
            "committed, and nobody else can see it.",
            "`pop` replays the entry and removes it; `apply` leaves it on the pile. Either way "
            "everything comes back UNSTAGED — the staged/unstaged split is not preserved.",
            "revert ADDS a commit that undoes an old one, so both stay in the history. That is why "
            "it is the only safe undo for anything already pushed — and it publishes like any "
            "other commit, no force, no argument with your teammates.",
            "reset ERASES commits, and --hard throws the working files away with them. Safe "
            "exactly as long as those commits are yours alone. (`git reflog` can still find a "
            "dropped commit for a while — the uncommitted edits it held were never anywhere.)",
            "A branch pointer moves with every commit; a tag never moves. That is what makes "
            "v1.0.0 mean one exact commit forever — and tags stay local until you push them by "
            "name.",
        ],
        "solution": [
            "git status",
            "git log --oneline",
            f"git show {LEAK_SHA[:7]}",
            "git show HEAD",
            "git add .",
            "git status",
            "git restore --staged debug.log",
            "git status",
            'git commit -m "wip"',
            "git log --oneline",
            'git commit --amend -m "mask tokens before they reach a log"',
            "git log --oneline",
            "edit .gitignore",
            "# secrets and junk have no business in a repo",
            ".env",
            "*.log",
            ".",
            "git rm --cached .env",
            "git status",
            "git add .gitignore",
            'git commit -m "ignore .env and logs, and stop tracking the committed .env"',
            "edit README.md",
            "# git-python-practice", "",
            "Practice repo for the Git assignment.", "",
            "Run it with: python app.py", "",
            "## 1.0.0", "",
            "- mask tokens before they reach a log",
            ".",
            "git status",
            "git stash",
            "git status",
            "git stash list",
            "git stash pop",
            "git stash list",
            "git add README.md",
            'git commit -m "release notes for 1.0.0"',
            "git log --oneline",
            f"git revert {DEBUG_SHA[:7]}",
            "cat app.py",
            "git push",
            "edit experiment.py",
            "def greet(name):",
            '    return f"🌈✨ H E L L O  {name.upper()} ✨🌈"',
            ".",
            "git add experiment.py",
            'git commit -m "rainbow greeting, why not"',
            "git log --oneline",
            "git reset --hard HEAD~1",
            "git status",
            "ls",
            'git tag -a v1.0.0 -m "first release with no token in it"',
            "git tag",
            "git push origin v1.0.0",
            "git log --oneline --graph",
        ],
    },
]
