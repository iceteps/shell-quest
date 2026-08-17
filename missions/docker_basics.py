"""Docker missions — mirror the course's Docker classes AND Yariv's real
'Docker Basics – Assignment 1' (build → tag → login with token → push).

Several objectives here are BEFORE/AFTER claims — the files survived a stop but
not an `rm`, this rebuild cached what the last one didn't, that name failed to
resolve on the default bridge. The engine has no reason to keep the 'before', so
the handlers below wrap its own docker commands, let it do the work, and note
what changed. They never re-implement a command: they delegate and remember."""
import shlex

from engine import INSTALLISH, c, do_docker, run_inside

BASE_APP = '''from http.server import BaseHTTPRequestHandler, HTTPServer

HOST = "0.0.0.0"
PORT = 8080

class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Hello from Docker! Your app is running.\\n")

if __name__ == "__main__":
    print(f"Server starting on {HOST}:{PORT}")
    HTTPServer((HOST, PORT), SimpleHandler).serve_forever()
'''


def _curl_flask(world, m, io):
    ok = any(d["status"] == "running" and d["image"].startswith("my-flask-app")
             for d in world.containers.values())
    if ok and world.flags.get("fixed_build"):
        io.print("Hello! I am a Flask application — back from the dead 🎉")
        world.flags["curl_ok"] = True
    elif ok:
        io.print("curl: (52) Empty reply from server   (the container is up but the app inside crashed — check its logs)")
    else:
        io.print("curl: (7) Failed to connect to localhost port 8080: Connection refused")


def _build_flask(world, m, io):
    req = world.files.get("requirements.txt", "")
    if "flask" in req.lower():
        io.print(" => [2/4] RUN pip install -r requirements.txt")
        io.print(" => => # Installing collected packages: flask")
        io.print(" => naming to docker.io/library/my-flask-app:latest")
        world.images.add("my-flask-app:latest")
        world.flags["fixed_build"] = True
        world.flags["built"] = "my-flask-app:latest"
    else:
        io.print(" => [2/4] RUN pip install -r requirements.txt")
        io.print(" => => # (nothing to install — requirements.txt is empty!)")
        io.print(c("Build 'succeeded'… but with no dependencies installed the app will crash again.", "yellow"))
        io.print(c("(look at requirements.txt — what SHOULD it contain? fix it with: edit requirements.txt)", "dim"))


# The Class 02 Lab B app, with ONE deliberate change: it listens on 9090. The
# note's quiz asks "in -p 8080:9090, which number is the host?" — a lab where
# both numbers are 8080 can never make that question land.
FLASK_APP = '''from flask import Flask

app = Flask(__name__)


@app.route("/")
def home():
    return "Hello! I am a Flask application"


if __name__ == "__main__":
    print("listening on port:  9090")
    app.run(host="0.0.0.0", port=9090)
'''

# The Dockerfile as people first write it: code copied in ABOVE the install, so
# every one-character edit re-runs pip. Fixing that order is the mission.
SLOW_DOCKERFILE = """FROM python:3
WORKDIR /usr/src
COPY . /usr/src/
RUN pip install -r requirements.txt
EXPOSE 9090
CMD ["python3", "app.py"]"""


# ------------------------------------------------------- delegating handlers --
def _run_docker(world, io, line):
    """Hand a command line back to the engine's own docker, exactly as dispatch
    would. Handlers match BEFORE dispatch looks at `world.inside`, so the
    inside-a-container case has to be honoured here — otherwise `docker ps`
    would work from inside a container, where docker isn't installed at all.
    Returns False when the line never reached docker."""
    args = shlex.split(line)
    if world.inside:
        run_inside(world, world.inside, args, io)
        return False
    do_docker(world, args[1:], io)
    return True


def _tail(line, sub):
    """The arguments after `docker [container|image] <sub>`."""
    args = shlex.split(line)[1:]
    return args[args.index(sub) + 1:]


