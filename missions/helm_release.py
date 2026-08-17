"""Helm missions — a small, honest Helm: chart + values in, YAML out.

Helm isn't simulated by the engine; this module ships a mission-local handler
(the house rule: promote to engine only when 2+ missions need it).

Nothing here is hardcoded per mission. `helm template` really reads the chart
out of `world.files`, really layers `values.yaml < -f file < --set` in that
order, and really renders `{{ .Values.x }}` / `{{ .Release.Name }}` into YAML.
Install and upgrade then hand that rendered text to the ENGINE's own manifest
parser — the same code path `kubectl apply -f` goes through — which makes the
class deck's punchline mechanical rather than decorative: *Kubernetes only ever
receives YAML; the cluster never knows Helm exists.* Change a value and the
deployment changes because the rendered manifest changed, not because a regex
spotted `--set replicaCount=`.

Deliberate omissions, said out loud instead of faked: no `helm create`
scaffolding, no chart repositories (`helm repo add` wants a network this world
hasn't got), no hooks or `helm test`, and the template dialect is placeholders
plus the `quote`/`default`/`upper`/`lower` pipes — `{{ if }}`, `{{ range }}` and
`{{ include }}` are REFUSED with a message rather than silently mis-rendered.
"""
import copy
import re
import shlex

from engine import (TOOL_VERSION_LINES, _k8s_apply_doc, _parse_manifests,
                    _reconcile, _table, c)

# --------------------------------------------------------- the class-6 chart --
CHART_YAML = '''apiVersion: v2
name: my-service
description: A simple web service deployment
version: 0.1.0
appVersion: "1.25"
'''

VALUES_YAML = '''replicaCount: 2
image:
  repository: nginx
  tag: "1.25"
  pullPolicy: IfNotPresent
service:
  type: ClusterIP
  port: 80
'''

# `{{ .Release.Name }}-deploy`, not `my-service-deploy`: the note's own quiz
# question is why a resource carries the RELEASE name and not the chart name.
DEPLOY_TPL = '''apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ .Release.Name }}-deploy
spec:
  replicas: {{ .Values.replicaCount }}
  selector:
    matchLabels:
      app: {{ .Release.Name }}
  template:
    metadata:
      labels:
        app: {{ .Release.Name }}
    spec:
      containers:
        - name: my-service
          image: "{{ .Values.image.repository }}:{{ .Values.image.tag }}"
          ports:
            - containerPort: {{ .Values.service.port }}
'''

SERVICE_TPL = '''apiVersion: v1
kind: Service
metadata:
  name: {{ .Release.Name }}-svc
spec:
  type: {{ .Values.service.type }}
  selector:
    app: {{ .Release.Name }}
  ports:
    - port: {{ .Values.service.port }}
      targetPort: {{ .Values.service.port }}
'''

NOTES_TPL = '''Thank you for installing {{ .Chart.Name }}.

Your release is named {{ .Release.Name }}, in namespace {{ .Release.Namespace }}.

  kubectl get all -n {{ .Release.Namespace }}
'''

# ------------------------------------------------- the Assignment-A chart --
# Assignment A hand-writes its chart (it never mentions `helm create`) and pins
# hashicorp/http-echo, whose 0.3.1 → 0.3.0 tags exist precisely so the upgrade
# and the rollback have somewhere to go.
APP_CHART_YAML = '''apiVersion: v2
name: myapp
description: Assignment A — a hand-written chart for a tiny HTTP app
version: 0.1.0
appVersion: "0.3.1"
'''

APP_VALUES_YAML = '''replicaCount: 1
image:
  repository: hashicorp/http-echo
  tag: "0.3.1"
  pullPolicy: IfNotPresent
message: "Hello from Helm"
service:
  type: ClusterIP
  port: 5678
secret:
  apiToken: "changeme"
'''

# The environment file. Base values stay conservative (Assignment B, part B);
# everything a dev cluster wants louder lives here.
APP_VALUES_DEV_YAML = '''replicaCount: 4
message: "Hello from DEV"
service:
  type: NodePort
  port: 5678
'''

APP_DEPLOY_TPL = '''apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ .Release.Name }}-deployment
spec:
  replicas: {{ .Values.replicaCount }}
  selector:
    matchLabels:
      app: {{ .Release.Name }}
  template:
    metadata:
      labels:
        app: {{ .Release.Name }}
    spec:
      containers:
        - name: web
          image: "{{ .Values.image.repository }}:{{ .Values.image.tag }}"
          args: ["-text={{ .Values.message }}"]
          ports:
            - containerPort: {{ .Values.service.port }}
          envFrom:
            - configMapRef:
                name: {{ .Release.Name }}-config
            - secretRef:
                name: {{ .Release.Name }}-secret
'''

APP_SERVICE_TPL = '''apiVersion: v1
kind: Service
metadata:
  name: {{ .Release.Name }}-service
spec:
  type: {{ .Values.service.type }}
  selector:
    app: {{ .Release.Name }}
  ports:
    - port: {{ .Values.service.port }}
      targetPort: {{ .Values.service.port }}
'''

APP_CONFIGMAP_TPL = '''apiVersion: v1
kind: ConfigMap
metadata:
  name: {{ .Release.Name }}-config
data:
  message: {{ .Values.message | quote }}
  service-port: "{{ .Values.service.port }}"
'''

APP_SECRET_TPL = '''apiVersion: v1
kind: Secret
metadata:
  name: {{ .Release.Name }}-secret
type: Opaque
stringData:
  api-token: {{ .Values.secret.apiToken | quote }}
'''

APP_NOTES_TPL = '''{{ .Release.Name }} is deployed in namespace {{ .Release.Namespace }}.

  kubectl get all -n {{ .Release.Namespace }}
  kubectl get configmap,secret -n {{ .Release.Namespace }}

The app answers with: {{ .Values.message }}
'''


# ------------------------------------------------------------- YAML, lite --
def _strip_comment(line):
    """Drop a trailing `# comment` — but only outside quotes, or the note's own
    `schedule: "* * * * *"` would lose its stars to a `#` that never came."""
    out, quote = [], None
    for ch in line:
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            out.append(ch)
        elif ch == "#" and (not out or out[-1] in " \t"):
            break
        else:
            out.append(ch)
    return "".join(out)


def _split_flow(text):
    """Comma-split a `{a: 1, b: 2}` / `[x, y]` body, respecting nesting."""
    parts, buf, depth, quote = [], "", 0, None
    for ch in text:
        if quote:
            buf += ch
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote, buf = ch, buf + ch
        elif ch in "[{":
            depth, buf = depth + 1, buf + ch
        elif ch in "]}":
            depth, buf = depth - 1, buf + ch
        elif ch == "," and depth == 0:
            parts.append(buf)
            buf = ""
        else:
            buf += ch
    parts.append(buf)
    return [p for p in parts if p.strip()]


