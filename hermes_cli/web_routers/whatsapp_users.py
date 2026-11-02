"""Admin-only REST over the shared WhatsApp user service (default-deny access policy)."""
from __future__ import annotations

import asyncio
from dataclasses import asdict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from hermes_cli import whatsapp_users as wu

router = APIRouter()


class WhatsAppUserAdd(BaseModel):
    name: str
    number: str


@router.get('/api/whatsapp/users')
async def list_whatsapp_users():
    users = await asyncio.to_thread(wu.list_users)
    return {'users': [asdict(user) for user in users]}


@router.post('/api/whatsapp/users')
async def add_whatsapp_user(body: WhatsAppUserAdd):
    try:
        return asdict(await asyncio.to_thread(wu.add_user, body.name, body.number))
    except wu.WhatsAppUsersError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete('/api/whatsapp/users/{target}')
async def remove_whatsapp_user(target: str, delete_profile: bool = False):
    try:
        return asdict(await asyncio.to_thread(wu.remove_user, target, delete_profile=delete_profile))
    except wu.WhatsAppUsersError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
