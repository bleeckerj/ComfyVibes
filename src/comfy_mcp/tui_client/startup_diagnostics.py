"""Startup diagnostics rendering for the TUI."""

from __future__ import annotations

import asyncio
from typing import Any

from comfy_mcp.tui_client.tool_exposure_diagnostics import build_zero_tool_warnings


class StartupDiagnosticsRenderer:
    async def render_connected_servers(self, app: Any) -> None:
        status_fetcher = getattr(app._router, "get_server_connection_statuses", None)
        if not callable(status_fetcher):
            status_fetcher = getattr(app._router, "get_server_health_statuses", None)
        if not callable(status_fetcher):
            return
        try:
            statuses = await status_fetcher()
        except Exception as exc:
            app._write_chat(
                f"[yellow]Connected, but failed to read server health details: {exc}[/yellow]",
                f"Connected, but failed to read server health details: {exc}",
            )
            return
        if not isinstance(statuses, list) or not statuses:
            return
        app._write_chat("[bold]Connected servers:[/bold]", "Connected servers:")
        for status in statuses:
            if not isinstance(status, dict):
                continue
            name = str(status.get("name") or "unknown")
            transport = str(status.get("transport") or "")
            if transport == "stdio":
                connected = bool(status.get("connected"))
                command = str(status.get("command") or "")
                cwd = str(status.get("cwd") or "")
                stderr_log_path = str(status.get("stderr_log_path") or "")
                args = status.get("args")
                args_text = ""
                if isinstance(args, list) and args:
                    args_text = " " + " ".join(str(item) for item in args)
                plain = f"- {name} (stdio) connected={'yes' if connected else 'no'} command={command}{args_text}"
                if cwd:
                    plain += f", cwd={cwd}"
                if stderr_log_path:
                    plain += f", stderr_log={stderr_log_path}"
                app._write_chat(f"[dim]{plain}[/dim]", plain)
                continue
            base_url = str(status.get("base_url") or "")
            ok = bool(status.get("ok"))
            payload = status.get("payload")
            payload_dict = payload if isinstance(payload, dict) else {}
            runtime_status = str(payload_dict.get("status") or ("ok" if ok else "unknown"))
            version = payload_dict.get("service_version")
            commit = payload_dict.get("git_commit")
            branch = payload_dict.get("git_branch")
            dirty = payload_dict.get("git_dirty")
            detail_parts: list[str] = [f"status={runtime_status}"]
            if isinstance(version, str) and version:
                detail_parts.append(f"version={version}")
            if isinstance(commit, str) and commit:
                detail_parts.append(f"commit={commit}")
            if isinstance(branch, str) and branch:
                detail_parts.append(f"branch={branch}")
            if isinstance(dirty, bool):
                detail_parts.append(f"dirty={'yes' if dirty else 'no'}")
            plain = f"- {name} ({base_url}) {', '.join(detail_parts)}"
            app._write_chat(f"[dim]{plain}[/dim]", plain)

    async def render_tool_exposure_warnings(self, app: Any) -> None:
        tool_specs = app._router.list_tool_specs()
        active_config_builder = getattr(app, "_build_active_config", None)
        if callable(active_config_builder):
            servers = active_config_builder().servers
        else:
            servers = app._all_servers
        warnings = build_zero_tool_warnings(servers, tool_specs)
        if not warnings:
            return
        app._write_chat("[bold yellow]Startup warnings:[/bold yellow]", "Startup warnings:")
        app._write_tools("[bold yellow]Startup warnings:[/bold yellow]", "Startup warnings:")
        for warning in warnings:
            plain = f"Tool exposure warning: {warning.message}"
            app._write_chat(f"[yellow]{plain}[/yellow]", plain)
            app._write_tools(f"[yellow]{plain}[/yellow]", plain)
        app._write_chat(
            "[yellow]Startup warnings are also persisted in the Tools pane and session log.[/yellow]",
            "Startup warnings are also persisted in the Tools pane and session log.",
        )

    async def render_comfy_server_info(self, app: Any) -> None:
        if "comfy_server_info" not in app._available_tool_names:
            return
        call_tool = getattr(app._router, "call_tool", None)
        if not callable(call_tool):
            return
        try:
            payload = await asyncio.wait_for(call_tool("comfy_server_info", {}), timeout=8.0)
        except Exception as exc:
            app._write_chat(
                f"[yellow]Comfy server-info diagnostics failed: {exc}[/yellow]",
                f"Comfy server-info diagnostics failed: {exc}",
            )
            return
        if not isinstance(payload, dict):
            app._write_chat(
                "[yellow]Comfy server-info diagnostics returned a non-object payload.[/yellow]",
                "Comfy server-info diagnostics returned a non-object payload.",
            )
            return
        base_url = str(payload.get("configured_base_url") or "").strip()
        target_dict = payload.get("target") if isinstance(payload.get("target"), dict) else {}
        host = str(target_dict.get("host") or "")
        port = target_dict.get("port")
        resolved_ips = target_dict.get("resolved_ips")
        resolved_ip_list = [str(item) for item in resolved_ips] if isinstance(resolved_ips, list) else []
        dns_error = str(target_dict.get("dns_error") or "").strip()
        probe_dict = payload.get("probe") if isinstance(payload.get("probe"), dict) else {}
        app._write_chat("[bold]Comfy server-info:[/bold]", "Comfy server-info:")
        target_line = f"- configured_base_url={base_url or '<empty>'}"
        if host:
            target_line += f", host={host}"
        if isinstance(port, int):
            target_line += f", port={port}"
        app._write_chat(f"[dim]{target_line}[/dim]", target_line)
        if resolved_ip_list:
            ips_line = f"- resolved_ips={', '.join(resolved_ip_list)}"
            app._write_chat(f"[dim]{ips_line}[/dim]", ips_line)
        elif dns_error:
            dns_line = f"- dns_error={dns_error}"
            app._write_chat(f"[yellow]{dns_line}[/yellow]", dns_line)
        else:
            app._write_chat("[dim]- resolved_ips=<none>[/dim]", "- resolved_ips=<none>")
        if probe_dict:
            endpoint = str(probe_dict.get("endpoint") or "")
            latency_ms = probe_dict.get("latency_ms")
            probe_ok = bool(probe_dict.get("ok"))
            if probe_ok:
                parts = [f"- probe_ok=true", f"endpoint={endpoint}"]
                running = probe_dict.get("queue_running_count")
                pending = probe_dict.get("queue_pending_count")
                if isinstance(latency_ms, (int, float)):
                    parts.append(f"latency_ms={latency_ms}")
                if isinstance(running, int):
                    parts.append(f"queue_running={running}")
                if isinstance(pending, int):
                    parts.append(f"queue_pending={pending}")
                line = ", ".join(parts)
                app._write_chat(f"[green]{line}[/green]", line)
            else:
                probe_error = str(probe_dict.get("error") or "unknown error")
                parts = [f"- probe_ok=false", f"endpoint={endpoint}", f"error={probe_error}"]
                if isinstance(latency_ms, (int, float)):
                    parts.append(f"latency_ms={latency_ms}")
                line = ", ".join(parts)
                app._write_chat(f"[yellow]{line}[/yellow]", line)