def _scalar(tok):
    """One YAML scalar — or a flow map/list, which is how the class's
    `requests: { cpu: 200m }` is written."""
    tok = tok.strip()
    if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in "\"'":
        return tok[1:-1]
    if tok.startswith("{") and tok.endswith("}"):
        return {k.strip(): _scalar(v) for k, _, v in
                (p.partition(":") for p in _split_flow(tok[1:-1]))}
    if tok.startswith("[") and tok.endswith("]"):
        return [_scalar(p) for p in _split_flow(tok[1:-1])]
    if re.fullmatch(r"-?\d+", tok):
        return int(tok)
    if tok in ("true", "false"):
        return tok == "true"
    if tok in ("null", "~", ""):
        return None
    return tok


def _yaml_load(text):
    """The subset of YAML a values file needs: nested maps, scalars, flow
    collections. Values files are DATA — parsing them for real is what lets
    `-f values-dev.yaml` change the render instead of a regex pretending it did."""
    root = {}
    stack = [(-1, root)]
    for raw in (text or "").splitlines():
        line = _strip_comment(raw).rstrip()
        if not line.strip() or line.lstrip().startswith("-"):
            continue                     # top-level lists aren't a values-file shape
        indent = len(line) - len(line.lstrip())
        key, sep, val = line.strip().partition(":")
        if not sep:
            continue
        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        key = key.strip()
        if val.strip() == "":
            child = parent[key] if isinstance(parent.get(key), dict) else {}
            parent[key] = child
            stack.append((indent, child))
        else:
            parent[key] = _scalar(val)
    return root


def _quoted(val):
    """A string that would read back as a number or a bool keeps its quotes:
    `tag: 1.25` and `tag: "1.25"` are different values, and the class note spends
    a gotcha on exactly that."""
    if isinstance(val, str) and (re.fullmatch(r"-?\d+(\.\d+)?", val)
                                 or val in ("true", "false", "null", "")):
        return f'"{val}"'
    return val


def _yaml_dump(data, indent=0):
    """Print values back the way `helm get values` does."""
    pad, out = " " * indent, []
    for key in sorted(data):
        val = data[key]
        if isinstance(val, dict):
            out.append(f"{pad}{key}:")
            out.append(_yaml_dump(val, indent + 2))
        elif isinstance(val, bool):
            out.append(f"{pad}{key}: {str(val).lower()}")
        elif isinstance(val, list):
            out.append(f"{pad}{key}: [{', '.join(str(_quoted(v)) for v in val)}]")
        else:
            out.append(f"{pad}{key}: {_quoted(val)}")
    return "\n".join(x for x in out if x)


def _merge(base, over):
    """Deep merge — a later values file overrides key by key, it does not
    replace the whole tree (that's why `-f values-dev.yaml` can set only
    replicaCount and leave the image alone)."""
    out = dict(base)
    for key, val in over.items():
        out[key] = (_merge(out[key], val)
                    if isinstance(val, dict) and isinstance(out.get(key), dict) else val)
    return out


def _set_path(values, path, val):
    """`--set image.tag=0.3.0` — walk the dotted path, creating maps as needed."""
    node, parts = values, path.split(".")
    for part in parts[:-1]:
        if not isinstance(node.get(part), dict):
            node[part] = {}
        node = node[part]
    node[parts[-1]] = val


def _split_sets(raw):
    """helm splits one --set on commas; `\\,` keeps a literal comma."""
    parts, buf, esc = [], "", False
    for ch in raw:
        if esc:
            buf, esc = buf + ch, False
        elif ch == "\\":
            esc = True
        elif ch == ",":
            parts.append(buf)
            buf = ""
        else:
            buf += ch
    parts.append(buf)
    return [p for p in parts if p.strip()]


# ------------------------------------------------------ template rendering --
class _RenderError(Exception):
    """A template this small engine can't honestly render."""


_MISSING = object()
_PLACEHOLDER = re.compile(r"(\s*)\{\{(-?)\s*(.*?)\s*(-?)\}\}(\s*)", re.S)


def _lookup(ctx, path):
    node = ctx
    for part in path.strip(".").split("."):
        if not isinstance(node, dict) or part not in node:
            return _MISSING
        node = node[part]
    return node


def _text(val):
    if val is _MISSING or val is None:
        return "<no value>"          # what Go's templates print for a nil key
    if isinstance(val, bool):
        return "true" if val else "false"
    return str(val)


def _render_expr(expr, ctx):
    stages = [s.strip() for s in expr.split("|")]
    head = stages[0]
    if not head.startswith("."):
        raise _RenderError(f"{{{{ {expr} }}}}")
    val = _lookup(ctx, head)
    for stage in stages[1:]:
        name, _, arg = stage.partition(" ")
        if name == "quote":
            val = '"%s"' % _text(val)
        elif name == "default":
            val = _scalar(arg) if val in (_MISSING, None, "") else val
        elif name == "upper":
            val = _text(val).upper()
        elif name == "lower":
            val = _text(val).lower()
        else:
            raise _RenderError(f"{{{{ {expr} }}}}")
    return _text(val)


def _render(text, ctx):
    def sub(m):
        lead, ltrim, expr, rtrim, trail = m.groups()
        return (("" if ltrim else lead) + _render_expr(expr, ctx)
                + ("" if rtrim else trail))
    return _PLACEHOLDER.sub(sub, text)


# ------------------------------------------------------------ chart access --
def _norm_path(path):
    path = path.strip()
    if path.startswith("./"):
        path = path[2:]
    return path.rstrip("/")


def _chart_dirs(world):
    return sorted(k[:-len("/Chart.yaml")] for k in world.files if k.endswith("/Chart.yaml"))


def _load_chart(world, path):
    """A chart is a folder with a Chart.yaml — exactly as on disk."""
    root = _norm_path(path)
    if f"{root}/Chart.yaml" not in world.files:
        return None
    meta = _yaml_load(world.files[f"{root}/Chart.yaml"])
    return {
        "dir": root,
        "meta": meta,
        "name": meta.get("name", root.rsplit("/", 1)[-1]),
        "version": str(meta.get("version", "0.1.0")),
        "appVersion": str(meta.get("appVersion", "")),
        "values": _yaml_load(world.files.get(f"{root}/values.yaml", "")),
        "templates": sorted(k for k in world.files
                            if k.startswith(root + "/templates/") and k.endswith((".yaml", ".yml"))),
        "notes": world.files.get(f"{root}/templates/NOTES.txt", ""),
    }


