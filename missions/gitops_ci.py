"""GitOps / CI-CD missions — the class-8 loop, plus the half of it people skip.

Mission 1 is the loop itself: review the pipeline before you trust it, push, and
watch the robot build, tag by SHORT COMMIT SHA and commit the bump while ArgoCD
deploys it. Mission 2 is drift and rollback — a cluster that heals itself, and
an undo that is a `git revert`, not a `kubectl`.

Two things the note is emphatic about shape this module:

* **The tag is the real short SHA of the commit the player just pushed.** The
  engine's commits already carry honest shas, so CI reads `_sha(HEAD)[:7]` the
  way GitHub Actions reads `${GITHUB_SHA::7}`. Hard-coding "v2" would have
  taught the exact habit the class exists to break — you cannot trace `:latest`
  back to a commit, and this mission's win condition IS tracing it back.
* **The bump is `sed`, not `yq`.** `pip install yq` installs a different tool
  with incompatible syntax (the note's gotcha), so the class file sidesteps it.
  That means the drills need a way to change ONE line of a file, which is why
  this module ships small `sed` and `grep` handlers: the generic `edit` retypes
  the whole file, and typing the one-liner IS drill 4.

`argocd` is mission-local (only these two missions and the campaign use it);
git, kubectl and the host shell come from the engine.
"""
import re
import shlex

from engine import (TOOL_VERSION_LINES, _mark_edited, _now_stamp, _reconcile, _sha,
                    _stable_id, c, do_git, do_host, do_kubectl)

CI_PATH = ".github/workflows/ci.yaml"

# A DockerHub personal access token, in DockerHub's real `dckr_pat_` shape —
# because GitHub's push protection recognises tokens BY that shape, and the
# rejection it triggers is the whole lesson of drill 3.
LEAKED_TOKEN = "dckr_pat_8Kq3vN2sT9wLpXdR4hJ"

APP_PY = '''from flask import Flask
app = Flask(__name__)


@app.route("/")
def home():
    return "SkyWatch v1"
'''

# The class workflow, shipped with the two sins the note names: a credential in
# plain YAML and a mutable tag. Everything else is right, so the fix is one sed
# each — the file teaches by being ALMOST correct.
CI_YAML = '''name: CI
on:
  push:
    branches: [main]

jobs:
  ci:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Set IMAGE TAG
        id: vars
        run: echo "SHA_TAG=${GITHUB_SHA::7}" >> $GITHUB_OUTPUT

      - name: Login to Docker Hub
        uses: docker/login-action@v3
        with:
          username: ${{ secrets.DOCKERHUB_USERNAME }}
          password: ''' + LEAKED_TOKEN + '''

      - name: Build and push image
        uses: docker/build-push-action@v5
        with:
          push: true
          tags: ghcr.io/you/skywatch:latest

      - name: Bump the tag in the GitOps values
        run: |
          sed -i "s/tag: .*/tag: ${{ steps.vars.outputs.SHA_TAG }}/" values.yaml
          git commit -am "ci: bump image tag [skip ci]"
          git push
'''

VALUES_YAML = '''# Desired state — ArgoCD watches THIS file, not your laptop
image:
  repository: ghcr.io/you/skywatch
  tag: v1
replicas: 2
'''

# --- mission 2: the GitOps repo, one bump commit deep -----------------------
SHA_OLD, SHA_NEW = "9c1d4a2", "4f2a9c1"

GITOPS_VALUES = '''# charts/skywatch/values.yaml — the only place the tag is allowed to change
image:
  repository: ghcr.io/you/skywatch
  tag: {tag}
replicas: 2
'''

DEV_YAML = '''apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: skywatch
  namespace: argocd
spec:
  project: default                 # REQUIRED in ArgoCD 2.x
  destination:                     # WHERE to deploy
    namespace: default
    server: https://kubernetes.default.svc
  source:                          # WHAT to deploy (the desired state)
    repoURL: https://github.com/you/skywatch-gitops.git
    targetRevision: main
    path: charts/skywatch
    helm:
      valueFiles:
        - values.yaml
  syncPolicy:                      # WHO presses deploy (nobody)
    automated:
      prune: true                  # gone from Git -> gone from the cluster
      selfHeal: true               # hand-edited the cluster? undone.
      allowEmpty: false            # refuse to sync to an empty desired state
    syncOptions:
      - CreateNamespace=true
'''


# ------------------------------------------------------------ file surgery --
def _cat(world, m, io):
    """`cat`, plus a note of WHICH files were actually read end to end.

    Reading the pipeline before you trust it is an objective, not a formality —
    but a `cat … | grep …` is a search, not a read, so only the unpiped form
    counts.
    """
    args = shlex.split(m.group(0))[1:]
    if "|" not in args:
        for name in [a for a in args if not a.startswith("-")]:
            if name in world.files:
                world.flags.setdefault("read", set()).add(name)
    do_host(world, "cat", args, io)
    world.flags["_noop"] = True          # reading changes nothing


