"""RabbitMQ mission — compose up the broker, produce, inspect, consume, and
find out what your messages are actually worth.

The engine's world has no idea what a broker is, so this module IS the broker:
the `orders` queue lives in `world.flags["mq_queue"]` and every handler here
moves it the way RabbitMQ really would. The two behaviours the class note hangs
its extra credit on are modelled properly rather than hand-waved:

  * durability — a transient queue and its messages die with the broker
    process; only `durable=True` + `delivery_mode=2` come back from disk (and
    `down`/`rm` takes even those, because this compose file has no volume);
  * acknowledgements — `auto_ack=True` with no `basic_qos` empties the queue
    into one consumer's buffer, so killing it loses the work; manual
    `basic_ack` + `prefetch_count=1` puts the unacked message straight back.

The note's boss challenge — two workers on one queue — is the second mission
(`mq-02`), and it needs one more thing the engine has no idea about: a process
that is still alive at the next prompt. `world.flags["mq_workers"]` is that job
table, `_simulate()` is the broker's dispatcher, and the two dispatch rules it
implements are the whole lesson: no QoS means blind round-robin (a slow worker
is handed exactly as many as a fast one and sits on them), `prefetch_count=1`
means the share follows the speed.
"""
import json
import random
import re
import shlex

from engine import c, do_docker

QUEUE = "orders"

COMPOSE_YAML = '''services:
  rabbitmq:
    image: rabbitmq:3-management     # the -management tag is what enables 15672
    ports:
      - "5672:5672"                  # AMQP — pika talks here
      - "15672:15672"                # management UI + HTTP API — guest/guest
'''

PRODUCER_PY = '''import sys, time, pika

# --durable declares the queue durable AND publishes with delivery_mode=2.
# BOTH halves are needed: a durable queue full of transient messages comes
# back from a broker restart empty.
DURABLE = "--durable" in sys.argv

credentials = pika.PlainCredentials("guest", "guest")   # guest only works from localhost
params = pika.ConnectionParameters(host="localhost", credentials=credentials)

connection = pika.BlockingConnection(params)            # AMQP connection, port 5672
channel = connection.channel()                          # a channel to make calls on
queue_name = "orders"
channel.queue_declare(queue=queue_name, durable=DURABLE)  # idempotent: create if absent

props = pika.BasicProperties(delivery_mode=2) if DURABLE else None   # 2 = write to disk

for i in range(1, 21):
    message = f"Order #{i}"
    channel.basic_publish(
        exchange="",                # the DEFAULT exchange...
        routing_key=queue_name,     # ...which routes by queue NAME -> "orders"
        body=message,
        properties=props,
    )
    print(f"Sent: {message}")
    time.sleep(1)                   # 1/sec, so you can watch the depth climb
connection.close()
'''

CONSUMER_PY = '''import sys, pika

DURABLE = "--durable" in sys.argv   # must MATCH how the queue was declared

credentials = pika.PlainCredentials("guest", "guest")
params = pika.ConnectionParameters(host="localhost", credentials=credentials)
connection = pika.BlockingConnection(params)
channel = connection.channel()
channel.queue_declare(queue="orders", durable=DURABLE)   # declare again — safe & idempotent

def callback(ch, method, properties, body):     # runs once per delivered message
    print(f"Received: {body.decode()}")
    ch.basic_ack(delivery_tag=method.delivery_tag)   # "done with it — you can delete it"

channel.basic_qos(prefetch_count=1)             # fair dispatch: one unacked message at a time
channel.basic_consume(queue="orders", on_message_callback=callback)

print("Waiting for messages. Press CTRL+C to exit.")
channel.start_consuming()                       # blocks forever, calling callback
'''

WORKER_PY = '''import os, random, sys, time, pika

# A worker that DIES ON PURPOSE, mid-job, on its third message. That is this
# sandbox standing in for you running `kill -9` on a real worker while it is
# holding a message — the only way to see what an ack is actually for.
#
#   python worker.py              auto_ack=False + basic_qos(prefetch_count=1)
#   python worker.py --auto-ack   auto_ack=True, no QoS  (fast, and lossy)
AUTO_ACK = "--auto-ack" in sys.argv
DURABLE = "--durable" in sys.argv
NAME = f"worker-{random.randbytes(3).hex()}"

credentials = pika.PlainCredentials("guest", "guest")
params = pika.ConnectionParameters(host="localhost", credentials=credentials)
connection = pika.BlockingConnection(params)
channel = connection.channel()
channel.queue_declare(queue="orders", durable=DURABLE)

handled = 0

def callback(ch, method, properties, body):
    global handled
    handled += 1
    if handled == 3:
        print(f"[{NAME}] killed mid-job on {body.decode()}")
        os._exit(137)               # SIGKILL: no ack, no clean shutdown, no goodbye
    time.sleep(2)                   # slow work — this is where a crash hurts
    print(f"[{NAME}] done: {body.decode()}")
    if not AUTO_ACK:
        ch.basic_ack(delivery_tag=method.delivery_tag)

if not AUTO_ACK:
    channel.basic_qos(prefetch_count=1)     # at most ONE message at risk at a time
channel.basic_consume(queue="orders", on_message_callback=callback, auto_ack=AUTO_ACK)
print(f"[{NAME}] waiting for messages.")
channel.start_consuming()
'''

CONSUMER_MULTI_PY = '''import random, sys, time, pika

# The class-13 work-queue script. Run it in TWO terminals against the same
# queue and RabbitMQ round-robins between them — that is "competing consumers",
# and it is horizontal scaling with no code change and no coordinator.
#
#   python consumer-multi.py           auto_ack=True, no QoS   (exactly as class ran it)
#   python consumer-multi.py --fair    auto_ack=False + basic_ack + basic_qos(prefetch_count=1)
#   python consumer-multi.py --slow    time.sleep(6) instead of 2 — the worker that falls behind
FAIR = "--fair" in sys.argv
SLOW = "--slow" in sys.argv
WORK = 6 if SLOW else 2
NAME = f"consumer-{random.randbytes(3).hex()}"   # so you can SEE which one grabbed which

credentials = pika.PlainCredentials("guest", "guest")
params = pika.ConnectionParameters(host="localhost", credentials=credentials)
connection = pika.BlockingConnection(params)
channel = connection.channel()
channel.queue_declare(queue="orders")            # idempotent — whoever starts first creates it

def callback(ch, method, properties, body):
    print(f"[{NAME}] Received: {body.decode()}")
    time.sleep(WORK)                             # "processing" — the slow bit that matters
    if FAIR:
        ch.basic_ack(delivery_tag=method.delivery_tag)

if FAIR:
    # Fair dispatch: don't hand me another one until I've acked this one. Without
    # it the broker round-robins blindly and a slow worker hoards a whole batch.
    channel.basic_qos(prefetch_count=1)
channel.basic_consume(queue="orders", on_message_callback=callback, auto_ack=not FAIR)

print(f"Consumer started: {NAME}")
channel.start_consuming()                        # blocks this terminal forever
'''

BOOT_LOG = ("Starting RabbitMQ 3.13.7 on Erlang 26.2\n"
            "started TCP listener on [::]:5672\n"
            "Management plugin: HTTP listener started on port 15672\n"
            "Server startup complete; 4 plugins started.")


# ------------------------------------------------------------ broker state --
def _broker_up(world):
    return any(n == "rabbitmq" and d["status"] == "running"
               for n, d in world.containers.items())


def _q(world):
    """The `orders` queue — None until something declares it. A queue is not a
    given: it exists because a client asked for it, and it can cease to exist."""
    return world.flags.get("mq_queue")


def _depth(world):
    q = _q(world)
    return len(q["msgs"]) if q else 0


def _sync(world):
    """Mirror the depth into a flat flag — objective checks read it, and so does
    anyone extending this mission without knowing the message layout."""
    world.flags["queue_depth"] = _depth(world)


def _script(world, io, name):
    """Every mission here ships a different set of .py files, and python has an
    opinion about the ones that aren't there. Answer with python's, not ours."""
    if name in world.files:
        return True
    io.print(f"python: can't open file '/root/quest/{name}': [Errno 2] No such file or directory")
    io.print(c(f"(`ls` shows what this mission actually ships — {name} belongs to a different one)",
               "dim"))
    world.flags["_noop"] = True
    return False