def _context(chart, release, ns, values):
    return {"Release": {"Name": release, "Namespace": ns, "Service": "Helm"},
            "Chart": {"Name": chart["name"], "Version": chart["version"],
                      "AppVersion": chart["appVersion"]},
            "Values": values}


# Helm's install order, trimmed to the kinds this world knows. It is not
# cosmetic: a Deployment that mounts a ConfigMap wants the ConfigMap to exist
# first, and printing the documents in this order is what real `helm template`
# does too.
_INSTALL_ORDER = ["Namespace", "ServiceAccount", "Role", "RoleBinding", "ConfigMap", "Secret",
                  "PersistentVolumeClaim", "Service", "DaemonSet", "StatefulSet", "Deployment",
                  "Job", "CronJob", "Ingress", "HorizontalPodAutoscaler"]


def _render_chart(world, chart, release, ns, values):
    """templates + values → one multi-document manifest, `# Source:` lines and
    all. The `# Source` path uses the CHART name, which is how real helm output
    reminds you the chart and the release are two different names."""
    ctx, docs = _context(chart, release, ns, values), []
    for path in chart["templates"]:
        body = _render(world.files[path], ctx).strip("\n")
        if not body:
            continue
        kind = re.search(r"(?m)^kind:\s*([\w-]+)", body)
        order = (_INSTALL_ORDER.index(kind.group(1)) if kind and kind.group(1) in _INSTALL_ORDER
                 else len(_INSTALL_ORDER))
        docs.append((order, f"---\n# Source: {chart['name']}{path[len(chart['dir']):]}\n{body}\n"))
    return "".join(text for _order, text in sorted(docs, key=lambda d: d[0]))


# ----------------------------------------------------------- the cluster --
class _Quiet:
    """helm prints its own summary; the engine's per-object `created` lines are
    kubectl's voice, not helm's."""

    def print(self, *_args, **_kw):
        pass


_QUIET = _Quiet()


def _apply(world, manifest, ns, deleting=False):
    """Hand the rendered YAML to the engine's own apply path — the whole point
    of the exercise is that this is the only thing the cluster ever sees."""
    for doc in _parse_manifests(manifest):
        # A chart's templates here never hardcode metadata.namespace, so the
        # release's namespace is the answer for every document in it.
        doc["ns"] = ns
        if doc.get("image"):
            doc["image"] = doc["image"].strip("\"'")
        _k8s_apply_doc(world, doc, _QUIET, deleting=deleting)
    _reconcile(world)


def _validate(manifest):
    """What the API server would reject after Helm happily rendered it. The class
    note spends a whole gotcha on `replicaCount: "2"`, and it only bites HERE —
    the render is perfectly valid text either way."""
    m = re.search(r"(?m)^\s*replicas:\s*(\S+)", manifest)
    if m and not m.group(1).isdigit():
        return ('Deployment in version "v1" cannot be handled as a Deployment: json: cannot '
                "unmarshal string into Go struct field DeploymentSpec.spec.replicas of type int32",
                "(replicas wants an INT: `--set replicaCount=2` gives one, `replicaCount: \"2\"` "
                "in a values file gives a string. 'It rendered' is not 'it deployed')")
    return None


def _prune(world, old_manifest, new_manifest, ns):
    """A resource the new revision no longer renders is deleted, not orphaned —
    the difference between `helm upgrade` and a pile of `kubectl apply`s."""
    keep = {(d["kind"], d["name"]) for d in _parse_manifests(new_manifest)}
    for doc in _parse_manifests(old_manifest):
        if (doc["kind"], doc["name"]) not in keep:
            doc["ns"] = ns
            _k8s_apply_doc(world, doc, _QUIET, deleting=True)


# --------------------------------------------------------------- releases --
def _releases(world):
    """(namespace, name) -> release. Keyed by BOTH because that is how Helm 3
    scopes a release, and it's why `helm list` in the wrong namespace is empty."""
    return world.flags.setdefault("helm_releases", {})


def _rel(world, name, ns="default"):
    """What a mission's check() asks for: the release, or None."""
    return _releases(world).get((ns, name))


def _rev(world, name, ns="default"):
    rel = _rel(world, name, ns)
    return len(rel["history"]) if rel else 0


def _dep(world, name):
    return ((world.k8s or {}).get("deployments") or {}).get(name, {})


def _saw(world, *flags):
    """Did the player actually run one of these inspections?"""
    return any(world.flags.get(f) for f in flags)


def _other_ns(world, name, ns):
    """The hint for a release the player is hunting in the wrong namespace."""
    found = sorted(k_ns for (k_ns, k_name) in _releases(world)
                   if k_name == name and k_ns != ns)
    if not found:
        return ""
    return (f"(release '{name}' DOES exist — in namespace {', '.join(found)}. Releases are "
            f"namespace-scoped: add -n {found[0]})")


# ---------------------------------------------------------------- the CLI --
_VALUE_FLAGS = {"-f", "--values", "--set", "--set-string", "--set-json", "-n", "--namespace",
                "--timeout", "--description", "-o", "--output", "--version", "--repo",
                "--revision", "--kube-context", "--kubeconfig", "--max", "--keyring"}
_BOOL_FLAGS = {"--create-namespace", "-i", "--install", "--dry-run", "--wait", "--atomic",
               "--debug", "--force", "--reuse-values", "--reset-values", "--no-hooks",
               "--cleanup-on-fail", "--keep-history", "--generate-name", "--devel",
               "-A", "--all-namespaces", "--all", "--short", "--deployed", "--failed"}


def _flags(args):
    """helm's argv split into positionals and the flags this world honours.

    An unrecognized flag is REPORTED, never ignored: silently dropping
    `--set-file` would make a student's override vanish with no error, which is
    the one thing a package manager must not do."""
    pos, opt = [], {"values": [], "sets": [], "ns": "default", "create_ns": False,
                    "install": False, "dry_run": False, "all_ns": False, "all": False,
                    "revision": None, "bad": None}
    i = 0
    while i < len(args):
        arg = args[i]
        name, eq, inline = arg.partition("=")
        if name in _VALUE_FLAGS:
            val = inline if eq else (args[i + 1] if i + 1 < len(args) else "")
            i += 1 if eq else 2
            if name in ("-f", "--values"):
                opt["values"].append(val)
            elif name in ("--set", "--set-string", "--set-json"):
                opt["sets"].append(val)
            elif name in ("-n", "--namespace"):
                opt["ns"] = val
            elif name == "--revision":
                opt["revision"] = val
            continue
        if name in _BOOL_FLAGS:
            if name == "--create-namespace":
                opt["create_ns"] = True
            elif name in ("-i", "--install"):
                opt["install"] = True
            elif name == "--dry-run":
                opt["dry_run"] = True
            elif name in ("-A", "--all-namespaces"):
                opt["all_ns"] = True
            elif name == "--all":
                opt["all"] = True
            i += 1
            continue
        if arg.startswith("-") and arg != "-":
            opt["bad"] = arg
            return pos, opt
        pos.append(arg)
        i += 1
    return pos, opt