def _grep(world, m, io):
    """`grep [-n] [-i] [-v] PATTERN FILE…` — enough grep to audit a YAML file.

    Silence on no match is grep's real answer, so it stays silent; the dim line
    after it is there because a player who has never seen that is sure it broke.
    """
    args = shlex.split(m.group(0))[1:]
    opts = {ch for a in args if a.startswith("-") and not a.startswith("--") for ch in a[1:]}
    rest = [a for a in args if not a.startswith("-")]
    unknown = sorted(opts - set("niv"))
    if unknown:
        world.flags["_noop"] = True
        io.print(f"grep: invalid option -- '{unknown[0]}'")
        io.print(c("(this world's grep knows -n line numbers, -i ignore case and -v invert. "
                   "The real one has a hundred more — `man grep` on your own machine.)", "dim"))
        return
    if len(rest) < 2:
        world.flags["_noop"] = True
        io.print("usage: grep [-n] [-i] [-v] PATTERN FILE...")
        io.print(c("(this world's grep reads files, not stdin — name the file. "
                   "`cat f | grep x` works too.)", "dim"))
        return
    pat, names = rest[0], rest[1:]
    hits = 0
    for name in names:
        if name not in world.files:
            io.print(f"grep: {name}: No such file or directory")
            continue
        for n, ln in enumerate(world.files[name].split("\n"), 1):
            found = (pat.lower() in ln.lower()) if "i" in opts else (pat in ln)
            if found == ("v" in opts):
                continue
            hits += 1
            prefix = f"{name}:" if len(names) > 1 else ""
            io.print(prefix + (f"{n}:" if "n" in opts else "") + ln)
    if not hits:
        io.print(c("(no match — grep prints nothing when it finds nothing, and that IS "
                   "the answer. Drill 3 is done when grepping for the token comes back "
                   "empty.)", "dim"))
    world.flags["_noop"] = True


def _expand(rep, mo):
    """sed's replacement syntax, the two pieces students actually use: `&` is the
    whole match and `\\1` a group. Everything else stays literal — which is why
    this is hand-rolled instead of `re.sub`'s expansion, whose escapes would eat
    the `${{ secrets.* }}` a player is trying to write into a workflow."""
    out, i = [], 0
    while i < len(rep):
        ch = rep[i]
        if ch == "\\" and i + 1 < len(rep):
            nxt = rep[i + 1]
            if nxt.isdigit():
                try:
                    out.append(mo.group(int(nxt)) or "")
                except (IndexError, re.error):
                    out.append("")
            else:
                out.append(nxt)
            i += 2
        elif ch == "&":
            out.append(mo.group(0))
            i += 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


_BRE_SWAP = "+?{}|()"


def _bre(pat):
    """Translate a POSIX *basic* regular expression — what `sed` gets without
    `-E` — into the extended syntax Python's `re` speaks.

    In a BRE the characters `+ ? { } | ( )` are ORDINARY and `\\+ \\?` … are the
    operators, which is the exact reverse of what everyone assumes. A student
    writing s/INSTALL_K3S_VERSION=v1.29.5+k3s1/…/ means the literal plus, and
    real sed agrees with them; without this they would get a silent no-match and
    blame the game.
    """
    out, i = [], 0
    while i < len(pat):
        ch = pat[i]
        if ch == "\\" and i + 1 < len(pat):
            nxt = pat[i + 1]
            out.append(nxt if nxt in _BRE_SWAP else "\\" + nxt)
            i += 2
        else:
            out.append("\\" + ch if ch in _BRE_SWAP else ch)
            i += 1
    return "".join(out)


def _parse_s(script):
    """`s/PAT/REP/flags` with any delimiter -> (pat, rep, global). None if it
    isn't a substitution at all."""
    if len(script) < 4 or script[0] != "s":
        return None
    delim = script[1]
    if delim.isalnum() or delim in "\\ ":
        return None
    parts, cur, i = [], "", 2
    while i < len(script):
        if script[i] == "\\" and i + 1 < len(script) and script[i + 1] == delim:
            cur += delim
            i += 2
        elif script[i] == delim:
            parts.append(cur)
            cur, i = "", i + 1
        else:
            cur += script[i]
            i += 1
    parts.append(cur)
    if len(parts) < 2:
        return None
    return parts[0], parts[1], "g" in (parts[2] if len(parts) > 2 else "")


