# FF GPT Image 2.5 Generator

ComfyUI 自定义节点，支持 GPT Image 2.5 文生图和最多 16 张参考图编辑。布局参考提供的截图：上方 16 个图像端口，下方配置控件，右侧输出。

## 安装

1. 将安装包解压到 `ComfyUI/custom_nodes/`，目录结构应为 `ComfyUI/custom_nodes/comfyui_ffnode/__init__.py`，不要再多套一层文件夹。
2. 使用 **运行 ComfyUI 的 Python** 安装本目录的 `requirements.txt`。

Windows 官方便携版，在 `ComfyUI_windows_portable` 目录打开终端：

```powershell
.\python_embeded\python.exe -m pip install -r .\ComfyUI\custom_nodes\comfyui_ffnode\requirements.txt
```

普通 Python / 虚拟环境安装，在 ComfyUI 对应环境中执行：

```shell
python -m pip install -r custom_nodes/comfyui_ffnode/requirements.txt
```

3. 重启 ComfyUI，并刷新浏览器页面。
4. 双击画布搜索 **FF GPT Image 2.5 Generator**，或在 `FF Nodes/GPT Image` 分类添加。
5. 也可以拖入 `examples/text_to_image.json`，填写 URL、API Key、提示词后运行。示例已连接 Save Image。

本节点使用 ComfyUI 自带的 PyTorch；依赖列表不会重新安装或替换你的 CUDA / PyTorch。

## 使用

- **文生图：** `task_type=auto`，不连接图片，`image` 输出连接 Save Image 或 Preview Image。
- **参考图编辑：** 将 Load Image 等节点的 IMAGE 连接到 `image1`～`image16` 任意端口，`task_type=auto` 自动切换至编辑接口。图片按端口编号、再按批次顺序发送；无需连续连接。
- **多图批次：** 一个端口可以传入多个图片，所有端口合计最多 16 张。超过上限会报错，不会丢弃图片。
- **透明底：** 打开 `transparent_background`，将 `image` 和 `mask` 接入 ComfyUI 的 `JoinImageWithAlpha`（合并图像与 Alpha）后连接 Save Image，以 PNG 保存。

## 参数

| 参数 | 用途 |
| --- | --- |
| `task_type` | `auto` 自动选择；`generate` 仅文生图；`edit` 至少需要一张参考图。 |
| `url` | 默认 `https://api.openai.com/v1`；可以替换为支持 Images API 的服务商地址。接受根地址、`/v1`、`/v1/images`、完整 `/images/generations` 或 `/images/edits`，自动选择生成/编辑端点。带自定义路径时保留该路径前缀。 |
| `api_format` | 默认 `v1/images (multipart)`：生成用 JSON，编辑上传 PNG 文件；另一选项对编辑也使用 JSON `images[].image_url`。根据服务商支持情况选择。 |
| `model` | `gpt-image-2.5-sunburst` / `gpt-image-2.5-flare` / `custom`。 |
| `custom_model` | 选择 `custom` 后生效，填写服务商要求的精确模型 ID。其他模式忽略此字段。 |
| `api_key` | 填写服务商密钥；留空时依次读取 `FF_GPT_IMAGE_API_KEY`、`OPENAI_API_KEY`。 |
| `prompt` | 多行提示词，原样发送，不额外改写。 |
| `ratio` | 1:1、16:9、9:16、4:3、3:4、3:2、2:3、21:9、9:21、3:1、1:3。 |
| `resolution` | 1K / 2K / 4K；换算规则见下。 |
| `quality` | auto / low / medium / high / xhigh / max，默认 medium。 |
| `seed` | 改变此值会使 ComfyUI 重新执行节点。默认不发送给 API。 |
| `control_after_generate` | ComfyUI 自动生成的种子控件，默认 randomize；设置 fixed 可复用未变化节点的缓存。 |
| `timeout` | 默认 1800 秒，可设置 1～7200 秒，包含网络请求、生成和图片下载。 |
| `transparent_background` | 透明底开关；始终请求 PNG 格式。 |
| `send_seed` | 仅供明确支持 seed 参数的第三方服务。官方 Images API 保持关闭；固定 seed 不能保证官方图像可复现。 |

