"""Mission registry — add your topic module here and it appears in the game."""
from missions import (ansible_ops, docker_basics, git_basics, gitops_ci,
                      helm_release, k8s_basics, rabbitmq_queue, terraform_infra)

ALL_MISSIONS = (docker_basics.MISSIONS + git_basics.MISSIONS + k8s_basics.MISSIONS
                + helm_release.MISSIONS + gitops_ci.MISSIONS + ansible_ops.MISSIONS
                + terraform_infra.MISSIONS + rabbitmq_queue.MISSIONS)

TOPICS = {
    "docker": "🐳 Docker",
    "git": "🌿 Git",
    "k8s": "☸️ Kubernetes",
    "helm": "⎈ Helm",
    "gitops": "🔁 GitOps / CI-CD",
    "ansible": "📜 Ansible",
    "terraform": "🏗️ Terraform",
    "rabbitmq": "📨 RabbitMQ",
}
