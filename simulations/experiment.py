import json
import os

import torch

import server
import client
from simulator import Simulation
import workload
import random
import constants
import numpy as np
import sys
import muUpdater
from simulations.monitor import Monitor
from simulation_args import SimulationArgs
from pathlib import Path
from model_trainer import Trainer
from simulations.plotting import ExperimentPlot
import matplotlib.pyplot as plt


def print_monitor_time_series_to_file(file_desc, prefix, monitor):
    for entry in monitor:
        file_desc.write("%s %s %s\n" % (prefix, entry[0], entry[1]))


def log_arguments(out_folder: Path):
    args_dict = vars(args.args)

    json_file_path = out_folder / 'arguments.json'
    with open(json_file_path, 'w') as json_file:
        json.dump(args_dict, json_file, indent=4)


def rl_experiment_wrapper(simulation_args: SimulationArgs):
    random.seed(1)
    np.random.seed(1)
    torch.manual_seed(1)
    # Start the models and etc.
    # Adapted from https://pytorch.org/tutorials/intermediate/reinforcement_q_learning.html
    trainer = Trainer(simulation_args.args.num_servers, long_requests_ratio=args.args.long_tasks_fraction,
                      gamma=args.args.gamma,
                      eps_decay=args.args.eps_decay, eps_start=args.args.eps_start, eps_end=args.args.eps_end,
                      tau=args.args.tau, tau_decay=args.args.tau_decay,
                      lr=args.args.lr, batch_size=args.args.batch_size)
    NUM_EPSIODES = 400
    train_plotter = ExperimentPlot()
    test_plotter = ExperimentPlot()
    to_print = False

    experiment_num = 0
    plot_path = Path('..', simulation_args.args.plot_folder, str(experiment_num))

    while os.path.isdir(plot_path):
        experiment_num += 1
        plot_path = Path('..', simulation_args.args.plot_folder, str(experiment_num))

    simulation_args.args.exp_prefix = str(experiment_num)
    os.makedirs(plot_path, exist_ok=True)

    simulation_args.set_print(to_print)

    log_arguments(plot_path)

    policies_to_run = [
        'expDelay',
        # 'response_time',
        # 'weighted_response_time',
        'random',
        'dqn'
    ]

    print('Starting experiments')
    for policy in policies_to_run:
        simulation_args.set_policy(policy)
        for i_episode in range(NUM_EPSIODES):
            print(i_episode)
            simulation_args.set_seed(i_episode)
            latencies = run_experiment(simulation_args.args, trainer)
            train_plotter.add_data(latencies, simulation_args.args.selection_strategy, i_episode)
            if policy == 'dqn':
                # Update LR
                trainer.scheduler.step()

                # trainer.print_weights()

                trainer.eval_mode = True
                test_latencies = run_experiment(simulation_args.args, trainer, eval_mode=trainer.eval_mode)
                test_plotter.add_data(test_latencies, simulation_args.args.selection_strategy, i_episode)
                trainer.eval_mode = False
            else:
                # Note that this uses the same seed at the moment
                test_plotter.add_data(latencies, simulation_args.args.selection_strategy, i_episode)

    print('Finished')

    trainer.plot_grads_and_losses(plot_path=plot_path)

    fig, ax = train_plotter.plot()
    plt.savefig(plot_path / 'output_train.jpg')
    fig, ax = train_plotter.plot_quantile(0.90)
    plt.savefig(plot_path / 'output_train_p_90.jpg')
    fig, ax = train_plotter.plot_quantile(0.95)
    plt.savefig(plot_path / 'output_train_p_95.jpg')
    fig, ax = train_plotter.plot_quantile(0.99)
    plt.savefig(plot_path / 'output_train_p_99.jpg')

    plt_episode = NUM_EPSIODES - 1
    fig, ax = train_plotter.plot_episode(epoch=plt_episode)
    plt.savefig(plot_path / f'output_train_{plt_episode}_epoch.jpg')

    fig, ax = test_plotter.plot()
    plt.savefig(plot_path / 'output.jpg')
    fig, ax = test_plotter.plot_quantile(0.90)
    plt.savefig(plot_path / 'output_p_90.jpg')
    fig, ax = test_plotter.plot_quantile(0.95)
    plt.savefig(plot_path / 'output_p_95.jpg')
    fig, ax = test_plotter.plot_quantile(0.99)
    plt.savefig(plot_path / 'output_p_99.jpg')


