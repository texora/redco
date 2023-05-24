#  Copyright 2021 Google LLC
#  #
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#  #
#      https://www.apache.org/licenses/LICENSE-2.0
#  #
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.

import os
from functools import partial
from itertools import chain
import fire
import datasets
import numpy as np
import jax
import jax.numpy as jnp
import optax
from transformers import AutoTokenizer, FlaxAutoModelForCausalLM
from redco import Deployer, Trainer


def group_texts(examples, block_size):
    concatenated_examples = {
        k: list(chain(*examples[k])) for k in examples.keys()
    }
    total_length = len(concatenated_examples[list(examples.keys())[0]])
    if total_length >= block_size:
        total_length = (total_length // block_size) * block_size
    result = {
        k: [t[i: i + block_size] for i in range(0, total_length, block_size)]
        for k, t in concatenated_examples.items()
    }
    return result


def collate_fn(examples):
    batch = {
        key: np.stack([example[key] for example in examples])
        for key in examples[0].keys()
    }
    batch['labels'] = batch['input_ids'][..., 1:]
    batch['input_ids'] = batch['input_ids'][..., :-1]
    batch['attention_mask'] = batch['attention_mask'][..., :-1]
    return batch


def loss_fn(train_rng, state, params, batch, is_training, model_type):
    labels = batch.pop("labels")
    label_weights = batch['attention_mask']

    if model_type != 'opt':
        is_training_kwarg = {'train': is_training}
    else:
        is_training_kwarg = {'deterministic': not is_training}

    logits = state.apply_fn(
        **batch, params=params, dropout_rng=train_rng, **is_training_kwarg)[0]

    loss = optax.softmax_cross_entropy_with_integer_labels(
        logits=logits, labels=labels)

    return jnp.sum(loss * label_weights) / jnp.sum(label_weights)



def main(text_key='text',
         model_name_or_path='EleutherAI/gpt-j-6b',
         n_model_shards=4,
         n_epochs=2,
         per_device_batch_size=4,
         eval_per_device_batch_size=8,
         accumulate_grad_batches=1,
         max_length=1024,
         learning_rate=2e-5,
         warmup_rate=0.1,
         weight_decay=0.,
         jax_seed=42):
    tokenizer = AutoTokenizer.from_pretrained(model_name_or_path)
    tokenizer.pad_token = tokenizer.eos_token
    with jax.default_device(jax.devices('cpu')[0]):
        model = FlaxAutoModelForCausalLM.from_pretrained(model_name_or_path)
        model.params = model.to_fp32(model.params)

    raw_dataset = datasets.load_dataset('wikitext', 'wikitext-2-raw-v1')
    tokenized_dataset = raw_dataset.map(
        lambda example: tokenizer(example[text_key]),
        batched=True,
        num_proc=os.cpu_count(),
        remove_columns=list(raw_dataset['train'][0].keys()),
        load_from_cache_file=True,
        desc="Running tokenizer on dataset")
    dataset = tokenized_dataset.map(
        partial(group_texts, block_size=max_length),
        batched=True,
        num_proc=os.cpu_count(),
        load_from_cache_file=True,
        desc=f"Grouping texts in chunks of {max_length}")

    dataset = {split: list(dataset[split]) for split in dataset.keys()}

    deployer = Deployer(jax_seed=jax_seed, n_model_shards=n_model_shards)

    optimizer, lr_schedule_fn = deployer.get_adamw_optimizer(
        train_size=len(dataset['train']),
        per_device_batch_size=per_device_batch_size,
        n_epochs=n_epochs,
        learning_rate=learning_rate,
        accumulate_grad_batches=accumulate_grad_batches,
        warmup_rate=warmup_rate,
        weight_decay=weight_decay)

    trainer = Trainer(
        deployer=deployer,
        collate_fn=collate_fn,
        apply_fn=model.__call__,
        loss_fn=partial(loss_fn, model_type=model.config.model_type),
        params=model.params,
        optimizer=optimizer,
        lr_schedule_fn=lr_schedule_fn,
        params_shard_rules=deployer.get_sharding_rules(params=model.params))

    trainer.fit(
        train_examples=dataset['train'],
        n_epochs=n_epochs,
        per_device_batch_size=per_device_batch_size,
        eval_examples=dataset['validation'],
        eval_per_device_batch_size=eval_per_device_batch_size,
        eval_loss=True)


if __name__ == '__main__':
    fire.Fire(main)