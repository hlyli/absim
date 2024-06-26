import json
import os
from typing import Any, Dict, List

import torch

import random
import numpy as np
from simulation_args import BaseArgs, HeterogeneousRequestsArgs, SimulationArgs, StaticSlowServerArgs, TimeVaryingServerArgs, log_arguments
from pathlib import Path
from simulations.training.model_trainer import Trainer
from simulations.feature_data_collector import FeatureDataCollector
from simulations.monitor import Monitor
from simulations.plotting import ExperimentPlot
from experiment_runner import ExperimentRunner
from simulations.state import StateParser
from simulations.training.offline_model_trainer import OfflineTrainer
from simulations.training.training_data_collector import TrainingDataCollector
from simulations.workload.workload import BaseWorkload
from simulations.workload.workload_builder import WorkloadBuilder
import constants as const

DQN_EXPLR_MAPPING = {item for i in range(101)
                     for item in [(f'DQN_EXPLR_{i}', i / 100.0), (f'DQN_EXPLR_{i}_TRAIN', i / 100), (f'OFFLINE_DQN_EXPLR_{i}', i / 100.0), (f'OFFLINE_DQN_EXPLR_{i}_TRAIN', i / 100)]}
DQN_DUPL_MAPPING = {item for i in range(101)
                    for item in [(f'DQN_DUPL_{i}', i / 100.0), (f'DQN_DUPL_{i}_TRAIN', i / 100), (f'OFFLINE_DQN_DUPL_{i}', i / 100.0), (f'OFFLINE_DQN_DUPL_{i}_TRAIN', i / 100)]}

BASE_TEST_SEED = 111111
BASE_TEST_EXPLR_SEED = 222222


def print_monitor_time_series_to_file(file_desc, prefix, monitor) -> None:
    for entry in monitor:
        file_desc.write("%s %s %s\n" % (prefix, entry[0], entry[1]))


def create_experiment_folders(simulation_args: SimulationArgs, state_parser: StateParser) -> Path:
    # Start the models and etc.
    # Adapted from https://pytorch.org/tutorials/intermediate/reinforcement_q_learning.html
    experiment_num = 0
    if simulation_args.args.exp_name != "":
        out_path = Path('..', simulation_args.args.output_folder, simulation_args.args.exp_name, str(experiment_num))
        while os.path.isdir(out_path):
            experiment_num += 1
            out_path = Path('..', simulation_args.args.output_folder,
                            simulation_args.args.exp_name, str(experiment_num))
    else:
        out_path = Path('..', simulation_args.args.output_folder, str(experiment_num))
        while os.path.isdir(out_path):
            experiment_num += 1
            out_path = Path('..', simulation_args.args.output_folder, str(experiment_num))

    os.makedirs(out_path, exist_ok=True)

    return out_path