def _refused(world, io, port=5672):
    io.print("pika.exceptions.AMQPConnectionError: [Errno 111] Connection refused"
             if port == 5672 else
             f"curl: (7) Failed to connect to localhost port {port}: Connection refused")
    io.print(c(f"(nothing is listening on localhost:{port} — is the rabbitmq container up? "
               "docker compose ps)", "dim"))
    world.flags["_noop"] = True


def _declare(world, io, durable, retry_as):
    """pika's `queue_declare`, including the 406 that a durability mismatch
    really raises. Declaring is idempotent, NOT 'whatever I say goes' — the
    broker compares your arguments against the queue it already has."""
    q = _q(world)
    if q is None:
        world.flags["mq_queue"] = {"durable": durable, "msgs": []}
        _sync(world)
        return True
    if q["durable"] != durable:
        io.print('pika.exceptions.ChannelClosedByBroker: (406, "PRECONDITION_FAILED - '
                 f"inequivalent arg 'durable' for queue '{QUEUE}' in vhost '/': received "
                 f"'{str(durable).lower()}' but current is '{str(q['durable']).lower()}'\")")
        io.print(c(f"(the queue on the broker is {'durable' if q['durable'] else 'transient'} and "
                   "re-declaring can't change that — every declare has to agree. Retry as: "
                   f"{retry_as}{' --durable' if q['durable'] else ''}   ·   or bin the queue: "
                   f"docker exec rabbitmq rabbitmqctl delete_queue {QUEUE})", "dim"))
        world.flags["_noop"] = True
        return False
    if not world.flags.get("taught_idempotent"):
        world.flags["taught_idempotent"] = True
        io.print(c(f"(queue_declare found '{QUEUE}' already there and did nothing — it is "
                   "idempotent, which is exactly why producer AND consumer both call it: "
                   "whichever starts first creates the queue.)", "dim"))
    return True


def _publish(world, body, persistent, routing_key=QUEUE):
    """The default exchange in one line: it delivers to the queue whose NAME
    equals the routing key, and drops the message when there isn't one."""
    q = _q(world)
    if q is None or routing_key != QUEUE:
        return False
    q["msgs"].append({"body": body, "persistent": persistent, "redelivered": False})
    _sync(world)
    return True


def _elide(io, items, render, keep=3):
    """Print the first few lines and the last one. Twenty near-identical lines
    bury the lesson in scrollback; hiding the gap silently would be a lie."""
    if len(items) <= keep + 1:
        for it in items:
            io.print(render(it))
        return
    for it in items[:keep]:
        io.print(render(it))
    io.print(c(f"… ({len(items) - keep - 1} more)", "dim"))
    io.print(render(items[-1]))


# ------------------------------------------------------ the consumer table --
# A competing-consumers lab needs something the engine has never had: a process
# that is still there at the next prompt. These four fields are that process —
# `unacked` is what a manual-ack worker is holding right now (the thing an ack
# exists for), `buffer` is work an auto_ack worker has already been credited
# with but hasn't done (the thing auto_ack quietly loses).
def _running(world):
    return world.flags.setdefault("mq_workers", [])


def _consumers(world):
    return len(_running(world))


def _unacked(world):
    return sum(len(w["unacked"]) for w in _running(world))


def _settle(world):
    """Time passes between two of your commands, and 2-second jobs finish in it.
    Whatever the workers were holding when you last looked, they have acked by
    the time the next batch lands — otherwise the sandbox would remember a
    message as 'in flight' forever."""
    for w in _running(world):
        w["handled"] += len(w["unacked"])
        w["unacked"], w["buffer"] = [], []


def _tag(msg):
    """'Order #7' → '#7', so a worker's whole share fits on one line."""
    body = msg["body"]
    return body.split()[-1] if body.startswith("Order #") else f"'{body[:16]}'"


def _tags(msgs, keep=6):
    tags = [_tag(m) for m in msgs]
    return " ".join(tags) if len(tags) <= keep + 1 else " ".join(tags[:keep]) + f" … {tags[-1]}"


def _simulate(workers, bodies):
    """The broker's dispatcher, and the entire point of the mission lives here.

    A consumer with `basic_qos(prefetch_count=1)` is only handed its next
    message once it has ACKED the last one — so the share follows the speed. A
    consumer with no QoS accepts everything it is offered, so the broker just
    round-robins blindly and a slow worker is handed exactly as many as a fast
    one. Returns per-worker shares, the delivery order, and when each worker
    will have chewed through what it took."""
    n = len(workers)
    free = [0.0] * n                 # when this worker finishes all it now holds
    got = [[] for _ in range(n)]
    deliveries, cur = [], 0
    for msg in bodies:
        best = None
        for step in range(n):
            i = (cur + step) % n
            ready = free[i] if workers[i]["fair"] else 0.0   # no QoS = always "ready"
            if best is None or ready < best[0]:
                best = (ready, i, step)
        _ready, i, step = best
        free[i] += workers[i]["rate"]
        got[i].append(msg)
        deliveries.append((i, msg))
        cur = (cur + step + 1) % n   # round-robin: start the next scan after this one
    return got, deliveries, free


def _dispatch(world, io, trigger):
    """Hand the ready messages to whoever is consuming, and print it the way the
    worker terminals would have printed it."""
    workers, q = _running(world), _q(world)
    if not workers or not q or not q["msgs"]:
        return
    _settle(world)
    bodies = list(q["msgs"])
    q["msgs"].clear()
    got, deliveries, free = _simulate(workers, bodies)
    _sync(world)

    io.print(c(f"── worker terminals: {len(bodies)} message(s), {len(workers)} consumer(s) "
               f"({trigger}) ──", "dim"))
    if not world.flags.get("taught_terminals"):
        world.flags["taught_terminals"] = True
        io.print(c("(in class every worker owns a terminal and these lines interleave live. There "
                   "is one prompt here, so they are merged — the name in front of each line is the "
                   "terminal it would have come from.)", "dim"))
    _elide(io, deliveries,
           lambda d: f"[{workers[d[0]]['name']}] Received: {d[1]['body']}"
                     + ("   (redelivered)" if d[1]["redelivered"] else ""))

    # A one-off publish (the curl drill, a requeue) needs the delivery line and
    # nothing else — the share table is for batches.
    detailed = len(bodies) > 2
    for i, w in enumerate(workers):
        mode = "prefetch=1" if w["fair"] else "no QoS"
        if not got[i]:
            if detailed:
                io.print(f"  {w['name']:<17} {w['rate']}s/msg  {mode:<11}  nothing — "
                         "it was never offered one")
            continue
        if detailed:
            io.print(f"  {w['name']:<17} {w['rate']}s/msg  {mode:<11} {len(got[i]):>2} msg  "
                     f"{_tags(got[i])}  → clear at {int(free[i])}s")
        # Delivered is not done: a fair worker still has the last one in its
        # hands, an auto_ack worker has a whole buffer it has already been
        # credited for. That difference is what the kill drill is about.
        if w["fair"]:
            w["handled"] += len(got[i]) - 1
            w["unacked"] = [got[i][-1]]
        else:
            w["handled"] += len(got[i])
            w["buffer"] = list(got[i])
    _sync(world)

    if not detailed:
        if len(workers) > 1:
            io.print(c("(one message, ONE consumer — the broker round-robins, it does not "
                       "broadcast. Getting a copy to every consumer is a different shape "
                       "entirely: a fanout exchange with a queue each.)", "dim"))
        return

    shares = [len(g) for g in got if g]
    fair_all = all(w["fair"] for w in workers)
    rates = {w["rate"] for w in workers}
    if len(shares) >= 2:
        world.flags["mq_split"] = max(world.flags.get("mq_split", 0), len(shares))
    if len(workers) < 2:
        if len(bodies) > 1:
            io.print(c("(one consumer attached, so it took the lot — there is nobody to share "
                       "with. Start BOTH workers first, THEN run the producer: that is why the "
                       "class opens two terminals before it fires the batch.)", "dim"))
    elif fair_all:
        io.print(c(f"(the broker's books right now: 0 ready, {_unacked(world)} unacknowledged — "
                   "prefetch_count=1 means each worker is holding exactly one message and will not "
                   "be handed another until it acks. Kill one now and that message is not lost.)",
                   "dim"))
        if len(rates) > 1 and len(shares) >= 2:
            fast = min(workers, key=lambda w: w["rate"])
            slow = max(workers, key=lambda w: w["rate"])
            world.flags["mq_fair"] = True
            io.print(c(f"(fair dispatch: {fast['name']} took "
                       f"{len(got[workers.index(fast)])} and {slow['name']} took "
                       f"{len(got[workers.index(slow)])} — the share followed the SPEED, nobody "
                       f"queued behind the slow one, and the whole batch is clear at "
                       f"{int(max(free))}s.)", "dim"))
    elif any(w["fair"] for w in workers):
        # QoS is per-CONSUMER, not per-queue: one worker without it is enough to
        # unbalance the whole thing, and that is worth saying out loud.
        greedy = [w for w in workers if not w["fair"]]
        io.print(c(f"(mixed prefetch — {len(greedy)} of the {len(workers)} consumers here "
                   f"{'has' if len(greedy) == 1 else 'have'} no basic_qos at all, and a consumer "
                   "without it takes everything it is offered. Hence the split "
                   f"{' / '.join(str(s) for s in shares)}. QoS is set per CONSUMER on its own "
                   "channel, so one greedy worker is enough to starve a careful one.)", "dim"))
    else:
        io.print(c(f"(the broker's books right now: 0 ready, 0 unacknowledged — auto_ack=True "
                   f"ticked all {len(bodies)} messages off the instant they went out the door. "
                   "list_queues reads empty while the work is still sitting in the workers' "
                   "buffers.)", "dim"))
        if len(rates) > 1 and len(shares) >= 2:
            world.flags["mq_hog"] = True
            waste = int(max(free) - min(f for f in free if f))
            # What these same workers COULD have done: throughput is the sum of
            # their rates, so the batch has no business taking longer than that.
            ideal = len(bodies) / sum(1.0 / w["rate"] for w in workers)
            io.print(c(f"(and look at the split: {' / '.join(str(s) for s in shares)} — blind "
                       "round-robin handed the slow worker exactly as many as the fast one. The "
                       f"fast one goes idle {waste}s early while the slow one is still grinding, "
                       f"so the last order isn't done until {int(max(free))}s — these two workers "
                       f"between them could have cleared it in {int(ideal)}s. That gap is what "
                       "basic_qos(prefetch_count=1) closes.)", "dim"))


