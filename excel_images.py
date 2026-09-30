"""Local Excel image insertion. Dependencies are loaded only when executed."""
import io
import json
import os
from pathlib import Path
import tempfile


def _path(value, base=None):
    value = str(value).strip().strip('"').strip("'")
    result = Path(os.path.expandvars(value)).expanduser()
    if base is not None and not result.is_absolute():
        result = base / result
    return result.resolve()


def _interrupt():
    try:
        from comfy.model_management import throw_exception_if_processing_interrupted
    except ImportError:
        return
    throw_exception_if_processing_interrupted()


class FFExcelInsertImages:
    CATEGORY = "FF Nodes/Excel"
    FUNCTION = "insert_images"
    RETURN_TYPES = ("STRING", "INT", "STRING")
    RETURN_NAMES = ("excel_path", "inserted_count", "report")
    OUTPUT_NODE = True
    DESCRIPTION = "按表格现有尺寸插入本地图片；可选 WPS 单元格嵌入或浮动图片，不修改行高列宽。另存新文件。"
    SEARCH_ALIASES = ["Excel", "WPS", "表格", "插入图片"]

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "excel_path": ("STRING", {"default": "", "tooltip": "ComfyUI 所在电脑上的 .xlsx 文件完整路径。"}),
            "image_path_column": ("INT", {"default": 2, "min": 1, "max": 16384, "tooltip": "图片路径列：1=A，2=B，3=C。"}),
            "start_row": ("INT", {"default": 2, "min": 1, "max": 1048576}),
            "end_row": ("INT", {"default": 8, "min": 1, "max": 1048576, "tooltip": "包含结束行；2 到 8 共处理 7 行。"}),
            "target_column": ("INT", {"default": 3, "min": 1, "max": 16384}),
            "sheet_name": ("STRING", {"default": "", "tooltip": "留空使用第一张工作表；否则精确填写工作表名称。"}),
            "target_start_row": ("INT", {"default": 0, "min": 0, "max": 1048576, "tooltip": "0=与来源同一行；其他值用于从指定目标行开始插图。"}),
            "image_mode": (["嵌入单元格（WPS）", "浮动图片"], {"default": "嵌入单元格（WPS）", "tooltip": "嵌入：写入 WPS DISPIMG，替换目标格内容，由 WPS 自动适配；浮动：图片按原单元格大小等比居中。"}),
            "padding": ("INT", {"default": 2, "min": 0, "max": 100, "tooltip": "浮动图片与单元格边缘的间距，单位像素。嵌入模式由 WPS 控制布局，此值不生效。"}),
            "on_error": (["skip", "stop"], {"default": "skip", "tooltip": "skip：跳过空路径、缺失/损坏图片，保持行号；stop：报错且不生成文件。"}),
            "output_path": ("STRING", {"default": "", "tooltip": "留空在原表旁生成 *_with_images.xlsx；同名时自动编号。填写路径也不会覆盖已有文件。"}),
        }}

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        # Excel and image files can change without their path widgets changing.
        return float("nan")

    def insert_images(self, excel_path, image_path_column, start_row, end_row,
                      target_column, sheet_name="", target_start_row=0,
                      image_mode="嵌入单元格（WPS）", padding=2, on_error="skip", output_path="",
                      **legacy_options):
        try:
            from .xlsx_images import WorkbookImages, column_name
            from PIL import Image, ImageOps
        except ImportError as exc:
            raise RuntimeError("请使用运行 ComfyUI 的 Python 安装本节点的 requirements.txt（需要 lxml、Pillow）。") from exc

        if not str(excel_path).strip():
            raise ValueError("请填写 Excel 文件路径。")
        source = _path(excel_path)
        if source.suffix.lower() != ".xlsx":
            raise ValueError("仅支持 .xlsx；请先在 WPS/Excel 中将 .xls、.et 或 .xlsm 另存为 .xlsx。")
        if not source.is_file():
            raise FileNotFoundError(f"Excel 文件不存在：{source}")
        for name, value, maximum in (("图片路径列", image_path_column, 16384),
                                     ("目标列", target_column, 16384),
                                     ("开始行", start_row, 1048576), ("结束行", end_row, 1048576)):
            if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= maximum:
                raise ValueError(f"{name}必须是 1 到 {maximum} 的整数。")
        if end_row < start_row:
            raise ValueError("结束行不能小于开始行。")
        if not isinstance(target_start_row, int) or not 0 <= target_start_row <= 1048576:
            raise ValueError("目标起始行必须是 0 到 1048576 的整数。")
        first_target = target_start_row or start_row
        last_target = first_target + end_row - start_row
        if last_target > 1048576:
            raise ValueError("目标结束行超出 Excel 行数限制。")
        if on_error not in ("skip", "stop"):
            raise ValueError("on_error 必须是 skip 或 stop。")
        if image_mode not in ("嵌入单元格（WPS）", "浮动图片"):
            raise ValueError("image_mode 必须为嵌入单元格（WPS）或浮动图片。")
        if not isinstance(padding, int) or not 0 <= padding <= 100:
            raise ValueError("图片间距必须是 0 到 100 的整数。")
        mode = "embedded" if image_mode == "嵌入单元格（WPS）" else "floating"
        requested = _path(output_path, source.parent) if output_path.strip() else source.with_name(source.stem + "_with_images.xlsx")
        if requested == source:
            raise ValueError("输出路径不能与原表相同，请指定一个新文件名。")
        if requested.suffix.lower() != ".xlsx":
            raise ValueError("输出文件扩展名必须为 .xlsx。")

        book = WorkbookImages(source, sheet_name)
        temporary = None
        try:
            book.check_targets(first_target, last_target, target_column)
            # Snapshot input strings before replacing target cells (overlapping ranges).
            paths = []
            for row in range(start_row, end_row + 1):
                _interrupt()
                try:
                    paths.append(book.path_value(row, image_path_column))
                except ValueError as exc:
                    paths.append(exc)
            entries = []
            inserted = 0
            for row in range(start_row, end_row + 1):
                _interrupt()
                source_address = f"{column_name(image_path_column)}{row}"
                target_row = first_target + row - start_row
                target_address = f"{column_name(target_column)}{target_row}"
                entry = {"source": source_address, "target": target_address}
                try:
                    value = paths[row - start_row]
                    if isinstance(value, ValueError):
                        raise value
                    if not isinstance(value, str) or not value.strip():
                        raise ValueError("图片路径为空或不是文本。")
                    if "://" in value:
                        raise ValueError("仅接受本地文件路径，不接受网址或 file:// 链接。")
                    picture_path = _path(value, source.parent)
                    if not picture_path.is_file():
                        raise FileNotFoundError(f"找不到图片：{picture_path}")
                    box_width, box_height = book.cell_size(target_row, target_column)
                    if mode == "floating" and min(box_width, box_height) <= 2 * padding:
                        raise ValueError("单元格尺寸小于图片边距，请减小 padding；节点不会改变单元格大小。")
                    with Image.open(picture_path) as original:
                        frame = ImageOps.exif_transpose(original).convert("RGBA")
                        width, height = frame.size
                        with io.BytesIO() as stream:
                            frame.save(stream, format="PNG")
                            png = stream.getvalue()
                        frame.close()
                except (OSError, ValueError, Image.DecompressionBombError) as exc:
                    if on_error == "stop":
                        raise ValueError(f"{source_address} → {target_address}：{exc}") from exc
                    entry.update(status="skipped", reason=str(exc))
                    entries.append(entry)
                    continue

                book.insert(target_row, target_column, png, width, height, mode, padding)
                entry.update(status="inserted", image_path=str(picture_path))
                entries.append(entry)
                inserted += 1

            if not inserted:
                raise ValueError("没有可插入的图片，未生成文件。\n" + json.dumps(entries, ensure_ascii=False, indent=2))
            requested.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix=".ff_excel_", suffix=".xlsx", dir=requested.parent)
            os.close(fd)
            _interrupt()
            book.save(temporary)
            _interrupt()
            # Exclusive creation avoids overwriting another run or a user file.
            output = requested
            index = 1
            while True:
                try:
                    dest = output.open("xb")
                    break
                except FileExistsError:
                    output = requested.with_name(f"{requested.stem}_{index:03d}{requested.suffix}")
                    index += 1
            try:
                with dest, open(temporary, "rb") as contents:
                    import shutil
                    shutil.copyfileobj(contents, dest)
            except BaseException:
                output.unlink(missing_ok=True)
                raise
            report = json.dumps({"output_path": str(output), "sheet": book.title, "image_mode": image_mode,
                                 "inserted": inserted, "skipped": len(entries) - inserted,
                                 "rows": entries}, ensure_ascii=False, indent=2)
            message = f"{image_mode}：已插入 {inserted} 张，跳过 {len(entries) - inserted} 行；保留原行高列宽。\n保存至：{output}"
            print("[FF Excel] " + message)
            return {"ui": {"text": [message]}, "result": (str(output), inserted, report)}
        finally:
            book.close()
            if temporary:
                Path(temporary).unlink(missing_ok=True)