def _sed(world, m, io):
    """`sed [-i] 's/PAT/REP/[g]' FILE…` — the one-line edit the class file uses.

    Without `-i` it prints the result and touches nothing, which is exactly how
    you *test* a substitution before letting it rewrite a file. The dim line on
    a multi-line hit is the note's "sed is greedy" gotcha, delivered the moment
    it happens instead of a page later.
    """
    args = shlex.split(m.group(0))[1:]
    inplace, extended, rest, skip = False, False, [], False
    for a in args:
        if skip:
            rest.append(a)
            skip = False
        elif a in ("-i", "--in-place") or (a.startswith("-i") and not a.startswith("--")):
            inplace = True
        elif a in ("-E", "-r", "--regexp-extended"):
            extended = True
        elif a in ("-e", "--expression"):
            skip = True
        elif a.startswith("-") and a != "-":
            world.flags["_noop"] = True
            io.print(f"sed: invalid option -- '{a.lstrip('-')}'")
            io.print(c("(this world's sed does one thing: s/PATTERN/REPLACEMENT/ , with -i to "
                       "rewrite the file in place. `man sed` on your own machine has the "
                       "other forty flags.)", "dim"))
            return
        else:
            rest.append(a)
    if not rest:
        world.flags["_noop"] = True
        io.print("Usage: sed [-i] SCRIPT FILE...")
        return
    parsed = _parse_s(rest[0])
    if parsed is None:
        world.flags["_noop"] = True
        io.print(f"sed: -e expression #1, char 1: unknown command: `{rest[0][:1]}'")
        io.print(c("(the only script this shell speaks is a substitution: "
                   "s/what-is-there/what-you-want/ — quote it so the shell keeps it "
                   "in one piece)", "dim"))
        return
    pat, rep, glob = parsed
    if not rest[1:]:
        world.flags["_noop"] = True
        io.print("sed: no input files")
        io.print(c("(real sed would sit here reading your keyboard — there is no stdin to "
                   "give it in this world, so name the file you mean)", "dim"))
        return
    try:
        rx = re.compile(pat if extended else _bre(pat))
    except re.error as e:
        world.flags["_noop"] = True
        io.print(f"sed: -e expression #1, char {len(pat) + 2}: {e}")
        return
    touched = 0
    for name in rest[1:]:
        if name not in world.files:
            io.print(f"sed: can't read {name}: No such file or directory")
            continue
        lines = world.files[name].split("\n")
        hits = 0
        for i, ln in enumerate(lines):
            new = rx.sub(lambda mo: _expand(rep, mo), ln, count=0 if glob else 1)
            if new != ln:
                hits += 1
                lines[i] = new
        text = "\n".join(lines)
        if not inplace:
            io.print(text)
            continue
        if hits:
            world.files[name] = text
            _mark_edited(world, name)
            touched += hits
        if hits > 1:
            io.print(c(f"({hits} lines changed in {name}. sed runs the script against EVERY line, "
                       "not just the first one it likes — `s/tag:.*/…/` rewrites both images in a "
                       "two-image values file. grep the result and make sure it hit only what you "
                       "meant.)", "dim"))
        elif not hits:
            io.print(c(f"(0 substitutions in {name} — the pattern matched nothing and the file "
                       "is untouched. Drop the -i to see what a script WOULD do before it "
                       "does it.)", "dim"))
    if not (inplace and touched):
        world.flags["_noop"] = True      # nothing was written: pure inspection


# --------------------------------------------------------------- the robot --
def _yq(world, m, io):
    """Two different programs answer to `yq`, and reaching for it is the reflex
    the note spends a gotcha on. Typing it earns the warning, not a bare
    command-not-found — and this world hands you `sed`, which is what the class
    workflow uses precisely to dodge the argument."""
    world.flags["_noop"] = True
    io.print("yq: command not found")
    io.print(c("🌍 mind this one — there are TWO yq. `pip install yq` gets a Python wrapper "
               "around jq whose in-place editing does not behave; the one everybody means is "
               "the mikefarah Go binary, `yq e -i '.image.tag = \"abc1234\"' values.yaml`, and "
               "in CI you wget it explicitly. The class workflow sidesteps the whole question "
               "with sed — which is what you have here.", "dim"))


def _values_tag(world, name="values.yaml"):
    m = re.search(r"(?m)^\s*tag:\s*(\S+)\s*$", world.files.get(name, ""))
    return m.group(1).strip('"\'') if m else None


# The player is invited to break the cluster by hand, and `kubectl delete
# deployment skywatch` is one of the ways they will. Objective checks run after
# EVERY command, so any one of them that indexes the deployment directly turns a
# legal move into a crash that eats the rest of the session — these two are the
# only way this module is allowed to read it.
def _live(world, name="skywatch"):
    return ((world.k8s or {}).get("deployments") or {}).get(name) or {}


def _live_image(world, name="skywatch"):
    return _live(world, name).get("image", "")


def _tags_by_sha(ci):
    """Does the workflow's `tags:` line name the short-SHA step output? That is
    the whole learning goal of the class, expressed as a string test."""
    m = re.search(r"(?m)^\s*tags:\s*(.+)$", ci or "")
    return bool(m) and "steps.vars.outputs.SHA_TAG" in m.group(1)


def _bot_commit(world, g, path, content, msg):
    """Record the robot's commit the way `git commit` does — sha, snapshot, and
    the content it replaced — so `git log`, `git show` and above all
    `git revert` see a real commit instead of a message with no patch behind
    it. Mission 2's rollback depends on that patch existing."""
    prev = {path: g["head_files"].get(path)}
    world.files[path] = content
    g["head_files"][path] = content
    g["tracked"].add(path)
    g["modified"].discard(path)
    sha = _stable_id(f"ci:{len(g['commits'])}:{msg}")
    g["commits"].append({"branch": g["branch"], "msg": msg, "sha": sha, "date": _now_stamp(),
                         "files": {path: content}, "prev": prev})
    g["pushed_at"][g["branch"]] = len(g["commits"])   # the bot pushed it as well
    return sha


