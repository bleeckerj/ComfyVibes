import asyncio
import json
from typing import Iterable

import aiohttp

WS_URL = "ws://192.168.15.54:8188/ws"
PROMPT_IDS = {
    "77805276-e7d7-4d05-88bf-530912e2d9cf",
    "b820ff72-09c7-44bf-b432-a7e49a943336",
    "28dd7dd5-14fe-4c03-8eff-1f61c19e76e0",
    "b06529a3-b86b-49e5-a5b2-dd5af2162cc1",
}


def _prompt_id_from_payload(payload: dict) -> str | None:
    return payload.get("prompt_id") if isinstance(payload, dict) else None


async def main() -> None:
    remaining = set(PROMPT_IDS)
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(WS_URL) as ws:
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                try:
                    data = json.loads(msg.data)
                except json.JSONDecodeError:
                    continue
                payload = data.get("data") if isinstance(data, dict) else None
                prompt_id = _prompt_id_from_payload(payload or {})
                if prompt_id and prompt_id in remaining:
                    print(json.dumps(data, indent=2))
                    if data.get("type") == "execution_success":
                        remaining.discard(prompt_id)
                        if not remaining:
                            return


if __name__ == "__main__":
    asyncio.run(main())
