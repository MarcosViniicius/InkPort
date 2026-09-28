"""Web panel: device profiles."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.database.base import get_session
from app.database.models import DeviceProfileModel
from app.devices.profile import DeviceProfile
from app.devices.registry import all_profiles, delete_custom_profile, save_custom_profile
from app.security.auth import require_panel
from app.web.templating import render

router = APIRouter(prefix="/devices", dependencies=[Depends(require_panel)], tags=["painel"])


@router.get("")
def devices_page(request: Request, session: Session = Depends(get_session)):
    from sqlalchemy import select

    builtin_slugs = {
        row.slug
        for row in session.scalars(
            select(DeviceProfileModel).where(DeviceProfileModel.builtin.is_(True))
        )
    }
    return render(
        request,
        "devices.html",
        {
            "active": "devices",
            "profiles": all_profiles(session),
            "builtin_slugs": builtin_slugs,
        },
    )


@router.post("/save")
def save_device(
    slug: str = Form(...),
    name: str = Form(...),
    screen_width: int = Form(0),
    screen_height: int = Form(0),
    ppi: int = Form(300),
    preferred_format: str = Form("epub"),
    comic_output: str = Form("epub_images"),
    grayscale: str = Form(""),
    color: str = Form(""),
    image_quality: int = Form(85),
    image_format: str = Form("jpg"),
    posterize_levels: int = Form(0),
    spreads: str = Form("none"),
    render_dpi: int = Form(0),
    preserve_resolution: str = Form(""),
    max_long_side: int = Form(2000),
    autocontrast: str = Form(""),
    sharpen: float = Form(0.6),
    rotate_portrait: str = Form(""),
    crop_margins: str = Form(""),
    manga_rtl: str = Form(""),
    max_file_size_mb: int = Form(300),
    native_formats: str = Form("epub"),
    notes: str = Form(""),
    session: Session = Depends(get_session),
):
    profile = DeviceProfile(
        slug=slug.strip(),
        name=name.strip(),
        screen_width=int(screen_width),
        screen_height=int(screen_height),
        ppi=int(ppi),
        grayscale=bool(grayscale),
        color=bool(color),
        gray_levels=16 if grayscale else 256,
        native_formats=[f.strip() for f in native_formats.split(",") if f.strip()] or ["epub"],
        preferred_format=preferred_format.strip(),
        comic_output=comic_output.strip(),
        image_quality=int(image_quality),
        image_format=image_format.strip() or "jpg",
        posterize_levels=int(posterize_levels),
        spreads=spreads.strip() or "none",
        render_dpi=int(render_dpi),
        preserve_resolution=bool(preserve_resolution),
        max_long_side=int(max_long_side or 0),
        autocontrast=bool(autocontrast),
        sharpen=float(sharpen),
        rotate_portrait=bool(rotate_portrait),
        crop_margins=bool(crop_margins),
        manga_rtl=bool(manga_rtl),
        max_file_size_mb=int(max_file_size_mb),
        notes=notes.strip(),
    )
    save_custom_profile(session, profile)
    return RedirectResponse("/devices", status_code=303)


@router.post("/{slug}/delete")
def delete_device(slug: str, session: Session = Depends(get_session)):
    delete_custom_profile(session, slug)
    return RedirectResponse("/devices", status_code=303)