def _push_protection(world, io, sha):
    """GitHub's secret scanning, which is a real gate in front of a real repo.
    Refusing the push is more honest than letting CI 'succeed' with a burned
    credential, and it puts the lesson exactly where the mistake was made."""
    io.print("Enumerating objects: 5, done.")
    io.print(c("remote: error: GH013: Repository rule violations found for refs/heads/main.", "red"))
    io.print("remote:")
    io.print("remote:   —— Docker Hub Personal Access Token ————————————————")
    io.print("remote:    locations:")
    io.print(f"remote:      - commit: {sha}")
    io.print(f"remote:        path: {CI_PATH}:20")
    io.print("remote:")
    io.print("remote:   (?) To push, remove the secret from the commit(s).")
    io.print("To github.com:you/skywatch.git")
    io.print(c(" ! [remote rejected] main -> main (push declined due to repository rule violations)", "red"))
    io.print("error: failed to push some refs to 'github.com:you/skywatch.git'")
    io.print(c("(push protection scans what you push and recognises a dckr_pat_ token on "
               "sight. Put it in Settings → Secrets and reference it as "
               "${{ secrets.DOCKERHUB_TOKEN }} — and rotate it, that one is burned.)", "dim"))
    world.flags["_noop"] = True
    world.flags["push_blocked"] = True


def _argo_tick(world, io, note=True):
    """One ArgoCD polling cycle: compare the tag in Git with the live cluster and
    close the gap. Nobody types this — that is the entire point of the class."""
    want = _values_tag(world)
    d = world.k8s["deployments"].get("skywatch")
    if not want or not d:
        return False
    live = d["image"].rsplit(":", 1)[-1]
    if want == live:
        return False
    repo = d["image"].rsplit(":", 1)[0]
    n = d["replicas"]
    io.print("")
    io.print(c("┌─ ArgoCD · application skywatch ────────────────────────┐", "magenta"))
    for left, right in (("values.yaml changed on main", "OutOfSync"),
                        ("automated sync (prune, selfHeal)", "Syncing"),
                        ("deployment.apps/skywatch", f"image :{want}"),
                        (f"{n}/{n} pods Ready", c("Synced · Healthy", "green"))):
        io.print(c("│", "magenta") + f" {left:<32} → {right}")
    io.print(c("└────────────────────────────────────────────────────────┘", "magenta"))
    if note:
        io.print(c("(nobody typed a sync. syncPolicy.automated polls Git about every 3 minutes; "
                   "the game runs that tick right here so you can watch it land.)", "dim"))
    d["image"] = f"{repo}:{want}"
    d.setdefault("history", []).append(d["image"])
    for p in [p for p, pd in world.k8s["pods"].items() if pd.get("deploy") == "skywatch"]:
        del world.k8s["pods"][p]
    _reconcile(world)
    world.flags["argo_tag"] = want
    world.flags["argo_synced"] = True
    return True


def _git_push(world, m, io):
    """Push, and then everything a push sets off: secret scanning, the CI run,
    the robot's tag-bump commit, and ArgoCD noticing."""
    g = world.git
    if LEAKED_TOKEN in g["head_files"].get(CI_PATH, ""):
        _push_protection(world, io, _sha(g["commits"][-1])[:7] if g["commits"] else "HEAD")
        return
    do_git(world, m.group(0).split()[1:], io)
    if "main" not in g["pushed"]:
        return                                   # do_git already said why
    if g["pushed_at"].get("main") == world.flags.get("ci_at"):
        io.print(c("(no new commits went up, so no workflow run — `on: push` means on NEW "
                   "commits, not on the act of typing git push)", "dim"))
        return
    head = g["commits"][-1]
    sha = _sha(head)[:7]
    ci = g["head_files"].get(CI_PATH, "")        # Actions checks out the COMMIT, not your tree
    if ci != world.files.get(CI_PATH, ""):
        io.print(c("(heads up: the runner checks out the commit you pushed, so it is the "
                   "COMMITTED ci.yaml running below — your unstaged edits aren't in it)", "dim"))
    tag = sha if _tags_by_sha(ci) else "latest"
    io.print("")
    io.print(c("┌─ GitHub Actions · .github/workflows/ci.yaml ───────────┐", "blue"))
    for step, what in (("checkout ", "actions/checkout@v4                  (3s)"),
                       ("vars     ", f"SHA_TAG={sha}   ← ${{GITHUB_SHA::7}}"),
                       ("login    ", "docker/login-action@v3 (secrets.*)    (2s)"),
                       ("build+push", f"ghcr.io/you/skywatch:{tag}".ljust(37) + "(41s)"),
                       ("bump-tag ", f'sed -i "s/tag: .*/tag: {tag}/" values.yaml'),
                       ("commit   ", f'"ci: bump image tag to {tag} [skip ci]"')):
        io.print(c("│", "blue") + f" ✓ {step:<11} {what}")
    io.print(c("└────────────────────────────────────────────────────────┘", "blue"))
    if tag == "latest":
        io.print(c("⚠ the image went up as :latest. Nothing failed — that is the problem. Next "
                   "week nobody can say which commit is running, and a rollback is a guess.", "yellow"))
    else:
        io.print(c(f"(the ROBOT committed the bump — with [skip ci], or its own commit would "
                   f"trigger this workflow again, forever. {sha} is your commit; it is now "
                   "also the image tag.)", "dim"))
    _bot_commit(world, g, "values.yaml",
                re.sub(r"(?m)^(\s*tag:\s*).*$", lambda mo: mo.group(1) + tag,
                       world.files.get("values.yaml", VALUES_YAML)),
                f"ci: bump image tag to {tag} [skip ci]")
    world.flags["ci_ran"] = True
    world.flags["ci_at"] = g["pushed_at"]["main"]
    world.flags["ci_sha"] = sha
    world.flags["ci_tag"] = tag
    _argo_tick(world, io)


