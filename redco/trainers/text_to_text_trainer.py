from functools import partial

from .trainer import Trainer
from ..utils.text_to_text_utils import \
    text_to_text_default_collate_fn, text_to_text_default_loss_fn

from ..predictors.text_to_text_predictor import TextToTextPredictor


class TextToTextTrainer(Trainer):
    def __init__(self,
                 deployer,
                 model,
                 params,
                 optimizer,
                 learning_rate,
                 tokenizer,
                 max_src_len,
                 max_tgt_len,
                 src_key='src',
                 tgt_key='tgt',
                 params_shard_rules=None):
        collate_fn = partial(
            text_to_text_default_collate_fn,
            tokenizer=tokenizer,
            decoder_start_token_id=model.config.decoder_start_token_id,
            max_src_len=max_src_len,
            max_tgt_len=max_tgt_len,
            src_key=src_key,
            tgt_key=tgt_key)

        super(TextToTextTrainer, self).__init__(
            collate_fn=collate_fn,
            apply_fn=model.__call__,
            loss_fn=text_to_text_default_loss_fn,
            params=params,
            optimizer=optimizer,
            deployer=deployer,
            learning_rate=learning_rate,
            params_shard_rules=params_shard_rules)

        self._default_predictor_fn = partial(
            TextToTextPredictor,
            deployer=deployer,
            hf_model=model,
            tokenizer=tokenizer,
            max_src_len=max_src_len,
            max_tgt_len=max_tgt_len,
            src_key=src_key,
            tgt_key=tgt_key,
            params=params,
            params_shard_rules=params_shard_rules)

    def get_default_predictor(self, gen_kwargs):
        return self._default_predictor_fn(gen_kwargs=gen_kwargs)