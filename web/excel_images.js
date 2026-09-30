import { app } from "../../scripts/app.js";

app.registerExtension({
    name: "FFNodes.ExcelInsertImages",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== "FF_Excel_Insert_Images") return;
        const onConfigure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function (info) {
            onConfigure?.apply(this, arguments);
            // Old workflows had row_height/column_width at these positions.
            if (typeof info.widgets_values?.[7] === "number") {
                this.widgets.find(w => w.name === "image_mode").value = "浮动图片";
                this.widgets.find(w => w.name === "padding").value = 2;
            }
        };
        const onExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (message) {
            onExecuted?.apply(this, arguments);
            if (!this.ffExcelResult) {
                this.ffExcelResult = this.addWidget("text", "处理结果", "", () => {}, {});
                this.ffExcelResult.options.serialize = false;
            }
            this.ffExcelResult.value = (message.text || []).join(" | ").replaceAll("\n", " | ");
            this.setDirtyCanvas(true, true);
        };
    },
});
