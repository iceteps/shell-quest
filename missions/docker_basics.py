"""Docker missions — mirror the course's Docker classes AND Yariv's real
'Docker Basics – Assignment 1' (build → tag → login with token → push)."""
from engine import c

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


MISSIONS = [
    {
        "id": "docker-01",
        "topic": "docker",
        "title": "Hello, Container 🐳",
        "vault_note": "Class 01 - Docker Basics",
        "brief": ("First day with Docker. Get the ubuntu image, start a container you can\n"
                  "work in, go INSIDE it, leave a file as proof you were there, and confirm\n"
                  "the container is running. (Exactly what class did — from memory.)\n\n"
                  "🌍 Real-world setup (already done for you here): Docker itself arrives via\n"
                  "   Docker Desktop — GUI installer from docker.com, or a package manager\n"
                  "   (winget/brew/apt). Once installed, 'downloading an image' is Docker's\n"
                  "   own job, from the Docker Hub registry:\n"
                  "   CLI: docker pull <image>   ·   GUI: Docker Desktop → search → Pull"),
        "world": {},
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
             "hint": "While inside: touch file1.txt — then `exit` to come home.",
             "check": lambda w: w.inside is None and any("file1.txt" in d["files"] for d in w.containers.values())},
            {"desc": "Verify your container is running", "xp": 5,
             "hint": "The command that lists RUNNING containers.",
             "check": lambda w: w.flags.get("ps")},
        ],
        "teach": [
            "Images download once and cache locally — `pull` fetches from a registry (Docker Hub by default). "
            "Docker Desktop's GUI can pull too, but the CLI is the muscle worth building.",
            "-d runs detached (background); -it keeps an interactive terminal alive — bash would exit instantly without it.",
            "`exec` enters a RUNNING container; `run` would have created a brand-new one.",
            "A container's filesystem is its own little world — files you create live (and die) with it.",
            "`docker ps` = running only; add -a and stopped containers appear too.",
        ],
        "solution": [
            "docker pull ubuntu:latest",
            "docker run -dit --name devops1 ubuntu bash",
            "docker exec -it devops1 bash",
            "touch file1.txt", "exit",
            "docker ps",
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
                  "will find 'rabbitmq' later in the course. Create a user-defined network,\n"
                  "put two nginx containers on it, and prove name-resolution works."),
        "world": {"images": ["nginx:alpine"]},
        "objectives": [
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
            "The default bridge has NO name-resolution — user-defined networks add container-name DNS.",
            "--network at run-time wires a container into the network at birth.",
            "Name-based discovery is how services find each other — the frontend will find 'rabbitmq' exactly like this.",
        ],
        "solution": [
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
        "objectives": [
            {"desc": "Write a Dockerfile: base image, workdir, copy app.py, expose 8080, start command", "xp": 30,
             "hint": "edit Dockerfile — you need FROM, WORKDIR, COPY, EXPOSE, CMD. Which base image fits a Python app?",
             "check": lambda w: all(k in w.files.get("Dockerfile", "").upper()
                                    for k in ("FROM", "COPY", "EXPOSE", "CMD"))
                                and "8080" in w.files.get("Dockerfile", "")},
            {"desc": "Build the image with a meaningful name", "xp": 20,
             "hint": "docker build -t <name> .",
             "check": lambda w: w.flags.get("built")},
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
            "docker tag hello-docker student123/hello-docker",
            "docker login", "student123", "my-access-token",
            "docker push student123/hello-docker",
        ],
    },
]
