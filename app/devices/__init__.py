"""Device profiles and registry."""

from app.devices.builtin import BUILTIN_PROFILES, DEFAULT_PROFILE_SLUG
from app.devices.profile import DeviceProfile
from app.devices.registry import (
    all_profiles,
    custom_profiles,
    delete_custom_profile,
    get_profile,
    save_custom_profile,
    seed_builtin_profiles,
)

__all__ = [
    "BUILTIN_PROFILES",
    "DEFAULT_PROFILE_SLUG",
    "DeviceProfile",
    "all_profiles",
    "custom_profiles",
    "delete_custom_profile",
    "get_profile",
    "save_custom_profile",
    "seed_builtin_profiles",
]
