from global_sim import Simulation


class Task:
    """A simple Task. Applications may subclass this
       for holding specific attributes if need be"""

    def __init__(self, id_, latency_monitor) -> None:
        self.id: str = id_
        self.start: int = Simulation.now
        self.completion_event = Simulation.event()
        self.latency_monitor = latency_monitor

    # Used as a notifier mechanism
    def signal_task_complete(self, piggyback=None):
        if self.completion_event is not None:
            self.completion_event.succeed(piggyback)
