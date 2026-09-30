"""OpenAI-compatible Images API transport, independent of ComfyUI and torch."""

import asyncio
import base64
import binascii
import io
import math
from urllib.parse import urlsplit, urlunsplit

import aiohttp
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

RATIOS = ("1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "21:9", "9:21", "3:1", "1:3")
RESOLUTIONS = ("1K", "2K", "4K")
MAX_RESPONSE_BYTES = 128 * 1024 * 1024
MAX_IMAGE_BYTES = 50 * 1024 * 1024


class ImageAPIError(RuntimeError):
    pass


def image_size(ratio, resolution):
    """Preserve the exact ratio and multiples of 16 within API pixel limits."""
    if ratio not in RATIOS or resolution not in RESOLUTIONS:
        raise ValueError("不支持的图像比例或分辨率。")
    w, h = map(int, ratio.split(":"))
    divisor = math.gcd(w, h)
    w, h = w // divisor * 16, h // divisor * 16
    minimum = math.ceil(math.sqrt(655360 / (w * h)))
    maximum = min(3840 // max(w, h), math.isqrt(8294400 // (w * h)))
    desired = round({"1K": 1024, "2K": 2048, "4K": 3840}[resolution] / max(w, h))
    scale = max(minimum, min(maximum, desired))
    return f"{w * scale}x{h * scale}"


def validate_url(url):
    try:
        parts = urlsplit(url.strip())
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError
        if parts.username or parts.password or parts.fragment:
            raise ValueError
        _ = parts.port
    except ValueError:
        raise ValueError("URL 必须是完整的 http(s) 地址，且不能包含用户名、密码或片段。") from None
    return parts


def endpoint_url(url, editing):
    parts = validate_url(url)
    if parts.query:
        raise ValueError("API URL 请不要带查询参数；密钥请填写到 api_key。")
    path = parts.path.rstrip("/")
    for suffix in ("/images/generations", "/images/edits", "/images"):
        if path.endswith(suffix):
            path = path[:-len(suffix)]
            break
    if not path:
        path = "/v1"
    path += "/images/edits" if editing else "/images/generations"
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def encode_image(array):
    array = np.asarray(array)
    if array.ndim != 3 or array.shape[-1] not in (1, 3, 4) or min(array.shape[:2]) < 1:
        raise ValueError("输入图片必须是 [高度, 宽度, 1/3/4 通道]。")
    if not np.isfinite(array).all():
        raise ValueError("输入图片包含 NaN 或无穷值。")
    pixels = np.rint(np.clip(array, 0, 1) * 255).astype(np.uint8)
    if pixels.shape[-1] == 1:
        pixels = pixels[:, :, 0]
    buffer = io.BytesIO()
    Image.fromarray(pixels).save(buffer, format="PNG")
    data = buffer.getvalue()
    if len(data) >= MAX_IMAGE_BYTES:
        raise ValueError("单张参考图必须小于 50 MB，请缩小后再试。")
    return data


def decode_image(data):
    try:
        with Image.open(io.BytesIO(data)) as opened:
            if opened.width * opened.height > 40_000_000:
                raise ImageAPIError("接口返回的图片超过 4000 万像素。")
            rgba = np.asarray(ImageOps.exif_transpose(opened).convert("RGBA"), dtype=np.float32) / 255.0
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise ImageAPIError("接口返回的内容不是可解码的图片。") from None
    return rgba[:, :, :3].copy(), (1.0 - rgba[:, :, 3]).copy()


def redact(value, api_key):
    """Keep metadata useful without emitting megabytes of base64 or the key."""
    if isinstance(value, dict):
        return {
            k: ("[omitted]" if k.lower() in ("b64_json", "api_key", "apikey", "authorization")
                else redact(v, api_key)) for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(item, api_key) for item in value]
    if isinstance(value, str):
        if value.startswith("data:image/"):
            return "[image data omitted]"
        return value.replace(api_key, "[redacted]") if api_key else value
    return value


async def read_limited(response, limit=MAX_RESPONSE_BYTES):
    chunks, length = [], 0
    async for chunk in response.content.iter_chunked(65536):
        length += len(chunk)
        if length > limit:
            raise ImageAPIError("接口响应过大，已停止读取。")
        chunks.append(chunk)
    return b"".join(chunks)


async def request_image(url, api_key, fields, images, api_format, timeout):
    """One billable POST only; never retry a generation automatically."""
    import json

    endpoint = endpoint_url(url, bool(images))
    headers = {"Authorization": f"Bearer {api_key}"}
    if images and api_format == "v1/images (multipart)":
        form = aiohttp.FormData()
        for key, value in fields.items():
            form.add_field(key, str(value))
        for index, data in enumerate(images, 1):
            form.add_field("image[]", data, filename=f"image{index}.png", content_type="image/png")
        body = {"data": form}
    else:
        payload = dict(fields)
        if images:
            payload["images"] = [{"image_url": "data:image/png;base64," + base64.b64encode(data).decode("ascii")} for data in images]
        body = {"json": payload}

    async def exchange():
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout, connect=min(30, timeout))) as session:
            async with session.post(endpoint, headers=headers, allow_redirects=False, **body) as response:
                raw = await read_limited(response)
                try:
                    result = json.loads(raw)
                except (ValueError, UnicodeDecodeError):
                    raise ImageAPIError(f"接口返回 HTTP {response.status}，内容不是 JSON；请检查 API URL。") from None
                if response.status >= 300 or (isinstance(result, dict) and result.get("error")):
                    detail = result.get("error", result) if isinstance(result, dict) else result
                    if isinstance(detail, dict):
                        detail = detail.get("message", detail)
                    message = str(redact(detail, api_key))[:1200]
                    raise ImageAPIError(f"图像接口失败 (HTTP {response.status}): {message}")
            if not isinstance(result, dict) or not isinstance(result.get("data"), list) or not result["data"]:
                raise ImageAPIError("接口未返回 data 图片数组；此节点需要同步 Images API，不支持异步任务 ID 或聊天接口。")
            decoded, urls = [], []
            for entry in result["data"]:
                if not isinstance(entry, dict):
                    raise ImageAPIError("接口 data 数组中的图片格式无效。")
                encoded, image_url = entry.get("b64_json"), entry.get("url")
                if not encoded and isinstance(image_url, str) and image_url.startswith("data:image/"):
                    encoded, image_url = image_url, None
                if encoded:
                    try:
                        if encoded.startswith("data:image/"):
                            encoded = encoded.split(",", 1)[1]
                        data = base64.b64decode(encoded, validate=True)
                    except (ValueError, TypeError, AttributeError, IndexError, binascii.Error):
                        raise ImageAPIError("接口返回的图片 Base64 无效。") from None
                elif image_url:
                    validate_url(image_url)
                    # No API authorization headers are sent to an image/CDN URL.
                    async with session.get(image_url) as download:
                        if download.status != 200:
                            raise ImageAPIError(f"图片下载失败 (HTTP {download.status})。")
                        data = await read_limited(download, MAX_IMAGE_BYTES)
                else:
                    raise ImageAPIError("接口图片缺少 b64_json 或 url。")
                decoded.append(decode_image(data))
                if image_url:
                    urls.append(image_url)
            if len({rgb.shape for rgb, _ in decoded}) != 1:
                raise ImageAPIError("接口返回了不同尺寸的图片，无法组成 ComfyUI 批次。")
            return decoded, redact(result, api_key), "\n".join(urls)

    try:
        # This deadline includes generation AND any subsequent image downloads.
        return await asyncio.wait_for(exchange(), timeout=float(timeout))
    except (asyncio.TimeoutError, aiohttp.ServerTimeoutError):
        raise ImageAPIError(f"请求超过 {timeout} 秒；服务端可能仍在生成，请确认后再重试。") from None
    except aiohttp.ClientError as error:
        raise ImageAPIError(f"无法连接图像服务或下载图片 ({type(error).__name__})，请检查 URL、网络和证书。") from None