def _consumer_multi(world, m, io):
    """`consumer-multi.py` — the class's work-queue script. The `&` is not
    decoration: `start_consuming()` blocks, so without it there is no second
    worker and no round-robin to look at."""
    line = m.group(0).strip()
    if not _script(world, io, "consumer-multi.py"):
        return
    if not _broker_up(world):
        _refused(world, io)
        return
    if not _declare(world, io, "--durable" in line, "python consumer-multi.py"):
        return
    fair, slow = "--fair" in line, "--slow" in line
    name = f"consumer-{random.randbytes(3).hex()}"
    if not line.endswith("&"):
        io.print(f"Consumer started: {name}")
        io.print(c("^C   (…and there it sat. start_consuming() blocks the terminal forever — that "
                   "is why the class opens a SECOND terminal for the second worker. This shell has "
                   f"one prompt, so background it instead and both stay alive: {line} &)", "dim"))
        world.flags["_noop"] = True
        return

    workers = _running(world)
    # bash reuses %1 the moment the job table empties — so do we, or `kill %1`
    # stops meaning what the player just read in `jobs`.
    job = max((w["job"] for w in workers), default=0) + 1
    pid = 3400 + job * 37 + random.randint(0, 9)
    workers.append({"job": job, "pid": pid, "name": name,
                    "fair": fair, "slow": slow, "rate": 6 if slow else 2, "cmd": line,
                    "unacked": [], "buffer": [], "handled": 0})
    io.print(f"[{job}] {pid}")                      # bash's own "job started" line
    io.print(f"Consumer started: {name}")
    if len(workers) == 2 and not world.flags.get("taught_competing"):
        world.flags["taught_competing"] = True
        io.print(c("(two consumers on ONE queue = competing consumers. No code change, no "
                   "coordinator, no config — the broker round-robins between them the moment the "
                   "second one attaches. That is the whole scaling story.)", "dim"))
    _dispatch(world, io, f"{name} attached")


def _jobs(world, m, io):
    """bash's own job table, which is the only place a background worker is
    visible — plus what each one is holding, which bash could never tell you."""
    world.flags["_noop"] = True
    workers = _running(world)
    if not workers:
        io.print(c("(no background jobs — real bash prints nothing at all here. A trailing & is "
                   "what puts a process in this table and hands your prompt back.)", "dim"))
        return
    for n, w in enumerate(workers):
        mark = "+" if n == len(workers) - 1 else ("-" if n == len(workers) - 2 else " ")
        io.print(f"[{w['job']}]{mark}  {'Running':<24}{w['cmd']}")
    for w in workers:
        state = (f"{w['handled']} acked · {len(w['unacked'])} unacked in hand" if w["fair"]
                 else f"{w['handled']} auto-acked · {len(w['buffer'])} still unprocessed")
        io.print(c(f"   {w['name']}  pid {w['pid']}  "
                   f"{'auto_ack=False, prefetch_count=1' if w['fair'] else 'auto_ack=True, no QoS  '}"
                   f"  · {state}", "dim"))


def _fg_bg(world, m, io):
    """`fg` and `bg` — the two job-control verbs this prompt cannot honour.

    A student who has just been taught `&`, `jobs` and `kill %1` reaches for
    `fg` next, and "isn't part of this simulated world" tells them nothing.
    Say what the command does and why THIS shell can't, the way the Linux
    missions answer a foreground `sleep`."""
    world.flags["_noop"] = True
    verb = m.group(0).split()[0]
    workers = _running(world)
    which = workers[-1] if workers else None
    if verb == "fg":
        io.print(c(f"(`fg` pulls a background job back into the FOREGROUND — "
                   f"{'%' + str(which['job']) + ', ' + which['cmd'] if which else 'the last one'}"
                   " — and then blocks this prompt until it exits. A consumer never exits, so "
                   "real bash would sit there until you hit Ctrl+C. That is exactly why this "
                   "mission backgrounds everything with `&`.)", "dim"))
    else:
        io.print(c("(`bg` RESUMES a job that was stopped with Ctrl+Z, in the background. Nothing "
                   "here is stopped — a job in this world is either running or killed, because "
                   "there is no Ctrl+Z to send it.)", "dim"))
    io.print(c(f"   what does work: `jobs` to see the table"
               f"{', `kill %' + str(which['job']) + '` to stop that one' if which else ''}"
               ", `pkill -f consumer-multi.py` to stop them all.", "dim"))


def _stop_worker(world, io, w, hard):
    """What actually happens to the messages a dying consumer was holding — the
    reason `basic_ack` exists, in twenty lines."""
    workers = _running(world)
    mark = "+" if w is workers[-1] else ("-" if len(workers) > 1 and w is workers[-2] else " ")
    workers.remove(w)
    io.print(f"[{w['job']}]{mark}  {'Killed' if hard else 'Terminated':<24}{w['cmd']}")
    q = _q(world)
    if w["buffer"]:
        world.flags["mq_lost"] = world.flags.get("mq_lost", 0) + len(w["buffer"])
        io.print(c(f"({len(w['buffer'])} message(s) went with it. auto_ack=True acked every one at "
                   "delivery, so as far as the broker is concerned that work is DONE — nothing to "
                   "redeliver, nothing to notice, nobody will ever redo it.)", "dim"))
    if w["unacked"] and q is not None:
        for msg in reversed(w["unacked"]):
            msg["redelivered"] = True
            q["msgs"].insert(0, msg)               # unacked work goes back at the HEAD
        _sync(world)
        io.print(c(f"({w['name']} died holding {len(w['unacked'])} unacknowledged message(s). It "
                   "never sent basic_ack, so the moment its connection dropped the broker put "
                   "it straight back on the queue — redelivered=True.)", "dim"))
        if _running(world):
            world.flags["mq_redelivered"] = (world.flags.get("mq_redelivered", 0)
                                             + len(w["unacked"]))
            _dispatch(world, io, f"requeued from {w['name']}")
        else:
            io.print(c("(no consumer left to take it, so it is simply waiting — ready count is "
                       "back up. Start another worker and it gets picked up.)", "dim"))
    w["unacked"], w["buffer"] = [], []


