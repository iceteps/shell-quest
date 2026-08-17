"""Terraform missions — the lifecycle (init → plan → apply → destroy) and the
day-2 half of the job: variables, outputs, a change diff you can actually read,
and moving the state ledger into an S3 backend.

Mission-local handlers (the house rule: promote to engine only when 2+ missions
need it). Everything is derived from the real files: the .tf sources live in
`world.files` and are PARSED — Terraform reads every `*.tf` in the folder and
merges them, so this does too. The ledger lives in `world.flags["tf_state"]`
and is rendered to `terraform.tfstate` so `cat` can show what state really is.

Deliberate limits, so nothing here fakes success: no modules, no `count` /
`for_each`, no `data` sources, no expressions beyond `var.x` and
`TYPE.NAME.ATTR` references, and only the s3 backend is implemented. Anything
else answers in Terraform's own voice instead of pretending.
"""
import copy
import json
import re
import shlex
import zlib

from engine import TOOL_VERSION_LINES, c

# ------------------------------------------------------------------ parsing --
# A deliberately small HCL reader: enough for provider / resource / variable /
# output / terraform-backend blocks, which is the whole vocabulary of class 12.

_HEAD = re.compile(r'^(?P<kind>[A-Za-z_][\w-]*)(?P<labels>(?:\s+"[^"]*")*)\s*=?\s*\{$')
_ASSIGN = re.compile(r'^(?P<key>[A-Za-z_][\w-]*)\s*=\s*(?P<val>.+?)\s*$')
_REF = re.compile(r'\b(aws_[a-z0-9_]+)\.([A-Za-z_][\w-]*)\.([A-Za-z_][\w.-]*)')
_VAR = re.compile(r'\bvar\.([A-Za-z_][\w-]*)')


def _decomment(text):
    """Strip `#` / `//` comments — but only outside quotes, so a CIDR or a URL
    survives. The whole backend.tf drill is 'uncomment this', so comments have
    to be genuinely invisible to the parser."""
    out = []
    for line in text.splitlines():
        cut, quoted, i = None, False, 0
        while i < len(line):
            ch = line[i]
            if ch == '"':
                quoted = not quoted
            elif not quoted and (ch == "#" or line.startswith("//", i)):
                cut = i
                break
            i += 1
        out.append(line if cut is None else line[:cut])
    return "\n".join(out)


def _tf_files(files):
    return sorted(n for n in files if n.endswith(".tf"))


def _merged(files):
    """Filenames are for humans; the engine sees one merged configuration."""
    return "\n".join(files[n] for n in _tf_files(files))


def _parse(files):
    """Parse every .tf file in the folder into one config dict.

    Returns: resources {addr: {attr: raw_value}}, order (declaration order),
    variables, outputs, provider, backend, dupes, and src/attr_src so error
    messages can name the file and line the way Terraform does.
    """
    cfg = {"resources": {}, "order": [], "variables": {}, "outputs": {},
           "provider": {}, "backend": None, "dupes": [], "src": {}, "attr_src": {},
           "unbalanced": None}
    for fname in _tf_files(files):
        stack = []          # (dict-to-fill, attribute-prefix, owner-label)
        for lineno, raw in enumerate(_decomment(files[fname]).splitlines(), 1):
            line = raw.strip()
            if not line:
                continue
            if line.startswith("}"):
                if stack:
                    stack.pop()
                else:
                    cfg["unbalanced"] = cfg["unbalanced"] or (fname, lineno)
                continue
            # `variable "size" {}` is legal HCL: the block exists, it just has
            # nothing in it. Open it and close it again.
            line = re.sub(r"\{\s*\}$", "{}", line)
            empty_block = line.endswith("{}")
            if empty_block:
                line = line[:-1].rstrip()
            head = _HEAD.match(line)
            if head:
                kind = head.group("kind")
                labels = re.findall(r'"([^"]*)"', head.group("labels"))
                if not stack:
                    if kind == "resource" and len(labels) == 2:
                        addr = f"{labels[0]}.{labels[1]}"
                        if addr in cfg["resources"]:
                            cfg["dupes"].append((addr, fname, lineno))
                        else:
                            cfg["order"].append(addr)
                        cfg["src"][addr] = (fname, lineno)
                        stack.append((cfg["resources"].setdefault(addr, {}), "", addr))
                    elif kind == "variable" and labels:
                        cfg["src"][f"var.{labels[0]}"] = (fname, lineno)
                        stack.append((cfg["variables"].setdefault(labels[0], {}), "",
                                      f'variable "{labels[0]}"'))
                    elif kind == "output" and labels:
                        cfg["src"][f"output.{labels[0]}"] = (fname, lineno)
                        stack.append((cfg["outputs"].setdefault(labels[0], {}), "",
                                      f'output "{labels[0]}"'))
                    elif kind == "provider":
                        stack.append((cfg["provider"], "", "provider"))
                    else:                      # terraform {} and anything exotic
                        stack.append(({}, "", kind))
                else:
                    parent, prefix, owner = stack[-1]
                    if kind == "backend" and labels and owner == "terraform":
                        cfg["backend"] = {"type": labels[0]}
                        cfg["src"]["backend"] = (fname, lineno)
                        stack.append((cfg["backend"], "", "backend"))
                    else:
                        # A nested block's attributes are flattened as
                        # `ingress.cidr_blocks` — one readable key per setting.
                        stack.append((parent, f"{prefix}{kind}.", owner))
                if empty_block:
                    stack.pop()
                continue
            assign = _ASSIGN.match(line)
            if assign and stack:
                d, prefix, owner = stack[-1]
                key = prefix + assign.group("key")
                d[key] = assign.group("val")
                # the un-stripped line, because error output quotes it verbatim
                cfg["attr_src"][(owner, key)] = (fname, lineno, raw.rstrip())
        if stack and cfg["unbalanced"] is None:
            cfg["unbalanced"] = (fname, len(files[fname].splitlines()))
    return cfg


def _config(world):
    return _parse(world.files)


def _fingerprint(world):
    """Identifies the configuration a plan was read against. A plan you ran
    before an edit says nothing about the code you have now."""
    return zlib.crc32(_merged(world.files).encode()) & 0xFFFFFFFF


# ------------------------------------------------------------ values & refs --
def _unq(value):
    v = (value or "").strip()
    return v[1:-1] if len(v) >= 2 and v[0] == v[-1] == '"' else v


def _var_values(world, cfg, line, io):
    """Variable precedence, low → high: `default` → terraform.tfvars → -var.
    A variable with no value anywhere makes real Terraform stop and ask — so
    this asks too, rather than inventing one."""
    vals, asked = {}, world.flags.setdefault("tf_var_answers", {})
    tfvars = _parse_tfvars(world.files.get("terraform.tfvars", ""))
    cli = dict(re.findall(r'-var\s+["\']?([\w-]+)=([^"\'\s]+)', line))
    for name, block in cfg["variables"].items():
        if name in cli:
            vals[name] = f'"{cli[name]}"'
        elif name in tfvars:
            vals[name] = tfvars[name]
        elif "default" in block:
            vals[name] = block["default"]
        elif name in asked:
            vals[name] = asked[name]
        else:
            io.print(f"var.{name}")
            answer = io.input("  Enter a value: ").strip()
            vals[name] = f'"{answer}"'
            asked[name] = vals[name]
            io.print(c("(no default, no tfvars, no -var → Terraform has to ask. Kept for the "
                       "rest of this mission; the real CLI asks on EVERY run, which is why "
                       "pipelines set TF_VAR_* or pass -var-file)", "dim"))
    return vals


def _parse_tfvars(text):
    out = {}
    for line in _decomment(text).splitlines():
        m = _ASSIGN.match(line.strip())
        if m:
            out[m.group("key")] = m.group("val")
    return out


def _sub_vars(value, vals):
    return _VAR.sub(lambda m: vals.get(m.group(1), f"var.{m.group(1)}"), value)


def _resolved(cfg, addr, vals):
    """Desired attributes: variables substituted, resource references left
    symbolic — a reference only changes when the CODE changes, which is exactly
    what the diff should notice."""
    return {k: _sub_vars(v, vals) for k, v in cfg["resources"][addr].items()}


def _refs(value):
    return {f"{m.group(1)}.{m.group(2)}" for m in _REF.finditer(value or "")}


