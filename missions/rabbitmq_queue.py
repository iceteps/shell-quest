"""RabbitMQ mission — compose up the broker, produce, inspect, consume.
The queue itself is simulated in world.flags; docker compose comes from
the engine."""
from engine import c

COMPOSE_YAML = '''services:
  rabbitmq:
    image: rabbitmq:3-management
    ports:
      - "5672:5672"
      - "15672:15672"
'''

PRODUCER_PY = '''import pika

connection = pika.BlockingConnection(pika.ConnectionParameters("localhost"))
channel = connection.channel()
channel.queue_declare(queue="hello")

for i in range(1, 6):
    channel.basic_publish(exchange="", routing_key="hello", body=f"Hello {i}")
    print(f" [x] Sent 'Hello {i}'")
connection.close()
'''

CONSUMER_PY = '''import pika

connection = pika.BlockingConnection(pika.ConnectionParameters("localhost"))
channel = connection.channel()
channel.queue_declare(queue="hello")

def callback(ch, method, properties, body):
    print(f" [v] Received {body.decode()}")

channel.basic_consume(queue="hello", on_message_callback=callback, auto_ack=True)
print(" [*] Waiting for messages. To exit press CTRL+C")
channel.start_consuming()
'''


def _broker_up(world):
    return any(n == "rabbitmq" and d["status"] == "running"
               for n, d in world.containers.items())


def _producer(world, m, io):
    if not _broker_up(world):
        io.print("pika.exceptions.AMQPConnectionError: [Errno 111] Connection refused")
        io.print(c("(no broker listening on localhost:5672 — is the rabbitmq container up?)", "dim"))
        return
    q = world.flags.get("queue_depth", 0)
    for i in range(1, 6):
        io.print(f" [x] Sent 'Hello {i}'")
    world.flags["queue_depth"] = q + 5
    world.flags["produced"] = True


def _consumer(world, m, io):
    if not _broker_up(world):
        io.print("pika.exceptions.AMQPConnectionError: [Errno 111] Connection refused")
        io.print(c("(no broker listening on localhost:5672 — is the rabbitmq container up?)", "dim"))
        return
    io.print(" [*] Waiting for messages. To exit press CTRL+C")
    q = world.flags.get("queue_depth", 0)
    if q == 0:
        io.print(c("(…silence. The queue is empty — produce something first, then run me again)", "dim"))
        return
    for i in range(1, q + 1):
        io.print(f" [v] Received Hello {i}")
    world.flags["queue_depth"] = 0
    world.flags["consumed"] = True
    io.print(c("^C  (all messages drained — the queue is empty again)", "dim"))


def _rabbitmqctl(world, m, io):
    if not _broker_up(world):
        io.print("Error: unable to perform an operation on node 'rabbit@localhost'. The node is down.")
        return
    io.print("Timeout: 60.0 seconds ...\nListing queues for vhost / ...")
    io.print(f"name\tmessages\nhello\t{world.flags.get('queue_depth', 0)}")
    world.flags["queue_inspected"] = True


MISSIONS = [
    {
        "id": "mq-01",
        "topic": "rabbitmq",
        "title": "Post Office 📨 — producers, queues, consumers",
        "vault_note": "Class 13 - RabbitMQ Messaging",
        "brief": ("The whole point of a queue: the sender and receiver DON'T have to be\n"
                  "awake at the same time. Boot the broker with compose, send messages\n"
                  "with producer.py while NO consumer exists, peek at the queue holding\n"
                  "them safely, then let consumer.py drain it. (cat the .py files —\n"
                  "they're the exact class-13 scripts.)"),
        "world": {
            "images": ["rabbitmq:3-management"],
            "files": {
                "docker-compose.yaml": COMPOSE_YAML,
                "producer.py": PRODUCER_PY,
                "consumer.py": CONSUMER_PY,
            },
        },
        "handlers": [
            (r"python3?\s+producer\.py", _producer),
            (r"python3?\s+consumer\.py", _consumer),
            (r"docker\s+exec\s+(-it\s+)?rabbitmq\s+rabbitmqctl\s+list_queues.*", _rabbitmqctl),
        ],
        "objectives": [
            {"desc": "Boot the broker (detached!)", "xp": 15,
             "hint": "docker compose up -d — the compose file already describes the rabbitmq service.",
             "check": lambda w: w.flags.get("compose_up") and _broker_up(w)},
            {"desc": "Confirm the broker is ready (read its logs)", "xp": 10,
             "hint": "docker logs rabbitmq — look for 'Server startup complete'.",
             "check": lambda w: w.flags.get("logs_rabbitmq") or w.flags.get("compose_logs")},
            {"desc": "Send 5 messages — with NO consumer running", "xp": 15,
             "hint": "python producer.py",
             "check": lambda w: w.flags.get("produced")},
            {"desc": "PROVE the queue is holding them (that's decoupling)", "xp": 20,
             "hint": "docker exec rabbitmq rabbitmqctl list_queues — depth should read 5.",
             "check": lambda w: w.flags.get("queue_inspected") and w.flags.get("queue_depth", 0) >= 5},
            {"desc": "Drain the queue with a consumer", "xp": 15,
             "hint": "python consumer.py",
             "check": lambda w: w.flags.get("consumed") and w.flags.get("queue_depth") == 0},
        ],
        "teach": [
            "compose up -d boots a whole service stack from one file — infrastructure as a checklist.",
            "The startup log is the readiness check — 'Server startup complete' means the listeners are up.",
            "The producer worked with NO consumer alive — decoupling is the entire point of a queue.",
            "rabbitmqctl list_queues shows depth: 5 messages waiting patiently, nothing lost.",
            "The consumer drained the backlog on arrival — sender and receiver never had to meet.",
        ],
        "solution": [
            "docker compose up -d",
            "docker logs rabbitmq",
            "python producer.py",
            "docker exec rabbitmq rabbitmqctl list_queues",
            "python consumer.py",
        ],
    },
]
