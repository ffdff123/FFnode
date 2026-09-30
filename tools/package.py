"""Build the drag-and-drop example and a clean installable node ZIP."""
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]


def build():
    workflow = {
        "last_node_id": 2, "last_link_id": 1,
        "nodes": [
            {
                "id": 1, "type": "FF_GPT_Image_25", "pos": [80, 80], "size": [560, 990],
                "flags": {}, "order": 0, "mode": 0,
                "inputs": [{"name": f"image{i}", "type": "IMAGE", "link": None} for i in range(1, 17)],
                "outputs": [
                    {"name": "image", "type": "IMAGE", "links": [1], "slot_index": 0},
                    {"name": "response", "type": "STRING", "links": None},
                    {"name": "image_url", "type": "STRING", "links": None},
                    {"name": "mask", "type": "MASK", "links": None},
                ],
                "properties": {"Node name for S&R": "FF_GPT_Image_25"},
                "widgets_values": ["auto", "https://api.openai.com/v1", "v1/images (multipart)",
                                   "gpt-image-2.5-sunburst", "", "", "一只橘猫坐在窗边，柔和的自然光，写实摄影。",
                                   "1:1", "2K", "medium", 0, "randomize", 1800, False, False],
            },
            {
                "id": 2, "type": "SaveImage", "pos": [750, 80], "size": [420, 400],
                "flags": {}, "order": 1, "mode": 0,
                "inputs": [{"name": "images", "type": "IMAGE", "link": 1}], "outputs": [],
                "properties": {"Node name for S&R": "SaveImage"}, "widgets_values": ["FF_GPT_Image"],
            },
        ],
        "links": [[1, 1, 0, 2, 0, "IMAGE"]], "groups": [], "config": {}, "extra": {}, "version": 0.4,
    }
    examples = ROOT / "examples"
    examples.mkdir(exist_ok=True)
    (examples / "text_to_image.json").write_text(json.dumps(workflow, ensure_ascii=False, indent=2), encoding="utf-8")
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    target = dist / "comfyui_ffnode.zip"
    paths = [ROOT / name for name in ("__init__.py", "nodes.py", "image_api.py", "requirements.txt", "README.md")]
    for folder in ("web", "examples", "tests", "tools"):
        paths.extend(p for p in (ROOT / folder).rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    with ZipFile(target, "w", ZIP_DEFLATED) as archive:
        for path in sorted(paths):
            archive.write(path, "comfyui_ffnode/" + path.relative_to(ROOT).as_posix())
    with ZipFile(target) as archive:
        assert archive.testzip() is None
        assert "comfyui_ffnode/__init__.py" in archive.namelist()
    print(f"Built {target} ({target.stat().st_size:,} bytes)")


if __name__ == "__main__":
    build()