def _deps(attrs):
    out = set()
    for v in attrs.values():
        out |= _refs(v)
    return out


def _dep_order(attrs_by_addr, addrs):
    """Terraform never takes your file order: `vpc_id = aws_vpc.main.id` IS the
    edge in its dependency graph, and the graph decides who is built first."""
    pending, out = list(addrs), []
    while pending:
        ready = [a for a in pending
                 if not (_deps(attrs_by_addr.get(a, {})) & set(pending)) - {a}]
        if not ready:                      # a cycle — Terraform refuses those
            return out + pending, True
        for a in ready:
            out.append(a)
            pending.remove(a)
    return out, False


# ------------------------------------------------------------------- state --
_ID_PREFIX = {"aws_vpc": "vpc-", "aws_subnet": "subnet-", "aws_security_group": "sg-",
              "aws_instance": "i-", "aws_internet_gateway": "igw-",
              "aws_route_table": "rtb-", "aws_key_pair": "key-", "aws_eip": "eipalloc-"}
_ARN_SERVICE = {"aws_s3_bucket": ("s3", ""), "aws_instance": ("ec2", "instance"),
                "aws_vpc": ("ec2", "vpc"), "aws_subnet": ("ec2", "subnet"),
                "aws_security_group": ("ec2", "security-group")}


def _hexish(seed, n):
    h, out = zlib.crc32(seed.encode()) & 0xFFFFFFFF, ""
    while len(out) < n:
        out += f"{h:08x}"
        h = zlib.crc32(out.encode()) & 0xFFFFFFFF
    return out[:n]


def _fake_id(kind, addr):
    return f"{_ID_PREFIX.get(kind, kind.replace('aws_', '') + '-')}0{_hexish(addr, 16)}"


def _fake_ip(addr):
    h = zlib.crc32(addr.encode()) & 0xFFFFFFFF
    return f"54.{h % 200 + 20}.{h // 200 % 250}.{h // 50000 % 250}"


def _state(world):
    """The ledger. World() only shallow-copies a mission's flags, so a mission
    that SEEDS state (day-2 missions start on a stack someone already applied)
    would otherwise share — and slowly corrupt — one dict across every replay."""
    if not world.flags.get("_tf_owned"):
        world.flags["tf_state"] = copy.deepcopy(world.flags.get("tf_state", {}))
        world.flags["tf_outputs"] = copy.deepcopy(world.flags.get("tf_outputs", {}))
        world.flags["_tf_owned"] = True
    return world.flags["tf_state"]


def _region(cfg, vals):
    return _unq(_sub_vars(cfg["provider"].get("region", '"eu-west-1"'), vals)) or "eu-west-1"


def _create(state, cfg, addr, vals):
    """Write one resource into the ledger, including the attributes only the
    cloud can tell you (id, ip, arn) — the ones that are in state and will
    never be in your .tf files."""
    kind = addr.split(".")[0]
    attrs = _resolved(cfg, addr, vals)
    computed = {"id": _fake_id(kind, addr)}
    if kind == "aws_s3_bucket":
        computed["id"] = _unq(attrs.get("bucket", '""')) or computed["id"]
    if kind == "aws_instance":
        computed["public_ip"] = _fake_ip(addr)
        computed["private_ip"] = f"10.0.1.{zlib.crc32(addr.encode()) % 200 + 20}"
    service, restype = _ARN_SERVICE.get(kind, ("ec2", kind.replace("aws_", "")))
    computed["arn"] = (f"arn:aws:{service}:{_region(cfg, vals)}:123456789012:"
                       + (f"{restype}/" if restype else "") + computed["id"])
    state[addr] = {"attrs": attrs, "computed": computed}
    return state[addr]


def _known(state, expr):
    """Resolve `aws_vpc.main.id` against the ledger. None = not built yet, which
    Terraform prints as (known after apply)."""
    expr = (expr or "").strip()
    m = _REF.fullmatch(expr)
    if not m:
        return _unq(expr) if expr and not _REF.search(expr) else None
    addr, attr = f"{m.group(1)}.{m.group(2)}", m.group(3)
    entry = state.get(addr)
    if not entry:
        return None
    if attr in entry["computed"]:
        return entry["computed"][attr]
    if attr in entry["attrs"]:
        return _unq(entry["attrs"][attr])
    return None


def _display(state, value):
    """How one attribute value prints inside a plan."""
    if _REF.search(value or ""):
        known = _known(state, value)
        return f'"{known}"' if known else c("(known after apply)", "dim")
    return value


def _expand(state, value):
    """Attributes are kept SYMBOLIC in state so the diff only fires when the
    code changes — but what state really holds is the resolved id, and that is
    what `state show` and terraform.tfstate must show."""
    def one(m):
        known = _known(state, m.group(0))
        return f'"{known}"' if known else m.group(0)
    return _REF.sub(one, value or "")


def _state_doc(state, outputs, serial):
    return json.dumps({
        "version": 4,
        "terraform_version": "1.9.5",
        "serial": serial,
        "outputs": {n: {"value": v, "type": "string"} for n, v in sorted(outputs.items())},
        "resources": [{"mode": "managed", "type": addr.split(".")[0], "name": addr.split(".")[1],
                       "instances": [{"attributes": dict(
                           {k: _unq(_expand(state, v)) for k, v in entry["attrs"].items()},
                           **entry["computed"])}]}
                      for addr, entry in sorted(state.items())],
    }, indent=2) + "\n"


def _save_state(world):
    """Terraform writes the ledger to disk after every apply. Here that means a
    real file the player can `cat` — and a real reason .gitignore exists."""
    world.flags["tf_serial"] = world.flags.get("tf_serial", 0) + 1
    doc = _state_doc(_state(world), world.flags.get("tf_outputs", {}), world.flags["tf_serial"])
    backend = world.flags.get("tf_backend")
    if backend:
        buckets = world.flags.setdefault("aws_buckets", {})
        bucket = buckets.setdefault(backend["bucket"], {"region": backend["region"], "keys": {}})
        bucket["keys"][backend["key"]] = doc
    else:
        world.files["terraform.tfstate"] = doc


# ----------------------------------------------------------- config errors --
def _config_errors(world, cfg):
    """What `validate` reports and what `plan`/`apply` refuse to run past. Real
    Terraform checks the config before it ever calls AWS — so a typo in an edit
    fails HERE, loudly, instead of silently doing nothing."""
    errs = []
    if cfg["unbalanced"]:
        fname, lineno = cfg["unbalanced"]
        errs.append(("Unclosed configuration block",
                     f'  on {fname} line {lineno}:',
                     "There is no closing brace for this block before the end of the file."))
    for addr, fname, lineno in cfg["dupes"]:
        rtype, name = addr.split(".")
        errs.append((f'Duplicate resource "{rtype}" configuration',
                     f'  on {fname} line {lineno}:',
                     f'A {rtype} resource named "{name}" was already declared. '
                     "Resource names must be unique per type in each module."))
    for addr in cfg["order"]:
        for key, value in cfg["resources"][addr].items():
            fname, lineno, text = cfg["attr_src"].get((addr, key), ("main.tf", 0, ""))
            where = (f'  on {fname} line {lineno}, in resource "{addr.split(".")[0]}" '
                     f'"{addr.split(".")[1]}":\n{lineno:>4}: {text}')
            for ref in sorted(_refs(value)):
                if ref not in cfg["resources"]:
                    rtype, name = ref.split(".")
                    errs.append(("Reference to undeclared resource", where,
                                 f'A managed resource "{rtype}" "{name}" has not been declared '
                                 "in the root module."))
            for var in sorted(set(_VAR.findall(value))):
                if var not in cfg["variables"]:
                    errs.append(("Reference to undeclared input variable", where,
                                 f'An input variable with the name "{var}" has not been declared. '
                                 'This variable can be declared with a variable "'
                                 f'{var}" {{}} block.'))
    for name, block in cfg["outputs"].items():
        if "value" not in block:
            errs.append(("Missing required argument",
                         f'  on {cfg["src"].get(f"output.{name}", ("outputs.tf", 0))[0]}, '
                         f'in output "{name}":',
                         'The argument "value" is required, but no definition was found.'))
            continue
        for ref in sorted(_refs(block["value"])):
            if ref not in cfg["resources"]:
                rtype, rname = ref.split(".")
                errs.append(("Reference to undeclared resource",
                             f'  in output "{name}":',
                             f'A managed resource "{rtype}" "{rname}" has not been declared '
                             "in the root module."))
    return errs


