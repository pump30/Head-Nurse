import asyncio
import signal
import sys

from .config import Config
from .agent import KanbanAgent
from .logging_setup import setup_logging


def main():
    config = Config.load()
    setup_logging(config.log_file, config.log_level)

    agent = KanbanAgent(config)

    loop = asyncio.new_event_loop()

    if sys.platform != "win32":
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, agent.shutdown)
    else:
        signal.signal(signal.SIGINT, lambda *_: agent.shutdown())

    try:
        loop.run_until_complete(agent.run())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
