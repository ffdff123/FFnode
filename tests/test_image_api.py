"""Offline tests with a real local HTTP server; no API key or paid calls."""
import asyncio
import base64
import importlib.util
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image
from aiohttp import web

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ff_test_package", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
from ff_test_package.image_api import (RATIOS, RESOLUTIONS, ImageAPIError, decode_image,
    encode_image, endpoint_url, image_size, request_image)
from ff_test_package.nodes import FFGPTImage25, interruptible


def sample_png():
    buffer = io.BytesIO()
    Image.new("RGBA", (4, 3), (12, 34, 56, 128)).save(buffer, format="PNG")
    return buffer.getvalue()


class GeometryTests(unittest.TestCase):
    def test_all_sizes_respect_api_constraints_and_exact_ratio(self):
        for ratio in RATIOS:
            sizes = []
            for tier in RESOLUTIONS:
                with self.subTest(ratio=ratio, tier=tier):
                    w, h = map(int, image_size(ratio, tier).split("x"))
                    rw, rh = map(int, ratio.split(":"))
                    self.assertEqual(w * rh, h * rw)
                    self.assertEqual(w % 16, 0)
                    self.assertEqual(h % 16, 0)
                    self.assertLessEqual(max(w, h), 3840)
                    self.assertTrue(655360 <= w * h <= 8294400)
                    sizes.append(w * h)
            self.assertEqual(sizes, sorted(set(sizes)))

    def test_expected_4k_sizes(self):
        self.assertEqual(image_size("16:9", "4K"), "3840x2160")
        self.assertEqual(image_size("9:16", "4K"), "2160x3840")
        self.assertEqual(image_size("1:1", "4K"), "2880x2880")

    def test_url_variants(self):
        for suffix in ("", "/", "/v1", "/v1/", "/v1/images", "/v1/images/generations", "/v1/images/edits"):
            for editing in (False, True):
                expected = "edits" if editing else "generations"
                self.assertEqual(endpoint_url("https://example.com" + suffix, editing), f"https://example.com/v1/images/{expected}")
        self.assertEqual(endpoint_url("https://example.com/proxy/v1", True), "https://example.com/proxy/v1/images/edits")

    def test_invalid_urls(self):
        for url in ("example.com", "file:///secret", "https://user:pass@example.com", "https://example.com?api_key=secret"):
            with self.assertRaises(ValueError):
                endpoint_url(url, False)

    def test_alpha_and_color_round_trip(self):
        original = np.asarray(Image.open(io.BytesIO(sample_png())), dtype=np.float32) / 255
        rgb, mask = decode_image(encode_image(original))
        np.testing.assert_allclose(rgb, original[:, :, :3])
        np.testing.assert_allclose(mask, 1 - original[:, :, 3])

    def test_invalid_image_rejected(self):
        with self.assertRaises(ImageAPIError):
            decode_image(b"not an image")
        with self.assertRaises(ValueError):
            encode_image(np.zeros((1, 2, 3, 3)))
        with self.assertRaises(ValueError):
            encode_image(np.full((2, 3, 3), np.nan))

    def test_schema_and_registration(self):
        node = package.NODE_CLASS_MAPPINGS["FF_GPT_Image_25"]
        self.assertEqual(list(node.INPUT_TYPES()["optional"]), [f"image{i}" for i in range(1, 17)])
        self.assertEqual(node.RETURN_TYPES, ("IMAGE", "STRING", "STRING", "MASK"))
        self.assertTrue(node.INPUT_TYPES()["required"]["seed"][1]["control_after_generate"])


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.received = []
        self.download_auth = []
        self.behavior = "base64"
        app = web.Application()
        app.router.add_post("/v1/images/{action}", self.handle)
        app.router.add_get("/image.png", self.download)
        app.router.add_get("/delay.png", self.delay_download)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"
        self.fields = {"model": "gpt-image-2.5-sunburst", "prompt": "测试", "size": "1024x1024", "n": 1, "quality": "medium", "output_format": "png"}

    async def asyncTearDown(self):
        await self.runner.cleanup()

    async def handle(self, request):
        files = []
        if request.content_type == "multipart/form-data":
            reader = await request.multipart()
            fields = {}
            while True:
                part = await reader.next()
                if part is None:
                    break
                if part.filename:
                    files.append((part.name, part.filename, bytes(await part.read())))
                else:
                    fields[part.name] = await part.text()
        else:
            fields = await request.json()
        self.received.append((request.match_info["action"], fields, files, request.headers.get("Authorization")))
        if self.behavior == "slow":
            await asyncio.sleep(0.2)
        if self.behavior == "error":
            return web.json_response({"error": {"message": "invalid secret-test-key"}}, status=401)
        if self.behavior == "html":
            return web.Response(text="<html>wrong endpoint</html>")
        if self.behavior == "empty":
            return web.json_response({"data": []})
        if self.behavior == "invalid-base64":
            return web.json_response({"data": [{"b64_json": "!bad!"}]})
        if self.behavior == "redirect":
            raise web.HTTPTemporaryRedirect(location=self.url + "/v1/images/generations")
        if self.behavior in ("url", "slow-download"):
            name = "delay.png" if self.behavior == "slow-download" else "image.png"
            return web.json_response({"data": [{"url": self.url + "/" + name}]})
        encoded = base64.b64encode(sample_png()).decode()
        entry = {"b64_json": encoded}
        if self.behavior == "data-url":
            entry = {"url": "data:image/png;base64," + encoded}
        return web.json_response({"data": [entry], "usage": {"total_tokens": 123}, "echo": "secret-test-key"})

    async def download(self, request):
        self.download_auth.append(request.headers.get("Authorization"))
        return web.Response(body=sample_png(), content_type="image/png")

    async def delay_download(self, request):
        await asyncio.sleep(0.2)
        return await self.download(request)

    async def call(self, images=None, api_format="v1/images (multipart)", timeout=3):
        return await request_image(self.url, "secret-test-key", self.fields, images or [], api_format, timeout)

    async def test_generation_json_and_metadata_redaction(self):
        images, result, url = await self.call()
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0][0].shape, (3, 4, 3))
        self.assertEqual(url, "")
        self.assertEqual(self.received[0][0], "generations")
        self.assertEqual(self.received[0][1], self.fields)
        self.assertEqual(self.received[0][3], "Bearer secret-test-key")
        self.assertEqual(result["data"][0]["b64_json"], "[omitted]")
        self.assertNotIn("secret-test-key", json.dumps(result))

    async def test_sixteen_multipart_references_in_order(self):
        pngs = [encode_image(np.full((2, 2, 3), i / 16)) for i in range(16)]
        await self.call(pngs)
        action, fields, files, _ = self.received[0]
        self.assertEqual(action, "edits")
        self.assertEqual(fields["prompt"], "测试")
        self.assertEqual(len(files), 16)
        self.assertEqual([f[0] for f in files], ["image[]"] * 16)
        self.assertEqual([f[2] for f in files], pngs)

    async def test_json_edit_references(self):
        await self.call([sample_png()], "v1/images (JSON)")
        action, fields, _, _ = self.received[0]
        self.assertEqual(action, "edits")
        data = fields["images"][0]["image_url"].split(",")[1]
        self.assertEqual(base64.b64decode(data), sample_png())

    async def test_url_download_does_not_receive_api_key(self):
        self.behavior = "url"
        images, _, url = await self.call()
        self.assertEqual(len(images), 1)
        self.assertEqual(url, self.url + "/image.png")
        self.assertEqual(self.download_auth, [None])

    async def test_data_url_response(self):
        self.behavior = "data-url"
        images, result, url = await self.call()
        self.assertEqual(len(images), 1)
        self.assertEqual(url, "")
        self.assertEqual(result["data"][0]["url"], "[image data omitted]")

    async def test_api_error_is_redacted_and_not_retried(self):
        self.behavior = "error"
        with self.assertRaises(ImageAPIError) as caught:
            await self.call()
        self.assertIn("401", str(caught.exception))
        self.assertNotIn("secret-test-key", str(caught.exception))
        self.assertEqual(len(self.received), 1)

    async def test_invalid_responses(self):
        for behavior in ("html", "empty", "invalid-base64", "redirect"):
            self.behavior = behavior
            with self.subTest(behavior=behavior), self.assertRaises(ImageAPIError):
                await self.call()
        self.assertEqual(len(self.received), 4)

    async def test_generation_timeout(self):
        self.behavior = "slow"
        with self.assertRaisesRegex(ImageAPIError, "超过"):
            await self.call(timeout=0.03)
        self.assertEqual(len(self.received), 1)

    async def test_download_shares_total_deadline(self):
        self.behavior = "slow-download"
        with self.assertRaisesRegex(ImageAPIError, "超过"):
            await self.call(timeout=0.03)

    async def test_cancellation_closes_pending_operation(self):
        closed = asyncio.Event()
        async def pending():
            try:
                await asyncio.sleep(10)
            finally:
                closed.set()
        task = asyncio.create_task(interruptible(pending()))
        await asyncio.sleep(0.01)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(closed.is_set())


