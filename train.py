import os
# Make sure this is above the import of AnimalAIEnvironment
os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"  # noqa

import random
import argparse
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass

import logging
logging.basicConfig(  # noqa
    format='[%(asctime)s] [%(levelname)-8s] [%(module)s] %(message)s',
    level=logging.INFO
)

import dreamerv3
from dreamerv3 import embodied
from dreamerv3.embodied.envs import from_gym
from gym.wrappers.compatibility import EnvCompatibility
from mlagents_envs.envs.unity_gym_env import UnityToGymWrapper
from animalai.envs.environment import AnimalAIEnvironment

@dataclass
class Args:
    task: Path
    aai: Path
    # TODO: Checkpoint restart


def run(args: Args):
    task_path = args.task
    task_name = Path(args.task).stem
    date = datetime.now().strftime("%Y_%m_%d_%H_%M")
    logdir = Path("./logdir/") / f'training-{date}-{task_name}'

    logging.info("Creating DreamerV3 config")
    dreamer_config, step, logger = get_dreamer_config(logdir)

    logging.info(f"Creating AAI Dreamer Environment")
    env = get_aai_env(task_path, args.aai, dreamer_config)

    logging.info("Creating DreamerV3 Agent")
    agent = dreamerv3.Agent(env.obs_space, env.act_space, step, dreamer_config)
    replay = embodied.replay.Uniform(
        dreamer_config.batch_length, 
        dreamer_config.replay_size,
        logdir / 'replay')
    emb_config = embodied.Config(
        **dreamer_config.run, 
        logdir=dreamer_config.logdir,
        batch_steps=dreamer_config.batch_size * dreamer_config.batch_length)  # type: ignore

    logging.info("Starting training")
    embodied.run.train(agent, env, replay, logger, emb_config)
    # embodied.run.eval_only(agent, env, logger, args)


def get_dreamer_config(logdir: Path):
    # See configs.yaml for all options.
    config = embodied.Config(dreamerv3.configs['defaults'])
    config = config.update(dreamerv3.configs['xlarge'])
    config = config.update({
        'logdir': logdir,
        'run.train_ratio': 64,
        'run.log_every': 60,  # Seconds
        'batch_size': 16,
        'jax.prealloc': False,
        'encoder.mlp_keys': '$^',
        'decoder.mlp_keys': '$^',
        'encoder.cnn_keys': 'image',
        'decoder.cnn_keys': 'image',
        #   'jax.platform': 'cpu',
    })
    config = embodied.Flags(config).parse()

    step = embodied.Counter()

    logger = embodied.Logger(step, [
        embodied.logger.TerminalOutput(),
        embodied.logger.JSONLOutput(logdir, 'metrics.jsonl'),
        embodied.logger.TensorBoardOutput(logdir),
        # embodied.logger.WandBOutput(logdir.name, config),
        # embodied.logger.MLFlowOutput(logdir.name),
    ])

    return config, step, logger


def get_aai_env(task_path, env_path, dreamer_config):
    # Use a random port to avoid problems if a previous version exits slowly
    port = 5005 + random.randint(0, 1000)

    logging.info("Initializing AAI environment")
    aai_env = AnimalAIEnvironment(
        file_name=env_path,
        base_port=port,
        arenas_configurations=task_path,
        # Set pixels to 64x64 cause it has to be power of 2 for dreamerv3
        resolution=64, # same size as Minecraft in DreamerV3
    )
    env = UnityToGymWrapper(
        aai_env, uint8_visual=True,
        # allow_multiple_obs=True, # This crashes somewhere in one of the wrappers.
        flatten_branched=True)  # Necessary. Dreamerv3 doesn't support MultiDiscrete action space.
    env = EnvCompatibility(env, render_mode='rgb_array')  # type: ignore
    env = from_gym.FromGym(env, obs_key='image')
    logging.info(f"Using observation space {env.obs_space}")
    logging.info(f"Using action space {env.act_space}")
    env = dreamerv3.wrap_env(env, dreamer_config)
    env = embodied.BatchEnv([env], parallel=False)

    return env


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--task', type=Path, required=True)
    parser.add_argument('--aai', type=Path, default=Path('./aai/env/AnimalAI.x86_64'))
    args_raw = parser.parse_args()

    args = Args(**vars(args_raw))

    run(args)