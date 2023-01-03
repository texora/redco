from typing import Callable

import jax
import jax.numpy as jnp

from flax import struct
from flax.training import train_state
from flax.jax_utils import replicate
from flax.training.common_utils import shard_prng_key


class TrainState(train_state.TrainState):
    dropout_rng: jnp.ndarray
    lr_schedule_fn: Callable = struct.field(pytree_node=False)

    def replicate(self):
        return replicate(self).replace(
            dropout_rng=shard_prng_key(self.dropout_rng))


def default_loss_and_grads(state, batch, loss_fn):
    def compute_loss(params):
        return loss_fn(state=state, params=params, batch=batch, train=True)

    grad_fn = jax.value_and_grad(compute_loss)
    return grad_fn(state.params)


def default_train_step(state, batch, loss_fn, under_pmap):
    loss, grad = default_loss_and_grads(
        state=state, batch=batch, loss_fn=loss_fn)
    if under_pmap:
        grad = jax.lax.pmean(grad, 'batch')

    dropout_rng, new_dropout_rng = jax.random.split(state.dropout_rng)
    new_state = state.apply_gradients(grads=grad, dropout_rng=new_dropout_rng)

    metrics = {
        'loss': loss,
        'step': state.step,
        'lr': state.lr_schedule_fn(state.step)
    }
    if under_pmap:
        metrics = jax.lax.pmean(metrics, axis_name='batch')

    return new_state, metrics


def default_eval_step(state, batch, loss_fn, under_pmap):
    loss = loss_fn(state=state, params=state.params, batch=batch, train=False)

    metrics = {'loss': loss}
    if under_pmap:
        metrics = jax.lax.pmean(metrics, axis_name='batch')

    return metrics