def _err(world, io, message, hint=""):
    """helm's error, then the house-style dim lesson. An error is not a move."""
    world.flags["_noop"] = True
    io.print(message)
    if hint:
        io.print(c(hint, "dim"))


def _chart_hint(world):
    charts = _chart_dirs(world)
    return (f"(the chart in this world is ./{charts[0]} — a chart is the FOLDER holding "
            "Chart.yaml, values.yaml and templates/)" if charts else
            "(a chart is a folder holding Chart.yaml, values.yaml and templates/)")


def _collect_values(world, chart, opt):
    """Helm's precedence, in code: values.yaml < -f files (in order) < --set."""
    values, user = copy.deepcopy(chart["values"]), {}
    for path in opt["values"]:
        key = _norm_path(path)
        if key not in world.files:
            return None, None, (f"Error: open {path}: no such file or directory",
                                "(`ls` lists what's actually here — the dev overrides live "
                                "next to the chart, not inside it)")
        loaded = _yaml_load(world.files[key])
        values, user = _merge(values, loaded), _merge(user, loaded)
    for raw in opt["sets"]:
        for pair in _split_sets(raw):
            key, eq, val = pair.partition("=")
            if not eq:
                return None, None, (f'Error: failed parsing --set data: key "{pair}" has no value',
                                    "(the form is --set path.to.key=value, dots and all)")
            _set_path(values, key.strip(), _scalar(val))
            _set_path(user, key.strip(), _scalar(val))
    return values, user, None


def _header(release, ns, status, revision, notes=""):
    head = (f"NAME: {release}\nLAST DEPLOYED: right now\nNAMESPACE: {ns}\n"
            f"STATUS: {status}\nREVISION: {revision}")
    return head + (f"\nNOTES:\n{notes.rstrip()}" if notes.strip() else "")


def _record_render(world, release, chart, opt, values, manifest, named):
    """What the last render actually resolved to — a mission checks THIS, not
    the keystrokes that produced it."""
    world.flags["helm_template"] = True
    world.flags["helm_rendered"] = {
        "release": release, "chart": chart["dir"], "named": named,
        "values_files": [_norm_path(p) for p in opt["values"]],
        "sets": list(opt["sets"]), "values": values, "manifest": manifest,
    }


# ---------------------------------------------------------- subcommands --
def _install_or_upgrade(world, io, sub, pos, opt):
    rel_db, ns = _releases(world), opt["ns"]
    fail = "INSTALLATION FAILED" if sub == "install" else "UPGRADE FAILED"

    if len(pos) < 3:
        if sub == "install" and len(pos) == 2:
            _err(world, io, "Error: must either provide a name or specify --generate-name",
                 "(helm install <RELEASE-NAME> <CHART> — the release name is yours to pick, "
                 "and it's what {{ .Release.Name }} renders to)")
        else:
            _err(world, io, f'Error: "helm {sub}" requires 2 arguments\n\n'
                            f"Usage:  helm {sub} [RELEASE] [CHART] [flags]",
                 _chart_hint(world))
        return
    release, chart_path = pos[1], pos[2]

    chart = _load_chart(world, chart_path)
    if not chart:
        _err(world, io, f'Error: {fail}: path "{chart_path}" not found', _chart_hint(world))
        return

    exists = (ns, release) in rel_db
    if sub == "install" and exists:
        _err(world, io, f"Error: {fail}: cannot re-use a name that is still in use",
             "(that error is exactly what `helm upgrade --install` deletes from your life: "
             "install if absent, upgrade if present — one command, safe on every CI run)")
        return
    if sub == "upgrade" and not exists:
        if not opt["install"]:
            _err(world, io, f'Error: {fail}: "{release}" has no deployed releases',
                 _other_ns(world, release, ns) or
                 "(add --install: `helm upgrade --install` installs when the release is missing. "
                 "That's the form Assignment A uses everywhere)")
            return
        sub, fail = "install", "INSTALLATION FAILED"   # --install: this IS revision 1

    values, user, problem = _collect_values(world, chart, opt)
    if problem:
        _err(world, io, *problem)
        return

    try:
        manifest = _render_chart(world, chart, release, ns, values)
        notes = _render(chart["notes"], _context(chart, release, ns, values))
    except _RenderError as e:
        _err(world, io, f"Error: {fail}: template: {e} is not something this Helm renders",
             "(this world renders {{ .Values.x }}, {{ .Release.Name }}, {{ .Chart.Name }} and the "
             "quote/default/upper/lower pipes — if/range/include are real Helm, just not here)")
        return

    revision = len(rel_db[(ns, release)]["history"]) + 1 if exists else 1
    if opt["dry_run"]:
        _record_render(world, release, chart, opt, values, manifest, named=True)
        io.print(_header(release, ns, "pending-" + sub, revision))
        io.print("HOOKS:\nMANIFEST:")
        io.print(manifest)
        io.print(c("(--dry-run rendered and stopped — nothing was sent to the cluster. Same "
                   "preview as `helm template`, but with the release name filled in for real)", "dim"))
        return

    if not world.k8s or not world.k8s["started"]:
        _err(world, io, f"Error: {fail}: Kubernetes cluster unreachable: "
                        'Get "https://127.0.0.1:8443/version": dial tcp 127.0.0.1:8443: '
                        "connect: connection refused",
             "(helm renders locally, but it still has to SEND the YAML somewhere — "
             "start the cluster first: minikube start)")
        return

    rejected = _validate(manifest)
    if rejected:
        _err(world, io, f"Error: {fail}: {rejected[0]}", rejected[1])
        return

    if ns not in world.k8s["namespaces"]:
        if not opt["create_ns"]:
            _err(world, io, f'Error: {fail}: create: failed to create: namespaces "{ns}" not found',
                 "(namespaces are never auto-created — add --create-namespace, or run "
                 f"`kubectl create namespace {ns}` first)")
            return
        world.k8s["namespaces"].add(ns)

    if exists:
        _prune(world, rel_db[(ns, release)]["history"][-1]["manifest"], manifest, ns)
    _apply(world, manifest, ns)

    record = rel_db.setdefault((ns, release), {"ns": ns, "name": release, "history": []})
    record.update({"chart": chart["name"], "chart_dir": chart["dir"],
                   "version": chart["version"], "appVersion": chart["appVersion"]})
    record["history"].append({
        "values": values, "user": user, "manifest": manifest, "notes": notes,
        "desc": "Install complete" if sub == "install" else "Upgrade complete",
    })
    # NB: no _record_render here. `helm_rendered` means "the player previewed a
    # render" — install renders too, but silently completing a *preview first*
    # objective by skipping the preview would teach the opposite lesson.
    if sub == "install":
        world.flags["helm_installed"] = release
    else:
        world.flags["helm_upgraded"] = len(record["history"])
        io.print(f'Release "{release}" has been upgraded. Happy Helming!')
    io.print(_header(release, ns, "deployed", len(record["history"]), notes))