APP_NAME = "skywatch"


def _wrong_app(world, args, io):
    """`argocd app <verb> <name>` on an app that does not exist. Telling a
    student who typo'd the name that some OTHER app is Healthy is worse than
    telling them nothing, so mimic the server's own NotFound."""
    if len(args) < 2 or args[0] != "app" or args[1] not in (
            "get", "sync", "history", "diff", "logs", "wait", "rollback"):
        return False
    name = next((a for a in args[2:] if not a.startswith("-")), None)
    if name is None:
        io.print("Usage:\n  argocd app " + args[1] + " APPNAME [flags]")
        io.print(c("(real argocd prints its whole help page here and exits 1 — the app name is "
                   "not optional, because a controller can watch hundreds of them)", "dim"))
    elif name != APP_NAME:
        io.print(c(f'FATA[0000] rpc error: code = NotFound desc = applications.argoproj.io '
                   f'"{name}" not found', "red"))
        io.print(c(f"(the Application is a Kubernetes object like any other — `argocd app list` "
                   f"or `kubectl get applications -n argocd` is how you find its real name. "
                   f"This cluster has one: {APP_NAME}.)", "dim"))
    else:
        return False
    world.flags["_noop"] = True
    return True


def _argocd(world, m, io):
    args = shlex.split(m.group(0))[1:]
    if _wrong_app(world, args, io):
        return
    d = _live(world)
    live = d.get("image", "?").rsplit(":", 1)[-1]
    want = _values_tag(world) or _values_tag(world, "values.yaml") or live
    # A managed resource that isn't in the cluster at all is Missing, not
    # Healthy — the health of nothing is not "fine".
    synced = bool(d) and want == live
    health = "Healthy" if d else "Missing"
    if args[:2] == ["app", "get"]:
        io.print("Name:               skywatch\n"
                 "Project:            default\n"
                 "Server:             https://kubernetes.default.svc\n"
                 f"Repo:               github.com/you/{world.flags.get('repo_name', 'skywatch')}"
                 " (path: charts/skywatch)\n"
                 "Target:             main\n"
                 "Sync Policy:        Automated (prune, selfHeal)\n"
                 f"Sync Status:        {'Synced to HEAD' if synced else 'OutOfSync'}\n"
                 f"Health Status:      {health}\n"
                 + (f"Images:             ghcr.io/you/skywatch:{live}" if d else
                    "Resources:          deployment.apps/skywatch  OutOfSync  Missing"))
        if not d:
            io.print(c("(Git declares a Deployment the cluster does not have. That is what "
                       "Missing means — and with selfHeal on, it is a state ArgoCD will not "
                       "leave you in.)", "dim"))
        elif not synced:
            io.print(c(f"(values.yaml says {want}, the cluster runs {live} — that gap is what "
                       "OutOfSync means. Automated sync closes it on its next poll.)", "dim"))
        elif re.fullmatch(r"[0-9a-f]{7}", live):
            io.print(c(f"(Synced means: the tag in Git and the tag in the cluster are the same "
                       f"string — {live}. That string is a commit you can name.)", "dim"))
        else:
            io.print(c(f"(Synced, and still useless: ':{live}' is not a commit. ArgoCD can only "
                       "tell you the cluster matches Git — it cannot tell you WHICH code that "
                       "is, because the tag doesn't say.)", "dim"))
        world.flags["argo_get"] = True
        world.flags["_noop"] = True
    elif args[:2] == ["app", "list"]:
        io.print("NAME      CLUSTER                         NAMESPACE  PROJECT  STATUS     HEALTH")
        io.print(f"skywatch  https://kubernetes.default.svc  default    default  "
                 f"{'Synced' if synced else 'OutOfSync':<9}  {health}")
        world.flags["_noop"] = True
    elif args[:2] == ["app", "history"]:
        io.print("ID  DATE                           REVISION")
        for i, cm in enumerate(world.git["commits"], 1):
            io.print(f"{i:<3} {cm.get('date', 'just now'):<30} {_sha(cm)[:7]}  {cm['msg']}")
        io.print(c("(every deploy is a commit, so the deploy history IS the git history — this "
                   "is the audit log you get for free)", "dim"))
        world.flags["argo_history"] = True
        world.flags["_noop"] = True
    elif args[:2] == ["app", "sync"]:
        io.print("Name:               skywatch\nOperation:          Sync\n"
                 "Phase:              Succeeded\nMessage:            successfully synced (all tasks run)\n"
                 f"Sync Status:        {'Synced to HEAD' if synced else 'OutOfSync'}")
        if synced:
            io.print(c("(nothing to do — automated sync had already applied this commit. With "
                       "syncPolicy.automated on, the sync button is a thing you press to feel "
                       "better, not a step in the loop.)", "dim"))
        else:
            _argo_tick(world, io, note=False)
        world.flags["manual_sync"] = True
    elif args[:1] in (["version"], ["--version"]):
        # Check-first works everywhere else in the game; a mission that owns the
        # tool must not be the one place the check is refused.
        world.flags["_noop"] = True
        io.print(TOOL_VERSION_LINES["argocd"])
        io.print(c("(it answered → the CLI is installed. Whether it can reach a server is the "
                   "next question: `argocd app list`)", "dim"))
    else:
        world.flags["_noop"] = True
        io.print("argocd: try `argocd app get|list|history|sync skywatch`")