def run_experiment(args, trainer: Trainer = None, eval_mode=False):
    # Set the random seed
    simulation = Simulation()
    simulation.set_seed(args.seed)

    servers = []
    clients = []
    workload_gens = []

    constants.NW_LATENCY_BASE = args.nw_latency_base
    constants.NW_LATENCY_MU = args.nw_latency_mu
    constants.NW_LATENCY_SIGMA = args.nw_latency_sigma
    constants.NUMBER_OF_CLIENTS = args.num_clients

    assert args.exp_scenario != ""

    service_rate_per_server = []
    if args.exp_scenario == "base":
        # Start the servers
        for i in range(args.num_servers):
            serv = server.Server(i,
                                 resource_capacity=args.server_concurrency,
                                 service_time=(args.service_time),
                                 service_time_model=args.service_time_model,
                                 simulation=simulation,
                                 long_task_added_service_time=args.long_task_added_service_time)
            servers.append(serv)
    elif args.exp_scenario == "multipleServiceTimeServers":
        # Start the servers
        for i in range(args.num_servers):
            serv = server.Server(i,
                                 resource_capacity=args.server_concurrency,
                                 service_time=((i + 1) * args.service_time),
                                 service_time_model=args.service_time_model,
                                 simulation=simulation,
                                 long_task_added_service_time=args.long_task_added_service_time)
            servers.append(serv)
    elif args.exp_scenario == "heterogenous_static_service_time_scenario":
        base_service_time = args.service_time

        assert 0 <= args.slow_server_fraction < 1.0
        assert 0 <= args.slow_server_slowness < 1.0
        assert not (args.slow_server_slowness == 0 and args.slow_server_fraction != 0)
        assert not (args.slow_server_slowness != 0 and args.slow_server_fraction == 0)
        assert args.long_tasks_fraction == 0

        if args.slow_server_fraction > 0.0:
            slow_server_rate = (args.server_concurrency *
                                1 / float(base_service_time)) * \
                               args.slow_server_slowness
            num_slow_servers = int(args.slow_server_fraction * args.num_servers)
            slow_server_rates = [slow_server_rate] * num_slow_servers

            num_fast_servers = args.num_servers - num_slow_servers
            total_rate = (args.server_concurrency *
                          1 / float(args.service_time) * args.num_servers)
            fast_server_rate = (total_rate - sum(slow_server_rates)) \
                               / float(num_fast_servers)
            fast_server_rates = [fast_server_rate] * num_fast_servers
            service_rate_per_server = slow_server_rates + fast_server_rates
        else:
            service_rate_per_server = [args.server_concurrency *
                                       1 / float(args.service_time)] * args.num_servers

        simulation.random.shuffle(service_rate_per_server)
        # print(sum(serviceRatePerServer), (1/float(baseServiceTime)) * args.num_servers)
        assert sum(service_rate_per_server) > 0.99 * \
               (1 / float(base_service_time)) * args.num_servers
        assert sum(service_rate_per_server) <= \
               (1 / float(base_service_time)) * args.num_servers

        # Start the servers
        for i in range(args.num_servers):
            st = 1 / float(service_rate_per_server[i])
            serv = server.Server(i,
                                 resource_capacity=args.server_concurrency,
                                 service_time=st,
                                 service_time_model=args.service_time_model,
                                 simulation=simulation,
                                 long_task_added_service_time=args.long_task_added_service_time)
            servers.append(serv)
    elif args.exp_scenario == "time_varying_service_time_servers":
        assert args.interval_param != 0.0
        assert args.time_varying_drift != 0.0

        # Start the servers
        for i in range(args.num_servers):
            serv = server.Server(i,
                                 resource_capacity=args.server_concurrency,
                                 service_time=args.service_time,
                                 service_time_model=args.service_time_model,
                                 simulation=simulation,
                                 long_task_added_service_time=args.long_task_added_service_time)
            mup = muUpdater.MuUpdater(serv,
                                      args.interval_param,
                                      args.service_time,
                                      args.time_varying_drift,
                                      simulation)
            simulation.process(mup.run())
            servers.append(serv)
    elif args.exp_scenario == "heterogenous_static_nw_delay":
        # print('heterogenous_static_nw_delay scenario')
        assert 0 < args.slow_nw_server_fraction < 1.0
        assert not (args.slow_nw_server_slowness == 0 and args.slow_nw_server_fraction != 0)
        assert not (args.slow_nw_server_slowness != 0 and args.slow_nw_server_fraction == 0)

        slow_nw_latency_base = constants.NW_LATENCY_BASE * args.slow_nw_server_slowness
        num_slow_nw_servers = int(args.slow_nw_server_fraction * args.num_servers)
        slow_nw_server_latency_bases = [slow_nw_latency_base] * num_slow_nw_servers

        num_fast_nw_servers = args.num_servers - num_slow_nw_servers
        fast_server_nw_rates = [constants.NW_LATENCY_BASE] * num_fast_nw_servers
        nw_latency_bases = slow_nw_server_latency_bases + fast_server_nw_rates

        # Start the servers
        for i in range(args.num_servers):
            serv = server.Server(i,
                                 resource_capacity=args.server_concurrency,
                                 service_time=args.service_time,
                                 service_time_model=args.service_time_model,
                                 simulation=simulation,
                                 nw_latency_base=nw_latency_bases[i],
                                 long_task_added_service_time=args.long_task_added_service_time)
            servers.append(serv)
    else:
        print("Unknown experiment scenario")
        sys.exit(-1)

    base_demand_weight = 1.0
    client_weights = []
    assert 0 <= args.high_demand_fraction < 1.0
    assert 0 <= args.demand_skew < 1.0
    assert not (args.demand_skew == 0 and args.high_demand_fraction != 0)
    assert not (args.demand_skew != 0 and args.high_demand_fraction == 0)

    if args.high_demand_fraction > 0.0 and args.demand_skew >= 0:
        heavy_client_weight = base_demand_weight * \
                              args.demand_skew / args.high_demand_fraction
        num_heavy_clients = int(args.high_demand_fraction * args.num_clients)
        heavy_client_weights = [heavy_client_weight] * num_heavy_clients

        light_client_weight = base_demand_weight * \
                              (1 - args.demand_skew) / (1 - args.high_demand_fraction)
        num_light_clients = args.num_clients - num_heavy_clients
        light_client_weights = [light_client_weight] * num_light_clients
        client_weights = heavy_client_weights + light_client_weights
    else:
        client_weights = [base_demand_weight] * args.num_clients

    assert sum(client_weights) > 0.99 * args.num_clients
    assert sum(client_weights) <= args.num_clients

    # Start the clients
    for i in range(args.num_clients):
        c = client.Client(id_="Client%s" % (i),
                          server_list=servers,
                          replica_selection_strategy=args.selection_strategy,
                          access_pattern=args.access_pattern,
                          replication_factor=args.replication_factor,
                          backpressure=args.backpressure,
                          shadow_read_ratio=args.shadow_read_ratio,
                          rate_interval=args.rate_interval,
                          cubic_c=args.cubic_c,
                          cubic_smax=args.cubic_smax,
                          cubic_beta=args.cubic_beta,
                          hysterisis_factor=args.hysterisis_factor,
                          demand_weight=client_weights[i],
                          rate_intervals=args.rate_intervals,
                          trainer=trainer,
                          simulation=simulation)
        clients.append(c)

    # Start workload generators (analogous to YCSB)
    latency_monitor = Monitor(name="Latency", simulation=simulation)

    # This is where we set the inter-arrival times based on
    # the required utilization level and the service time
    # of the overall server pool.
    if len(service_rate_per_server) > 0:
        print(service_rate_per_server)
        arrival_rate = (args.utilization * sum(service_rate_per_server))
        inter_arrival_time = 1 / float(arrival_rate)
    else:
        average_service_time = args.service_time * (1 - args.long_tasks_fraction) + args.long_tasks_fraction * (
                args.service_time + args.long_task_added_service_time)
        arrival_rate = args.num_servers * \
                       (args.utilization * args.server_concurrency *
                        1 / float(average_service_time))
        inter_arrival_time = 1 / float(arrival_rate)

    for i in range(args.num_workload):
        w = workload.Workload(i, latency_monitor,
                              clients,
                              args.workload_model,
                              inter_arrival_time * args.num_workload,
                              args.num_requests / args.num_workload,
                              simulation,
                              long_tasks_fraction=args.long_tasks_fraction)
        simulation.process(w.run())
        workload_gens.append(w)

    # Begin simulation
    simulation.run(until=args.simulation_duration)

    #
    # print(a bunch of timeseries)
    #

    exp_prefix = f'{args.exp_prefix}_test' if eval_mode else args.exp_prefix
    exp_path = Path('..', args.log_folder, exp_prefix)

    if not exp_path.exists():
        exp_path.mkdir(parents=True, exist_ok=True)

    # pending_requests_fd = open("../%s/%s_PendingRequests" %
    #                            (args.log_folder,
    #                             exp_prefix), 'w')
    # wait_mon_fd = open("../%s/%s_WaitMon" % (args.log_folder,
    #                                          exp_prefix), 'w')
    # act_mon_fd = open("../%s/%s_ActMon" % (args.log_folder,
    #                                        exp_prefix), 'w')
    # latency_fd = open("../%s/%s_Latency" % (args.log_folder,
    #                                         exp_prefix), 'w')
    # latency_tracker_fd = open("../%s/%s_LatencyTracker" %
    #                           (args.log_folder, exp_prefix), 'w')
    # rate_fd = open("../%s/%s_Rate" % (args.log_folder,
    #                                   exp_prefix), 'w')
    # token_fd = open("../%s/%s_Tokens" % (args.log_folder,
    #                                      exp_prefix), 'w')
    # receive_rate_fd = open("../%s/%s_ReceiveRate" % (args.log_folder,
    #                                                  exp_prefix), 'w')
    # ed_score_fd = open("../%s/%s_EdScore" % (args.log_folder,
    #                                          exp_prefix), 'w')
    # server_rrfd = open("../%s/%s_serverRR" % (args.log_folder,
    #                                           exp_prefix), 'w')

    # for clientNode in clients:
    #     print_monitor_time_series_to_file(pending_requests_fd,
    #                                       clientNode.id,
    #                                       clientNode.pendingRequestsMonitor)
    #     print_monitor_time_series_to_file(latency_tracker_fd,
    #                                       clientNode.id,
    #                                       clientNode.latencyTrackerMonitor)
    #     print_monitor_time_series_to_file(rate_fd,
    #                                       clientNode.id,
    #                                       clientNode.rateMonitor)
    #     print_monitor_time_series_to_file(token_fd,
    #                                       clientNode.id,
    #                                       clientNode.tokenMonitor)
    #     print_monitor_time_series_to_file(receive_rate_fd,
    #                                       clientNode.id,
    #                                       clientNode.receiveRateMonitor)
    #     print_monitor_time_series_to_file(ed_score_fd,
    #                                       clientNode.id,
    #                                       clientNode.edScoreMonitor)

    if args.print:
        for serv in servers:
            #     print_monitor_time_series_to_file(wait_mon_fd,
            #                                       serv.id,
            #                                       serv.wait_monitor)
            #     print_monitor_time_series_to_file(act_mon_fd,
            #                                       serv.id,
            #                                       serv.act_monitor)
            #     print_monitor_time_series_to_file(server_rrfd,
            #                                       serv.id,
            #                                       serv.server_RR_monitor)
            print("------- Server:%s %s ------" % (serv.id, "WaitMon"))
            print("Mean:", serv.wait_monitor.mean())

            print("------- Server:%s %s ------" % (serv.id, "ActMon"))
            print("Mean:", serv.act_monitor.mean())

        print("------- Latency ------")
        print("Mean Latency:", latency_monitor.mean())
        for p in [50, 95, 99]:
            print(f"p{p} Latency: {latency_monitor.percentile(p)}")

        # print_monitor_time_series_to_file(latency_fd, "0",
        #                                   latency_monitor)
        assert args.num_requests == len(latency_monitor)

    return latency_monitor


if __name__ == '__main__':
    args = SimulationArgs()
    # args = TimeVaryingArgs(0.1,5)
    # args = SlowServerArgs(0.5,0.5)
    args.set_policy('expDelay')
    rl_experiment_wrapper(args)