API Key 直接填写到控件时，会随 ComfyUI 工作流保存，并可能进入保存图片的工作流元数据。分享前清空；使用服务器环境变量则不把真实密钥写入控件。例如在启动 ComfyUI 的同一个 PowerShell 窗口设置：

```powershell
$env:FF_GPT_IMAGE_API_KEY = "你的 API Key"
# 然后在此窗口启动 ComfyUI
```

## 分辨率规则

这里的 1K / 2K / 4K 是请求尺寸档位。目标最长边分别为 1024 / 2048 / 3840；按所选比例，选择满足 API 约束的最近尺寸：比例保持精确、宽高为 16 的倍数、最长边不超过 3840、总像素 655,360～8,294,400。不在本地放大图片。

| 比例 | 1K | 2K | 4K |
| --- | --- | --- | --- |
| 1:1 | 1024×1024 | 2048×2048 | 2880×2880 |
| 16:9 | 1280×720 | 2048×1152 | 3840×2160 |
| 9:16 | 720×1280 | 1152×2048 | 2160×3840 |
| 4:3 | 1024×768 | 2048×1536 | 3328×2496 |
| 3:2 | 1008×672 | 2064×1376 | 3504×2336 |
| 21:9 | 1344×576 | 2016×864 | 3808×1632 |

超出 2560×1440 的尺寸在官方接口中属于实验性支持。第三方服务可能有自己的尺寸或模型限制；以其返回结果为准。实际请求尺寸可查看 `response` 中的 `ff_request.size`。

## 输出

| 输出 | 说明 |
| --- | --- |
| `image` | 标准 ComfyUI IMAGE，RGB，形状 `[B,H,W,3]`，float32，0～1。 |
| `response` | JSON 文本，保留 usage、revised_prompt 等返回信息，并添加实际请求尺寸和参考图数量。Base64 图片字段省略，密钥脱敏。 |
| `image_url` | 服务商返回的图片 URL。官方通常返回 Base64，此输出为空；不伪造外部 URL。 |
| `mask` | ComfyUI MASK，形状 `[B,H,W]`，值为 `1-alpha`：0 为不透明，1 为透明。 |

每次请求生成一张图片。兼容返回 Base64、图片 URL 或图像 data URL 的同步 Images API；暂不支持 Chat Completions、Responses API 或返回任务 ID 的异步轮询接口。

## 超时与错误

- 401/403：核对密钥、服务商地址和模型权限。
- 400：检查服务商是否支持当前模型、尺寸、质量、透明底及参考图格式。
- 404 或返回 HTML：检查 URL 是否为 API 地址，而不是服务商网页地址。
- 超时：可提高 timeout。客户端超时或取消后，服务端可能仍在处理。
- 不自动重试生成请求，避免网络异常后重复生成、重复计费。
- 支持 ComfyUI 中断检查，约每 0.25 秒检查一次网络等待；图片转换等本地同步步骤不保证即时中断。
- 默认验证 HTTPS 证书。图片 URL 的下载请求不携带 API Key。

## 验证

```shell
python -m unittest discover -s tests -v
```

测试使用本地 HTTP 服务，不消耗 API 额度。覆盖尺寸约束、16 图 multipart 上传、JSON 编辑、透明通道、响应解析、密钥脱敏、下载授权隔离、超时、取消及输入校验。

开发环境未安装 ComfyUI / PyTorch，也未提供真实服务商密钥：已验证接口与图像处理逻辑；实际 ComfyUI 界面和真实模型出图尚未联调。

## 参考

- [OpenAI 图像生成文档](https://developers.openai.com/api/docs/guides/image-generation)
- [OpenAI 图像编辑接口](https://developers.openai.com/api/reference/resources/images/methods/edit)
- [ComfyUI 数据类型](https://docs.comfy.org/custom-nodes/backend/datatypes)
- [ComfyUI 前端扩展](https://docs.comfy.org/custom-nodes/js/javascript_overview)
