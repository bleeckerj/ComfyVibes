"""CLI entry point for running the MCP server over stdio."""

from __future__ import annotations

import asyncio
import inspect
import os
import sys
import time
from pathlib import Path

from comfy_mcp.config.load_config import load_config
from comfy_mcp.mcp_server.server import create_server


def _load_stdio_runner():
    try:
        from mcp.server.stdio import serve
        return ("serve", serve)
    except ImportError:
        pass

    from mcp.server import stdio as stdio_module
    serve = getattr(stdio_module, "serve", None) or getattr(stdio_module, "run", None)
    if serve is not None:
        return ("serve", serve)

    stdio_server = getattr(stdio_module, "stdio_server", None)
    if stdio_server is not None:
        return ("context", stdio_server)

    raise RuntimeError("MCP stdio runner not found in MCP SDK")


def _print_attract_screen() -> None:
    """Print a startup banner to stderr (safe for MCP stdio)."""
    force = os.environ.get("COMFYVIBE_ATTRACT", "").lower() in {"1", "true", "yes", "on"}
    if not force and not sys.stderr.isatty():
        return
    banner_path = Path(__file__).parent / "assets" / "banner.txt"
    if banner_path.exists():
        try:
            ascii_art = banner_path.read_text()
        except Exception:
            ascii_art = ""
    else:
        ascii_art = ""

    if not ascii_art:
        ascii_art = r"""
$$$$$$$$$$$$$$$$$$$$$@BB%%8888&&&&8888%%BB@$$$$$$$$$$$$$$$$$$$$$
$$$$$$$$$$$$$$$$$@B%88&&&&888888&&&&&&&&&&88%B@$$$$$$$$$$$$$$$$$
$$$$$$$$$$$$$$@%8&&&&&&8%%W#ohhb%%%8&&&&&&&&&&&8%@$$$$$$$$$$$$$$
$$$$$$$$$$$@B8&&&&&&&%8bCu({?---nQQk88&&&&&&&&&&&&8B@$$$$$$$$$$$
$$$$$$$$$@%8&&&&&&&&8wr-+~++++++~++-v&8&&&&&&&&&&&&&8%@$$$$$$$$$
$$$$$$$$B8&&&&&&&&&%0++_?{|tfxrf/--_+fM8&&&&&&&&&&&&&&8B$$$$$$$$
$$$$$$@8&&&&&&&&&&&%U+{x0dkkkhhhd]_--~x88&&&&&&&&&&&&&&&8@$$$$$$
$$$$$B8&&&&&&&&&&&8h[-uYbkkkkhhhk/_--_]#8&&&&&&&&&&&&&&&&8B$$$$$
$$$$B&&&&&&&&&&&&&8b-?vQkhkpOCUXzz(_-_+Z%&&&&&&&&&&&&&&&&&&B$$$$
$$$%&&&&&&&&&&&&&&8W}_/ucuftfffz|zY(-|(d8&&&&&&&&&&&&&&&&&&&B$$$
$$B&&&&&&&&&&&&&&&8&r+txn/UnLQL0Qhau-cfo8&&&&&&&&&&&&&&&&&&&&B$$
$@8&&&&&&&&&&&&&&&&8&v/XUzabZmwpkkhj}CY8&&&&&&&&&&&&&&&&&&&&&8@$
$%&&&&&&&&&&&&&&&&&&88zXQzYnvZdkhdz_xJM%8&&&&&&&&&&&&&&&&&&&&&%$
@&&&&&&&&&&&&&&&&&&&&8hxx{]}(]])f}++nYdqh8&&&&&&&&&&&&&&&&&&&&&@
%&&&&&&&&&&&&&&&&&&&&&%X+?//|[__+_+xqtn/jq%88&&&&&&&&&&&&&&&&&&%
8&&&&&&&&&&&&&&&&&&&&&8#(+____-__-nkhu/vzjZoM8%%88&&&&&&&&&&&&&8
&&&&&&&&&&&&&&&&&&&&&88Bb}?-___[|cbkQxcznjuucY0paM8%88&&&&&&&&&&
&&&&&&&&&&&&&&&&&&8%8#dQrnc}/jrvXCJcrxxrrzzzzcvuucU0bW8&&&&&&&&&
&&&&&&&&&&&&&&&&&8*mUvux|cv[tCUCzxvzzzzzzzzzzzzzzzvtrvh%&&&&&&&&
8&&&&&&&&&&&&&&&%hf|fzntzzzt}rnnuzzzzzzzzzzzzzzzzztuXvno8&&&&&&8
%&&&&&&&&&&88%8%*f{tnzzrxzzv||/vzzzzzzzzzzzzzzzzzrfXzzvu#8&&&&&%
@&&&&&&&&8%MaOO0/}/Xzzzzjxzz/rzzzzzzzzzzzzzzzzzzztczzzzuz&8&&&&@
$%&&&&&&8&Y({/njYfnzzzzzzrxctzzzzzzzzzzzzzzzzzzzzvzzzzzznC8&&&%$
$@8&&&&8hLLCJLxYzfzzzzzzzzrtrzzzn/jxrnrnxuuuczzzzzzzzzzzzxZ%&8@$
$$B&&&8#YmQLL0Cc/jXzzzzzzzz/uzzzrf|)/tf/)///nzzzzzzzzzzzzzrd8B$$
$$$%&&8&YddZO00Xrtzzzzzzzzztczzznrrjrrrjjjrfuzzzzzzzzzzzzzcn&$$$
$$$$B&&8kUkqQQQjcfuzzzzzzzufzzzzvunxxrrrxxnuzzzzzzzzzzzzzczd@$$$
$$$$$B8&%ZQbdZunzvfzzzzzzzrxzzzzzzzzzzzzzzzzzzzzzzzzzzzzcYo$$$$$
$$$$$$@88otbwQjvzzr/czzzzzfvzzzzzzzzzzzzzzzzzzzzxzzzzzcc0&$$$$$$
$$$$$$$$@d]JdbJnzzc(czzzzctzzzzzzzzzzzzzzzzzzzzzrfzzcvUh@$$$$$$$
$$$$$$$$$8qudkcuzzctzzzzznjzzzzzzzzzzzzzzzzzzzzzzrjvUb%$$$$$$$$$
$$$$$$$$$$$%WdxcczxrzzzzzjnzzzzzzzzzzzzzzzzzzzzcvXUbB$$$$$$$$$$$
$$$$$$$$$$$$$@MdLX/uczzzztczzzzzzzzzzzzzzzzcccXLdW$$$$$$$$$$$$$$
$$$$$$$$$$$$$$$$@&dmLYzcutzzzzzzzzzzzcccczYLmh&@$$$$$$$$$$$$$$$$
$$$$$$$$$$$$$$$$$$$$@8#kZCLUYXzzzzXYULOqk#8@$$$$$$$$$$$$$$$$$$$$
"""
    banner = [
        "",
        "ComfyVibes MCP Server",
        "",
        ascii_art,
        "",
        "Ready for MCP clients.",
        "",
    ]
    use_color = os.environ.get("COMFYVIBE_COLOR", "").lower() in {"1", "true", "yes", "on"}
    if use_color:
        color_banner = "\033[38;5;45m"
        color_body = "\033[38;5;156m"
        color_reset = "\033[0m"
        print(color_banner + banner[1] + color_reset, file=sys.stderr)
        print("", file=sys.stderr)
        print(color_body + ascii_art + color_reset, file=sys.stderr)
        print("", file=sys.stderr)
        print(color_banner + "Ready for MCP clients." + color_reset, file=sys.stderr)
        print("", file=sys.stderr)
        return
    for line in banner:
        print(line, file=sys.stderr)


def _maybe_hold_attract_screen() -> None:
    hold = os.environ.get("COMFYVIBE_ATTRACT_HOLD")
    if not hold:
        return
    hold_lower = hold.strip().lower()
    if hold_lower in {"forever", "inf", "infinite"}:
        while True:
            time.sleep(3600)
    try:
        seconds = float(hold)
    except ValueError:
        seconds = 15.0
    time.sleep(max(0.0, seconds))


def main() -> None:
    """Run the MCP server with stdio transport."""
    _print_attract_screen()
    _maybe_hold_attract_screen()
    config = load_config()
    server = create_server(config)
    runner = _load_stdio_runner()

    kind, handler = runner
    try:
        if kind == "serve":
            if inspect.iscoroutinefunction(handler):
                asyncio.run(handler(server))
            else:
                handler(server)
            return

        async def _run_with_context() -> None:
            async with handler() as (read_stream, write_stream):
                await server.run(
                    read_stream,
                    write_stream,
                    server.create_initialization_options(),
                )

        asyncio.run(_run_with_context())
    except KeyboardInterrupt:
        print("\nShutting down ComfyVibes MCP Server...", file=sys.stderr)


if __name__ == "__main__":
    main()
