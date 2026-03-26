import asyncio
from comfy_mcp.comfy_client.client import ComfyClient

TARGET = (
    "Editorial photography of a dark workspace with a high-resolution computer monitor displaying complex, "
    "swirling digital abstract patterns. Moody lighting, subtle reflections, atmospheric ambiance, emphasizing "
    "the mysterious nature of digital art."
)


async def main() -> None:
    async with ComfyClient("http://192.168.15.56:8188") as client:
        history = await client.get_history()

    matches = []
    for prompt_id, payload in history.items():
        prompt = payload.get("prompt", [])
        graph = prompt[2] if len(prompt) > 2 else {}
        for node in graph.values():
            if isinstance(node, dict) and node.get("class_type") == "CLIPTextEncode":
                text = node.get("inputs", {}).get("text")
                if text == TARGET:
                    matches.append(prompt_id)
                    break

    print(matches)


if __name__ == "__main__":
    asyncio.run(main())
