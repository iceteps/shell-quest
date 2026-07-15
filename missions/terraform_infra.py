"""Terraform mission — the init → plan → apply → destroy lifecycle with real
error messages. Mission-local handlers; state lives in world.flags."""
import re

from engine import c

MAIN_TF = '''provider "aws" {
  region = "eu-central-1"
}

resource "aws_instance" "web" {
  ami           = "ami-0abcdef1234567890"
  instance_type = "t3.micro"
}
'''

S3_SNIPPET = '''resource "aws_s3_bucket" "artifacts" {
  bucket = "my-artifacts-bucket-12345"
}
'''


def _resources_in_code(world):
    return re.findall(r'resource\s+"([\w_]+)"\s+"([\w_]+)"', world.files.get("main.tf", ""))


def _tf(world, m, io):
    line = m.group(0)
    args = line.split()[1:]
    sub = args[0] if args else ""
    state = world.flags.setdefault("tf_state", {})

    if sub == "init":
        if "main.tf" not in world.files:
            io.print(c("Error:", "red") + " No Terraform configuration files found in this directory.")
            return
        io.print("Initializing the backend...\nInitializing provider plugins...")
        io.print('- Finding latest version of hashicorp/aws...\n- Installing hashicorp/aws v5.54.0...')
        io.print(c("\nTerraform has been initialized!", "green"))
        world.flags["tf_init"] = True

    elif sub in ("plan", "apply", "destroy") and not world.flags.get("tf_init"):
        io.print(c("╷\n│ Error: ", "red") + "Inconsistent dependency lock file / plugins not installed")
        io.print(c("│ ", "red") + 'Please run "terraform init" to install the providers required for this configuration.')
        io.print(c("╵", "red"))
        io.print(c("(every fresh terraform folder starts with: terraform init)", "dim"))

    elif sub == "plan":
        code = _resources_in_code(world)
        to_add = [f"{t}.{n}" for t, n in code if f"{t}.{n}" not in state]
        to_del = [addr for addr in state if addr not in {f"{t}.{n}" for t, n in code}]
        if not to_add and not to_del:
            io.print("No changes. Your infrastructure matches the configuration.")
            world.flags["tf_plan_clean"] = True
            return
        io.print("Terraform will perform the following actions:\n")
        for addr in to_add:
            io.print(c(f"  # {addr} will be created", "green"))
            io.print(c(f"  + resource \"{addr.split('.')[0]}\" \"{addr.split('.')[1]}\" {{ ... }}\n", "green"))
        for addr in to_del:
            io.print(c(f"  # {addr} will be destroyed", "red"))
        io.print(f"Plan: {len(to_add)} to add, 0 to change, {len(to_del)} to destroy.")
        io.print(c('(nothing happened yet — plan is the "are you sure?" preview)', "dim"))
        world.flags["tf_planned"] = (len(to_add), len(to_del))
        if state and to_add:
            world.flags["tf_plan_after_edit"] = True

    elif sub == "apply":
        code = _resources_in_code(world)
        to_add = [f"{t}.{n}" for t, n in code if f"{t}.{n}" not in state]
        to_del = [addr for addr in state if addr not in {f"{t}.{n}" for t, n in code}]
        if not to_add and not to_del:
            io.print("No changes. Your infrastructure matches the configuration.\n\nApply complete! Resources: 0 added, 0 changed, 0 destroyed.")
            return
        io.print(f"Plan: {len(to_add)} to add, 0 to change, {len(to_del)} to destroy.")
        if "-auto-approve" not in line:
            io.print("\nDo you want to perform these actions?")
            io.print("  Terraform will perform the actions described above.")
            io.print("  Only 'yes' will be accepted to approve.\n")
            answer = io.input("  Enter a value: ").strip()
            if answer != "yes":
                io.print("\nApply cancelled.")
                return
        io.print("")
        for addr in to_add:
            io.print(f"{addr}: Creating...")
            io.print(f"{addr}: Creation complete after 12s [id={'i-' if 'instance' in addr else ''}0{addr.__hash__() % 10 ** 8:08d}]")
            state[addr] = True
        for addr in to_del:
            io.print(f"{addr}: Destroying...")
            io.print(f"{addr}: Destruction complete after 31s")
            del state[addr]
        io.print(c(f"\nApply complete! Resources: {len(to_add)} added, 0 changed, {len(to_del)} destroyed.", "green"))
        world.flags["tf_applied"] = len(state)

    elif sub == "destroy":
        if not state:
            io.print("No changes. No objects need to be destroyed.")
            return
        io.print(f"Plan: 0 to add, 0 to change, {len(state)} to destroy.")
        if "-auto-approve" not in line:
            io.print("\nDo you really want to destroy all resources?")
            io.print("  Only 'yes' will be accepted to confirm.\n")
            answer = io.input("  Enter a value: ").strip()
            if answer != "yes":
                io.print("\nDestroy cancelled.")
                return
        n = len(state)
        for addr in list(state):
            io.print(f"{addr}: Destroying...")
            io.print(f"{addr}: Destruction complete after 28s")
            del state[addr]
        io.print(c(f"\nDestroy complete! Resources: {n} destroyed.", "green"))
        io.print(c("(cloud bills don't tick for resources that don't exist — destroy your labs!)", "dim"))
        world.flags["tf_destroyed"] = True

    elif sub == "state" and args[1:2] == ["list"]:
        for addr in sorted(state):
            io.print(addr)
        world.flags["tf_state_list"] = True

    elif sub == "validate":
        io.print(c("Success!", "green") + " The configuration is valid.")

    else:
        io.print("terraform: try init / validate / plan / apply / destroy / state list")


