from .nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS
from .excel_images import FFExcelInsertImages

NODE_CLASS_MAPPINGS = {**NODE_CLASS_MAPPINGS, "FF_Excel_Insert_Images": FFExcelInsertImages}
NODE_DISPLAY_NAME_MAPPINGS = {**NODE_DISPLAY_NAME_MAPPINGS, "FF_Excel_Insert_Images": "FF Excel 插入本地图片"}

WEB_DIRECTORY = "./web"
__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
