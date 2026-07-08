"""Mission registry — add your topic module here and it appears in the game."""
from missions import docker_basics, git_basics

ALL_MISSIONS = docker_basics.MISSIONS + git_basics.MISSIONS

TOPICS = {
    "docker": "🐳 Docker",
    "git": "🌿 Git",
}
