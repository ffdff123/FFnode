import { app } from "../../scripts/app.js";

app.registerExtension({
    name: "FFNodes.GPTImage25",
    nodeCreated(node) {
        if (node.comfyClass !== "FF_GPT_Image_25") return;
        // Keep ComfyUI's native sockets and widgets; only set the initial layout.
        node.color = "#303030";
        node.bgcolor = "#353535";
        const computed = node.computeSize();
        node.setSize([Math.max(560, computed[0]), Math.max(990, computed[1])]);
        const control = node.widgets?.find(w => w.name === "control_after_generate");
        if (control) control.value = "randomize";
    },
});