def _rollback(world, io, pos, opt):
    ns, rel_db = opt["ns"], _releases(world)
    if len(pos) < 2:
        _err(world, io, 'Error: "helm rollback" requires at least 1 argument\n\n'
                        "Usage:  helm rollback <RELEASE> [REVISION] [flags]")
        return
    release = pos[1]
    record = rel_db.get((ns, release))
    if not record:
        _err(world, io, "Error: release: not found", _other_ns(world, release, ns))
        return
    history = record["history"]
    target = int(pos[2]) if len(pos) > 2 and pos[2].isdigit() else max(1, len(history) - 1)
    if not 1 <= target <= len(history):
        _err(world, io, "Error: release: not found",
             f"(revision {target} doesn't exist — this release has 1..{len(history)}. "
             f"`helm history {release}{'' if ns == 'default' else ' -n ' + ns}` lists them)")
        return
    old = history[target - 1]
    _prune(world, history[-1]["manifest"], old["manifest"], ns)
    _apply(world, old["manifest"], ns)
    history.append(dict(old, desc=f"Rollback to {target}"))
    io.print("Rollback was a success! Happy Helming!")
    world.flags["helm_rolled_back"] = target


def _uninstall(world, io, pos, opt):
    ns, rel_db = opt["ns"], _releases(world)
    if len(pos) < 2:
        _err(world, io, 'Error: "helm uninstall" requires at least 1 argument\n\n'
                        "Usage:  helm uninstall RELEASE_NAME [...] [flags]")
        return
    release = pos[1]
    record = rel_db.pop((ns, release), None)
    if not record:
        _err(world, io, f"Error: uninstall: Release not loaded: {release}: release: not found",
             _other_ns(world, release, ns))
        return
    _apply(world, record["history"][-1]["manifest"], ns, deleting=True)
    io.print(f'release "{release}" uninstalled')
    io.print(c("(every resource the release created went with it — the namespace stays, "
               "because Helm doesn't own it)", "dim"))
    world.flags["helm_uninstalled"] = release


def _list(world, io, opt):
    rel_db = _releases(world)
    rows = [[name, ns, str(len(r["history"])), "deployed",
             f"{r['chart']}-{r['version']}", r["appVersion"] or "-"]
            for (ns, name), r in sorted(rel_db.items())
            if opt["all_ns"] or ns == opt["ns"]]
    _table(io, ["NAME", "NAMESPACE", "REVISION", "STATUS", "CHART", "APP VERSION"], rows)
    if not rows:
        io.print(c(f"(nothing in namespace {opt['ns']}" +
                   (" — releases are namespace-scoped: -n <ns> looks elsewhere, "
                    "-A looks everywhere)" if rel_db else " — install something first)"), "dim"))
    world.flags["helm_list"] = True
    world.flags["helm_listed_ns"] = "*" if opt["all_ns"] else opt["ns"]


def _history(world, io, pos, opt):
    if len(pos) < 2:
        _err(world, io, 'Error: "helm history" requires 1 argument\n\n'
                        "Usage:  helm history RELEASE_NAME [flags]")
        return
    release = pos[1]
    record = _releases(world).get((opt["ns"], release))
    if not record:
        _err(world, io, "Error: release: not found", _other_ns(world, release, opt["ns"]))
        return
    history = record["history"]
    rows = [[str(i), "deployed" if i == len(history) else "superseded",
             f"{record['chart']}-{record['version']}", record["appVersion"] or "-", h["desc"]]
            for i, h in enumerate(history, 1)]
    _table(io, ["REVISION", "STATUS", "CHART", "APP VERSION", "DESCRIPTION"], rows)
    io.print(c("(those REVISION numbers are the argument rollback takes — an audit log you "
               "can travel back through)", "dim"))
    world.flags["helm_history"] = release


def _status(world, io, pos, opt):
    if len(pos) < 2:
        _err(world, io, 'Error: "helm status" requires 1 argument')
        return
    record = _releases(world).get((opt["ns"], pos[1]))
    if not record:
        _err(world, io, "Error: release: not found", _other_ns(world, pos[1], opt["ns"]))
        return
    last = record["history"][-1]
    io.print(_header(pos[1], opt["ns"], "deployed", len(record["history"]), last["notes"]))
    world.flags["helm_status"] = pos[1]


def _get(world, io, pos, opt):
    what = pos[1] if len(pos) > 1 else ""
    if what not in ("values", "manifest") or len(pos) < 3:
        _err(world, io, 'Error: "helm get" requires a subcommand and a release\n\n'
                        "Usage:  helm get values|manifest RELEASE_NAME [flags]",
             "(values = what YOU supplied · --all = everything after the merge · "
             "manifest = the YAML the cluster actually got)")
        return
    record = _releases(world).get((opt["ns"], pos[2]))
    if not record:
        _err(world, io, "Error: release: not found", _other_ns(world, pos[2], opt["ns"]))
        return
    # --revision reads an OLD revision — how you diff what changed between two
    # deploys without a cluster archaeologist.
    wanted = int(opt["revision"]) if (opt["revision"] or "").isdigit() else len(record["history"])
    if not 1 <= wanted <= len(record["history"]):
        _err(world, io, "Error: release: not found",
             f"(this release has revisions 1..{len(record['history'])})")
        return
    last = record["history"][wanted - 1]
    if what == "manifest":
        io.print(last["manifest"])
        io.print(c("(this is what the cluster received — no templates, no values, just YAML)", "dim"))
    elif opt["all"]:
        io.print("COMPUTED VALUES:")
        io.print(_yaml_dump(last["values"]))
    else:
        io.print("USER-SUPPLIED VALUES:")
        io.print(_yaml_dump(last["user"]) if last["user"] else "null")
        io.print(c("(only your overrides — the chart's own defaults are behind `--all`. "
                   "That difference IS the precedence chain)", "dim"))
    world.flags["helm_get"] = what