# ----------------------------------------------------- mission 2: the drift --
def _selfheal(world, io):
    """selfHeal, doing the one thing it exists for: putting Git's number back."""
    want = 2
    m = re.search(r"(?m)^\s*replicas:\s*(\d+)", world.files.get("values.yaml", ""))
    if m:
        want = int(m.group(1))
    d = world.k8s["deployments"].get("skywatch")
    if not d or d["replicas"] == want:
        return
    was = d["replicas"]
    io.print("")
    io.print(c("┌─ ArgoCD · application skywatch ────────────────────────┐", "magenta"))
    for left, right in ((f"live replicas {was}, values.yaml says {want}", "OutOfSync"),
                        ("selfHeal: true", "Syncing"),
                        ("deployment.apps/skywatch scaled", f"{want} replicas"),
                        (f"{want}/{want} pods Ready", c("Synced · Healthy", "green"))):
        io.print(c("│", "magenta") + f" {left:<32} → {right}")
    io.print(c("└────────────────────────────────────────────────────────┘", "magenta"))
    io.print(c("(your kubectl was undone, on purpose. selfHeal means the cluster obeys Git and "
               "not you — if you WANT one replica, change values.yaml and push. Real ArgoCD "
               "takes up to its 3-minute poll to do this; the game does it now.)", "dim"))
    d["replicas"] = want
    _reconcile(world)
    world.flags["selfhealed"] = True


def _selfheal_gone(world, io, was):
    """The other half of selfHeal, and the one that separates it from prune:
    deleting a resource Git still declares is drift too, so ArgoCD puts it back.
    prune deletes what GIT dropped; selfHeal restores what YOU dropped."""
    k = world.k8s
    if not was or "skywatch" in k["deployments"]:
        return
    tag = _values_tag(world) or was["image"].rsplit(":", 1)[-1]
    repo = was["image"].rsplit(":", 1)[0]
    d = dict(was)
    d["image"] = f"{repo}:{tag}"
    n = d.get("replicas", 2)
    io.print("")
    io.print(c("┌─ ArgoCD · application skywatch ────────────────────────┐", "magenta"))
    for left, right in (("deployment.apps/skywatch missing", "OutOfSync"),
                        ("selfHeal: true", "Syncing"),
                        ("deployment.apps/skywatch created", f"image :{tag}"),
                        (f"{n}/{n} pods Ready", c("Synced · Healthy", "green"))):
        io.print(c("│", "magenta") + f" {left:<32} → {right}")
    io.print(c("└────────────────────────────────────────────────────────┘", "magenta"))
    io.print(c("(it came straight back, because Git still says it should exist. To actually "
               "remove it you delete it from the REPO and let `prune: true` do the kubectl — "
               "selfHeal restores what you dropped, prune drops what Git did.)", "dim"))
    k["deployments"]["skywatch"] = d
    _reconcile(world)
    world.flags["selfhealed"] = True


def _drift(world, m, io):
    """`kubectl scale` / `kubectl delete` here are drift on purpose: the engine
    performs them, ArgoCD reverses them, and the player watches Git win the
    argument. Both verbs, because a student who is told to break the cluster by
    hand reaches for delete at least as fast as for scale."""
    was = _live(world) or None
    do_kubectl(world, shlex.split(m.group(0))[1:], io)
    _selfheal_gone(world, io, was)
    _selfheal(world, io)


def _gitops_push(world, m, io):
    """In the GitOps repo there is no CI — only the fact that ArgoCD watches the
    REMOTE. A commit that never left your laptop deploys nothing, and feeling
    that gap is the point of pushing the revert as a separate step."""
    do_git(world, m.group(0).split()[1:], io)
    if world.git["pushed_at"].get(world.git["branch"]) == len(world.git["commits"]):
        if _argo_tick(world, io):
            world.flags["argo_rollback"] = True