def rl_experiment_wrapper(simulation_args: SimulationArgs, train_workloads: List[BaseWorkload], test_workloads: List[BaseWorkload]) -> float:
    state_parser = StateParser(num_servers=simulation_args.args.num_servers,
                               num_request_rates=len(simulation_args.args.rate_intervals),
                               poly_feat_degree=simulation_args.args.poly_feat_degree)

    random.seed(simulation_args.args.seed)
    np.random.seed(simulation_args.args.seed)
    torch.manual_seed(simulation_args.args.seed)

    torch.backends.cudnn.benchmark = False
    torch.use_deterministic_algorithms(True)

    # Start the models and etc.
    # Adapted from https://pytorch.org/tutorials/intermediate/reinforcement_q_learning.html
    trainer = Trainer(state_parser=state_parser, model_structure=simulation_args.args.model_structure, n_actions=simulation_args.args.num_servers, replay_always_use_newest=simulation_args.args.replay_always_use_newest, replay_memory_size=simulation_args.args.replay_memory_size,
                      summary_stats_max_size=simulation_args.args.summary_stats_max_size,
                      gamma=simulation_args.args.gamma,
                      eps_decay=simulation_args.args.eps_decay, eps_start=simulation_args.args.eps_start, eps_end=simulation_args.args.eps_end,
                      tau=simulation_args.args.tau, tau_decay=simulation_args.args.tau_decay,
                      lr=simulation_args.args.lr, batch_size=simulation_args.args.batch_size, lr_scheduler_gamma=simulation_args.args.lr_scheduler_gamma, lr_scheduler_step_size=simulation_args.args.lr_scheduler_step_size)

    offline_trainer = OfflineTrainer(state_parser=state_parser,
                                     model_structure=simulation_args.args.model_structure,
                                     n_actions=simulation_args.args.num_servers,
                                     replay_always_use_newest=simulation_args.args.replay_always_use_newest,
                                     replay_memory_size=simulation_args.args.replay_memory_size,
                                     gamma=simulation_args.args.gamma,
                                     eps_decay=simulation_args.args.eps_decay,
                                     eps_start=simulation_args.args.eps_start,
                                     eps_end=simulation_args.args.eps_end,
                                     tau=simulation_args.args.tau,
                                     tau_decay=simulation_args.args.tau_decay,
                                     lr=simulation_args.args.lr,
                                     batch_size=simulation_args.args.batch_size, )

    out_path = create_experiment_folders(simulation_args=simulation_args, state_parser=state_parser)

    training_data_folder = out_path / 'collected_training_data'

    training_data_collector = TrainingDataCollector(
        state_parser=state_parser, n_actions=simulation_args.args.num_servers, summary_stats_max_size=simulation_args.args.summary_stats_max_size, offline_trainer=offline_trainer, offline_train_batch_size=simulation_args.args.offline_train_batch_size, data_folder=training_data_folder)

    assert simulation_args.args.offline_train_data == '' or simulation_args.args.offline_model == ''

    if len(const.TRAIN_POLICIES_TO_RUN) > 0:
        run_rl_training(simulation_args=simulation_args, workloads=train_workloads, offline_trainer=offline_trainer,
                        trainer=trainer, state_parser=state_parser, out_folder=out_path, training_data_collector=training_data_collector)

    if simulation_args.args.model_folder == '':
        model_folder = out_path / 'train' / simulation_args.args.data_folder
    else:
        model_folder = Path(simulation_args.args.model_folder)

    offline_train_data_folder = Path(simulation_args.args.offline_train_data)
    # Use data that was just collected to train model
    if simulation_args.args.offline_model == '' and simulation_args.args.offline_train_data == '':
        offline_train_data_folder = training_data_folder

    if offline_train_data_folder != Path(''):
        offline_train_out_folder = out_path / 'offline_train' / simulation_args.args.data_folder
        offline_plot_folder = out_path / 'offline_train' / simulation_args.args.plot_folder

        os.makedirs(offline_train_out_folder, exist_ok=True)
        os.makedirs(offline_plot_folder, exist_ok=True)

        # Offline training data given, load data and train model on it
        transitions, norm_stats = training_data_collector.read_training_data_from_csv(
            train_data_folder=offline_train_data_folder)
        offline_trainer.run_offline_training_epoch(transitions=transitions, norm_stats=norm_stats)
        offline_trainer.save_models_and_stats(offline_train_out_folder)
        offline_trainer.plot_grads_and_losses(plot_path=offline_plot_folder, file_prefix='offline_train')
        offline_model_folder = offline_train_out_folder
    else:
        offline_model_folder = Path(simulation_args.args.offline_model)

    trainer.set_model_folder(model_folder=model_folder)
    offline_trainer.set_model_folder(model_folder=offline_model_folder)

    run_rl_tests(simulation_args=simulation_args, workloads=test_workloads, out_folder=out_path, trainer=trainer,
                 offline_trainer=offline_trainer, state_parser=state_parser, training_data_collector=training_data_collector)

    return 0