class NodeValidationTests(unittest.IsolatedAsyncioTestCase):
    def args(self, **changes):
        args = dict(task_type="auto", url="https://example.com/v1", api_format="v1/images (multipart)",
                    model="gpt-image-2.5-sunburst", custom_model="", api_key="test-key", prompt="test",
                    ratio="1:1", resolution="2K", quality="medium", seed=0, timeout=1800,
                    transparent_background=False, send_seed=False)
        args.update(changes)
        return args

    async def test_edit_requires_image(self):
        with self.assertRaisesRegex(ValueError, "至少"):
            await FFGPTImage25().generate(**self.args(task_type="edit"))

    async def test_generation_never_silently_discards_inputs(self):
        with self.assertRaisesRegex(ValueError, "generate"):
            await FFGPTImage25().generate(**self.args(task_type="generate", image16=np.zeros((1, 2, 2, 3))))

    async def test_batch_limit_counts_every_frame(self):
        with self.assertRaisesRegex(ValueError, "17"):
            await FFGPTImage25().generate(**self.args(image1=np.zeros((16, 2, 2, 3)), image16=np.zeros((1, 2, 2, 3))))

    async def test_missing_key(self):
        with patch.dict("os.environ", {}, clear=True), self.assertRaisesRegex(ValueError, "api_key"):
            await FFGPTImage25().generate(**self.args(api_key=""))

    async def test_blank_custom_model(self):
        with self.assertRaisesRegex(ValueError, "custom_model"):
            await FFGPTImage25().generate(**self.args(model="custom"))


if __name__ == "__main__":
    unittest.main()