def _watch_lifecycle(world, m, io):
    """`docker stop|start|rm` — plus what the container held BEFORE the command.
    stop-vs-rm is the comparison beginners blur, and only the 'before' can
    settle it: stop keeps the writable layer, rm destroys it."""
    line, sub = m.group(0), m.group(1)
    names = [a for a in _tail(line, sub) if not a.startswith("-")]
    had = {n: dict(world.containers[n]["files"]) for n in names if n in world.containers}
    if not _run_docker(world, io, line):
        return
    for n, files in had.items():
        ctr = world.containers.get(n)
        if sub == "stop" and ctr and ctr["status"] != "running":
            world.flags.setdefault("_stopped", {})[n] = files
            # "I've seen inside it" expires with the container's state: after a
            # stop, only a fresh look proves what came back.
            world.flags.get("_looked", set()).discard(n)
        elif sub == "start" and ctr and ctr["status"] == "running":
            kept = world.flags.get("_stopped", {}).pop(n, None)
            if files and ctr["files"] == kept:
                world.flags["resumed_intact"] = n
        elif sub == "rm" and n not in world.containers:
            if files:
                world.flags["rm_lost"] = (n, sorted(files))
            # Anything we knew about being inside THAT container is stale now —
            # the next container to wear the name is a different container.
            world.flags.pop("exec_" + n, None)
            world.flags.get("_looked", set()).discard(n)


def _watch_exec(world, m, io):
    """`docker exec` — plus which container the player actually looked inside.
    'Prove the files are gone' is only proved by looking."""
    line = m.group(0)
    name = next((a for a in _tail(line, "exec") if not a.startswith("-")), None)
    if not _run_docker(world, io, line):
        return
    ctr = world.containers.get(name)
    if ctr and ctr["status"] == "running":
        world.flags.setdefault("_looked", set()).add(name)


def _img_matches(img, filt):
    """The engine's own `docker images <filter>` test — repeated here so we
    record the rows that were really on screen, not every image that exists."""
    repo = img.rsplit(":", 1)[0]
    return not filt or filt in (repo, img, repo.rsplit("/", 1)[-1])


def _watch_images(world, m, io):
    """`docker images` — remembering which rows the player SAW. 'Verify it exists
    locally' is a claim about the terminal output, not about the daemon."""
    line = m.group(0)
    args = shlex.split(line)[1:]
    words = [a for a in args if not a.startswith("-")]
    filt = next((w for w in words if w not in ("images", "image", "ls", "list")), None)
    if not _run_docker(world, io, line):
        return
    if any(a in ("--help", "-h") for a in args):
        return                        # a help page lists flags, not images
    world.flags.setdefault("_images_seen", set()).update(
        i for i in world.images if _img_matches(i, filt))


def _prefix(a, b):
    """How many leading entries two lists share."""
    n = 0
    while n < len(a) and n < len(b) and a[n] == b[n]:
        n += 1
    return n


def _watch_build(world, m, io):
    """`docker build`, then the comparison a single build can't make. The engine
    keeps one cache key per instruction per build in `_build_keys` (newest last);
    how far a new list agrees with an older one is exactly how many steps said
    CACHED — and WHICH steps those are is the ordering lesson."""
    line = m.group(0)
    before = list(world.flags.get("_build_keys", []))
    if not _run_docker(world, io, line):
        return
    builds = world.flags.get("_build_keys", [])
    if len(builds) == len(before):
        return                        # the build failed: no layers, nothing to say
    now = builds[-1]
    cached = max([_prefix(old, now) for old in before] or [0])
    if not cached:
        return
    if cached == len(now):
        world.flags["cache_all"] = True
        return
    world.flags["cache_partial"] = True
    if any(k.startswith("RUN ") and any(t in k for t in INSTALLISH) for k in now[:cached]):
        # Something DID change, and the install still didn't re-run. That only
        # happens when the dependencies are copied in above the application code.
        world.flags["cache_kept_install"] = True