def run_rl_training(simulation_args: SimulationArgs, workloads: List[BaseWorkload], trainer: Trainer, offline_trainer: OfflineTrainer, state_parser: StateParser, training_data_collector: TrainingDataCollector, out_folder: Path):

    if len(workloads) == 0:
        return
    NUM_EPSIODES = simulation_args.args.epochs
    LAST_EPOCH = NUM_EPSIODES - 1

    # Init directories
    experiment_folder = out_folder / 'train'
    plot_path = experiment_folder / simulation_args.args.plot_folder
    data_folder = experiment_folder / simulation_args.args.data_folder
    os.makedirs(plot_path, exist_ok=True)
    os.makedirs(data_folder, exist_ok=True)
    os.makedirs(plot_path / 'pdfs', exist_ok=True)

    # TODO: Fix / workaround
    utilization = workloads[0].utilization
    long_tasks_fraction = workloads[0].long_tasks_fraction

    experiment_runner = ExperimentRunner(state_parser=state_parser, trainer=trainer, offline_trainer=offline_trainer)

    train_plotter = ExperimentPlot(plot_folder=plot_path, data_folder=data_folder,
                                   utilization=utilization, long_tasks_fraction=long_tasks_fraction)

    # Log arguments and workload config
    log_arguments(experiment_folder, simulation_args)
    for i, workload in enumerate(workloads):
        workload.to_json_file(out_folder=experiment_folder, prefix=f'{i}_')

    duplication_rate = 0.0

    print('Starting experiments')
    for policy in const.TRAIN_POLICIES_TO_RUN:
        simulation_args.set_policy(policy)
        for i_episode in range(NUM_EPSIODES):
            print(i_episode)
            random.seed(i_episode)
            np.random.seed(i_episode)
            torch.manual_seed(i_episode)
            simulation_args.set_seed(i_episode)
            # randomly select one of the workload configs
            workload = random.choice(workloads)
            # TODO: Log workload configs used somewhere

            data_point_monitor = experiment_runner.run_experiment(
                simulation_args.args, service_time_model=simulation_args.args.service_time_model, workload=workload, duplication_rate=duplication_rate, training_data_collector=training_data_collector)
            train_plotter.add_data(data_point_monitor, policy, i_episode)

            if simulation_args.args.collect_train_data:
                training_data_collector.end_train_episode()

            if policy == 'DQN':
                # Print number of DQN decisions that matched ARS
                experiment_runner.print_dqn_decision_equal_to_ars_ratio()

                # Update LR
                trainer.scheduler.step()

                trainer.reset_episode_counters()
                # trainer.print_weights()

    print('Finished')
    # train_data_analyzer.run_latency_lin_reg(epoch=LAST_EPOCH)
    trainer.save_models_and_stats(model_folder=data_folder)

    train_plotter.export_data()

    if simulation_args.args.collect_train_data:
        training_data_collector.save_training_data()
        training_data_collector.save_training_data_collector_stats()

    trainer.plot_grads_and_losses(plot_path=plot_path, file_prefix='train')

    plot_collected_data(plotter=train_plotter, epoch_to_plot=LAST_EPOCH, policies_to_plot=const.TRAIN_POLICIES_TO_RUN)


def run_rl_tests(simulation_args: SimulationArgs, workloads: List[BaseWorkload], out_folder: Path, trainer: Trainer, offline_trainer: OfflineTrainer, state_parser: StateParser, training_data_collector: TrainingDataCollector) -> None:
    const.NUM_TEST_EPSIODES = simulation_args.args.test_epochs

    for test_workload in workloads:
        # Start the models and etc.
        # Adapted from https://pytorch.org/tutorials/intermediate/reinforcement_q_learning.html

        LAST_EPOCH = const.NUM_TEST_EPSIODES - 1

        EXPERIMENT = test_workload.to_file_name()

        experiment_folder = out_folder / EXPERIMENT
        plot_path = experiment_folder / simulation_args.args.plot_folder
        data_folder = experiment_folder / simulation_args.args.data_folder
        # model_folder = out_folder / 'train' / simulation_args.args.data_folder

        os.makedirs(plot_path, exist_ok=True)
        os.makedirs(data_folder, exist_ok=True)
        os.makedirs(plot_path / 'pdfs', exist_ok=True)

        utilization = test_workload.utilization

        test_plotter = ExperimentPlot(plot_folder=plot_path, data_folder=data_folder,
                                      utilization=utilization, long_tasks_fraction=test_workload.long_tasks_fraction)

        experiment_runner = ExperimentRunner(
            state_parser=state_parser, trainer=trainer, offline_trainer=offline_trainer)

        log_arguments(experiment_folder, simulation_args)
        test_workload.to_json_file(out_folder=experiment_folder)

        for policy in const.EVAL_POLICIES_TO_RUN:
            simulation_args.set_policy(policy)
            print(f'Starting Test Sequence for {policy}')

            if policy.startswith('OFFLINE_'):
                run_rl_offline_test(simulation_args=simulation_args, workload=test_workload, policy=policy,
                                    plot_folder=plot_path, data_folder=data_folder, offline_trainer=offline_trainer, experiment_runner=experiment_runner, training_data_collector=training_data_collector, test_plotter=test_plotter)
            elif policy.startswith('DQN'):
                run_rl_dqn_test(simulation_args=simulation_args, workload=test_workload, policy=policy,
                                plot_folder=plot_path, data_folder=data_folder, trainer=trainer, experiment_runner=experiment_runner, training_data_collector=training_data_collector, test_plotter=test_plotter)
            else:
                duplication_rate = 0
                for i_episode in range(const.NUM_TEST_EPSIODES):
                    seed = BASE_TEST_SEED + i_episode

                    random.seed(seed)
                    np.random.seed(seed)
                    torch.manual_seed(seed)
                    simulation_args.set_seed(seed)

                    test_data_point_monitor = experiment_runner.run_experiment(
                        simulation_args.args, service_time_model=simulation_args.args.test_service_time_model, workload=test_workload, duplication_rate=duplication_rate, training_data_collector=training_data_collector)
                    print(f'{i_episode}, {policy}')
                    test_plotter.add_data(test_data_point_monitor, policy=policy, epoch_num=i_episode)

        # Export data
        test_plotter.export_data()

        # TOOD: Remove hacky solution
        plot_collected_data(plotter=test_plotter, epoch_to_plot=LAST_EPOCH,
                            policies_to_plot=test_plotter.df['Policy'].unique())

        if simulation_args.args.collect_train_data:
            training_data_collector.save_training_data()
            training_data_collector.save_training_data_collector_stats()
        print('Finished workload')


