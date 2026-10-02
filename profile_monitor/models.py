from dataclasses import dataclass


@dataclass(frozen=True)
class ProfileSnapshot:
    user_id: int
    username: str | None
    first_name: str | None
    last_name: str | None
    bio: str | None
    photo_id: str | None


FIELDS = ("username", "first_name", "last_name", "bio", "photo_id")


def compare_snapshots(old: ProfileSnapshot, new: ProfileSnapshot):
    """Return all changed fields, including changes back to an old value."""
    return [
        (field, getattr(old, field), getattr(new, field))
        for field in FIELDS
        if getattr(old, field) != getattr(new, field)
    ]