def _print_errors(world, io, errs):
    for title, where, detail in errs:
        io.print(c("╷", "red"))
        io.print(c("│ Error: ", "red") + title)
        io.print(c("│", "red"))
        for line in where.splitlines():
            io.print(c("│ ", "red") + line)
        io.print(c("│", "red"))
        io.print(c("│ ", "red") + detail)
        io.print(c("╵", "red"))
    io.print(c("(the config never reached AWS — Terraform parses and checks it first, so a typo "
               "costs you nothing but the error above)", "dim"))
    world.flags["_noop"] = True


def _backend_ready(world, cfg, io):
    """Declaring a backend and not re-initializing is the classic day-2 trap."""
    want, have = cfg["backend"], world.flags.get("tf_backend")
    if not want:
        return True
    same = have and all(have.get(k) == _unq(want.get(k, "")) for k in ("bucket", "key", "region"))
    if same:
        return True
    io.print(c("╷", "red"))
    io.print(c("│ Error: ", "red") + 'Backend initialization required: please run "terraform init"')
    io.print(c("│", "red"))
    io.print(c("│ ", "red") + f'Reason: Initial configuration of the requested backend "{want["type"]}"')
    io.print(c("│", "red"))
    io.print(c("│ ", "red") + 'The "backend" is the interface that Terraform uses to store state,')
    io.print(c("│ ", "red") + "perform operations, etc. If this message is showing up, it means that the")
    io.print(c("│ ", "red") + "Terraform configuration you're using is using a custom configuration for")
    io.print(c("│ ", "red") + "the Terraform backend.")
    io.print(c("╵", "red"))
    io.print(c("(you changed WHERE state lives — terraform init is what moves it there)", "dim"))
    world.flags["_noop"] = True
    return False


# -------------------------------------------------------------- the diff --
def _diff(world, cfg, vals):
    """The three buckets every plan and apply is built from."""
    state = _state(world)
    desired = {addr: _resolved(cfg, addr, vals) for addr in cfg["order"]}
    add = [a for a in cfg["order"] if a not in state]
    change = []
    for addr in cfg["order"]:
        if addr not in state:
            continue
        before = state[addr]["attrs"]
        delta = {k: (before.get(k), v) for k, v in desired[addr].items() if before.get(k) != v}
        delta.update({k: (v, None) for k, v in before.items() if k not in desired[addr]})
        if delta:
            change.append((addr, delta))
    destroy = _destroy_order(state, [a for a in state if a not in desired])
    return desired, add, change, destroy


def _destroy_order(state, addrs):
    """Dependents die first — the subnet before the VPC it lives in."""
    order, _cycle = _dep_order({a: state[a]["attrs"] for a in state}, addrs)
    return list(reversed(order))


def _record_plan(world, add, change, destroy):
    """Remember the last diff the player was SHOWN, tagged with the config it
    was computed from — apply prints one too, so it counts the same way."""
    world.flags["tf_last_plan"] = [_fingerprint(world), len(add), len(change), len(destroy),
                                   [addr for addr, _delta in change]]


def _output_diff(world, cfg, state):
    """Outputs live in STATE, not in code — which is why declaring one changes
    nothing until you apply."""
    current = world.flags.get("tf_outputs", {})
    wanted = {name: _known(state, block.get("value", '""'))
              for name, block in cfg["outputs"].items()}
    changes = []
    for name, value in wanted.items():
        if name not in current:
            changes.append(("+", name, value))
        elif current[name] != value and value is not None:
            changes.append(("~", name, value))
    changes += [("-", name, None) for name in current if name not in wanted]
    return wanted, changes


def _print_plan(world, io, state, add, change, destroy, out_changes, desired):
    symbols = ([("+", "create")] if add else []) + ([("~", "update in-place")] if change else []) \
              + ([("-", "destroy")] if destroy else [])
    if symbols:
        io.print("Terraform used the selected providers to generate the following execution")
        io.print("plan. Resource actions are indicated with the following symbols:")
        for sym, word in symbols:
            io.print(f"  {sym} {word}")
        io.print("\nTerraform will perform the following actions:\n")
    for addr in add:
        rtype, name = addr.split(".")
        io.print(c(f"  # {addr} will be created", "green"))
        io.print(c(f'  + resource "{rtype}" "{name}" {{', "green"))
        width = max([len(k) for k in desired[addr]] + [2])
        for key, value in desired[addr].items():
            io.print(c(f"      + {key.ljust(width)} = ", "green") + _display(state, value))
        io.print(c(f"      + {'id'.ljust(width)} = ", "green") + c("(known after apply)", "dim"))
        io.print(c("    }\n", "green"))
    for addr, delta in change:
        rtype, name = addr.split(".")
        io.print(c(f"  # {addr} will be updated in-place", "yellow"))
        io.print(c(f'  ~ resource "{rtype}" "{name}" {{', "yellow"))
        width = max([len(k) for k in list(desired[addr]) + list(delta)] + [2])
        for key, value in desired[addr].items():
            if key not in delta:
                io.print(c(f"        {key.ljust(width)} = {value}", "dim"))
        for key, (before, after) in delta.items():
            arrow = f"{before} -> {after}" if after is not None else f"{before} -> null"
            io.print(c(f"      ~ {key.ljust(width)} = {arrow}", "yellow"))
        io.print(c("    }\n", "yellow"))
    for addr in destroy:
        rtype, name = addr.split(".")
        entry = state[addr]
        io.print(c(f"  # {addr} will be destroyed", "red"))
        io.print(c(f'  - resource "{rtype}" "{name}" {{', "red"))
        shown = {k: _expand(state, v) for k, v in entry["attrs"].items()}
        shown["id"] = f'"{entry["computed"]["id"]}"'
        width = max([len(k) for k in shown] + [2])
        for key, value in shown.items():
            io.print(c(f"      - {key.ljust(width)} = {value} -> null", "red"))
        io.print(c("    }\n", "red"))
    io.print(f"Plan: {len(add)} to add, {len(change)} to change, {len(destroy)} to destroy.")
    if out_changes:
        io.print("\nChanges to Outputs:")
        width = max(len(n) for _s, n, _v in out_changes)
        for sym, name, value in out_changes:
            shown = f'"{value}"' if value is not None else c("(known after apply)", "dim")
            colour = {"+": "green", "~": "yellow", "-": "red"}[sym]
            io.print(c(f"  {sym} {name.ljust(width)} = ", colour)
                     + (shown if sym != "-" else c("null", "red")))


def _warn_open_ssh(world, cfg, io):
    """Not a Terraform warning — ours. `plan` is where a human still has time to
    notice, which is the entire argument for reading it."""
    for addr in cfg["order"]:
        attrs = cfg["resources"][addr]
        if addr.split(".")[0] != "aws_security_group":
            continue
        for key, value in attrs.items():
            if key.endswith("cidr_blocks") and "0.0.0.0/0" in value:
                port = attrs.get(key.rsplit(".", 1)[0] + ".from_port", "")
                if key.startswith("ingress") and port.strip() in ("22", "3389", ""):
                    io.print(c(f"(⚠ {addr} lets 0.0.0.0/0 reach port {port or '?'} — that is the "
                               "whole internet knocking on SSH. Scope it to your own IP, "
                               'e.g. ["203.0.113.4/32"])', "dim"))
                    return


# --------------------------------------------------------------- terraform --
def _tf_init(world, cfg, io):
    if not _tf_files(world.files):
        io.print(c("╷", "red"))
        io.print(c("│ Error: ", "red") + "No configuration files")
        io.print(c("│", "red"))
        io.print(c("│ ", "red") + "Terraform did not find any .tf files in this directory.")
        io.print(c("╵", "red"))
        world.flags["_noop"] = True
        return
    io.print("\nInitializing the backend...")
    if cfg["backend"] and not _init_backend(world, cfg, io):
        return
    if world.flags.get("tf_init"):
        io.print("\nInitializing provider plugins...")
        io.print("- Reusing previous version of hashicorp/aws from the dependency lock file")
        io.print("- Using previously-installed hashicorp/aws v5.54.0")
    else:
        io.print("\nInitializing provider plugins...")
        io.print("- Finding latest version of hashicorp/aws...")
        io.print("- Installing hashicorp/aws v5.54.0...")
        io.print("- Installed hashicorp/aws v5.54.0 (signed by HashiCorp)")
    io.print(c("\nTerraform has been successfully initialized!", "green"))
    io.print('\nYou may now begin working with Terraform. Try running "terraform plan" to see')
    io.print("any changes that are required for your infrastructure.")
    world.flags["tf_init"] = True