def run_rl_offline_test(simulation_args: SimulationArgs, workload: BaseWorkload, plot_folder: Path, data_folder: Path, policy: str, offline_trainer: OfflineTrainer, experiment_runner: ExperimentRunner, training_data_collector: TrainingDataCollector, test_plotter: ExperimentPlot) -> float:
    # Start the models and etc.
    # Adapted from https://pytorch.org/tutorials/intermediate/reinforcement_q_learning.html
    duplication_rate = 0.0

    # Reset hyperparameters
    offline_trainer.EPS_START = 0
    offline_trainer.EPS_END = 0
    offline_trainer.do_active_retraining = True

    if not policy.endswith('_TRAIN'):
        offline_trainer.do_active_retraining = False

    if policy == 'OFFLINE_DQN':
        offline_trainer.EPS_END = 0
        offline_trainer.EPS_START = 0
    if policy.startswith('OFFLINE_DQN_EXPLR_'):
        print(f'simulation_args.args.dqn_explr: {simulation_args.args.dqn_explr}')
        print(f'OFFLINE_DQN_EXPLR_MAPPING: {DQN_EXPLR_MAPPING[policy]}')
        offline_trainer.EPS_END = DQN_EXPLR_MAPPING[policy]
        offline_trainer.EPS_START = DQN_EXPLR_MAPPING[policy]
    elif policy.startswith('OFFLINE_DQN_DUPL_'):
        print(f'DQN_DUPL_MAPPING: {DQN_DUPL_MAPPING[policy]}')
        duplication_rate = DQN_DUPL_MAPPING[policy]
        offline_trainer.EPS_END = 0
        offline_trainer.EPS_START = 0
    else:
        raise Exception(f'Invalid policy for offline RL adapting: {policy}')

    for i_episode in range(const.NUM_TEST_EPSIODES):
        seed = BASE_TEST_SEED + i_episode

        random.seed(seed)
        np.random.seed(seed)
        torch.manual_seed(seed)
        simulation_args.set_seed(seed)

        offline_trainer.load_models()
        # Also reset model steps and stats
        offline_trainer.reset_training_stats()
        print(i_episode)

        test_data_point_monitor = experiment_runner.run_experiment(
            simulation_args.args, service_time_model=simulation_args.args.test_service_time_model, workload=workload, duplication_rate=duplication_rate, training_data_collector=training_data_collector)

        if policy.endswith('_TRAIN'):
            offline_model_folder = data_folder / f'{policy}_{i_episode}'
            os.makedirs(offline_model_folder, exist_ok=True)
            offline_trainer.save_models_and_stats(model_folder=offline_model_folder)
            file_prefix = f'{policy}_{i_episode}'
            offline_trainer.plot_grads_and_losses(plot_path=plot_folder, file_prefix=file_prefix)

        print(f'Adding offline data: {policy}')
        test_plotter.add_data(test_data_point_monitor, policy=policy, epoch_num=i_episode)

        # Print number of DQN decisions that matched ARS
        experiment_runner.print_dqn_decision_equal_to_ars_ratio()
        print(f'Exlore actions this episode: {offline_trainer.explore_actions_episode}')
        print(f'Exploit actions this episode: {offline_trainer.exploit_actions_episode}')
        offline_trainer.reset_episode_counters()


