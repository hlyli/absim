# These are defaults.
NW_LATENCY_BASE = 0.960
NW_LATENCY_MU = 0.040
NW_LATENCY_SIGMA = 0.0
NUMBER_OF_CLIENTS = 1

DQN_EXPLR_SETTINGS = [item for i in range(101) for item in [f'DQN_EXPLR_{i}_TRAIN', f'DQN_EXPLR_{i}']]

POLICY_ORDER = ["DQN", 'DQN_OPTIMIZED', "DQN_DUPL", "DQN_EXPLR"] + DQN_EXPLR_SETTINGS + ["random", "ARS", "round_robin"]

POLICY_COLORS = {
    "ARS": "C0",
    "random": "C1",
    "DQN": "C2",
    "DQN_OPTIMIZED": "C2",
    "round_robin": "C3",
    'DQN_EXPLR': "C4",
    "DQN_DUPL": 'C5',
} | {f'DQN_EXPLR_{i}': 'C4' for i in range(101)} | {f'DQN_EXPLR_{i}_TRAIN': 'C4' for i in range(101)}

# Pareto distribution alpha
ALPHA = 1.1


TRAIN_POLICIES_TO_RUN = [
    # 'round_robin',
    'ARS',
    # 'response_time',
    # 'weighted_response_time',
    # 'random',
    'DQN'
]


EVAL_POLICIES_TO_RUN = [
    # 'round_robin',
    'ARS',
    'DQN',
    'random',
    # 'DQN_EXPLR',
    'DQN_DUPL'
] + ['DQN_EXPLR_0', 'DQN_EXPLR_10', 'DQN_EXPLR_15', 'DQN_EXPLR_20', 'DQN_EXPLR_25']