def _init_backend(world, cfg, io):
    """`terraform init` is also the command that MOVES state. The migration
    prompt is the real one, including the answer that throws the ledger away."""
    want = cfg["backend"]
    if want["type"] != "s3":
        io.print(c("╷", "red"))
        io.print(c("│ Error: ", "red") + f'Unsupported backend type: {want["type"]}')
        io.print(c("╵", "red"))
        io.print(c(f"(this lab simulates the s3 backend only — the one class 12 uses)", "dim"))
        world.flags["_noop"] = True
        return False
    missing = [k for k in ("bucket", "key", "region") if k not in want]
    if missing:
        io.print(c("╷", "red"))
        io.print(c("│ Error: ", "red") + "Missing required argument")
        io.print(c("│", "red"))
        io.print(c("│ ", "red") + f'The argument "{missing[0]}" is required, but was not set.')
        io.print(c("╵", "red"))
        io.print(c('(an s3 backend needs all three: bucket = where, key = the path of the state '
                   "file inside it, region = which region the bucket is in)", "dim"))
        world.flags["_noop"] = True
        return False
    bucket, key, region = (_unq(want["bucket"]), _unq(want["key"]), _unq(want["region"]))
    buckets = world.flags.setdefault("aws_buckets", {})
    if bucket not in buckets:
        io.print(c("╷", "red"))
        io.print(c("│ Error: ", "red") + "Failed to get existing workspaces: Unable to list objects "
                 f'in S3 bucket "{bucket}":')
        io.print(c("│ ", "red") + "operation error S3: ListObjectsV2, https response error StatusCode: 404, "
                                 "api error NoSuchBucket: The specified bucket does not exist")
        io.print(c("╵", "red"))
        io.print(c("(Terraform does not create the bucket for you — chicken and egg: state has to "
                   f"live somewhere before there is state. Make it first:\n"
                   f"    aws s3api create-bucket --bucket {bucket} --region {region} "
                   f"--create-bucket-configuration LocationConstraint={region})", "dim"))
        world.flags["_noop"] = True
        return False
    have = world.flags.get("tf_backend")
    if have and (have["bucket"], have["key"]) == (bucket, key):
        io.print(f'Successfully configured the backend "s3"! Terraform will automatically')
        io.print("use this backend unless the backend configuration changes.")
        return True
    state = _state(world)
    if state:
        io.print("Do you want to copy existing state to the new backend?")
        io.print('  Pre-existing state was found while migrating the previous "local" backend to the')
        io.print('  newly configured "s3" backend. No existing state was found in the newly')
        io.print('  configured "s3" backend. Do you want to copy this state to the new "s3"')
        io.print('  backend? Enter "yes" to copy and "no" to start with an empty state.\n')
        answer = io.input("  Enter a value: ").strip()
        if answer == "no":
            world.flags["tf_state"] = {}
            world.flags["tf_outputs"] = {}
            io.print(c(f'\n(you answered "no": the new backend starts EMPTY. Those {len(state)} '
                       "resources still exist in AWS, but Terraform no longer knows about them — "
                       "the next apply would build them all over again. `terraform import` is the "
                       "way back.)", "yellow"))
        elif answer != "yes":
            io.print(c("\nError: Invalid value. Must be \"yes\" or \"no\".", "red"))
            world.flags["_noop"] = True
            return False
    world.flags["tf_backend"] = {"type": "s3", "bucket": bucket, "key": key, "region": region}
    buckets[bucket].setdefault("keys", {})
    local = world.files.pop("terraform.tfstate", None)
    if local is not None:
        # the migration leaves the old local ledger behind as a backup, unchanged
        world.files["terraform.tfstate.backup"] = local
    _save_state(world)
    io.print(c('\nSuccessfully configured the backend "s3"!', "green")
             + " Terraform will automatically")
    io.print("use this backend unless the backend configuration changes.")
    io.print(c(f"(the ledger now lives at s3://{bucket}/{key} — one shared truth for the whole "
               "team, lockable so two applies can't race. The local copy is only a backup now.)",
               "dim"))
    world.flags["tf_migrated"] = True
    return True


def _cycle_error(world, desired, io):
    """Terraform refuses a graph that loops, and it refuses it while planning —
    before anything is built."""
    order, cycle = _dep_order(desired, list(desired))
    if not cycle:
        return False
    io.print(c("╷", "red"))
    io.print(c("│ Error: ", "red") + "Cycle: " + ", ".join(order))
    io.print(c("╵", "red"))
    io.print(c("(two resources reference each other — a dependency graph has to be a one-way "
               "street. Break the loop, or split the attribute into its own resource)", "dim"))
    world.flags["_noop"] = True
    return True


def _tf_plan(world, cfg, vals, io):
    state = _state(world)
    desired, add, change, destroy = _diff(world, cfg, vals)
    if _cycle_error(world, desired, io):
        return
    _wanted, out_changes = _output_diff(world, cfg, state)
    _record_plan(world, add, change, destroy)
    world.flags["_noop"] = True                    # plan changes nothing. That IS the lesson.
    if not (add or change or destroy):
        if out_changes:
            io.print("Changes to Outputs:")
            for sym, name, value in out_changes:
                shown = f'"{value}"' if value is not None else c("(known after apply)", "dim")
                io.print(c(f"  {sym} {name} = ", "green") + shown)
            io.print("\nYou can apply this plan to save these new output values to the Terraform")
            io.print("state, without changing any real infrastructure.")
            io.print(c("(outputs live in STATE, not in your .tf files — that's why a brand-new "
                       "output still needs an apply before `terraform output` can print it)", "dim"))
            _warn_open_ssh(world, cfg, io)
            return
        io.print("No changes. Your infrastructure matches the configuration.\n")
        io.print("Terraform has compared your real infrastructure against your configuration and")
        io.print("found no differences, so no changes are needed.")
        io.print(c("(idempotent: the same config applied twice is a no-op. A bash script would "
                   "have run every step again)", "dim"))
        # "No changes" is exactly when a bad rule hides in plain sight
        _warn_open_ssh(world, cfg, io)
        return
    _print_plan(world, io, state, add, change, destroy, out_changes, desired)
    io.print(c('\n(nothing happened yet — plan is the "are you sure?" preview. A - you did not '
               "expect is your warning to stop)", "dim"))
    _warn_open_ssh(world, cfg, io)


def _confirm(io, line, question, prompt_lines):
    if "-auto-approve" in line:
        return True
    io.print(f"\n{question}")
    for extra in prompt_lines:
        io.print(f"  {extra}")
    io.print("  Only 'yes' will be accepted to approve.\n")
    return io.input("  Enter a value: ").strip() == "yes"