def _kill(world, m, io):
    hard = bool(re.search(r"-(?:9|KILL)\b", m.group(0)))
    target = m.group(1)
    workers = _running(world)
    if target.startswith("%"):
        w = next((x for x in workers if str(x["job"]) == target[1:]), None)
        miss = f"bash: kill: {target}: no such job"
    else:
        w = next((x for x in workers if str(x["pid"]) == target), None)
        miss = f"bash: kill: ({target}) - No such process"
    if w is None:
        io.print(miss)
        io.print(c("(`jobs` lists what is actually running — %1 is the first background job, and a "
                   "bare number is a PID)", "dim"))
        world.flags["_noop"] = True
        return
    _stop_worker(world, io, w, hard)


def _killall(world, m, io):
    workers = _running(world)
    if not workers:
        if m.group(0).startswith("pkill"):
            # Real pkill matches nothing, says nothing, and exits 1 — the silence
            # is the answer, so explain it instead of inventing an error line.
            io.print(c("(pkill printed nothing and exited 1 — it matched no process. `jobs` is "
                       "where you check what is actually running.)", "dim"))
        else:
            io.print("killall: consumer-multi.py: no process found")
        world.flags["_noop"] = True
        return
    for w in list(workers):
        _stop_worker(world, io, w, hard=False)


def _disconnect_workers(world, io):
    """A consumer is a client, not part of the server: when the broker process
    goes, so does every AMQP connection — and stopping the broker does not
    restart the workers afterwards."""
    workers = _running(world)
    if not workers:
        return
    q = _q(world)
    for w in workers:
        io.print(f"[{w['job']}]   {'Exited':<24}{w['cmd']}")
        if w["buffer"]:
            world.flags["mq_lost"] = world.flags.get("mq_lost", 0) + len(w["buffer"])
        if q is not None:
            for msg in reversed(w["unacked"]):
                msg["redelivered"] = True
                q["msgs"].insert(0, msg)
    io.print(c(f"({len(workers)} consumer(s) died with the broker — "
               "pika.exceptions.StreamLostError: transport indicated EOF. Unacked messages go back "
               "on the queue; the workers do NOT come back when the broker does. Restart them "
               "yourself.)", "dim"))
    workers.clear()
    _sync(world)


# --------------------------------------------------------- broker lifecycle --
def _broker_down(world, io, wipe_disk=False):
    """What losing the broker process does to a queue. Non-durable: the queue
    itself only ever existed in RAM. Durable: the queue and its persistent
    messages are on disk — unless the disk went too, which is what removing a
    container with no named volume does."""
    _disconnect_workers(world, io)     # the consumers lose their socket first
    q = _q(world)
    if q is None:
        return
    if wipe_disk:
        world.flags.pop("mq_queue", None)
        _sync(world)
        io.print(c("(that removed the CONTAINER, and this compose file declares no volume — so "
                   "/var/lib/rabbitmq went with its writable layer. durable=True survives a "
                   "restart, not a `down`. Real deployments mount a named volume.)", "dim"))
        return
    if not q["durable"]:
        lost = len(q["msgs"])
        world.flags.pop("mq_queue", None)
        world.flags["transient_lost"] = world.flags.get("transient_lost", 0) + lost
        _sync(world)
        io.print(c(f"(the broker process is gone — and so is '{QUEUE}' itself. It was declared "
                   "durable=False, so the queue only ever existed in the broker's memory"
                   + (f", and its {lost} message(s) with it. Nothing was written to disk, so "
                      "there is nothing to recover.)" if lost else
                      ". Nothing was written to disk: the next declare starts from scratch.)"),
                   "dim"))
        return
    kept = [msg for msg in q["msgs"] if msg["persistent"]]
    dropped = len(q["msgs"]) - len(kept)
    q["msgs"] = kept
    _sync(world)
    if dropped:
        io.print(c(f"({dropped} message(s) were published without delivery_mode=2 and died with "
                   "the process. The durable QUEUE saved itself; only persistent MESSAGES save "
                   "themselves.)", "dim"))


def _broker_boot(world, io):
    """Coming back up: a durable queue is recovered from disk, and the boot log
    says so — which is where you check it in real life."""
    ctr = world.containers.get("rabbitmq")
    q = _q(world)
    recovered = len(q["msgs"]) if q and q["durable"] else 0
    if ctr:
        ctr["logs"] = BOOT_LOG + (
            f"\nRecovering {1 if q and q['durable'] else 0} queues and 7 exchanges\n"
            f"Message store: recovered {recovered} persistent message(s)" if q and q["durable"]
            else "")
    if q and q["durable"]:
        world.flags["durable_survived"] = max(world.flags.get("durable_survived", 0), recovered)
        io.print(c(f"('{QUEUE}' came back from disk with {recovered} message(s) still in it — "
                   "durable=True on the queue plus delivery_mode=2 on each message is what "
                   "bought that. Check it yourself: rabbitmqctl list_queues)", "dim"))


def _lifecycle(world, m, io):
    """docker / docker compose  stop · start · restart · down — the commands the
    durability drill is made of. They go through here so the queue in memory
    dies exactly when the broker process does."""
    args = m.group(0).split()
    compose = args[1] == "compose"
    verb = args[2] if compose else args[1]
    names = [a for a in args[(3 if compose else 2):] if not a.startswith("-")]
    if compose and not names:
        names = ["rabbitmq"]                 # compose acts on every service; there is one
    if "rabbitmq" not in names:
        do_docker(world, args[1:], io)       # some other container: engine's business
        return

    if verb == "down":
        do_docker(world, ["compose", "down"], io)
        _broker_down(world, io, wipe_disk=True)
    elif verb == "stop":
        if compose:
            for n in names:
                if n in world.containers:
                    world.containers[n]["status"] = "exited"
                    io.print(f" ✔ Container {n}  Stopped")
        else:
            do_docker(world, ["stop"] + names, io)
        _broker_down(world, io)
    elif verb == "start":
        if compose:
            for n in names:
                if n in world.containers:
                    world.containers[n]["status"] = "running"
                    io.print(f" ✔ Container {n}  Started")
        else:
            do_docker(world, ["start"] + names, io)
        _broker_boot(world, io)
    else:                                     # restart = stop + start, one prompt
        if "rabbitmq" not in world.containers:
            io.print("Error response from daemon: No such container: rabbitmq")
            world.flags["_noop"] = True
            return
        if compose:
            io.print(f"[+] Restarting {len(names)}/{len(names)}")
            io.print(" ✔ Container rabbitmq  Started")
        else:
            io.print("rabbitmq")
        world.containers["rabbitmq"]["status"] = "running"
        _broker_down(world, io)
        _broker_boot(world, io)
    world.flags["restarts"] = world.flags.get("restarts", 0) + 1


def _compose_up(world, m, io):
    """`compose up` is the engine's job — but whether the queue survives depends
    on whether this is the SAME container coming back or a brand new one."""
    fresh = "rabbitmq" not in world.containers
    do_docker(world, m.group(0).split()[1:], io)
    if fresh and _q(world):
        _broker_down(world, io, wipe_disk=True)
    elif not fresh:
        _broker_boot(world, io)


