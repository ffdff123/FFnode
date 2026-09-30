"""Surgical OOXML edits: retain untouched workbook parts and grid geometry."""
import posixpath
import re
import uuid
from zipfile import ZipFile, ZIP_DEFLATED

from lxml import etree as ET

NS = {
    "s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "p": "http://schemas.openxmlformats.org/package/2006/relationships",
    "ct": "http://schemas.openxmlformats.org/package/2006/content-types",
    "xdr": "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "etc": "http://www.wps.cn/officeDocument/2017/etCustomData",
}
CELL_REL = "http://www.wps.cn/officeDocument/2020/cellImage"
CELL_TYPE = "application/vnd.wps-officedocument.cellimage+xml"
DRAWING_TYPE = "application/vnd.openxmlformats-officedocument.drawing+xml"


def tag(prefix, local):
    return f"{{{NS[prefix]}}}{local}"


def child(parent, prefix, local, text=None, **attrs):
    node = ET.SubElement(parent, tag(prefix, local), {k: str(v) for k, v in attrs.items()})
    if text is not None:
        node.text = str(text)
    return node


def column_name(index):
    text = ""
    while index:
        index, remain = divmod(index - 1, 26)
        text = chr(65 + remain) + text
    return text


def coordinate(address):
    match = re.fullmatch(r"\$?([A-Z]+)\$?(\d+)", address.upper())
    if not match:
        raise ValueError(f"不支持的单元格地址：{address}")
    col = 0
    for letter in match[1]:
        col = col * 26 + ord(letter) - 64
    return int(match[2]), col


def rels_path(part):
    parent, name = posixpath.split(part)
    return posixpath.join(parent, "_rels", name + ".rels")


def resolve(part, target):
    result = posixpath.normpath(posixpath.join(posixpath.dirname(part), target)) if not target.startswith("/") else target.lstrip("/")
    if result.startswith("../"):
        raise ValueError("工作簿包含无效的文件关系。")
    return result


def is_true(value):
    return value in ("1", "true", "True")