def _tf_apply(world, cfg, vals, line, io):
    state = _state(world)
    desired, add, change, destroy = _diff(world, cfg, vals)
    if _cycle_error(world, desired, io):
        return
    _wanted, out_changes = _output_diff(world, cfg, state)
    _record_plan(world, add, change, destroy)      # apply's first act is a plan
    if not (add or change or destroy or out_changes):
        io.print("No changes. Your infrastructure matches the configuration.\n")
        io.print(c("Apply complete! Resources: 0 added, 0 changed, 0 destroyed.", "green"))
        world.flags["_noop"] = True
        return
    if add or change or destroy:
        _print_plan(world, io, state, add, change, destroy, out_changes, desired)
        _warn_open_ssh(world, cfg, io)
    else:
        io.print("Changes to Outputs:")
        for sym, name, value in out_changes:
            io.print(c(f"  {sym} {name} = ", "green")
                     + (f'"{value}"' if value is not None else c("(known after apply)", "dim")))
    if not _confirm(io, line, "Do you want to perform these actions?",
                    ["Terraform will perform the actions described above."]):
        io.print("\nApply cancelled.")
        world.flags["_noop"] = True
        return
    io.print("")
    order, _cycle = _dep_order(desired, add)       # cycles were refused above
    for addr in destroy:                     # already in dependents-first order
        io.print(f"{addr}: Destroying... [id={state[addr]['computed']['id']}]")
        io.print(f"{addr}: Destruction complete after 31s")
        del state[addr]
    for addr, delta in change:
        entry = state[addr]
        io.print(f"{addr}: Modifying... [id={entry['computed']['id']}]")
        entry["attrs"] = desired[addr]
        io.print(f"{addr}: Modifications complete after 4s [id={entry['computed']['id']}]")
    for addr in order:
        io.print(f"{addr}: Creating...")
        entry = _create(state, cfg, addr, vals)
        io.print(f"{addr}: Creation complete after 12s [id={entry['computed']['id']}]")
    outputs = {n: v for n, v in _output_diff(world, cfg, state)[0].items() if v is not None}
    world.flags["tf_outputs"] = outputs
    _save_state(world)
    io.print(c(f"\nApply complete! Resources: {len(add)} added, {len(change)} changed, "
               f"{len(destroy)} destroyed.", "green"))
    if outputs:
        io.print("\nOutputs:\n")
        for name, value in sorted(outputs.items()):
            io.print(f'{name} = "{value}"')
    world.flags["tf_applied"] = len(state)
    if len(add) > 1:
        io.print(c("(you never wrote that order — the references between resources ARE the "
                   "dependency graph, and Terraform walked it for you)", "dim"))


def _tf_destroy(world, cfg, vals, line, io):
    state = _state(world)
    if not state:
        io.print("No changes. No objects need to be destroyed.\n")
        io.print("Either you have not created any objects yet or the existing objects were")
        io.print("already deleted outside of Terraform.")
        world.flags["_noop"] = True
        return
    desired = {addr: entry["attrs"] for addr, entry in state.items()}
    doomed = _destroy_order(state, list(state))
    _record_plan(world, [], [], doomed)
    _print_plan(world, io, state, [], [], doomed, [], desired)
    if not _confirm(io, line, "Do you really want to destroy all resources?",
                    ["Terraform will destroy all your managed infrastructure, as shown above.",
                     "There is no undo."]):
        io.print("\nDestroy cancelled.")
        world.flags["_noop"] = True
        return
    io.print("")
    count = len(state)
    for addr in doomed:
        io.print(f"{addr}: Destroying... [id={state[addr]['computed']['id']}]")
        io.print(f"{addr}: Destruction complete after 28s")
        del state[addr]
    world.flags["tf_outputs"] = {}
    _save_state(world)
    io.print(c(f"\nDestroy complete! Resources: {count} destroyed.", "green"))
    io.print(c("(destroyed in REVERSE dependency order — whatever depends on a thing goes "
               "first, the way a subnet goes before its VPC. Cloud bills don't tick for "
               "resources that don't exist: destroy your labs!)", "dim"))
    world.flags["tf_destroyed"] = True


def _tf_output(world, cfg, args, io):
    world.flags["_noop"] = True
    outputs = world.flags.get("tf_outputs", {})
    raw = "-raw" in args
    as_json = "-json" in args
    names = [a for a in args if not a.startswith("-")]
    if not outputs:
        io.print(c("╷", "yellow"))
        io.print(c("│ Warning: ", "yellow") + "No outputs found")
        io.print(c("│", "yellow"))
        io.print(c("│ ", "yellow") + "The state file either has no outputs defined, or all the defined")
        io.print(c("│ ", "yellow") + "outputs are empty.")
        io.print(c("╵", "yellow"))
        io.print(c("(output reads STATE, not code — declare it in outputs.tf, then apply)", "dim"))
        return
    if names:
        name = names[0]
        if name not in outputs:
            io.print(c("╷", "red"))
            io.print(c("│ Error: ", "red") + "Output \"%s\" not found" % name)
            io.print(c("╵", "red"))
            io.print(c("(`terraform output` with no arguments lists every output in state)", "dim"))
            return
        io.print(outputs[name] if raw else f'"{outputs[name]}"')
        if raw:
            io.print(c("(-raw prints the bare value — the form you pipe into ssh, curl or a "
                       "pipeline variable)", "dim"))
    elif as_json:
        io.print(json.dumps({n: {"value": v, "type": "string"} for n, v in sorted(outputs.items())},
                            indent=2))
    else:
        width = max(len(n) for n in outputs)
        for name, value in sorted(outputs.items()):
            io.print(f'{name.ljust(width)} = "{value}"')
        io.print(c("(these came out of state after apply — the useful facts, without digging "
                   "through the console)", "dim"))
    world.flags["tf_output_read"] = True


def _render_resource(state, addr, entry, io):
    """One resource the way state holds it — attributes resolved, ids included."""
    rtype, name = addr.split(".")
    shown = dict({k: _expand(state, v) for k, v in entry["attrs"].items()},
                 **{k: f'"{v}"' for k, v in entry["computed"].items()})
    width = max(len(k) for k in shown)
    io.print(f"# {addr}:")
    io.print(f'resource "{rtype}" "{name}" {{')
    for key in sorted(shown):
        io.print(f"    {key.ljust(width)} = {shown[key]}")
    io.print("}")


def _tf_show(world, io):
    state, outputs = _state(world), world.flags.get("tf_outputs", {})
    world.flags["_noop"] = True
    if not state:
        io.print("The state file is empty. No resources are represented.")
        return
    io.print("# " + ("s3 backend" if world.flags.get("tf_backend") else "terraform.tfstate")
             + " — the whole ledger:\n")
    for addr in sorted(state):
        _render_resource(state, addr, state[addr], io)
        io.print("")
    if outputs:
        io.print("Outputs:\n")
        for name, value in sorted(outputs.items()):
            io.print(f'{name} = "{value}"')
    io.print(c("(`terraform show` is state in human form; `terraform show -json` is the machine "
               "form pipelines and policy tools read)", "dim"))


def _tf_refresh(world, io):
    state = _state(world)
    world.flags["_noop"] = True
    for addr in sorted(state):
        io.print(f"{addr}: Refreshing state... [id={state[addr]['computed']['id']}]")
    io.print(c("\n(nothing in this lab touches AWS behind Terraform's back, so refresh can never "
               "find drift here. On a real account it does: someone clicks in the console, and "
               "the next plan quietly proposes to undo them. `terraform plan -refresh-only` is "
               "the modern spelling.)", "dim"))


def _tf_state_cmd(world, args, io):
    world.flags["_noop"] = True
    state = _state(world)
    if args[:1] == ["list"]:
        for addr in sorted(state):
            io.print(addr)
        if not state:
            io.print(c("(state is empty — nothing has been applied, or it was all destroyed)", "dim"))
        world.flags["tf_state_list"] = True
    elif args[:1] == ["show"] and len(args) > 1:
        addr = args[1]
        entry = state.get(addr)
        if not entry:
            io.print(c("╷", "red"))
            io.print(c("│ Error: ", "red") + f"Resource {addr} not found in state")
            io.print(c("╵", "red"))
            io.print(c("(`terraform state list` prints the addresses state knows)", "dim"))
            return
        _render_resource(state, addr, entry, io)
        io.print(c("(id and arn are in state and in NO .tf file — proof that state is not a copy "
                   "of your code, it's what Terraform learned from the cloud)", "dim"))
        world.flags["tf_state_show"] = True
    else:
        io.print("Usage: terraform state <subcommand> [options] [args]")
        io.print(c("(this lab simulates: terraform state list · terraform state show <address>)", "dim"))


