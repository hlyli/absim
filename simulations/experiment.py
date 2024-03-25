from global_sim import Simulation
import server
import client
import workload
import argparse
import random
import constants
import numpy as np
import sys
import muUpdater
from simulations.monitor import Monitor
from pathlib import Path
from model_trainer import Trainer


def printMonitorTimeSeriesToFile(fileDesc, prefix, monitor):
    for entry in monitor:
        fileDesc.write("%s %s %s\n" % (prefix, entry[0], entry[1]))


def rlExperimentWrapper(args):
    # Start the models and etc.
    # Adapted from https://pytorch.org/tutorials/intermediate/reinforcement_q_learning.html
    trainer = Trainer(args.num_servers)
    NUM_EPSIODES = 10

    for i_episode in range(NUM_EPSIODES):
        runExperiment(args, trainer)
    

def runExperiment(args, trainer : Trainer = None):
    # Set the random seed
    random.seed(args.seed)
    np.random.seed(args.seed)

    servers = []
    clients = []
    workloadGens = []

    constants.NW_LATENCY_BASE = args.nwLatencyBase
    constants.NW_LATENCY_MU = args.nwLatencyMu
    constants.NW_LATENCY_SIGMA = args.nwLatencySigma
    constants.NUMBER_OF_CLIENTS = args.numClients

    assert args.expScenario != ""

    serviceRatePerServer = []
    if (args.expScenario == "base"):
        # Start the servers
        for i in range(args.numServers):
            serv = server.Server(i,
                                 resourceCapacity=args.serverConcurrency,
                                 serviceTime=(args.serviceTime),
                                 serviceTimeModel=args.serviceTimeModel)
            servers.append(serv)
    elif (args.expScenario == "multipleServiceTimeServers"):
        # Start the servers
        for i in range(args.numServers):
            serv = server.Server(i,
                                 resourceCapacity=args.serverConcurrency,
                                 serviceTime=((i + 1) * args.serviceTime),
                                 serviceTimeModel=args.serviceTimeModel)
            servers.append(serv)
    elif (args.expScenario == "heterogenousStaticServiceTimeScenario"):
        baseServiceTime = args.serviceTime

        assert args.slowServerFraction >= 0 and args.slowServerFraction < 1.0
        assert args.slowServerSlowness >= 0 and args.slowServerSlowness < 1.0
        assert not (args.slowServerSlowness == 0
                    and args.slowServerFraction != 0)
        assert not (args.slowServerSlowness != 0
                    and args.slowServerFraction == 0)

        if (args.slowServerFraction > 0.0):
            slowServerRate = (args.serverConcurrency *
                              1 / float(baseServiceTime)) * \
                             args.slowServerSlowness
            numSlowServers = int(args.slowServerFraction * args.numServers)
            slowServerRates = [slowServerRate] * numSlowServers

            numFastServers = args.numServers - numSlowServers
            totalRate = (args.serverConcurrency *
                         1 / float(args.serviceTime) * args.numServers)
            fastServerRate = (totalRate - sum(slowServerRates)) \
                             / float(numFastServers)
            fastServerRates = [fastServerRate] * numFastServers
            serviceRatePerServer = slowServerRates + fastServerRates
        else:
            serviceRatePerServer = [args.serverConcurrency *
                                    1 / float(args.serviceTime)] * args.numServers

        random.shuffle(serviceRatePerServer)
        # print(sum(serviceRatePerServer), (1/float(baseServiceTime)) * args.numServers)
        assert sum(serviceRatePerServer) > 0.99 * \
               (1 / float(baseServiceTime)) * args.numServers
        assert sum(serviceRatePerServer) <= \
               (1 / float(baseServiceTime)) * args.numServers

        # Start the servers
        for i in range(args.numServers):
            st = 1 / float(serviceRatePerServer[i])
            serv = server.Server(i,
                                 resourceCapacity=args.serverConcurrency,
                                 serviceTime=st,
                                 serviceTimeModel=args.serviceTimeModel)
            servers.append(serv)
    elif (args.expScenario == "timeVaryingServiceTimeServers"):
        assert args.intervalParam != 0.0
        assert args.timeVaryingDrift != 0.0

        # Start the servers
        for i in range(args.numServers):
            serv = server.Server(i,
                                 resourceCapacity=args.serverConcurrency,
                                 serviceTime=(args.serviceTime),
                                 serviceTimeModel=args.serviceTimeModel)
            mup = muUpdater.MuUpdater(serv, args.intervalParam,
                                      args.serviceTime,
                                      args.timeVaryingDrift)
            Simulation.process(mup.run(), at=0.0)
            servers.append(serv)
    else:
        print("Unknown experiment scenario")
        sys.exit(-1)

    baseDemandWeight = 1.0
    clientWeights = []
    assert args.highDemandFraction >= 0 and args.highDemandFraction < 1.0
    assert args.demandSkew >= 0 and args.demandSkew < 1.0
    assert not (args.demandSkew == 0 and args.highDemandFraction != 0)
    assert not (args.demandSkew != 0 and args.highDemandFraction == 0)

    if (args.highDemandFraction > 0.0 and args.demandSkew >= 0):
        heavyClientWeight = baseDemandWeight * \
                            args.demandSkew / args.highDemandFraction
        numHeavyClients = int(args.highDemandFraction * args.numClients)
        heavyClientWeights = [heavyClientWeight] * numHeavyClients

        lightClientWeight = baseDemandWeight * \
                            (1 - args.demandSkew) / (1 - args.highDemandFraction)
        numLightClients = args.numClients - numHeavyClients
        lightClientWeights = [lightClientWeight] * numLightClients
        clientWeights = heavyClientWeights + lightClientWeights
    else:
        clientWeights = [baseDemandWeight] * args.numClients

    assert sum(clientWeights) > 0.99 * args.numClients
    assert sum(clientWeights) <= args.numClients

    # Start the clients
    for i in range(args.numClients):
        c = client.Client(id_="Client%s" % (i),
                          serverList=servers,
                          replicaSelectionStrategy=args.selectionStrategy,
                          accessPattern=args.accessPattern,
                          replicationFactor=args.replicationFactor,
                          backpressure=args.backpressure,
                          shadowReadRatio=args.shadowReadRatio,
                          rateInterval=args.rateInterval,
                          cubicC=args.cubicC,
                          cubicSmax=args.cubicSmax,
                          cubicBeta=args.cubicBeta,
                          hysterisisFactor=args.hysterisisFactor,
                          demandWeight=clientWeights[i],
                          trainer=trainer)
        clients.append(c)

    # Start workload generators (analogous to YCSB)
    latencyMonitor = Monitor(name="Latency")

    # This is where we set the inter-arrival times based on
    # the required utilization level and the service time
    # of the overall server pool.
    arrivalRate = 0
    interArrivalTime = 0
    if (len(serviceRatePerServer) > 0):
        print(serviceRatePerServer)
        arrivalRate = (args.utilization * sum(serviceRatePerServer))
        interArrivalTime = 1 / float(arrivalRate)
    else:
        arrivalRate = args.numServers * \
                      (args.utilization * args.serverConcurrency *
                       1 / float(args.serviceTime))
        interArrivalTime = 1 / float(arrivalRate)

    for i in range(args.numWorkload):
        w = workload.Workload(i, latencyMonitor,
                              clients,
                              args.workloadModel,
                              interArrivalTime * args.numWorkload,
                              args.numRequests / args.numWorkload)
        Simulation.process(w.run())
        workloadGens.append(w)

    # Begin simulation
    Simulation.run(until=args.simulationDuration)

    #
    # print(a bunch of timeseries)
    #

    exp_path = Path('..', args.logFolder, args.expPrefix)

    if not exp_path.exists():
        exp_path.mkdir(parents=True, exist_ok=True)

    pendingRequestsFD = open("../%s/%s_PendingRequests" %
                             (args.logFolder,
                              args.expPrefix), 'w')
    waitMonFD = open("../%s/%s_WaitMon" % (args.logFolder,
                                           args.expPrefix), 'w')
    actMonFD = open("../%s/%s_ActMon" % (args.logFolder,
                                         args.expPrefix), 'w')
    latencyFD = open("../%s/%s_Latency" % (args.logFolder,
                                           args.expPrefix), 'w')
    latencyTrackerFD = open("../%s/%s_LatencyTracker" %
                            (args.logFolder, args.expPrefix), 'w')
    rateFD = open("../%s/%s_Rate" % (args.logFolder,
                                     args.expPrefix), 'w')
    tokenFD = open("../%s/%s_Tokens" % (args.logFolder,
                                        args.expPrefix), 'w')
    receiveRateFD = open("../%s/%s_ReceiveRate" % (args.logFolder,
                                                   args.expPrefix), 'w')
    edScoreFD = open("../%s/%s_EdScore" % (args.logFolder,
                                           args.expPrefix), 'w')
    serverRRFD = open("../%s/%s_serverRR" % (args.logFolder,
                                             args.expPrefix), 'w')

    for clientNode in clients:
        printMonitorTimeSeriesToFile(pendingRequestsFD,
                                     clientNode.id,
                                     clientNode.pendingRequestsMonitor)
        printMonitorTimeSeriesToFile(latencyTrackerFD,
                                     clientNode.id,
                                     clientNode.latencyTrackerMonitor)
        printMonitorTimeSeriesToFile(rateFD,
                                     clientNode.id,
                                     clientNode.rateMonitor)
        printMonitorTimeSeriesToFile(tokenFD,
                                     clientNode.id,
                                     clientNode.tokenMonitor)
        printMonitorTimeSeriesToFile(receiveRateFD,
                                     clientNode.id,
                                     clientNode.receiveRateMonitor)
        printMonitorTimeSeriesToFile(edScoreFD,
                                     clientNode.id,
                                     clientNode.edScoreMonitor)
    for serv in servers:
        printMonitorTimeSeriesToFile(waitMonFD,
                                     serv.id,
                                     serv.waitMon)
        printMonitorTimeSeriesToFile(actMonFD,
                                     serv.id,
                                     serv.actMon)
        printMonitorTimeSeriesToFile(serverRRFD,
                                     serv.id,
                                     serv.serverRRMonitor)
        print("------- Server:%s %s ------" % (serv.id, "WaitMon"))
        print("Mean:", serv.waitMon.mean())

        print("------- Server:%s %s ------" % (serv.id, "ActMon"))
        print("Mean:", serv.actMon.mean())

    print("------- Latency ------")
    print("Mean Latency:", np.mean([entry[0] for entry in latencyMonitor]))

    printMonitorTimeSeriesToFile(latencyFD, "0",
                                 latencyMonitor)
    assert args.numRequests == len(latencyMonitor)


