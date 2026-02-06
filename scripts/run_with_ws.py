import asyncio
import json
from pathlib import Path

import aiohttp

from comfy_mcp.comfy_client.client import ComfyClient
from comfy_mcp.params.patch import patch_workflow
from comfy_mcp.params.schema import ParamSpec

PROMPT = (
    "A fresh cut  thick plump TOp Sirloin steak on a wood chopping board, fresh "
    "medium rare slices have been just cut. THe outside is seared nearly black "
    "with seasonings encrusted. There are garlic and sage cuttings scattered. "
    "Food photography."
)

API_PATH = Path("/Volumes/home/ComfyUI_00356__api.json")
PARAMS_PATH = Path("/Users/julian/Code/nfl-comfymcp/ComfyUI_00356__params.json")
COMFY_URL = "http://192.168.15.54:8188"
WS_URL = "ws://192.168.15.54:8188/ws"


async def main() -> None:
    workflow = json.loads(API_PATH.read_text(encoding="utf-8"))
    params = json.loads(PARAMS_PATH.read_text(encoding="utf-8"))

    spec = ParamSpec.model_validate(params)
    patched = patch_workflow(workflow, spec, {"prompt": PROMPT, "width": 1824, "height": 1024})

    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(WS_URL) as ws:
            async with ComfyClient(COMFY_URL) as client:
                result = await client.queue_prompt(patched)
            prompt_id = result.get("prompt_id")
            print({"prompt_id": prompt_id})

            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                    except json.JSONDecodeError:
                        continue
                    payload = data.get("data") if isinstance(data, dict) else None
                    if isinstance(payload, dict) and payload.get("prompt_id") == prompt_id:
                        print(json.dumps(data, indent=2))
                        if data.get("type") == "execution_success":
                            return
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break


if __name__ == "__main__":
    asyncio.run(main())