class WorkbookImages:
    def __init__(self, source, sheet_name=""):
        self.archive = ZipFile(source)
        self.names = set(self.archive.namelist())
        self.changed = {}
        self.media = {}
        self.removed = set()
        try:
            self.workbook = self.read_xml("xl/workbook.xml")
            if self.workbook.tag != tag("s", "workbook"):
                raise ValueError("暂不支持 Strict OOXML，请先另存为普通 .xlsx。")
            self.workbook_rels = self.read_xml("xl/_rels/workbook.xml.rels")
            sheets = self.workbook.findall("s:sheets/s:sheet", NS)
            relation_map = {r.get("Id"): r for r in self.workbook_rels}
            candidates = [s for s in sheets if relation_map[s.get(tag("r", "id"))].get("Type", "").endswith("/worksheet")]
            selected = next((s for s in candidates if s.get("name") == sheet_name), None) if sheet_name else next(iter(candidates), None)
            if selected is None:
                raise ValueError(f"找不到工作表 {sheet_name!r}；可用工作表：{', '.join(s.get('name') for s in candidates)}")
            self.title = selected.get("name")
            self.sheet_part = resolve("xl/workbook.xml", relation_map[selected.get(tag("r", "id"))].get("Target"))
            self.sheet = self.read_xml(self.sheet_part)
            self.sheet_data = self.sheet.find("s:sheetData", NS)
            if self.sheet_data is None:
                raise ValueError("工作表缺少 sheetData。")
            self.rows = {int(r.get("r")): r for r in self.sheet_data}
            self.cells = {c.get("r"): c for r in self.sheet_data for c in r if c.tag == tag("s", "c")}
            self.strings = []
            string_rel = next((r for r in self.workbook_rels if r.get("Type", "").endswith("/sharedStrings")), None)
            if string_rel is not None:
                strings = self.read_xml(resolve("xl/workbook.xml", string_rel.get("Target")))
                self.strings = ["".join(t.text or "" for t in s.findall(".//s:t", NS) if t.getparent().tag != tag("s", "rPh")) for s in strings]
            self.drawing = None
            self.drawing_part = None
            existing_drawing = self.sheet.find("s:drawing", NS)
            if existing_drawing is not None:
                rels = self.read_xml(rels_path(self.sheet_part))
                relation = next(r for r in rels if r.get("Id") == existing_drawing.get(tag("r", "id")))
                self.drawing_part = resolve(self.sheet_part, relation.get("Target"))
                self.drawing = self.read_xml(self.drawing_part)
        except BaseException:
            self.archive.close()
            raise

    def close(self):
        self.archive.close()

    def read_xml(self, name):
        return ET.fromstring(self.archive.read(name), ET.XMLParser(resolve_entities=False, no_network=True))

    def edit_xml(self, name, root=None):
        if name not in self.changed:
            self.changed[name] = self.read_xml(name) if name in self.names else root
        return self.changed[name]

    def rels(self, part):
        return self.edit_xml(rels_path(part), ET.Element(tag("p", "Relationships"), nsmap={None: NS["p"]}))

    def add_rel(self, part, target, rel_type):
        rels = self.rels(part)
        ids = {r.get("Id") for r in rels}
        index = 1
        while f"rId{index}" in ids:
            index += 1
        rid = f"rId{index}"
        child(rels, "p", "Relationship", Id=rid, Type=rel_type, Target=posixpath.relpath(target, posixpath.dirname(part)))
        return rid

    def content_type(self, part, value):
        types = self.edit_xml("[Content_Types].xml")
        part_name = "/" + part
        if not any(n.get("PartName") == part_name for n in types):
            child(types, "ct", "Override", PartName=part_name, ContentType=value)

    def path_value(self, row, col):
        cell = self.cells.get(f"{column_name(col)}{row}")
        if cell is None:
            return ""
        if cell.find("s:f", NS) is not None:
            raise ValueError("图片路径须为纯文本，不执行单元格公式。")
        kind = cell.get("t")
        value = cell.findtext("s:v", default="", namespaces=NS)
        if kind == "s":
            return self.strings[int(value)]
        if kind == "inlineStr":
            return "".join(t.text or "" for t in cell.findall("s:is//s:t", NS))
        if kind == "str":
            return value
        if not value:
            return ""
        raise ValueError("图片路径不是文本。")

    def check_targets(self, first_row, last_row, col):
        for merge in self.sheet.findall("s:mergeCells/s:mergeCell", NS):
            left, _, right = merge.get("ref").partition(":")
            r1, c1 = coordinate(left)
            r2, c2 = coordinate(right or left)
            if c1 <= col <= c2 and r1 <= last_row and r2 >= first_row:
                raise ValueError(f"目标区域包含合并单元格 {merge.get('ref')}，请先取消该区域的合并。")
        # Do not split array or shared-formula groups when replacing cell values.
        for formula in self.sheet.findall("s:sheetData/s:row/s:c/s:f", NS):
            if formula.get("t") not in ("array", "shared") or not formula.get("ref"):
                continue
            left, _, right = formula.get("ref").partition(":")
            r1, c1 = coordinate(left)
            r2, c2 = coordinate(right or left)
            if c1 <= col <= c2 and r1 <= last_row and r2 >= first_row:
                raise ValueError("目标区域与数组或共享公式重叠，请改用空白列。")

    def cell_size(self, row, col):
        fmt = self.sheet.find("s:sheetFormatPr", NS)
        default_height = float(fmt.get("defaultRowHeight", "15")) if fmt is not None else 15.0
        default_width = float(fmt.get("defaultColWidth", "8.43")) if fmt is not None else 8.43
        row_node = self.rows.get(row)
        height = float(row_node.get("ht", default_height)) if row_node is not None else default_height
        row_hidden = is_true(row_node.get("hidden")) if row_node is not None else (fmt is not None and is_true(fmt.get("zeroHeight")))
        width, col_hidden = default_width, False
        for dimension in self.sheet.findall("s:cols/s:col", NS):
            if int(dimension.get("min")) <= col <= int(dimension.get("max")):
                width = float(dimension.get("width", default_width))
                col_hidden = is_true(dimension.get("hidden"))
        if row_hidden or col_hidden or width <= 0 or height <= 0:
            raise ValueError("目标单元格所在行或列已隐藏/尺寸为零。")
        # Standard Calibri/Arial grid approximation; never modify the stored sizes.
        pixels = int(width * 12 + 0.5) if width < 1 else int(width * 7 + 0.5) + 5
        return pixels, height * 96 / 72

    def remove_floating(self, row, col):
        if self.drawing is None:
            return
        for anchor in list(self.drawing):
            marker = anchor.find("xdr:from", NS)
            if marker is None or anchor.find("xdr:pic", NS) is None:
                continue
            if int(marker.findtext("xdr:row", namespaces=NS)) == row - 1 and int(marker.findtext("xdr:col", namespaces=NS)) == col - 1:
                self.drawing.remove(anchor)
                self.changed[self.drawing_part] = self.drawing

    def _cell(self, row, col):
        address = f"{column_name(col)}{row}"
        if address in self.cells:
            return self.cells[address]
        row_node = self.rows.get(row)
        if row_node is None:
            row_node = ET.Element(tag("s", "row"), r=str(row))
            before = next((n for n in self.sheet_data if int(n.get("r")) > row), None)
            if before is None:
                self.sheet_data.append(row_node)
            else:
                before.addprevious(row_node)
            self.rows[row] = row_node
        cell = ET.Element(tag("s", "c"), r=address)
        before = next((n for n in row_node if n.tag == tag("s", "c") and coordinate(n.get("r"))[1] > col), None)
        if before is None:
            row_node.append(cell)
        else:
            before.addprevious(cell)
        self.cells[address] = cell
        return cell

    def write_cell(self, row, col, formula=None):
        cell = self._cell(row, col)
        for node in list(cell):
            if node.tag in (tag("s", "f"), tag("s", "v"), tag("s", "is")):
                cell.remove(node)
        for attr in ("t", "cm", "vm"):
            cell.attrib.pop(attr, None)
        if formula is not None:
            cell.set("t", "str")
            f = ET.Element(tag("s", "f"))
            f.text = "_xlfn." + formula
            v = ET.Element(tag("s", "v"))
            v.text = "=" + formula
            cell.insert(0, f)
            cell.insert(1, v)
        self.changed[self.sheet_part] = self.sheet
        dimension = self.sheet.find("s:dimension", NS)
        if dimension is not None:
            left, _, right = dimension.get("ref").partition(":")
            r1, c1 = coordinate(left)
            r2, c2 = coordinate(right or left)
            dimension.set("ref", f"{column_name(min(c1, col))}{min(r1, row)}:{column_name(max(c2, col))}{max(r2, row)}")

    def clear_embedded_formula(self, row, col):
        cell = self.cells.get(f"{column_name(col)}{row}")
        if cell is not None:
            formula = cell.findtext("s:f", default="", namespaces=NS)
            if re.match(r"^(?:_xlfn\.)?DISPIMG\s*\(", formula, re.I):
                self.write_cell(row, col)

    def _picture(self, parent, rid, name, width, height, number):
        picture = child(parent, "xdr", "pic")
        nv = child(picture, "xdr", "nvPicPr")
        child(nv, "xdr", "cNvPr", id=number, name=name, descr="FF Excel image")
        child(child(nv, "xdr", "cNvPicPr"), "a", "picLocks", noChangeAspect=1)
        fill = child(picture, "xdr", "blipFill")
        blip = child(fill, "a", "blip")
        blip.set(tag("r", "embed"), rid)
        child(child(fill, "a", "stretch"), "a", "fillRect")
        shape = child(picture, "xdr", "spPr")
        transform = child(shape, "a", "xfrm")
        child(transform, "a", "off", x=0, y=0)
        child(transform, "a", "ext", cx=round(width * 9525), cy=round(height * 9525))
        child(child(shape, "a", "prstGeom", prst="rect"), "a", "avLst")

    def insert(self, row, col, png, width, height, mode, padding):
        unique = uuid.uuid4().hex.upper()
        media_path = f"xl/media/ff_{unique}.png"
        self.media[media_path] = png
        self.content_type(media_path, "image/png")
        self.remove_floating(row, col)
        if mode == "embedded":
            relation = next((r for r in self.workbook_rels if r.get("Type", "").rstrip("/").lower().endswith(("/cellimage", "/cellimages"))), None)
            part = resolve("xl/workbook.xml", relation.get("Target")) if relation is not None else "xl/cellimages.xml"
            if relation is None:
                self.add_rel("xl/workbook.xml", part, CELL_REL)
                self.workbook_rels = self.changed["xl/_rels/workbook.xml.rels"]
            root = self.edit_xml(part, ET.Element(tag("etc", "cellImages"), nsmap={k: NS[k] for k in ("etc", "xdr", "a", "r")}))
            self.content_type(part, CELL_TYPE)
            rid = self.add_rel(part, media_path, NS["r"] + "/image")
            identifier = "ID_" + unique
            number = max([int(n.get("id", "0")) for n in root.findall(".//xdr:cNvPr", NS)] + [0]) + 1
            self._picture(child(root, "etc", "cellImage"), rid, identifier, width, height, number)
            self.write_cell(row, col, f'DISPIMG("{identifier}",1)')
        else:
            self.clear_embedded_formula(row, col)
            if self.drawing is None:
                self.drawing_part = f"xl/drawings/ff_{unique}.xml"
                self.drawing = ET.Element(tag("xdr", "wsDr"), nsmap={k: NS[k] for k in ("xdr", "a", "r")})
                rid = self.add_rel(self.sheet_part, self.drawing_part, NS["r"] + "/drawing")
                drawing_element = ET.Element(tag("s", "drawing"))
                drawing_element.set(tag("r", "id"), rid)
                # Drawing must precede these later worksheet elements in OOXML.
                following = {"legacyDrawing", "legacyDrawingHF", "picture", "oleObjects", "controls", "webPublishItems", "tableParts", "extLst"}
                before = next((n for n in self.sheet if ET.QName(n).localname in following), None)
                if before is None:
                    self.sheet.append(drawing_element)
                else:
                    before.addprevious(drawing_element)
                self.changed[self.sheet_part] = self.sheet
                self.content_type(self.drawing_part, DRAWING_TYPE)
            self.changed[self.drawing_part] = self.drawing
            rid = self.add_rel(self.drawing_part, media_path, NS["r"] + "/image")
            box_w, box_h = self.cell_size(row, col)
            scale = min((box_w - 2 * padding) / width, (box_h - 2 * padding) / height)
            w, h = width * scale, height * scale
            anchor = child(self.drawing, "xdr", "oneCellAnchor")
            marker = child(anchor, "xdr", "from")
            for local, value in (("col", col - 1), ("colOff", round((box_w - w) / 2 * 9525)),
                                 ("row", row - 1), ("rowOff", round((box_h - h) / 2 * 9525))):
                child(marker, "xdr", local, text=value)
            child(anchor, "xdr", "ext", cx=round(w * 9525), cy=round(h * 9525))
            number = max([int(n.get("id", "0")) for n in self.drawing.findall(".//xdr:cNvPr", NS)] + [0]) + 1
            self._picture(anchor, rid, "FF_" + unique, w, h, number)
            child(anchor, "xdr", "clientData")

    def save(self, destination):
        # Modified formula cells invalidate the optional calculation-chain cache.
        if self.sheet_part in self.changed:
            rels = self.rels("xl/workbook.xml")
            for relation in list(rels):
                if relation.get("Type", "").endswith("/calcChain"):
                    part = resolve("xl/workbook.xml", relation.get("Target"))
                    self.removed.add(part)
                    rels.remove(relation)
                    types = self.edit_xml("[Content_Types].xml")
                    for entry in list(types):
                        if entry.get("PartName") == "/" + part:
                            types.remove(entry)
        with ZipFile(destination, "w", ZIP_DEFLATED) as output:
            for info in self.archive.infolist():
                if info.filename not in self.changed and info.filename not in self.removed:
                    output.writestr(info, self.archive.read(info.filename))
            for name, root in self.changed.items():
                output.writestr(name, ET.tostring(root, encoding="UTF-8", xml_declaration=True, standalone=True))
            for name, content in self.media.items():
                output.writestr(name, content)
