import asyncio
import contextlib
import json
import os

import numpy as np

from .image_api import RATIOS, RESOLUTIONS, encode_image, image_size, request_image


def check_interrupt():
    try:
        import comfy.model_management
    except ImportError:
        return
    comfy.model_management.throw_exception_if_processing_interrupted()


async def interruptible(coroutine):
    task = asyncio.create_task(coroutine)
    try:
        while not task.done():
            check_interrupt()
            await asyncio.wait({task}, timeout=0.25)
        check_interrupt()
        return await task
    finally:
        if not task.done():
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


class FFGPTImage25:
    CATEGORY = "FF Nodes/GPT Image"
    FUNCTION = "generate"
    RETURN_TYPES = ("IMAGE", "STRING", "STRING", "MASK")
    RETURN_NAMES = ("image", "response", "image_url", "mask")
    DESCRIPTION = "GPT Image 2.5 文生图 / 16 图参考编辑。兼容同步 OpenAI Images API。"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "task_type": (["auto", "generate", "edit"], {"default": "auto", "tooltip": "auto：接图编辑，不接图生成。"}),
                "url": ("STRING", {"default": "https://api.openai.com/v1", "tooltip": "服务根地址、/v1、/v1/images 或完整 generations/edits 地址。"}),
                "api_format": (["v1/images (multipart)", "v1/images (JSON)"],),
                "model": (["gpt-image-2.5-sunburst", "gpt-image-2.5-flare", "custom"],),
                "custom_model": ("STRING", {"default": "", "tooltip": "model=custom 时使用服务商的模型 ID。"}),
                "api_key": ("STRING", {"default": "", "tooltip": "留空读取 FF_GPT_IMAGE_API_KEY 或 OPENAI_API_KEY；填写值会随工作流保存。"}),
                "prompt": ("STRING", {"default": "", "multiline": True, "dynamicPrompts": False}),
                "ratio": (list(RATIOS), {"default": "1:1"}),
                "resolution": (list(RESOLUTIONS), {"default": "2K", "tooltip": "按比例换算为合法尺寸；4K 方图为 2880×2880，16:9 为 3840×2160。"}),
                "quality": (["auto", "low", "medium", "high", "xhigh", "max"], {"default": "medium"}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xFFFFFFFF, "control_after_generate": True, "tooltip": "改变种子可触发重新生成。官方 Images API 不支持固定种子复现。"}),
                "timeout": ("INT", {"default": 1800, "min": 1, "max": 7200, "step": 1, "tooltip": "秒；包括生成和下载。"}),
                "transparent_background": ("BOOLEAN", {"default": False, "label_on": "透明底开启", "label_off": "透明底关闭"}),
                "send_seed": ("BOOLEAN", {"default": False, "tooltip": "仅在第三方明确支持 seed 参数时打开；官方请保持关闭。"}),
            },
            "optional": {f"image{i}": ("IMAGE", {"tooltip": "可不连接；多图按端口顺序发送，批次全部计入 16 张上限。"}) for i in range(1, 17)},
        }

    async def generate(self, task_type, url, api_format, model, custom_model, api_key,
                       prompt, ratio, resolution, quality, seed, timeout,
                       transparent_background, send_seed, **kwargs):
        api_key = api_key.strip() or os.getenv("FF_GPT_IMAGE_API_KEY", "").strip() or os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise ValueError("请填写 api_key，或设置 FF_GPT_IMAGE_API_KEY / OPENAI_API_KEY。")
        if not prompt.strip():
            raise ValueError("提示词不能为空。")
        if task_type not in ("auto", "generate", "edit"):
            raise ValueError("未知的 task_type。")
        if api_format not in ("v1/images (multipart)", "v1/images (JSON)"):
            raise ValueError("未知的 api_format。")
        selected_model = custom_model.strip() if model == "custom" else model
        if not selected_model:
            raise ValueError("请选择模型，或填写 custom_model。")
        if not 1 <= timeout <= 7200:
            raise ValueError("timeout 必须在 1–7200 秒之间。")

        batches, count = [], 0
        for index in range(1, 17):
            value = kwargs.get(f"image{index}")
            if value is None:
                continue
            if value.ndim != 4 or value.shape[0] == 0:
                raise ValueError(f"image{index} 必须是非空 ComfyUI IMAGE 批次 [B,H,W,C]。")
            count += value.shape[0]
            batches.append(value)
        if count > 16:
            raise ValueError(f"参考图合计 {count} 张，最多 16 张（包括每个端口的批次）。")
        if task_type == "edit" and not count:
            raise ValueError("edit 模式至少需要连接一张图片。")
        if task_type == "generate" and count:
            raise ValueError("generate 模式不能使用参考图；请选择 auto 或 edit。")

        images = []
        for batch in batches:
            for frame in batch:
                check_interrupt()
                images.append(encode_image(frame.detach().cpu().float().numpy()))
        fields = {
            "model": selected_model, "prompt": prompt, "n": 1,
            "size": image_size(ratio, resolution), "quality": quality,
            "background": "transparent" if transparent_background else "opaque",
            "output_format": "png",
        }
        if send_seed:
            fields["seed"] = int(seed)
        decoded, response, image_url = await interruptible(
            request_image(url, api_key, fields, images, api_format, timeout)
        )
        response["ff_request"] = {
            "model": selected_model, "size": fields["size"], "reference_images": count,
            "mode": "edit" if images else "generate", "seed_sent": bool(send_seed),
        }
        import torch

        rgb = torch.from_numpy(np.stack([item[0] for item in decoded]))
        mask = torch.from_numpy(np.stack([item[1] for item in decoded]))
        return rgb, json.dumps(response, ensure_ascii=False, indent=2), image_url, mask


NODE_CLASS_MAPPINGS = {"FF_GPT_Image_25": FFGPTImage25}
NODE_DISPLAY_NAME_MAPPINGS = {"FF_GPT_Image_25": "FF GPT Image 2.5 Generator"}