def _show(world, io, pos):
    what = pos[1] if len(pos) > 1 else ""
    if what not in ("values", "chart", "all", "readme") or len(pos) < 3:
        _err(world, io, 'Error: "helm show" requires a subcommand and a chart\n\n'
                        "Usage:  helm show values|chart|all CHART",
             "(`helm show values <chart>` before installing anything is the power move: it "
             "lists every knob you're allowed to override)")
        return
    chart = _load_chart(world, pos[2])
    if not chart:
        _err(world, io, f'Error: path "{pos[2]}" not found', _chart_hint(world))
        return
    if what == "readme":
        io.print(c("(this chart ships no README.md — real charts often do)", "dim"))
        return
    if what in ("chart", "all"):
        io.print(world.files[f"{chart['dir']}/Chart.yaml"].rstrip())
    if what == "all":
        io.print("---")
    if what in ("values", "all"):
        io.print(world.files.get(f"{chart['dir']}/values.yaml", "").rstrip())
    world.flags["helm_show"] = what


def _lint(world, io, pos):
    path = pos[1] if len(pos) > 1 else "."
    chart = _load_chart(world, path)
    io.print(f"==> Linting {path}")
    if not chart:
        io.print("Error: unable to check Chart.yaml file in chart: "
                 f"stat {_norm_path(path)}/Chart.yaml: no such file or directory")
        io.print(c(_chart_hint(world), "dim"))
        io.print("\nError: 1 chart(s) linted, 1 chart(s) failed")
        return
    problems = [f"[ERROR] Chart.yaml: {key} is required"
                for key in ("apiVersion", "name", "version") if not chart["meta"].get(key)]
    try:
        _render_chart(world, chart, "release-name", "default", chart["values"])
    except _RenderError as e:
        problems.append(f"[ERROR] templates/: {e} is not something this Helm renders")
    for line in problems or ["[INFO] Chart.yaml: icon is recommended"]:
        io.print(line)
    io.print("")
    if problems:
        io.print("Error: 1 chart(s) linted, 1 chart(s) failed")
    else:
        io.print("1 chart(s) linted, 0 chart(s) failed")
        io.print(c("(lint is the cheapest gate there is — Assignment B grades `helm lint` "
                   "with zero errors)", "dim"))
    world.flags["helm_lint"] = True


def _template(world, io, pos, opt):
    if len(pos) < 2:
        _err(world, io, 'Error: "helm template" requires at least 1 argument\n\n'
                        "Usage:  helm template [NAME] [CHART] [flags]", _chart_hint(world))
        return
    named = len(pos) > 2
    release, chart_path = (pos[1], pos[2]) if named else ("release-name", pos[1])
    chart = _load_chart(world, chart_path)
    if not chart:
        _err(world, io, f'Error: path "{chart_path}" not found', _chart_hint(world))
        return
    values, _user, problem = _collect_values(world, chart, opt)
    if problem:
        _err(world, io, *problem)
        return
    try:
        manifest = _render_chart(world, chart, release, opt["ns"], values)
    except _RenderError as e:
        _err(world, io, f"Error: template: {e} is not something this Helm renders",
             "(this world renders {{ .Values.x }}, {{ .Release.Name }}, {{ .Chart.Name }} and the "
             "quote/default/upper/lower pipes — if/range/include are real Helm, just not here)")
        return
    io.print(manifest.rstrip())
    _record_render(world, release, chart, opt, values, manifest, named)
    if not named:
        io.print(c("(no release name given, so Helm rendered with the placeholder "
                   "`release-name` — every real command names the release: "
                   f"helm template my-release ./{chart['dir']})", "dim"))
    elif "<no value>" in manifest:
        io.print(c("(`<no value>` = a template asked for a value nothing set. That's how a typo "
                   "in --set shows up — silently, in the YAML)", "dim"))
    else:
        io.print(c("(this is the RENDERED yaml: templates + values, no cluster touched. "
                   "'It rendered' is not 'it deployed')", "dim"))


def _usage(world, io):
    world.flags["_noop"] = True
    io.print("The Kubernetes package manager\n\nUsage:\n  helm [command]\n\n"
             "Available Commands (in this world):")
    for name, blurb in (
            ("template", "render chart + values to YAML locally — no cluster"),
            ("install", "render, apply, and record revision 1 of a new release"),
            ("upgrade", "apply changes to a release (--install = install if missing)"),
            ("rollback", "return a release to a previous revision number"),
            ("history", "every revision of a release"),
            ("list", "releases in a namespace (-A for all of them)"),
            ("status", "one release's current state and NOTES"),
            ("get", "values | manifest — what you supplied, what the cluster got"),
            ("show", "values | chart — read a chart before installing it"),
            ("lint", "check a chart for problems"),
            ("uninstall", "remove a release and everything it created"),
            ("version", "the client version")):
        io.print(f"  {name:<11}{blurb}")
    io.print(c("\nFlags this world honours: -f/--values · --set · -n/--namespace · "
               "--create-namespace · --install · --dry-run · -A", "dim"))


# The real tools that DO exist but can't be honest here. Saying so beats faking
# a success the student would then look for on their own machine.
_UNSIMULATED = {
    "create": ("helm create scaffolds a chart with best-practice boilerplate "
               "(Chart.yaml, values.yaml, templates/deployment.yaml, service.yaml, "
               "serviceaccount.yaml, hpa.yaml, ingress.yaml, NOTES.txt, _helpers.tpl).",
               "This world hands you a hand-written chart instead — which is what "
               "Assignment A grades: it never mentions `helm create`. Read it with "
               "`helm show values <chart>`."),
    "repo": ("helm repo add/update talks to a chart repository over HTTPS.",
             "There's no network in this simulated world. On your own machine the bonus "
             "step is: helm repo add bitnami https://charts.bitnami.com/bitnami"),
    "package": ("helm package tars a chart directory into chart-0.1.0.tgz for a repository.",
                "Not simulated — but remember it bumps nothing: the version in Chart.yaml "
                "is what ends up in the filename."),
    "dependency": ("helm dependency update pulls the subcharts declared in Chart.yaml into "
                   "charts/.", "Not simulated. That's Assignment B's parent chart: api, "
                   "worker and cache as dependencies, each with a condition."),
    "test": ("helm test runs the chart's test hooks against a live release.",
             "Hooks aren't simulated here — Assignment B asks for two of them."),
    "pull": ("helm pull downloads a chart from a repository or an OCI registry.",
             "No network here. Local paths (./my-service) are the only charts this world has."),
}