def _watch_ping(world, m, io):
    """`ping` from inside a container — and, when it fails, why. A name that IS a
    running container and still doesn't resolve is the default bridge's missing
    DNS: the negative control the class only describes."""
    args = shlex.split(m.group(0))
    if not world.inside:
        world.flags["_noop"] = True
        io.print("ping: works from INSIDE a container here — "
                 "docker exec -it <name> bash, then ping <other>")
        return
    me = world.inside
    target = next((a for a in reversed(args[1:]) if not a.startswith("-")), None)
    # ping_ok is the engine's success flag; clearing it first is the only way to
    # tell THIS ping's result from a successful one earlier in the mission.
    before = world.flags.pop("ping_ok", None)
    run_inside(world, me, args, io)
    if "ping_ok" in world.flags:
        return
    if before is not None:
        world.flags["ping_ok"] = before
    peer = world.containers.get(target)
    if peer is not None and peer["status"] == "running":
        world.flags["bridge_ping_failed"] = (me, target)


# ------------------------------------------------------------ check helpers --
def _dance_done(ctr):
    """Class 01's file drill: a temp/ directory with something in it, and a .txt
    still loose in /root — which together mean a copy was made and one of the
    two was moved, whichever file the student chose to move."""
    files = ctr["files"]
    return ("temp/" in files
            and any(k.startswith("temp/") and k != "temp/" for k in files)
            and any("/" not in k and k.endswith(".txt") for k in files))


def _proof_of_disposal(w):
    """Class 01 step 6: the container that held the files is gone, and a fresh
    one has been opened and found empty. Any name works — the lesson is the empty
    /root, not the spelling of --name."""
    if not w.flags.get("rm_lost"):
        return False
    looked = w.flags.get("_looked", set())
    return any(n in looked and d["status"] == "running" and not d["files"]
               for n, d in w.containers.items())


def _deps_first(text):
    """Is the dependency list copied in and installed BEFORE the application
    code? That order is the entire reason a code edit can leave pip CACHED."""
    deps = install = code = None
    for i, raw in enumerate(text.split("\n")):
        head, _, arg = raw.strip().partition(" ")
        head, first = head.upper(), (arg.split() or [""])[0]
        if head == "COPY" and "requirements" in first and deps is None:
            deps = i
        elif head == "COPY" and code is None and (first.startswith(".")
                                                  or first.endswith((".py", ".js", ".go"))):
            code = i
        elif head == "RUN" and "install" in arg and install is None:
            install = i
    return None not in (deps, install, code) and deps < install < code


def _published(w, host_port, ctr_port):
    """The running container publishing host_port → ctr_port, if any. `-p` is
    HOST:CONTAINER, and this is the only check in the game that cares which
    side is which."""
    for name, d in w.running().items():
        for mapping in d.get("ports", []):
            fields = [f.split("/")[0] for f in mapping.split(":")]
            if len(fields) >= 2 and fields[-2] == host_port and fields[-1] == ctr_port:
                return name
    return None