# class WorkloadUpdater(Simulation.Process):
#     def __init__(self, workload, value, clients, servers):
#         self.workload = workload
#         self.value = value
#         self.clients = clients
#         self.servers = servers
#         Simulation.Process.__init__(self, name='WorkloadUpdater')

#     def run(self):
#         # while(1):
#             yield Simulation.hold, self,
#             # self.workload.model_param = random.uniform(self.value,
#                                                        # self.value * 40)
#             # old = self.workload.model_param
#             # self.workload.model_param = self.value
#             # self.workload.clientList = self.clients
#             # self.workload.total = \
#             #     sum(client.demandWeight for client in self.clients)
#             # yield Simulation.hold, self, 1000
#             # self.workload.model_param = old
#             self.servers[0].serviceTime = 1000

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Absinthe sim.')
    parser.add_argument('--numClients', nargs='?',
                        type=int, default=1, help='Number of clients')
    parser.add_argument('--numServers', nargs='?',
                        type=int, default=5, help='Number of servers')
    parser.add_argument('--numWorkload', nargs='?',
                        type=int, default=1, help='Number of workload generators. Seems to distribute the '
                                                  'tasks out to different clients.')
    parser.add_argument('--serverConcurrency', nargs='?',
                        type=int, default=1, help='Amount of resources per server.')
    parser.add_argument('--serviceTime', nargs='?',
                        type=float, default=1, help='Mean? service time per server')
    parser.add_argument('--workloadModel', nargs='?',
                        type=str, default="poisson", help='Arrival model of requests from client')
    parser.add_argument('--utilization', nargs='?',
                        type=float, default=0.90, help='Arrival rate of requests')
    parser.add_argument('--serviceTimeModel', nargs='?',
                        type=str, default="random.expovariate", help='Distribution of service time on server')
    parser.add_argument('--replicationFactor', nargs='?',
                        type=int, default=1, help='Replication factor (# of choices)')
    parser.add_argument('--selectionStrategy', nargs='?',
                        type=str, default="expDelay", help='Policy to use for replica selection')
    parser.add_argument('--shadowReadRatio', nargs='?',
                        type=float, default=0.10, help='Controls the probability of sending a shadow read '
                                                       '(idk exactly what this is, it seems to be a function '
                                                       'that sends requests out to non-chosen servers to force '
                                                       'an update.')
    parser.add_argument('--rateInterval', nargs='?',
                        type=int, default=10, help='Unclear what this one does')
    parser.add_argument('--cubicC', nargs='?',
                        type=float, default=0.000004, help='Controls sending rate of client (Called gamma in paper)')
    parser.add_argument('--cubicSmax', nargs='?',
                        type=float, default=10, help='Controls sending rate of client. ')
    parser.add_argument('--cubicBeta', nargs='?',
                        type=float, default=0.2, help='Controls sending rate of client')
    parser.add_argument('--hysterisisFactor', nargs='?',
                        type=float, default=2, help='Hysteresis period before another rate change')
    parser.add_argument('--backpressure', action='store_true',
                        default=False, help='Adds backpressure mode which waits once rate limits are reached')
    parser.add_argument('--accessPattern', nargs='?',
                        type=str, default="uniform", help='Key access pattern of requests, e.g., zipfian will cause '
                                                          'requests to desire a subset of replica sets')
    parser.add_argument('--nwLatencyBase', nargs='?',
                        type=float, default=0.960, help='Seems to be the time it takes to deliver requests?')
    parser.add_argument('--nwLatencyMu', nargs='?',
                        type=float, default=0.040, help='Seems to be the time it takes to deliver requests?')
    parser.add_argument('--nwLatencySigma', nargs='?',
                        type=float, default=0.0, help='Seems to be the time it takes to deliver requests?')
    parser.add_argument('--expPrefix', nargs='?',
                        type=str, default="0")
    parser.add_argument('--seed', nargs='?',
                        type=int, default=25072014)
    parser.add_argument('--simulationDuration', nargs='?',
                        type=int, default=500, help='Time that experiment takes, '
                                                    'note that if this is too low and numRequests is too high, '
                                                    'it will error')
    parser.add_argument('--numRequests', nargs='?',
                        type=int, default=100, help='Number of requests')
    parser.add_argument('--logFolder', nargs='?',
                        type=str, default="logs")
    parser.add_argument('--expScenario', nargs='?',
                        type=str, default="base", help='Defines some scenarios for experiments such as \n'
                                                       '[base] - default setting\n'
                                                       '[multipleServiceTimeServers] - increasing mean service time '
                                                       'based on server index\n'
                                                       '[heterogenousStaticServiceTimeScenario] - '
                                                       'fraction of servers are slower\n'
                                                       '[timeVaryingServiceTimeServers] - servers change service times')
    parser.add_argument('--demandSkew', nargs='?',
                        type=float, default=0, help='Skews clients such that some clients send many'
                                                    ' more requests than others')
    parser.add_argument('--highDemandFraction', nargs='?',
                        type=float, default=0, help='Fraction of the high demand clients')
    parser.add_argument('--slowServerFraction', nargs='?',
                        type=float, default=0, help='Fraction of slow servers '
                                                    '(expScenario=heterogenousStaticServiceTimeScenario)')
    parser.add_argument('--slowServerSlowness', nargs='?',
                        type=float, default=0, help='How slow those slowed servers are '
                                                    '(expScenario=heterogenousStaticServiceTimeScenario)')
    parser.add_argument('--intervalParam', nargs='?',
                        type=float, default=0.0, help='Interval between which server service times change '
                                                      '(expScenario=timeVaryingServiceTimeServers)')
    parser.add_argument('--timeVaryingDrift', nargs='?',
                        type=float, default=0.0, help='How much service times change '
                                                      '(expScenario=timeVaryingServiceTimeServers)')
    args = parser.parse_args()

    runExperiment(args)