def _helm(world, m, io):
    try:
        argv = shlex.split(m.group(0))
    except ValueError:
        argv = m.group(0).split()
    args = argv[1:]

    if not args or args[0] in ("help", "--help", "-h"):
        _usage(world, io)
        return

    sub = args[0]
    if sub in ("version", "--version", "-v"):
        # The check the lab opens with. The catch-all regex used to swallow it and
        # answer with a chart error — check-first is a habit the game teaches
        # everywhere else, so it has to work here too.
        world.flags["_noop"] = True
        io.print(TOOL_VERSION_LINES["helm"] if "--short" not in args else "v3.15.2+g1a500d5")
        io.print(c("(it answered → helm is installed. That's the check that belongs "
                   "before any `helm install`)", "dim"))
        return

    if sub in _UNSIMULATED:
        world.flags["_noop"] = True
        head, follow = _UNSIMULATED[sub]
        io.print(f"🌍 `helm {sub}` isn't simulated here. {head}")
        io.print(c("   " + follow, "dim"))
        return

    pos, opt = _flags(args)
    if opt["bad"]:
        _err(world, io, f'Error: unknown flag: {opt["bad"]}',
             "(this world honours -f/--values, --set, -n/--namespace, --create-namespace, "
             "--install, --dry-run and -A. Real helm has many more — `helm <cmd> --help` "
             "on your own machine lists them)")
        return

    if sub == "template":
        _template(world, io, pos, opt)
    elif sub in ("install", "upgrade"):
        _install_or_upgrade(world, io, sub, pos, opt)
    elif sub == "rollback":
        _rollback(world, io, pos, opt)
    elif sub in ("list", "ls"):
        _list(world, io, opt)
    elif sub == "history":
        _history(world, io, pos, opt)
    elif sub == "status":
        _status(world, io, pos, opt)
    elif sub == "get":
        _get(world, io, pos, opt)
    elif sub == "show":
        _show(world, io, pos)
    elif sub == "lint":
        _lint(world, io, pos)
    elif sub == "uninstall":
        _uninstall(world, io, pos, opt)
    else:
        _err(world, io, f'Error: unknown command "{sub}" for "helm"',
             "(run `helm help` for the commands this world answers)")


