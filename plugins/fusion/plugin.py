"""Autodesk Fusion plugin: browse hubs, projects, folders, files and versions.

Sign-in is one Connect click (OAuth2 code flow); tokens refresh themselves.
Read-only in this version: uploads are a two-step resumable flow and come next.
"""
from __future__ import annotations

import base64

try:
    from core.plugins import oauth2_token
except ImportError:  # pragma: no cover
    oauth2_token = None

DM = "https://developer.api.autodesk.com"


def _auth(api) -> dict:
    cid, csec = (api.config("client_id") or "").strip(), api.config("client_secret")
    rt = (api.config("refresh_token") or "").strip()
    if not (cid and csec):
        raise RuntimeError("Fusion is not configured: paste the Client ID + secret, then press Connect.")
    if not rt:
        raise RuntimeError("Fusion is not signed in yet: press Connect in Plugins and approve in the browser.")
    basic = base64.b64encode(f"{cid}:{csec}".encode()).decode()
    data = oauth2_token(None, "fusion", f"{DM}/authentication/v2/token",
                        {"grant_type": "refresh_token", "refresh_token": rt},
                        headers={"Authorization": f"Basic {basic}"})
    return {"Authorization": f"Bearer {data['access_token']}"}


def hubs(ctx, args, api):
    return api.http(ctx, "GET", f"{DM}/project/v1/hubs", headers=_auth(api))


def projects(ctx, args, api):
    return api.http(ctx, "GET", f"{DM}/project/v1/hubs/{args['hub_id']}/projects", headers=_auth(api))


def top_folders(ctx, args, api):
    return api.http(ctx, "GET", f"{DM}/project/v1/hubs/{args['hub_id']}/projects/{args['project_id']}/topFolders",
                    headers=_auth(api))


def folder_contents(ctx, args, api):
    return api.http(ctx, "GET", f"{DM}/data/v1/projects/{args['project_id']}/folders/{args['folder_id']}/contents",
                    headers=_auth(api))


def item_versions(ctx, args, api):
    return api.http(ctx, "GET", f"{DM}/data/v1/projects/{args['project_id']}/items/{args['item_id']}/versions",
                    headers=_auth(api))


def register(api):
    return [
        {"name": "hubs", "description": "List Fusion hubs (accounts) with their ids.",
         "input_schema": {"type": "object", "properties": {}},
         "handler": lambda ctx, a: hubs(ctx, a, api)},
        {"name": "projects", "description": "Projects inside a hub.",
         "input_schema": {"type": "object", "properties": {"hub_id": {"type": "string"}}, "required": ["hub_id"]},
         "handler": lambda ctx, a: projects(ctx, a, api)},
        {"name": "top_folders", "description": "Top-level folders of a project.",
         "input_schema": {"type": "object", "properties": {
             "hub_id": {"type": "string"}, "project_id": {"type": "string"}}, "required": ["hub_id", "project_id"]},
         "handler": lambda ctx, a: top_folders(ctx, a, api)},
        {"name": "folder_contents", "description": "Files and subfolders inside a folder (use the ids from top_folders).",
         "input_schema": {"type": "object", "properties": {
             "project_id": {"type": "string"}, "folder_id": {"type": "string"}}, "required": ["project_id", "folder_id"]},
         "handler": lambda ctx, a: folder_contents(ctx, a, api)},
        {"name": "item_versions", "description": "Version history of a file item.",
         "input_schema": {"type": "object", "properties": {
             "project_id": {"type": "string"}, "item_id": {"type": "string"}}, "required": ["project_id", "item_id"]},
         "handler": lambda ctx, a: item_versions(ctx, a, api)},
    ]
