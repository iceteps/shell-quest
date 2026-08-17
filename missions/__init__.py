"""Mission registry — add your topic module here and it appears in the game."""
from missions import (ansible_lab, ansible_ops, docker_basics, git_basics, gitops_ci,
                      helm_release, k8s_basics, linux_basics, rabbitmq_queue,
                      skywatch_campaign, terraform_infra)

# Order matters twice over: the mission map prints topics in this order, and
# "next up" picks the first uncompleted mission — so the course's real first
# class (Linux) has to come first.
#
# ansible_lab is class 14's dockerized lab: same topic key as ansible_ops, and
# it goes AFTER it because the lab is class 11's theory made hands-on — playing
# it first would ask you to debug an inventory you've never read.
ALL_MISSIONS = (linux_basics.MISSIONS + docker_basics.MISSIONS + git_basics.MISSIONS
                + k8s_basics.MISSIONS + helm_release.MISSIONS + gitops_ci.MISSIONS
                + ansible_ops.MISSIONS + ansible_lab.MISSIONS
                + terraform_infra.MISSIONS
                + rabbitmq_queue.MISSIONS + skywatch_campaign.MISSIONS)

TOPICS = {
    "linux": "🐧 Linux",
    "docker": "🐳 Docker",
    "git": "🌿 Git",
    "k8s": "☸️ Kubernetes",
    "helm": "⎈ Helm",
    "gitops": "🔁 GitOps / CI-CD",
    "ansible": "📜 Ansible",
    "terraform": "🏗️ Terraform",
    "rabbitmq": "📨 RabbitMQ",
    "capstone": "🛰️ THE CAMPAIGN",
}