MISSIONS = [
    {
        "id": "helm-01",
        "topic": "helm",
        "title": "Package It ⎈ — install, upgrade, roll back",
        "vault_note": "Class 06 - Helm",
        "brief": ("Raw YAML doesn't scale — Helm charts do. The class-6 chart ./my-service\n"
                  "is here: Chart.yaml, values.yaml and templates/ (ls · cat my-service/values.yaml\n"
                  "· helm show values ./my-service). Render it, install it as a release named\n"
                  "'demo', watch it become real pods, upgrade it with --set, then use Helm's\n"
                  "killer feature — roll a bad release back in one command — and clean up."),
        "world": {
            "k8s": {"started": True},
            "files": {
                "my-service/Chart.yaml": CHART_YAML,
                "my-service/values.yaml": VALUES_YAML,
                "my-service/templates/deployment.yaml": DEPLOY_TPL,
                "my-service/templates/service.yaml": SERVICE_TPL,
                "my-service/templates/NOTES.txt": NOTES_TPL,
            },
        },
        "handlers": [
            (r"helm(\s.*)?", _helm),
        ],
        "objectives": [
            {"desc": "Render the chart locally FIRST — and name the release while you do it", "xp": 10,
             "hint": "helm template demo ./my-service — NAME comes first, then the chart path. "
                     "Templates + values → YAML, no cluster touched.",
             "check": lambda w: w.flags.get("helm_rendered", {}).get("named")},
            {"desc": "Install the chart as a release named 'demo'", "xp": 20,
             "hint": "helm install demo ./my-service",
             "check": lambda w: _rel(w, "demo") is not None},
            {"desc": "See it in Helm's own registry of releases", "xp": 5,
             "hint": "helm list — NAME, REVISION, STATUS, CHART.",
             "check": lambda w: w.flags.get("helm_list") and _rel(w, "demo")},
            {"desc": "Verify the release became real pods (2 of them)", "xp": 10,
             "hint": "kubectl get pods — helm is just a factory for k8s objects.",
             "check": lambda w: _saw(w, "get_pods", "get_all")
                                and sum(1 for p in w.k8s["pods"].values()
                                        if p.get("deploy") == "demo-deploy") >= 2},
            {"desc": "Upgrade the release to 4 replicas using --set", "xp": 20,
             "hint": "helm upgrade demo ./my-service --set replicaCount=4  "
                     "(values.yaml is the default; --set overrides it)",
             "check": lambda w: _rev(w, "demo") >= 2 and _dep(w, "demo-deploy").get("replicas") == 4},
            {"desc": "Read the release history before you touch anything", "xp": 10,
             "hint": "helm history demo — every install and upgrade is a numbered revision.",
             "check": lambda w: w.flags.get("helm_history") == "demo"},
            {"desc": "Something's wrong with rev 2 — ROLL BACK to revision 1", "xp": 20,
             "hint": "helm rollback demo 1 — then kubectl get pods to see the replica count snap back.",
             "check": lambda w: w.flags.get("helm_rolled_back") == 1
                                and _dep(w, "demo-deploy").get("replicas") == 2},
            {"desc": "Lab's over — uninstall the release and leave nothing behind", "xp": 10,
             "hint": "helm uninstall demo — then kubectl get all to prove the deployment AND "
                     "the service are gone.",
             # "gone" is true before you ever install, so the flag the uninstall
             # itself records is what separates a finished lab from move one.
             "check": lambda w: w.flags.get("helm_uninstalled") == "demo"
                                and _rel(w, "demo") is None and "demo-deploy" not in w.k8s["deployments"]
                                and "demo-svc" not in w.k8s["services"]},
        ],
        "teach": [
            "helm template renders locally — and the release name is an ARGUMENT, which is why "
            "every resource can carry it.",
            "install = render + apply + record REVISION 1. A release is a chart instance with a history.",
            "helm list is Helm's own registry: the cluster stores release records, but only helm reads them.",
            "Helm is a factory for ordinary k8s objects — after install it's just deployments, "
            "services and pods.",
            "--set overrides values.yaml from the CLI — same chart, different knobs per environment.",
            "helm history turns deploys into an audit log: every change has a number you can name.",
            "rollback doesn't rewind time — it re-applies the OLD values as a NEW revision.",
            "uninstall removes every resource the release created in one operation — that is what "
            "packaging an app buys you.",
        ],
        "solution": [
            "helm show values ./my-service",
            "helm template demo ./my-service",
            "helm install demo ./my-service",
            "helm list",
            "kubectl get pods",
            "helm upgrade demo ./my-service --set replicaCount=4",
            "helm history demo",
            "helm rollback demo 1",
            "kubectl get pods",
            "helm uninstall demo",
            "kubectl get all",
        ],
    },
    {
        "id": "helm-02",
        "topic": "helm",
        "title": "Ship It To Dev ⎈ — values files, namespaces, tags",
        "vault_note": "Class 06 - Helm",
        "brief": ("Assignment A ships ONE chart and deploys it with ONE command:\n"
                  "helm upgrade --install. The chart is ./charts/myapp; the dev environment's\n"
                  "overrides live in values-dev.yaml (cat it — then helm show values ./charts/myapp\n"
                  "to see what it's overriding). Deploy it into a namespace that doesn't exist\n"
                  "yet, prove which values source wins, bump the image tag from 0.3.1 to 0.3.0,\n"
                  "then roll it back. Release name: myapp."),
        "world": {
            "k8s": {"started": True},
            "files": {
                "charts/myapp/Chart.yaml": APP_CHART_YAML,
                "charts/myapp/values.yaml": APP_VALUES_YAML,
                "charts/myapp/templates/deployment.yaml": APP_DEPLOY_TPL,
                "charts/myapp/templates/service.yaml": APP_SERVICE_TPL,
                "charts/myapp/templates/configmap.yaml": APP_CONFIGMAP_TPL,
                "charts/myapp/templates/secret.yaml": APP_SECRET_TPL,
                "charts/myapp/templates/NOTES.txt": APP_NOTES_TPL,
                "values-dev.yaml": APP_VALUES_DEV_YAML,
            },
        },
        "handlers": [
            (r"helm(\s.*)?", _helm),
        ],
        "objectives": [
            {"desc": "Preview the DEV render — same chart, the dev values file", "xp": 10,
             "hint": "helm template myapp ./charts/myapp -f values-dev.yaml — read the replicas "
                     "and the message, then compare with the render without -f.",
             "check": lambda w: w.flags.get("helm_rendered", {}).get("values_files")
                                and w.flags["helm_rendered"]["values"].get("replicaCount") == 4},
            {"desc": "Deploy to a namespace that doesn't exist yet — with the idempotent form", "xp": 20,
             "hint": "helm upgrade --install myapp ./charts/myapp -n dev --create-namespace "
                     "-f values-dev.yaml",
             "check": lambda w: _rel(w, "myapp", "dev") and "dev" in w.k8s["namespaces"]
                                and _dep(w, "myapp-deployment").get("replicas") == 4},
            {"desc": "Verify it from BOTH sides: helm's release list and the cluster itself", "xp": 15,
             "hint": "helm list -n dev  (plain `helm list` looks in default and finds nothing), "
                     "then kubectl get all -n dev",
             "check": lambda w: w.flags.get("helm_listed_ns") in ("dev", "*")
                                and _saw(w, "get_all", "get_pods")},
            {"desc": "The same chart also shipped a ConfigMap and a Secret — find them", "xp": 10,
             "hint": "kubectl get configmap,secret -n dev — `get all` is a category that leaves "
                     "them out.",
             "check": lambda w: _saw(w, "get_configmap_secret", "get_secret_configmap",
                                     "get_configmap", "get_secret")
                                and ("myapp-config", "dev") in w.k8s["objects"].get("ConfigMap", set())
                                and ("myapp-secret", "dev") in w.k8s["objects"].get("Secret", set())},
            {"desc": "Prove the precedence: pin it to 2 replicas without editing a file", "xp": 15,
             "hint": "helm upgrade --install myapp ./charts/myapp -n dev -f values-dev.yaml "
                     "--set replicaCount=2 — the file says 4, --set outranks it.",
             "check": lambda w: _rev(w, "myapp", "dev") >= 2
                                and _dep(w, "myapp-deployment").get("replicas") == 2},
            {"desc": "Ship image tag 0.3.0 — without touching a single line of YAML", "xp": 20,
             "hint": "helm upgrade myapp ./charts/myapp -n dev -f values-dev.yaml "
                     "--set image.tag=0.3.0",
             "check": lambda w: _dep(w, "myapp-deployment").get("image") == "hashicorp/http-echo:0.3.0"},
            {"desc": "Read the history — Assignment A wants this output saved", "xp": 10,
             "hint": "helm history myapp -n dev",
             "check": lambda w: w.flags.get("helm_history") == "myapp"},
            {"desc": "Roll back to revision 1 — the image reverts to 0.3.1", "xp": 20,
             "hint": "helm rollback myapp 1 -n dev — then kubectl describe deploy -n dev to see "
                     "the image.",
             "check": lambda w: w.flags.get("helm_rolled_back") == 1
                                and _dep(w, "myapp-deployment").get("image") == "hashicorp/http-echo:0.3.1"},
            {"desc": "Clean up: uninstall the release from its namespace", "xp": 15,
             "hint": "helm uninstall myapp -n dev — the -n is not optional, releases are "
                     "namespace-scoped.",
             "check": lambda w: w.flags.get("helm_uninstalled") == "myapp"
                                and _rel(w, "myapp", "dev") is None
                                and "myapp-deployment" not in w.k8s["deployments"]},
        ],
        "teach": [
            "-f layers a whole values file over the chart's defaults: one chart, one file per "
            "environment, zero copy-paste.",
            "upgrade --install is idempotent — installs when absent, upgrades when present. That's "
            "why CI runs it on every commit, and why Assignment A uses nothing else.",
            "Two different truths: `helm list` reads release records, `kubectl get` reads the "
            "cluster. A release is deployed only when both agree.",
            "One chart ships every kind the app needs — and `kubectl get all` is a category, so "
            "ConfigMaps and Secrets are outside it. Ask for those by name.",
            "Precedence: values.yaml < -f file < --set. If an override 'isn't taking', something "
            "higher up the chain is winning.",
            "An image tag lives in values, never in the manifests — but --set lives for ONE "
            "command: the next upgrade re-reads the files. Permanent changes belong in a file.",
            "helm history is the release's audit log, and its REVISION numbers are what rollback "
            "takes as an argument.",
            "rollback re-applies a numbered revision — the fastest undo in Kubernetes, and it "
            "touches no YAML at all.",
            "uninstall removes the release's resources but NOT the namespace it created — Helm "
            "only owns what it rendered.",
        ],
        "solution": [
            "cat values-dev.yaml",
            "helm show values ./charts/myapp",
            "helm template myapp ./charts/myapp -f values-dev.yaml",
            "helm upgrade --install myapp ./charts/myapp -n dev --create-namespace -f values-dev.yaml",
            "helm list -n dev",
            "kubectl get all -n dev",
            "kubectl get configmap,secret -n dev",
            "helm upgrade --install myapp ./charts/myapp -n dev -f values-dev.yaml --set replicaCount=2",
            "helm upgrade myapp ./charts/myapp -n dev -f values-dev.yaml --set image.tag=0.3.0",
            "helm history myapp -n dev",
            "helm rollback myapp 1 -n dev",
            "helm get values myapp -n dev",
            "helm uninstall myapp -n dev",
        ],
    },
]
