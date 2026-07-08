# ⚡ Quick Quiz

A fast, colourful terminal quiz across the whole course — broader coverage than the
missions (K8s, Helm, Ansible, Terraform, RabbitMQ, GitOps, foundations). Pure Python
standard library. Great for a 5-minute warm-up before class or a study break.

## Run it

```bash
python quiz/quiz.py                 # from the repo root — 12 random questions
python quiz/quiz.py --all           # every question
python quiz/quiz.py -n 20           # pick how many
python quiz/quiz.py --topic git     # one topic only
```

Topics: `docker` · `git` · `k8s` · `helm` · `ansible` · `terraform` · `rabbitmq` · `gitops` · `foundations`

## What you get
- Multiple-choice **and** type-the-command questions (answers are matched leniently).
- **Streaks** 🔥, a score %, and a rank at the end (Beginner → DevOps Legend).
- Options are shuffled each run, so you can't memorise "it's always b".

## Study loop
Weak on a topic? Open the matching note in the Obsidian vault
(`laVault/devopsExperts/Class NN …`), do its drills, then run
`python quiz.py --topic <that topic>` to check yourself.

> Want more questions? They live in the `QUESTIONS` list at the top of `quiz.py` —
> add your own in the same format. Contributions from classmates welcome. 🎓