# ---------------------------------------------------------- pika the client --
def _producer(world, m, io):
    if not _script(world, io, "producer.py"):
        return
    if not _broker_up(world):
        _refused(world, io)
        return
    durable = "--durable" in m.group(0)
    if not _declare(world, io, durable, "python producer.py"):
        return
    orders = [f"Order #{i}" for i in range(1, 21)]
    _elide(io, orders, lambda o: f"Sent: {o}")
    io.print(c("(the real script sleeps 1s between sends — 20 seconds of the Ready count climbing "
               "in the UI at :15672. Here they land at once; the depth is the part that matters.)",
               "dim"))
    for body in orders:
        _publish(world, body, persistent=durable)
    world.flags["produced"] = True
    _dispatch(world, io, "producer.py")


def _consumer(world, m, io):
    if not _script(world, io, "consumer.py"):
        return
    if not _broker_up(world):
        _refused(world, io)
        return
    if not _declare(world, io, "--durable" in m.group(0), "python consumer.py"):
        return
    io.print("Waiting for messages. Press CTRL+C to exit.")
    msgs = _q(world)["msgs"]
    if not msgs:
        io.print(c("(…silence. Nothing in the queue — a consumer can only wait. That is not a "
                   "failure: produce something and it starts printing.)", "dim"))
        world.flags["_noop"] = True
        return
    _elide(io, list(msgs),
           lambda msg: f"Received: {msg['body']}" + ("   (redelivered)" if msg["redelivered"] else ""))
    msgs.clear()
    _sync(world)
    world.flags["consumed"] = True
    io.print(c("^C  (drained and acked one by one — the queue is empty and the work is done)", "dim"))


def _worker(world, m, io):
    """The ack drill: same queue, same messages, two ack modes, one kill -9."""
    if not _script(world, io, "worker.py"):
        return
    if not _broker_up(world):
        _refused(world, io)
        return
    line = m.group(0)
    auto = "--auto-ack" in line
    if not _declare(world, io, "--durable" in line, "python worker.py"):
        return
    name = f"worker-{random.randbytes(3).hex()}"
    mode = "auto_ack=True, no basic_qos" if auto else "auto_ack=False, basic_qos(prefetch_count=1)"
    io.print(f"[{name}] waiting for messages.   ({mode})")
    msgs = _q(world)["msgs"]
    if not msgs:
        io.print(c("(…silence — nothing to work on. Refill the queue: python producer.py)", "dim"))
        world.flags["_noop"] = True
        return
    if len(msgs) < 3:
        for msg in msgs:
            io.print(f"[{name}] done: {msg['body']}")
        msgs.clear()
        _sync(world)
        io.print(c("(this worker only dies on its THIRD message and the queue ran out first — "
                   "put 20 back in with producer.py to see the kill)", "dim"))
        return

    done, victim = msgs[:2], msgs[2]
    for msg in done:
        io.print(f"[{name}] done: {msg['body']}")
    io.print(f"[{name}] killed mid-job on {victim['body']}")
    if auto:
        # No QoS at all: the broker pushes everything it has at the one consumer
        # and auto_ack ticks each message off as it goes out the door. The queue
        # is empty long before the work is.
        lost = len(msgs) - 2
        msgs.clear()
        _sync(world)
        world.flags["autoack_lost"] = world.flags.get("autoack_lost", 0) + lost
        io.print(c(f"(kill -9, and {lost} message(s) are simply GONE. auto_ack=True acked every "
                   "one the instant it was delivered, and with no basic_qos the broker had already "
                   f"emptied the whole queue into this process. list_queues will read 0 — and "
                   "nobody did the work.)", "dim"))
    else:
        del msgs[:2]
        msgs[0]["redelivered"] = True
        _sync(world)
        world.flags["requeued"] = world.flags.get("requeued", 0) + 1
        io.print(c(f"(0 lost. '{victim['body']}' was never acked, so when the connection dropped "
                   f"the broker put it straight back at the head of the queue — redelivered=True, "
                   f"{len(msgs)} ready. prefetch_count=1 is why exactly ONE message was ever at "
                   "risk instead of the whole backlog.)", "dim"))


# ------------------------------------------------------------- rabbitmqctl --
# `messages` is ready + unacknowledged, exactly as the real column is: a message
# a consumer is holding but hasn't acked still belongs to the queue.
QCOLS = {"name": lambda w, q: QUEUE,
         "messages": lambda w, q: len(q["msgs"]) + _unacked(w),
         "messages_ready": lambda w, q: len(q["msgs"]),
         "messages_unacknowledged": lambda w, q: _unacked(w),
         "consumers": lambda w, q: _consumers(w),
         "durable": lambda w, q: str(q["durable"]).lower(),
         "state": lambda w, q: "running"}

EXCHANGES = [("", "direct"), ("amq.direct", "direct"), ("amq.fanout", "fanout"),
             ("amq.headers", "headers"), ("amq.match", "headers"),
             ("amq.rabbitmq.trace", "topic"), ("amq.topic", "topic")]


def _on_node(world, io, ctr, tool):
    """rabbitmqctl and rabbitmq-diagnostics drive the Erlang node directly, not
    AMQP — so they only exist ON the broker, and only while it is running."""
    if ctr is None and world.inside != "rabbitmq":
        io.print(f"{tool}: command not found")
        io.print(c(f"({tool} is the BROKER's own CLI — it ships inside the rabbitmq image and "
                   "speaks to the Erlang node, not AMQP. Run it there: "
                   f"docker exec rabbitmq {tool} …)", "dim"))
    elif ctr is not None and ctr not in world.containers:
        io.print(f"Error response from daemon: No such container: {ctr}")
    elif not _broker_up(world):
        io.print("Error: unable to perform an operation on node 'rabbit@rabbitmq'. "
                 "Please see diagnostics information and suggestions below.")
        io.print(c(f"(the node is down — {tool} needs the broker running. docker compose up -d)",
                   "dim"))
    else:
        return True
    world.flags["_noop"] = True
    return False


def _rabbitmqctl(world, m, io):
    ctr, argline = m.group(1), m.group(2)
    if not _on_node(world, io, ctr, "rabbitmqctl"):
        return

    parts = argline.split()
    sub, rest = parts[0], parts[1:]
    q = _q(world)

    if sub == "list_queues":
        world.flags["_noop"] = True             # pure inspection
        bad = [k for k in rest if k not in QCOLS]
        if bad:
            io.print(f"Error: invalid queue info key: {bad[0]}")
            io.print(c(f"(valid here: {' '.join(QCOLS)})", "dim"))
            return
        cols = rest or ["name", "messages"]
        io.print("Timeout: 60.0 seconds ...")
        io.print("Listing queues for vhost / ...")
        if q is None:
            io.print(c("(no queues at all. Not 'zero messages' — the queue itself is not there. "
                       "Something has to declare it before anything can be published.)", "dim"))
            return
        io.print("\t".join(cols))
        io.print("\t".join(str(QCOLS[k](world, q)) for k in cols))
        world.flags["queue_inspected"] = True
        if "consumers" in cols:
            world.flags["mq_consumers_seen"] = max(world.flags.get("mq_consumers_seen", 0),
                                                   _consumers(world))

    elif sub == "list_exchanges":
        world.flags["_noop"] = True
        io.print("Listing exchanges for vhost / ...")
        io.print("name\ttype")
        for name, kind in EXCHANGES:
            io.print(f"{name}\t{kind}")
        io.print(c("(the nameless first row IS the default exchange — pika's exchange='' and the "
                   "HTTP API's amq.default. It routes by queue name. direct matches a binding key, "
                   "fanout copies to EVERY bound queue, topic pattern-matches: logs.*.error.)",
                   "dim"))
        world.flags["exchanges_listed"] = True

    elif sub == "list_consumers":
        world.flags["_noop"] = True
        io.print("Listing consumers on vhost / ...")
        for w in _running(world):
            io.print(f"{QUEUE}\t<rabbit@rabbitmq.{w['pid']}.0>\t{w['name']}\t"
                     f"{str(w['fair']).lower()}\t{1 if w['fair'] else 0}")
        if _consumers(world) > 1:
            world.flags["mq_consumers_seen"] = max(world.flags.get("mq_consumers_seen", 0),
                                                   _consumers(world))
            io.print(c("(queue · channel · consumer tag · ack_required · prefetch_count. Several "
                       "rows on ONE queue IS the competing-consumers pattern — the same number the "
                       "UI shows in its Consumers column at :15672.)", "dim"))
        elif _consumers(world):
            io.print(c("(queue · channel · consumer tag · ack_required · prefetch_count. One row = "
                       "one consumer, so this queue has nobody to round-robin with yet.)", "dim"))
        else:
            io.print(c("(none attached. A consumer exists only while its process is alive — start "
                       "one in the background: python consumer-multi.py &)", "dim"))

    elif sub in ("purge_queue", "delete_queue"):
        target = rest[0] if rest else None
        if target != QUEUE or q is None:
            io.print(f"Error: queue '{target}' in vhost '/' not found")
            world.flags["_noop"] = True
            return
        n = len(q["msgs"])
        if sub == "purge_queue":
            io.print(f"Purging queue '{QUEUE}' in vhost '/' ...")
            q["msgs"].clear()
            io.print(c(f"({n} message(s) dropped — the queue stays, its contents don't)", "dim"))
        else:
            io.print(f"Deleting queue '{QUEUE}' in vhost '/' ...")
            world.flags.pop("mq_queue", None)
            io.print(c(f"(queue and its {n} message(s) gone. This is the way out of a 406 "
                       "PRECONDITION_FAILED: durability can't be changed, only re-declared from "
                       "scratch.)", "dim"))
        _sync(world)

    elif sub in ("status", "ping"):
        world.flags["_noop"] = True
        io.print("Status of node rabbit@rabbitmq ...")
        io.print("Runtime\n  OS PID: 1\n  Erlang version: 26.2")
        io.print("Config files\n  /etc/rabbitmq/conf.d/10-default-guest-user.conf")
        io.print("Listeners\n  interface: [::], port: 5672, protocol: amqp\n"
                 "  interface: [::], port: 15672, protocol: http")
        world.flags["broker_pinged"] = True

    else:
        world.flags["_noop"] = True
        io.print(f"Error: unknown command: {sub}")
        io.print(c("(this sandbox answers: list_queues [cols…] · list_exchanges · list_consumers · "
                   "purge_queue <q> · delete_queue <q> · status)", "dim"))


