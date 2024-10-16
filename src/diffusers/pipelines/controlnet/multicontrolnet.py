import os
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import torch
from torch import nn
from itertools import compress

from ...models.controlnet import ControlNetModel, ControlNetOutput
from ...models.modeling_utils import ModelMixin
from ...utils import logging


logger = logging.get_logger(__name__)


class MultiControlNetModel(ModelMixin):
    r"""
    Multiple `ControlNetModel` wrapper class for Multi-ControlNet

    This module is a wrapper for multiple instances of the `ControlNetModel`. The `forward()` API is designed to be
    compatible with `ControlNetModel`.

    Args:
        controlnets (`List[ControlNetModel]`):
            Provides additional conditioning to the unet during the denoising process. You must set multiple
            `ControlNetModel` as a list.
    """

    def __init__(self, controlnets: Union[List[ControlNetModel], Tuple[ControlNetModel]]):
        super().__init__()
        self.nets = nn.ModuleList(controlnets)
        self.control_disable = [True] * len(controlnets)

    def num_active_controlnets(self) -> int:
        return sum(self.control_disable)

    def toggle_controlnets(self, flags: list):
        r"""
        Disable or enable controlnets by index. Minnimum one needs to be active else
        raises ValueError.
        """
        if len(self.control_disable) != len(flags):
            raise ValueError(f"One value per controlnet expected ({len(self.control_disable)}), got {len(flags)}.")
        self.control_disable = flags
        flags.index(True)  # Raise Value error if none are active

    def forward(
        self,
        sample: torch.Tensor,
        timestep: Union[torch.Tensor, float, int],
        encoder_hidden_states: torch.Tensor,
        controlnet_cond: List[torch.tensor],
        conditioning_scale: List[float],
        class_labels: Optional[torch.Tensor] = None,
        timestep_cond: Optional[torch.Tensor] = None,
        attention_mask: Optional[torch.Tensor] = None,
        added_cond_kwargs: Optional[Dict[str, torch.Tensor]] = None,
        cross_attention_kwargs: Optional[Dict[str, Any]] = None,
        guess_mode: bool = False,
        return_dict: bool = True,
    ) -> Union[ControlNetOutput, Tuple]:
        # print(f"Multi controlnet before {[str(c.shape) for c in controlnet_cond]}")
        # print(f"zip: {zip(controlnet_cond, conditioning_scale, self.nets)}")
        # print(f"conditioning size: {len(conditioning_scale)}")
        #n = int(sample.shape[0] / len(self.nets))
        #print(f"batchsize: {n}")
        n = len(self.nets)
        # controlnet_cond = [controlnet_cond[i * n:(i + 1) * n] for i in range((len(controlnet_cond) + n - 1) // n)]
        controlnet_cond = [torch.cat([controlnet_cond[i + n*j].unsqueeze(0) for j in range(len(controlnet_cond) // n)]) for i in range(n)]
        down_block_res_samples, mid_block_res_sample = (None, None)
        for i, (image, scale, controlnet) in enumerate(zip(controlnet_cond,
                                                           compress(conditioning_scale, self.control_disable),
                                                           compress(self.nets, self.control_disable))):
            down_samples, mid_sample = controlnet(
                sample=sample,
                timestep=timestep,
                encoder_hidden_states=encoder_hidden_states,
                controlnet_cond=image,
                conditioning_scale=scale,
                class_labels=class_labels,
                timestep_cond=timestep_cond,
                attention_mask=attention_mask,
                added_cond_kwargs=added_cond_kwargs,
                cross_attention_kwargs=cross_attention_kwargs,
                guess_mode=guess_mode,
                return_dict=return_dict,
            )
            # print(f"{i}: {image.shape}, {scale}, [{','.join([str(layer.shape) for layer in down_samples])}]")
            #
            # if i == 0:
            #
            #     def gen_mask(shape):
            #         mask_p = torch.ones((1, *shape[1:]), dtype=down_samples[j].dtype,
            #                             device=down_samples[j].device)
            #         mask_n = torch.zeros((1, *shape[1:]), dtype=down_samples[j].dtype,
            #                              device=down_samples[j].device)
            #         return torch.cat([mask_p if (i % 6) > 3 else mask_n for i in range(shape[0])], dim=0)
            #
            #     for j, layer in enumerate(down_samples):
            #         mask = gen_mask(layer.shape)
            #         down_samples[j] = mask * layer
            #
            #     # for j, layer in enumerate(down_samples):
            #     #     down_samples[j] = 0 * down_samples[j]
            #
            #     mid_sample = gen_mask(mid_sample.shape) * mid_sample


            # # measure variance
            # for j, layer in enumerate(down_samples):
            #     var = torch.var(layer, dim=(1, 2, 3))
            #     # logger.warning(f"Variance [{j}]: {var.shape} -> {var}")
            #     norm = torch.nn.functional.normalize(torch.var(layer, dim=(1, 2, 3)), dim=0)
            #     # logger.warning(f"Norm [{j}]: {norm.shape} -> {norm}")
            #     # logger.warning(f"layer: {layer.shape}")
            #     shape = list(layer.shape)
            #     shape[0] = 1
            #     unsqueezer = [1] * len(shape)
            #     unsqueezer[0] = norm.shape[0]
            #     factor = 1
            #     #down_samples[j] = layer * norm.reshape(unsqueezer).repeat(shape)
            #
            # logger.warning(f"Mid sample {mid_sample.shape}")
            #
            # var = torch.var(mid_sample, dim=(1, 2, 3))
            # logger.warning(f"Variance [mid]: {var.shape} -> {var}")
            # norm = torch.nn.functional.normalize(torch.var(mid_sample, dim=(1, 2, 3)), dim=0)
            # logger.warning(f"Norm [mid]: {norm.shape} -> {norm}")
            # logger.warning(f"layer: {mid_sample.shape}")
            # shape = list(mid_sample.shape)
            # shape[0] = 1
            # unsqueezer = [1] * len(shape)
            # unsqueezer[0] = norm.shape[0]
            # factor = 1
            # #mid_sample = mid_sample * norm.reshape(unsqueezer).repeat(shape)

            # merge samples
            if mid_block_res_sample is None:
                down_block_res_samples, mid_block_res_sample = down_samples, mid_sample
            else:
                down_block_res_samples = [
                    samples_prev + samples_curr
                    for samples_prev, samples_curr in zip(down_block_res_samples, down_samples)
                ]
                mid_block_res_sample += mid_sample

        return down_block_res_samples, mid_block_res_sample

    def save_pretrained(
        self,
        save_directory: Union[str, os.PathLike],
        is_main_process: bool = True,
        save_function: Callable = None,
        safe_serialization: bool = True,
        variant: Optional[str] = None,
    ):
        """
        Save a model and its configuration file to a directory, so that it can be re-loaded using the
        `[`~pipelines.controlnet.MultiControlNetModel.from_pretrained`]` class method.

        Arguments:
            save_directory (`str` or `os.PathLike`):
                Directory to which to save. Will be created if it doesn't exist.
            is_main_process (`bool`, *optional*, defaults to `True`):
                Whether the process calling this is the main process or not. Useful when in distributed training like
                TPUs and need to call this function on all processes. In this case, set `is_main_process=True` only on
                the main process to avoid race conditions.
            save_function (`Callable`):
                The function to use to save the state dictionary. Useful on distributed training like TPUs when one
                need to replace `torch.save` by another method. Can be configured with the environment variable
                `DIFFUSERS_SAVE_MODE`.
            safe_serialization (`bool`, *optional*, defaults to `True`):
                Whether to save the model using `safetensors` or the traditional PyTorch way (that uses `pickle`).
            variant (`str`, *optional*):
                If specified, weights are saved in the format pytorch_model.<variant>.bin.
        """
        for idx, controlnet in enumerate(self.nets):
            suffix = "" if idx == 0 else f"_{idx}"
            controlnet.save_pretrained(
                save_directory + suffix,
                is_main_process=is_main_process,
                save_function=save_function,
                safe_serialization=safe_serialization,
                variant=variant,
            )

    @classmethod
    def from_pretrained(cls, pretrained_model_path: Optional[Union[str, os.PathLike]], **kwargs):
        r"""
        Instantiate a pretrained MultiControlNet model from multiple pre-trained controlnet models.

        The model is set in evaluation mode by default using `model.eval()` (Dropout modules are deactivated). To train
        the model, you should first set it back in training mode with `model.train()`.

        The warning *Weights from XXX not initialized from pretrained model* means that the weights of XXX do not come
        pretrained with the rest of the model. It is up to you to train those weights with a downstream fine-tuning
        task.

        The warning *Weights from XXX not used in YYY* means that the layer XXX is not used by YYY, therefore those
        weights are discarded.

        Parameters:
            pretrained_model_path (`os.PathLike`):
                A path to a *directory* containing model weights saved using
                [`~diffusers.pipelines.controlnet.MultiControlNetModel.save_pretrained`], e.g.,
                `./my_model_directory/controlnet`.
            torch_dtype (`str` or `torch.dtype`, *optional*):
                Override the default `torch.dtype` and load the model under this dtype. If `"auto"` is passed the dtype
                will be automatically derived from the model's weights.
            output_loading_info(`bool`, *optional*, defaults to `False`):
                Whether or not to also return a dictionary containing missing keys, unexpected keys and error messages.
            device_map (`str` or `Dict[str, Union[int, str, torch.device]]`, *optional*):
                A map that specifies where each submodule should go. It doesn't need to be refined to each
                parameter/buffer name, once a given module name is inside, every submodule of it will be sent to the
                same device.

                To have Accelerate compute the most optimized `device_map` automatically, set `device_map="auto"`. For
                more information about each option see [designing a device
                map](https://hf.co/docs/accelerate/main/en/usage_guides/big_modeling#designing-a-device-map).
            max_memory (`Dict`, *optional*):
                A dictionary device identifier to maximum memory. Will default to the maximum memory available for each
                GPU and the available CPU RAM if unset.
            low_cpu_mem_usage (`bool`, *optional*, defaults to `True` if torch version >= 1.9.0 else `False`):
                Speed up model loading by not initializing the weights and only loading the pre-trained weights. This
                also tries to not use more than 1x model size in CPU memory (including peak memory) while loading the
                model. This is only supported when torch version >= 1.9.0. If you are using an older version of torch,
                setting this argument to `True` will raise an error.
            variant (`str`, *optional*):
                If specified load weights from `variant` filename, *e.g.* pytorch_model.<variant>.bin. `variant` is
                ignored when using `from_flax`.
            use_safetensors (`bool`, *optional*, defaults to `None`):
                If set to `None`, the `safetensors` weights will be downloaded if they're available **and** if the
                `safetensors` library is installed. If set to `True`, the model will be forcibly loaded from
                `safetensors` weights. If set to `False`, loading will *not* use `safetensors`.
        """
        idx = 0
        controlnets = []

        # load controlnet and append to list until no controlnet directory exists anymore
        # first controlnet has to be saved under `./mydirectory/controlnet` to be compliant with `DiffusionPipeline.from_prertained`
        # second, third, ... controlnets have to be saved under `./mydirectory/controlnet_1`, `./mydirectory/controlnet_2`, ...
        model_path_to_load = pretrained_model_path
        while os.path.isdir(model_path_to_load):
            controlnet = ControlNetModel.from_pretrained(model_path_to_load, **kwargs)
            controlnets.append(controlnet)

            idx += 1
            model_path_to_load = pretrained_model_path + f"_{idx}"

        logger.info(f"{len(controlnets)} controlnets loaded from {pretrained_model_path}.")

        if len(controlnets) == 0:
            raise ValueError(
                f"No ControlNets found under {os.path.dirname(pretrained_model_path)}. Expected at least {pretrained_model_path + '_0'}."
            )

        return cls(controlnets)