MISSIONS = [
    {
        "id": "tf-01",
        "topic": "terraform",
        "title": "Declare the Cloud 🏗️ — init, plan, apply, destroy",
        "vault_note": "Class 12 - Terraform",
        "brief": ("main.tf declares an EC2 instance that doesn't exist yet (cat main.tf).\n"
                  "Walk the sacred lifecycle: init → plan (read it!) → apply. Then GROW\n"
                  "the infra by declaring an S3 bucket too — the brief's snippet:\n\n"
                  '  resource "aws_s3_bucket" "artifacts" {\n'
                  '    bucket = "my-artifacts-bucket-12345"\n'
                  "  }\n\n"
                  "…and when you're done, leave nothing running. Declarative means the\n"
                  "CODE is the truth — you never click-create anything."),
        "world": {
            "files": {"main.tf": MAIN_TF},
        },
        "handlers": [
            (r"terraform\s+.*", _tf),
        ],
        "objectives": [
            {"desc": "Initialize the working directory (downloads the AWS provider)", "xp": 10,
             "hint": "terraform init — always the first command in a fresh terraform folder.",
             "check": lambda w: w.flags.get("tf_init")},
            {"desc": "Preview: plan shows exactly 1 resource to add", "xp": 15,
             "hint": "terraform plan — read the + lines and the summary.",
             "check": lambda w: w.flags.get("tf_planned") == (1, 0)},
            {"desc": "Apply it — and type the magic word when asked", "xp": 20,
             "hint": "terraform apply — it re-shows the plan and waits for the literal word: yes",
             "check": lambda w: "aws_instance.web" in w.flags.get("tf_state", {})},
            {"desc": "Declare an S3 bucket in main.tf, plan shows +1 more", "xp": 25,
             "hint": "edit main.tf — KEEP everything and add the aws_s3_bucket block from the brief. Then plan.",
             "check": lambda w: w.flags.get("tf_plan_after_edit")
                                and len(_resources_in_code(w)) == 2},
            {"desc": "Apply without the prompt (CI-style)", "xp": 15,
             "hint": "terraform apply -auto-approve — how pipelines do it (no human to type yes).",
             "check": lambda w: len(w.flags.get("tf_state", {})) == 2},
            {"desc": "Tear it ALL down — the lab is over", "xp": 15,
             "hint": "terraform destroy (type yes) — free-tier stays free only if you clean up.",
             "check": lambda w: w.flags.get("tf_destroyed") and not w.flags.get("tf_state")},
        ],
        "teach": [
            "init downloads providers and wires the backend — always step zero in a fresh folder.",
            "plan is the free preview — read the + and - lines BEFORE anything real happens.",
            "apply executes the plan; the literal word 'yes' is the safety catch.",
            "Growing infra = DECLARING more in code — the .tf files are the inventory of what exists.",
            "-auto-approve exists because pipelines can't type yes — that's the CI mode.",
            "destroy reverses everything state remembers — labs die with the session, and so do the bills.",
        ],
        "solution": [
            "terraform init",
            "terraform plan",
            "terraform apply",
            "yes",
            "edit main.tf",
            'provider "aws" {', '  region = "eu-central-1"', '}', '',
            'resource "aws_instance" "web" {', '  ami           = "ami-0abcdef1234567890"',
            '  instance_type = "t3.micro"', '}', '',
            'resource "aws_s3_bucket" "artifacts" {', '  bucket = "my-artifacts-bucket-12345"', '}', ".",
            "terraform plan",
            "terraform apply -auto-approve",
            "terraform destroy",
            "yes",
        ],
    },
]