MISSIONS = [
    {
        "id": "gitops-01",
        "topic": "gitops",
        "title": "The Robot Deploys 🤖 — GitOps end to end",
        "vault_note": "Class 08 - GitOps and CI-CD",
        "repo_name": "skywatch",
        "brief": ("Nobody kubectl-applies to prod by hand. You push code; a CI robot builds\n"
                  "the image, tags it with your commit's SHORT SHA, and commits that tag into\n"
                  "Git — then ArgoCD makes the cluster match. The cluster runs v1 right now.\n\n"
                  "The pipeline (.github/workflows/ci.yaml) was written by someone in a hurry.\n"
                  "READ it before you trust it: there is a credential in it that must not be\n"
                  "there, and a tag that makes 'what is running?' unanswerable.\n"
                  "Tools on hand: cat · grep · sed -i 's/old/new/' file · git · argocd."),
        "world": {
            "files": {"app.py": APP_PY, CI_PATH: CI_YAML, "values.yaml": VALUES_YAML},
            "git": {"branch": "main", "tracked": ["app.py", CI_PATH, "values.yaml"],
                    "commits": [{"branch": "main", "msg": "initial skywatch"}],
                    "pushed": [],
                    "branch_files": {"main": {}}},
            "k8s": {"started": True,
                    "deployments": {"skywatch": {"ns": "default", "replicas": 2,
                                                 "image": "ghcr.io/you/skywatch:v1"}}},
        },
        "handlers": [
            (r"cat\s+.*", _cat),
            (r"grep\s+.*", _grep),
            (r"sed\s+.*", _sed),
            (r"yq(\s+.*)?", _yq),
            # Same Application, same syncPolicy.automated — so the cluster has to
            # answer hand-drift the same way here as in mission 2, or the game
            # contradicts the lesson it is about to teach.
            (r"kubectl\s+(scale|delete)\s+.*", _drift),
            (r"git\s+push.*", _git_push),
            (r"argocd\s+.*", _argocd),
        ],
        "objectives": [
            {"desc": "Read the pipeline you are about to trust — all of ci.yaml", "xp": 10,
             "hint": "cat .github/workflows/ci.yaml — every line. `ls` shows the paths.",
             "check": lambda w: CI_PATH in w.flags.get("read", set())},
            {"desc": "Get the DockerHub token out of the YAML and behind a GitHub Secret", "xp": 20,
             "hint": ("grep -n dckr_pat .github/workflows/ci.yaml finds it. Then: "
                      "sed -i 's/password: .*/password: ${{ secrets.DOCKERHUB_TOKEN }}/' "
                      ".github/workflows/ci.yaml"),
             "check": lambda w: ("secrets.DOCKERHUB_TOKEN" in w.files.get(CI_PATH, "")
                                 and LEAKED_TOKEN not in w.files.get(CI_PATH, ""))},
            {"desc": "Kill `:latest` — tag the image with the short SHA the workflow already computes", "xp": 20,
             "hint": ("the Set IMAGE TAG step exports SHA_TAG. Point the build at it: "
                      "sed -i 's|skywatch:latest|skywatch:${{ steps.vars.outputs.SHA_TAG }}|' "
                      ".github/workflows/ci.yaml"),
             "check": lambda w: (_tags_by_sha(w.files.get(CI_PATH, ""))
                                 and ":latest" not in w.files.get(CI_PATH, ""))},
            {"desc": "Commit your changes — and know the tag CI will use BEFORE it runs", "xp": 15,
             "hint": ("git add . ; git commit -m \"…\" ; then git log --oneline — those 7 "
                      "characters at the start of the line are the tag."),
             "check": lambda w: (w.git and len(w.git["commits"]) >= 2 and w.flags.get("git_log"))},
            {"desc": "Push — and watch the robot build, tag and commit", "xp": 20,
             "hint": "git push -u origin main (the first push of a branch needs -u). Then READ the run.",
             "check": lambda w: w.flags.get("ci_ran")},
            {"desc": "ArgoCD deployed it without you: Synced, and the tag is the sha you predicted", "xp": 20,
             "hint": "argocd app get skywatch — Sync Status and the Images line.",
             "check": lambda w: (w.flags.get("argo_get") and w.flags.get("ci_sha")
                                 and w.flags.get("argo_tag") == w.flags.get("ci_sha"))},
            {"desc": "Trace it home: the running image names a commit you can look up", "xp": 15,
             "hint": "kubectl describe deployment skywatch — the Image line — then git log --oneline.",
             "check": lambda w: (w.flags.get("describe_deploy_skywatch") and w.k8s
                                 and _live_image(w).endswith(
                                     ":" + (w.flags.get("ci_sha") or "\x00")))},
        ],
        "teach": [
            "Review a pipeline like code, because it is code — and it runs with your credentials.",
            "A token in plain YAML is a leaked token. ${{ secrets.NAME }} is the only shape allowed; "
            "GitHub's push protection will refuse the push if you forget.",
            ":latest is a mutable pointer — you cannot say which code it is. ${GITHUB_SHA::7} is "
            "immutable and traceable, which is what makes a rollback deterministic.",
            "The tag is not invented by CI: it IS your commit. git log --oneline tells you the "
            "image name before the runner does.",
            "CI stops at 'edited a file in Git'. It never touches the cluster — which is why it "
            "never needs cluster credentials. [skip ci] keeps its own commit from looping.",
            "You never ran a deploy. syncPolicy.automated means the cluster pulls Git; the push "
            "was the deploy.",
            "Running image → commit → diff → author. That chain is the entire argument for GitOps.",
        ],
        "solution": [
            "ls",
            "cat .github/workflows/ci.yaml",
            "grep -n dckr_pat .github/workflows/ci.yaml",
            "sed -i 's/password: .*/password: ${{ secrets.DOCKERHUB_TOKEN }}/' .github/workflows/ci.yaml",
            "sed -i 's|skywatch:latest|skywatch:${{ steps.vars.outputs.SHA_TAG }}|' .github/workflows/ci.yaml",
            "grep -n dckr_pat .github/workflows/ci.yaml",
            "sed -i 's/SkyWatch v1/SkyWatch v2/' app.py",
            "git add .",
            'git commit -m "ci: use a secret for the token, tag images by short SHA"',
            "git log --oneline",
            "git push -u origin main",
            "argocd app get skywatch",
            "kubectl describe deployment skywatch",
        ],
    },
    {
        "id": "gitops-02",
        "topic": "gitops",
        "title": "Drift and the Undo Button 🧲 — selfHeal + git revert",
        "vault_note": "Class 08 - GitOps and CI-CD",
        "repo_name": "skywatch-gitops",
        "brief": ("You are inside the GitOps repo now — no app code, no CI, just the desired\n"
                  "state ArgoCD reads: values.yaml and the Application (dev.yaml). The cluster\n"
                  f"runs skywatch:{SHA_NEW}, bumped there by the robot in the last commit.\n\n"
                  "Two things every GitOps engineer has to have done once: break the cluster by\n"
                  "hand and watch it refuse to stay broken, then roll a release back WITHOUT\n"
                  "touching the cluster at all. Zero kubectl apply. Ready?"),
        "world": {
            "files": {"values.yaml": GITOPS_VALUES.format(tag=SHA_NEW), "dev.yaml": DEV_YAML},
            "git": {"branch": "main", "tracked": ["values.yaml", "dev.yaml"],
                    "pushed": ["main"],
                    "commits": [
                        {"branch": "main", "msg": "chart: skywatch values + ArgoCD Application"},
                        {"branch": "main", "msg": f"ci: bump image tag to {SHA_NEW} [skip ci]",
                         "files": {"values.yaml": GITOPS_VALUES.format(tag=SHA_NEW)},
                         "prev": {"values.yaml": GITOPS_VALUES.format(tag=SHA_OLD)}},
                    ],
                    "branch_files": {"main": {}}},
            "k8s": {"started": True,
                    "deployments": {"skywatch": {"ns": "default", "replicas": 2,
                                                 "image": f"ghcr.io/you/skywatch:{SHA_NEW}"}}},
        },
        "handlers": [
            (r"cat\s+.*", _cat),
            (r"grep\s+.*", _grep),
            (r"sed\s+.*", _sed),
            (r"yq(\s+.*)?", _yq),
            (r"kubectl\s+(scale|delete)\s+.*", _drift),
            (r"git\s+push.*", _gitops_push),
            (r"argocd\s+.*", _argocd),
        ],
        "objectives": [
            {"desc": "Read the Application — name its four spec blocks and what each controls", "xp": 10,
             "hint": "cat dev.yaml — project, destination, source, syncPolicy.",
             "check": lambda w: "dev.yaml" in w.flags.get("read", set())},
            {"desc": "Confirm the guard is armed: Synced, Healthy, Automated (prune, selfHeal)", "xp": 10,
             "hint": "argocd app get skywatch",
             "check": lambda w: w.flags.get("argo_get")},
            {"desc": "Break it on purpose by hand: scale the deployment to 0 (or delete it)", "xp": 25,
             "hint": "kubectl scale deployment skywatch --replicas=0 — then read what answers you.",
             "check": lambda w: (w.flags.get("selfhealed")
                                 and _live(w).get("replicas") == 2)},
            {"desc": f"Roll back the GitOps way: revert the bump commit so Git says {SHA_OLD} again", "xp": 25,
             "hint": "git log --oneline to find the bump commit, then git revert <sha> (HEAD works too).",
             "check": lambda w: (w.flags.get("git_revert") and _values_tag(w) == SHA_OLD)},
            {"desc": "Push it — ArgoCD watches the REMOTE, not your laptop", "xp": 20,
             "hint": "git push. A commit that never left your machine deploys nothing.",
             "check": lambda w: w.flags.get("argo_rollback")},
            {"desc": f"Prove the cluster followed: it runs {SHA_OLD}, and you never ran kubectl apply", "xp": 15,
             "hint": "kubectl describe deployment skywatch — read the Image line.",
             "check": lambda w: (w.flags.get("describe_deploy_skywatch")
                                 and _live_image(w).endswith(":" + SHA_OLD))},
        ],
        "teach": [
            "project / destination / source / syncPolicy: who owns it, where it goes, what it is, "
            "and who presses deploy. `project: default` is required in ArgoCD 2.x — omit it and "
            "the Application is invalid.",
            "Synced + Healthy are two different questions: does the cluster MATCH Git, and is what "
            "it runs actually well.",
            "selfHeal reverts manual drift; prune deletes what you removed from Git. Together they "
            "say: Git is the only truth, and your kubectl is a suggestion.",
            "git revert ADDS a commit that undoes an old one — history is preserved, so the "
            "rollback is itself auditable. That is why GitOps rollbacks are boring.",
            "ArgoCD reconciles against the repo, not your working tree. Committed is not deployed; "
            "PUSHED is deployed.",
            "A rollback with zero kubectl and zero pipeline re-runs. The cluster followed a commit, "
            "the way it does every other day of the week.",
        ],
        "solution": [
            "ls",
            "cat dev.yaml",
            "argocd app get skywatch",
            "kubectl scale deployment skywatch --replicas=0",
            "kubectl get pods",
            "git log --oneline",
            "git revert HEAD --no-edit",
            "cat values.yaml",
            "git push",
            "argocd app get skywatch",
            "kubectl describe deployment skywatch",
        ],
    },
]