def _tf(world, m, io):
    """Every `terraform ...` line in every Terraform mission (and the capstone)."""
    line = m.group(0)
    try:
        args = shlex.split(line)[1:]
    except ValueError:
        args = line.split()[1:]
    sub = args[0] if args else ""
    cfg = _config(world)

    if sub in ("version", "--version", "-version", "-v"):
        world.flags["_noop"] = True
        # Same string the engine hands back from a mission that has no terraform
        # handler — one version for one tool, or the game contradicts itself.
        io.print(TOOL_VERSION_LINES["terraform"])
        io.print(c("(it answered → terraform is installed. The check that belongs before any "
                   "`terraform init`)", "dim"))
        return

    if sub in ("", "-help", "--help", "help", "-h"):
        world.flags["_noop"] = True
        io.print("Usage: terraform [global options] <subcommand> [args]\n")
        io.print("The available commands for execution are listed below.\n")
        for name, desc in (("init", "Prepare your working directory for other commands"),
                           ("validate", "Check whether the configuration is valid"),
                           ("plan", "Show changes required by the current configuration"),
                           ("apply", "Create or update infrastructure"),
                           ("destroy", "Destroy previously-created infrastructure"),
                           ("output", "Show output values from your root module"),
                           ("show", "Show the current state in a readable form"),
                           ("state", "Advanced state management"),
                           ("fmt", "Reformat your configuration in the standard style")):
            io.print(f"  {name:<10} {desc}")
        io.print(c('\n(`terraform <cmd> -help` in real life — asking the tool first is the '
                   "habit that beats searching)", "dim"))
        return

    if sub == "fmt":
        world.flags["_noop"] = True
        io.print(c("(no files needed reformatting — fmt rewrites indentation in place, so run it "
                   "before you commit, not after review comments)", "dim"))
        return

    if sub == "init":
        _tf_init(world, cfg, io)
        return

    if sub in ("plan", "apply", "destroy", "validate", "output", "state", "show", "refresh") \
            and not world.flags.get("tf_init"):
        io.print(c("╷", "red"))
        io.print(c("│ Error: ", "red") + "Inconsistent dependency lock file / plugins not installed")
        io.print(c("│", "red"))
        io.print(c("│ ", "red") + 'Please run "terraform init" to install the providers required '
                                  "for this configuration.")
        io.print(c("╵", "red"))
        io.print(c("(every fresh terraform folder starts with: terraform init)", "dim"))
        world.flags["_noop"] = True
        return

    if sub == "state":
        _tf_state_cmd(world, args[1:], io)
        return

    if sub == "show":
        _tf_show(world, io)
        return

    if sub == "refresh":
        _tf_refresh(world, io)
        return

    errs = _config_errors(world, cfg)
    if sub == "validate":
        world.flags["_noop"] = True
        if errs:
            _print_errors(world, io, errs)
            return
        io.print(c("Success!", "green") + " The configuration is valid.")
        io.print(c("(validate never talks to AWS — it checks syntax, references and required "
                   "arguments. Cheap, so run it before every plan)", "dim"))
        world.flags["tf_validated"] = True
        return
    if errs and sub in ("plan", "apply", "destroy", "output"):
        _print_errors(world, io, errs)
        return

    if sub == "output":
        _tf_output(world, cfg, args[1:], io)
        return

    if sub in ("plan", "apply", "destroy") and not _backend_ready(world, cfg, io):
        return

    vals = _var_values(world, cfg, line, io) if cfg["variables"] else {}
    if sub == "plan":
        _tf_plan(world, cfg, vals, io)
    elif sub == "apply":
        _tf_apply(world, cfg, vals, line, io)
    elif sub == "destroy":
        _tf_destroy(world, cfg, vals, line, io)
    else:
        world.flags["_noop"] = True
        io.print(f'Terraform has no command named "{sub}".\n')
        io.print("To see all of Terraform's top-level commands, run:\n  terraform -help")
        io.print(c("(this lab simulates: init · validate · plan · apply · destroy · output · "
                   "show · refresh · state list · state show · fmt)", "dim"))


# --------------------------------------------------------------- aws cli --
_IDENTITY = ('{\n    "UserId": "AIDAEXAMPLE6QRSTUVWXY",\n'
             '    "Account": "123456789012",\n'
             '    "Arn": "arn:aws:iam::123456789012:user/student"\n}')


def _aws(world, m, io):
    """The two AWS CLI calls class 12 actually needs around Terraform: whose
    account am I in, and make the bucket state will live in."""
    line = m.group(0)
    try:
        args = shlex.split(line)[1:]
    except ValueError:
        args = line.split()[1:]
    buckets = world.flags.setdefault("aws_buckets", {})

    # Check-first, everywhere: outside this lab the engine answers `aws --version`
    # from its own table, and a lab that refused the same question would teach
    # that the tool is missing right where it is most present.
    # (only `--version`: real aws answers `Invalid choice: 'version'` to the
    # docker/terraform spelling, and the fallback below says exactly that.)
    if args[:1] == ["--version"]:
        world.flags["_noop"] = True
        io.print(TOOL_VERSION_LINES["aws"])
        io.print(c("(it answered → the CLI is installed. `aws configure` is the next check: "
                   "credentials are a separate question from installation)", "dim"))
        return

    if args[:2] == ["sts", "get-caller-identity"]:
        world.flags["_noop"] = True
        io.print(_IDENTITY)
        io.print(c("(who am I, which account — the sanity check before you build anything that "
                   "bills. Wrong account is the most expensive typo in DevOps)", "dim"))
        world.flags["tf_whoami"] = True
        return

    if args[:2] == ["s3api", "create-bucket"]:
        name = _flag(args, "--bucket")
        region = _flag(args, "--region") or "us-east-1"
        constraint = None
        cfg_flag = _flag(args, "--create-bucket-configuration") or ""
        m_c = re.search(r"LocationConstraint=([\w-]+)", cfg_flag)
        if m_c:
            constraint = m_c.group(1)
        if not name:
            world.flags["_noop"] = True
            io.print("usage: aws [options] <command> <subcommand> [parameters]")
            io.print("aws: error: the following arguments are required: --bucket")
            return
        if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", name):
            world.flags["_noop"] = True
            io.print("An error occurred (InvalidBucketName) when calling the CreateBucket "
                     "operation: The specified bucket is not valid.")
            io.print(c("(bucket names are lowercase, 3–63 chars, no underscores — and GLOBALLY "
                       "unique across all of AWS. devops-course-<yourname>)", "dim"))
            return
        if region != "us-east-1" and constraint != region:
            world.flags["_noop"] = True
            io.print("An error occurred (IllegalLocationConstraintException) when calling the "
                     "CreateBucket operation: The unspecified location constraint is incompatible "
                     "for the region specific endpoint this request was sent to.")
            io.print(c("(every region except us-east-1 wants it spelled twice: "
                       f"--region {region} --create-bucket-configuration "
                       f"LocationConstraint={region})", "dim"))
            return
        if name in buckets:
            world.flags["_noop"] = True
            io.print("An error occurred (BucketAlreadyOwnedByYou) when calling the CreateBucket "
                     "operation: Your previous request to create the named bucket succeeded and "
                     "you already own it.")
            io.print(c("(already yours — creating it twice is a no-op, not a disaster. Someone "
                       "else owning the name is the error you actually fear: BucketAlreadyExists)",
                       "dim"))
            return
        buckets[name] = {"region": region, "keys": {}}
        io.print('{\n    "Location": "http://%s.s3.amazonaws.com/"\n}' % name)
        io.print(c("(the bucket is not managed by Terraform on purpose — state has to live "
                   "somewhere BEFORE there is state to live there)", "dim"))
        return

    if args[:1] == ["s3"] and args[1:2] == ["ls"]:
        world.flags["_noop"] = True
        target = args[2] if len(args) > 2 else ""
        if not target:
            for name in sorted(buckets):
                io.print(f"2026-08-17 10:04:11 {name}")
            if not buckets:
                io.print(c("(no buckets yet — aws s3api create-bucket makes one)", "dim"))
            return
        name = target.replace("s3://", "").rstrip("/").split("/")[0]
        if name not in buckets:
            io.print("An error occurred (NoSuchBucket) when calling the ListObjectsV2 operation: "
                     "The specified bucket does not exist")
            return
        for key, doc in sorted(buckets[name].get("keys", {}).items()):
            io.print(f"2026-08-17 10:07:52 {len(doc):>8} {key}")
        if not buckets[name].get("keys"):
            io.print(c("(empty bucket — nothing has been written to it yet)", "dim"))
        return

    world.flags["_noop"] = True
    io.print("usage: aws [options] <command> <subcommand> [parameters]")
    io.print("aws: error: the following arguments are required: command" if not args
             else f"Invalid choice: '{args[0]}'")
    io.print(c("(this lab simulates the three calls class 12 needs: `aws sts get-caller-identity`, "
               "`aws s3api create-bucket …`, `aws s3 ls`. Everything else is real-AWS territory)",
               "dim"))


