import jax


def default_loss_and_grads(train_rng, state, batch, loss_fn):
    def compute_loss(params):
        return loss_fn(
            train_rng=train_rng,
            state=state,
            params=params,
            batch=batch,
            is_training=True)

    grad_fn = jax.value_and_grad(compute_loss)
    return grad_fn(state.params)


def default_train_step(train_rng,
                       state,
                       batch,
                       loss_fn,
                       lr_schedule_fn,
                       params_grad_weights,
                       under_pmap):
    loss, grads = default_loss_and_grads(
        train_rng=train_rng, state=state, batch=batch, loss_fn=loss_fn)

    if params_grad_weights is not None:
        grads = jax.tree_util.tree_map(
            lambda x, y: x * y, grads, params_grad_weights)

    if under_pmap:
        grads = jax.lax.pmean(grads, 'batch')

    new_state = state.apply_gradients(grads=grads)

    metrics = {'loss': loss, 'step': state.step}
    if lr_schedule_fn is not None:
        metrics.update({'lr': lr_schedule_fn(state.step)})

    if under_pmap:
        metrics = jax.lax.pmean(metrics, axis_name='batch')

    return new_state, metrics


def default_eval_step(state, batch, loss_fn, under_pmap):
    loss = loss_fn(
        train_rng=jax.random.PRNGKey(0),
        state=state,
        params=state.params,
        batch=batch,
        is_training=False)

    metrics = {'loss': loss}
    if under_pmap:
        metrics = jax.lax.pmean(metrics, axis_name='batch')

    return metrics