def _diagnostics(world, m, io):
    """`rabbitmq-diagnostics ping` is the readiness check people actually script
    (it's what a compose healthcheck runs), so it answers here too."""
    if not _on_node(world, io, m.group(1), "rabbitmq-diagnostics"):
        return
    world.flags["_noop"] = True
    io.print("Ping rabbit@rabbitmq ...")
    io.print("Ping succeeded")
    io.print(c("(this is the honest readiness probe — 'the container is Up' only means the process "
               "started, not that the listeners are accepting)", "dim"))
    world.flags["broker_pinged"] = True


# ------------------------------------------------------------- the HTTP API --
def _curl(world, m, io):
    """The management API on 15672 — publishing with nothing but curl, and the
    %2F vhost that everybody gets wrong the first time."""
    try:
        args = shlex.split(m.group(0))
    except ValueError:
        args = m.group(0).split()
    url, user, data, method = None, None, None, "GET"
    skip = False
    for i, a in enumerate(args[1:], 1):
        if skip:
            skip = False
            continue
        if a in ("-u", "--user"):
            user, skip = args[i + 1] if i + 1 < len(args) else "", True
        elif a in ("-d", "--data", "--data-raw"):
            data, skip, method = (args[i + 1] if i + 1 < len(args) else ""), True, "POST"
        elif a in ("-X", "--request"):
            method, skip = (args[i + 1] if i + 1 < len(args) else "GET"), True
        elif a in ("-H", "--header", "-o", "--output"):
            skip = True
        elif a.startswith("-"):
            continue
        else:
            url = url or a

    # Split the SCHEME off, not every "//" — the vhost trap this mission teaches
    # is a URL with a literal / in the path, and that must survive parsing.
    bare = (url or "").split("://", 1)[-1]
    port = bare.partition("/")[0].partition(":")[2] or "80"
    path = "/" + bare.partition("/")[2]

    if port == "5672":
        io.print("curl: (52) Empty reply from server")
        io.print(c("(5672 speaks AMQP — a binary protocol, not HTTP. That door is pika's. The "
                   "browser UI and this HTTP API both live on 15672.)", "dim"))
        world.flags["_noop"] = True
        return
    if not _broker_up(world):
        _refused(world, io, port=15672)
        return
    if not path.startswith("/api"):
        io.print("<!DOCTYPE html>")
        io.print("<html><head><title>RabbitMQ Management</title>")
        io.print(c("(that's the UI's HTML — in a browser it's the Queues tab, guest/guest. "
                   "Headless, everything the UI does is under /api/… )", "dim"))
        world.flags["_noop"] = True
        return
    if user != "guest:guest":
        io.print('{"error":"not_authorised","reason":"Login failed"}')
        io.print(c("(the API is authenticated like the UI: curl -u guest:guest … — and guest only "
                   "works from localhost, by design)", "dim"))
        world.flags["_noop"] = True
        return

    seg = [s for s in path.split("/") if s][1:]        # drop the leading "api"
    if seg[:1] == ["queues"]:
        if len(seg) > 1 and seg[1] != "%2F":
            _not_found(world, io)
            return
        q = _q(world)
        io.print(json.dumps({"name": QUEUE, "vhost": "/", "durable": bool(q and q["durable"]),
                             "messages": _depth(world) + _unacked(world),
                             "messages_ready": _depth(world),
                             "messages_unacknowledged": _unacked(world),
                             "consumers": _consumers(world)}
                            if q else {"error": "Object Not Found", "reason": "Not Found"}))
        io.print(c("(this is the JSON behind the UI's Queues tab — the same numbers, scriptable. "
                   "A monitoring check is a curl away.)", "dim"))
        if q:
            world.flags["mq_consumers_seen"] = max(world.flags.get("mq_consumers_seen", 0),
                                                   _consumers(world))
        world.flags["_noop"] = True
        return

    if seg[:1] != ["exchanges"] or seg[-1:] != ["publish"]:
        _not_found(world, io)
        return
    if len(seg) < 4 or seg[1] != "%2F":
        io.print('{"error":"Object Not Found","reason":"Not Found"}')
        io.print(c("(the vhost is a PATH SEGMENT, so the default vhost '/' has to be written %2F. "
                   "A literal / just makes up a route that doesn't exist: "
                   ".../api/exchanges/%2F/amq.default/publish)", "dim"))
        world.flags["_noop"] = True
        return
    if method != "POST":
        io.print('{"error":"Method Not Allowed","reason":"Only POST is allowed on publish"}')
        io.print(c("(-d is what turns curl into a POST — with no body it just GETs)", "dim"))
        world.flags["_noop"] = True
        return
    try:
        body = json.loads(data or "")
    except (TypeError, ValueError):
        io.print('{"error":"bad_request","reason":"Body must be a JSON object"}')
        io.print(c('(it needs routing_key, payload and payload_encoding — mind the quoting: '
                   "wrap the whole JSON in single quotes)", "dim"))
        world.flags["_noop"] = True
        return

    exchange = seg[2]
    key = body.get("routing_key", "")
    payload = body.get("payload", "")
    persistent = str(body.get("properties", {}).get("delivery_mode", "")) == "2"
    routed = exchange in ("amq.default", "") and _publish(world, payload, persistent, key)
    io.print(json.dumps({"routed": routed}))
    if routed:
        world.flags["http_published"] = True
        io.print(c("(routed:true — amq.default is the same default exchange pika means by "
                   "exchange='', and it matched routing_key to the queue NAME. No Python, no pika, "
                   "no AMQP: publishing is just an HTTP call.)", "dim"))
        _dispatch(world, io, "curl publish")   # a live worker prints it, same as in class
    else:
        world.flags["_noop"] = True
        io.print(c(f"(routed:false — the message was accepted by exchange '{exchange or 'amq.default'}' "
                   f"and then dropped, because nothing is bound to it for routing key '{key}'. You "
                   "publish to an EXCHANGE; whether a queue catches it is a separate question.)",
                   "dim"))