def _flag(args, name):
    for i, a in enumerate(args):
        if a == name and i + 1 < len(args):
            return args[i + 1]
        if a.startswith(name + "="):
            return a.split("=", 1)[1]
    return None


# ------------------------------------------------------- objective helpers --
def _last_plan(world):
    """(add, change, destroy) of the most recent plan — but only while the code
    still matches the plan you read. A stale plan proves nothing."""
    lp = world.flags.get("tf_last_plan")
    if not lp or lp[0] != _fingerprint(world):
        return None
    return tuple(lp[1:4])


def _last_changed(world):
    """Which addresses that plan said it would update in-place."""
    lp = world.flags.get("tf_last_plan")
    if not lp or lp[0] != _fingerprint(world):
        return []
    return list(lp[4])


def _has_resource(world, addr):
    return addr in _config(world)["resources"]


def _var_default(world, name):
    return _unq(_config(world)["variables"].get(name, {}).get("default", ""))


def _attr_uses_var(world, addr, attr, var):
    return f"var.{var}" in _config(world)["resources"].get(addr, {}).get(attr, "")


def _sg_open_to_world(world):
    """Read it from STATE, not from the file: the lesson is that a rule is only
    tightened once it has been applied."""
    for addr, entry in _state_snapshot(world).items():
        if addr.split(".")[0] != "aws_security_group":
            continue
        if any(k.endswith("cidr_blocks") and "0.0.0.0/0" in v for k, v in entry["attrs"].items()):
            return True
    return False


def _state_snapshot(world):
    return world.flags.get("tf_state", {})


# ------------------------------------------------------------ mission data --
MAIN_TF = '''provider "aws" {
  region = "eu-central-1"
}

resource "aws_vpc" "main" {
  cidr_block = "10.0.0.0/16"
}

resource "aws_subnet" "public" {
  vpc_id     = aws_vpc.main.id
  cidr_block = "10.0.1.0/24"
}
'''

INSTANCE_SNIPPET = '''resource "aws_instance" "web" {
  ami           = "ami-0abcdef1234567890"
  instance_type = "t3.micro"
  subnet_id     = aws_subnet.public.id
}
'''

MAIN_TF_GROWN = MAIN_TF + "\n" + INSTANCE_SNIPPET

# ---- mission 2: a stack that is already applied. Split by concern, the way the
# note's table says: provider / networking / instances / variables / outputs.
PROVIDER_TF = '''provider "aws" {
  region = var.aws_region
}
'''

VARIABLES_TF = '''variable "aws_region" {
  default = "eu-west-1"
}
'''

NETWORKING_TF = '''resource "aws_vpc" "main" {
  cidr_block = "10.0.0.0/16"
}

resource "aws_subnet" "public" {
  vpc_id     = aws_vpc.main.id
  cidr_block = "10.0.1.0/24"
}

resource "aws_security_group" "ssh" {
  name   = "allow-ssh"
  vpc_id = aws_vpc.main.id

  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
}
'''

INSTANCES_TF = '''resource "aws_instance" "web" {
  ami                    = "ami-0abcdef1234567890"
  instance_type          = "t3.micro"
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.ssh.id]
}
'''

OUTPUTS_TF = '''output "public_ip" {
  value = aws_instance.web.public_ip
}
'''

BACKEND_TF = '''# State is LOCAL today: one laptop, one copy, no locking.
# Make the bucket first, then uncomment this and re-run: terraform init
#
# terraform {
#   backend "s3" {
#     bucket = "devops-course-CHANGEME"
#     key    = "dev/terraform.tfstate"
#     region = "eu-west-1"
#   }
# }
'''

GITIGNORE = '''terraform.tfstate
terraform.tfstate.backup
*.tfvars
*.pem
.terraform/
'''

# The finished versions the drills lead to — derived from the originals, because
# every drill is "the same file, one thing different". The solution types these,
# so brief, demo and check can never drift apart.
VARIABLES_TF_FIXED = VARIABLES_TF + '''
variable "instance_type" {
  default = "t3.micro"
}
'''
INSTANCES_TF_FIXED = INSTANCES_TF.replace('"t3.micro"', "var.instance_type")
OUTPUTS_TF_FIXED = OUTPUTS_TF + '''
output "vpc_id" {
  value = aws_vpc.main.id
}
'''
NETWORKING_TF_FIXED = NETWORKING_TF.replace('["0.0.0.0/0"]', '["203.0.113.4/32"]')
BACKEND_TF_FIXED = '''terraform {
  backend "s3" {
    bucket = "devops-course-quest"
    key    = "dev/terraform.tfstate"
    region = "eu-west-1"
  }
}
'''

DAY2_FILES = {
    "provider.tf": PROVIDER_TF,
    "variables.tf": VARIABLES_TF,
    "networking.tf": NETWORKING_TF,
    "instances.tf": INSTANCES_TF,
    "outputs.tf": OUTPUTS_TF,
    "backend.tf": BACKEND_TF,
    ".gitignore": GITIGNORE,
}


def _applied(files):
    """The ledger a previous `apply` of exactly these files would have written —
    so mission 2 can start on day 2 of the job instead of an empty account."""
    cfg = _parse(files)
    vals = {n: b.get("default", '""') for n, b in cfg["variables"].items()}
    state = {}
    for addr in _dep_order({a: _resolved(cfg, a, vals) for a in cfg["order"]}, cfg["order"])[0]:
        _create(state, cfg, addr, vals)
    outputs = {n: _known(state, b.get("value", '""')) for n, b in cfg["outputs"].items()}
    return state, {n: v for n, v in outputs.items() if v is not None}


DAY2_STATE, DAY2_OUTPUTS = _applied(DAY2_FILES)
DAY2_FILES["terraform.tfstate"] = _state_doc(DAY2_STATE, DAY2_OUTPUTS, 4)