MISSIONS = [
    {
        "id": "docker-01",
        "topic": "docker",
        "title": "Hello, Container 🐳",
        "vault_note": "Class 01 - Docker Basics",
        "brief": ("First day with Docker. Get the ubuntu image, start a container you can\n"
                  "work in, go INSIDE it, leave files as proof you were there — then find\n"
                  "out what a container really is by stopping it (files survive) and\n"
                  "destroying it (files don't). Exactly what class did — from memory.\n\n"
                  "🌍 Real-world setup (already done for you here): Docker itself arrives via\n"
                  "   Docker Desktop — GUI installer from docker.com, or a package manager\n"
                  "   (winget/brew/apt). Already have it? Check BEFORE installing:\n"
                  "   `docker --version` answering means you're set — a reinstall can run\n"
                  "   over a working setup. 'Downloading an image' is then Docker's own job,\n"
                  "   from the Docker Hub registry:\n"
                  "   CLI: docker pull <image>   ·   GUI: Docker Desktop → search → Pull"),
        "world": {},
        "handlers": [
            (r"docker\s+(?:container\s+)?exec\b.*", _watch_exec),
            (r"docker\s+(?:container\s+)?(stop|start|rm)\b.*", _watch_lifecycle),
        ],
        "objectives": [
            {"desc": "Download the ubuntu image", "xp": 10,
             "hint": "Getting an image from Docker Hub is called PULLING it.",
             "check": lambda w: "ubuntu:latest" in w.images},
            {"desc": "Start a NAMED ubuntu container running bash, detached + interactive", "xp": 15,
             "hint": "docker run with three little flags (-d -i -t, combinable) and --name <something>.",
             "check": lambda w: any(d["image"].startswith("ubuntu") and d["status"] == "running"
                                    for d in w.containers.values())},
            {"desc": "Get a shell INSIDE the container", "xp": 15,
             "hint": "exec-ute an interactive bash in it: docker exec -it <name> bash",
             "check": lambda w: any(k.startswith("exec_") for k in w.flags)},
            {"desc": "Create file1.txt inside the container, then exit", "xp": 15,
             # `mv file1.txt temp/` (the next objective) moves the key, so the file
             # is looked for anywhere in the container — not only at the top.
             "hint": "While inside: touch file1.txt — then `exit` to come home.",
             "check": lambda w: w.inside is None and any(
                 any(k == "file1.txt" or k.endswith("/file1.txt") for k in d["files"])
                 for d in w.containers.values())},
            {"desc": "Verify your container is running", "xp": 5,
             "hint": "The command that lists RUNNING containers.",
             "check": lambda w: w.flags.get("ps")},
            {"desc": "Inside it: mkdir temp, copy file1.txt to file2.txt, move file1.txt into temp/",
             "xp": 15,
             "hint": "The class's file dance: mkdir temp · cp file1.txt file2.txt · "
                     "mv file1.txt temp/ · then ls and ls temp to see what you did.",
             "check": lambda w: any(_dance_done(d) for d in w.containers.values())},
            {"desc": "Stop the container, start it again, and check: the files are still there",
             "xp": 10,
             "hint": "docker stop <name>, then docker start <name> — then look inside again: "
                     "docker exec <name> ls",
             "check": lambda w: w.flags.get("resumed_intact") in w.flags.get("_looked", set())},
            {"desc": "Now DESTROY it, run a fresh one from the same image, and look inside", "xp": 20,
             "hint": "docker rm -f <name> · docker run -dit --name <name> ubuntu bash · "
                     "docker exec <name> ls /root — expect nothing.",
             "check": _proof_of_disposal},
        ],
        "teach": [
            "Images download once and cache locally — `pull` fetches from a registry (Docker Hub by default). "
            "Docker Desktop's GUI can pull too, but the CLI is the muscle worth building.",
            "-d runs detached (background); -it keeps an interactive terminal alive — bash would exit instantly without it.",
            "`exec` enters a RUNNING container; `run` would have created a brand-new one.",
            "A container's filesystem is its own little world — files you create live (and die) with it.",
            "`docker ps` = running only; add -a and stopped containers appear too.",
            "Inside a container you're on an ordinary Linux filesystem: `cp` duplicates, "
            "`mv <file> <dir>/` moves and keeps the name. All of it lands in this container's "
            "own writable layer — not in the image.",
            "`stop` freezes a container and KEEPS its writable layer; `start` resumes it with every "
            "file intact. stop ≠ rm — that's the distinction beginners blur.",
            "`rm` deletes the writable layer for good: same recipe, brand-new empty plate. That's "
            "what 'containers are disposable' means — and why anything you care about belongs in a "
            "volume, never in the container.",
        ],
        "solution": [
            "docker --version",
            "docker pull ubuntu:latest",
            "docker run -dit --name devops1 ubuntu bash",
            "docker ps",
            "docker exec -it devops1 bash",
            "touch file1.txt",
            "mkdir temp",
            "cp file1.txt file2.txt",
            "mv file1.txt temp/",
            "ls", "ls temp", "exit",
            "docker stop devops1",
            "docker start devops1",
            "docker exec devops1 ls temp",
            "docker rm -f devops1",
            "docker run -dit --name devops1 ubuntu bash",
            "docker exec devops1 ls /root",
        ],
    },
    {
        "id": "docker-02",
        "topic": "docker",
        "title": "The Vanishing Container 🕵️",
        "vault_note": "Class 02 - Docker Networking and Images",
        "brief": ("The demo is in 5 minutes and the app is DOWN. A container named 'webapp'\n"
                  "should be serving on port 8080, but nothing answers. Find out what\n"
                  "happened, fix the ROOT CAUSE, and bring it back."),
        "world": {
            "images": ["my-flask-app:latest", "python:3.11-slim"],
            "containers": [{
                "name": "webapp", "image": "my-flask-app:latest",
                "status": "exited", "exit_code": 1,
                "logs": ('Traceback (most recent call last):\n'
                         '  File "/app/app.py", line 1, in <module>\n'
                         '    from flask import Flask\n'
                         "ModuleNotFoundError: No module named 'flask'"),
            }],
            "files": {
                "Dockerfile": ("FROM python:3.11-slim\nWORKDIR /app\nCOPY requirements.txt .\n"
                               "RUN pip install -r requirements.txt\nCOPY app.py .\nEXPOSE 8080\n"
                               'CMD ["python", "app.py"]'),
                "requirements.txt": "",
                "app.py": "from flask import Flask\n# ... the app ...",
            },
        },
        "handlers": [
            (r"docker\s+build.*", _build_flask),
            (r"curl\s+(-s\s+)?(http://)?localhost:8080/?", _curl_flask),
        ],
        "objectives": [
            {"desc": "Find the dead container (it's not in the normal list…)", "xp": 10,
             "hint": "docker ps shows only running containers — there's a flag that shows ALL.",
             "check": lambda w: w.flags.get("ps_a")},
            {"desc": "Read the crash logs to find the root cause", "xp": 15,
             "hint": "Every container keeps its output: docker logs <name>.",
             "check": lambda w: w.flags.get("logs_webapp")},
            {"desc": "Fix the root cause and rebuild the image", "xp": 25,
             "hint": "The log says flask isn't installed. Where do this image's dependencies come from? "
                     "cat requirements.txt … then edit it, then docker build -t my-flask-app .",
             "check": lambda w: w.flags.get("fixed_build")},
            {"desc": "Run the fixed app: detached, port 8080 published", "xp": 20,
             "hint": "Remove/rename the old dead container if the name clashes; then docker run -d -p 8080:8080 --name … my-flask-app",
             "check": lambda w: any(d["image"].startswith("my-flask-app") and d["status"] == "running"
                                    and any("8080" in p for p in d["ports"]) for d in w.containers.values())
                                and w.flags.get("fixed_build")},
            {"desc": "Prove it answers: curl localhost:8080", "xp": 10,
             "hint": "curl localhost:8080",
             "check": lambda w: w.flags.get("curl_ok")},
        ],
        "teach": [
            "Crashed containers vanish from `docker ps` — `-a` is where the dead ones go.",
            "`docker logs` keeps a container's output even after it dies — read it BEFORE guessing.",
            "Dependencies bake into the image at BUILD time — fixing them means rebuilding, not restarting.",
            "-p host:container publishes the port; a name conflict means the old container must go first.",
            "Verify like an outsider: if curl can't reach it, neither can your users.",
        ],
        "solution": [
            "docker ps -a",
            "docker logs webapp",
            "cat requirements.txt",
            "edit requirements.txt", "flask", ".",
            "docker build -t my-flask-app .",
            "docker rm webapp",
            "docker run -d -p 8080:8080 --name webapp my-flask-app",
            "curl localhost:8080",
        ],
    },
    {
        "id": "docker-03",
        "topic": "docker",
        "title": "Talk to Each Other 🕸️",
        "vault_note": "Class 02 - Docker Networking and Images",
        "brief": ("Two services need to find each other BY NAME — that's how the frontend\n"
                  "will find 'rabbitmq' later in the course. But first run the control\n"
                  "experiment the class only talks about: try it WITHOUT a network of your\n"
                  "own and watch it fail. Then create a user-defined network, put two nginx\n"
                  "containers on it, and prove name-resolution works."),
        "world": {"images": ["nginx:alpine"]},
        "handlers": [(r"ping\b.*", _watch_ping)],
        "objectives": [
            {"desc": "Control experiment: two nginx containers on the DEFAULT bridge — "
                     "ping one by name and watch it fail", "xp": 15,
             "hint": "docker run -d --name bridge1 nginx:alpine (twice, no --network at all), "
                     "then docker exec -it bridge1 sh and ping bridge2. It is SUPPOSED to fail — "
                     "read the error word for word.",
             "check": lambda w: bool(w.flags.get("bridge_ping_failed"))},
            {"desc": "Create a user-defined network", "xp": 15,
             "hint": "docker network create <a-name-you-choose>",
             "check": lambda w: len(w.networks) > 1},
            {"desc": "Run TWO nginx containers named web1 and web2 on that network", "xp": 20,
             "hint": "docker run -d --name web1 --network <yournet> nginx:alpine  (twice, different names)",
             "check": lambda w: all(n in w.containers and w.containers[n]["status"] == "running"
                                    and w.containers[n]["network"] != "bridge" for n in ("web1", "web2"))},
            {"desc": "From INSIDE web1, ping web2 by name", "xp": 25,
             "hint": "docker exec -it web1 sh — then: ping web2",
             "check": lambda w: w.flags.get("ping_ok") in (("web1", "web2"), ("web2", "web1"))},
        ],
        "teach": [
            "`bad address 'name'` on the default bridge is the lesson, not a bug: containers there "
            "get connectivity but no DNS, so only raw IPs work — and those change on every restart.",
            "The default bridge has NO name-resolution — user-defined networks add container-name DNS.",
            "--network at run-time wires a container into the network at birth.",
            "Name-based discovery is how services find each other — the frontend will find 'rabbitmq' exactly like this.",
        ],
        "solution": [
            "docker run -d --name bridge1 nginx:alpine",
            "docker run -d --name bridge2 nginx:alpine",
            "docker exec -it bridge1 sh",
            "ping bridge2", "exit",
            "docker network create demo-net",
            "docker run -d --name web1 --network demo-net nginx:alpine",
            "docker run -d --name web2 --network demo-net nginx:alpine",
            "docker exec -it web1 sh",
            "ping web2", "exit",
        ],
    },
    {
        "id": "docker-04",
        "topic": "docker",
        "title": "Ship It ⚓ — Yariv's REAL Assignment 1",
        "vault_note": "Class 02 - Docker Networking and Images",
        "brief": ("This mission mirrors the actual graded assignment: package the provided\n"
                  "app.py (a tiny Python web server on port 8080) into an image YOU design,\n"
                  "then publish it to Docker Hub. Remember the assignment's rules: no\n"
                  "copy-pasted Dockerfile — you must understand every instruction.\n"
                  "(cat app.py to see what you're packaging.)"),
        "world": {
            "images": ["python:3.11-slim"],
            "files": {"app.py": BASE_APP},
        },
        "handlers": [(r"docker\s+(?:images|image\s+(?:ls|list))\b.*", _watch_images)],
        "objectives": [
            {"desc": "Write a Dockerfile: base image, workdir, copy app.py, expose 8080, start command", "xp": 30,
             "hint": "edit Dockerfile — you need FROM, WORKDIR, COPY, EXPOSE, CMD. Which base image fits a Python app?",
             "check": lambda w: all(k in w.files.get("Dockerfile", "").upper()
                                    for k in ("FROM", "COPY", "EXPOSE", "CMD"))
                                and "8080" in w.files.get("Dockerfile", "")},
            {"desc": "Build the image with a meaningful name", "xp": 20,
             "hint": "docker build -t <name> .",
             "check": lambda w: w.flags.get("built")},
            {"desc": "Verify the image really exists locally", "xp": 10,
             "hint": "The assignment says 'verify it exists locally' — the command that lists "
                     "the images on your machine.",
             "check": lambda w: w.flags.get("built") in w.flags.get("_images_seen", set())},
            {"desc": "Tag it for Docker Hub: <username>/<repo>", "xp": 15,
             "hint": "Hub images need a namespace: docker tag <local> <dockerhub-user>/<repo>  "
                     "(or build with that name directly).",
             "check": lambda w: any("/" in img for img in w.images if not img.startswith("python"))},
            {"desc": "Log in to Docker Hub (with an access token!)", "xp": 10,
             "hint": "docker login — and remember: the 'password' should be an ACCESS TOKEN.",
             "check": lambda w: w.flags.get("logged_in")},
            {"desc": "Push your image to the public registry", "xp": 25,
             "hint": "docker push <username>/<repo> — pushing ALWAYS requires login, even for public repos.",
             "check": lambda w: w.flags.get("pushed_remote")},
        ],
        "teach": [
            "FROM→WORKDIR→COPY→EXPOSE→CMD: base image, folder, code in, port documented, process to run.",
            "`docker build -t <name> .` — the -t names the output, the dot is the build context.",
            "`docker images` is how you check the build produced what you think it did — repository, "
            "tag, ID and SIZE. 'It printed no error' is not the same as 'the image is there'.",
            "Registry images are namespaced <user>/<repo> — that's ownership, not decoration.",
            "Log in with an ACCESS TOKEN: revocable and scoped; your real password never touches a terminal.",
            "Public = anyone can PULL; only the verified owner can PUSH. That's why login is non-negotiable.",
        ],
        "solution": [
            "cat app.py",
            "edit Dockerfile",
            "FROM python:3.11-slim", "WORKDIR /app", "COPY app.py .",
            "EXPOSE 8080", 'CMD ["python", "app.py"]', ".",
            "docker build -t hello-docker .",
            "docker images",
            "docker tag hello-docker student123/hello-docker",
            "docker login", "student123", "my-access-token",
            "docker push student123/hello-docker",
        ],
    },
    {
        "id": "docker-05",
        "topic": "docker",
        "title": "Build It Twice 🧱",
        "vault_note": "Class 02 - Docker Networking and Images",
        "brief": ("Your team's Flask service builds — and every build takes forever, because\n"
                  "`pip install` runs again for every one-line code change. Same Dockerfile,\n"
                  "same laptop, 12 seconds of waiting you don't have to spend.\n\n"
                  "Build it, build it AGAIN, and watch what Docker says the second time.\n"
                  "Then break the cache on purpose, fix the instruction ORDER, put the image\n"
                  "on a diet, and finally publish it — the app listens on 9090 and your\n"
                  "users expect http://localhost:8080, so mind which side of -p is which.\n"
                  "(start with: cat Dockerfile)"),
        "world": {
            "files": {
                "Dockerfile": SLOW_DOCKERFILE,
                "requirements.txt": "flask",
                "app.py": FLASK_APP,
            },
        },
        "handlers": [
            (r"docker\s+build\b.*", _watch_build),
            (r"docker\s+(?:images|image\s+(?:ls|list))\b.*", _watch_images),
        ],
        "objectives": [
            {"desc": "Build the image and tag it", "xp": 15,
             "hint": "docker build -t flask-app .  — and don't forget the trailing dot.",
             "check": lambda w: bool(w.flags.get("built"))},
            {"desc": "Build it AGAIN without changing a thing — every step should say CACHED",
             "xp": 10,
             "hint": "Run the exact same build command a second time and read the => lines.",
             "check": lambda w: bool(w.flags.get("cache_all"))},
            {"desc": "Change one line of app.py, rebuild, and watch the cache break", "xp": 15,
             "hint": 'echo "# tweak" >> app.py  (or edit app.py), then build again. Note WHICH '
                     "step stops saying CACHED — and what re-runs below it.",
             "check": lambda w: bool(w.flags.get("cache_partial"))},
            {"desc": "Put it on a diet: compare python:3 with python:3-slim, then rebuild "
                     "FROM the small one", "xp": 20,
             "hint": "docker pull python:3-slim, then docker images and read the SIZE column. "
                     "Change the FROM line and build again.",
             "check": lambda w: ({"python:3", "python:3-slim"} <= w.flags.get("_images_seen", set())
                                 and any("slim" in meta.get("base", "")
                                         for meta in w.flags.get("_img_meta", {}).values()))},
            {"desc": "Reorder the Dockerfile — deps in and installed BEFORE the code — then "
                     "prove it: change app.py again and rebuild with pip still CACHED", "xp": 25,
             "hint": "COPY requirements.txt first, RUN pip install, THEN COPY app.py. Rebuild once "
                     "to seed the new order, tweak app.py, and rebuild again.",
             "check": lambda w: (_deps_first(w.files.get("Dockerfile", ""))
                                 and bool(w.flags.get("cache_kept_install")))},
            {"desc": "Publish it: the app listens on 9090, your users want localhost:8080", "xp": 20,
             "hint": "docker run -d -p 8080:9090 --name api flask-app — left is the HOST port, "
                     "right is the CONTAINER port. Then: curl localhost:8080",
             "check": lambda w: (w.flags.get("curl_ok")
                                 and w.flags.get("curl_ok") == _published(w, "8080", "9090"))},
        ],
        "teach": [
            "A build turns instructions into layers: one filesystem diff per step, stacked into "
            "an image. `-t` names the result; the dot is the build context Docker may COPY from.",
            "Docker photographs every finished step. Nothing changed → nothing rebuilds → the "
            "second build is instant. That reuse is the whole point of layers.",
            "A layer's cache key is the instruction PLUS what it reads — so editing app.py "
            "invalidates the COPY that reads it, and every step BELOW rebuilds too. The cascade "
            "is downward only.",
            "python:3 is ~1GB, python:3-slim ~150MB. Smaller base = faster pulls and pushes, less "
            "disk, smaller attack surface. Slim strips system libs, so occasionally you install "
            "a build dependency back.",
            "COPY requirements.txt → RUN install → COPY code. Dependencies change rarely, code "
            "changes constantly: put the slow, stable step ABOVE the fast, volatile one and a "
            "code edit stops costing you a re-install.",
            "-p is HOST:CONTAINER — left is your machine, right is what the app inside actually "
            "listens on. EXPOSE only documents the port; -p is what wires it up.",
        ],
        "solution": [
            "cat Dockerfile",
            "docker build -t flask-app .",
            "docker build -t flask-app .",
            'echo "# tweak: friendlier home page" >> app.py',
            "docker build -t flask-app .",
            "docker pull python:3-slim",
            "docker images",
            "edit Dockerfile",
            "FROM python:3-slim",
            "WORKDIR /usr/src",
            "COPY requirements.txt /usr/src/",
            "RUN pip install -r requirements.txt",
            "COPY app.py /usr/src/",
            "EXPOSE 9090",
            'CMD ["python3", "app.py"]',
            ".",
            "docker build -t flask-app .",
            "docker images",
            'echo "# tweak: nicer 404 page" >> app.py',
            "docker build -t flask-app .",
            "docker run -d -p 8080:9090 --name api flask-app",
            "docker ps",
            "curl localhost:8080",
        ],
    },
]