def _not_found(world, io):
    io.print('{"error":"Object Not Found","reason":"Not Found"}')
    io.print(c("(this sandbox answers two routes: POST /api/exchanges/%2F/amq.default/publish and "
               "GET /api/queues/%2F/orders — the real API has ~100 more, all listed at "
               "http://localhost:15672/api/)", "dim"))
    world.flags["_noop"] = True


# Shared by both missions on this broker — which script exists is the mission's
# business (`_script` answers with python's own ENOENT), so the toolbox doesn't
# have to be split in two.
HANDLERS = [
    (r"(?:python3?\s+|\./)consumer-multi\.py(?:\s+-{0,2}[\w-]+)*\s*&?", _consumer_multi),
    (r"jobs(?:\s+-\w+)*", _jobs),
    (r"(?:fg|bg)(?:\s+%?\d+)?\s*$", _fg_bg),
    (r"kill(?:\s+-\w+)*\s+(%\d+|\d+)", _kill),
    (r"(?:pkill|killall)(?:\s+-\w+)*\s+\S*consumer-multi[\w.]*", _killall),
    (r"(?:python3?\s+|\./)producer\.py(?:\s+.*)?", _producer),
    (r"(?:python3?\s+|\./)consumer\.py(?:\s+.*)?", _consumer),
    (r"(?:python3?\s+|\./)worker\.py(?:\s+.*)?", _worker),
    (r"(?:docker\s+exec\s+(?:-\S+\s+)*(\S+)\s+)?rabbitmqctl\s+(.+)", _rabbitmqctl),
    (r"(?:docker\s+exec\s+(?:-\S+\s+)*(\S+)\s+)?rabbitmq-diagnostics\s+.+", _diagnostics),
    (r"curl\s+.*(?:5672|15672).*", _curl),
    (r"docker\s+compose\s+up(?:\s+.*)?", _compose_up),
    (r"docker\s+compose\s+(?:restart|stop|down)(?:\s+.*)?", _lifecycle),
    (r"docker\s+(?:restart|stop|start)\s+.+", _lifecycle),
]