MISSIONS = [
    {
        "id": "tf-01",
        "topic": "terraform",
        "title": "Declare the Cloud 🏗️ — init, plan, apply, destroy",
        "vault_note": "Class 12 - Terraform",
        "brief": ("main.tf declares a VPC and a subnet that don't exist yet (cat main.tf).\n"
                  "Look at the subnet: `vpc_id = aws_vpc.main.id`. That one reference is\n"
                  "the whole dependency graph — you never write the order.\n\n"
                  "Walk the sacred lifecycle: init → validate → plan (READ it!) → apply.\n"
                  "Then GROW the infra by declaring a server inside that subnet:\n\n"
                  '  resource "aws_instance" "web" {\n'
                  '    ami           = "ami-0abcdef1234567890"\n'
                  '    instance_type = "t3.micro"\n'
                  '    subnet_id     = aws_subnet.public.id\n'
                  "  }\n\n"
                  "…and when you're done, leave nothing running. Declarative means the\n"
                  "CODE is the truth — you never click-create anything."),
        "world": {
            "files": {"main.tf": MAIN_TF},
        },
        "handlers": [
            (r"terraform(\s+.*)?", _tf),
        ],
        "objectives": [
            {"desc": "Initialize the working directory (downloads the AWS provider)", "xp": 10,
             "hint": "terraform init — always the first command in a fresh terraform folder.",
             "check": lambda w: w.flags.get("tf_init")},
            {"desc": "Check the config is valid — before it costs anything", "xp": 10,
             "hint": "terraform validate — reads your .tf files, never talks to AWS.",
             "check": lambda w: w.flags.get("tf_validated")},
            {"desc": "Preview: plan shows exactly 2 to add, 0 to change, 0 to destroy", "xp": 15,
             "hint": "terraform plan — read the + lines and the Plan: summary at the bottom.",
             "check": lambda w: _last_plan(w) == (2, 0, 0)},
            {"desc": "Apply it — type the magic word, and watch the VPC land before the subnet",
             "xp": 20,
             "hint": "terraform apply — it re-shows the plan and waits for the literal word: yes",
             "check": lambda w: {"aws_vpc.main", "aws_subnet.public"} <= set(_state_snapshot(w))},
            {"desc": "Read the ledger: list what state now tracks", "xp": 10,
             "hint": "terraform state list — state is Terraform's memory of what it built.",
             "check": lambda w: w.flags.get("tf_state_list")},
            {"desc": "Declare an EC2 instance in that subnet; plan shows +1 more", "xp": 15,
             "hint": "edit main.tf — KEEP everything and add the aws_instance block from the "
                     "brief. Then plan.",
             "check": lambda w: _last_plan(w) == (1, 0, 0) and _has_resource(w, "aws_instance.web")},
            {"desc": "Apply without the prompt (CI-style)", "xp": 15,
             "hint": "terraform apply -auto-approve — how pipelines do it (no human to type yes).",
             "check": lambda w: "aws_instance.web" in _state_snapshot(w)},
            {"desc": "Tear it ALL down — the lab is over", "xp": 15,
             "hint": "terraform destroy (type yes) — free-tier stays free only if you clean up.",
             "check": lambda w: w.flags.get("tf_destroyed") and not _state_snapshot(w)},
        ],
        "teach": [
            "init downloads providers and wires the backend — always step zero in a fresh folder.",
            "validate reads the config, not the cloud: the cheapest error is the one found "
            "before any API call.",
            "plan is the free preview — read the + and - lines BEFORE anything real happens.",
            "apply executes the plan; 'yes' is the safety catch, and the REFERENCES decide the "
            "build order.",
            "state is the ledger: config ↔ real resource ids. No ledger, no diff, no idempotence.",
            "Growing infra = DECLARING more in code — the .tf files are the inventory of what exists.",
            "-auto-approve exists because pipelines can't type yes — that's the CI mode.",
            "destroy reverses everything state remembers, in reverse dependency order — labs die "
            "with the session, and so do the bills.",
        ],
        "solution": [
            "cat main.tf",
            "terraform init",
            "terraform validate",
            "terraform plan",
            "terraform apply",
            "yes",
            "terraform state list",
            "edit main.tf", *MAIN_TF_GROWN.splitlines(), ".",
            "terraform plan",
            "terraform apply -auto-approve",
            "terraform destroy",
            "yes",
        ],
    },
    {
        "id": "tf-02",
        "topic": "terraform",
        "title": "Day Two 🔐 — variables, outputs, and remote state",
        "vault_note": "Class 12 - Terraform",
        "brief": ("You inherit a stack that is ALREADY applied: a VPC, a subnet, a security\n"
                  "group and an EC2 — split across files by concern (ls, then cat each one).\n"
                  "State is local, the instance size is hardcoded, the only output is the IP,\n"
                  "and the SSH rule is open to 0.0.0.0/0. Day two of the job is fixing that.\n\n"
                  "  1. Make the size an input:  variable \"instance_type\" { default = \"t3.micro\" }\n"
                  "     then use var.instance_type in instances.tf. plan must stay CLEAN.\n"
                  "  2. Publish a fact:          output \"vpc_id\" { value = aws_vpc.main.id }\n"
                  "  3. Close the door:          cidr_blocks = [\"203.0.113.4/32\"] on the SSH rule,\n"
                  "     and read the ~ line in the plan before you apply it.\n"
                  "  4. Move the ledger:         make an S3 bucket, uncomment backend.tf with\n"
                  "     YOUR bucket name, and let terraform init migrate the state.\n\n"
                  "Everything here is a real diff. Read every plan before you approve it."),
        "world": {
            "files": dict(DAY2_FILES),
            "flags": {"tf_init": True, "tf_serial": 4,
                      "tf_state": DAY2_STATE, "tf_outputs": DAY2_OUTPUTS},
        },
        "handlers": [
            (r"terraform(\s+.*)?", _tf),
            (r"aws(\s+.*)?", _aws),
        ],
        "objectives": [
            {"desc": "Confirm which AWS account you're pointed at, before changing anything",
             "xp": 10,
             "hint": "aws sts get-caller-identity — the ARN and account id you're about to bill.",
             "check": lambda w: w.flags.get("tf_whoami")},
            {"desc": "Parameterize the size: instance_type variable, used via var.instance_type, "
                     "plan still clean", "xp": 20,
             "hint": 'edit variables.tf (keep aws_region!) and add variable "instance_type" '
                     '{ default = "t3.micro" }, then edit instances.tf to say '
                     "instance_type = var.instance_type. Then plan: same value = no diff.",
             "check": lambda w: (_var_default(w, "instance_type") == "t3.micro"
                                 and _attr_uses_var(w, "aws_instance.web", "instance_type",
                                                    "instance_type")
                                 and _last_plan(w) == (0, 0, 0))},
            {"desc": "Publish the VPC id as an output — and apply so state carries it", "xp": 15,
             "hint": 'edit outputs.tf (keep public_ip!) and add output "vpc_id" '
                     "{ value = aws_vpc.main.id }, then terraform apply (yes).",
             "check": lambda w: w.flags.get("tf_outputs", {}).get("vpc_id")},
            {"desc": "Read the outputs back out of state", "xp": 10,
             "hint": "terraform output (or terraform output vpc_id, or -raw for the bare value).",
             "check": lambda w: w.flags.get("tf_output_read")},
            {"desc": "Close SSH to your IP only — read the ~ diff: 0 to add, 1 to change (the SG)",
             "xp": 20,
             "hint": 'edit networking.tf and change the ingress cidr_blocks to '
                     '["203.0.113.4/32"], then plan and read the ~ line.',
             "check": lambda w: (_last_plan(w) == (0, 1, 0)
                                 and _last_changed(w) == ["aws_security_group.ssh"])},
            {"desc": "Apply the lockdown — 0.0.0.0/0 gone from state", "xp": 15,
             "hint": "terraform apply (yes) — a rule is only closed once it's applied.",
             "check": lambda w: (_has_resource(w, "aws_security_group.ssh")
                                 and not _sg_open_to_world(w))},
            {"desc": "Create the S3 bucket that will hold state (yours, lowercase, unique)",
             "xp": 15,
             "hint": "aws s3api create-bucket --bucket devops-course-<yourname> --region eu-west-1 "
                     "--create-bucket-configuration LocationConstraint=eu-west-1",
             "check": lambda w: bool(w.flags.get("aws_buckets"))},
            {"desc": "Migrate state to the S3 backend (uncomment backend.tf, re-init, copy it)",
             "xp": 25,
             "hint": "edit backend.tf — write the terraform { backend \"s3\" { … } } block with "
                     "YOUR bucket name, then terraform init and answer yes to copy the state.",
             "check": lambda w: ((w.flags.get("tf_backend") or {}).get("type") == "s3"
                                 and len(_state_snapshot(w)) == 4)},
        ],
        "teach": [
            "Know the account before you touch it — `sts get-caller-identity` is the cheapest "
            "insurance in the cloud.",
            "A variable replaces a magic string with an input. Same value in, no diff out — "
            "refactoring config should never move infrastructure.",
            "Outputs live in STATE, not in code: declare it, apply it, and only then can anyone "
            "read it.",
            "`terraform output` is how the next step in a pipeline gets the IP — no console "
            "clicking, no copy-paste.",
            "A ~ in the plan is an in-place update; a - is a delete. Reading those symbols is "
            "the entire safety mechanism.",
            "0.0.0.0/0 on port 22 offers SSH to the whole internet — scope it to one IP, and "
            "remember the fix only counts once applied.",
            "State must live somewhere before there is state: the bucket is created OUTSIDE "
            "Terraform, on purpose.",
            "Remote state = one shared, lockable source of truth. Local state is one laptop "
            "away from a team-wide outage.",
        ],
        "solution": [
            "ls",
            "cat instances.tf",
            "aws sts get-caller-identity",
            "terraform plan",
            "edit variables.tf", *VARIABLES_TF_FIXED.splitlines(), ".",
            "edit instances.tf", *INSTANCES_TF_FIXED.splitlines(), ".",
            "terraform plan",
            "edit outputs.tf", *OUTPUTS_TF_FIXED.splitlines(), ".",
            "terraform apply",
            "yes",
            "terraform output",
            "edit networking.tf", *NETWORKING_TF_FIXED.splitlines(), ".",
            "terraform plan",
            "terraform apply",
            "yes",
            "aws s3api create-bucket --bucket devops-course-quest --region eu-west-1 "
            "--create-bucket-configuration LocationConstraint=eu-west-1",
            "edit backend.tf", *BACKEND_TF_FIXED.splitlines(), ".",
            "terraform init",
            "yes",
            "aws s3 ls s3://devops-course-quest",
            "terraform state list",
        ],
    },
]
