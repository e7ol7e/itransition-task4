"""Execute the teaching notebook using this environment's real Jupyter kernel.

Uses jupyter_client (already installed with ipykernel); no extra runner required.
"""

import json
import os
import sys
import tempfile
from pathlib import Path
from queue import Empty

from jupyter_client import KernelManager

ROOT = Path(__file__).resolve().parent
path = ROOT / "explore.ipynb"
notebook = json.loads(path.read_text(encoding="utf-8"))
with tempfile.TemporaryDirectory(prefix="task4-notebook-") as temporary:
    manager = KernelManager(connection_file=str(Path(temporary) / "kernel.json"))
    manager.kernel_spec.argv = [sys.executable, "-m", "ipykernel_launcher", "-f", "{connection_file}"]
    environment = dict(os.environ, IPYTHONDIR=temporary, JUPYTER_RUNTIME_DIR=temporary,
                       MPLBACKEND="module://matplotlib_inline.backend_inline")
    manager.start_kernel(cwd=str(ROOT), env=environment)
    client = manager.client()
    client.start_channels()
    try:
        client.wait_for_ready(timeout=60)
        for index, cell in enumerate(notebook["cells"]):
            if cell["cell_type"] != "code":
                continue
            cell["outputs"] = []
            message_id = client.execute("".join(cell["source"]), stop_on_error=True)
            failure = None
            while True:
                try:
                    message = client.get_iopub_msg(timeout=60)
                except Empty:
                    raise TimeoutError(f"Cell {index + 1} did not respond") from None
                if message["parent_header"].get("msg_id") != message_id:
                    continue
                kind, content = message["msg_type"], message["content"]
                if kind == "execute_input":
                    cell["execution_count"] = content["execution_count"]
                elif kind == "stream":
                    cell["outputs"].append({"output_type": "stream", "name": content["name"], "text": content["text"]})
                elif kind in ("execute_result", "display_data"):
                    output = {"output_type": kind, "data": content["data"], "metadata": content["metadata"]}
                    if kind == "execute_result":
                        output["execution_count"] = content["execution_count"]
                    cell["outputs"].append(output)
                elif kind == "error":
                    failure = content
                    cell["outputs"].append({"output_type": "error", **{key: content[key] for key in ["ename", "evalue", "traceback"]}})
                elif kind == "status" and content["execution_state"] == "idle":
                    break
            path.write_text(json.dumps(notebook, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            if failure:
                raise RuntimeError(f"Cell {index + 1}: {failure['ename']}: {failure['evalue']}")
            print(f"Executed cell {index + 1}/{len(notebook['cells'])}", flush=True)
    finally:
        client.stop_channels()
        manager.shutdown_kernel(now=True)
print("Notebook completed and outputs saved.")
