from functools import partial
import json
import pickle
import numpy as np

import jax
from jax.experimental.pjit import pjit
from jax.experimental.pjit import PartitionSpec as P
from flax.jax_utils import replicate
from flax.training.train_state import TrainState
from flax.traverse_util import flatten_dict

from .utils import default_train_step, default_eval_step


class Trainer:
    def __init__(self,
                 deployer,
                 collate_fn,
                 apply_fn,
                 loss_fn,
                 params,
                 optimizer,
                 lr_schedule_fn=None,
                 params_shard_rules=None):
        self._deployer = deployer
        self._collate_fn = collate_fn
        self._loss_fn = loss_fn
        self._lr_schedule_fn = lr_schedule_fn

        self._state = None
        self._state_spec = None
        self._p_train_step = None
        self._p_eval_step = None

        self.create_train_state(
            apply_fn=apply_fn,
            params=params,
            params_shard_rules=params_shard_rules,
            optimizer=optimizer)

        n_params = \
            sum(np.prod(param.shape) for param in flatten_dict(params).values())
        self._deployer.logger.info(f'#params: {n_params}')

    def create_train_state(self,
                           apply_fn,
                           params,
                           params_shard_rules,
                           optimizer):
        if self._deployer.mesh is None:
            self._state = TrainState.create(
                apply_fn=apply_fn, params=params, tx=optimizer)
            self._state = replicate(self._state)
        else:
            params_spec = self._deployer.get_params_spec(
                params=params, shard_rules=params_shard_rules)

            params, opt_state, opt_state_spec = \
                self._deployer.shard_params_and_opt_state(
                    params=params, params_spec=params_spec, optimizer=optimizer)

            self._state = TrainState(
                apply_fn=apply_fn,
                params=params,
                tx=optimizer,
                opt_state=opt_state,
                step=0)

            self._state_spec = TrainState(
                apply_fn=apply_fn,
                params=params_spec,
                tx=optimizer,
                opt_state=opt_state_spec,
                step=None)

    def setup_running_step(self, dummy_batch):
        train_step_fn = partial(
            default_train_step,
            loss_fn=self._loss_fn,
            lr_schedule_fn=self._lr_schedule_fn,
            under_pmap=(self._deployer.mesh is None))

        eval_step_fn = partial(
            default_eval_step,
            loss_fn=self._loss_fn,
            under_pmap=(self._deployer.mesh is None))

        if self._deployer.mesh is None:
            del dummy_batch

            self._p_train_step = jax.pmap(train_step_fn, axis_name='batch')
            self._p_eval_step = jax.pmap(eval_step_fn, axis_name='batch')
        else:
            data_spec = {
                key: P(*(('dp',) + (None,) * (len(value.shape) - 1)))
                for key, value in dummy_batch.items()
            }

            self._p_train_step = pjit(
                train_step_fn,
                in_axis_resources=(None, self._state_spec, data_spec),
                out_axis_resources=(self._state_spec, None),
                donate_argnums=(1, ))

            self._p_eval_step = pjit(
                eval_step_fn,
                in_axis_resources=(self._state_spec, data_spec),
                out_axis_resources=None)

    def train(self, examples, per_device_batch_size, desc=''):
        data_batches = self._deployer.get_model_input_batches(
            examples=examples,
            per_device_batch_size=per_device_batch_size,
            collate_fn=self._collate_fn,
            shuffle=True,
            shuffle_rng=self._deployer.gen_rng(),
            desc=f'Training ({desc})')

        for batch in data_batches:
            if self._p_train_step is None:
                self.setup_running_step(dummy_batch=batch)

            train_rng = self._deployer.process_to_run_model(
                self._deployer.gen_rng())
            self._state, metrics = self._deployer.run_model_step(
                step_fn=self._p_train_step,
                input_args=(train_rng, self._state, batch))

            metrics = self._deployer.process_to_deliver(metrics)
            data_batches.set_postfix(**metrics)

    def eval_loss(self, examples, per_device_batch_size):
        data_batches = self._deployer.get_model_input_batches(
            examples=examples,
            per_device_batch_size=per_device_batch_size,
            collate_fn=self._collate_fn,
            shuffle=False,
            shuffle_rng=None,
            desc=f'Evaluating')

        losses = []
        for batch in data_batches:
            if self._p_eval_step is None:
                self.setup_running_step(dummy_batch=batch)

            metrics = self._deployer.run_model_step(
                step_fn=self._p_eval_step, input_args=(self._state, batch))

            metrics = self._deployer.process_to_deliver(metrics)
            data_batches.set_postfix(**metrics)

            losses.append(metrics['loss'])

        return np.mean(losses).item()

    def fit(self,
            train_examples,
            per_device_batch_size,
            n_epochs,
            eval_examples=None,
            eval_per_device_batch_size=None,
            eval_loss=True,
            eval_predictor=None,
            eval_metric_fn=None):
        for epoch_idx in range(n_epochs):
            if isinstance(train_examples, list):
                epoch_train_examples = train_examples
            else:
                epoch_train_examples = train_examples(epoch_idx=epoch_idx)

            self.train(
                examples=epoch_train_examples,
                per_device_batch_size=per_device_batch_size,
                desc=f'epoch {epoch_idx} / {n_epochs}')

            if eval_examples is None:
                self._deployer.logger.info(
                    'No evaluation cuz \'eval_examples\' is None.')
            else:
                eval_metrics = {}

                if eval_per_device_batch_size is None:
                    eval_per_device_batch_size = per_device_batch_size

                if eval_loss:
                    loss = self.eval_loss(
                        examples=eval_examples,
                        per_device_batch_size=eval_per_device_batch_size)
                    eval_metrics['loss'] = loss

                if eval_predictor is not None:
                    preds = eval_predictor.predict(
                        examples=eval_examples,
                        params=self.params,
                        per_device_batch_size=eval_per_device_batch_size)

                    eval_results = [
                        {'example': example, 'pred': pred}
                        for example, pred in zip(eval_examples, preds)]

                    try:
                        json.dump(
                            eval_results,
                            open(f'outputs_epoch{epoch_idx}.json', 'w'),
                            indent=4)
                    except:
                        pickle.dump(eval_results, open(
                            f'outputs_epoch{epoch_idx}.pkl', 'wb'))

                    if eval_metric_fn is not None:
                        eval_metrics.update(eval_metric_fn(eval_results))

                self._deployer.logger.info(
                    f'Epoch {epoch_idx}, evaluation results: {eval_metrics}')

    @property
    def params(self):
        return self._deployer.process_to_deliver(self._state.params)

    @property
    def step(self):
        return self._deployer.process_to_deliver(self._state.step)

    def get_default_predictor(self, *args, **kwargs):
        raise NotImplementedError