def run_rl_dqn_test(simulation_args: SimulationArgs, workload: BaseWorkload, policy: str, plot_folder: Path,
                    data_folder: Path, trainer: Trainer, experiment_runner: ExperimentRunner,
                    training_data_collector: TrainingDataCollector, test_plotter: ExperimentPlot) -> float:
    trainer.eval_mode = False
    trainer.LR = simulation_args.args.dqn_explr_lr
    duplication_rate = 0.0

    if not policy.endswith('_TRAIN'):
        trainer.eval_mode = True

    if policy == 'DQN':
        trainer.EPS_END = 0
        trainer.EPS_START = 0
    elif policy.startswith('DQN_EXPLR_'):
        print(f'simulation_args.args.dqn_explr: {simulation_args.args.dqn_explr}')
        print(f'DQN_EXPLR_MAPPING: {DQN_EXPLR_MAPPING[policy]}')
        trainer.EPS_END = DQN_EXPLR_MAPPING[policy]
        trainer.EPS_START = DQN_EXPLR_MAPPING[policy]
    elif policy.startswith('DQN_DUPL_'):
        print(f'DQN_DUPL_MAPPING: {DQN_DUPL_MAPPING[policy]}')
        duplication_rate = DQN_DUPL_MAPPING[policy]
        trainer.EPS_END = 0
        trainer.EPS_START = 0
    else:
        raise Exception(f'Invalid policy for adapting: {policy}')

    for i_episode in range(const.NUM_TEST_EPSIODES):
        seed = BASE_TEST_SEED + i_episode

        random.seed(seed)
        np.random.seed(seed)
        torch.manual_seed(seed)
        simulation_args.set_seed(seed)

        trainer.load_models()
        # Also reset model steps and stats
        trainer.reset_model_training_stats()
        print(i_episode)

        test_data_point_monitor = experiment_runner.run_experiment(
            simulation_args.args, service_time_model=simulation_args.args.test_service_time_model, workload=workload, duplication_rate=duplication_rate, training_data_collector=training_data_collector)

        policy_str = policy
        if policy != 'DQN':
            policy_str = f'{policy_str}_TRAIN'

            model_folder = data_folder / f'{policy}_{i_episode}'
            os.makedirs(model_folder, exist_ok=True)
            trainer.save_models_and_stats(model_folder=model_folder)

            file_prefix = f'{policy}_{i_episode}'
            trainer.plot_grads_and_losses(plot_path=plot_folder, file_prefix=file_prefix)

        test_plotter.add_data(test_data_point_monitor, policy=policy_str, epoch_num=i_episode)

        # Print number of DQN decisions that matched ARS
        experiment_runner.print_dqn_decision_equal_to_ars_ratio()
        print(f'Exlore actions this episode: {trainer.explore_actions_episode}')
        print(f'Exploit actions this episode: {trainer.exploit_actions_episode}')
        trainer.reset_episode_counters()

    # Reset hyperparameters
    trainer.EPS_START = simulation_args.args.eps_start
    trainer.EPS_END = simulation_args.args.eps_end
    trainer.eval_mode = False


def plot_collected_data(plotter: ExperimentPlot, epoch_to_plot: int, policies_to_plot: List[str]) -> None:
    plotter.generate_plots()
    plotter.save_stats_to_file()

    plotter.plot_episode(epoch=epoch_to_plot)
    for policy in policies_to_plot:
        plotter.plot_policy_episode(epoch=epoch_to_plot, policy=policy)