MISSIONS = [
    {
        "id": "mq-01",
        "topic": "rabbitmq",
        "title": "Post Office 📨 — producers, queues, consumers",
        "vault_note": "Class 13 - RabbitMQ Messaging",
        "brief": ("The whole point of a queue: the sender and the receiver DON'T have to be\n"
                  "awake at the same time. Boot the broker with compose, fire 20 orders at\n"
                  "it with NO consumer alive, then find out what those messages are really\n"
                  "worth — the wrong ack mode and a broker restart each eat a queue for\n"
                  "breakfast. (cat the .py files — they're the class-13 scripts.)"),
        "world": {
            "images": ["rabbitmq:3-management"],
            "files": {
                "docker-compose.yaml": COMPOSE_YAML,
                "producer.py": PRODUCER_PY,
                "consumer.py": CONSUMER_PY,
                "worker.py": WORKER_PY,
            },
        },
        "help_lines": [
            "   broker: docker compose up -d · ps · logs · restart · down",
            "   pika:   python producer.py [--durable] · consumer.py · worker.py [--auto-ack]",
            "   broker CLI: docker exec rabbitmq rabbitmqctl list_queues [cols…] · list_exchanges "
            "· purge_queue orders · delete_queue orders",
            "   HTTP API: curl -u guest:guest http://localhost:15672/api/queues/%2F/orders",
            "   shell: ls · cat · echo · edit <file>",
        ],
        "handlers": HANDLERS,
        "objectives": [
            {"desc": "Boot the broker (detached!)", "xp": 15,
             "hint": "docker compose up -d — the compose file already describes the rabbitmq service.",
             "check": lambda w: w.flags.get("compose_up") and _broker_up(w)},
            {"desc": "Confirm the broker is actually ready", "xp": 10,
             "hint": "docker logs rabbitmq — look for 'Server startup complete'. "
                     "(docker exec rabbitmq rabbitmq-diagnostics ping works too.)",
             "check": lambda w: (w.flags.get("logs_rabbitmq") or w.flags.get("compose_logs")
                                 or w.flags.get("broker_pinged"))},
            {"desc": "Send 20 orders — with NO consumer running", "xp": 10,
             "hint": "python producer.py",
             "check": lambda w: w.flags.get("produced")},
            {"desc": "PROVE the queue is holding them (that's decoupling)", "xp": 15,
             "hint": "docker exec rabbitmq rabbitmqctl list_queues — add columns if you like: "
                     "list_queues name messages consumers.",
             "check": lambda w: w.flags.get("queue_inspected") and _depth(w) >= 20},
            {"desc": "Find the exchange every one of those messages went through", "xp": 10,
             "hint": "docker exec rabbitmq rabbitmqctl list_exchanges — look for the row with no name.",
             "check": lambda w: w.flags.get("exchanges_listed")},
            {"desc": "Publish an order over the HTTP API on 15672 — no Python", "xp": 20,
             "hint": "curl -u guest:guest -H \"content-type:application/json\" -X POST "
                     "-d '{\"properties\":{},\"routing_key\":\"orders\",\"payload\":\"Order created "
                     "from curl\",\"payload_encoding\":\"string\"}' "
                     "http://localhost:15672/api/exchanges/%2F/amq.default/publish",
             "check": lambda w: w.flags.get("http_published")},
            {"desc": "Watch auto-ack lose work: kill a worker mid-job", "xp": 20,
             "hint": "cat worker.py first — then python worker.py --auto-ack (it dies on its 3rd "
                     "message on purpose). Check the depth afterwards.",
             "check": lambda w: w.flags.get("autoack_lost", 0) >= 1},
            {"desc": "Do it right: manual ack + prefetch, kill it again, lose nothing", "xp": 20,
             "hint": "Refill the queue (python producer.py), then python worker.py with no flags — "
                     "auto_ack=False + basic_qos(prefetch_count=1).",
             "check": lambda w: w.flags.get("requeued", 0) >= 1},
            {"desc": "Restart the broker and find out what a transient queue is worth", "xp": 15,
             "hint": "docker compose restart rabbitmq — then list_queues again. Brace yourself.",
             "check": lambda w: w.flags.get("transient_lost", 0) >= 1},
            {"desc": "Make the messages survive a restart", "xp": 25,
             "hint": "python producer.py --durable (durable=True + delivery_mode=2), then restart "
                     "the broker again and count what came back.",
             "check": lambda w: w.flags.get("durable_survived", 0) >= 1},
            {"desc": "Drain the queue with the consumer", "xp": 10,
             "hint": "python consumer.py — and it must declare the queue the same way the producer "
                     "did: --durable.",
             "check": lambda w: w.flags.get("consumed") and _depth(w) == 0},
        ],
        "teach": [
            "compose up -d boots a whole service stack from one file — infrastructure as a checklist.",
            "The startup log IS the readiness check: 'Server startup complete' means 5672 and 15672 "
            "are accepting. 'Container Up' only means the process started.",
            "The producer ran with NO consumer alive — that's decoupling. queue_declare is "
            "idempotent, which is why producer and consumer both call it: whoever starts first "
            "creates the queue.",
            "Queue depth is the buffer made visible: 20 ready, 0 consumers, nothing lost. A rush "
            "piles up on the rail instead of taking the system down.",
            "You never publish to a queue — you publish to an EXCHANGE. '' (amq.default) routes by "
            "queue name; direct matches a binding key, fanout copies to every bound queue, topic "
            "pattern-matches (logs.*.error).",
            "5672 is AMQP for clients, 15672 is the management UI and its HTTP API — and %2F in "
            "the path is the default vhost '/', URL-encoded, because a vhost is a path segment.",
            "auto_ack=True deletes a message the moment it is delivered, and with no basic_qos the "
            "broker hands one consumer the entire queue — a single kill -9 and the work is gone.",
            "basic_ack + basic_qos(prefetch_count=1) means exactly one message is ever at risk: "
            "unacked work is requeued (redelivered=True) when the consumer dies.",
            "A non-durable queue lives only in the broker's memory — a restart takes the messages "
            "AND the queue itself with it.",
            "Surviving a restart takes both halves: durable=True on the queue and delivery_mode=2 "
            "on every message. Either one alone still loses the mail.",
            "A consumer drains a backlog on arrival and acks as it goes — sender and receiver never "
            "had to be awake at the same time. That is the entire point.",
        ],
        "solution": [
            "cat docker-compose.yaml",
            "docker compose up -d",
            "docker compose ps",
            "docker logs rabbitmq",
            "cat producer.py",
            "python producer.py",
            "docker exec rabbitmq rabbitmqctl list_queues name messages consumers",
            "docker exec rabbitmq rabbitmqctl list_exchanges",
            "curl -u guest:guest -H \"content-type:application/json\" -X POST -d "
            "'{\"properties\":{},\"routing_key\":\"orders\",\"payload\":\"Order created from curl\","
            "\"payload_encoding\":\"string\"}' "
            "http://localhost:15672/api/exchanges/%2F/amq.default/publish",
            "cat worker.py",
            "python worker.py --auto-ack",
            "docker exec rabbitmq rabbitmqctl list_queues",
            "python producer.py",
            "python worker.py",
            "docker exec rabbitmq rabbitmqctl list_queues name messages",
            "docker compose restart rabbitmq",
            "docker exec rabbitmq rabbitmqctl list_queues",
            "python producer.py --durable",
            "docker compose restart rabbitmq",
            "docker exec rabbitmq rabbitmqctl list_queues name messages durable",
            "python consumer.py --durable",
        ],
    },
    {
        "id": "mq-02",
        "topic": "rabbitmq",
        "title": "Ticket Rail 🍳 — competing consumers & fair dispatch",
        "vault_note": "Class 13 - RabbitMQ Messaging",
        "brief": ("One cook can't clear a dinner rush. Put TWO workers on the same queue and\n"
                  "RabbitMQ round-robins between them — horizontal scaling with no code\n"
                  "change and no coordinator. Then find out what 'round-robin' costs when\n"
                  "one worker is slower than the other, and what an ack is really for when\n"
                  "you kill one mid-ticket. (A background worker needs a trailing `&` —\n"
                  "`jobs` lists them, `kill %1` takes one out.)"),
        "world": {
            "images": ["rabbitmq:3-management"],
            "files": {
                "docker-compose.yaml": COMPOSE_YAML,
                "producer.py": PRODUCER_PY,
                "consumer.py": CONSUMER_PY,
                "consumer-multi.py": CONSUMER_MULTI_PY,
            },
        },
        "help_lines": [
            "   broker: docker compose up -d · ps · logs · restart",
            "   workers: python consumer-multi.py [--fair] [--slow] &   ← the & keeps your prompt",
            "   jobs · kill %1 · kill -9 <pid> · pkill -f consumer-multi.py",
            "   producer: python producer.py   ·   one-off consumer: python consumer.py",
            "   broker CLI: docker exec rabbitmq rabbitmqctl list_queues name messages_ready "
            "messages_unacknowledged consumers · list_consumers",
            "   shell: ls · cat · echo · edit <file>",
        ],
        "handlers": HANDLERS,
        "objectives": [
            {"desc": "Boot the broker", "xp": 10,
             "hint": "docker compose up -d — same compose file as the last mission.",
             "check": lambda w: w.flags.get("compose_up") and _broker_up(w)},
            {"desc": "Get TWO workers onto the 'orders' queue at the same time", "xp": 15,
             "hint": "cat consumer-multi.py, then start it twice — with a trailing & each time, "
                     "or the first one blocks the prompt forever: python consumer-multi.py &",
             "check": lambda w: _consumers(w) >= 2},
            {"desc": "Fire the 20 orders and watch them SPLIT across the two", "xp": 15,
             "hint": "python producer.py — with both workers already attached. Each line is "
                     "prefixed with the worker that got it.",
             "check": lambda w: w.flags.get("mq_split", 0) >= 2},
            {"desc": "Prove it at the broker: Consumers = 2 on one queue", "xp": 10,
             "hint": "docker exec rabbitmq rabbitmqctl list_queues name messages_ready "
                     "messages_unacknowledged consumers   (list_consumers names them)",
             "check": lambda w: w.flags.get("mq_consumers_seen", 0) >= 2},
            {"desc": "Scale out — a third worker, and everyone's share drops to a third", "xp": 15,
             "hint": "Start one more (python consumer-multi.py &) and re-run the producer. No "
                     "code changed, no config changed.",
             "check": lambda w: w.flags.get("mq_split", 0) >= 3},
            {"desc": "Make a SLOW worker hog its half — round-robin with no basic_qos", "xp": 20,
             "hint": "pkill -f consumer-multi.py, then start exactly two: one plain, one "
                     "--slow (6s a message). Run the producer and compare the shares.",
             "check": lambda w: w.flags.get("mq_hog")},
            {"desc": "Fix the dispatch with prefetch_count=1 — the share follows the speed", "xp": 20,
             "hint": "Same two workers, both with --fair (auto_ack=False + basic_ack + "
                     "basic_qos(prefetch_count=1)): python consumer-multi.py --fair & and "
                     "python consumer-multi.py --fair --slow &",
             "check": lambda w: w.flags.get("mq_fair")},
            {"desc": "Kill a worker mid-message — the unacked order must land on the survivor",
             "xp": 25,
             "hint": "Right after a --fair batch each worker is holding exactly one unacked "
                     "message (list_queues messages_unacknowledged says so). jobs, then kill %2.",
             "check": lambda w: w.flags.get("mq_redelivered", 0) >= 1},
        ],
        "teach": [
            "Same broker, same compose file — the workers are what changes. Scaling consumers "
            "never touches the thing holding the messages.",
            "start_consuming() blocks its terminal forever, so every extra worker needs its own "
            "terminal (or a trailing & here). Two processes on ONE queue is the whole pattern: "
            "competing consumers.",
            "RabbitMQ round-robins a queue across its consumers — each message goes to exactly ONE "
            "of them. That is a work queue; a fanout exchange copying to every bound queue is "
            "pub/sub, and mixing the two up is the interview question.",
            "The Consumers column is the proof that scaling worked, and messages_unacknowledged is "
            "the proof that work is in flight rather than done. Both are one rabbitmqctl away — "
            "and they are the same numbers the UI shows at :15672.",
            "Throughput scales by starting another process. No coordinator, no partition "
            "assignment, no producer change — that is why work queues are the default answer to "
            "'this step is too slow'.",
            "Without basic_qos the broker round-robins BLINDLY: the slow worker is handed exactly "
            "as many messages as the fast one and sits on them while the fast one goes idle. Even "
            "split, uneven work.",
            "basic_qos(prefetch_count=1) is fair dispatch: no new message until you ack the last "
            "one, so a fast worker takes more and a slow one takes fewer — and the batch finishes "
            "in the time the WORKERS can do, not the time the slowest one needs.",
            "An unacked message is still the queue's. When the consumer holding it dies, the broker "
            "requeues it (redelivered=True) and another worker picks it up. That is the difference "
            "between a crash costing a retry and a crash costing the order.",
        ],
        "solution": [
            "cat docker-compose.yaml",
            "docker compose up -d",
            "cat consumer-multi.py",
            "python consumer-multi.py &",
            "python consumer-multi.py &",
            "jobs",
            "python producer.py",
            "docker exec rabbitmq rabbitmqctl list_queues name messages_ready "
            "messages_unacknowledged consumers",
            "docker exec rabbitmq rabbitmqctl list_consumers",
            "python consumer-multi.py &",
            "python producer.py",
            "pkill -f consumer-multi.py",
            "python consumer-multi.py &",
            "python consumer-multi.py --slow &",
            "python producer.py",
            "pkill -f consumer-multi.py",
            "python consumer-multi.py --fair &",
            "python consumer-multi.py --fair --slow &",
            "python producer.py",
            "docker exec rabbitmq rabbitmqctl list_queues name messages_ready "
            "messages_unacknowledged consumers",
            "jobs",
            "kill %2",
            "docker exec rabbitmq rabbitmqctl list_queues name messages_ready "
            "messages_unacknowledged consumers",
        ],
    },
]
