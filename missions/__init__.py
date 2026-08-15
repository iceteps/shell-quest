"""Mission registry — add your topic module here and it appears in the game."""
from missions import (ansible_ops, docker_basics, git_basics, gitops_ci,
                      helm_release, k8s_basics, linux_basics, rabbitmq_queue,
                      skywatch_campaign, terraform_infra)

# Order matters twice over: the mission map prints topics in this order, and
# "next up" picks the first uncompleted mission — so the course's real first
# class (Linux) has to come first.
ALL_MISSIONS = (linux_basics.MISSIONS + docker_basics.MISSIONS + git_basics.MISSIONS
                + k8s_basics.MISSIONS + helm_release.MISSIONS + gitops_ci.MISSIONS
                + ansible_ops.MISSIONS + terraform_infra.MISSIONS
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