# TODO: Make scenarios enum and find better way to select args for them
def main(input_args=None, setting="base") -> None:
    if setting == "base":
        args = BaseArgs(input_args=input_args)
    elif setting == "heterogenous_requests_scenario":
        args = HeterogeneousRequestsArgs(input_args=input_args)
    elif setting == "heterogenous_static_service_time_scenario":
        args = StaticSlowServerArgs(input_args=input_args)
    elif setting == "time_varying_service_time_servers":
        args = TimeVaryingServerArgs(input_args=input_args)
    else:
        raise Exception(f'Unknown setting {setting}')

    SEED = args.args.seed

    config_folder = Path('..', 'configs')
    workload_builder = WorkloadBuilder(config_folder=config_folder)

    args.set_policy('ARS')

    last = 0.0

    # EXPERIMENT_NAME = 'training_data_test'

    # const.EVAL_POLICIES_TO_RUN = []
    # const.TRAIN_POLICIES_TO_RUN = ['ARS_20']

    # args = HeterogeneousRequestsArgs(input_args=input_args)
    # args.args.exp_name = EXPERIMENT_NAME
    # args.args.epochs = 5
    # args.args.model_folder = ''
    # args.args.collect_train_data = True

    # for service_time_model in ['random.expovariate']:  # 'pareto'
    #     for test_service_time_model in ['random.expovariate']:  # 'random.expovariate',
    #         for dqn_explr_lr in [1e-5]:
    #             args.args.seed = SEED
    #             args.args.test_service_time_model = test_service_time_model
    #             args.args.service_time_model = service_time_model
    #             args.args.dqn_explr_lr = dqn_explr_lr
    #             last = rl_experiment_wrapper(args,
    #                                          train_workloads=train_workloads, test_workloads=test_workloads)
    # return

    # EXPERIMENT_NAME = 'replay_mem_size_experiment'

    # train_workloads = workload_builder.create_train_base_workloads(
    #     long_tasks_fractions=[0.3, 0.35, 0.4], utilizations=[0.45], num_requests=8000)

    # test_workloads = workload_builder.create_test_var_long_tasks_workloads(
    #     num_requests=128000)

    # const.EVAL_POLICIES_TO_RUN = [
    #     'round_robin',
    #     'ARS',
    #     'DQN',
    #     'random',
    #     # 'DQN_EXPLR',
    #     # 'DQN_DUPL'
    # ] + ['DQN_DUPL_10', 'DQN_DUPL_25', 'DQN_DUPL_40', 'DQN_DUPL_45'] + ['DQN_EXPLR_0', 'DQN_EXPLR_10', 'DQN_EXPLR_20', 'DQN_EXPLR_25']

    # args = HeterogeneousRequestsArgs(input_args=input_args)
    # args.args.exp_name = EXPERIMENT_NAME
    # args.args.epochs = 100
    # args.args.eps_decay = 180000
    # args.args.lr_scheduler_step_size = 30
    # args.args.model_folder = ''
    # args.args.offline_model = '/home/jonas/projects/absim/outputs/offline_parameter_search/9/train/data'
    # args.args.collect_train_data = False

    # i = 12
    # for buffer_size in [1000, 100]:
    #     for use_newest in [True, False]:
    #         for dqn_explr_lr in [1e-5, 1e-6]:
    #             args.args.seed = SEED
    #             args.args.replay_always_use_newest = use_newest
    #             args.args.replay_memory_size = buffer_size
    #             args.args.dqn_explr_lr = dqn_explr_lr
    #             last = rl_experiment_wrapper(args,
    #                                          train_workloads=train_workloads, test_workloads=test_workloads)
    #             if dqn_explr_lr == 1e-5:
    #                 args.args.model_folder = f'/home/jonas/projects/absim/outputs/replay_mem_size_experiment/{i}/train/data'
    #             else:
    #                 args.args.model_folder = ''
    #             i += 1

    # EXPERIMENT_NAME = 'offline_parameter_search'

    # train_workloads = workload_builder.create_train_base_workloads(
    #     long_tasks_fractions=[0.3, 0.35, 0.4], utilizations=[0.45], num_requests=48000)

    # test_workloads = workload_builder.create_test_base_workloads(utilizations=[0.45], long_tasks_fractions=[
    #                                                              0.3, 0.35, 0.4], num_requests=8000)  # workload_builder.create_test_var_long_tasks_workloads(num_requests=128000)

    # const.EVAL_POLICIES_TO_RUN = ['ARS', 'DQN', 'OFFLINE_DQN', 'random']
    # args = HeterogeneousRequestsArgs(input_args=input_args)
    # args.args.exp_name = EXPERIMENT_NAME
    # args.args.eps_decay = 180000
    # args.args.lr_scheduler_step_size = 30
    # args.args.replay_always_use_newest = False
    # args.args.model_folder = '/home/jonas/projects/absim/outputs/fixed_memory_not_use_latest/0/train/data'
    # args.args.offline_model = ''  # '/home/jonas/projects/absim/outputs/fixed_memory_not_use_latest/0/train/data'

    # # '/home/jonas/projects/absim/outputs/collect_offline_data/0/collected_training_data'

    # for train_policy in ['random']:
    #     for epochs in [10, 20]:
    #         for num_requests in [48000, 96000]:
    #             args.args.collect_train_data = True

    #             const.TRAIN_POLICIES_TO_RUN = [train_policy]
    #             train_workloads = workload_builder.create_train_base_workloads(
    #                 long_tasks_fractions=[0.3, 0.35, 0.4], utilizations=[0.45], num_requests=num_requests)

    #             test_workloads = workload_builder.create_test_base_workloads(utilizations=[0.45], long_tasks_fractions=[
    #                 0.0, 0.1, 0.3, 0.35, 0.4, 0.6, 0.7], num_requests=8000)  # workload_builder.create_test_var_long_tasks_workloads(num_requests=128000)
    #             args.args.epochs = epochs

    #             args.args.seed = SEED
    #             last = rl_experiment_wrapper(args,
    #                                          train_workloads=train_workloads, test_workloads=test_workloads)

    EXPERIMENT_NAME = 'offline_adaption'

    train_workloads = []

    test_workloads = workload_builder.create_test_var_long_tasks_workloads(num_requests=128000)

    const.EVAL_POLICIES_TO_RUN = ['ARS', 'OFFLINE_DQN', 'OFFLINE_DQN_EXPLR_10_TRAIN', 'OFFLINE_DQN_EXPLR_20_TRAIN',
                                  'OFFLINE_DQN_EXPLR_30_TRAIN', 'OFFLINE_DQN_DUPL_10_TRAIN', 'OFFLINE_DQN_DUPL_20_TRAIN', 'OFFLINE_DQN_DUPL_30_TRAIN']
    args = HeterogeneousRequestsArgs(input_args=input_args)
    args.args.exp_name = EXPERIMENT_NAME
    args.args.eps_decay = 180000
    args.args.lr_scheduler_step_size = 30
    args.args.replay_always_use_newest = False
    args.args.collect_train_data = False
    args.args.model_folder = '/home/jonas/projects/absim/outputs/fixed_memory_not_use_latest/0/train/data'
    args.args.offline_model = ''  # '/home/jonas/projects/absim/outputs/fixed_memory_not_use_latest/0/train/data'

    # '/home/jonas/projects/absim/outputs/collect_offline_data/0/collected_training_data'

    for offline_model in ['/home/jonas/projects/absim/outputs/offline_parameter_search/9/offline_train/data', '/home/jonas/projects/absim/outputs/offline_parameter_search/0/offline_train/data']:
        for offline_train_batch_size in [2000, 4000, 8000]:
            args.args.collect_train_data = False
            args.args.offline_model = offline_model
            args.args.offline_train_batch_size = offline_train_batch_size

            args.args.seed = SEED
            last = rl_experiment_wrapper(args,
                                         train_workloads=train_workloads, test_workloads=test_workloads)

    return
    EXPERIMENT_NAME = 'fixed_memory_not_use_latest'

    train_workloads = workload_builder.create_train_base_workloads(
        long_tasks_fractions=[0.3, 0.35, 0.4], utilizations=[0.45], num_requests=8000)

    test_workloads = workload_builder.create_test_var_long_tasks_workloads(
        num_requests=128000)

    const.EVAL_POLICIES_TO_RUN = [
        'round_robin',
        'ARS',
        'DQN',
        'random',
        # 'DQN_EXPLR',
        # 'DQN_DUPL'
    ] + ['DQN_DUPL_10', 'DQN_DUPL_25', 'DQN_DUPL_40', 'DQN_DUPL_45'] + ['DQN_EXPLR_0', 'DQN_EXPLR_10', 'DQN_EXPLR_20', 'DQN_EXPLR_25']

    args = HeterogeneousRequestsArgs(input_args=input_args)
    args.args.exp_name = EXPERIMENT_NAME
    args.args.epochs = 100
    args.args.eps_decay = 180000
    args.args.lr_scheduler_step_size = 30
    args.args.model_folder = ''
    args.args.replay_always_use_newest = False
    args.args.offline_model = 'dummy'

    for service_time_model in ['random.expovariate']:  # 'pareto'
        for test_service_time_model in ['random.expovariate', 'pareto']:  # 'random.expovariate',
            for dqn_explr_lr in [1e-5, 1e-6]:
                args.args.seed = SEED
                args.args.test_service_time_model = test_service_time_model
                args.args.service_time_model = service_time_model
                args.args.dqn_explr_lr = dqn_explr_lr
                last = rl_experiment_wrapper(args,
                                             train_workloads=train_workloads, test_workloads=test_workloads)
                if (test_service_time_model == 'random.expovariate' and dqn_explr_lr == 1e-5):
                    args.args.model_folder = '/home/jonas/projects/absim/outputs/fixed_memory_not_use_latest/0/train/data'

    EXPERIMENT_NAME = 'fixed_memory_train_not_test_use_latest'

    train_workloads = workload_builder.create_train_base_workloads(
        long_tasks_fractions=[0.3, 0.35, 0.4], utilizations=[0.45], num_requests=8000)

    test_workloads = workload_builder.create_test_var_long_tasks_workloads(
        num_requests=128000)

    const.EVAL_POLICIES_TO_RUN = [
        'round_robin',
        'ARS',
        'DQN',
        'random',
        # 'DQN_EXPLR',
        # 'DQN_DUPL'
    ] + ['DQN_DUPL_10', 'DQN_DUPL_25', 'DQN_DUPL_40', 'DQN_DUPL_45'] + ['DQN_EXPLR_0', 'DQN_EXPLR_10', 'DQN_EXPLR_20', 'DQN_EXPLR_25']

    args = HeterogeneousRequestsArgs(input_args=input_args)
    args.args.exp_name = EXPERIMENT_NAME
    args.args.epochs = 100
    args.args.eps_decay = 180000
    args.args.lr_scheduler_step_size = 30
    args.args.model_folder = ''
    args.args.replay_always_use_newest = True
    args.args.offline_model = 'dummy'
    args.args.model_folder = '/home/jonas/projects/absim/outputs/fixed_memory_not_use_latest/0/train/data'

    for service_time_model in ['random.expovariate']:  # 'pareto'
        for test_service_time_model in ['random.expovariate', 'pareto']:  # 'random.expovariate',
            for dqn_explr_lr in [1e-5, 1e-6]:
                args.args.seed = SEED
                args.args.test_service_time_model = test_service_time_model
                args.args.service_time_model = service_time_model
                args.args.dqn_explr_lr = dqn_explr_lr
                last = rl_experiment_wrapper(args,
                                             train_workloads=train_workloads, test_workloads=test_workloads)

    EXPERIMENT_NAME = 'replay_mem_size_experiment'

    train_workloads = workload_builder.create_train_base_workloads(
        long_tasks_fractions=[0.3, 0.35, 0.4], utilizations=[0.45], num_requests=8000)

    test_workloads = workload_builder.create_test_var_long_tasks_workloads(
        num_requests=128000)

    const.EVAL_POLICIES_TO_RUN = [
        'round_robin',
        'ARS',
        'DQN',
        'random',
        # 'DQN_EXPLR',
        # 'DQN_DUPL'
    ] + ['DQN_DUPL_10', 'DQN_DUPL_25', 'DQN_DUPL_40', 'DQN_DUPL_45'] + ['DQN_EXPLR_0', 'DQN_EXPLR_10', 'DQN_EXPLR_20', 'DQN_EXPLR_25']

    args = HeterogeneousRequestsArgs(input_args=input_args)
    args.args.exp_name = EXPERIMENT_NAME
    args.args.epochs = 100
    args.args.eps_decay = 180000
    args.args.lr_scheduler_step_size = 30
    args.args.model_folder = ''
    args.args.offline_model = 'dummy'

    i = 0
    for buffer_size in [25000, 5000, 100000]:  # 'pareto'
        for use_newest in [True, False]:  # 'random.expovariate',
            for dqn_explr_lr in [1e-5, 1e-6]:
                args.args.seed = SEED
                args.args.replay_always_use_newest = use_newest
                args.args.replay_memory_size = buffer_size
                args.args.service_time_model = service_time_model
                args.args.dqn_explr_lr = dqn_explr_lr
                last = rl_experiment_wrapper(args,
                                             train_workloads=train_workloads, test_workloads=test_workloads)
                if dqn_explr_lr == 1e-5:
                    args.args.model_folder = f'/home/jonas/projects/absim/outputs/replay_mem_size_experiment/{i}/train/data'
                else:
                    args.args.model_folder = ''
                i += 1

    return
    EXPERIMENT_NAME = 'larger_replay_memory'

    train_workloads = workload_builder.create_train_base_workloads(
        long_tasks_fractions=[0.3, 0.35, 0.4], utilizations=[0.45], num_requests=8000)

    test_workloads = workload_builder.create_test_var_long_tasks_workloads(
        num_requests=128000)

    const.EVAL_POLICIES_TO_RUN = [
        'round_robin',
        'ARS',
        'DQN',
        'random',
        # 'DQN_EXPLR',
        # 'DQN_DUPL'
    ] + ['DQN_DUPL_10', 'DQN_DUPL_25', 'DQN_DUPL_40', 'DQN_DUPL_45'] + ['DQN_EXPLR_0', 'DQN_EXPLR_10', 'DQN_EXPLR_20', 'DQN_EXPLR_25']

    args = HeterogeneousRequestsArgs(input_args=input_args)
    args.args.exp_name = EXPERIMENT_NAME
    args.args.epochs = 100
    args.args.eps_decay = 180000
    args.args.lr_scheduler_step_size = 30
    args.args.model_folder = ''
    args.args.replay_always_use_newest = True
    args.args.replay_memory_size = 50000
    args.args.model_folder = '/home/jonas/projects/absim/outputs/fixed_memory_not_use_latest/0/train/data'

    args.args.offline_model = 'dummy'

    for service_time_model in ['random.expovariate']:  # 'pareto'
        for test_service_time_model in ['random.expovariate', 'pareto']:  # 'random.expovariate',
            for dqn_explr_lr in [1e-5, 1e-6]:
                args.args.seed = SEED
                args.args.test_service_time_model = test_service_time_model
                args.args.service_time_model = service_time_model
                args.args.dqn_explr_lr = dqn_explr_lr
                last = rl_experiment_wrapper(args,
                                             train_workloads=train_workloads, test_workloads=test_workloads)

    return


if __name__ == '__main__':
    main(setting="base")
