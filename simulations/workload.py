import random
import task
import numpy


class Workload:

    def __init__(self, id_, latencyMonitor, clientList,
                 model, model_param, numRequests, simulation):
        self.latencyMonitor = latencyMonitor
        self.clientList = clientList
        self.model = model
        self.model_param = model_param
        self.numRequests = numRequests
        self.simulation = simulation
        self.total = sum(client.demandWeight for client in self.clientList)
        # self.proc = self.simulation.process(self.run(), 'Workload' + str(id_))

    # TODO: also need non-uniform client access
    # Need to pin workload to a client
    def run(self):

        task_counter = 0

        while self.numRequests != 0:
            yield self.simulation.timeout(0)

            task_to_schedule = task.Task("Task" + str(task_counter),
                                         self.latencyMonitor, self.simulation)
            task_counter += 1

            # Push out a task...
            client_node = self.weighted_choice()

            client_node.schedule(task_to_schedule)

            # Simulate client delay
            if self.model == "poisson":
                yield self.simulation.timeout(self.simulation.np_random.poisson(self.model_param))

            # If model is gaussian, add gaussian delay
            # If model is constant, add fixed delay
            if self.model == "constant":
                yield self.simulation.timeout(self.model_param)

            self.numRequests -= 1

    def weighted_choice(self):
        r = self.simulation.random.uniform(0, self.total)
        upto = 0
        for client in self.clientList:
            if upto + client.demandWeight > r:
                return client
            upto += client.demandWeight
        assert False, "Shouldn't get here"
