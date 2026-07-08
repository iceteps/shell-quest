"""Git missions — mirror Yariv's real 'Git Fundamentals Assignment:
Branching, Merging & Conflicts' step by step."""
from engine import _has_markers

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


MISSIONS = [
    {
        "id": "git-01",
        "topic": "git",
        "title": "The First Commit 🌱",
        "vault_note": "Class 03 - Git",
        "repo_name": "git-python-practice",
        "brief": ("You've just cloned your new (empty) repo 'git-python-practice' —\n"
                  "exactly like Part 2 of the real assignment. Create app.py with a\n"
                  "greet() function, then walk it through the full pipeline:\n"
                  "working dir → staging → local repo → GitHub."),
        "world": {"git": {"branch": "main"}, "files": {}},
        "objectives": [
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
        "solution": [
            "edit app.py",
            'def greet(name):', '    return f"Hello, {name}!"', "",
            'if __name__ == "__main__":', '    print(greet("World"))', ".",
            "git status",
            "git add app.py",
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
        "brief": ("Part 4 of the real assignment: main already has app.py committed.\n"
                  "Create a branch named feature/add-time, switch to it, make the greeting\n"
                  "include the current time (import datetime…), commit, and push the branch."),
        "world": {
            "files": {"app.py": BASE_APP},
            "git": {"branch": "main", "tracked": ["app.py"],
                    "commits": [{"branch": "main", "msg": "add greet app"}],
                    "branch_files": {"main": {"app.py": BASE_APP}}},
        },
        "objectives": [
            {"desc": "Create the branch feature/add-time AND switch to it", "xp": 15,
             "hint": "git branch feature/add-time then git switch it — or one shot: git checkout -b feature/add-time.",
             "check": lambda w: w.git and w.git["branch"] == "feature/add-time"},
            {"desc": "Change app.py so the greeting includes the current time", "xp": 20,
             "hint": "edit app.py — import datetime and put the time into the returned string.",
             "check": lambda w: "datetime" in w.files.get("app.py", "")
                                and (w.git and w.git["branch"] == "feature/add-time")},
            {"desc": "Commit the change ON the feature branch", "xp": 15,
             "hint": "add, then commit -m — check git status shows the feature branch first.",
             "check": lambda w: w.git and any(cm["branch"] == "feature/add-time" for cm in w.git["commits"])},
            {"desc": "Push the branch to GitHub", "xp": 15,
             "hint": "A brand-new branch needs: git push -u origin feature/add-time.",
             "check": lambda w: w.git and "feature/add-time" in w.git["pushed"]},
        ],
        "solution": [
            "git checkout -b feature/add-time",
            "edit app.py",
            "import datetime", "",
            "def greet(name):",
            '    now = datetime.datetime.now().strftime("%H:%M")',
            '    return f"Hello, {name}! The time is {now}"', ".",
            "git add app.py",
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
        "brief": ("Parts 7–9 of the real assignment. Both branches changed the SAME\n"
                  "greet() function differently: main reworded the greeting, and\n"
                  "feature/add-time added the time. You're on main. Merge the feature\n"
                  "branch, face the conflict, and resolve it so the final greet() keeps\n"
                  "BOTH ideas. Then read your history like the assignment asks."),
        "world": {
            "files": {"app.py": MAIN_GREET},
            "git": {"branch": "main", "branches": ["main", "feature/add-time"],
                    "tracked": ["app.py"],
                    "commits": [{"branch": "main", "msg": "add greet app"},
                                {"branch": "feature/add-time", "msg": "greeting includes current time"},
                                {"branch": "main", "msg": "reword greeting"}],
                    "branch_files": {"main": {"app.py": MAIN_GREET},
                                     "feature/add-time": {"app.py": FEAT_GREET}}},
        },
        "objectives": [
            {"desc": "Trigger the merge conflict (merge the feature branch into main)", "xp": 15,
             "hint": "You're already on main: git merge feature/add-time — and don't panic at the red text.",
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
            {"desc": "Read your history (the assignment's submission proof)", "xp": 10,
             "hint": "git log --oneline",
             "check": lambda w: w.flags.get("git_log")},
        ],
        "solution": [
            "git merge feature/add-time",
            "cat app.py",
            "edit app.py",
            "import datetime", "",
            "def greet(name):",
            '    now = datetime.datetime.now().strftime("%H:%M")',
            '    return f"Hello there, {name}!! The time is {now}"', ".",
            "git add app.py",
            'git commit -m "resolve merge conflict: keep new wording + time"',
            "git log --oneline",
        ],
    },
]
